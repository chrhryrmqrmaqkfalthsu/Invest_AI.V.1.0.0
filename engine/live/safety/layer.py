"""
SafetyLayer - 주문 발사 전 모든 안전장치를 통과시키는 게이트.

BQ-2a invariants:
- 일반 BUY(entry)는 기존 PositionManager 포지션, broker 보유, ticker cooldown 중
  하나라도 있으면 fail-closed 한다.
- 승인형 추가매수(add_buy)는 기존 추적 포지션과 broker 보유가 모두 있어야 하며,
  add_buy.enabled 및 min_cooldown_minutes를 통과해야 한다.
- ticker별 BUY 시각은 실제 체결 주문만 SafetyState에 영속 기록한다.

BN-1 invariants:
- 주문 제출 카운트와 실제 체결 정산을 분리한다.
- 같은 order_id의 재조회/재시작 정산은 idempotent하게 한 번만 반영한다.

BS-1a invariants:
- KILL_SWITCH/손실잠금/쿨다운/시장시간/whitelist/첫주문승인은
  small_amount_safety.enabled 값과 무관하게 항상 강제한다.
- enabled=False는 주문당 수량·금액·일일 주문 수·당일 매수/전체 노출 소액 한도만 비활성화한다.
"""
from __future__ import annotations

import json
import yaml
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import state as state_mod
from ..broker.base import Broker, Order, OrderStatus

KILL_SWITCH_PATH = Path.home() / "kingmaker" / "data" / "_system" / "KILL_SWITCH"
POLICY_PATH = Path.home() / "kingmaker" / "config" / "policy.yaml"
SYMBOLS_DIR = Path.home() / "kingmaker" / "data" / "symbols"
POSITIONS_PATH = Path.home() / "kingmaker" / "data" / "_system" / "positions.json"
SHARE_EPS = 1e-6
DEFAULT_MIN_NOTIONAL_PER_ORDER = 1.0
DEFAULT_MIN_FRACTIONAL_SHARES_PER_ORDER = 0.001


@dataclass
class SafetyDecision:
    allowed: bool
    reason: str = ""
    code: str = ""


