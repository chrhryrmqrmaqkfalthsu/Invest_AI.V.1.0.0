#!/usr/bin/env python3
"""AAP/POWI floored-hybrid pilot with train↔stress fitness consistency.

Six independent ticker×train-split GAs run in six worker processes.  The base
feature, label, gate, interval, group-threshold and rolling-exit implementations
are reused.  Only the GA fitness evaluator is replaced by the lambda=0.5
train↔stress precision consistency scorer.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class _NoopLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None

    def success(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bind(self, *args: Any, **kwargs: Any) -> "_NoopLogger":
        return self


logger_stub = types.ModuleType("engine.core.logger")
logger_stub.get_logger = lambda name="": _NoopLogger()
logger_stub.trade_logger = lambda: _NoopLogger()
sys.modules["engine.core.logger"] = logger_stub

HERE = Path(__file__).resolve().parent
ISOLATED_ROOT = HERE.parents[1]
KINGMAKER_ROOT = HERE.parents[5]
if str(ISOLATED_ROOT) not in sys.path:
    sys.path.insert(0, str(ISOLATED_ROOT))

from engine.learning import grouped_genetic_floored_consistency as penalty_ga
from engine.learning.genetic import IntervalGAConfig

BASE_RUNNER_PATH = HERE / "run_hybrid_group_test_2sym.py"
OUT_DIR = KINGMAKER_ROOT / "data/_system/analysis/fitness_consistency_penalty_2sym_20260712"
STRICT_DIR = KINGMAKER_ROOT / "data/_system/analysis/stage2_3_rediscovery_pilot_20260712"
UNFLOORED_DIR = KINGMAKER_ROOT / "data/_system/analysis/hybrid_group_test_2sym_20260712"
FLOORED_DIR = KINGMAKER_ROOT / "data/_system/analysis/hybrid_group_test_2sym_floored_20260712"
TARGET_TICKERS = ["AAP", "POWI"]
MAX_WORKERS = 6


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_base_runner(module_suffix: str) -> Any:
    module_name = f"hybrid_group_base_consistency_{module_suffix}_{os.getpid()}"
    spec = importlib.util.spec_from_file_location(module_name, BASE_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load base runner: {BASE_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    module.validate_grouped_gene = penalty_ga.validate_grouped_gene
    module.group_count_details = penalty_ga.group_count_details
    return module


def _consistency_model_hash(base_hash: str) -> str:
    payload = (
        f"{base_hash}|fitness=train_precision-0.5*positive_gap|"
        "stress_role=consistency_scorer_only"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replace_candidate_hash(candidate: dict[str, Any]) -> None:
    new_hash = _consistency_model_hash(str(candidate["model_hash"]))
    candidate["model_hash"] = new_hash
    candidate["survivor_row"]["model_hash"] = new_hash
    for key in ["training_rows", "learned_rows", "threshold_rows", "metric_rows"]:
        for row in candidate[key]:
            row["model_hash"] = new_hash


def _worker_train(task: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    runner = _load_base_runner(f"worker_{task['ticker']}_{task['split_index']}")
    ticker = str(task["ticker"])
    split_index = int(task["split_index"])
    split = dict(task["split"])
    ticker_frame = task["ticker_frame"].copy()
    cfg = IntervalGAConfig()

    origin_train = runner.split_frame(ticker_frame, split)
    if len(origin_train) < 100:
        raise RuntimeError(
            f"INSUFFICIENT_DATA {ticker} {split['label']} {len(origin_train)}"
        )
    stress = ticker_frame[ticker_frame["regime"] == "stress"].copy()
    domain_low, domain_high = runner.fit_domain(origin_train)
    x_train = runner.normalize(origin_train, domain_low, domain_high)
    y_train = origin_train["label_2d3pct"].to_numpy(int)
    x_stress = runner.normalize(stress, domain_low, domain_high)
    y_stress = stress["label_2d3pct"].to_numpy(int)
    g3_floor_norm = np.nanquantile(
        x_train[:, runner.GROUP_INDEXES[runner.G3_GROUP_INDEX]],
        runner.G3_MEMBER_PERCENTILE_FLOOR,
        axis=0,
    )

    ga = penalty_ga.train_grouped_interval_ga(
        x_train,
        y_train,
        runner.FEATURES,
        runner.GROUP_INDEXES,
        x_stress=x_stress,
        y_stress=y_stress,
        seed=runner.ticker_seed(ticker, split_index),
        config=cfg,
        g3_group_index=runner.G3_GROUP_INDEX,
        g3_floor_norm=g3_floor_norm,
    )
    candidate = runner.evaluate_candidate(
        ticker,
        ticker_frame,
        split,
        split_index,
        domain_low,
        domain_high,
        g3_floor_norm,
        ga,
        cfg,
    )
    _replace_candidate_hash(candidate)

    best = candidate["best"]
    fitness_fields = {
        "fitness_consistency_lambda": penalty_ga.CONSISTENCY_LAMBDA,
        "fitness_train_precision": float(best.precision),
        "fitness_stress_passed_count": int(
            getattr(best, "stress_passed_count_for_fitness", 0)
        ),
        "fitness_stress_precision": float(
            getattr(best, "stress_precision_for_fitness", 0.0)
        ),
        "fitness_precision_gap": float(
            getattr(best, "precision_gap_for_fitness", 0.0)
        ),
        "fitness_adjusted_precision": float(
            getattr(best, "adjusted_precision_for_fitness", 0.0)
        ),
        "fitness_consistency_penalty_points": float(
            getattr(best, "consistency_penalty_points", 0.0)
        ),
        "stress_role": "FITNESS_SCORER_ONLY",
        "gene_domain_source": "TRAIN_ONLY",
        "fallback_source": "TRAIN_ONLY",
    }
    candidate["survivor_row"].update(fitness_fields)
    for row in candidate["learned_rows"]:
        row.update(
            {
                "fitness_consistency_lambda": penalty_ga.CONSISTENCY_LAMBDA,
                "stress_role": "FITNESS_SCORER_ONLY",
            }
        )
    for row in candidate["training_rows"]:
        row.update(
            {
                "parallel_worker_pid": os.getpid(),
                "parallel_worker_count_limit": MAX_WORKERS,
                "stress_role": "FITNESS_SCORER_ONLY",
                "gene_domain_source": "TRAIN_ONLY",
                "fallback_source": "TRAIN_ONLY",
            }
        )

    candidate["worker_log"] = {
        "ticker": ticker,
        "origin_train_label": split["label"],
        "split_index": split_index,
        "worker_pid": os.getpid(),
        "started_at_utc": task["submitted_at_utc"],
        "finished_at_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        "status": "OK",
        "population": cfg.population,
        "generations": cfg.generations,
        "patience": cfg.patience,
        "max_workers": MAX_WORKERS,
        "train_rows": len(origin_train),
        "stress_rows": len(stress),
        "stress_used_for_gene_inputs": False,
        "stress_used_for_fitness_only": True,
    }
    return candidate


def _apply_floor_audit(output_dir: Path) -> None:
    thresholds = pd.read_csv(output_dir / "group_threshold_check.csv")
    thresholds["threshold_min_allowed"] = 2
    thresholds["threshold_max_allowed"] = thresholds["group_size"].astype(int) - 1
    thresholds["threshold_floor_valid"] = (
        thresholds["learned_threshold"].astype(int)
        >= thresholds["threshold_min_allowed"].astype(int)
    ) & (
        thresholds["learned_threshold"].astype(int)
        <= thresholds["threshold_max_allowed"].astype(int)
    )
    thresholds["full_group_threshold_present"] = (
        thresholds["learned_threshold"].astype(int)
        == thresholds["group_size"].astype(int)
    )
    thresholds.to_csv(output_dir / "group_threshold_check.csv", index=False)

    learned = pd.read_csv(output_dir / "learned_genes.csv")
    is_threshold = learned["gene_type"].eq("GROUP_THRESHOLD")
    learned["threshold_min_allowed"] = np.where(is_threshold, 2, np.nan)
    learned["threshold_max_allowed"] = np.where(
        is_threshold, learned["group_size"].astype(float) - 1.0, np.nan
    )
    learned["threshold_floor_valid"] = np.where(
        is_threshold,
        (learned["group_threshold"].astype(float) >= 2.0)
        & (
            learned["group_threshold"].astype(float)
            <= learned["group_size"].astype(float) - 1.0
        ),
        np.nan,
    )
    learned.to_csv(output_dir / "learned_genes.csv", index=False)

    survivors = pd.read_csv(output_dir / "survivor_summary.csv")
    valid_flags: list[bool] = []
    for raw in survivors["group_thresholds_json"]:
        values = json.loads(raw)
        valid_flags.append(
            bool(
                2 <= int(values["G1_PULLBACK"]) <= 3
                and 2 <= int(values["G2_VOLATILITY"]) <= 3
                and int(values["G3_RANGE_EXPANSION"]) == 2
                and int(values["G4_VOLUME_CONFIRMATION"]) == 2
            )
        )
    survivors["threshold_range_spec"] = "G1=2..3|G2=2..3|G3=2..2|G4=2..2"
    survivors["threshold_floor_valid"] = valid_flags
    survivors["full_group_threshold_present"] = False
    survivors.to_csv(output_dir / "survivor_summary.csv", index=False)


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "1.0", "yes"])


def _candidate_stats(path: Path, tickers: list[str]) -> dict[str, Any]:
    frame = pd.read_csv(path)
    frame = frame[frame["ticker"].isin(tickers)].copy()
    return {
        "candidate_count": len(frame),
        "train_avg_precision": float(frame["train_precision"].mean()),
        "stress_avg_precision": float(frame["stress_precision"].mean()),
        "oos_avg_precision": float(frame["oos_precision"].mean()),
        "stress_gate_count": int(_truthy(frame["stress_gate"]).sum()),
        "oos_gate_count": int(_truthy(frame["oos_gate"]).sum()),
        "survivor_count": int(_truthy(frame["survivor"]).sum()),
    }


def _build_four_way(current_internal: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    previous = pd.read_csv(FLOORED_DIR / "three_way_comparison.csv")
    previous = previous[previous["ticker"].eq("ALL_2_POOLED")].copy()
    previous = previous[
        previous["method"].isin(
            [
                "STRICT_AND_12_BASELINE",
                "HYBRID_GROUP_COUNT_AND_UNFLOORED",
                "HYBRID_GROUP_COUNT_AND_FLOORED",
            ]
        )
    ]
    current = current_internal[
        current_internal["ticker"].eq("ALL_2_POOLED")
        & current_internal["method"].eq("HYBRID_GROUP_COUNT_AND")
    ].copy()
    current["method"] = "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5"

    combined = pd.concat([previous, current], ignore_index=True, sort=False)
    stats_by_method = {
        "STRICT_AND_12_BASELINE": _candidate_stats(
            STRICT_DIR / "survivor_summary.csv", TARGET_TICKERS
        ),
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": _candidate_stats(
            UNFLOORED_DIR / "survivor_summary.csv", TARGET_TICKERS
        ),
        "HYBRID_GROUP_COUNT_AND_FLOORED": _candidate_stats(
            FLOORED_DIR / "survivor_summary.csv", TARGET_TICKERS
        ),
        "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5": _candidate_stats(
            OUT_DIR / "survivor_summary.csv", TARGET_TICKERS
        ),
    }
    for field in [
        "candidate_count",
        "train_avg_precision",
        "stress_avg_precision",
        "oos_avg_precision",
        "stress_gate_count",
        "oos_gate_count",
        "survivor_count",
    ]:
        combined[field] = combined["method"].map(
            {method: values[field] for method, values in stats_by_method.items()}
        )

    method_order = {
        "STRICT_AND_12_BASELINE": 0,
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": 1,
        "HYBRID_GROUP_COUNT_AND_FLOORED": 2,
        "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5": 3,
    }
    combined["fitness_consistency_lambda"] = combined["method"].map(
        {
            "STRICT_AND_12_BASELINE": 0.0,
            "HYBRID_GROUP_COUNT_AND_UNFLOORED": 0.0,
            "HYBRID_GROUP_COUNT_AND_FLOORED": 0.0,
            "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5": 0.5,
        }
    )
    combined["diagnostic_trade_warning"] = combined["survivor_count"].eq(0)
    combined["_order"] = combined["method"].map(method_order)
    keep = [
        "method",
        "oos_rows",
        "oos_signal_count",
        "oos_coverage",
        "oos_precision",
        "train_avg_precision",
        "stress_avg_precision",
        "oos_avg_precision",
        "stress_gate_count",
        "oos_gate_count",
        "survivor_count",
        "candidate_count",
        "trade_count",
        "avg_return_pct",
        "compounded_return_pct",
        "max_drawdown_pct",
        "win_rate",
        "plus3_reach_rate",
        "avg_holding_sessions",
        "max_holding_sessions",
        "fitness_consistency_lambda",
        "diagnostic_trade_warning",
        "_order",
    ]
    combined = combined[keep].sort_values("_order").drop(columns="_order")
    combined.to_csv(OUT_DIR / "four_way_comparison.csv", index=False)
    return combined, stats_by_method


def _verdict(four_way: pd.DataFrame) -> tuple[str, dict[str, float]]:
    indexed = four_way.set_index("method")
    previous = indexed.loc["HYBRID_GROUP_COUNT_AND_FLOORED"]
    current = indexed.loc["HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5"]
    stress_delta = float(
        current["stress_avg_precision"] - previous["stress_avg_precision"]
    )
    train_delta = float(
        current["train_avg_precision"] - previous["train_avg_precision"]
    )
    oos_delta = float(
        current["oos_avg_precision"] - previous["oos_avg_precision"]
    )
    pooled_oos_delta = float(current["oos_precision"] - previous["oos_precision"])
    survivor_count = int(current["survivor_count"])

    meaningful_stress = float(current["stress_avg_precision"]) >= 0.50
    survivor_appeared = survivor_count >= 1
    excessive_tradeoff = (
        oos_delta <= -0.10
        or pooled_oos_delta <= -0.08
        or train_delta <= -0.20
    )
    if (meaningful_stress or survivor_appeared) and excessive_tradeoff:
        verdict = "PENALTY_TRADEOFF"
    elif meaningful_stress or survivor_appeared:
        verdict = "PENALTY_WORKS"
    else:
        verdict = "PENALTY_NOEFFECT"
    return verdict, {
        "stress_delta": stress_delta,
        "train_delta": train_delta,
        "oos_avg_delta": oos_delta,
        "pooled_oos_delta": pooled_oos_delta,
    }


def run() -> dict[str, Any]:
    started = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parent_runner = _load_base_runner("parent")
    symbol_rows = parent_runner.load_symbol_rows()
    feature_set, errors = parent_runner.build_feature_set(symbol_rows)
    if errors:
        parent_runner.write_csv(OUT_DIR / "feature_errors.csv", errors)
    if feature_set.empty or feature_set["ticker"].nunique() != 2:
        raise RuntimeError(f"UNRECOVERABLE feature set rows={len(feature_set)}")
    feature_set["date"] = pd.to_datetime(feature_set["date"])
    feature_set.to_csv(OUT_DIR / "feature_set_2sym.csv", index=False)
    parent_runner.write_csv(
        OUT_DIR / "label_distribution.csv",
        parent_runner.label_distribution(feature_set),
    )

    tasks: list[dict[str, Any]] = []
    for ticker in TARGET_TICKERS:
        ticker_frame = (
            feature_set[feature_set["ticker"] == ticker]
            .copy()
            .sort_values("date")
            .reset_index(drop=True)
        )
        for split_index, split in enumerate(parent_runner.TRAIN_SPLITS, 1):
            tasks.append(
                {
                    "ticker": ticker,
                    "split_index": split_index,
                    "split": split,
                    "ticker_frame": ticker_frame,
                    "submitted_at_utc": utc_now(),
                }
            )

    all_candidates: list[dict[str, Any]] = []
    context = mp.get_context("fork")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=MAX_WORKERS, mp_context=context
    ) as executor:
        future_map = {
            executor.submit(_worker_train, task): (
                task["ticker"],
                task["split_index"],
            )
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            ticker, split_index = future_map[future]
            try:
                all_candidates.append(future.result())
            except Exception as exc:
                raise RuntimeError(
                    f"worker failed ticker={ticker} split={split_index}: {exc}"
                ) from exc

    all_candidates.sort(
        key=lambda item: (TARGET_TICKERS.index(item["ticker"]), item["split_index"])
    )
    selected_by_ticker: dict[str, dict[str, Any]] = {}
    hybrid_performance: dict[str, dict[str, Any]] = {}
    hybrid_trades: dict[str, list[dict[str, Any]]] = {}
    offset_stats: dict[str, dict[str, Any]] = {}

    for ticker in TARGET_TICKERS:
        candidates = [item for item in all_candidates if item["ticker"] == ticker]
        selected, selection_rule = parent_runner.select_trade_candidate(candidates)
        selected["survivor_row"]["selected_for_trade"] = True
        selected["survivor_row"]["selection_rule"] = selection_rule
        selected_by_ticker[ticker] = selected
        performance, trades, offset = parent_runner.trace_trades(ticker, selected)
        hybrid_performance[ticker] = performance
        hybrid_trades[ticker] = trades
        offset_stats[ticker] = offset

    training_rows = [
        row for candidate in all_candidates for row in candidate["training_rows"]
    ]
    learned_rows = [
        row for candidate in all_candidates for row in candidate["learned_rows"]
    ]
    threshold_rows = [
        row for candidate in all_candidates for row in candidate["threshold_rows"]
    ]
    metric_rows = [
        row for candidate in all_candidates for row in candidate["metric_rows"]
    ]
    survivor_rows = [candidate["survivor_row"] for candidate in all_candidates]
    worker_rows = [candidate["worker_log"] for candidate in all_candidates]

    parent_runner.write_csv(OUT_DIR / "training_log.csv", training_rows)
    parent_runner.write_csv(OUT_DIR / "learned_genes.csv", learned_rows)
    parent_runner.write_csv(
        OUT_DIR / "group_threshold_check.csv", threshold_rows
    )
    parent_runner.write_csv(OUT_DIR / "per_regime_metrics.csv", metric_rows)
    parent_runner.write_csv(OUT_DIR / "survivor_summary.csv", survivor_rows)
    parent_runner.write_csv(OUT_DIR / "parallel_run_log.csv", worker_rows)
    parent_runner.write_csv(
        OUT_DIR / "aap_trades_penalty.csv", hybrid_trades["AAP"]
    )
    parent_runner.write_csv(
        OUT_DIR / "powi_trades_penalty.csv", hybrid_trades["POWI"]
    )

    current_comparison = parent_runner.comparison_rows(
        feature_set,
        selected_by_ticker,
        hybrid_performance,
        hybrid_trades,
        offset_stats,
    )
    parent_runner.write_csv(
        OUT_DIR / "penalty_vs_strict_internal.csv", current_comparison
    )
    _apply_floor_audit(OUT_DIR)
    current_frame = pd.DataFrame(current_comparison)
    four_way, method_stats = _build_four_way(current_frame)
    verdict, deltas = _verdict(four_way)

    current_stats = method_stats["HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5"]
    summary = {
        "generated_at_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        "question": "Does a train↔stress precision fitness penalty produce stress-gate passers?",
        "fitness_formula": "precision_term=(train_precision-0.5*max(0,train_precision-stress_precision))*220",
        "consistency_lambda": penalty_ga.CONSISTENCY_LAMBDA,
        "stress_role": "FITNESS_SCORER_ONLY",
        "gene_domain_source": "TRAIN_ONLY",
        "fallback_source": "TRAIN_ONLY",
        "max_parallel_workers": MAX_WORKERS,
        "actual_worker_pids": sorted(
            {int(row["worker_pid"]) for row in worker_rows}
        ),
        "feature_rows": len(feature_set),
        "candidate_count": len(all_candidates),
        "ga_config": {
            "population": 100,
            "generations": 50,
            "patience": 15,
            "train_splits": ["train_1", "train_2", "train_3"],
        },
        "threshold_constraint": {
            "G1_PULLBACK": [2, 3],
            "G2_VOLATILITY": [2, 3],
            "G3_RANGE_EXPANSION": [2, 2],
            "G4_VOLUME_CONFIRMATION": [2, 2],
        },
        "current_candidate_stats": current_stats,
        "floored_baseline_candidate_stats": method_stats[
            "HYBRID_GROUP_COUNT_AND_FLOORED"
        ],
        "deltas_vs_floored": deltas,
        "survivor_tickers": sorted(
            {
                row["ticker"]
                for row in survivor_rows
                if bool(row["survivor"])
            }
        ),
        "selected_models": {
            ticker: {
                "model_hash": selected_by_ticker[ticker]["model_hash"],
                "origin_train_label": selected_by_ticker[ticker]["split"]["label"],
                "survivor": bool(
                    selected_by_ticker[ticker]["survivor_row"]["survivor"]
                ),
                "selection_rule": selected_by_ticker[ticker]["survivor_row"][
                    "selection_rule"
                ],
                "fitness_train_precision": float(
                    selected_by_ticker[ticker]["best"].precision
                ),
                "fitness_stress_precision": float(
                    getattr(
                        selected_by_ticker[ticker]["best"],
                        "stress_precision_for_fitness",
                        0.0,
                    )
                ),
                "fitness_gap": float(
                    getattr(
                        selected_by_ticker[ticker]["best"],
                        "precision_gap_for_fitness",
                        0.0,
                    )
                ),
                "group_thresholds": {
                    group: int(
                        selected_by_ticker[ticker]["best"].group_thresholds[index]
                    )
                    for index, group in enumerate(parent_runner.GROUP_NAMES)
                },
            }
            for ticker in TARGET_TICKERS
        },
        "verdict": verdict,
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
