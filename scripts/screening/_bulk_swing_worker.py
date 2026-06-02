#!/usr/bin/env python3
"""bulk swing diagnostic worker.

종목 1개를 TRAIN-only GA + OOS TEST 단일 분할로 빠르게 진단한다.
운영 학습 경로와 완전히 분리하기 위해 engine.learning.learner.learn()은 호출하지 않는다.
출력은 data/_system/bulk_diagnostic/swing/results/{ticker}.json 하나뿐이다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.adapters.factory import get_adapter
from engine.learning.backtest import run_backtest
from engine.learning.genetic import GAConfig, run_ga
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.rulebook import Rulebook, default_rulebook

OUTPUT_ROOT = ROOT / "data" / "_system" / "bulk_diagnostic" / "swing"

DEFAULT_YEARS = 6
DEFAULT_TEST_MONTHS = 24
DEFAULT_POSITION_LIMIT_KRW = 10_000_000
DEFAULT_FITNESS_MODE = "swing"
DEFAULT_SEED = 42
DEFAULT_POPULATION = 40
DEFAULT_GENERATIONS = 50
DEFAULT_ELITE_RATIO = 0.2
DEFAULT_EARLY_STOP = 10


def _detect_sector_name(meta_name: str) -> str:
    """learner._detect_sector_name와 동일한 규칙을 worker 내부에 복제한다."""
    name = (meta_name or "").lower()
    if any(k in name for k in ["반도체", "tech", "qqq", "kodex", "tiger", "s&p", "나스닥", "semi", "it"]):
        return "tech"
    if any(k in name for k in ["에너지", "energy", "oil", "원유"]):
        return "energy"
    if any(k in name for k in ["금융", "finance", "bank", "은행", "보험"]):
        return "finance"
    if any(k in name for k in ["헬스", "health", "bio", "제약"]):
        return "healthcare"
    if any(k in name for k in ["소비", "consumer", "리테일"]):
        return "consumer"
    if any(k in name for k in ["산업", "industrial"]):
        return "industrials"
    return "tech"


def _result_path(ticker: str, output_root: Path = OUTPUT_ROOT) -> Path:
    return output_root / "results" / f"{ticker}.json"


def _date_series(df: pd.DataFrame):
    if "date" in df.columns:
        return pd.to_datetime(df["date"])
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(df.index), index=df.index)
    return None


def _cut_df_end_date(df: pd.DataFrame, end_date: str | None) -> pd.DataFrame:
    """재현성 검증용 end_date 컷. 기본 bulk 실행에서는 쓰지 않는다."""
    if not end_date:
        return df
    end_ts = pd.Timestamp(end_date)
    if "date" in df.columns:
        dates = pd.to_datetime(df["date"])
        return df.loc[dates <= end_ts].copy()
    if isinstance(df.index, pd.DatetimeIndex):
        return df.loc[df.index <= end_ts].copy()
    return df


def _split_dates(df: pd.DataFrame, test_months: int) -> dict[str, str | None]:
    dates = _date_series(df)
    if dates is None or len(dates) == 0:
        return {
            "train_start": None,
            "train_end": None,
            "test_start": None,
            "test_end": None,
        }
    end_date = pd.Timestamp(dates.max())
    split_date = end_date - pd.DateOffset(months=test_months)
    train_start = pd.Timestamp(dates.min()).strftime("%Y-%m-%d")
    train_end = split_date.strftime("%Y-%m-%d")
    test_start = (split_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    test_end = end_date.strftime("%Y-%m-%d")
    return {
        "train_start": train_start,
        "train_end": train_end,
        "test_start": test_start,
        "test_end": test_end,
    }


def classify(train_trades: int | None, train_exp: float | None, test_trades: int | None, test_exp: float | None) -> str:
    """Diagnostic 3등급 분류. exp 단위는 %이다."""
    if test_exp is None or test_trades is None or test_trades == 0:
        return "NEG"
    if test_exp < 0:
        return "NEG"
    if test_trades >= 10 and test_exp < 0.5:
        return "NEG"
    if test_trades >= 10 and test_exp >= 1.0:
        return "POS"
    if test_exp >= 1.0 and test_trades < 10:
        return "UNCERTAIN"
    if 0.5 <= test_exp < 1.0:
        return "UNCERTAIN"
    if (train_trades or 0) >= 10 and (train_exp or 0.0) >= 2.0 and test_exp >= 0:
        return "UNCERTAIN"
    return "NEG"


def priority_score(train_result, test_result) -> float:
    """true-WF 후보 정렬용 점수. avg_return_pct는 사용하지 않는다."""
    test_exp_p = max(0.0, float(test_result.expectancy_pct or 0.0))
    train_exp_p = max(0.0, float(train_result.expectancy_pct or 0.0))
    test_tr = max(0, int(test_result.trade_count or 0))
    train_tr = max(0, int(train_result.trade_count or 0))
    return test_exp_p * math.log1p(test_tr) + 0.5 * train_exp_p * math.log1p(train_tr)


def _bt_summary(result) -> dict[str, Any]:
    return {
        "trades": int(result.trade_count or 0),
        "expectancy_pct": float(result.expectancy_pct or 0.0),
        "fitness": float(result.fitness or 0.0),
        "win_rate": float(result.win_rate or 0.0),
    }


def run_one(
    ticker: str,
    output_root: Path = OUTPUT_ROOT,
    years: int = DEFAULT_YEARS,
    test_months: int = DEFAULT_TEST_MONTHS,
    position_limit_krw: float = DEFAULT_POSITION_LIMIT_KRW,
    fitness_mode: str = DEFAULT_FITNESS_MODE,
    seed: int | None = DEFAULT_SEED,
    end_date: str | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    ticker = ticker.upper().strip()
    fitness_mode = (fitness_mode or DEFAULT_FITNESS_MODE).strip().lower()

    adapter = get_adapter(ticker)
    meta = adapter.meta
    df = adapter.load_history(years=years)
    df = _cut_df_end_date(df, end_date)
    periods = _split_dates(df, test_months)
    if not periods["train_start"] or not periods["test_start"]:
        raise RuntimeError("date information unavailable; cannot split train/test")

    market_hist = get_market_history(years=max(years + 1, 6))
    sector_name = _detect_sector_name(meta.name)
    ticker_sentiment = load_ticker_sentiment(ticker)

    base_rb = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rb.sector_name = sector_name

    def evaluate_fn(rb: Rulebook) -> float:
        train_r = run_backtest(
            rb,
            df,
            position_limit_krw=position_limit_krw,
            market_history_df=market_hist,
            sector_name=sector_name,
            start_date=periods["train_start"],
            end_date=periods["train_end"],
            ticker_sentiment=ticker_sentiment,
            fitness_mode=fitness_mode,
        )
        return train_r.fitness

    ga_cfg = GAConfig(
        population=DEFAULT_POPULATION,
        generations=DEFAULT_GENERATIONS,
        elite_ratio=DEFAULT_ELITE_RATIO,
        early_stop_no_improve=DEFAULT_EARLY_STOP,
        random_seed=seed,
    )
    ga_result = run_ga(base_rulebook=base_rb, evaluate_fn=evaluate_fn, ga_config=ga_cfg)

    best_rb = ga_result.best
    ga_best_fitness = float(getattr(best_rb, "fitness", 0.0) or 0.0)
    best_rb.ticker = ticker
    best_rb.asset_type = meta.asset_type
    best_rb.direction = meta.direction
    best_rb.sector_name = sector_name

    train_result = run_backtest(
        best_rb,
        df,
        position_limit_krw=position_limit_krw,
        market_history_df=market_hist,
        sector_name=sector_name,
        start_date=periods["train_start"],
        end_date=periods["train_end"],
        ticker_sentiment=ticker_sentiment,
        fitness_mode=fitness_mode,
    )
    test_result = run_backtest(
        best_rb,
        df,
        position_limit_krw=position_limit_krw,
        market_history_df=market_hist,
        sector_name=sector_name,
        start_date=periods["test_start"],
        end_date=periods["test_end"],
        ticker_sentiment=ticker_sentiment,
        fitness_mode=fitness_mode,
    )

    status = classify(
        int(train_result.trade_count or 0),
        float(train_result.expectancy_pct or 0.0),
        int(test_result.trade_count or 0),
        float(test_result.expectancy_pct or 0.0),
    )
    score = priority_score(train_result, test_result)
    elapsed = time.time() - t0

    result = {
        "ticker": ticker,
        "type": "bulk_swing_diagnostic",
        "validated": False,
        "fitness_mode": fitness_mode,
        "status": status,
        "priority_score": score,
        "elapsed_sec": elapsed,
        "data_rows": int(len(df)),
        "data_end_date": periods["test_end"],
        "train_period": [periods["train_start"], periods["train_end"]],
        "test_period": [periods["test_start"], periods["test_end"]],
        "train": _bt_summary(train_result),
        "test": _bt_summary(test_result),
        "ga": {
            "population": DEFAULT_POPULATION,
            "generations": DEFAULT_GENERATIONS,
            "seed": seed,
            "generations_run": int(ga_result.generations_run or 0),
            "best_fitness": ga_best_fitness,
        },
        "asset_meta": meta.to_dict(),
        "note": "Diagnostic only. Not validated. Must pass true-WF before trading.",
    }

    out_path = _result_path(ticker, output_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(out_path)
    return result


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="bulk swing diagnostic worker for one ticker")
    p.add_argument("ticker")
    p.add_argument("--output-root", default=str(OUTPUT_ROOT))
    p.add_argument("--years", type=int, default=DEFAULT_YEARS)
    p.add_argument("--test-months", type=int, default=DEFAULT_TEST_MONTHS)
    p.add_argument("--position-limit-krw", type=float, default=DEFAULT_POSITION_LIMIT_KRW)
    p.add_argument("--fitness-mode", default=DEFAULT_FITNESS_MODE)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--end-date", default=None, help="재현성 검증용 데이터 컷오프 YYYY-MM-DD. 기본 bulk 실행에서는 미사용")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = run_one(
            ticker=args.ticker,
            output_root=Path(args.output_root),
            years=args.years,
            test_months=args.test_months,
            position_limit_krw=args.position_limit_krw,
            fitness_mode=args.fitness_mode,
            seed=args.seed,
            end_date=args.end_date,
        )
        tr = result["train"]
        te = result["test"]
        print(
            f"{result['ticker']} {result['status']} elapsed={result['elapsed_sec']:.0f}s "
            f"train={tr['trades']}/{tr['expectancy_pct']:+.2f}% "
            f"test={te['trades']}/{te['expectancy_pct']:+.2f}% "
            f"score={result['priority_score']:.3f}"
        )
        return 0
    except Exception as e:
        error = {
            "ticker": args.ticker.upper().strip(),
            "type": "bulk_swing_diagnostic",
            "validated": False,
            "fitness_mode": args.fitness_mode,
            "status": "ERROR",
            "priority_score": 0.0,
            "elapsed_sec": None,
            "error": f"{type(e).__name__}: {str(e)}",
            "note": "Diagnostic only. Not validated. Must pass true-WF before trading.",
        }
        out_path = _result_path(args.ticker.upper().strip(), Path(args.output_root))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(error, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"{error['ticker']} ERROR {error['error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
