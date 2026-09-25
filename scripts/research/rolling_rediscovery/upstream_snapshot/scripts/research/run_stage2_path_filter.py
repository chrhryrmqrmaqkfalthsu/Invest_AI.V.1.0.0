#!/usr/bin/env python3
"""Stage2/3 rolling rediscovery pilot entry point.

This is the directly modified copy of scripts/research/run_stage2_path_filter.py.
It delegates to the directly modified copied orchestration in run_stage2.py;
there is no separate newly invented runner outside the copied working tree.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import random
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ISOLATED_ROOT = Path(__file__).resolve().parents[2]
if str(ISOLATED_ROOT) in sys.path:
    sys.path.remove(str(ISOLATED_ROOT))
sys.path.insert(0, str(ISOLATED_ROOT))

from scripts.research import run_stage2


def select_symbols_from_available_2020_history() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use the copied runner's frozen-data helpers with a 2020-available gate.

    The frozen snapshot is a six-year capture starting 2020-06-08, not a full
    January-2020 archive. Current-live symbols are mandatory, so a symbol is
    eligible when its first stored date is within calendar year 2020 and it has
    OOS coverage. The exact first date remains recorded in symbol_list.csv.
    """
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for path in sorted(run_stage2.SNAPSHOT_DIR.glob("*_ohlcv.csv")):
        ticker = path.name.replace("_ohlcv.csv", "")
        if ticker.startswith("benchmark_"):
            continue
        try:
            frame = run_stage2.read_ohlcv(path)
            first = pd.Timestamp(frame.index.min()).normalize()
            last = pd.Timestamp(frame.index.max()).normalize()
            eligible = first.year <= 2020 and last >= run_stage2.OOS_START and len(frame) >= 500
            row = {
                "ticker": ticker,
                "source_path": str(path.relative_to(run_stage2.KINGMAKER_ROOT)),
                "source_sha256": run_stage2.sha256(path),
                "history_first_date": first.strftime("%Y-%m-%d"),
                "history_last_date": last.strftime("%Y-%m-%d"),
                "history_rows": len(frame),
            }
            if eligible:
                candidates.append(row)
            else:
                rejected.append({**row, "status": "INSUFFICIENT_HISTORY"})
        except Exception as exc:
            rejected.append(
                {
                    "ticker": ticker,
                    "source_path": str(path),
                    "status": "UNRECOVERABLE",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    by_ticker = {row["ticker"]: row for row in candidates}
    missing_current = [ticker for ticker in run_stage2.CURRENT_LIVE_10 if ticker not in by_ticker]
    if missing_current:
        raise RuntimeError(f"current live symbols missing 2020-available frozen history: {missing_current}")

    pool = [row for row in candidates if row["ticker"] not in run_stage2.CURRENT_LIVE_10]
    rng = random.Random(run_stage2.SELECTION_SEED)
    rng.shuffle(pool)
    chosen = [by_ticker[ticker] for ticker in run_stage2.CURRENT_LIVE_10] + pool[:40]
    if len(chosen) != 50:
        raise RuntimeError(f"50 symbols not available: {len(chosen)}")

    selected: list[dict[str, Any]] = []
    for index, row in enumerate(chosen, 1):
        selected.append(
            {
                "selection_order": index,
                "selection_type": "CURRENT_LIVE_10" if row["ticker"] in run_stage2.CURRENT_LIVE_10 else "DETERMINISTIC_RANDOM_40",
                "selection_seed": run_stage2.SELECTION_SEED,
                **row,
                "status": "SELECTED",
                "history_coverage_note": "frozen six-year snapshot begins 2020-06-08; exact available start used",
            }
        )
    return selected, rejected


def merge_worker_results_without_method_loss(worker_returns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Merge every worker row, preserving both rolling and fixed methods.

    Each successful worker must contribute 6 backtest rows: 3 regimes ×
    (rolling_same_threshold_no_holding_cap, fixed_2_sessions). Any other count is
    retained but recorded as a worker error instead of silently dropping rows.
    """
    merged: dict[str, list[dict[str, Any]]] = {
        "training": [],
        "bounds": [],
        "fallback": [],
        "metrics": [],
        "survivors": [],
        "backtests": [],
        "whipsaw": [],
        "overfit": [],
        "parallel": [],
        "errors": [],
    }
    for returned in worker_returns:
        path = Path(returned["output_path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        merged["parallel"].append(data.get("parallel_row", returned.get("parallel_row", {})))
        if data.get("status") != "OK":
            merged["errors"].append(
                {
                    "ticker": data.get("ticker"),
                    "status": data.get("status"),
                    "error": data.get("error", "UNKNOWN"),
                }
            )
            continue
        backtest_rows = list(data.get("backtest_rows", []) or [])
        if len(backtest_rows) != 6:
            merged["errors"].append(
                {
                    "ticker": data.get("ticker"),
                    "status": "PARTIAL_BACKTEST_ROWS",
                    "error": f"expected 6 backtest rows, got {len(backtest_rows)}",
                }
            )
        merged["training"].extend(list(data.get("training_rows", []) or []))
        merged["bounds"].extend(list(data.get("bounds_rows", []) or []))
        merged["fallback"].extend(list(data.get("fallback_rows", []) or []))
        merged["metrics"].extend(list(data.get("metric_rows", []) or []))
        merged["survivors"].append(dict(data.get("survivor_row", {}) or {}))
        merged["backtests"].extend(backtest_rows)
        merged["whipsaw"].extend(list(data.get("whipsaw_rows", []) or []))
        merged["overfit"].append(dict(data.get("overfit_row", {}) or {}))
    return merged


# Directly alter copied orchestration dependencies before executing it.
run_stage2.select_symbols = select_symbols_from_available_2020_history
run_stage2._merge_worker_results = merge_worker_results_without_method_loss


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(run_stage2.main())
