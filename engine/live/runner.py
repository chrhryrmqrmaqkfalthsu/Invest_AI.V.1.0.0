"""
Runner - 라이브 트레이딩 메인 로직.

Scheduler가 시계라면, Runner는 그 시계 신호 받아서 실제로 일하는 친구.

콜백 4종:
  - startup_check()    : 봇 가동시 1회. 토큰/잔고/텔레그램 점검.
  - tick_market()      : 장중 1분마다. 시그널 평가 → 안전성 체크 → 주문.
  - tick_offmarket()   : 장외 60분마다. 시세 캐싱/헬스체크.
  - daily_summary()    : 매일 16:00. 손익/체결 요약 전송.
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from engine.live.approval_manager import ApprovalManager, classify_strength
from engine.live.broker.base import Broker, OrderStatus, OrderType
from engine.live.buy_reconciliation import BuyPreflight, BuyReconciliationService
from engine.live.market_clock import MarketClock, select_market_clock
from engine.live.pending_order_manager import PendingOrderManager
from engine.live.position_manager import PositionManager
from engine.live.safety.layer import SafetyLayer
from engine.live.telegram.notifier import TelegramNotifier
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import build_market_context
from engine.strategies.demo_rulebook import RuleBook, Signal

logger = logging.getLogger("runner")

SHARE_ROUND_DIGITS = 6
SHARE_EPS = 10 ** (-SHARE_ROUND_DIGITS)


def _normalize_shares(value: float) -> float:
    v = round(float(value), SHARE_ROUND_DIGITS)
    return 0.0 if abs(v) <= SHARE_EPS else v


@dataclass
class RunnerStats:
    market_ticks: int = 0
    offmarket_ticks: int = 0
    signals_buy: int = 0
    signals_sell: int = 0
    signals_hold: int = 0
    orders_attempted: int = 0
    orders_filled: int = 0
    orders_blocked: int = 0
    market_refreshes: int = 0
    last_regime: str = ""
    last_error: str = ""
    started_at: Optional[datetime] = None

    def reset_daily(self):
        self.market_ticks = 0
        self.offmarket_ticks = 0
        self.signals_buy = 0
        self.signals_sell = 0
        self.signals_hold = 0
        self.orders_attempted = 0
        self.orders_filled = 0
        self.orders_blocked = 0


class Runner:
    def __init__(
        self,
        broker: Broker,
        safety: SafetyLayer,
        notifier: TelegramNotifier,
        clock: MarketClock,
        rulebook: RuleBook,
        symbols: List[str],
        order_shares: float = 1.0,
        order_notional: Optional[float] = None,
        universe_config: Optional[LiveUniverseConfig] = None,
    ):
        self.broker = broker
        self.safety = safety
        self.notifier = notifier
        self.clock = clock
        self.rulebook = rulebook
        self.symbols = list(symbols)
        self.universe_config = universe_config.normalized() if universe_config is not None else None
        self.order_shares = float(order_shares)
        self.order_notional = float(order_notional) if order_notional and float(order_notional) > 0 else None
        self.stats = RunnerStats(started_at=datetime.now(ZoneInfo("Asia/Seoul")))
        order_mode = f"notional={self.order_notional:g}" if self.order_notional else f"shares={self.order_shares:g}"
        logger.info(
            f"Runner 초기화: mode={broker.mode} symbols={len(self.symbols)}개 "
            f"rulebook={rulebook.name()} order_mode={order_mode}"
        )
        self.position_manager = PositionManager()
        self.approval_manager = ApprovalManager()
        self.pending_order_manager = PendingOrderManager(self.broker)
        try:
            self.safety.pending_order_manager = self.pending_order_manager
        except Exception:
            pass
        self.buy_reconciler = self._make_buy_reconciler()
        self._tick_locked_tickers = set()

    def _make_buy_reconciler(self) -> BuyReconciliationService:
        return BuyReconciliationService(
            broker=self.broker,
            rulebook_provider=self.rulebook,
            position_manager=self.position_manager,
            pending_manager=getattr(self, "pending_order_manager", None),
            notifier=self.notifier,
        )

    def _get_buy_reconciler(self) -> BuyReconciliationService:
        reconciler = getattr(self, "buy_reconciler", None)
        if reconciler is None:
            reconciler = self._make_buy_reconciler()
            self.buy_reconciler = reconciler
        return reconciler

    def _supports_fractional_shares(self) -> bool:
        mode = str(getattr(self.broker, "mode", "") or "").lower()
        cls = type(self.broker).__name__.lower()
        return mode == "paper" or "alpaca" in mode or "alpaca" in cls

    def _calc_shares_from_notional(self, target_notional: float, price: float) -> float:
        if price <= 0 or target_notional <= 0:
            return 0.0
        raw = float(target_notional) / float(price)
        if self._supports_fractional_shares():
            return _normalize_shares(raw)
        return float(max(1, int(raw)))

    def _resolve_order_shares(
        self,
        side: str,
        ticker: str,
        price: float,
        target_notional: Optional[float] = None,
    ) -> float:
        side_u = str(side).upper()
        target = float(target_notional) if target_notional and float(target_notional) > 0 else self.order_notional
        if target and target > 0:
            shares = self._calc_shares_from_notional(target, price)
        else:
            raw = float(self.order_shares)
            shares = _normalize_shares(raw) if self._supports_fractional_shares() else float(max(1, int(raw)))

        if side_u == "SELL":
            try:
                holdings = {h.ticker: h for h in self.broker.get_holdings()}
                held = float(getattr(holdings.get(ticker), "shares", 0.0) or 0.0)
                if held <= SHARE_EPS:
                    return 0.0
                shares = min(shares, held)
            except Exception:
                pass
        return _normalize_shares(shares)

    def reload_symbols(self) -> dict:
        """Add only symbols that still satisfy the immutable startup policy."""
        if self.universe_config is None:
            logger.warning("[HOT-RELOAD] universe policy 없음 — 안전을 위해 신규 종목 편입 차단")
            return {
                "added": [],
                "total": len(self.symbols),
                "eligible": len(self.symbols),
                "blocked": True,
                "reason": "UNIVERSE_POLICY_MISSING",
            }

        result = load_live_universe(self.universe_config)
        eligible = list(result.symbols)
        if eligible:
            reload_clock = select_market_clock(eligible)
            if reload_clock.name != self.clock.name:
                raise RuntimeError(
                    f"hot-reload clock mismatch: runner={self.clock.name} eligible={reload_clock.name}"
                )

        current = set(self.symbols)
        added = sorted(set(eligible) - current)
        if added:
            self.symbols.extend(added)
            try:
                cache = getattr(self.rulebook, "_rulebook_cache", None)
                if isinstance(cache, dict):
                    for ticker in added:
                        cache.pop(ticker, None)
                logger.info(f"[HOT-RELOAD] 정책 통과 신규 종목 편입: {added} (총 {len(self.symbols)}개)")
            except Exception as e:
                logger.warning(f"[HOT-RELOAD] rulebook 캐시 invalidate 실패: {e}")
        return {
            "added": added,
            "total": len(self.symbols),
            "eligible": len(eligible),
            "blocked": False,
            "excluded_reason_counts": dict(result.excluded_reason_counts),
        }

    def attach_bot(self, bot) -> None:
        bot.position_manager = self.position_manager
        bot.approval_manager = self.approval_manager
        bot.rulebook = self.rulebook
        logger.info("TelegramBot에 PositionManager/ApprovalManager/Rulebook 주입 완료")

    def startup_check(self) -> None:
        try:
            logger.info("startup_check 시작...")
            if not self.broker.health_check():
                raise RuntimeError("broker.health_check() = False")
            self._poll_pending_orders(context="startup_check")
            balance = self.broker.get_balance()
            try:
                orphans = self._get_buy_reconciler().detect_orphan_holdings(balance.holdings)
                if orphans:
                    logger.error("[ORPHAN-HOLDINGS] startup 고아 보유 탐지: %s", orphans)
            except Exception as orphan_exc:
                logger.error("startup 고아 보유 탐지 실패: %s", orphan_exc)
            warmup = []
            for t in self.symbols:
                p = self.broker.get_current_price(t)
                warmup.append(f"  {t}: {p:,.0f}원" if p else f"  {t}: 조회 실패")
            msg = (
                f"🚀 Kingmaker 가동\n모드: {self.broker.mode}\n룰북: {self.rulebook.name()}\n"
                f"종목: {len(self.symbols)}개\n현금: {balance.cash_krw:,.0f}원\n"
                f"평가: {balance.total_value_krw:,.0f}원\n보유: {len(balance.holdings)}개\n"
                f"--- 현재가 ---\n" + "\n".join(warmup)
            )
            self.notifier.send(msg)
            logger.info("startup_check 완료")
        except Exception as e:
            self._handle_error("startup_check", e)

    def tick_market(self) -> None:
        try:
            self._poll_pending_orders(context="tick_market.pre_exit")
            if self.pending_order_manager.all():
                logger.info("pending 주문 존재 → 자동청산 체크 1 tick 보류")
                exited = []
            else:
                exited = self.position_manager.check_exits(self.broker, self.notifier, pending_manager=self.pending_order_manager)
            if exited:
                logger.info(f"자동 청산 {len(exited)}건 완료")
        except Exception as e:
            self._handle_error("position_manager.check_exits", e)
        try:
            self._process_pending_approvals()
        except Exception as e:
            self._handle_error("_process_pending_approvals", e)
        self.stats.market_ticks += 1
        try:
            self._poll_pending_orders(context="tick_market.pre_signal")
            self._tick_locked_tickers = set()
            logger.debug(f"tick_market #{self.stats.market_ticks}")
            for ticker in self.symbols:
                self._process_ticker(ticker)
        except Exception as e:
            self._handle_error("tick_market", e)

    def _process_ticker(self, ticker: str) -> None:
        pending_mgr = getattr(self, "pending_order_manager", None)
        if pending_mgr is not None and pending_mgr.is_ticker_locked(ticker):
            logger.info(f"{ticker} pending 주문 잠금 → 신규 시그널 처리 스킵")
            return
        price = self.broker.get_current_price(ticker)
        if price is None:
            logger.warning(f"{ticker} 현재가 조회 실패")
            return
        self._maybe_reconfirm_existing(ticker, price)
        sig = self.rulebook.evaluate(ticker, price)
        if sig.signal == Signal.BUY:
            self.stats.signals_buy += 1
            self._try_order("BUY", ticker, price, sig.reason, signal_result=sig)
        elif sig.signal == Signal.SELL:
            self.stats.signals_sell += 1
            self._try_order("SELL", ticker, price, sig.reason, signal_result=sig)
        else:
            self.stats.signals_hold += 1
            logger.debug(f"{ticker} HOLD: {sig.reason}")

    def _process_pending_approvals(self) -> None:
        if not self.approval_manager:
            return
        for req in list(self.approval_manager._requests.values()):
            if req.status == "approved":
                self._execute_approved(req)
            elif req.status == "reevaluating":
                self._reevaluate_request(req)

    def _execute_approved(self, req) -> None:
        ticker = req.ticker
        amount = req.approved_krw
        if amount <= 0:
            req.status = "rejected"
            self.approval_manager._save()
            return
        try:
            pending_mgr = getattr(self, "pending_order_manager", None)
            if pending_mgr is not None and pending_mgr.is_ticker_locked(ticker):
                logger.info(f"[APPROVAL-EXEC] {ticker} pending 주문 잠금 → 추가매수 보류")
                return
            price = self.broker.get_current_price(ticker)
            if price is None or price <= 0:
                logger.warning(f"[APPROVAL-EXEC] {ticker} 현재가 조회 실패")
                return

            add_buy_guard = self.safety.check_add_buy_guard(ticker)
            if not add_buy_guard.allowed:
                logger.warning(f"[APPROVAL-EXEC] {ticker} stale/invalid 추가매수 차단: [{add_buy_guard.code}] {add_buy_guard.reason}")
                self.notifier.send(f"⛔ `{ticker}` 추가매수 차단: [{add_buy_guard.code}] {add_buy_guard.reason}", parse_mode="Markdown")
                req.status = "rejected"
                self.approval_manager._save()
                return

            try:
                preflight = self._get_buy_reconciler().preflight(ticker)
            except Exception as preflight_exc:
                logger.error(f"[APPROVAL-EXEC] {ticker} 추가매수 preflight 실패: {preflight_exc}")
                self.notifier.send_error(f"{ticker} 추가매수 preflight 실패: {preflight_exc}")
                req.status = "rejected"
                self.approval_manager._save()
                return

            shares = self._resolve_order_shares("BUY", ticker, price, target_notional=amount)
            if shares <= SHARE_EPS:
                logger.warning(f"[APPROVAL-EXEC] {ticker} 주문 수량 계산 실패: amount={amount}, price={price}")
                req.status = "rejected"
                self.approval_manager._save()
                return

            original_max_krw = getattr(self.safety, "max_krw", None)
            original_max_notional = getattr(self.safety, "max_notional_per_order", None)
            original_max_shares = getattr(self.safety, "max_shares", None)
            original_max_total = getattr(self.safety, "max_total_invested", None)
            original_max_total_ntl = getattr(self.safety, "max_total_notional", None)
            try:
                temporary_notional_limit = float(amount) + price * max(2.0, shares * 0.02)
                if original_max_krw is not None:
                    self.safety.max_krw = max(float(original_max_krw), temporary_notional_limit)
                if original_max_notional is not None:
                    self.safety.max_notional_per_order = max(float(original_max_notional), temporary_notional_limit)
                if original_max_shares is not None:
                    self.safety.max_shares = max(float(original_max_shares), float(shares))
                if original_max_total is not None:
                    self.safety.max_total_invested = float(original_max_total) + temporary_notional_limit
                if original_max_total_ntl is not None:
                    self.safety.max_total_notional = float(original_max_total_ntl) + temporary_notional_limit

                check = self.safety.check_order("BUY", ticker, shares, price, purpose="add_buy")
                if not check.allowed:
                    logger.warning(f"[APPROVAL-EXEC] {ticker} 안전체크 차단: [{check.code}] {check.reason}")
                    self.notifier.send(f"⛔ `{ticker}` 추가매수 차단: [{check.code}] {check.reason}", parse_mode="Markdown")
                    req.status = "rejected"
                    self.approval_manager._save()
                    return

                order = self._submit_order_with_intent(
                    side="BUY",
                    ticker=ticker,
                    shares=shares,
                    purpose="add_buy",
                    order_type=OrderType.MARKET,
                    metadata={"approved_krw": amount},
                    approval_request_id=req.request_id,
                    seed=f"approval|{req.request_id}|{ticker}|{amount:g}",
                )
                self.safety.record_order(order, "BUY", purpose="add_buy")
                if not hasattr(self, "_tick_locked_tickers"):
                    self._tick_locked_tickers = set()
                self._tick_locked_tickers.add(ticker)
                if order.status != OrderStatus.FILLED and pending_mgr is not None:
                    pending_mgr.track_order(
                        order,
                        purpose="add_buy",
                        approval_request_id=req.request_id,
                        metadata={"approved_krw": amount},
                    )
                    req.status = "order_pending"
                    self.approval_manager._save()
                    self.notifier.send(f"⚠️ `{ticker}` 추가매수 주문 접수/미체결: status={order.status.value}", parse_mode="Markdown")
                    logger.info(f"[APPROVAL-EXEC] {ticker} 추가매수 pending 추적 id={order.order_id}")
                    return
                if order.status == OrderStatus.FILLED:
                    self.stats.orders_filled += 1
                    try:
                        updated = self._get_buy_reconciler().reconcile(order, purpose="add_buy", preflight=preflight)
                    except Exception as reconcile_exc:
                        self._get_buy_reconciler().track_failure(
                            order,
                            purpose="add_buy",
                            approval_request_id=req.request_id,
                            error=reconcile_exc,
                            metadata={"approved_krw": amount},
                        )
                        req.status = "order_pending"
                        self.approval_manager._save()
                        return
                    self.notifier.send(
                        f"✅ `{ticker}` 추가매수 체결: {float(order.filled_shares or shares):g}주 @ {float(order.filled_avg_price or price):,.4f} (req={req.request_id[:8]})",
                        parse_mode="Markdown",
                    )
                    logger.info(f"[APPROVAL-EXEC] {ticker} 추가매수 체결/정산 완료: {updated}")
            finally:
                if original_max_krw is not None:
                    self.safety.max_krw = original_max_krw
                if original_max_notional is not None:
                    self.safety.max_notional_per_order = original_max_notional
                if original_max_shares is not None:
                    self.safety.max_shares = original_max_shares
                if original_max_total is not None:
                    self.safety.max_total_invested = original_max_total
                if original_max_total_ntl is not None:
                    self.safety.max_total_notional = original_max_total_ntl
            req.status = "executed"
            self.approval_manager._save()
        except Exception as e:
            logger.error(f"[APPROVAL-EXEC] {ticker} 실행 예외: {e}")
            self.notifier.send(f"❌ `{ticker}` 추가매수 실행 실패: {e}", parse_mode="Markdown")
            req.status = "rejected"
            self.approval_manager._save()

    def _reevaluate_request(self, req) -> None:
        ticker = req.ticker
        try:
            price = self.broker.get_current_price(ticker)
            if price is None:
                return
            sig = self.rulebook.evaluate(ticker, price)
            if sig is None:
                return
            score = float(getattr(sig, "score", 0.0) or 0.0)
            threshold = float(getattr(sig, "threshold", 0.0) or 0.0)
            still_strong = (threshold > 0) and (score >= threshold * 1.2)
            if still_strong:
                ok, _, _ = self.approval_manager.confirm_after_reeval(
                    req.request_id, req.approved_krw or req.options_krw[0], new_signal_ok=True
                )
                if ok:
                    self.notifier.send(
                        f"⏱ `{ticker}` 재평가 통과 → 추가매수 진행 (score={score:.2f}/{threshold:.2f})",
                        parse_mode="Markdown",
                    )
                    self._execute_approved(req)
            else:
                self.approval_manager.confirm_after_reeval(req.request_id, 0, new_signal_ok=False)
                self.notifier.send(
                    f"🔻 `{ticker}` 재평가 결과 시그널 약화 → 추가매수 취소 (score={score:.2f}/{threshold:.2f})",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"{ticker} _reevaluate_request 예외: {e}")

    def _maybe_reconfirm_existing(self, ticker: str, price: float) -> None:
        if not self.position_manager.get(ticker) or not self.approval_manager.should_reconfirm(ticker):
            return
        try:
            sig = self.rulebook.evaluate(ticker, price)
            if sig is None:
                return
            rb = self.rulebook.get_rulebook(ticker) if hasattr(self.rulebook, "get_rulebook") else None
            if rb is None:
                return
            score = float(getattr(sig, "score", 0.0) or 0.0)
            threshold = float(getattr(sig, "threshold", 0.0) or 0.0)
            win_rate = float(getattr(rb, "win_rate", 0.0) or 0.0)
            try:
                from engine.market.context import get_market_context
                ctx = get_market_context()
                regime = ctx.regime
                sector_score = ctx.sector_strength.get(getattr(rb, "sector_name", ""), 50.0)
            except Exception:
                regime, sector_score = "neutral", 50.0
            strength = classify_strength(score=score, threshold=threshold, win_rate=win_rate, regime=regime, sector_score=sector_score)
            if strength is None:
                return
            self._maybe_request_approval(ticker, price, rb, sig)
            self.approval_manager.mark_reconfirmed(ticker)
            logger.info(f"[RECONFIRM] {ticker} 1시간 재알림 발급 ({strength.value})")
        except Exception as e:
            logger.warning(f"{ticker} _maybe_reconfirm_existing 예외: {e}")


    def _bn2_client_intent_supported(self) -> bool:
        mode = str(getattr(self.broker, "mode", "") or "").lower()
        return mode.startswith("alpaca_")

    def _submit_order_with_intent(
        self,
        *,
        side: str,
        ticker: str,
        shares: float,
        purpose: str,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        metadata: Optional[dict] = None,
        exit_reason: str = "",
        approval_request_id: str = "",
        seed: str = "",
    ):
        pending_mgr = getattr(self, "pending_order_manager", None)
        if pending_mgr is None or not self._bn2_client_intent_supported():
            if str(side).upper() == "BUY":
                return self.broker.place_buy(ticker, shares, order_type) if float(price or 0.0) == 0.0 else self.broker.place_buy(ticker, shares, order_type, price)
            return self.broker.place_sell(ticker, shares, order_type) if float(price or 0.0) == 0.0 else self.broker.place_sell(ticker, shares, order_type, price)

        cid = pending_mgr.make_client_order_id(
            ticker=ticker,
            side=str(side).lower(),
            purpose=purpose,
            seed=seed or f"{ticker}|{side}|{purpose}|{shares:g}|{price:g}",
        )
        pending_mgr.create_submitting_intent(
            client_order_id=cid,
            ticker=ticker,
            side=str(side).lower(),
            purpose=purpose,
            requested_shares=shares,
            metadata=metadata or {},
            exit_reason=exit_reason,
            approval_request_id=approval_request_id,
        )
        try:
            order = (
                self.broker.place_buy(ticker, shares, order_type, price, client_order_id=cid)
                if str(side).upper() == "BUY"
                else self.broker.place_sell(ticker, shares, order_type, price, client_order_id=cid)
            )
        except Exception:
            recovered = pending_mgr.resolve_submit_exception(cid)
            if recovered is not None:
                logger.warning("[BN2-RECOVERED-AFTER-SUBMIT-ERROR] %s cid=%s id=%s", ticker, cid, recovered.order_id)
                return recovered
            raise
        if not getattr(order, "client_order_id", ""):
            order.client_order_id = cid
        pending_mgr.mark_submitted(
            cid,
            order,
            purpose=purpose,
            metadata=metadata or {},
            exit_reason=exit_reason,
            approval_request_id=approval_request_id,
        )
        return order

    def _maybe_request_approval(self, ticker, fill_price, rb, sig) -> None:
        if sig is None:
            return
        try:
            try:
                from engine.market.context import get_market_context
                ctx = get_market_context()
                market_score = ctx.score
                market_regime = ctx.regime
                sector_score = ctx.sector_strength.get(getattr(rb, "sector_name", ""), 50.0)
                buy_mult = ctx.buy_multiplier
            except Exception:
                market_score, market_regime, sector_score, buy_mult = 50.0, "neutral", 50.0, 1.0
            win_rate = float(getattr(rb, "win_rate", 0.0) or 0.0)
            score = float(getattr(sig, "score", 0.0) or 0.0)
            threshold = float(getattr(sig, "threshold", 0.0) or 0.0)
            strength = classify_strength(score=score, threshold=threshold, win_rate=win_rate, regime=market_regime, sector_score=sector_score)
            if strength is None:
                logger.debug(f"{ticker} 강한 시그널 아님 (score={score:.2f}/{threshold:.2f})")
                return
            reasons = list(getattr(sig, "reasons", []) or [])
            pos = self.position_manager.get(ticker)
            if pos is None:
                logger.warning(f"{ticker} ApprovalRequest 생성 실패: PositionEntry 없음")
                return
            req = self.approval_manager.create_request(
                ticker=ticker, strength=strength, current_price=fill_price,
                signal_score=score, signal_threshold=threshold, signal_reasons=reasons,
                win_rate=win_rate, fitness=float(getattr(rb, "fitness", 0.0) or 0.0),
                target_price=pos.target_price, stop_price=pos.stop_price,
                trailing_stop=pos.trailing_stop, max_holding_days=pos.max_holding_days,
                market_score=market_score, market_regime=market_regime,
                sector_score=sector_score, buy_multiplier=buy_mult,
            )
            pos.signal_score_at_entry = score
            pos.signal_threshold_at_entry = threshold
            self.position_manager._save()
            try:
                self.notifier.send_approval_request(req)
                logger.info(f"[APPROVAL] {ticker} {strength.value} 알림 발송 (req={req.request_id[:8]})")
            except Exception as ne:
                logger.warning(f"{ticker} approval 알림 발송 실패: {ne}")
        except Exception as e:
            logger.error(f"{ticker} _maybe_request_approval 예외: {e}")

    def _try_order(self, side: str, ticker: str, price: float, reason: str, signal_result=None) -> None:
        self.stats.orders_attempted += 1
        pending_mgr = getattr(self, "pending_order_manager", None)
        tick_locks = getattr(self, "_tick_locked_tickers", set())
        if (pending_mgr is not None and pending_mgr.is_ticker_locked(ticker)) or ticker in tick_locks:
            self.stats.orders_blocked += 1
            logger.info(f"{ticker} {side} 차단: pending 주문/tick 잠금")
            return
        buy_preflight: Optional[BuyPreflight] = None
        if side == "BUY":
            try:
                buy_preflight = self._get_buy_reconciler().preflight(ticker)
            except Exception as preflight_exc:
                self.stats.orders_blocked += 1
                logger.error(f"{ticker} BUY preflight 차단: {preflight_exc}")
                self.notifier.send_error(f"{ticker} BUY preflight 차단: {preflight_exc}")
                return
        if side == "SELL":
            holdings = {h.ticker: h for h in self.broker.get_holdings()}
            if ticker not in holdings or holdings[ticker].shares <= 0:
                logger.debug(f"{ticker} SELL 시그널이지만 포지션 없음, 스킵")
                return
        order_shares = self._resolve_order_shares(side, ticker, price)
        if order_shares <= SHARE_EPS:
            self.stats.orders_blocked += 1
            logger.info(f"{ticker} {side} 차단: 주문 수량 계산 결과 0")
            return
        check = self.safety.check_order(side, ticker, order_shares, price, purpose="entry")
        if not check.allowed:
            self.stats.orders_blocked += 1
            logger.info(f"{ticker} {side} 차단: [{check.code}] {check.reason}")
            self.notifier.send_safety_block(check.code, f"{ticker} {side}: {check.reason}")
            return

        try:
            order = self._submit_order_with_intent(
                side=side,
                ticker=ticker,
                shares=order_shares,
                purpose="entry",
                order_type=OrderType.MARKET,
                metadata={"reason": reason},
                seed=f"signal|{ticker}|{side}|{reason}|{self.stats.market_ticks}",
            )
            self.safety.record_order(order, side, purpose="entry")
            if not hasattr(self, "_tick_locked_tickers"):
                self._tick_locked_tickers = set()
            self._tick_locked_tickers.add(ticker)
            if order.status != OrderStatus.FILLED and pending_mgr is not None:
                pending_mgr.track_order(order, purpose="entry", metadata={"reason": reason})
                self.notifier.send_order(order)
                logger.info(f"{ticker} {side} pending 추적 시작: id={order.order_id} status={order.status.value}")
                return
            if order.status == OrderStatus.FILLED:
                self.stats.orders_filled += 1
                if side == "BUY":
                    try:
                        self._get_buy_reconciler().reconcile(order, purpose="entry", preflight=buy_preflight)
                        self._maybe_request_approval(ticker, float(order.filled_avg_price or price), buy_preflight.rulebook if buy_preflight else None, signal_result)
                    except Exception as reconcile_exc:
                        self._get_buy_reconciler().track_failure(
                            order,
                            purpose="entry",
                            error=reconcile_exc,
                            metadata={"reason": reason},
                        )
                        return
            self.notifier.send_order(order)
            logger.info(f"{ticker} {side} 발주 완료: shares={order_shares:g} id={order.order_id} status={order.status.value}")
        except Exception as e:
            self.stats.orders_blocked += 1
            logger.error(f"{ticker} {side} 주문 실패: {e}")
            self.notifier.send_error(f"{ticker} {side} 주문 실패: {e}")

    def tick_offmarket(self) -> None:
        self.stats.offmarket_ticks += 1
        try:
            self._poll_pending_orders(context="tick_offmarket")
            logger.debug(f"tick_offmarket #{self.stats.offmarket_ticks}")
            if not self.broker.health_check():
                self.notifier.send_error("브로커 health_check 실패")
            try:
                ctx = build_market_context(force_refresh=True)
                self.stats.market_refreshes += 1
                logger.info(f"MarketContext 갱신: score={ctx.score:.1f} regime={ctx.regime} buy_mult={ctx.buy_multiplier:.2f}")
                prev = self.stats.last_regime
                if prev and prev != ctx.regime:
                    try:
                        self.notifier.send(
                            f"📈 시장 국면 변경\n  {prev} → {ctx.regime}\n  score: {ctx.score:.1f}\n  buy_multiplier: {ctx.buy_multiplier:.2f}"
                        )
                    except Exception as ne:
                        logger.warning(f"regime 변경 알림 실패: {ne}")
                self.stats.last_regime = ctx.regime
            except Exception as me:
                logger.error(f"MarketContext 갱신 실패: {me}")
        except Exception as e:
            self._handle_error("tick_offmarket", e)

    def _poll_pending_orders(self, context: str = "") -> None:
        """pending 주문을 선행 재조회하고, 최종화 가능한 체결만 한 번 정산한다."""
        if not hasattr(self, "pending_order_manager") or self.pending_order_manager is None:
            return
        events = self.pending_order_manager.poll_all()
        for record, order in events:
            try:
                self._finalize_pending_order(record, order)
                self.pending_order_manager.mark_finalized(record.order_id)
                logger.info(
                    f"[PENDING-FINALIZED] {context} {record.ticker} {record.side} "
                    f"id={record.order_id} status={order.status.value} filled={order.filled_shares:g}"
                )
            except Exception as exc:
                logger.error(f"[PENDING-RECONCILE-ERROR] {record.order_id}: {exc}")
                self.pending_order_manager.mark_reconcile_error(record.order_id, str(exc))

    def _finalize_pending_order(self, record, order) -> None:
        side = str(record.side or getattr(order.side, "value", order.side)).lower()
        purpose = str(record.purpose or "entry")
        filled_shares = float(order.filled_shares or 0.0)
        if filled_shares <= SHARE_EPS:
            return
        self.safety.record_fill(order, side, purpose=purpose)
        if side == "buy":
            try:
                result = self._get_buy_reconciler().reconcile(order, purpose=purpose)
            except Exception as reconcile_exc:
                self._get_buy_reconciler().track_failure(
                    order,
                    purpose=purpose,
                    approval_request_id=str(getattr(record, "approval_request_id", "") or ""),
                    error=reconcile_exc,
                    metadata=dict(getattr(record, "metadata", {}) or {}),
                )
                raise
            if purpose == "add_buy":
                rid = str(getattr(record, "approval_request_id", "") or "")
                req = self.approval_manager.get_request(rid) if rid else None
                if req is not None:
                    req.status = "executed"
                    self.approval_manager._save()
            logger.info("[BUY-RECONCILED] %s purpose=%s result=%s", order.ticker, purpose, result)
        elif side == "sell":
            self.position_manager.finalize_sell_fill(
                order,
                exit_reason=str(getattr(record, "exit_reason", "") or record.purpose or "manual"),
                broker=self.broker,
                notifier=self.notifier,
            )

    def daily_summary(self) -> None:
        try:
            balance = self.broker.get_balance()
            holdings = balance.holdings
            pnl_total = sum(h.unrealized_pnl for h in holdings)
            holdings_lines = [
                f"  {h.ticker}: {h.shares:g}주 평가 {h.market_value:,.0f}원 ({h.unrealized_pnl_pct:+.2f}%)"
                for h in holdings
            ] or ["  (없음)"]
            msg = (
                f"📊 일일 요약 ({datetime.now(ZoneInfo('Asia/Seoul')):%Y-%m-%d})\n--- 잔고 ---\n"
                f"현금: {balance.cash_krw:,.0f}원\n평가: {balance.total_value_krw:,.0f}원\n"
                f"평가손익: {pnl_total:+,.0f}원\n--- 보유 ---\n" + "\n".join(holdings_lines)
                + "\n--- 활동 ---\n"
                f"장중 tick: {self.stats.market_ticks}회\n시그널: BUY {self.stats.signals_buy} / "
                f"SELL {self.stats.signals_sell} / HOLD {self.stats.signals_hold}\n"
                f"주문: 시도 {self.stats.orders_attempted} / 체결 {self.stats.orders_filled} / 차단 {self.stats.orders_blocked}"
            )
            self.notifier.send(msg)
            logger.info("daily_summary 전송 완료")
            self.stats.reset_daily()
        except Exception as e:
            self._handle_error("daily_summary", e)

    def _handle_error(self, where: str, e: Exception) -> None:
        tb = traceback.format_exc()
        logger.error(f"[{where}] 실패: {e}\n{tb}")
        self.stats.last_error = f"{where}: {e}"
        try:
            self.notifier.send_error(f"[{where}] {e}")
        except Exception:
            pass
