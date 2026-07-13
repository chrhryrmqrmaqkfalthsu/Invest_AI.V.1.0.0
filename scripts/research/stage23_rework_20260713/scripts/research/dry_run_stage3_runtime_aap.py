#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = ROOT.parents[2]
RUNNER_PATH = ROOT / "scripts/research/run_stage3_aggressive.py"
AAP_PATH = PROJECT_ROOT / "data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv"


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_stage3_runtime_dry_run", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Stage3 wrapper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_aap() -> pd.DataFrame:
    from engine.core.indicators import calc_indicators

    df = pd.read_csv(AAP_PATH)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return calc_indicators(df)


def _candidate_errors(mod: Any, rb: Any) -> list[str]:
    from engine.strategies.rulebook import validate_entry_feature_domains, validate_entry_intervals

    return list(validate_entry_intervals(rb)) + list(validate_entry_feature_domains(rb))


def main() -> int:
    mod = _load_runner()
    df = _load_aap()
    split = next(item for item in mod._base.TRAIN_SPLITS if item["label"] == "train_1")
    base_rulebook = mod._base.Rulebook(ticker="AAP", asset_type="us_stock", direction="long")
    ctx = {
        "df": df,
        "base_rulebook": base_rulebook,
        "market_history_df": None,
        "sector_name": "tech",
        "ticker_sentiment": None,
        "data_start": str(df.index.min().date()),
        "data_end": str(df.index.max().date()),
    }
    domain = mod.build_entry_feature_domain(ctx, start=split["start"], end=split["end"])

    evaluation_results: list[Any] = []

    def evaluate_fn(rulebook: Any) -> float:
        result = mod.run_entry_backtest_period(rulebook, ctx, start=split["start"], end=split["end"])
        evaluation_results.append(result)
        return mod._base.safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

    ga = mod._base.run_ga(
        base_rulebook=base_rulebook,
        evaluate_fn=evaluate_fn,
        ga_config=mod._base.GAConfig(
            population=10,
            generations=3,
            elite_ratio=0.2,
            mutation_rate=0.15,
            mutation_strength=0.2,
            tournament_size=3,
            seed_pattern_ratio=0.0,
            early_stop_no_improve=3,
            random_seed=20260713,
        ),
        gene_scope="entry",
        entry_feature_domain=domain,
    )

    all_candidates = [ga.best, *list(ga.final_population)]
    validation_errors = [_candidate_errors(mod, rb) for rb in all_candidates]
    invalid_count = sum(1 for errors in validation_errors if errors)

    best_entry = mod.run_entry_backtest_period(ga.best, ctx, start=split["start"], end=split["end"])
    best_original = mod._base.run_backtest_period(ga.best, ctx, start=split["start"], end=split["end"])

    entry_trades = list(getattr(best_entry, "trades", []) or [])
    original_trades = list(getattr(best_original, "trades", []) or [])
    interval_break_count = sum(1 for trade in entry_trades if trade.get("exit_reason") == "entry_interval_break")
    holding_points = sum(int(trade.get("holding_signal_path_count", 0) or 0) for trade in entry_trades)
    cooldown_points = sum(int(trade.get("cooldown_signal_path_count", 0) or 0) for trade in entry_trades)
    high_quality_interval_fail_count = sum(
        1
        for point in list(getattr(best_entry, "daily_signal_tape", []) or [])
        if point.get("strict_entry") is True
        and point.get("should_buy") is False
        and float(point.get("quality_score", 0.0) or 0.0) >= float(point.get("threshold", 0.0) or 0.0)
    )

    expected = {
        "ma_trend": {"q01": -23.2794308986, "q99": 4.89041584351, "sample_count": 251},
        "macd_hist": {"q01": -8.33190114975, "q99": 2.62791978606, "sample_count": 251},
        "rsi": {"q01": 11.752174327, "q99": 70.2607673746, "sample_count": 251},
        "bb_position": {"q01": -0.238972470409, "q99": 1.09241939669, "sample_count": 251},
        "volume_ratio": {"q01": 0.524428105406, "q99": 2.2934032984, "sample_count": 251},
    }
    domain_checks: dict[str, Any] = {}
    for feature, exp in expected.items():
        got = domain[feature]
        domain_checks[feature] = {
            "sample_count": int(got["sample_count"]),
            "q01": float(got["q01"]),
            "q99": float(got["q99"]),
            "sample_count_match": int(got["sample_count"]) == int(exp["sample_count"]),
            "q01_close": bool(np.isclose(float(got["q01"]), float(exp["q01"]), rtol=1e-9, atol=1e-9)),
            "q99_close": bool(np.isclose(float(got["q99"]), float(exp["q99"]), rtol=1e-9, atol=1e-9)),
        }

    checks = {
        "domain_loaded": set(domain) == {"ma_trend", "macd_hist", "rsi", "bb_position", "volume_ratio"},
        "domain_matches_report": all(
            row["sample_count_match"] and row["q01_close"] and row["q99_close"]
            for row in domain_checks.values()
        ),
        "population_size_within_limit": len(ga.final_population) == 10,
        "generations_within_limit": int(ga.generations_run) <= 3,
        "all_candidates_valid": invalid_count == 0,
        "entry_scope_schema_v2": all(int(rb.entry_interval_schema_version) >= 2 for rb in all_candidates),
        "strict_and_runtime_seen": any(
            point.get("strict_entry") is True
            for point in list(getattr(best_entry, "daily_signal_tape", []) or [])
        ),
        "high_quality_cannot_override_interval": high_quality_interval_fail_count > 0,
        "daily_tape_populated": int(getattr(best_entry, "daily_signal_tape_count", 0) or 0) > 0,
        "holding_path_populated": holding_points > 0,
        "cooldown_path_measured": cooldown_points >= 0,
        "interval_break_triggered": interval_break_count > 0,
        "entry_wrapper_semantics": getattr(best_entry, "execution_semantics_cache_token", "") == mod._execution_backtest.EXECUTION_SEMANTICS_CACHE_TOKEN,
        "original_path_not_entry_exit": all(
            trade.get("exit_reason") not in {
                "entry_interval_break",
                "entry_provisional_atr_stop",
                "entry_provisional_max_holding",
            }
            for trade in original_trades
        ),
    }

    output = {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "ticker": "AAP",
        "split": split,
        "ga_config": {"population": 10, "generations": 3, "seed": 20260713},
        "domain": domain_checks,
        "ga": {
            "generations_run": ga.generations_run,
            "final_population_count": len(ga.final_population),
            "evaluations": len(evaluation_results),
            "invalid_candidate_count": invalid_count,
            "best_fitness": float(getattr(ga.best, "fitness", 0.0) or 0.0),
            "best_joint_support": int(getattr(ga.best, "entry_joint_support_count", 0) or 0),
            "best_feature_support": {
                feature: int(meta.get("interval_support_count", 0) or 0)
                for feature, meta in ga.best.entry_feature_domains.items()
            },
        },
        "entry_runtime": {
            "trade_count": int(getattr(best_entry, "trade_count", 0) or 0),
            "daily_signal_tape_count": int(getattr(best_entry, "daily_signal_tape_count", 0) or 0),
            "holding_signal_points": holding_points,
            "cooldown_signal_points": cooldown_points,
            "interval_break_count": interval_break_count,
            "high_quality_interval_fail_count": high_quality_interval_fail_count,
            "exit_reasons": dict(Counter(str(t.get("exit_reason", "")) for t in entry_trades)),
        },
        "original_runtime": {
            "trade_count": int(getattr(best_original, "trade_count", 0) or 0),
            "exit_reasons": dict(Counter(str(t.get("exit_reason", "")) for t in original_trades)),
        },
        "checks": checks,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if output["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
