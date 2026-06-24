"""
PositionManager - 보유 종목 자동 청산 매니저.

EXIT_LIVE_POLICY=0 (default): legacy exit authority.
EXIT_LIVE_POLICY=1: positions with an immutable rulebook_snapshot use shared
ExitPolicy; old positions without a snapshot remain on legacy authority.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from engine.live.broker.base import Broker, Order, OrderSide, OrderStatus, OrderType
from engine.live.exit_policy_guard import should_block_legacy_fallback
from engine.strategies.rulebook import Rulebook

log = logging.getLogger("position_manager")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POSITIONS_PATH = PROJECT_ROOT / "data" / "_system" / "positions.json"
TRADE_LOG_PATH = PROJECT_ROOT / "data" / "_system" / "trade_log.csv"
KST = ZoneInfo("Asia/Seoul")
SHARE_ROUND_DIGITS = 6
SHARE_EPS = 10 ** (-SHARE_ROUND_DIGITS)


def _env_enabled(name: str) -> bool:
    return str(os.environ.get(name, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _exit_live_shadow_enabled() -> bool:
    return _env_enabled("EXIT_LIVE_SHADOW")


def _exit_live_policy_enabled() -> bool:
    return _env_enabled("EXIT_LIVE_POLICY")


def _to_shares(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _normalize_shares(value: float) -> float:
    v = round(float(value), SHARE_ROUND_DIGITS)
    return 0.0 if abs(v) <= SHARE_EPS else v


def _mfe_pct(pos) -> Optional[float]:
    entry = _to_float(getattr(pos, "entry_price", 0.0), 0.0)
    high = _to_float(getattr(pos, "highest_price", entry), entry)
    if entry <= 0 or high <= 0:
        return None
    return (high / entry - 1.0) * 100.0


def _mae_pct(pos) -> Optional[float]:
    entry = _to_float(getattr(pos, "entry_price", 0.0), 0.0)
    low = _to_float(getattr(pos, "lowest_price", entry), entry)
    if entry <= 0 or low <= 0:
        return None
    return (low / entry - 1.0) * 100.0


def _resolve_rulebook_for_alert(pos) -> Optional[Rulebook]:
    try:
        from engine.live.exit_policy_adapter import resolve_position_rulebook
        rulebook, _ = resolve_position_rulebook(pos)
        return rulebook
    except Exception:
        return None


@dataclass
class PositionEntry:
    """단일 포지션의 청산 메타데이터."""

    ticker: str
    entry_date: str
    entry_price: float
    shares: float
    atr_at_entry: float
    stop_price: float
    target_price: float
    trailing_distance: float
    trailing_stop: float
    highest_price: float
    lowest_price: float
    exit_strategy: str
    max_holding_days: int
    rulebook_direction: str
    win_rate_at_entry: float = 0.0
    signal_score_at_entry: float = 0.0
    signal_threshold_at_entry: float = 0.0
    total_invested_krw: float = 0.0
    # C-P1 cutover fields. Defaults preserve old positions.json compatibility.
    rulebook_snapshot: dict = field(default_factory=dict)
    member_hash: str = ""
    entry_market_score: float = 50.0
    entry_vix_level: float = 18.0
    entry_sector_score: float = 50.0
    add_buy_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["shares"] = _normalize_shares(d.get("shares", 0.0))
        entry_price = _to_float(d.get("entry_price"), 0.0)
        if _to_float(d.get("lowest_price"), 0.0) <= 0 and entry_price > 0:
            d["lowest_price"] = entry_price
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PositionEntry":
        known = set(cls.__dataclass_fields__)
        raw = dict(d)
        data = {k: v for k, v in raw.items() if k in known}
        data["shares"] = _normalize_shares(_to_shares(data.get("shares", 0.0)))
        entry_price = _to_float(data.get("entry_price"), 0.0)
        if "lowest_price" not in data or _to_float(data.get("lowest_price"), 0.0) <= 0:
            data["lowest_price"] = entry_price or _to_float(data.get("highest_price"), 0.0)
        return cls(**data)


class PositionManager:
    """보유 종목 자동 청산 매니저."""

    def __init__(self):
        self._positions: Dict[str, PositionEntry] = {}
        self._load_error: str = ""
        self._load()
        log.info(f"PositionManager 초기화: 추적 중 {len(self._positions)}건")

    @property
    def load_error(self) -> str:
        """Non-empty when positions.json could not be trusted at startup/load time."""
        return self._load_error

    def _load(self) -> None:
        self._load_error = ""
        if not POSITIONS_PATH.exists():
            return
        try:
            with open(POSITIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._positions = {t: PositionEntry.from_dict(d) for t, d in data.items()}
            self._load_error = ""
            log.info(f"positions.json 로드: {len(self._positions)}건")
        except Exception as e:
            self._load_error = str(e)
            log.error(f"positions.json 로드 실패: {e}")
            self._positions = {}

    def _save(self) -> None:
        POSITIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(POSITIONS_PATH, "w", encoding="utf-8") as f:
                json.dump({t: p.to_dict() for t, p in self._positions.items()}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"positions.json 저장 실패: {e}")

    def _ensure_lowest_price(self, pos: PositionEntry) -> None:
        if _to_float(getattr(pos, "lowest_price", 0.0), 0.0) <= 0:
            pos.lowest_price = _to_float(pos.entry_price, 0.0)

    def _track_lowest_price(self, pos: PositionEntry, price: float) -> bool:
        self._ensure_lowest_price(pos)
        cur = _to_float(price, 0.0)
        if cur > 0 and (pos.lowest_price <= 0 or cur < pos.lowest_price):
            pos.lowest_price = cur
            self._save()
            return True
        return False

    def register_entry(
        self,
        ticker: str,
        entry_price: float,
        shares: float,
        rulebook: Rulebook,
        atr_value: float,
        entry_market_context: Optional[dict] = None,
    ) -> PositionEntry:
        """Register a filled entry with immutable rulebook/context snapshot."""
        from engine.core.exit_policy import initialize_position_state
        from engine.core.metadata import compute_member_hash
        from engine.live.exit_policy_adapter import market_context_to_exit_context

        shares = _normalize_shares(shares)
        entry_date = datetime.now(KST).isoformat()
        direction = str(getattr(rulebook, "direction", "long") or "long").lower()
        exit_ctx = market_context_to_exit_context(
            entry_market_context,
            str(getattr(rulebook, "sector_name", "") or ""),
        )
        snapshot = rulebook.to_dict() if hasattr(rulebook, "to_dict") else {}
        member_hash = compute_member_hash(rulebook)

        if direction == "long":
            state = initialize_position_state(
                ticker=ticker,
                entry_price=entry_price,
                shares=shares,
                rulebook=rulebook,
                atr_value=atr_value,
                market_context=exit_ctx,
                entry_date=entry_date,
                member_hash=member_hash,
            )
            stop = state.stop_price
            target = state.target_price
            trail_dist = state.trailing_distance
            trailing = state.trailing_stop
        else:
            # ExitPolicy is long-only. Preserve legacy registration for old short/inverse paths.
            stop = entry_price - rulebook.stop_loss_atr * atr_value
            target = entry_price + rulebook.take_profit_atr * atr_value
            trail_dist = rulebook.trailing_atr * atr_value
            trailing = entry_price - trail_dist

        entry = PositionEntry(
            ticker=ticker,
            entry_date=entry_date,
            entry_price=entry_price,
            shares=shares,
            atr_at_entry=atr_value,
            stop_price=stop,
            target_price=target,
            trailing_distance=trail_dist,
            trailing_stop=trailing,
            highest_price=entry_price,
            lowest_price=entry_price,
            exit_strategy=rulebook.exit_strategy,
            max_holding_days=int(rulebook.max_holding_days),
            rulebook_direction=rulebook.direction,
            win_rate_at_entry=float(getattr(rulebook, "win_rate", 0.0) or 0.0),
            total_invested_krw=float(entry_price * shares),
            rulebook_snapshot=dict(snapshot),
            member_hash=member_hash,
            entry_market_score=float(exit_ctx.market_score),
            entry_vix_level=float(exit_ctx.vix_level),
            entry_sector_score=float(exit_ctx.sector_score),
            add_buy_count=0,
        )
        self._positions[ticker] = entry
        self._save()
        log.info(
            f"[ENTRY] {ticker} 등록: shares={shares:g}, entry={entry_price:,.4f} "
            f"stop={stop:,.4f}({(stop/entry_price-1)*100:+.2f}%) "
            f"target={target:,.4f}({(target/entry_price-1)*100:+.2f}%) "
            f"strategy={rulebook.direction}/{rulebook.exit_strategy} member={member_hash[:10]}"
        )
        return entry

    def add_to_position(
        self,
        ticker: str,
        add_price: float,
        add_shares: float,
        rulebook,
        atr_value: float,
    ) -> Optional[PositionEntry]:
        """Update average cost/levels after an approved add-buy fill.

        BQ-2a safety invariant: add-buy may never create a new position. If a
        stale approval is executed after the tracked position disappeared, fail
        closed instead of delegating to register_entry().
        """
        add_shares = _normalize_shares(add_shares)
        pos = self._positions.get(ticker)
        if pos is None:
            log.error(f"{ticker} add_to_position: 기존 포지션 없음 → stale 추가매수 차단")
            return None
        self._ensure_lowest_price(pos)

        # Snapshot-backed long positions use the same add-buy state transition as backtests.
        if pos.rulebook_snapshot and str(pos.rulebook_direction).lower() == "long":
            try:
                from engine.core.exit_policy import update_position_for_add_buy
                from engine.live.exit_policy_adapter import (
                    apply_state_to_position_entry,
                    entry_context_from_position,
                    position_entry_to_state,
                    resolve_position_rulebook,
                )

                snapshot_rulebook, _ = resolve_position_rulebook(pos)
                if snapshot_rulebook is None:
                    raise ValueError("position snapshot unavailable")
                prev_lowest = pos.lowest_price
                state = position_entry_to_state(pos, snapshot_rulebook, holding_trading_days=0)
                updated = update_position_for_add_buy(
                    state,
                    add_price=add_price,
                    add_shares=add_shares,
                    rulebook=snapshot_rulebook,
                    atr_value=atr_value,
                    market_context=entry_context_from_position(pos),
                )
                apply_state_to_position_entry(pos, updated)
                pos.lowest_price = min(prev_lowest, _to_float(add_price, prev_lowest), _to_float(pos.entry_price, prev_lowest))
                pos.shares = _normalize_shares(pos.shares)
                pos.total_invested_krw = float(pos.entry_price * pos.shares)
                self._save()
                log.info(
                    f"[ADD-BUY][POLICY] {ticker} +{add_shares:g}주 @ {add_price:,.4f} → "
                    f"총 {pos.shares:g}주 평균 {pos.entry_price:,.4f}, stop={pos.stop_price:,.4f} "
                    f"target={pos.target_price:,.4f} trail={pos.trailing_stop:,.4f}"
                )
                return pos
            except Exception as e:
                log.warning(f"{ticker} snapshot add-buy update 실패 → legacy 유지: {e}")

        # Legacy path for old/snapshot-less/non-long positions.
        old_shares = _normalize_shares(pos.shares)
        old_invested = pos.entry_price * old_shares
        new_shares = _normalize_shares(old_shares + add_shares)
        new_invested = old_invested + add_price * add_shares
        new_avg = new_invested / new_shares if new_shares > 0 else add_price
        stop = new_avg - rulebook.stop_loss_atr * atr_value
        target = new_avg + rulebook.take_profit_atr * atr_value
        trail_dist = rulebook.trailing_atr * atr_value
        new_trailing = new_avg - trail_dist
        pos.shares = new_shares
        pos.entry_price = new_avg
        pos.atr_at_entry = atr_value
        pos.stop_price = stop
        pos.target_price = target
        pos.trailing_distance = trail_dist
        pos.trailing_stop = max(pos.trailing_stop, new_trailing)
        pos.highest_price = max(pos.highest_price, add_price)
        pos.lowest_price = min(pos.lowest_price, _to_float(add_price, pos.lowest_price), _to_float(new_avg, pos.lowest_price))
        pos.total_invested_krw = float(new_invested)
        pos.add_buy_count += 1
        self._save()
        log.info(
            f"[ADD-BUY][LEGACY] {ticker} +{add_shares:g}주 @ {add_price:,.4f} → "
            f"총 {new_shares:g}주 평균 {new_avg:,.4f}, stop={stop:,.4f} "
            f"target={target:,.4f} trail={pos.trailing_stop:,.4f}"
        )
        return pos

    def unregister(self, ticker: str) -> None:
        if ticker in self._positions:
            del self._positions[ticker]
            self._save()

    def get(self, ticker: str) -> Optional[PositionEntry]:
        return self._positions.get(ticker)

    def all(self) -> List[PositionEntry]:
        return list(self._positions.values())

    def check_exits(self, broker: Broker, notifier=None, pending_manager=None) -> List[dict]:
        exited = []
        for ticker, pos in list(self._positions.items()):
            try:
                exit_info = self._check_one(ticker, pos, broker, notifier, pending_manager=pending_manager)
                if exit_info:
                    exited.append(exit_info)
            except Exception as e:
                log.error(f"{ticker} 청산 체크 실패: {e}")
        return exited

    def _legacy_exit_reason(self, pos: PositionEntry, price: float, holding_days: int) -> Optional[str]:
        """Original live decision/update path used for rollback and old positions."""
        changed = False
        self._ensure_lowest_price(pos)
        if price < pos.lowest_price:
            pos.lowest_price = price
            changed = True
        if price > pos.highest_price:
            pos.highest_price = price
            changed = True
            new_trailing = price - pos.trailing_distance
            if new_trailing > pos.trailing_stop:
                pos.trailing_stop = new_trailing
        if changed:
            self._save()

        strategy = pos.exit_strategy
        if strategy == "fixed":
            if price <= pos.stop_price:
                return "stop_loss"
            if price >= pos.target_price:
                return "take_profit"
            if holding_days >= pos.max_holding_days:
                return "time_out"
        elif strategy == "trailing":
            if price <= pos.trailing_stop:
                return "trailing"
            if holding_days >= pos.max_holding_days:
                return "time_out"
        elif strategy == "hybrid":
            if price >= pos.target_price:
                return "take_profit"
            if price <= pos.trailing_stop:
                return "trailing"
            if price <= pos.stop_price:
                return "stop_loss"
            if holding_days >= pos.max_holding_days:
                return "time_out"
        else:
            log.warning(f"{pos.ticker} unknown exit_strategy: {strategy}")
        return None

    def _evaluate_policy(self, ticker: str, pos: PositionEntry, price: float):
        from engine.live.exit_policy_adapter import (
            count_holding_trading_days,
            evaluate_live_policy,
            resolve_position_rulebook,
        )
        from engine.market.context import get_market_context

        rulebook, source = resolve_position_rulebook(pos)
        if rulebook is None:
            return None
        if str(getattr(rulebook, "direction", "long") or "long").lower() != "long":
            return None
        holding_trading_days = count_holding_trading_days(pos.entry_date, ticker=ticker)
        try:
            raw_market_context = get_market_context()
        except Exception as exc:
            raw_market_context = None
            log.warning(f"{ticker} policy market context fallback: {exc}")
        return evaluate_live_policy(
            ticker=ticker,
            pos=pos,
            price=price,
            rulebook=rulebook,
            raw_market_context=raw_market_context,
            holding_trading_days=holding_trading_days,
            timestamp=datetime.now(KST).isoformat(),
            rulebook_source=source,
        )

    def _apply_policy_update(self, pos: PositionEntry, evaluation) -> None:
        from engine.live.exit_policy_adapter import apply_state_to_position_entry

        updated = evaluation.decision.updated_position
        if updated is None:
            return
        before = (
            pos.highest_price,
            pos.lowest_price,
            pos.trailing_stop,
            pos.stop_price,
            pos.target_price,
            pos.entry_price,
            pos.shares,
            pos.add_buy_count,
        )
        prev_lowest = pos.lowest_price
        apply_state_to_position_entry(pos, updated)
        pos.lowest_price = prev_lowest
        pos.shares = _normalize_shares(pos.shares)
        after = (
            pos.highest_price,
            pos.lowest_price,
            pos.trailing_stop,
            pos.stop_price,
            pos.target_price,
            pos.entry_price,
            pos.shares,
            pos.add_buy_count,
        )
        if after != before:
            self._save()

    def _run_live_exit_shadow(
        self,
        ticker: str,
        pos: PositionEntry,
        price: float,
        holding_calendar_days: int,
        actual_legacy_reason: Optional[str],
        policy_evaluation=None,
    ) -> None:
        """Evaluate/log shadow only. Any exception must never affect orders."""
        from engine.live.exit_policy_adapter import (
            approximate_trading_days,
            evaluate_live_shadow,
            resolve_live_rulebook,
            shadow_record_from_live_policy,
            write_live_shadow_record,
        )
        from engine.market.context import get_market_context

        if policy_evaluation is not None:
            record = shadow_record_from_live_policy(
                policy_evaluation,
                ticker=ticker,
                pos=pos,
                price=price,
                holding_calendar_days=holding_calendar_days,
                actual_legacy_reason=actual_legacy_reason,
                timestamp=datetime.now(KST).isoformat(),
            )
            write_live_shadow_record(record)
            return

        rulebook, source = resolve_live_rulebook(ticker)
        if rulebook is None:
            log.warning(f"{ticker} live exit shadow skip: current live rulebook missing")
            return
        try:
            raw_market_context = get_market_context()
        except Exception as exc:
            raw_market_context = None
            log.warning(f"{ticker} live exit shadow market context fallback: {exc}")
        holding_trading_days = approximate_trading_days(pos.entry_date, ticker=ticker)
        record = evaluate_live_shadow(
            ticker=ticker,
            pos=pos,
            price=price,
            rulebook=rulebook,
            raw_market_context=raw_market_context,
            holding_calendar_days=holding_calendar_days,
            holding_trading_days=holding_trading_days,
            actual_legacy_reason=actual_legacy_reason,
            rulebook_source=source,
            timestamp=datetime.now(KST).isoformat(),
        )
        write_live_shadow_record(record)

    def _emergency_exit_policy_block(self, ticker: str, pos: PositionEntry, reason: str, notifier=None, pending_manager=None) -> None:
        """실계좌 silent legacy fallback을 막고 ticker 잠금을 영속화한다."""
        message = f"[CRITICAL][EXIT-POLICY-GUARD] {ticker} 실계좌 legacy 청산 fallback 차단: {reason}"
        log.error(message)
        if notifier is not None:
            try:
                notifier.send_error(message)
            except Exception as exc:
                log.warning(f"{ticker} ExitPolicy guard 알림 실패: {exc}")
        if pending_manager is not None:
            try:
                order = Order(
                    order_id=f"LOCAL-EXIT-POLICY-{ticker}",
                    ticker=ticker,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    shares=float(pos.shares or 0.0),
                    price=0.0,
                    status=OrderStatus.PENDING,
                    raw_status="exit_policy_guard",
                    message=reason,
                )
                pending_manager.track_order(order, purpose="exit_policy_guard", metadata={"reason": reason})
            except Exception as exc:
                log.error(f"{ticker} ExitPolicy guard 잠금 실패: {exc}")

    def _check_one(self, ticker: str, pos: PositionEntry, broker: Broker, notifier=None, pending_manager=None) -> Optional[dict]:
        price = broker.get_current_price(ticker)
        if price is None:
            log.warning(f"{ticker} 현재가 조회 실패, 청산 체크 skip")
            return None

        holdings = {h.ticker: h for h in broker.get_holdings()}
        held = holdings.get(ticker)
        if not held or _normalize_shares(held.shares) <= SHARE_EPS:
            if pending_manager is not None and pending_manager.has_pending_exit(ticker):
                log.warning(f"{ticker} broker 보유 없음이지만 pending SELL 존재 → unregister 보류")
                return None
            log.info(f"{ticker} broker에 보유 없음 → unregister")
            self.unregister(ticker)
            return None
        actual_shares = _normalize_shares(held.shares)
        self._track_lowest_price(pos, price)

        entry_dt = datetime.fromisoformat(pos.entry_date)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=KST)
        holding_calendar_days = (datetime.now(KST) - entry_dt).days

        if notifier is not None:
            try:
                from engine.live.news_alerts import maybe_send_sell_omen_prealert

                maybe_send_sell_omen_prealert(
                    ticker=ticker,
                    pos=pos,
                    current_price=price,
                    notifier=notifier,
                    asof=datetime.now(KST),
                )
            except Exception as exc:
                log.warning(f"{ticker} sell_omen 사전경고 실패(청산에는 영향 없음): {exc}")

        exit_reason: Optional[str] = None
        policy_evaluation = None
        policy_authority = False
        strict_no_fallback = should_block_legacy_fallback(broker)

        if _exit_live_policy_enabled() and pos.rulebook_snapshot and str(pos.rulebook_direction).lower() == "long":
            try:
                policy_evaluation = self._evaluate_policy(ticker, pos, price)
                if policy_evaluation is not None:
                    policy_authority = True
                    self._apply_policy_update(pos, policy_evaluation)
                    if policy_evaluation.decision.should_exit:
                        exit_reason = policy_evaluation.decision.reason
                else:
                    if strict_no_fallback:
                        self._emergency_exit_policy_block(
                            ticker, pos, "policy evaluation returned None", notifier, pending_manager
                        )
                        return None
                    log.warning(f"{ticker} policy snapshot invalid/non-long → legacy 유지")
                    exit_reason = self._legacy_exit_reason(pos, price, holding_calendar_days)
            except Exception as e:
                if strict_no_fallback:
                    self._emergency_exit_policy_block(
                        ticker, pos, f"ExitPolicy exception: {e}", notifier, pending_manager
                    )
                    return None
                log.error(f"{ticker} ExitPolicy cutover 실패 → legacy 유지: {e}")
                policy_evaluation = None
                policy_authority = False
                exit_reason = self._legacy_exit_reason(pos, price, holding_calendar_days)
        else:
            if strict_no_fallback:
                self._emergency_exit_policy_block(
                    ticker,
                    pos,
                    "EXIT_LIVE_POLICY disabled or snapshot missing/non-long",
                    notifier,
                    pending_manager,
                )
                return None
            if _exit_live_policy_enabled() and not pos.rulebook_snapshot:
                log.warning(f"{ticker} 구 포지션(snapshot 없음) → legacy 유지")
            exit_reason = self._legacy_exit_reason(pos, price, holding_calendar_days)

        if _exit_live_shadow_enabled():
            try:
                if policy_authority and policy_evaluation is not None:
                    from engine.live.exit_policy_adapter import legacy_live_decision
                    legacy_reason = legacy_live_decision(pos, price, holding_calendar_days).get("reason")
                    self._run_live_exit_shadow(
                        ticker, pos, price, holding_calendar_days, legacy_reason,
                        policy_evaluation=policy_evaluation,
                    )
                else:
                    self._run_live_exit_shadow(ticker, pos, price, holding_calendar_days, exit_reason)
            except Exception as e:
                log.warning(f"{ticker} live exit shadow failed (order unaffected): {e}")

        if exit_reason is None:
            return None

        if pending_manager is not None and pending_manager.is_ticker_locked(ticker):
            log.info(f"{ticker} pending 주문 잠금 → 자동 SELL 발사 보류")
            return None

        log.info(
            f"[EXIT-TRIGGER] {ticker} {exit_reason}: price={price:,.4f}, "
            f"entry={pos.entry_price:,.4f}, PnL={(price/pos.entry_price-1)*100:+.2f}%, "
            f"hold_calendar={holding_calendar_days} authority={'ExitPolicy' if policy_authority else 'legacy'}"
        )

        try:
            if pending_manager is not None and str(getattr(broker, "mode", "") or "").lower().startswith("alpaca_"):
                cid = pending_manager.make_client_order_id(
                    ticker=ticker,
                    side="sell",
                    purpose="exit",
                    seed=f"exit|{ticker}|{pos.entry_date}|{pos.member_hash}|{exit_reason}",
                )
                pending_manager.create_submitting_intent(
                    client_order_id=cid,
                    ticker=ticker,
                    side="sell",
                    purpose="exit",
                    requested_shares=actual_shares,
                    exit_reason=exit_reason,
                    metadata={"entry_date": pos.entry_date, "member_hash": pos.member_hash},
                )
                try:
                    order = broker.place_sell(ticker, actual_shares, OrderType.MARKET, client_order_id=cid)
                except Exception:
                    recovered = pending_manager.resolve_submit_exception(cid)
                    if recovered is None:
                        raise
                    order = recovered
                if not getattr(order, "client_order_id", ""):
                    order.client_order_id = cid
                pending_manager.mark_submitted(cid, order, purpose="exit", exit_reason=exit_reason)
            else:
                order = broker.place_sell(ticker, actual_shares, OrderType.MARKET)
        except Exception as e:
            log.error(f"{ticker} 매도 발사 실패: {e}")
            if notifier:
                try:
                    notifier.send_error(f"{ticker} 자동 매도 실패: {e}")
                except Exception:
                    pass
            return None

        # Safety guard: never finalize/unregister before the broker confirms FILLED.
        if order.status != OrderStatus.FILLED:
            if pending_manager is not None:
                pending_manager.track_order(order, purpose="exit", exit_reason=exit_reason)
            log.warning(f"{ticker} 매도 미체결({order.status.value}) → 포지션 유지")
            return None

        filled_price = float(order.filled_avg_price or price)
        filled_shares = _normalize_shares(order.filled_shares or actual_shares)
        pnl_pct = (filled_price - pos.entry_price) / pos.entry_price * 100
        pnl_krw = (filled_price - pos.entry_price) * filled_shares
        mfe_pct = _mfe_pct(pos)
        mae_pct = _mae_pct(pos)
        holding_trading_days = (
            int(policy_evaluation.holding_trading_days)
            if policy_evaluation is not None else holding_calendar_days
        )
        trade_record = {
            "exited_at": datetime.now(KST).isoformat(),
            "ticker": ticker,
            "direction": pos.rulebook_direction,
            "entry_date": pos.entry_date,
            "entry_price": pos.entry_price,
            "exit_price": filled_price,
            "shares": filled_shares,
            "exit_reason": exit_reason,
            "holding_days": holding_trading_days,
            "highest_price": pos.highest_price,
            "lowest_price": pos.lowest_price,
            "mfe_pct": round(mfe_pct, 3) if mfe_pct is not None else "",
            "mae_pct": round(mae_pct, 3) if mae_pct is not None else "",
            "pnl_pct": round(pnl_pct, 3),
            "pnl_krw": round(pnl_krw, 2),
            "exit_strategy": pos.exit_strategy,
        }
        self._append_trade_log(trade_record)

        if notifier:
            try:
                notifier.send_trade_exit(
                    trade_record,
                    order=order,
                    rulebook=_resolve_rulebook_for_alert(pos),
                    mfe_pct=mfe_pct,
                    mae_pct=mae_pct,
                )
            except Exception as e:
                log.warning(f"청산 정본 알림 실패: {e}")

        self.unregister(ticker)
        return trade_record

    def finalize_sell_fill(self, order, exit_reason: str, broker: Broker, notifier=None) -> Optional[dict]:
        """pending SELL 체결분을 한 번만 포지션/trade_log에 반영한다.

        부분 체결은 추적 shares만 줄이고 포지션을 유지한다. 전량 체결은 브로커
        holdings가 0임을 확인한 뒤 unregister한다.
        """
        ticker = str(order.ticker)
        pos = self._positions.get(ticker)
        if pos is None:
            log.warning(f"{ticker} pending SELL 정산 스킵: PositionEntry 없음")
            return None
        self._ensure_lowest_price(pos)
        filled_shares = _normalize_shares(float(order.filled_shares or 0.0))
        if filled_shares <= SHARE_EPS:
            return None
        filled_price = float(order.filled_avg_price or 0.0)
        if filled_price <= 0:
            filled_price = broker.get_current_price(ticker) or pos.entry_price
        if filled_price > 0 and filled_price < pos.lowest_price:
            pos.lowest_price = filled_price
        pnl_pct = (filled_price - pos.entry_price) / pos.entry_price * 100
        pnl_krw = (filled_price - pos.entry_price) * filled_shares
        mfe_pct = _mfe_pct(pos)
        mae_pct = _mae_pct(pos)
        trade_record = {
            "exited_at": datetime.now(KST).isoformat(),
            "ticker": ticker,
            "direction": pos.rulebook_direction,
            "entry_date": pos.entry_date,
            "entry_price": pos.entry_price,
            "exit_price": filled_price,
            "shares": filled_shares,
            "exit_reason": exit_reason or "pending_sell",
            "holding_days": max(0, (datetime.now(KST) - datetime.fromisoformat(pos.entry_date).replace(tzinfo=KST) if datetime.fromisoformat(pos.entry_date).tzinfo is None else datetime.now(KST) - datetime.fromisoformat(pos.entry_date)).days),
            "highest_price": pos.highest_price,
            "lowest_price": pos.lowest_price,
            "mfe_pct": round(mfe_pct, 3) if mfe_pct is not None else "",
            "mae_pct": round(mae_pct, 3) if mae_pct is not None else "",
            "pnl_pct": round(pnl_pct, 3),
            "pnl_krw": round(pnl_krw, 2),
            "exit_strategy": pos.exit_strategy,
        }
        self._append_trade_log(trade_record)

        rulebook_for_alert = _resolve_rulebook_for_alert(pos)

        remaining = _normalize_shares(float(pos.shares or 0.0) - filled_shares)
        broker_remaining = None
        try:
            holdings = {h.ticker: h for h in broker.get_holdings()}
            broker_remaining = _normalize_shares(float(getattr(holdings.get(ticker), "shares", 0.0) or 0.0))
        except Exception as exc:
            log.warning(f"{ticker} pending SELL 정산 중 holdings 재조회 실패: {exc}")

        if remaining <= SHARE_EPS and (broker_remaining is None or broker_remaining <= SHARE_EPS):
            self.unregister(ticker)
        else:
            pos.shares = broker_remaining if broker_remaining is not None and broker_remaining > SHARE_EPS else max(remaining, 0.0)
            pos.total_invested_krw = float(pos.entry_price * pos.shares)
            self._save()

        if notifier:
            try:
                notifier.send_trade_exit(
                    trade_record,
                    order=order,
                    rulebook=rulebook_for_alert,
                    mfe_pct=mfe_pct,
                    mae_pct=mae_pct,
                )
            except Exception as e:
                log.warning(f"pending SELL 정본 알림 실패: {e}")
        return trade_record

    def _append_trade_log(self, record: dict) -> None:
        TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_exists = TRADE_LOG_PATH.exists()
        try:
            with open(TRADE_LOG_PATH, "a", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(record.keys()))
                if not file_exists:
                    writer.writeheader()
                writer.writerow(record)
        except Exception as e:
            log.error(f"trade_log 기록 실패: {e}")
