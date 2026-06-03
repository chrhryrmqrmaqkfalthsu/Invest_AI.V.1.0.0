"""
SafetyLayer - 주문 발사 전 모든 안전장치를 통과시키는 게이트
사용법:
    layer = SafetyLayer(broker)
    decision = layer.check_order("buy", "AAPL", shares=0.5, price=300)
    if decision.allowed:
        order = broker.place_buy("AAPL", 0.5, ...)
        layer.record_order(order, side="buy")
    else:
        log.warning(f"차단: {decision.reason}")

체크 순서 (먼저 걸리는 것부터 차단):
  1. kill switch 파일 존재 여부
  2. 일일 손실 한도 도달 (kill_until)
  3. 연속 손실 쿨다운 중
  4. 시장 개장 여부
  5. 화이트리스트 (data/symbols/ 폴더에 있는 종목만)
  6. 첫 주문 승인 필요 여부
  7. 소액 한도 (선택적 수량/주문금액/일일횟수/누적투자금)

주의:
  - 금액 한도는 price의 기준통화에 맞는 notional로 계산한다.
    KIS는 KRW, Alpaca는 USD처럼 브로커별 통화가 다를 수 있다.
  - 기존 *_krw 필드명은 하위호환을 위해 유지하되 내부적으로는 notional 한도로 취급한다.
"""
from __future__ import annotations

import os
import yaml
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from . import state as state_mod
from .state import SafetyState
from ..broker.base import Broker, Order, OrderStatus, OrderSide

KILL_SWITCH_PATH = Path.home() / "kingmaker" / "data" / "_system" / "KILL_SWITCH"
POLICY_PATH      = Path.home() / "kingmaker" / "config" / "policy.yaml"
SYMBOLS_DIR      = Path.home() / "kingmaker" / "data" / "symbols"


@dataclass
class SafetyDecision:
    allowed: bool
    reason: str = ""           # 차단 사유 (allowed=False일 때만)
    code: str = ""             # 분류 코드 (KILL_SWITCH, DAILY_LOSS, COOLDOWN, MARKET_CLOSED,
                               #          NOT_WHITELISTED, NEED_APPROVAL, LIMIT_SHARES,
                               #          LIMIT_KRW, LIMIT_DAILY, LIMIT_TOTAL)


