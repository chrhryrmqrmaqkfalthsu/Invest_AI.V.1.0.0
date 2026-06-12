"""Small A/B experiment for explicit downturn-weighted GA fitness.

This file is intentionally isolated from the production stage2 runner.
It reuses the same backtest/execution semantics and GA core, but runs only a
small fixed 2025H2-fold experiment for a handful of tickers.

Condition A: current aggregate train fitness.
Condition B: aggregate train fitness + explicit 2022 downturn weighted fitness.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.metadata import compute_rulebook_hash
from engine.learning.genetic import GAConfig, run_ga
from engine.strategies.rulebook import Rulebook
from scripts.research.run_honest_stage2_full_ga_4fold import (
    DEFAULT_OHLCV_CACHE,
    ENTRY_EXECUTION_MODE,
    EXIT_EXECUTION_MODE,
    FITNESS_MODE,
    FOLD_EXIT_POLICY,
    STRESS_LABEL,
    context_from_cache,
    result_metrics,
    run_backtest_cc,
    safe_float,
)

TARGET_TICKERS = ["LASR", "WELL", "ITT", "CW", "WAB"]
TRAIN_END = "2025-05-31"
OOS_START = "2025-06-01"
DOWN_START = "2022-01-01"
DOWN_END = "2022-12-31"
POPULATION = 30
GENERATIONS = 20
BASE_SEED = 20260612
FULL_WEIGHT = 0.6
DOWN_WEIGHT = 0.4
DD_PENALTY_WEIGHT = 1.0
EXP_PENALTY_WEIGHT = 5.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_exp_ga_config(ticker: str) -> GAConfig:
    # Same seed for condition A and condition B for the same ticker.
    seed = BASE_SEED + sum(ord(ch) for ch in f"{ticker}|{STRESS_LABEL}_STRESS")
    return GAConfig(
        population=POPULATION,
        generations=GENERATIONS,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=8,
        random_seed=seed,
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _stop_pct(trade: Mapping[str, Any]) -> float | None:
    entry = _num(trade.get("entry_price"))
    stop = trade.get("stop_price_at_entry")
    if not entry or stop is None:
        return None
    return (_num(stop) - entry) / entry * 100.0


def _trail_pct(trade: Mapping[str, Any]) -> float | None:
    entry = _num(trade.get("entry_price"))
    trail = trade.get("trailing_stop_at_entry")
    if not entry or trail is None:
        return None
    return (_num(trail) - entry) / entry * 100.0


def _target_pct(trade: Mapping[str, Any]) -> float | None:
    entry = _num(trade.get("entry_price"))
    target = trade.get("target_price_at_entry")
    if not entry or target is None:
        return None
    return (_num(target) - entry) / entry * 100.0


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def trade_quality(result: Any) -> dict[str, Any]:
    trades = [dict(t) for t in (getattr(result, "trades", []) or [])]
    stops = [abs(x) for t in trades if (x := _stop_pct(t)) is not None]
    trails = [abs(x) for t in trades if (x := _trail_pct(t)) is not None]
    targets = [x for t in trades if (x := _target_pct(t)) is not None]
    mfe5_bad = [
        t
        for t in trades
        if _num(t.get("max_profit_during_hold")) >= 5.0 and _num(t.get("pnl_pct")) <= 1.0
    ]
    return {
        "trade_count": len(trades),
        "avg_initial_stop_abs_pct": round(_avg(stops), 6),
        "max_initial_stop_abs_pct": round(max(stops), 6) if stops else 0.0,
        "avg_initial_trail_abs_pct": round(_avg(trails), 6),
        "max_initial_trail_abs_pct": round(max(trails), 6) if trails else 0.0,
        "avg_target_pct": round(_avg(targets), 6),
        "max_target_pct": round(max(targets), 6) if targets else 0.0,
        "mfe5_bad_count": len(mfe5_bad),
        "mfe5_bad_rate_all": round(len(mfe5_bad) / len(trades) * 100.0, 6) if trades else 0.0,
        "exit_reason_counts": dict(Counter(str(t.get("exit_reason") or "") for t in trades)),
    }


def market_genes(rb: Rulebook) -> dict[str, Any]:
    use_adj = bool(getattr(rb, "use_market_entry_adjustment", True))
    strength = _num(getattr(rb, "market_adjustment_strength", 0.0))
    market_weight = _num(getattr(rb, "market_score_weight", 0.0))
    return {
        "use_market_entry_adjustment": use_adj,
        "market_adjustment_strength": round(strength, 6),
        "market_score_weight": round(market_weight, 6),
        "sector_strength_weight": round(_num(getattr(rb, "sector_strength_weight", 0.0)), 6),
        "vix_sensitivity": round(_num(getattr(rb, "vix_sensitivity", 0.0)), 6),
        "effective_market_score_sensitivity": round(abs(market_weight) * strength if use_adj else 0.0, 6),
        "crash_buy_enabled": bool(getattr(rb, "crash_buy_enabled", False)),
        "crash_threshold_score": round(_num(getattr(rb, "crash_threshold_score", 0.0)), 6),
        "signal_threshold": round(_num(getattr(rb, "signal_threshold", 0.0)), 6),
        "trailing_atr": round(_num(getattr(rb, "trailing_atr", 0.0)), 6),
        "take_profit_atr": round(_num(getattr(rb, "take_profit_atr", 0.0)), 6),
        "stop_loss_atr": round(_num(getattr(rb, "stop_loss_atr", 0.0)), 6),
        "exit_strategy": str(getattr(rb, "exit_strategy", "")),
        "breakeven_enabled": bool(getattr(rb, "breakeven_enabled", False)),
    }


def evaluate_fn_a(rb: Rulebook, ctx: Mapping[str, Any], train_start: str, train_end: str) -> float:
    result = run_backtest_cc(rb, ctx, start_date=train_start, end_date=train_end)
    return safe_float(getattr(result, "fitness", -1_000_000.0), -1_000_000.0)


def evaluate_fn_b(rb: Rulebook, ctx: Mapping[str, Any], train_start: str, train_end: str) -> float:
    full = run_backtest_cc(rb, ctx, start_date=train_start, end_date=train_end)
    down = run_backtest_cc(rb, ctx, start_date=DOWN_START, end_date=DOWN_END)
    f_full = safe_float(getattr(full, "fitness", -1_000_000.0), -1_000_000.0)
    f_down = safe_float(getattr(down, "fitness", -1_000_000.0), -1_000_000.0)
    dd_pen = min(0.0, safe_float(getattr(down, "max_drawdown_pct", 0.0), 0.0))
    exp_pen = min(0.0, safe_float(getattr(down, "expectancy_pct", 0.0), 0.0))
    return f_full * FULL_WEIGHT + f_down * DOWN_WEIGHT + dd_pen * DD_PENALTY_WEIGHT + exp_pen * EXP_PENALTY_WEIGHT


def evaluate_best(
    rb: Rulebook,
    ctx: Mapping[str, Any],
    *,
    train_start: str,
    train_end: str,
    oos_end: str,
) -> dict[str, Any]:
    full = run_backtest_cc(copy.deepcopy(rb), ctx, start_date=train_start, end_date=train_end)
    down = run_backtest_cc(copy.deepcopy(rb), ctx, start_date=DOWN_START, end_date=DOWN_END)
    oos = run_backtest_cc(copy.deepcopy(rb), ctx, start_date=OOS_START, end_date=oos_end)
    return {
        "train_full": result_metrics(full),
        "downturn_2022": result_metrics(down),
        "oos_2025h2": result_metrics(oos),
        "downturn_2022_quality": trade_quality(down),
        "oos_2025h2_quality": trade_quality(oos),
    }


def run_condition(
    *,
    ticker: str,
    condition: str,
    ctx: Mapping[str, Any],
    train_start: str,
    train_end: str,
    oos_end: str,
) -> dict[str, Any]:
    ga_cfg = make_exp_ga_config(ticker)
    base_rulebook = ctx["base_rulebook"]
    if condition == "A":
        evaluator: Callable[[Rulebook], float] = lambda rb: evaluate_fn_a(rb, ctx, train_start, train_end)
    elif condition == "B":
        evaluator = lambda rb: evaluate_fn_b(rb, ctx, train_start, train_end)
    else:
        raise ValueError(f"unknown condition={condition}")
    started = time.time()
    ga_result = run_ga(base_rulebook=base_rulebook, evaluate_fn=evaluator, ga_config=ga_cfg)
    best = copy.deepcopy(getattr(ga_result, "best"))
    composite_fitness = safe_float(getattr(best, "fitness", 0.0), 0.0)
    best_hash = compute_rulebook_hash(best)
    evals = evaluate_best(best, ctx, train_start=train_start, train_end=train_end, oos_end=oos_end)
    return {
        "ticker": ticker,
        "condition": condition,
        "rulebook_hash": best_hash,
        "composite_ga_fitness": composite_fitness,
        "market_genes": market_genes(best),
        "evaluations": evals,
        "ga_config": {
            "population": ga_cfg.population,
            "generations": ga_cfg.generations,
            "random_seed": ga_cfg.random_seed,
            "elite_ratio": ga_cfg.elite_ratio,
            "mutation_rate": ga_cfg.mutation_rate,
            "mutation_strength": ga_cfg.mutation_strength,
            "early_stop_no_improve": ga_cfg.early_stop_no_improve,
        },
        "ga_generations_run": int(getattr(ga_result, "generations_run", 0) or 0),
        "ga_fitness_history": list(getattr(ga_result, "fitness_history", []) or []),
        "elapsed_sec": round(time.time() - started, 6),
    }


def run_experiment(args: argparse.Namespace) -> int:
    out_dir = Path(args.output_root) / f"exp_downturn_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    started = time.time()
    for ticker in args.tickers:
        ticker = str(ticker).upper().strip()
        if not ticker:
            continue
        ctx = context_from_cache(ticker, Path(args.ohlcv_cache))
        train_start = str(ctx.get("data_start") or ctx.get("data_min") or "2020-01-01")
        train_end = TRAIN_END
        oos_end = str(ctx.get("data_end") or ctx.get("data_max") or "")
        for condition in ("A", "B"):
            row = run_condition(
                ticker=ticker,
                condition=condition,
                ctx=ctx,
                train_start=train_start,
                train_end=train_end,
                oos_end=oos_end,
            )
            row["train_start"] = train_start
            row["train_end"] = train_end
            row["downturn_start"] = DOWN_START
            row["downturn_end"] = DOWN_END
            row["oos_start"] = OOS_START
            row["oos_end"] = oos_end
            rows.append(row)
            print(
                "|".join(
                    map(
                        str,
                        [
                            ticker,
                            condition,
                            "use_adj",
                            row["market_genes"]["use_market_entry_adjustment"],
                            "eff",
                            row["market_genes"]["effective_market_score_sensitivity"],
                            "2022_trades",
                            row["evaluations"]["downturn_2022"]["trade_count"],
                            "2022_exp",
                            round(row["evaluations"]["downturn_2022"]["expectancy_pct"], 4),
                            "2022_dd",
                            round(row["evaluations"]["downturn_2022"]["max_drawdown_pct"], 4),
                            "2025H2_exp",
                            round(row["evaluations"]["oos_2025h2"]["expectancy_pct"], 4),
                        ],
                    )
                ),
                flush=True,
            )
    rows_path = out_dir / "ab_rows.jsonl"
    rows_path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) for r in rows) + "\n", encoding="utf-8")
    summary = {
        "created_at": utc_now(),
        "elapsed_sec": round(time.time() - started, 6),
        "output_dir": str(out_dir),
        "tickers": args.tickers,
        "conditions": {
            "A": "current aggregate train fitness: train_start~2025-05-31",
            "B": "0.6*full_train_fitness + 0.4*2022_fitness + 1.0*min(0, 2022_dd) + 5.0*min(0, 2022_expectancy)",
        },
        "weights": {
            "full_weight": FULL_WEIGHT,
            "down_weight": DOWN_WEIGHT,
            "dd_penalty_weight": DD_PENALTY_WEIGHT,
            "exp_penalty_weight": EXP_PENALTY_WEIGHT,
            "note": "first-pass arbitrary weights for direction check only; not optimized",
        },
        "execution": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "base_seed": BASE_SEED,
            "fitness_mode": FITNESS_MODE,
            "entry_execution_mode": ENTRY_EXECUTION_MODE,
            "exit_execution_mode": EXIT_EXECUTION_MODE,
            "fold_exit_policy": FOLD_EXIT_POLICY,
            "live_hard_stop_guard": True,
        },
        "rows_path": str(rows_path),
        "rows": rows,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", default=TARGET_TICKERS)
    parser.add_argument("--ohlcv-cache", type=Path, default=DEFAULT_OHLCV_CACHE)
    parser.add_argument("--output-root", type=Path, default=Path("data/_system/research/exp_downturn"))
    return parser.parse_args()


def main() -> int:
    return run_experiment(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
