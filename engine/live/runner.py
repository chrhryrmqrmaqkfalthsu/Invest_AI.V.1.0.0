"""
Runner - 라이브 트레이딩 메인 로직.

Scheduler가 시계라면, Runner는 그 시계 신호 받아서 실제로 일하는 친구.

콜백 4종:
  - startup_check()    : 봇 가동시 1회. 토큰/잔고/텔레그램 점검.
  - tick_market()      : 장중 1분마다. 시그널 평가 → 안전성 체크 → 주문.
  - tick_offmarket()   : 장외 60분마다. 시세 캐싱/헬스체크.
  - daily_summary()    : 매일 16:00. 손익/체결 요약 전송.

의존성:
  - broker        : PaperBroker | KisBroker
  - safety        : SafetyLayer
  - notifier      : TelegramNotifier
  - clock         : MarketClock (KrxMarketClock 등)
  - rulebook      : RuleBook (DemoRuleBook 등)
  - symbols       : List[str]   매매 대상 종목

예외 처리:
  - 모든 콜백은 try/except로 감싸서 1회 실패가 다음 tick을 막지 않도록.
  - 에러는 logger + telegram.send_error로 통지.
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from engine.live.broker.base import Broker, Order, OrderType, OrderStatus
from engine.live.market_clock import MarketClock
from engine.live.safety.layer import SafetyLayer
from engine.live.position_manager import PositionManager
from engine.live.approval_manager import (
    ApprovalManager, classify_strength, SignalStrength
)
from engine.market.context import build_market_context
from engine.live.telegram.notifier import TelegramNotifier
from engine.strategies.demo_rulebook import RuleBook, Signal

logger = logging.getLogger("runner")


@dataclass
class RunnerStats:
    """Runner 누적 통계 (메모리 보관, 일일 요약용)"""
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
        """일일 요약 전송 후 리셋"""
        self.market_ticks = 0
        self.offmarket_ticks = 0
        self.signals_buy = 0
        self.signals_sell = 0
        self.signals_hold = 0
        self.orders_attempted = 0
        self.orders_filled = 0
        self.orders_blocked = 0


class Runner:
    """
    Live trading 메인 클래스.
    Scheduler가 등록한 콜백을 시간에 맞춰 호출하면,
    Runner가 broker/safety/notifier/rulebook 조합해서 실제 작업 수행.
    """

    def __init__(
        self,
        broker: Broker,
        safety: SafetyLayer,
        notifier: TelegramNotifier,
        clock: MarketClock,
        rulebook: RuleBook,
        symbols: List[str],
        order_shares: int = 1,
    ):
        self.broker = broker
        self.safety = safety
        self.notifier = notifier
        self.clock = clock
        self.rulebook = rulebook
        self.symbols = list(symbols)
        self.order_shares = order_shares
        self.stats = RunnerStats(started_at=datetime.now(ZoneInfo("Asia/Seoul")))
        logger.info(
            f"Runner 초기화: mode={broker.mode} symbols={len(self.symbols)}개 "
            f"rulebook={rulebook.name()}"
        )
        self.position_manager = PositionManager()
        self.approval_manager = ApprovalManager()

    # ==========================================================
    # Hot-reload: 학습 완료 후 신규 종목 동적 편입
    # ==========================================================
    def reload_symbols(self) -> dict:
        """
        data/symbols/ 디렉토리 재스캔 → 신규 종목을 self.symbols 에 추가.
        Rulebook 의 None 캐시도 invalidate 해서 다음 tick 부터 학습 결과 사용 가능.

        Returns:
            {"added": [신규 ticker 리스트], "total": 현재 추적 종목 수}
        """
        from pathlib import Path as _P
        symbols_dir = _P("data/symbols")
        if not symbols_dir.exists():
            return {"added": [], "total": len(self.symbols)}

        current = set(self.symbols)
        on_disk = {d.name for d in symbols_dir.iterdir() if d.is_dir()}
        # parameters.json 이 실제로 존재하는 종목만 (빈 디렉토리 제외)
        valid = {t for t in on_disk if (symbols_dir / t / "parameters.json").exists()}
        added = sorted(valid - current)

        if added:
            self.symbols.extend(added)
            # Rulebook 캐시 invalidate (None 으로 저장된 미학습 항목들)
            try:
                cache = getattr(self.rulebook, "_rulebook_cache", None)
                if isinstance(cache, dict):
                    for t in added:
                        cache.pop(t, None)
                logger.info(f"[HOT-RELOAD] 신규 종목 편입: {added} (총 {len(self.symbols)}개)")
            except Exception as e:
                logger.warning(f"[HOT-RELOAD] rulebook 캐시 invalidate 실패: {e}")

        return {"added": added, "total": len(self.symbols)}

    # ==========================================================
    # 콜백 1: 가동 점검
    # ==========================================================
    def attach_bot(self, bot) -> None:
        """TelegramBot에 PositionManager/ApprovalManager/Rulebook 주입."""
        bot.position_manager = self.position_manager
        bot.approval_manager = self.approval_manager
        bot.rulebook = self.rulebook
        logger.info("TelegramBot에 PositionManager/ApprovalManager/Rulebook 주입 완료")

    def startup_check(self) -> None:
        """봇 가동시 1회. 모든 의존성 살아있는지 확인 후 텔레그램에 보고."""
        try:
            logger.info("startup_check 시작...")
            # 1) 브로커 health
            ok = self.broker.health_check()
            if not ok:
                raise RuntimeError("broker.health_check() = False")

            # 2) 잔고 조회
            balance = self.broker.get_balance()

            # 3) 종목별 현재가 1회 조회 (워밍업)
            warmup = []
            for t in self.symbols:
                p = self.broker.get_current_price(t)
                warmup.append(f"  {t}: {p:,.0f}원" if p else f"  {t}: 조회 실패")

            # 4) 텔레그램 알림
            msg = (
                f"🚀 Kingmaker 가동\n"
                f"모드: {self.broker.mode}\n"
                f"룰북: {self.rulebook.name()}\n"
                f"종목: {len(self.symbols)}개\n"
                f"현금: {balance.cash_krw:,.0f}원\n"
                f"평가: {balance.total_value_krw:,.0f}원\n"
                f"보유: {len(balance.holdings)}개\n"
                f"--- 현재가 ---\n"
                + "\n".join(warmup)
            )
            self.notifier.send(msg)
            logger.info("startup_check 완료")

        except Exception as e:
            self._handle_error("startup_check", e)

    # ==========================================================
    # 콜백 2: 장중 1분 tick
    # ==========================================================
    def tick_market(self) -> None:
        """장중 매 분. 시그널 평가 → 안전체크 → 주문."""
        # 1) 보유 포지션 자동 청산 체크 (손절/익절/트레일링/만기)
        try:
            exited = self.position_manager.check_exits(self.broker, self.notifier)
            if exited:
                logger.info(f"자동 청산 {len(exited)}건 완료")
        except Exception as e:
            self._handle_error("position_manager.check_exits", e)

        # 2) 사용자 승인된 추가매수 요청 처리 + 재평가
        try:
            self._process_pending_approvals()
        except Exception as e:
            self._handle_error("_process_pending_approvals", e)

        self.stats.market_ticks += 1
        try:
            logger.debug(f"tick_market #{self.stats.market_ticks}")
            for ticker in self.symbols:
                self._process_ticker(ticker)
        except Exception as e:
            self._handle_error("tick_market", e)

    def _process_ticker(self, ticker: str) -> None:
        """종목 1개 처리 (시그널 → 안전체크 → 주문)"""
        # 1) 현재가
        price = self.broker.get_current_price(ticker)
        if price is None:
            logger.warning(f"{ticker} 현재가 조회 실패")
            return

        # 1.5) 이미 보유 + 강한 시그널 유지 → 1시간마다 재알림
        self._maybe_reconfirm_existing(ticker, price)

        # 2) 시그널 평가
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
        """매 tick: approved 요청 실행 + reevaluating 요청 재평가."""
        if not self.approval_manager:
            return
        # status별로 처리 (approved 먼저, reevaluating 다음)
        all_reqs = list(self.approval_manager._requests.values())
        for req in all_reqs:
            if req.status == "approved":
                self._execute_approved(req)
            elif req.status == "reevaluating":
                self._reevaluate_request(req)

    def _execute_approved(self, req) -> None:
        """승인된 요청 → 한도 일시 상향 → 추가 매수 → add_to_position."""
        ticker = req.ticker
        amount = req.approved_krw
        if amount <= 0:
            req.status = "rejected"
            self.approval_manager._save()
            return

        try:
            price = self.broker.get_current_price(ticker)
            if price is None or price <= 0:
                logger.warning(f"[APPROVAL-EXEC] {ticker} 현재가 조회 실패")
                return
            shares = max(1, int(amount / price))

            # SafetyLayer 한도 일시 상향 (max_krw + max_shares + max_total_invested)
            original_max_krw      = getattr(self.safety, "max_krw", None)
            original_max_shares   = getattr(self.safety, "max_shares", None)
            original_max_total    = getattr(self.safety, "max_total_invested", None)
            try:
                if original_max_krw is not None:
                    self.safety.max_krw = max(float(original_max_krw), float(amount) + price * 2)
                if original_max_shares is not None:
                    self.safety.max_shares = max(int(original_max_shares), int(shares))
                if original_max_total is not None:
                    # 추가 매수분만큼 누적한도 임시 상향 (현 한도 + 이번 주문액 + 여유)
                    self.safety.max_total_invested = float(original_max_total) + float(amount) + price * 2
                # 안전 체크
                check = self.safety.check_order("BUY", ticker, shares, price)
                if not check.allowed:
                    logger.warning(f"[APPROVAL-EXEC] {ticker} 안전체크 차단: [{check.code}] {check.reason}")
                    self.notifier.send(f"⛔ `{ticker}` 추가매수 차단: [{check.code}] {check.reason}", parse_mode="Markdown")
                    req.status = "rejected"
                    self.approval_manager._save()
                    return

                # 매수 발주
                order = self.broker.place_buy(ticker, shares, OrderType.MARKET)
                self.safety.record_order(order, "BUY")

                if order.status == OrderStatus.FILLED:
                    self.stats.orders_filled += 1
                    fill_price = order.filled_avg_price or price
                    # add_to_position으로 평균가/stop/target 재계산
                    atr = self.rulebook.get_last_atr(ticker) if hasattr(self.rulebook, "get_last_atr") else None
                    rb = self.rulebook.get_rulebook(ticker) if hasattr(self.rulebook, "get_rulebook") else None
                    if atr and rb:
                        self.position_manager.add_to_position(ticker, fill_price, shares, rb, atr)
                    self.notifier.send(
                        f"✅ `{ticker}` 추가매수 체결: {shares}주 @ {fill_price:,.0f} (req={req.request_id[:8]})",
                        parse_mode="Markdown",
                    )
                    logger.info(f"[APPROVAL-EXEC] {ticker} 추가매수 체결 {shares}주 @ {fill_price:,.0f}")
                else:
                    self.notifier.send(f"⚠️ `{ticker}` 추가매수 미체결: status={order.status.value}", parse_mode="Markdown")
            finally:
                if original_max_krw is not None:
                    self.safety.max_krw = original_max_krw
                if original_max_shares is not None:
                    self.safety.max_shares = original_max_shares
                if original_max_total is not None:
                    self.safety.max_total_invested = original_max_total

            # 요청 완료 처리
            req.status = "executed"
            self.approval_manager._save()

        except Exception as e:
            logger.error(f"[APPROVAL-EXEC] {ticker} 실행 예외: {e}")
            self.notifier.send(f"❌ `{ticker}` 추가매수 실행 실패: {e}", parse_mode="Markdown")
            req.status = "rejected"
            self.approval_manager._save()

    def _reevaluate_request(self, req) -> None:
        """60초 초과 승인 요청 → 현재 시그널 재평가."""
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
                # 통과 → 사용자가 마지막에 요청한 금액으로 진행
                ok, msg_text, _ = self.approval_manager.confirm_after_reeval(
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
            logger.error(f"[APPROVAL-REEVAL] {ticker} 예외: {e}")

    def _maybe_reconfirm_existing(self, ticker: str, price: float) -> None:
        """보유 중 + 강한 시그널 유지 → 1시간마다 추가매수 의사 재확인."""
        if not self.position_manager.get(ticker):
            return
        if not self.approval_manager.should_reconfirm(ticker):
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

            # MarketContext
            try:
                from engine.market.context import get_market_context
                ctx = get_market_context()
                regime = ctx.regime
                sector_score = ctx.sector_strength.get(getattr(rb, "sector_name", ""), 50.0)
            except Exception:
                regime, sector_score = "neutral", 50.0

            strength = classify_strength(
                score=score, threshold=threshold,
                win_rate=win_rate, regime=regime, sector_score=sector_score,
            )
            if strength is None:
                return  # 더 이상 강한 시그널 아님

            # 1시간 재알림 발급
            self._maybe_request_approval(ticker, price, rb, sig)
            self.approval_manager.mark_reconfirmed(ticker)
            logger.info(f"[RECONFIRM] {ticker} 1시간 재알림 발급 ({strength.value})")
        except Exception as e:
            logger.warning(f"{ticker} _maybe_reconfirm_existing 예외: {e}")

    def _maybe_request_approval(self, ticker, fill_price, rb, sig) -> None:
        """매수 직후 강한 시그널이면 ApprovalRequest 생성 + 텔레그램 알림."""
        if sig is None:
            return
        try:
            # MarketContext 가져오기
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

            strength = classify_strength(
                score=score, threshold=threshold,
                win_rate=win_rate, regime=market_regime, sector_score=sector_score,
            )
            if strength is None:
                logger.debug(f"{ticker} 강한 시그널 아님 (score={score:.2f}/{threshold:.2f})")
                return

            # 시그널 reasons 추출
            reasons = []
            try:
                # SignalResult.reason은 "score=... reasons=[...]" 형태
                r_str = getattr(sig, "reason", "") or ""
                if "reasons=" in r_str:
                    raw = r_str.split("reasons=")[-1].strip("[]")
                    reasons = [s.strip(" '\"") for s in raw.split(",") if s.strip()]
            except Exception:
                pass

            # PositionEntry에서 target/stop/trailing/max_holding 조회
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

            # PositionEntry에 진입 시그널 정보 기록
            pos.signal_score_at_entry = score
            pos.signal_threshold_at_entry = threshold
            self.position_manager._save()

            # 텔레그램 알림
            try:
                self.notifier.send_approval_request(req)
                logger.info(f"[APPROVAL] {ticker} {strength.value} 알림 발송 (req={req.request_id[:8]})")
            except Exception as ne:
                logger.warning(f"{ticker} approval 알림 발송 실패: {ne}")

        except Exception as e:
            logger.error(f"{ticker} _maybe_request_approval 예외: {e}")

    def _try_order(self, side: str, ticker: str, price: float, reason: str, signal_result=None) -> None:
        """안전체크 통과시 주문 실행."""
        self.stats.orders_attempted += 1

        # SELL인데 포지션 없으면 스킵
        if side == "SELL":
            holdings = {h.ticker: h for h in self.broker.get_holdings()}
            if ticker not in holdings or holdings[ticker].shares <= 0:
                logger.debug(f"{ticker} SELL 시그널이지만 포지션 없음, 스킵")
                return

        # 안전 체크
        check = self.safety.check_order(side, ticker, self.order_shares, price)
        if not check.allowed:
            self.stats.orders_blocked += 1
            logger.info(f"{ticker} {side} 차단: [{check.code}] {check.reason}")
            self.notifier.send_safety_block(check.code, f"{ticker} {side}: {check.reason}")
            return

        # 주문 실행
        try:
            if side == "BUY":
                order = self.broker.place_buy(ticker, self.order_shares, OrderType.MARKET)
            else:
                order = self.broker.place_sell(ticker, self.order_shares, OrderType.MARKET)

            self.safety.record_order(order, side)

            if order.status == OrderStatus.FILLED:
                self.stats.orders_filled += 1

                # BUY 체결 시 PositionManager에 진입 등록 (자동 손절/익절)
                if side == "BUY" and hasattr(self.rulebook, "get_last_atr"):
                    try:
                        atr = self.rulebook.get_last_atr(ticker)
                        rb = self.rulebook.get_rulebook(ticker)
                        fill_price = order.filled_avg_price or price
                        if atr and rb:
                            self.position_manager.register_entry(
                                ticker, fill_price, self.order_shares, rb, atr
                            )
                            # 강한 시그널이면 추가 매수 승인 요청 발송
                            self._maybe_request_approval(
                                ticker, fill_price, rb, signal_result
                            )
                        else:
                            logger.warning(f"{ticker} register_entry 스킵: atr={atr} rb={rb}")
                    except Exception as e:
                        logger.error(f"{ticker} register_entry 실패: {e}")

            self.notifier.send_order(order)
            logger.info(f"{ticker} {side} 발주 완료: id={order.order_id} status={order.status.value}")

        except Exception as e:
            self.stats.orders_blocked += 1
            logger.error(f"{ticker} {side} 주문 실패: {e}")
            self.notifier.send_error(f"{ticker} {side} 주문 실패: {e}")

    # ==========================================================
    # 콜백 3: 장외 60분 tick
    # ==========================================================
    def tick_offmarket(self) -> None:
        """장외시간 또는 60분 간격. 헬스체크 + 시장 컨텍스트 갱신."""
        self.stats.offmarket_ticks += 1
        try:
            logger.debug(f"tick_offmarket #{self.stats.offmarket_ticks}")
            # 1) 브로커 헬스체크
            ok = self.broker.health_check()
            if not ok:
                self.notifier.send_error("브로커 health_check 실패")

            # 2) MarketContext 갱신 (KOSPI/SP500/VIX/섹터/이벤트)
            try:
                ctx = build_market_context(force_refresh=True)
                self.stats.market_refreshes += 1
                logger.info(
                    f"MarketContext 갱신: score={ctx.score:.1f} "
                    f"regime={ctx.regime} buy_mult={ctx.buy_multiplier:.2f}"
                )
                # regime 변동 시 텔레그램 알림
                prev = self.stats.last_regime
                if prev and prev != ctx.regime:
                    try:
                        self.notifier.send(
                            f"📈 시장 국면 변경\n"
                            f"  {prev} → {ctx.regime}\n"
                            f"  score: {ctx.score:.1f}\n"
                            f"  buy_multiplier: {ctx.buy_multiplier:.2f}"
                        )
                    except Exception as ne:
                        logger.warning(f"regime 변경 알림 실패: {ne}")
                self.stats.last_regime = ctx.regime
            except Exception as me:
                logger.error(f"MarketContext 갱신 실패: {me}")

        except Exception as e:
            self._handle_error("tick_offmarket", e)

    # ==========================================================
    # 콜백 4: 일일 요약
    # ==========================================================
    def daily_summary(self) -> None:
        """매일 16:00. 손익/체결 요약 전송 후 stats 리셋."""
        try:
            balance = self.broker.get_balance()
            holdings = balance.holdings

            pnl_total = sum(h.unrealized_pnl for h in holdings)
            holdings_lines = [
                f"  {h.ticker}: {h.shares}주 평가 {h.market_value:,.0f}원 "
                f"({h.unrealized_pnl_pct:+.2f}%)"
                for h in holdings
            ] or ["  (없음)"]

            msg = (
                f"📊 일일 요약 ({datetime.now(ZoneInfo('Asia/Seoul')):%Y-%m-%d})\n"
                f"--- 잔고 ---\n"
                f"현금: {balance.cash_krw:,.0f}원\n"
                f"평가: {balance.total_value_krw:,.0f}원\n"
                f"평가손익: {pnl_total:+,.0f}원\n"
                f"--- 보유 ---\n"
                + "\n".join(holdings_lines)
                + "\n--- 활동 ---\n"
                f"장중 tick: {self.stats.market_ticks}회\n"
                f"시그널: BUY {self.stats.signals_buy} / "
                f"SELL {self.stats.signals_sell} / HOLD {self.stats.signals_hold}\n"
                f"주문: 시도 {self.stats.orders_attempted} / "
                f"체결 {self.stats.orders_filled} / 차단 {self.stats.orders_blocked}"
            )
            self.notifier.send(msg)
            logger.info("daily_summary 전송 완료")
            self.stats.reset_daily()

        except Exception as e:
            self._handle_error("daily_summary", e)

    # ==========================================================
    # 공통 에러 처리
    # ==========================================================
    def _handle_error(self, where: str, e: Exception) -> None:
        tb = traceback.format_exc()
        logger.error(f"[{where}] 실패: {e}\n{tb}")
        self.stats.last_error = f"{where}: {e}"
        try:
            self.notifier.send_error(f"[{where}] {e}")
        except Exception:
            pass  # 텔레그램까지 실패하면 조용히 넘김


# ==========================================================
# 단위 테스트
# ==========================================================
if __name__ == "__main__":
    import logging as _lg
    _lg.basicConfig(
        level=_lg.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )

    print("=" * 60)
    print("Runner 단위 테스트 (mock 의존성)")
    print("=" * 60)

    from engine.live.broker.paper import PaperBroker
    from engine.live.market_clock import KrxMarketClock
    from engine.strategies.demo_rulebook import DemoRuleBook

    # 텔레그램 mock (실제 전송 안 함)
    class FakeNotifier:
        def __init__(self):
            self.sent = []
        def send(self, msg): self.sent.append(("INFO", msg))
        def send_error(self, msg): self.sent.append(("ERROR", msg))
        def send_order(self, order): self.sent.append(("ORDER", str(order.to_dict())))
        def send_safety_block(self, code, reason): self.sent.append(("BLOCK", f"[{code}] {reason}"))
        def send_info(self, msg): self.sent.append(("INFO", msg))

    # 셋업
    import os
    os.environ["PAPER_STATE_PATH"] = "/tmp/test_runner_paper.json"
    if os.path.exists("/tmp/test_runner_paper.json"):
        os.remove("/tmp/test_runner_paper.json")

    broker = PaperBroker(initial_cash=1_000_000)
    notifier = FakeNotifier()
    clock = KrxMarketClock()

    # symbols 폴더 화이트리스트 임시 생성
    os.makedirs("data/symbols/TEST_A", exist_ok=True)
    os.makedirs("data/symbols/TEST_B", exist_ok=True)

    # SafetyLayer 셋업 (테스트용: 첫주문 승인 자동, 시장 항상 open)
    from engine.live.safety.state import load as load_state, save as save_state
    safety_state_path = "/tmp/test_runner_safety.json"
    os.environ["SAFETY_STATE_PATH"] = safety_state_path
    if os.path.exists(safety_state_path):
        os.remove(safety_state_path)

    safety = SafetyLayer(broker=broker)
    safety.approve_first_order()  # 테스트라 미리 승인

    # 시장 시간 mock (항상 open)
    clock.is_open = lambda dt=None: True

    rulebook = DemoRuleBook(window=3, stop_loss_pct=0.03)

    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=clock,
        rulebook=rulebook,
        symbols=["TEST_A", "TEST_B"],
        order_shares=1,
    )

    # PaperBroker 현재가 mock (TEST_A: 상승, TEST_B: 횡보)
    prices_a = iter([1000, 1010, 1020, 1100, 1100, 1100, 1100])  # 상승 → BUY
    prices_b = iter([2000, 2000, 2000, 2000, 2000, 2000, 2000])  # 횡보
    def fake_price(t):
        if t == "TEST_A": return next(prices_a, 1100)
        if t == "TEST_B": return next(prices_b, 2000)
        return None
    broker.get_current_price = fake_price

    print("\n[1] startup_check 호출")
    runner.startup_check()
    assert any(s[0] == "INFO" and "Kingmaker 가동" in s[1] for s in notifier.sent), "startup 알림 누락"
    print(f"  ✅ startup 알림 {len([s for s in notifier.sent if s[0]=='INFO'])}건")

    print("\n[2] tick_market 7회 호출")
    for i in range(7):
        runner.tick_market()
    print(f"  signals: BUY={runner.stats.signals_buy} SELL={runner.stats.signals_sell} HOLD={runner.stats.signals_hold}")
    print(f"  orders : 시도={runner.stats.orders_attempted} 체결={runner.stats.orders_filled} 차단={runner.stats.orders_blocked}")
    assert runner.stats.signals_buy >= 1, "TEST_A는 BUY 시그널 떠야 함"

    print("\n[3] tick_offmarket 1회")
    runner.tick_offmarket()
    assert runner.stats.offmarket_ticks == 1

    print("\n[4] daily_summary 호출")
    sent_before = len(notifier.sent)
    runner.daily_summary()
    sent_after = len(notifier.sent)
    assert sent_after > sent_before, "daily_summary 알림 누락"
    assert runner.stats.market_ticks == 0, "리셋 안 됨"
    print(f"  ✅ 요약 전송됨, stats 리셋됨")

    print("\n[5] 에러 핸들링 테스트")
    broker.get_current_price = lambda t: (_ for _ in ()).throw(RuntimeError("price fail"))
    runner.tick_market()  # 예외 발생해도 죽으면 안 됨
    assert "tick_market" in runner.stats.last_error
    print(f"  ✅ 예외 catch됨: {runner.stats.last_error}")

    # 정리
    import shutil
    shutil.rmtree("data/symbols/TEST_A", ignore_errors=True)
    shutil.rmtree("data/symbols/TEST_B", ignore_errors=True)
    for p in ["/tmp/test_runner_paper.json", safety_state_path]:
        if os.path.exists(p): os.remove(p)

    print("\n" + "=" * 60)
    print("✅ Runner 검증 완료")
    print("=" * 60)
