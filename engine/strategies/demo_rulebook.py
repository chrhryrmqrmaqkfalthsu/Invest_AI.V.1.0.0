"""
DemoRuleBook - Runner 통합 검증용 단순 룰북

추상 인터페이스 RuleBook을 정의하고,
SMA20 기반 단순 룰북을 구현한다.
나중에 학습된 GA 룰북으로 교체 시 RuleBook을 상속받기만 하면 된다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class SignalResult:
    """룰북 평가 결과"""
    ticker: str
    signal: Signal
    price: float
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "signal": self.signal.value,
            "price": self.price,
            "reason": self.reason,
        }


class RuleBook(ABC):
    """룰북 추상 베이스. 학습된 GA 룰북도 이걸 상속."""

    @abstractmethod
    def evaluate(self, ticker: str, price: float, df=None) -> SignalResult:
        """현재가 받아서 BUY/SELL/HOLD 판단"""
        ...

    @abstractmethod
    def name(self) -> str:
        """룰북 이름 (로그용)"""
        ...


class DemoRuleBook(RuleBook):
    """
    SMA20 기반 단순 룰북.
    - 현재가 > SMA20 → BUY
    - 현재가 < SMA20 * (1 - stop_loss_pct) → SELL
    - 그 외 → HOLD

    가격 히스토리를 ticker별로 deque(maxlen=window)로 유지.
    매 tick마다 price를 push하고, window 차면 SMA 계산.
    """

    def __init__(self, window: int = 20, stop_loss_pct: float = 0.03):
        self.window = window
        self.stop_loss_pct = stop_loss_pct
        self._history: Dict[str, deque] = {}

    def name(self) -> str:
        return f"DemoRuleBook(SMA{self.window}, stop={self.stop_loss_pct:.0%})"

    def _push(self, ticker: str, price: float) -> None:
        if ticker not in self._history:
            self._history[ticker] = deque(maxlen=self.window)
        self._history[ticker].append(price)

    def _sma(self, ticker: str) -> Optional[float]:
        h = self._history.get(ticker)
        if h is None or len(h) < self.window:
            return None
        return sum(h) / len(h)

    def evaluate(self, ticker: str, price: float, df=None) -> SignalResult:
        self._push(ticker, price)
        sma = self._sma(ticker)

        if sma is None:
            n = len(self._history[ticker])
            return SignalResult(
                ticker=ticker,
                signal=Signal.HOLD,
                price=price,
                reason=f"warmup {n}/{self.window}",
            )

        # 손절 우선
        if price < sma * (1 - self.stop_loss_pct):
            return SignalResult(
                ticker=ticker,
                signal=Signal.SELL,
                price=price,
                reason=f"price {price:.0f} < SMA*(1-{self.stop_loss_pct:.0%}) ({sma*(1-self.stop_loss_pct):.0f})",
            )

        if price > sma:
            return SignalResult(
                ticker=ticker,
                signal=Signal.BUY,
                price=price,
                reason=f"price {price:.0f} > SMA ({sma:.0f})",
            )

        return SignalResult(
            ticker=ticker,
            signal=Signal.HOLD,
            price=price,
            reason=f"price {price:.0f} ≈ SMA ({sma:.0f})",
        )


# ----------------- 단위 테스트 -----------------
if __name__ == "__main__":
    print("=" * 60)
    print("DemoRuleBook 단위 테스트")
    print("=" * 60)

    rb = DemoRuleBook(window=5, stop_loss_pct=0.03)
    print(f"룰북: {rb.name()}")

    # warmup phase: 4번은 HOLD (window=5)
    print("\n[warmup phase]")
    for p in [100, 102, 101, 103, 105]:
        r = rb.evaluate("TEST", p)
        print(f"  price={p} signal={r.signal.value:5s} reason={r.reason}")

    # window 채워진 뒤
    print("\n[main phase - 상승]")
    # SMA = (100+102+101+103+105)/5 = 102.2
    # price=110 > 102.2 → BUY
    r = rb.evaluate("TEST", 110)
    print(f"  price=110 signal={r.signal.value} reason={r.reason}")
    assert r.signal == Signal.BUY, "기대: BUY"

    print("\n[손절 phase - 급락]")
    # 히스토리에 110 들어감. window=5라 오래된 거 빠짐
    # 강제로 손절 트리거하려면 SMA 대비 3% 이상 하락
    for _ in range(10):
        rb.evaluate("TEST", 110)  # SMA를 110 근처로
    r = rb.evaluate("TEST", 100)  # 110*0.97 = 106.7, 100 < 106.7 → SELL
    print(f"  price=100 signal={r.signal.value} reason={r.reason}")
    assert r.signal == Signal.SELL, "기대: SELL"

    print("\n[ticker 분리 확인]")
    rb2 = DemoRuleBook(window=3)
    rb2.evaluate("A", 100)
    rb2.evaluate("A", 101)
    rb2.evaluate("B", 200)  # B는 아직 warmup
    r_a = rb2.evaluate("A", 102)
    r_b = rb2.evaluate("B", 201)
    print(f"  A: {r_a.signal.value} ({r_a.reason})")
    print(f"  B: {r_b.signal.value} ({r_b.reason})")
    assert r_a.signal != Signal.HOLD or "warmup" not in r_a.reason
    assert "warmup" in r_b.reason, "B는 warmup 중이어야 함"

    print("\n" + "=" * 60)
    print("✅ DemoRuleBook 검증 완료")
    print("=" * 60)