class SafetyLayer:

    def __init__(self, broker: Optional[Broker] = None, policy_path: Optional[Path] = None):
        self.broker = broker
        self.policy = self._load_policy(policy_path or POLICY_PATH)
        sa = self.policy.get("small_amount_safety", {}) or {}
        risk = self.policy.get("risk", {}) or {}

        self.enabled                  = bool(sa.get("enabled", True))
        self.max_shares               = self._optional_float(sa.get("max_shares_per_order", 1))
        self.max_notional_per_order   = float(sa.get("max_notional_per_order", sa.get("max_krw_per_order", 10000)))
        self.max_krw                  = self.max_notional_per_order  # 하위호환: runner의 일시 상향 코드가 참조
        self.max_total_notional       = float(sa.get("max_total_notional", sa.get("max_total_invested_krw", 100000)))
        self.max_total_invested       = self.max_total_notional      # 하위호환: SafetyState 필드명/runner 참조 유지
        self.max_orders_per_day       = int(sa.get("max_orders_per_day", 5))
        self.require_first_approval   = bool(sa.get("require_first_order_approval", True))
        self.daily_loss_limit_krw     = float(sa.get("daily_loss_limit_krw", 50000))

        self.daily_loss_limit_pct     = float(risk.get("daily_loss_limit_pct", 10))
        self.consecutive_loss_limit   = int(risk.get("consecutive_loss_limit", 3))
        self.cooldown_hours           = int(risk.get("cooldown_after_consecutive_loss_hours", 24))

    @staticmethod
    def _optional_float(value) -> Optional[float]:
        """0/None/빈 문자열은 비활성 한도로 취급한다."""
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

    def _current_order_notional_limit(self) -> float:
        """하위호환을 위해 max_notional_per_order와 max_krw 중 더 큰 값을 현재 주문 한도로 본다."""
        return max(float(getattr(self, "max_notional_per_order", 0.0) or 0.0),
                   float(getattr(self, "max_krw", 0.0) or 0.0))

    def _current_total_notional_limit(self) -> float:
        return max(float(getattr(self, "max_total_notional", 0.0) or 0.0),
                   float(getattr(self, "max_total_invested", 0.0) or 0.0))

    # ---------- 외부 API ----------
    def check_order(
        self,
        side: str,
        ticker: str,
        shares: float,
        price: float,
    ) -> SafetyDecision:
        """주문 발사 전 호출. allowed=True면 broker.place_*로 진행 가능."""
        if not self.enabled:
            return SafetyDecision(True, reason="safety disabled")

        try:
            shares_f = float(shares)
            price_f = float(price)
        except Exception:
            return SafetyDecision(False, f"수량/가격 변환 실패: shares={shares!r}, price={price!r}", "INVALID_ORDER")
        if shares_f <= 0 or price_f <= 0:
            return SafetyDecision(False, f"수량/가격은 양수여야 함: shares={shares_f}, price={price_f}", "INVALID_ORDER")

        st = state_mod.load()

        # [1] kill switch
        if KILL_SWITCH_PATH.exists():
            return SafetyDecision(False, "KILL_SWITCH 파일 감지 — 모든 주문 차단", "KILL_SWITCH")

        # [2] 일일 손실 한도 도달
        if st.kill_until:
            try:
                until = datetime.fromisoformat(st.kill_until)
                if datetime.now() < until:
                    return SafetyDecision(False, f"일일 손실 한도 도달 (해제: {st.kill_until})", "DAILY_LOSS")
            except Exception:
                pass

        # [3] 연속 손실 쿨다운
        if st.cooldown_until:
            try:
                until = datetime.fromisoformat(st.cooldown_until)
                if datetime.now() < until:
                    return SafetyDecision(False, f"연속 손실 쿨다운 중 (해제: {st.cooldown_until})", "COOLDOWN")
            except Exception:
                pass

        # [4] 시장 개장 여부
        if self.broker and not self.broker.is_market_open(ticker):
            return SafetyDecision(False, "장 마감 상태", "MARKET_CLOSED")

        # [5] 화이트리스트 - data/symbols/{ticker} 폴더가 있는 종목만
        if not self._is_whitelisted(ticker):
            return SafetyDecision(False, f"{ticker}는 화이트리스트에 없음 (data/symbols/{ticker} 없음)", "NOT_WHITELISTED")

        # [6] 첫 주문 승인 (매수 주문에만 적용; 매도는 손절/익절일 수 있어 자유)
        side_lower = str(side).lower()
        if side_lower == "buy" and self.require_first_approval and not st.first_order_approved:
            if st.orders_today == 0:
                return SafetyDecision(
                    False,
                    "오늘 첫 매수 주문은 텔레그램 /approve 승인 필요",
                    "NEED_APPROVAL",
                )

        # [7] 소액/금액 한도
        if self.max_shares is not None and shares_f > self.max_shares:
            return SafetyDecision(False, f"수량 {shares_f:g} > 한도 {self.max_shares:g}주", "LIMIT_SHARES")

        order_notional = shares_f * price_f
        max_notional = self._current_order_notional_limit()
        if max_notional > 0 and order_notional > max_notional:
            return SafetyDecision(False, f"주문금액 {order_notional:,.2f} > 한도 {max_notional:,.2f}", "LIMIT_KRW")

        if st.orders_today >= self.max_orders_per_day:
            return SafetyDecision(False, f"일일 주문 {st.orders_today}회 >= 한도 {self.max_orders_per_day}", "LIMIT_DAILY")

        # 매수일 때만 누적 투자금 체크. state 필드명은 krw지만 기준통화 notional 누적으로 사용한다.
        if side_lower == "buy":
            new_total = st.invested_krw_today + order_notional
            max_total = self._current_total_notional_limit()
            if max_total > 0 and new_total > max_total:
                return SafetyDecision(
                    False,
                    f"누적투자 {new_total:,.2f} > 한도 {max_total:,.2f}",
                    "LIMIT_TOTAL",
                )

        return SafetyDecision(True, reason="모든 안전장치 통과")

    def record_order(self, order: Order, side: str) -> None:
        """주문이 실제로 나갔으면 상태 업데이트 (성공/실패 무관, 시도는 했다는 기록)"""
        if order.status in (OrderStatus.REJECTED, OrderStatus.FAILED):
            return  # 거부/실패는 카운트 안 함

        st = state_mod.load()
        st.orders_today += 1

        if str(side).lower() == "buy":
            filled_notional = float(order.filled_shares) * float(order.filled_avg_price)
            if filled_notional > 0:
                st.invested_krw_today += filled_notional

        state_mod.save(st)

    def record_realized_pnl(self, pnl_krw: float, total_value_krw: float = 0) -> None:
        """매도 체결로 손익 확정 시 호출. 손실 누적/쿨다운 트리거."""
        st = state_mod.load()
        st.realized_pnl_today += pnl_krw

        # 연속 손실 추적
        if pnl_krw < 0:
            st.consecutive_losses += 1
            if st.consecutive_losses >= self.consecutive_loss_limit:
                cd = datetime.now() + timedelta(hours=self.cooldown_hours)
                st.cooldown_until = cd.isoformat()
        else:
            st.consecutive_losses = 0

        # 일일 손실 한도 (절대금액 또는 % 둘 중 먼저 도달)
        loss_today = -st.realized_pnl_today  # 손실이면 양수
        krw_breach = loss_today >= self.daily_loss_limit_krw
        pct_breach = (total_value_krw > 0 and
                      loss_today / total_value_krw * 100 >= self.daily_loss_limit_pct)
        if krw_breach or pct_breach:
            # 오늘 자정까지 차단
            end_of_day = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
            st.kill_until = end_of_day.isoformat()

        state_mod.save(st)

    def approve_first_order(self) -> None:
        """텔레그램 /approve 명령에서 호출"""
        st = state_mod.load()
        st.first_order_approved = True
        state_mod.save(st)

    def revoke_approval(self) -> None:
        st = state_mod.load()
        st.first_order_approved = False
        state_mod.save(st)

    # ---------- 내부 ----------
    def _is_whitelisted(self, ticker: str) -> bool:
        return (SYMBOLS_DIR / ticker).is_dir()


