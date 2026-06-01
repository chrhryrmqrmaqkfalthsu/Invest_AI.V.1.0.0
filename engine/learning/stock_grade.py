"""개별주 스윙 단타 운용 등급 산정 유틸.

목표는 buy&hold 초과수익이 아니라, 비용 반영된 거래당 기대수익과
거래 표본이 비중복 연도별 구간에서 재현되는지 확인하는 것이다.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import pandas as pd

from engine.learning.backtest import run_backtest
from engine.learning.ensemble_backtest import run_ensemble_backtest
from engine.strategies.rulebook import Rulebook


DEFAULT_MIN_VALID_RULES = 3
DEFAULT_MIN_TEST_TRADES = 10
DEFAULT_PASS_EXP_PCT = 1.0
DEFAULT_WEAK_EXP_PCT = 0.5


def _date_series(df) -> pd.Series:
    if "date" in df.columns:
        return pd.Series(pd.to_datetime(df["date"]), index=df.index)
    return pd.Series(pd.to_datetime(df.index), index=df.index)


def _recent_complete_years(dates: pd.Series, n: int = 3) -> list[int]:
    data_min = pd.Timestamp(dates.min())
    data_max = pd.Timestamp(dates.max())
    last_full_year = data_max.year if (data_max.month == 12 and data_max.day >= 31) else data_max.year - 1
    years = [y for y in range(last_full_year - n + 1, last_full_year + 1)]
    return [y for y in years if pd.Timestamp(f"{y}-12-31") >= data_min]


def _period_status(trades: int, exp_pct: float) -> str:
    if trades >= DEFAULT_MIN_TEST_TRADES and exp_pct >= DEFAULT_PASS_EXP_PCT:
        return "PASS"
    if (trades >= DEFAULT_MIN_TEST_TRADES and exp_pct >= DEFAULT_WEAK_EXP_PCT) or (
        trades >= 5 and exp_pct >= DEFAULT_PASS_EXP_PCT
    ):
        return "WEAK"
    if trades == 0:
        return "NO_TRADE"
    return "FAIL"


def _grade_from_counts(pass_count: int, weak_count: int, positive_count: int) -> tuple[str, str]:
    """스윙 운용 등급과 권장 운용 모드."""
    if pass_count >= 2:
        return "A", "main"
    if pass_count >= 1 and (pass_count + weak_count) >= 2:
        return "B", "small"
    if positive_count >= 1 or weak_count >= 1:
        return "C", "watch"
    return "D", "no_trade"


def evaluate_swing_stock_grade(
    *,
    ticker: str,
    population: Iterable[Rulebook],
    df,
    position_limit_krw: float,
    market_history_df,
    sector_name: str,
    ticker_sentiment: Optional[dict] = None,
    fitness_mode: str = "spread",
    max_candidates: int = 20,
    top_n: int = 5,
    min_valid_rules: int = DEFAULT_MIN_VALID_RULES,
) -> dict:
    """GA 개체군을 이용해 비중복 연도별 스윙 단타 등급을 산정한다.

    각 연도별로 그 이전 데이터만 TRAIN으로 사용해 score_topN을 고르고,
    해당 연도 TEST에서 거래수/expectancy 재현성을 본다.
    score가 0인 개체는 후보에서 제외해 fitness_topN으로 퇴행하는 것을 막는다.
    """
    dates = _date_series(df)
    data_min = pd.Timestamp(dates.min())
    data_max = pd.Timestamp(dates.max())
    years = _recent_complete_years(dates, n=3)

    sorted_population = sorted(
        list(population),
        key=lambda rb: (rb.fitness if getattr(rb, "fitness", None) is not None else -1e9),
        reverse=True,
    )[:max_candidates]

    base_kwargs = dict(
        position_limit_krw=position_limit_krw,
        market_history_df=market_history_df,
        sector_name=sector_name,
        ticker_sentiment=ticker_sentiment,
        fitness_mode=fitness_mode,
    )

    periods = []
    pass_count = 0
    weak_count = 0
    positive_count = 0

    for year in years:
        test_start_ts = max(pd.Timestamp(f"{year}-01-01"), data_min)
        test_end_ts = min(pd.Timestamp(f"{year}-12-31"), data_max)
        train_start_ts = data_min
        train_end_ts = test_start_ts - pd.Timedelta(days=1)
        if train_end_ts <= train_start_ts or test_end_ts <= test_start_ts:
            continue

        train_start = train_start_ts.strftime("%Y-%m-%d")
        train_end = train_end_ts.strftime("%Y-%m-%d")
        test_start = test_start_ts.strftime("%Y-%m-%d")
        test_end = test_end_ts.strftime("%Y-%m-%d")

        scored = []
        for rb in sorted_population:
            train_r = run_backtest(rb, df, start_date=train_start, end_date=train_end, **base_kwargs)
            score = max(0.0, train_r.avg_return_pct) * math.log1p(max(0, train_r.trade_count))
            if score > 0.0:
                scored.append((score, rb, train_r))

        valid = sorted(scored, key=lambda x: x[0], reverse=True)
        if len(valid) < min_valid_rules:
            periods.append(
                {
                    "year": year,
                    "train_period": [train_start, train_end],
                    "test_period": [test_start, test_end],
                    "valid_rules": len(valid),
                    "used_rules": 0,
                    "trades": 0,
                    "expectancy_pct": 0.0,
                    "portfolio_pnl_pct": 0.0,
                    "status": f"NO_TRADE(valid={len(valid)})",
                }
            )
            continue

        top = [rb for score, rb, train_r in valid[:top_n]]
        ens = run_ensemble_backtest(top, df, start_date=test_start, end_date=test_end, **base_kwargs)
        raw = ens["raw"]
        trades = int(raw.trade_count)
        exp_pct = float(raw.avg_return_pct)
        pnl_pct = float(ens["portfolio_total_pnl_pct"])
        status = _period_status(trades, exp_pct)

        if status == "PASS":
            pass_count += 1
        elif status == "WEAK":
            weak_count += 1
        if exp_pct > 0:
            positive_count += 1

        periods.append(
            {
                "year": year,
                "train_period": [train_start, train_end],
                "test_period": [test_start, test_end],
                "valid_rules": len(valid),
                "used_rules": len(top),
                "trades": trades,
                "expectancy_pct": exp_pct,
                "portfolio_pnl_pct": pnl_pct,
                "status": status,
            }
        )

    total = len(periods)
    grade, mode = _grade_from_counts(pass_count, weak_count, positive_count)
    avg_exp = sum(p["expectancy_pct"] for p in periods) / total if total else 0.0
    avg_trades = sum(p["trades"] for p in periods) / total if total else 0.0

    return {
        "ticker": ticker,
        "type": "diagnostic",
        "validated": False,
        "method": "swing_score_wf_v1",
        "grade": grade,
        "mode": mode,
        "criteria": {
            "note": "Diagnostic grade: reuses the current GA final_population; not a true walk-forward retrain.",
            "score": "max(0, train_avg_return_pct) * log1p(train_trade_count)",
            "min_valid_rules": min_valid_rules,
            "top_n": top_n,
            "pass": f"trades >= {DEFAULT_MIN_TEST_TRADES} and expectancy_pct >= {DEFAULT_PASS_EXP_PCT}",
            "weak": f"(trades >= {DEFAULT_MIN_TEST_TRADES} and expectancy_pct >= {DEFAULT_WEAK_EXP_PCT}) or (trades >= 5 and expectancy_pct >= {DEFAULT_PASS_EXP_PCT})",
        },
        "summary": {
            "periods": total,
            "pass_count": pass_count,
            "weak_count": weak_count,
            "positive_count": positive_count,
            "avg_expectancy_pct": avg_exp,
            "avg_trades": avg_trades,
        },
        "periods": periods,
    }
