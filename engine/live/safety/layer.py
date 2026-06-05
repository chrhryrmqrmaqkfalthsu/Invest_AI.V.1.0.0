"""
SafetyLayer - 주문 발사 전 모든 안전장치를 통과시키는 게이트.

BQ-2a invariants:
- 일반 BUY(entry)는 기존 PositionManager 포지션, broker 보유, ticker cooldown 중
  하나라도 있으면 fail-closed 한다.
- 승인형 추가매수(add_buy)는 기존 추적 포지션과 broker 보유가 모두 있어야 하며,
  add_buy.enabled 및 min_cooldown_minutes를 통과해야 한다.
- ticker별 BUY 시각은 FILLED 주문만 SafetyState에 영속 기록한다.
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
        self.max_shares = self._optional_float(sa.get("max_shares_per_order", 1))
        self.max_notional_per_order = float(sa.get("max_notional_per_order", sa.get("max_krw_per_order", 10000)))
        self.max_krw = self.max_notional_per_order
        self.max_total_notional = float(sa.get("max_total_notional", sa.get("max_total_invested_krw", 100000)))
        self.max_total_invested = self.max_total_notional
        self.max_orders_per_day = int(sa.get("max_orders_per_day", 5))
        self.require_first_approval = bool(sa.get("require_first_order_approval", True))
        self.daily_loss_limit_krw = float(sa.get("daily_loss_limit_krw", 50000))

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
            float(getattr(self, "max_total_notional", 0.0) or 0.0),
            float(getattr(self, "max_total_invested", 0.0) or 0.0),
        )

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
        """주문 발사 전 호출. BUY purpose는 entry 또는 add_buy여야 한다."""
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

        if not self.enabled:
            return SafetyDecision(True, reason="small-amount safety disabled; entry guards passed")

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

        if self.max_shares is not None and shares_f > self.max_shares:
            return SafetyDecision(False, f"수량 {shares_f:g} > 한도 {self.max_shares:g}주", "LIMIT_SHARES")

        order_notional = shares_f * price_f
        max_notional = self._current_order_notional_limit()
        if max_notional > 0 and order_notional > max_notional:
            return SafetyDecision(False, f"주문금액 {order_notional:,.2f} > 한도 {max_notional:,.2f}", "LIMIT_KRW")

        if st.orders_today >= self.max_orders_per_day:
            return SafetyDecision(False, f"일일 주문 {st.orders_today}회 >= 한도 {self.max_orders_per_day}", "LIMIT_DAILY")

        if side_lower == "buy":
            new_total = st.invested_krw_today + order_notional
            max_total = self._current_total_notional_limit()
            if max_total > 0 and new_total > max_total:
                return SafetyDecision(False, f"누적투자 {new_total:,.2f} > 한도 {max_total:,.2f}", "LIMIT_TOTAL")

        return SafetyDecision(True, reason="모든 안전장치 통과")

    def record_order(self, order: Order, side: str, purpose: str = "entry") -> None:
        """주문 상태 기록. ticker cooldown 시각은 FILLED BUY만 기록한다."""
        if order.status in (OrderStatus.REJECTED, OrderStatus.FAILED):
            return

        st = state_mod.load()
        st.orders_today += 1
        side_lower = str(side).lower()
        purpose_lower = str(purpose or "entry").lower()

        if side_lower == "buy":
            filled_notional = float(order.filled_shares) * float(order.filled_avg_price)
            if filled_notional > 0:
                st.invested_krw_today += filled_notional
            if order.status == OrderStatus.FILLED:
                timestamp = str(order.filled_at or "").strip() or datetime.now().astimezone().isoformat()
                st.last_buy_at_by_ticker[str(order.ticker)] = timestamp
                if purpose_lower == "add_buy":
                    st.last_add_buy_at_by_ticker[str(order.ticker)] = timestamp

        state_mod.save(st)

    def record_realized_pnl(self, pnl_krw: float, total_value_krw: float = 0) -> None:
        st = state_mod.load()
        st.realized_pnl_today += pnl_krw
        if pnl_krw < 0:
            st.consecutive_losses += 1
            if st.consecutive_losses >= self.consecutive_loss_limit:
                st.cooldown_until = (datetime.now() + timedelta(hours=self.cooldown_hours)).isoformat()
        else:
            st.consecutive_losses = 0

        loss_today = -st.realized_pnl_today
        krw_breach = loss_today >= self.daily_loss_limit_krw
        pct_breach = total_value_krw > 0 and loss_today / total_value_krw * 100 >= self.daily_loss_limit_pct
        if krw_breach or pct_breach:
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