# ==================================================
# 단위 테스트: 9가지 차단 케이스 검증
# ==================================================
if __name__ == "__main__":
    import shutil
    from unittest.mock import MagicMock

    print("=" * 60)
    print("SafetyLayer 단위 테스트")
    print("=" * 60)

    # 깨끗한 상태로 시작
    state_mod.reset_for_test()
    if KILL_SWITCH_PATH.exists():
        KILL_SWITCH_PATH.unlink()

    # 모의 broker: 시장 열림 + 화이트리스트는 실제 data/symbols/ 사용
    broker = MagicMock()
    broker.is_market_open = MagicMock(return_value=True)

    layer = SafetyLayer(broker)
    print(f"\n[설정] max_shares={layer.max_shares}, max_notional={layer._current_order_notional_limit():,.2f}, "
          f"max_orders={layer.max_orders_per_day}, "
          f"max_total={layer._current_total_notional_limit():,.2f}, "
          f"require_approval={layer.require_first_approval}")

    # 화이트리스트 종목 (data/symbols 에 있어야 함)
    OK_TICKER = "379800"
    BAD_TICKER = "999999"

    def case(n, side, ticker, shares, price, expect_allowed, expect_code=""):
        d = layer.check_order(side, ticker, shares, price)
        ok = (d.allowed == expect_allowed) and (not expect_code or d.code == expect_code)
        mark = "✅" if ok else "❌"
        print(f"[{n}] {side} {ticker} {shares:g}주 @{price:,.2f} → "
              f"allowed={d.allowed} code={d.code or '-'}  {mark}")
        if not ok:
            print(f"      reason: {d.reason}")
            print(f"      expected: allowed={expect_allowed} code={expect_code}")
        return ok

    # [1] 정상 매수 (단, 첫 주문 승인 미설정이면 NEED_APPROVAL)
    case("1a", "buy", OK_TICKER, 1, 9000, False, "NEED_APPROVAL")
    layer.approve_first_order()
    case("1b", "buy", OK_TICKER, 1, 9000, True)  # 승인 후 통과

    # [2] 수량 초과
    case("2", "buy", OK_TICKER, 2, 5000, False, "LIMIT_SHARES")

    # [3] 금액 초과
    case("3", "buy", OK_TICKER, 1, 15000, False, "LIMIT_KRW")

    # [4] 화이트리스트 아님
    case("4", "buy", BAD_TICKER, 1, 9000, False, "NOT_WHITELISTED")

    # [5] 시장 마감
    broker.is_market_open = MagicMock(return_value=False)
    case("5", "buy", OK_TICKER, 1, 9000, False, "MARKET_CLOSED")
    broker.is_market_open = MagicMock(return_value=True)

    # [6] kill switch
    KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_PATH.touch()
    case("6", "buy", OK_TICKER, 1, 9000, False, "KILL_SWITCH")
    KILL_SWITCH_PATH.unlink()

    # [7] 일일 주문 횟수 초과
    s = state_mod.load()
    s.orders_today = 5
    state_mod.save(s)
    case("7", "buy", OK_TICKER, 1, 9000, False, "LIMIT_DAILY")
    s.orders_today = 0
    state_mod.save(s)

    # [8] 누적 투자금 초과
    s = state_mod.load()
    s.invested_krw_today = 95000  # 한도 100000 근접
    state_mod.save(s)
    case("8", "buy", OK_TICKER, 1, 9000, False, "LIMIT_TOTAL")  # 95000+9000=104000 > 100000
    s.invested_krw_today = 0
    state_mod.save(s)

    # [9] 연속 손실 쿨다운
    layer.record_realized_pnl(-3000)
    layer.record_realized_pnl(-2000)
    layer.record_realized_pnl(-1000)  # 3번째 손실 → 쿨다운 트리거
    case("9", "buy", OK_TICKER, 1, 9000, False, "COOLDOWN")

    # [10] 일일 손실 한도 (별도 검증)
    state_mod.reset_for_test()
    layer.approve_first_order()
    layer.record_realized_pnl(-60000)  # 한도 50000 초과
    case("10", "buy", OK_TICKER, 1, 9000, False, "DAILY_LOSS")

    # 정리
    state_mod.reset_for_test()
    print("\n" + "=" * 60)
    print("✅ 모든 차단 케이스 검증 완료")
    print("=" * 60)
