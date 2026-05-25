"""
백테스트 실행 모듈
- 룰북 + OHLCV → 전체 기간 진입/청산 시뮬레이션
- 시점별 시장 컨텍스트 시계열 지원 (market_history_df)
- 결과 요약: 승률, 기대값, MDD, Profit Factor, Sharpe-like, fitness
"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import numpy as np
import pandas as pd

from engine.core.logger import get_logger
from engine.strategies.rulebook import Rulebook
from engine.strategies.evaluator import evaluate_signal, calc_position_size_krw
from engine.strategies.exit_simulator import simulate_exit
from engine.market.context import lookup_market_at

log = get_logger("backtest")


@dataclass
class BacktestResult:
    rulebook: Rulebook
    trades: list = field(default_factory=list)
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_like: float = 0.0
    fitness: float = 0.0

    def to_dict(self) -> dict:
        return {
            "rulebook": asdict(self.rulebook),
            "trades": self.trades,
            "trade_count": self.trade_count,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_rate": self.win_rate,
            "avg_return_pct": self.avg_return_pct,
            "avg_win_pct": self.avg_win_pct,
            "avg_loss_pct": self.avg_loss_pct,
            "expectancy_pct": self.expectancy_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "profit_factor": self.profit_factor,
            "sharpe_like": self.sharpe_like,
            "fitness": self.fitness,
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
    market_history_df: Optional[pd.DataFrame] = None,
    sector_name: str = "tech",
) -> BacktestResult:
    """
    전체 기간을 순회하며 신호 발생 시 진입 → 청산 시뮬레이션 → 다음 진입.

    Args:
        rb: 룰북
        df: OHLCV + 지표 DataFrame
        market_score/sector_score/vix_level: 시계열이 없을 때 사용할 고정값
        position_limit_krw: 종목당 한도
        commission_rate: 왕복 수수료
        cooldown_days: 청산 후 재진입 대기일수
        warmup: 지표 안정화를 위한 시작 인덱스
        market_history_df: 시점별 시장 시계열 DataFrame (있으면 우선 사용)
        sector_name: market_history_df에서 조회할 섹터명 (tech/finance/energy/...)
    """
    trades: list = []
    i = max(warmup, 0)
    n = len(df)

    while i < n:
        sub_df = df.iloc[: i + 1]

        # 시점별 시장 컨텍스트 조회 (시계열이 있으면 사용, 없으면 고정값)
        if market_history_df is not None:
            cur_date = df.index[i]
            mkt = lookup_market_at(market_history_df, cur_date)
            cur_market = float(mkt.get("score", market_score))
            cur_sector = float(mkt.get(f"sector_{sector_name}", sector_score))
            cur_vix = float(mkt.get("vix", vix_level))
        else:
            cur_market = market_score
            cur_sector = sector_score
            cur_vix = vix_level

        sig = evaluate_signal(
            rb, sub_df,
            market_score=cur_market,
            sector_score=cur_sector,
            vix_level=cur_vix,
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

        trade_obj = simulate_exit(
            rb, df, i, shares, position_limit_krw, commission_rate=commission_rate
        )
        if trade_obj is None:
            break
        # Trade 데이터클래스 → dict로 변환 (storage 호환)
        trade = asdict(trade_obj) if hasattr(trade_obj, "__dataclass_fields__") else trade_obj
        trades.append(trade)

        # 청산 시점 인덱스 찾기 (날짜로 매칭)
        exit_date = trade.get("exit_date")
        if exit_date is None:
            i += 1
            continue
        try:
            exit_idx = df.index.get_loc(pd.Timestamp(exit_date))
            if isinstance(exit_idx, slice):
                exit_idx = exit_idx.start
        except KeyError:
            exit_idx = i + 1
        i = max(exit_idx + 1 + cooldown_days, i + 1)

    return _summarize(rb, trades)


def _summarize(rb: Rulebook, trades: list) -> BacktestResult:
    if not trades:
        return BacktestResult(rulebook=rb, trades=[], fitness=-1.0)

    pnl_pcts = np.array([t.get("pnl_pct", 0.0) for t in trades], dtype=float)
    pnl_krw = np.array([t.get("pnl_krw", 0.0) for t in trades], dtype=float)

    win_mask = pnl_pcts > 0
    loss_mask = pnl_pcts <= 0
    win_count = int(win_mask.sum())
    loss_count = int(loss_mask.sum())
    trade_count = len(trades)

    win_rate = (win_count / trade_count) * 100.0 if trade_count else 0.0
    avg_return = float(pnl_pcts.mean())
    avg_win = float(pnl_pcts[win_mask].mean()) if win_count else 0.0
    avg_loss = float(pnl_pcts[loss_mask].mean()) if loss_count else 0.0
    expectancy = avg_return  # 평균 거래당 기대수익률 (%)

    # 최대 낙폭 (누적 수익률 기반)
    cum = np.cumsum(pnl_pcts)
    running_max = np.maximum.accumulate(cum)
    drawdown = cum - running_max
    mdd = float(drawdown.min()) if len(drawdown) else 0.0

    # Profit Factor
    gross_profit = float(pnl_krw[win_mask].sum()) if win_count else 0.0
    gross_loss = float(-pnl_krw[loss_mask].sum()) if loss_count else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    # Sharpe-like
    std = float(pnl_pcts.std()) if len(pnl_pcts) > 1 else 1.0
    sharpe = avg_return / std if std > 0 else 0.0

    fitness = _calc_fitness(
        expectancy=expectancy,
        win_rate=win_rate,
        profit_factor=pf,
        mdd=mdd,
        trade_count=trade_count,
    )

    res = BacktestResult(
        rulebook=rb,
        trades=trades,
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        win_rate=win_rate,
        avg_return_pct=avg_return,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        expectancy_pct=expectancy,
        max_drawdown_pct=mdd,
        profit_factor=pf,
        sharpe_like=sharpe,
        fitness=fitness,
    )

    # 룰북에도 백테스트 성과 기록
    rb.fitness = fitness
    rb.win_rate = win_rate
    rb.avg_return_pct = avg_return
    rb.expectancy_pct = expectancy
    rb.max_drawdown_pct = mdd
    rb.trade_count = trade_count

    return res


def _calc_fitness(
    expectancy: float,
    win_rate: float,
    profit_factor: float,
    mdd: float,
    trade_count: int,
) -> float:
    """
    종합 적합도. 거래 표본이 충분해야 신뢰할 수 있음.
    - 거래 5건 미만: fitness 강하게 깎음 (overfitting 방지)
    - 거래 5~20건: 표본 부족 페널티
    - 거래 20건 이상: 정상 평가
    """
    if trade_count == 0:
        return -50.0

    # 거래 수 신뢰도 계수
    if trade_count < 5:
        sample_factor = trade_count / 5.0 * 0.2   # 0.04 ~ 0.16
    elif trade_count < 10:
        sample_factor = 0.3 + (trade_count - 5) / 5 * 0.3  # 0.3 ~ 0.6
    elif trade_count < 20:
        sample_factor = 0.6 + (trade_count - 10) / 10 * 0.3  # 0.6 ~ 0.9
    elif trade_count < 100:
        sample_factor = 0.9 + (trade_count - 20) / 80 * 0.1  # 0.9 ~ 1.0
    else:
        sample_factor = max(1.0 - (trade_count - 100) / 500, 0.85)

    exp_score = max(min(expectancy / 3.0 * 40.0, 50.0), -30.0)
    wr_score = max(min((win_rate - 50.0) / 50.0 * 30.0, 30.0), -30.0)
    pf_score = max(min((profit_factor - 1.0) / 2.0 * 20.0, 30.0), -20.0)
    mdd_penalty = max(min(mdd, 0.0), -30.0)

    base = exp_score + wr_score + pf_score + mdd_penalty
    return base * sample_factor


    exp_score = max(min(expectancy / 3.0 * 40.0, 50.0), -30.0)
    wr_score = max(min((win_rate - 50.0) / 50.0 * 30.0, 30.0), -30.0)
    pf_score = max(min((profit_factor - 1.0) / 2.0 * 20.0, 30.0), -20.0)
    mdd_penalty = max(min(mdd, 0.0), -30.0)  # mdd는 음수
    trade_penalty = 0.0
    if trade_count < 5:
        trade_penalty = -20.0
    elif trade_count < 10:
        trade_penalty = -10.0

    return exp_score + wr_score + pf_score + mdd_penalty + trade_penalty


if __name__ == "__main__":
    from engine.core.data_loader import load_ohlcv
    from engine.core.indicators import calc_indicators
    from engine.strategies.rulebook import default_rulebook
    from engine.market.context import get_market_history

    print("=== Backtest 테스트 (시계열 시장 컨텍스트 사용) ===")
    df = load_ohlcv("379800", years=5)
    df = calc_indicators(df)
    print(f"OHLCV: {len(df)} rows")

    market_hist = get_market_history(years=6)
    print(f"market_history: {len(market_hist)} rows")

    rb = default_rulebook("379800", asset_type="korean_etf", direction="long")
    rb.signal_threshold = 2.0
    rb.exit_strategy = "hybrid"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 3.0
    rb.trailing_atr = 1.5
    rb.max_holding_days = 20
    rb.base_position_ratio = 0.7
    rb.add_buy_enabled = True
    rb.add_buy_trigger_profit_pct = 1.5
    rb.add_buy_max_count = 1
    rb.add_buy_size_ratio = 0.5
    rb.market_score_weight = 0.5

    result = run_backtest(
        rb, df,
        position_limit_krw=120000,
        market_history_df=market_hist,
        sector_name="tech",
    )
    print(f"\n결과:")
    print(f"  거래수: {result.trade_count} (승 {result.win_count} / 패 {result.loss_count})")
    print(f"  승률: {result.win_rate:.2f}%")
    print(f"  평균 수익률: {result.avg_return_pct:+.3f}%")
    print(f"  평균 이익: {result.avg_win_pct:+.3f}% / 평균 손실: {result.avg_loss_pct:+.3f}%")
    print(f"  기대값: {result.expectancy_pct:+.3f}%")
    print(f"  MDD: {result.max_drawdown_pct:.2f}%")
    print(f"  Profit Factor: {result.profit_factor:.3f}")
    print(f"  Sharpe-like: {result.sharpe_like:.3f}")
    print(f"  Fitness: {result.fitness:.3f}")
    if result.trades:
        print(f"\n샘플 거래:")
        t = result.trades[0]
        print(f"  진입 {t.get('entry_date')} @ {t.get('entry_price'):.0f} ({t.get('shares')}주)")
        print(f"  청산 {t.get('exit_date')} @ {t.get('exit_price'):.2f} ({t.get('exit_reason')})")
        print(f"  PnL: {t.get('pnl_pct'):+.3f}% ({t.get('pnl_krw'):+.0f} KRW)")