class SafetyLayer:
    def __init__(self, broker: Optional[Broker] = None, policy_path: Optional[Path] = None):
        self.broker = broker
        self.policy = self._load_policy(policy_path or POLICY_PATH)
        sa = self.policy.get("small_amount_safety", {}) or {}
        risk = self.policy.get("risk", {}) or {}
        entry = self.policy.get("entry", {}) or {}
        add_buy = self.policy.get("add_buy", {}) or {}

        self.enabled = bool(sa.get("enabled", True))
        self.min_notional_per_order = float(
            sa.get(
                "min_notional_per_order",
                sa.get("min_order_notional", sa.get("min_krw_per_order", DEFAULT_MIN_NOTIONAL_PER_ORDER)),
            )
            or 0.0
        )
        self.min_fractional_shares_per_order = float(
            sa.get(
                "min_fractional_shares_per_order",
                sa.get("min_fractional_shares", DEFAULT_MIN_FRACTIONAL_SHARES_PER_ORDER),
            )
            or 0.0
        )
        self.max_shares = self._optional_float(sa.get("max_shares_per_order", 1))
        self.max_notional_per_order = float(sa.get("max_notional_per_order", sa.get("max_krw_per_order", 10000)))
        self.max_krw = self.max_notional_per_order
        daily_bought = sa.get("max_bought_notional_per_day", sa.get("max_daily_bought_notional", 0))
        self.max_bought_notional_per_day = float(daily_bought or 0.0)
        exposure_limit = sa.get("max_total_exposure_notional", sa.get("max_total_notional", sa.get("max_total_invested_krw", 100000)))
        self.max_total_exposure_notional = float(exposure_limit or 0.0)
        self.max_total_notional = self.max_total_exposure_notional
        self.max_total_invested = self.max_total_exposure_notional
        self.pending_order_manager = None
        self.max_orders_per_day = int(sa.get("max_orders_per_day", 5))
        self.require_first_approval = bool(sa.get("require_first_order_approval", True))
        self.daily_loss_limit_usd = float(
            sa.get("daily_loss_limit_usd", sa.get("daily_loss_limit_notional", sa.get("daily_loss_limit_krw", 50000)))
        )

        self.daily_loss_limit_pct = float(risk.get("daily_loss_limit_pct", 10))
        self.consecutive_loss_limit = int(risk.get("consecutive_loss_limit", 3))
        self.cooldown_hours = int(risk.get("cooldown_after_consecutive_loss_hours", 24))

        self.one_position_per_symbol = bool(entry.get("one_position_per_symbol", True))
        self.entry_cooldown_hours = float(entry.get("cooldown_after_buy_hours", 24) or 0)
        self.add_buy_enabled = bool(add_buy.get("enabled", False))
        self.add_buy_cooldown_minutes = float(add_buy.get("min_cooldown_minutes", 30) or 0)
        if not self.one_position_per_symbol:
            raise ValueError(
                "one_position_per_symbol=False is unsupported: PositionManager stores one snapshot per ticker"
            )

    @staticmethod
    def _optional_float(value) -> Optional[float]:
        if value in (None, "", False):
            return None
        try:
            v = float(value)
        except Exception:
            return None
        return v if v > 0 else None

    @staticmethod
    def _load_policy(path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _now(now: Optional[datetime] = None) -> datetime:
        return now or datetime.now().astimezone()

    @staticmethod
    def _parse_time(value: str, reference: datetime) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except Exception:
            return None
        if parsed.tzinfo is None and reference.tzinfo is not None:
            parsed = parsed.replace(tzinfo=reference.tzinfo)
        elif parsed.tzinfo is not None and reference.tzinfo is None:
            reference = reference.replace(tzinfo=parsed.tzinfo)
        return parsed

    def _current_order_notional_limit(self) -> float:
        return max(
            float(getattr(self, "max_notional_per_order", 0.0) or 0.0),
            float(getattr(self, "max_krw", 0.0) or 0.0),
        )

    def _current_total_notional_limit(self) -> float:
        return max(
            float(getattr(self, "max_total_exposure_notional", 0.0) or 0.0),
            float(getattr(self, "max_total_notional", 0.0) or 0.0),
            float(getattr(self, "max_total_invested", 0.0) or 0.0),
        )

    def _pending_buy_reserved_notional(self, reference_price: float) -> float:
        pending = getattr(self, "pending_order_manager", None)
        if pending is None:
            return 0.0
        total = 0.0
        try:
            records = pending.all()
        except Exception:
            raise RuntimeError("pending 주문 상태 조회 실패")
        for record in records:
            if str(getattr(record, "side", "")).lower() != "buy" or str(getattr(record, "state", "")) == "DONE":
                continue
            md = getattr(record, "metadata", {}) or {}
            reserved = md.get("approved_krw") or md.get("reserved_notional")
            try:
                if reserved is not None:
                    total += max(0.0, float(reserved))
                    continue
            except Exception:
                pass
            px = float(getattr(record, "filled_avg_price", 0.0) or 0.0) or float(reference_price or 0.0)
            total += max(0.0, float(getattr(record, "requested_shares", 0.0) or 0.0) * px)
        return total

    def _current_exposure_notional(self, reference_price: float) -> tuple[float, Optional[str]]:
        if self.broker is None:
            return 0.0, "broker 없음"
        try:
            holdings = self.broker.get_holdings()
        except Exception as exc:
            return 0.0, str(exc)
        exposure = 0.0
        try:
            for holding in holdings:
                shares = float(getattr(holding, "shares", 0.0) or 0.0)
                avg_cost = float(getattr(holding, "avg_cost", 0.0) or 0.0)
                market_value = float(getattr(holding, "market_value", 0.0) or 0.0)
                current_price = float(getattr(holding, "current_price", 0.0) or 0.0)
                cost_value = shares * avg_cost
                fallback_market = shares * current_price
                exposure += max(cost_value, market_value, fallback_market, 0.0)
            exposure += self._pending_buy_reserved_notional(reference_price)
            return exposure, None
        except Exception as exc:
            return 0.0, str(exc)

    def _tracked_position_exists(self, ticker: str) -> tuple[bool, Optional[str]]:
        if not POSITIONS_PATH.exists():
            return False, None
        try:
            payload = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, str(exc)
        if not isinstance(payload, dict):
            return False, "positions root is not an object"
        return ticker in payload, None

    def _broker_holding_exists(self, ticker: str) -> tuple[bool, Optional[str]]:
        if self.broker is None:
            return False, None
        try:
            for holding in self.broker.get_holdings():
                if str(getattr(holding, "ticker", "")) == ticker and float(getattr(holding, "shares", 0.0) or 0.0) > SHARE_EPS:
                    return True, None
            return False, None
        except Exception as exc:
            return False, str(exc)

    def check_entry_cooldown(self, ticker: str, now: Optional[datetime] = None) -> SafetyDecision:
        if self.entry_cooldown_hours <= 0:
            return SafetyDecision(True, reason="entry cooldown disabled")
        st = state_mod.load()
        current = self._now(now)
        last = self._parse_time(st.last_buy_at_by_ticker.get(ticker, ""), current)
        if last is None:
            return SafetyDecision(True, reason="no prior filled buy")
        until = last + timedelta(hours=self.entry_cooldown_hours)
        if current < until:
            return SafetyDecision(False, f"{ticker} 일반 BUY 쿨다운 중 (해제: {until.isoformat()})", "ENTRY_COOLDOWN")
        return SafetyDecision(True, reason="entry cooldown elapsed")

    def check_add_buy_cooldown(self, ticker: str, now: Optional[datetime] = None) -> SafetyDecision:
        if not self.add_buy_enabled:
            return SafetyDecision(False, "추가매수 정책 비활성", "ADD_BUY_DISABLED")
        if self.add_buy_cooldown_minutes <= 0:
            return SafetyDecision(True, reason="add-buy cooldown disabled")
        st = state_mod.load()
        current = self._now(now)
        candidates = [
            self._parse_time(st.last_buy_at_by_ticker.get(ticker, ""), current),
            self._parse_time(st.last_add_buy_at_by_ticker.get(ticker, ""), current),
        ]
        candidates = [value for value in candidates if value is not None]
        if not candidates:
            return SafetyDecision(True, reason="no prior filled buy")
        last = max(candidates)
        until = last + timedelta(minutes=self.add_buy_cooldown_minutes)
        if current < until:
            return SafetyDecision(False, f"{ticker} 추가매수 쿨다운 중 (해제: {until.isoformat()})", "ADD_BUY_COOLDOWN")
        return SafetyDecision(True, reason="add-buy cooldown elapsed")

    def check_entry_guard(self, ticker: str, now: Optional[datetime] = None) -> SafetyDecision:
        tracked, tracked_error = self._tracked_position_exists(ticker)
        if tracked_error:
            return SafetyDecision(False, f"positions 상태 확인 실패: {tracked_error}", "POSITION_STATE_ERROR")
        if tracked:
            return SafetyDecision(False, f"{ticker} PositionManager 포지션 존재 — 일반 BUY 차단", "EXISTING_POSITION")
        held, holding_error = self._broker_holding_exists(ticker)
        if holding_error:
            return SafetyDecision(False, f"broker 보유 확인 실패: {holding_error}", "HOLDINGS_CHECK_FAILED")
        if held:
            return SafetyDecision(False, f"{ticker} broker 보유 존재 — 일반 BUY 차단", "BROKER_HOLDING_EXISTS")
        return self.check_entry_cooldown(ticker, now=now)

    def check_add_buy_guard(self, ticker: str, now: Optional[datetime] = None) -> SafetyDecision:
        if not self.add_buy_enabled:
            return SafetyDecision(False, "추가매수 정책 비활성", "ADD_BUY_DISABLED")
        tracked, tracked_error = self._tracked_position_exists(ticker)
        if tracked_error:
            return SafetyDecision(False, f"positions 상태 확인 실패: {tracked_error}", "POSITION_STATE_ERROR")
        if not tracked:
            return SafetyDecision(False, f"{ticker} 추적 포지션 없음 — stale 추가매수 차단", "ADD_BUY_POSITION_MISSING")
        held, holding_error = self._broker_holding_exists(ticker)
        if holding_error:
            return SafetyDecision(False, f"broker 보유 확인 실패: {holding_error}", "HOLDINGS_CHECK_FAILED")
        if not held:
            return SafetyDecision(False, f"{ticker} broker 보유 없음 — stale 추가매수 차단", "ADD_BUY_HOLDING_MISSING")
        return self.check_add_buy_cooldown(ticker, now=now)

    def check_order(
        self,
        side: str,
        ticker: str,
        shares: float,
        price: float,
        purpose: str = "entry",
    ) -> SafetyDecision:
        try:
            shares_f = float(shares)
            price_f = float(price)
        except Exception:
            return SafetyDecision(False, f"수량/가격 변환 실패: shares={shares!r}, price={price!r}", "INVALID_ORDER")
        if shares_f <= 0 or price_f <= 0:
            return SafetyDecision(False, f"수량/가격은 양수여야 함: shares={shares_f}, price={price_f}", "INVALID_ORDER")

        side_lower = str(side).lower()
        purpose_lower = str(purpose or "entry").lower()
        if side_lower == "buy":
            if purpose_lower == "entry":
                guard = self.check_entry_guard(ticker)
            elif purpose_lower == "add_buy":
                guard = self.check_add_buy_guard(ticker)
            else:
                return SafetyDecision(False, f"알 수 없는 BUY purpose: {purpose}", "INVALID_BUY_PURPOSE")
            if not guard.allowed:
                return guard

        st = state_mod.load()
        if KILL_SWITCH_PATH.exists():
            return SafetyDecision(False, "KILL_SWITCH 파일 감지 — 모든 주문 차단", "KILL_SWITCH")

        if st.kill_until:
            try:
                until = datetime.fromisoformat(st.kill_until)
                if datetime.now() < until:
                    return SafetyDecision(False, f"일일 손실 한도 도달 (해제: {st.kill_until})", "DAILY_LOSS")
            except Exception:
                pass

        if st.cooldown_until:
            try:
                until = datetime.fromisoformat(st.cooldown_until)
                if datetime.now() < until:
                    return SafetyDecision(False, f"연속 손실 쿨다운 중 (해제: {st.cooldown_until})", "COOLDOWN")
            except Exception:
                pass

        if self.broker and not self.broker.is_market_open(ticker):
            return SafetyDecision(False, "장 마감 상태", "MARKET_CLOSED")

        if not self._is_whitelisted(ticker):
            return SafetyDecision(False, f"{ticker}는 화이트리스트에 없음 (data/symbols/{ticker} 없음)", "NOT_WHITELISTED")

        if side_lower == "buy" and self.require_first_approval and not st.first_order_approved:
            if st.orders_today == 0:
                return SafetyDecision(False, "오늘 첫 매수 주문은 텔레그램 /approve 승인 필요", "NEED_APPROVAL")

        if not self.enabled:
            return SafetyDecision(True, reason="운영 필수 게이트 통과; 소액 한도 비활성")

        order_notional = shares_f * price_f
        if side_lower == "buy":
            min_notional = float(getattr(self, "min_notional_per_order", 0.0) or 0.0)
            if min_notional > 0 and order_notional + 1e-9 < min_notional:
                return SafetyDecision(
                    False,
                    f"주문금액 {order_notional:,.4f} < 최소 {min_notional:,.2f}",
                    "MIN_NOTIONAL",
                )
            min_fractional = float(getattr(self, "min_fractional_shares_per_order", 0.0) or 0.0)
            if min_fractional > 0 and 0 < shares_f < min_fractional:
                return SafetyDecision(
                    False,
                    f"fractional 수량 {shares_f:.6f}주 < 최소 {min_fractional:.6f}주",
                    "MIN_FRACTIONAL_SHARES",
                )

        if self.max_shares is not None and shares_f > self.max_shares:
            return SafetyDecision(False, f"수량 {shares_f:g} > 한도 {self.max_shares:g}주", "LIMIT_SHARES")

        max_notional = self._current_order_notional_limit()
        if max_notional > 0 and order_notional > max_notional:
            return SafetyDecision(False, f"주문금액 {order_notional:,.2f} > 한도 {max_notional:,.2f}", "LIMIT_NOTIONAL")

        if st.orders_today >= self.max_orders_per_day:
            return SafetyDecision(False, f"일일 주문 {st.orders_today}회 >= 한도 {self.max_orders_per_day}", "LIMIT_DAILY")

        if side_lower == "buy":
            daily_limit = float(getattr(self, "max_bought_notional_per_day", 0.0) or 0.0)
            daily_total = st.invested_krw_today + order_notional
            if daily_limit > 0 and daily_total > daily_limit:
                return SafetyDecision(False, f"당일 매수금액 {daily_total:,.2f} > 한도 {daily_limit:,.2f}", "LIMIT_DAILY_BUY_NOTIONAL")

            max_total = self._current_total_notional_limit()
            if max_total > 0:
                exposure, exposure_error = self._current_exposure_notional(price_f)
                if exposure_error:
                    return SafetyDecision(False, f"전체 노출 조회 실패: {exposure_error}", "EXPOSURE_CHECK_FAILED")
                projected = exposure + order_notional
                if projected > max_total:
                    return SafetyDecision(False, f"전체 노출 {projected:,.2f} > 한도 {max_total:,.2f}", "LIMIT_TOTAL_EXPOSURE")

        return SafetyDecision(True, reason="모든 안전장치 통과")

    def record_submission(self, order: Order, side: str, purpose: str = "entry") -> None:
        if order.status in (OrderStatus.REJECTED, OrderStatus.FAILED):
            return
        st = state_mod.load()
        order_id = str(order.order_id or "").strip()
        if order_id and order_id in st.submitted_order_ids:
            return
        timestamp = str(order.submitted_at or "").strip() or datetime.now().astimezone().isoformat()
        st.orders_today += 1
        if order_id:
            st.submitted_order_ids[order_id] = timestamp
        state_mod.save(st)

    def record_fill(self, order: Order, side: str, purpose: str = "entry") -> None:
        filled_shares = float(order.filled_shares or 0.0)
        filled_avg_price = float(order.filled_avg_price or 0.0)
        if filled_shares <= SHARE_EPS or filled_avg_price <= 0:
            return

        st = state_mod.load()
        order_id = str(order.order_id or "").strip()
        if order_id and order_id in st.settled_order_ids:
            return

        side_lower = str(side).lower()
        purpose_lower = str(purpose or "entry").lower()
        timestamp = str(order.filled_at or "").strip() or datetime.now().astimezone().isoformat()
        if side_lower == "buy":
            st.invested_krw_today += filled_shares * filled_avg_price
            st.last_buy_at_by_ticker[str(order.ticker)] = timestamp
            if purpose_lower == "add_buy":
                st.last_add_buy_at_by_ticker[str(order.ticker)] = timestamp
        if order_id:
            st.settled_order_ids[order_id] = timestamp
        state_mod.save(st)

    def record_order(self, order: Order, side: str, purpose: str = "entry") -> None:
        self.record_submission(order, side, purpose=purpose)
        if float(order.filled_shares or 0.0) > SHARE_EPS:
            self.record_fill(order, side, purpose=purpose)

    def record_realized_pnl(self, pnl_usd: float, total_value_usd: float = 0, **legacy_kwargs) -> None:
        """Record realized PnL in USD notional.

        legacy keyword total_value_krw is accepted only for compatibility with
        older callers; it is interpreted as USD notional in the US-only live mode.
        """
        if not total_value_usd and "total_value_krw" in legacy_kwargs:
            total_value_usd = float(legacy_kwargs.get("total_value_krw") or 0.0)

        st = state_mod.load()
        st.realized_pnl_today += pnl_usd
        if pnl_usd < 0:
            st.consecutive_losses += 1
            if st.consecutive_losses >= self.consecutive_loss_limit:
                st.cooldown_until = (datetime.now() + timedelta(hours=self.cooldown_hours)).isoformat()
        else:
            st.consecutive_losses = 0

        loss_today = -st.realized_pnl_today
        usd_breach = loss_today >= self.daily_loss_limit_usd
        pct_breach = total_value_usd > 0 and loss_today / total_value_usd * 100 >= self.daily_loss_limit_pct
        if usd_breach or pct_breach:
            st.kill_until = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0).isoformat()
        state_mod.save(st)

    def approve_first_order(self) -> None:
        st = state_mod.load()
        st.first_order_approved = True
        state_mod.save(st)

    def revoke_approval(self) -> None:
        st = state_mod.load()
        st.first_order_approved = False
        state_mod.save(st)

    def _is_whitelisted(self, ticker: str) -> bool:
        return (SYMBOLS_DIR / ticker).is_dir()
