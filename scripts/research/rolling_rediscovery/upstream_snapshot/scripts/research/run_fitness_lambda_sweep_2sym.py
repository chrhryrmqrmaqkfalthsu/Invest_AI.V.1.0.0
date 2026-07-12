#!/usr/bin/env python3
"""Sweep consistency lambda 0.2 and 0.3 for the AAP/POWI floored hybrid.

The existing lambda=0.5 consistency fitness implementation is reused without
editing.  Each worker sets only penalty_ga.CONSISTENCY_LAMBDA before invoking
the previous worker routine.  All feature, gene, threshold, GA, gate and trade
logic remains identical to the lambda=0.5 pilot.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
KINGMAKER_ROOT = HERE.parents[5]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_fitness_consistency_penalty_2sym as base

OUT_ROOT = KINGMAKER_ROOT / "data/_system/analysis/fitness_lambda_sweep_2sym_20260712"
LAMBDA_VALUES = (0.2, 0.3)
MAX_WORKERS = 6
TARGET_TICKERS = ["AAP", "POWI"]
BASELINE_FLOORED_DIR = KINGMAKER_ROOT / "data/_system/analysis/hybrid_group_test_2sym_floored_20260712"
LAMBDA_05_DIR = KINGMAKER_ROOT / "data/_system/analysis/fitness_consistency_penalty_2sym_20260712"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def lambda_label(value: float) -> str:
    return f"lambda_{value:.1f}"


def _rehash_candidate(candidate: dict[str, Any], lambda_value: float) -> None:
    current = str(candidate["model_hash"])
    payload = f"{current}|runtime_consistency_lambda={lambda_value:.1f}"
    new_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    candidate["model_hash"] = new_hash
    candidate["survivor_row"]["model_hash"] = new_hash
    for key in ["training_rows", "learned_rows", "threshold_rows", "metric_rows"]:
        for row in candidate[key]:
            row["model_hash"] = new_hash


def _worker_train_lambda(task: dict[str, Any]) -> dict[str, Any]:
    lambda_value = float(task["consistency_lambda"])
    base.penalty_ga.CONSISTENCY_LAMBDA = lambda_value
    candidate = base._worker_train(task)
    _rehash_candidate(candidate, lambda_value)

    candidate["survivor_row"]["fitness_consistency_lambda"] = lambda_value
    candidate["survivor_row"]["lambda_source"] = "RUNTIME_CONSTANT_ONLY"
    for row in candidate["training_rows"]:
        row["fitness_consistency_lambda"] = lambda_value
        row["lambda_source"] = "RUNTIME_CONSTANT_ONLY"
    for row in candidate["learned_rows"]:
        row["fitness_consistency_lambda"] = lambda_value
        row["lambda_source"] = "RUNTIME_CONSTANT_ONLY"
    candidate["worker_log"]["fitness_consistency_lambda"] = lambda_value
    candidate["worker_log"]["lambda_source"] = "RUNTIME_CONSTANT_ONLY"
    return candidate


def _candidate_stats(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    frame = frame[frame["ticker"].isin(TARGET_TICKERS)].copy()
    truthy = lambda series: series.astype(str).str.lower().isin(["true", "1", "1.0", "yes"])
    return {
        "candidate_count": int(len(frame)),
        "train_avg_precision": float(frame["train_precision"].mean()),
        "stress_avg_precision_cheating": float(frame["stress_precision"].mean()),
        "oos_avg_precision": float(frame["oos_precision"].mean()),
        "stress_gate_count": int(truthy(frame["stress_gate"]).sum()),
        "oos_gate_count": int(truthy(frame["oos_gate"]).sum()),
        "survivor_count": int(truthy(frame["survivor"]).sum()),
    }


def _run_one_lambda(
    lambda_value: float,
    feature_set: pd.DataFrame,
    parent_runner: Any,
) -> dict[str, Any]:
    started = time.perf_counter()
    output_dir = OUT_ROOT / lambda_label(lambda_value)
    output_dir.mkdir(parents=True, exist_ok=True)

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
                    "consistency_lambda": lambda_value,
                }
            )

    candidates: list[dict[str, Any]] = []
    context = mp.get_context("fork")
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=MAX_WORKERS,
        mp_context=context,
    ) as executor:
        future_map = {
            executor.submit(_worker_train_lambda, task): (
                task["ticker"],
                task["split_index"],
            )
            for task in tasks
        }
        for future in concurrent.futures.as_completed(future_map):
            ticker, split_index = future_map[future]
            try:
                candidates.append(future.result())
            except Exception as exc:
                raise RuntimeError(
                    f"lambda={lambda_value} worker failed ticker={ticker} split={split_index}: {exc}"
                ) from exc

    candidates.sort(
        key=lambda item: (TARGET_TICKERS.index(item["ticker"]), item["split_index"])
    )
    selected_by_ticker: dict[str, dict[str, Any]] = {}
    performance_by_ticker: dict[str, dict[str, Any]] = {}
    trades_by_ticker: dict[str, list[dict[str, Any]]] = {}
    offset_by_ticker: dict[str, dict[str, Any]] = {}

    for ticker in TARGET_TICKERS:
        ticker_candidates = [item for item in candidates if item["ticker"] == ticker]
        selected, selection_rule = parent_runner.select_trade_candidate(ticker_candidates)
        selected["survivor_row"]["selected_for_trade"] = True
        selected["survivor_row"]["selection_rule"] = selection_rule
        selected_by_ticker[ticker] = selected
        performance, trades, offset = parent_runner.trace_trades(ticker, selected)
        performance_by_ticker[ticker] = performance
        trades_by_ticker[ticker] = trades
        offset_by_ticker[ticker] = offset

    training_rows = [row for candidate in candidates for row in candidate["training_rows"]]
    learned_rows = [row for candidate in candidates for row in candidate["learned_rows"]]
    threshold_rows = [row for candidate in candidates for row in candidate["threshold_rows"]]
    metric_rows = [row for candidate in candidates for row in candidate["metric_rows"]]
    survivor_rows = [candidate["survivor_row"] for candidate in candidates]
    worker_rows = [candidate["worker_log"] for candidate in candidates]

    parent_runner.write_csv(output_dir / "training_log.csv", training_rows)
    parent_runner.write_csv(output_dir / "learned_genes.csv", learned_rows)
    parent_runner.write_csv(output_dir / "group_threshold_check.csv", threshold_rows)
    parent_runner.write_csv(output_dir / "per_regime_metrics.csv", metric_rows)
    parent_runner.write_csv(output_dir / "survivor_summary.csv", survivor_rows)
    parent_runner.write_csv(output_dir / "parallel_run_log.csv", worker_rows)
    parent_runner.write_csv(output_dir / "aap_trades.csv", trades_by_ticker["AAP"])
    parent_runner.write_csv(output_dir / "powi_trades.csv", trades_by_ticker["POWI"])

    current_comparison = parent_runner.comparison_rows(
        feature_set,
        selected_by_ticker,
        performance_by_ticker,
        trades_by_ticker,
        offset_by_ticker,
    )
    parent_runner.write_csv(
        output_dir / "lambda_vs_strict_internal.csv",
        current_comparison,
    )
    base._apply_floor_audit(output_dir)

    comparison_frame = pd.DataFrame(current_comparison)
    pooled = comparison_frame[
        comparison_frame["ticker"].eq("ALL_2_POOLED")
        & comparison_frame["method"].eq("HYBRID_GROUP_COUNT_AND")
    ].iloc[0]
    stats = _candidate_stats(output_dir / "survivor_summary.csv")
    summary = {
        "generated_at_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        "consistency_lambda": lambda_value,
        "lambda_source": "RUNTIME_CONSTANT_ONLY",
        "fitness_module_sha_expected_unchanged": "4c716806d69e8d3f3d1321113f0bfe9ed2b696328da08ae2783caa57b24bc8c6",
        "stress_role": "FITNESS_SCORER_ONLY_CHEATING_NOT_INDEPENDENT",
        "gene_domain_source": "TRAIN_ONLY",
        "fallback_source": "TRAIN_ONLY",
        "max_parallel_workers": MAX_WORKERS,
        "actual_worker_pids": sorted({int(row["worker_pid"]) for row in worker_rows}),
        "candidate_stats": stats,
        "selected_pooled_metrics": {
            "oos_rows": int(pooled["oos_rows"]),
            "oos_signal_count": int(pooled["oos_signal_count"]),
            "oos_coverage": float(pooled["oos_coverage"]),
            "selected_oos_precision": float(pooled["oos_precision"]),
            "trade_count": int(pooled["trade_count"]),
            "avg_return_pct": float(pooled["avg_return_pct"]),
            "compounded_return_pct": float(pooled["compounded_return_pct"]),
            "max_drawdown_pct": float(pooled["max_drawdown_pct"]),
            "win_rate": float(pooled["win_rate"]),
        },
        "selected_models": {
            ticker: {
                "model_hash": selected_by_ticker[ticker]["model_hash"],
                "origin_train_label": selected_by_ticker[ticker]["split"]["label"],
                "survivor": bool(selected_by_ticker[ticker]["survivor_row"]["survivor"]),
                "selection_rule": selected_by_ticker[ticker]["survivor_row"]["selection_rule"],
                "group_thresholds": {
                    group: int(selected_by_ticker[ticker]["best"].group_thresholds[index])
                    for index, group in enumerate(parent_runner.GROUP_NAMES)
                },
            }
            for ticker in TARGET_TICKERS
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _row_from_existing(method: str, row: pd.Series, stats: dict[str, Any], lambda_value: float) -> dict[str, Any]:
    return {
        "method": method,
        "fitness_consistency_lambda": lambda_value,
        "oos_coverage": float(row["oos_coverage"]),
        "selected_oos_precision": float(row["oos_precision"]),
        "oos_avg_precision_6_candidates": float(stats["oos_avg_precision"]),
        "stress_avg_precision_(커닝_독립검증아님)": float(stats["stress_avg_precision_cheating"]),
        "oos_gate_count": int(stats["oos_gate_count"]),
        "oos_gate_denominator": int(stats["candidate_count"]),
        "survivor_count": int(stats["survivor_count"]),
        "survivor_denominator": int(stats["candidate_count"]),
        "avg_return_pct": float(row["avg_return_pct"]),
        "compounded_return_pct": float(row["compounded_return_pct"]),
        "max_drawdown_pct": float(row["max_drawdown_pct"]),
        "trade_count": int(row["trade_count"]),
        "diagnostic_trade_warning": bool(int(stats["survivor_count"]) == 0),
    }


def _existing_stats(path: Path) -> dict[str, Any]:
    raw = _candidate_stats(path)
    return raw


def _build_comparison(lambda_summaries: dict[float, dict[str, Any]]) -> tuple[pd.DataFrame, str, float | None]:
    previous = pd.read_csv(LAMBDA_05_DIR / "four_way_comparison.csv")
    rows: list[dict[str, Any]] = []
    method_map = [
        ("STRICT_AND_12_BASELINE", 0.0),
        ("HYBRID_GROUP_COUNT_AND_UNFLOORED", 0.0),
        ("HYBRID_GROUP_COUNT_AND_FLOORED", 0.0),
        ("HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5", 0.5),
    ]
    stats_paths = {
        "STRICT_AND_12_BASELINE": base.STRICT_DIR / "survivor_summary.csv",
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": base.UNFLOORED_DIR / "survivor_summary.csv",
        "HYBRID_GROUP_COUNT_AND_FLOORED": base.FLOORED_DIR / "survivor_summary.csv",
        "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5": LAMBDA_05_DIR / "survivor_summary.csv",
    }
    for method, lambda_value in method_map:
        source_row = previous[previous["method"].eq(method)].iloc[0]
        rows.append(
            _row_from_existing(
                method,
                source_row,
                _existing_stats(stats_paths[method]),
                lambda_value,
            )
        )

    for lambda_value in LAMBDA_VALUES:
        summary = lambda_summaries[lambda_value]
        selected = summary["selected_pooled_metrics"]
        stats = summary["candidate_stats"]
        rows.append(
            {
                "method": f"HYBRID_FLOORED_CONSISTENCY_LAMBDA_{lambda_value:.1f}",
                "fitness_consistency_lambda": lambda_value,
                "oos_coverage": selected["oos_coverage"],
                "selected_oos_precision": selected["selected_oos_precision"],
                "oos_avg_precision_6_candidates": stats["oos_avg_precision"],
                "stress_avg_precision_(커닝_독립검증아님)": stats["stress_avg_precision_cheating"],
                "oos_gate_count": stats["oos_gate_count"],
                "oos_gate_denominator": stats["candidate_count"],
                "survivor_count": stats["survivor_count"],
                "survivor_denominator": stats["candidate_count"],
                "avg_return_pct": selected["avg_return_pct"],
                "compounded_return_pct": selected["compounded_return_pct"],
                "max_drawdown_pct": selected["max_drawdown_pct"],
                "trade_count": selected["trade_count"],
                "diagnostic_trade_warning": bool(stats["survivor_count"] == 0),
            }
        )

    order = {
        "STRICT_AND_12_BASELINE": 0,
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": 1,
        "HYBRID_GROUP_COUNT_AND_FLOORED": 2,
        "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_5": 3,
        "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_3": 4,
        "HYBRID_FLOORED_CONSISTENCY_LAMBDA_0_2": 5,
    }
    comparison = pd.DataFrame(rows)
    comparison["_order"] = comparison["method"].map(order)
    comparison = comparison.sort_values("_order").drop(columns="_order")
    comparison.to_csv(OUT_ROOT / "five_way_comparison.csv", index=False)

    baseline = comparison[
        comparison["method"].eq("HYBRID_GROUP_COUNT_AND_FLOORED")
    ].iloc[0]
    baseline_precision = float(baseline["selected_oos_precision"])
    baseline_oos_gate = int(baseline["oos_gate_count"])
    found: list[tuple[float, int, int, float]] = []
    for lambda_value in LAMBDA_VALUES:
        row = comparison[
            comparison["method"].eq(
                f"HYBRID_FLOORED_CONSISTENCY_LAMBDA_{lambda_value:.1f}"
            )
        ].iloc[0]
        near_baseline = float(row["selected_oos_precision"]) >= baseline_precision - 0.03
        gate_improved = int(row["oos_gate_count"]) > baseline_oos_gate
        survivor_appeared = int(row["survivor_count"]) >= 1
        if near_baseline and (gate_improved or survivor_appeared):
            found.append(
                (
                    lambda_value,
                    int(row["survivor_count"]),
                    int(row["oos_gate_count"]),
                    float(row["selected_oos_precision"]),
                )
            )

    if found:
        found.sort(key=lambda item: (item[1], item[2], item[3], -item[0]), reverse=True)
        selected_lambda = found[0][0]
        verdict = "LAMBDA_FOUND"
    else:
        selected_lambda = None
        verdict = "LAMBDA_TRADEOFF_PERSISTS"
    return comparison, verdict, selected_lambda


def run() -> dict[str, Any]:
    started = time.perf_counter()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    parent_runner = base._load_base_runner("lambda_sweep_parent")
    symbols = parent_runner.load_symbol_rows()
    feature_set, errors = parent_runner.build_feature_set(symbols)
    if errors:
        parent_runner.write_csv(OUT_ROOT / "feature_errors.csv", errors)
    if feature_set.empty or feature_set["ticker"].nunique() != 2:
        raise RuntimeError(f"UNRECOVERABLE feature set rows={len(feature_set)}")
    feature_set["date"] = pd.to_datetime(feature_set["date"])
    feature_set.to_csv(OUT_ROOT / "feature_set_2sym.csv", index=False)
    parent_runner.write_csv(
        OUT_ROOT / "label_distribution.csv",
        parent_runner.label_distribution(feature_set),
    )

    summaries: dict[float, dict[str, Any]] = {}
    for lambda_value in LAMBDA_VALUES:
        summaries[lambda_value] = _run_one_lambda(
            lambda_value,
            feature_set,
            parent_runner,
        )

    comparison, verdict, selected_lambda = _build_comparison(summaries)
    baseline = comparison[
        comparison["method"].eq("HYBRID_GROUP_COUNT_AND_FLOORED")
    ].iloc[0]
    result = {
        "generated_at_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        "lambda_values": list(LAMBDA_VALUES),
        "fitness_logic_changed": False,
        "runtime_constant_only": True,
        "stress_role": "FITNESS_SCORER_ONLY_CHEATING_NOT_INDEPENDENT",
        "baseline_lambda_0_selected_oos_precision": float(
            baseline["selected_oos_precision"]
        ),
        "baseline_lambda_0_oos_gate_count": int(baseline["oos_gate_count"]),
        "lambda_results": {
            f"{value:.1f}": summaries[value] for value in LAMBDA_VALUES
        },
        "verdict": verdict,
        "selected_lambda": selected_lambda,
    }
    (OUT_ROOT / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
