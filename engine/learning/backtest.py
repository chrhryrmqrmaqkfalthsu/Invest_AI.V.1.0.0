"""
백테스트 엔진
- 룰북 + OHLCV → 거래 시뮬레이션
- 성과 지표 계산 (승률, 평균수익, 기대값, MDD, 적합도)
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from engine.core.logger import get_logger
from engine.strategies.evaluator import evaluate_signal, calc_position_size_krw
from engine.strategies.exit_simulator import Trade, simulate_exit
from engine.strategies.rulebook import Rulebook

log = get_logger("backtest")


@dataclass
class BacktestResult:
    rulebook: Rulebook
    trades: list                    # Trade 리스트
    trade_count: int
    win_count: int
    loss_count: int
    win_rate: float                 # 0~100
    avg_return_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy_pct: float           # 기댓값
    max_drawdown_pct: float
    profit_factor: float            # 총수익 / 총손실 절대값
    sharpe_like: float              # 단순 샤프 비율 유사값
    fitness: float                  # GA 적합도 (종합점수)

    def to_dict(self) -> dict:
        return {
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": round(self.win_rate, 2),
            "avg_return_pct": round(self.avg_return_pct, 3),
            "avg_win_pct": round(self.avg_win_pct, 3),
            "avg_loss_pct": round(self.avg_loss_pct, 3),
            "expectancy_pct": round(self.expectancy_pct, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "profit_factor": round(self.profit_factor, 3),
            "sharpe_like": round(self.sharpe_like, 3),
            "fitness": round(self.fitness, 4),
            "trades": [t.to_dict() for t in self.trades],
        }


def run_backtest(
    rb: Rulebook,
    df: pd.DataFrame,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    position_limit_krw: float = 120000.0,
    commission_rate: float = 0.0005,
    cooldown_days: int = 1,
    warmup: int = 200,
) -> BacktestResult:
    """
    전체 기간을 순회하며 신호 발생 시 진입 → 청산 시뮬레이션 → 다음 진입.

    Args:
        rb: 룰북
        df: OHLCV + 지표 DataFrame
        market_score/sector_score/vix_level: 시장 컨텍스트 (백테스트 기간 평균값 사용)
        position_limit_krw: 종목당 한도
        commission_rate: 왕복 수수료
        cooldown_days: 청산 후 재진입 대기일수
        warmup: 지표 안정화를 위한 시작 인덱스
    """
    trades: list = []
    i = max(warmup, 0)
    n = len(df)

    while i < n:
        sub_df = df.iloc[: i + 1]
        sig = evaluate_signal(
            rb, sub_df,
            market_score=market_score,
            sector_score=sector_score,
            vix_level=vix_level,
            news_sentiment=0.0,
        )
        if not sig.should_buy:
            i += 1
            continue

        # 포지션 사이징
        amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
        entry_price = float(df.iloc[i]["Close"])
        shares = int(amt_krw / entry_price) if entry_price > 0 else 0
        if shares <= 0:
            i += 1
            continue

        trade = simulate_exit(
            rb, df, i, shares, position_limit_krw, commission_rate=commission_rate
        )
        if trade is None:
            break
        trades.append(trade)

        # 청산 시점 인덱스 찾기 (날짜로 매칭)
        try:
            exit_idx = df.index.get_loc(pd.Timestamp(trade.exit_date))
            if isinstance(exit_idx, slice):
                exit_idx = exit_idx.start
        except KeyError:
            exit_idx = i + trade.holding_days
        i = max(i + 1, int(exit_idx) + cooldown_days)

    return _summarize(rb, trades)


def _summarize(rb: Rulebook, trades: list) -> BacktestResult:
    if not trades:
        return BacktestResult(
            rulebook=rb, trades=[], trade_count=0,
            win_count=0, loss_count=0, win_rate=0.0,
            avg_return_pct=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
            expectancy_pct=0.0, max_drawdown_pct=0.0,
            profit_factor=0.0, sharpe_like=0.0, fitness=-1.0,
        )

    pnls = np.array([t.pnl_pct for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    win_rate = (len(wins) / len(pnls)) * 100
    avg_return = pnls.mean()
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = losses.mean() if len(losses) > 0 else 0.0

    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    profit_factor = (
        wins.sum() / abs(losses.sum()) if len(losses) > 0 and losses.sum() != 0 else
        (wins.sum() if len(wins) > 0 else 0.0)
    )

    # MDD: 누적 수익률 곡선의 최대 낙폭
    cum = np.cumprod(1 + pnls / 100)
    peak = np.maximum.accumulate(cum)
    drawdowns = (cum - peak) / peak * 100
    mdd = float(drawdowns.min())

    # 샤프 유사값: 평균/표준편차 (연환산 안 함, 거래 단위)
    sharpe = avg_return / (pnls.std() + 1e-9)

    fitness = _calc_fitness(
        win_rate=win_rate,
        avg_return=avg_return,
        expectancy=expectancy,
        mdd=mdd,
        trade_count=len(trades),
        profit_factor=profit_factor,
    )

    # 룰북에 결과 기록
    rb.fitness = fitness
    rb.win_rate = win_rate
    rb.avg_return_pct = avg_return
    rb.expectancy_pct = expectancy
    rb.max_drawdown_pct = mdd
    rb.trade_count = len(trades)

    return BacktestResult(
        rulebook=rb,
        trades=trades,
        trade_count=len(trades),
        win_count=int(len(wins)),
        loss_count=int(len(losses)),
        win_rate=float(win_rate),
        avg_return_pct=float(avg_return),
        avg_win_pct=float(avg_win),
        avg_loss_pct=float(avg_loss),
        expectancy_pct=float(expectancy),
        max_drawdown_pct=float(mdd),
        profit_factor=float(profit_factor),
        sharpe_like=float(sharpe),
        fitness=float(fitness),
    )


def _calc_fitness(
    win_rate: float,
    avg_return: float,
    expectancy: float,
    mdd: float,
    trade_count: int,
    profit_factor: float,
) -> float:
    """
    GA 적합도 — 단일 지표 최적화 함정을 피하려고 여러 지표 가중 합산.

    구성:
    - 기대값 (가장 중요)
    - 승률 (안정성)
    - profit_factor
    - 거래수 패널티/보너스 (너무 적거나 많으면 감점)
    - MDD 패널티 (drawdown 클수록 감점)
    """
    if trade_count == 0:
        return -1.0

    # 정규화
    expect_score = np.tanh(expectancy / 2.0) * 50      # ±50
    winrate_score = (win_rate - 50) / 50 * 20          # ±20
    pf_score = np.tanh((profit_factor - 1.0) / 2.0) * 15  # ±15
    mdd_penalty = max(min(mdd, 0), -50) / 50 * 20      # 0 ~ -20 (mdd음수)
    
    # 거래수 보정 (이상범위 20~80)
    if trade_count < 5:
        count_penalty = -20
    elif trade_count < 10:
        count_penalty = -5
    elif trade_count > 100:
        count_penalty = -5
    else:
        count_penalty = 0

    total = expect_score + winrate_score + pf_score + mdd_penalty + count_penalty
    return float(total)


if __name__ == "__main__":
    import numpy as np
    from engine.core.data_loader import load_ohlcv
    from engine.core.indicators import calc_indicators
    from engine.strategies.rulebook import default_rulebook

    print("=" * 60)
    print("백테스트 테스트 (379800, 5년)")
    print("=" * 60)
    print("시세 로딩 중...")
    df = load_ohlcv("379800", years=5)
    df = calc_indicators(df)
    print(f"  {len(df)} 봉 로드 완료")

    rb = default_rulebook("379800", "korean_etf", "long")
    rb.signal_threshold = 2.0
    rb.exit_strategy = "hybrid"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 3.0
    rb.trailing_atr = 1.5
    rb.max_holding_days = 20
    rb.add_buy_enabled = True
    rb.add_buy_trigger_profit_pct = 1.5
    rb.add_buy_max_count = 1
    rb.add_buy_size_ratio = 0.5
    rb.base_position_ratio = 0.7
    rb.market_score_weight = 0.5

    print(f"\n백테스트 실행...")
    result = run_backtest(
        rb, df,
        market_score=70, sector_score=80, vix_level=18,
        position_limit_krw=120000,
    )

    print(f"\n결과:")
    d = result.to_dict()
    for k in ["trade_count", "win_count", "loss_count", "win_rate",
              "avg_return_pct", "avg_win_pct", "avg_loss_pct",
              "expectancy_pct", "max_drawdown_pct", "profit_factor",
              "sharpe_like", "fitness"]:
        print(f"  {k:20}: {d[k]}")

    if result.trades:
        print(f"\n첫 거래 샘플:")
        for k, v in result.trades[0].to_dict().items():
            print(f"  {k:14}: {v}")
