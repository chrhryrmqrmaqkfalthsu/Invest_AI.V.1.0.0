"""Phase-1b GA callback gate for LR8D full PIT rerun.

This gate verifies that the GA evaluate_fn can call the fold-aware execution-mode
backtest wrapper and that the wrapper returns the same BacktestResult interface
used by the existing GA fitness path.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from engine.learning.backtest import run_backtest
from engine.learning.execution_mode_backtest import (
    _synthetic_df,
    _synthetic_rulebook,
    run_backtest_execution_mode,
)
from engine.learning.genetic import GAConfig, run_ga
from engine.strategies.rulebook import Rulebook

OUT_DIR = Path("data/_system/research/learning_ga_execution_mode_gate")


def _time_calls(fn, *, repeat: int = 20) -> float:
    start = time.perf_counter()
    for _ in range(max(1, int(repeat))):
        fn()
    return time.perf_counter() - start


def run_learning_ga_execution_mode_gate() -> dict[str, Any]:
    df = _synthetic_df()
    start_date = str(df.index[60].date())
    fold_end_date = str(df.index[62].date())
    call_count = 0
    sample_results: list[dict[str, Any]] = []

    def evaluate_fn(rb: Rulebook) -> float:
        nonlocal call_count
        call_count += 1
        result = run_backtest_execution_mode(
            rb,
            df,
            start_date=start_date,
            end_date=fold_end_date,
            warmup=60,
            position_limit_krw=10_000.0,
            fitness_mode="swing",
            entry_execution_mode="t_plus_1_open",
            exit_execution_mode="conservative_core",
            fold_exit_policy="fold_end_mark_to_market",
        )
        if len(sample_results) < 5:
            first_trade = result.trades[0] if result.trades else {}
            sample_results.append(
                {
                    "trade_count": result.trade_count,
                    "fitness": result.fitness,
                    "entry_execution_mode": first_trade.get("entry_execution_mode"),
                    "exit_execution_mode": first_trade.get("exit_execution_mode"),
                    "fold_exit_policy": first_trade.get("fold_exit_policy"),
                    "exit_date": first_trade.get("exit_date"),
                    "exit_reason": first_trade.get("exit_reason"),
                }
            )
        return float(result.fitness)

    cfg = GAConfig(
        population=6,
        generations=2,
        elite_ratio=0.33,
        mutation_rate=0.05,
        mutation_strength=0.05,
        tournament_size=2,
        seed_pattern_ratio=0.34,
        early_stop_no_improve=2,
        random_seed=20260610,
    )
    base = _synthetic_rulebook()
    seed = copy.deepcopy(base)
    start = time.perf_counter()
    ga_result = run_ga(base_rulebook=base, evaluate_fn=evaluate_fn, ga_config=cfg, seed_rulebooks=[seed])
    ga_elapsed = time.perf_counter() - start

    legacy_rb = _synthetic_rulebook()
    wrapper_rb = _synthetic_rulebook()
    legacy_elapsed = _time_calls(
        lambda: run_backtest(
            legacy_rb,
            df,
            start_date=start_date,
            end_date=fold_end_date,
            warmup=60,
            position_limit_krw=10_000.0,
            fitness_mode="swing",
            use_llm_events=True,
        ),
        repeat=20,
    )
    wrapper_elapsed = _time_calls(
        lambda: run_backtest_execution_mode(
            wrapper_rb,
            df,
            start_date=start_date,
            end_date=fold_end_date,
            warmup=60,
            position_limit_krw=10_000.0,
            fitness_mode="swing",
            entry_execution_mode="t_plus_1_open",
            exit_execution_mode="conservative_core",
            fold_exit_policy="fold_end_mark_to_market",
        ),
        repeat=20,
    )
    ratio = (wrapper_elapsed / legacy_elapsed) if legacy_elapsed > 0 else None
    performance_warning = bool(ratio is not None and ratio > 3.0)

    wrapper_sample = run_backtest_execution_mode(
        _synthetic_rulebook(),
        df,
        start_date=start_date,
        end_date=fold_end_date,
        warmup=60,
        position_limit_krw=10_000.0,
        fitness_mode="swing",
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
    )
    trade = wrapper_sample.trades[0] if wrapper_sample.trades else {}
    checks = {
        "ga_evaluate_fn_called": call_count > 0,
        "ga_generations_run_positive": int(getattr(ga_result, "generations_run", 0) or 0) > 0,
        "ga_best_has_fitness": getattr(ga_result.best, "fitness", None) is not None,
        "wrapper_returns_backtest_result_shape": all(
            hasattr(wrapper_sample, attr)
            for attr in ["fitness", "trade_count", "expectancy_pct", "profit_factor", "trades"]
        ),
        "sample_trade_has_tplus1_mode": trade.get("entry_execution_mode") == "t_plus_1_open",
        "sample_trade_has_conservative_core_mode": trade.get("exit_execution_mode") == "conservative_core",
        "sample_trade_fold_end_bounded": bool(trade) and str(trade.get("exit_date")) <= fold_end_date,
        "performance_ratio_positive": ratio is not None and ratio > 0,
    }
    checks["passed"] = all(checks.values())
    summary = {
        "gate": "learning_ga_execution_mode_gate",
        "purpose": "verify GA evaluate_fn uses run_backtest_execution_mode with T+1/conservative_core/fold_end semantics",
        "ga_config": {
            "population": cfg.population,
            "generations": cfg.generations,
            "elite_ratio": cfg.elite_ratio,
            "mutation_rate": cfg.mutation_rate,
            "mutation_strength": cfg.mutation_strength,
            "seed_pattern_ratio": cfg.seed_pattern_ratio,
        },
        "entry_execution_mode": "t_plus_1_open",
        "exit_execution_mode": "conservative_core",
        "fold_exit_policy": "fold_end_mark_to_market",
        "fitness_mode": "swing",
        "start_date": start_date,
        "fold_end_date": fold_end_date,
        "ga_elapsed_seconds": ga_elapsed,
        "evaluate_fn_call_count": call_count,
        "generations_run": getattr(ga_result, "generations_run", None),
        "best_fitness": float(getattr(ga_result.best, "fitness", 0.0) or 0.0),
        "sample_results": sample_results,
        "performance": {
            "repeat": 20,
            "legacy_seconds": legacy_elapsed,
            "wrapper_seconds": wrapper_elapsed,
            "wrapper_vs_legacy_ratio": ratio,
            "warning_threshold_ratio": 3.0,
            "performance_warning": performance_warning,
        },
        "checks": checks,
        "passed": bool(checks["passed"]),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
