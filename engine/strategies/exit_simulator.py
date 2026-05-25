"""
청산 시뮬레이터
- 3가지 청산 전략: fixed, trailing, hybrid
- 추가매수(피라미딩) 시뮬레이션
- 백테스트와 실전 둘 다에서 사용
"""
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from engine.core.logger import get_logger
from engine.strategies.rulebook import Rulebook

log = get_logger("exit_simulator")


@dataclass
class Trade:
    """단일 거래 결과 (추가매수 포함)"""
    entry_date: str
    entry_price: float
    entry_shares: int
    exit_date: str
    exit_price: float
    exit_reason: str           # 'take_profit' | 'stop_loss' | 'trailing' | 'time_out' | 'signal_exit'
    holding_days: int
    add_buys: list = field(default_factory=list)  # [(date, price, shares), ...]
    total_shares: int = 0
    avg_cost: float = 0.0
    pnl_pct: float = 0.0       # 수익률 (수수료 차감 후)
    pnl_krw: float = 0.0       # 손익 금액
    commission: float = 0.0

    def to_dict(self) -> dict:
        return {
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "entry_shares": self.entry_shares,
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
            "add_buys": [
                {"date": d, "price": round(p, 2), "shares": s}
                for d, p, s in self.add_buys
            ],
            "total_shares": self.total_shares,
            "avg_cost": round(self.avg_cost, 2),
            "pnl_pct": round(self.pnl_pct, 3),
            "pnl_krw": round(self.pnl_krw, 0),
            "commission": round(self.commission, 0),
        }


def simulate_exit(
    rb: Rulebook,
    df: pd.DataFrame,
    entry_idx: int,
    initial_shares: int,
    initial_budget_krw: float,
    commission_rate: float = 0.0005,
) -> Optional[Trade]:
    """
    entry_idx 시점에 진입했다고 가정하고 청산까지 시뮬레이션.

    Args:
        rb: 룰북
        df: OHLCV+지표 DataFrame
        entry_idx: 진입 시점 인덱스
        initial_shares: 초기 매수 주수
        initial_budget_krw: 한도 (추가매수도 이 안에서)
        commission_rate: 왕복 수수료 비율

    Returns:
        Trade 또는 None (데이터 부족 시)
    """
    if entry_idx + 1 >= len(df):
        return None

    is_short = (rb.direction == "short")
    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row["Close"])
    if entry_price <= 0 or pd.isna(entry_price):
        return None

    atr = float(entry_row.get("ATR", entry_price * 0.02))
    if pd.isna(atr) or atr <= 0:
        atr = entry_price * 0.02

    # 손절/익절 가격 (방향 따라 부호 반전)
    if not is_short:
        stop_loss = entry_price - atr * rb.stop_loss_atr
        take_profit = entry_price + atr * rb.take_profit_atr
    else:
        stop_loss = entry_price + atr * rb.stop_loss_atr        # 인버스: 위로 손절
        take_profit = entry_price - atr * rb.take_profit_atr     # 인버스: 아래로 익절

    # 트레일링용 최고점 (long) / 최저점 (short)
    extreme = entry_price

    # 누적 포지션
    total_shares = initial_shares
    used_krw = entry_price * initial_shares
    add_buys: list = []
    avg_cost = entry_price

    entry_date = str(df.index[entry_idx].date())

    for i in range(entry_idx + 1, min(entry_idx + rb.max_holding_days + 1, len(df))):
        row = df.iloc[i]
        high = float(row.get("High", row["Close"]))
        low = float(row.get("Low", row["Close"]))
        close = float(row["Close"])

        # ----- 손익 추적 (방향 따라 다름) -----
        if not is_short:
            current_pnl_pct = (close - avg_cost) / avg_cost * 100
            extreme = max(extreme, high)
            trailing_stop = extreme - atr * rb.trailing_atr
        else:
            current_pnl_pct = (avg_cost - close) / avg_cost * 100
            extreme = min(extreme, low)
            trailing_stop = extreme + atr * rb.trailing_atr

        # ----- 추가매수 체크 -----
        if (
            rb.add_buy_enabled
            and len(add_buys) < rb.add_buy_max_count
            and current_pnl_pct >= rb.add_buy_trigger_profit_pct
        ):
            add_budget = used_krw * rb.add_buy_size_ratio
            remaining = initial_budget_krw - used_krw
            if remaining > add_budget * 0.5:  # 최소 절반은 가능해야 추가
                add_budget = min(add_budget, remaining)
                add_price = close
                add_shares = int(add_budget / add_price)
                if add_shares > 0:
                    add_buys.append((str(row.name.date()), add_price, add_shares))
                    new_total = total_shares + add_shares
                    avg_cost = (avg_cost * total_shares + add_price * add_shares) / new_total
                    total_shares = new_total
                    used_krw += add_price * add_shares
                    # 추가매수 후 손절가 재계산 (avg_cost 기준)
                    if not is_short:
                        stop_loss = avg_cost - atr * rb.stop_loss_atr
                        take_profit = avg_cost + atr * rb.take_profit_atr
                    else:
                        stop_loss = avg_cost + atr * rb.stop_loss_atr
                        take_profit = avg_cost - atr * rb.take_profit_atr

        # ----- 청산 조건 체크 (우선순위: 손절 > 익절 > 트레일링) -----
        exit_price: Optional[float] = None
        exit_reason: Optional[str] = None

        if rb.exit_strategy in ("fixed", "hybrid"):
            if not is_short:
                if low <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif high >= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"
            else:
                if high >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                elif low <= take_profit:
                    exit_price = take_profit
                    exit_reason = "take_profit"

        if exit_price is None and rb.exit_strategy in ("trailing", "hybrid"):
            if not is_short:
                if low <= trailing_stop and i > entry_idx + 2:
                    exit_price = trailing_stop
                    exit_reason = "trailing"
            else:
                if high >= trailing_stop and i > entry_idx + 2:
                    exit_price = trailing_stop
                    exit_reason = "trailing"

        if exit_price is not None:
            return _build_trade(
                entry_date, entry_price, initial_shares,
                row.name, exit_price, exit_reason, i - entry_idx,
                add_buys, total_shares, avg_cost, is_short, commission_rate,
            )

    # 시간 초과 청산
    last_row = df.iloc[min(entry_idx + rb.max_holding_days, len(df) - 1)]
    return _build_trade(
        entry_date, entry_price, initial_shares,
        last_row.name, float(last_row["Close"]), "time_out",
        min(rb.max_holding_days, len(df) - 1 - entry_idx),
        add_buys, total_shares, avg_cost, is_short, commission_rate,
    )


def _build_trade(
    entry_date, entry_price, initial_shares,
    exit_idx, exit_price, exit_reason, holding_days,
    add_buys, total_shares, avg_cost, is_short, commission_rate,
) -> Trade:
    if is_short:
        gross_pnl_pct = (avg_cost - exit_price) / avg_cost * 100
    else:
        gross_pnl_pct = (exit_price - avg_cost) / avg_cost * 100

    # 수수료: 매수 + 매도 (왕복)
    commission = (avg_cost * total_shares + exit_price * total_shares) * (commission_rate / 2)
    net_pnl_krw = (exit_price - avg_cost) * total_shares * (-1 if is_short else 1) - commission
    net_pnl_pct = net_pnl_krw / (avg_cost * total_shares) * 100

    return Trade(
        entry_date=entry_date,
        entry_price=entry_price,
        entry_shares=initial_shares,
        exit_date=str(exit_idx.date()),
        exit_price=exit_price,
        exit_reason=exit_reason,
        holding_days=holding_days,
        add_buys=add_buys,
        total_shares=total_shares,
        avg_cost=avg_cost,
        pnl_pct=net_pnl_pct,
        pnl_krw=net_pnl_krw,
        commission=commission,
    )


if __name__ == "__main__":
    import numpy as np
    from engine.core.indicators import calc_indicators
    from engine.strategies.rulebook import default_rulebook

    np.random.seed(7)
    n = 100
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    # 상승 추세 가짜 데이터
    close = 25000 + np.cumsum(np.random.randn(n) * 50 + 20)
    df = pd.DataFrame(
        {
            "Open": close + np.random.randn(n) * 30,
            "High": close + np.abs(np.random.randn(n)) * 80,
            "Low": close - np.abs(np.random.randn(n)) * 80,
            "Close": close,
            "Volume": np.random.randint(10000, 50000, n),
        },
        index=idx,
    )
    df = calc_indicators(df)

    rb = default_rulebook("TEST", "korean_etf", "long")
    rb.exit_strategy = "hybrid"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 3.0
    rb.trailing_atr = 1.5
    rb.max_holding_days = 20
    rb.add_buy_enabled = True
    rb.add_buy_trigger_profit_pct = 1.5
    rb.add_buy_max_count = 2
    rb.add_buy_size_ratio = 0.5

    # 30번째 봉에서 4주 매수, 한도 120,000원 가정
    trade = simulate_exit(rb, df, entry_idx=30, initial_shares=4, initial_budget_krw=120000)
    print("=" * 60)
    print("청산 시뮬레이션 결과 (LONG, hybrid 전략, 추가매수 활성)")
    print("=" * 60)
    if trade:
        for k, v in trade.to_dict().items():
            print(f"  {k:14}: {v}")
    else:
        print("  거래 없음 (데이터 부족)")
