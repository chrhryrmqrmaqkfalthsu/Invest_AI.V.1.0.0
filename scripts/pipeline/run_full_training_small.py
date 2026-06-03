#!/usr/bin/env python3
"""Small full-training smoke run.

Runs screening -> rolling -> full_training through process_ticker. NET/NVDA are
expected to pass the stock_score>=60 gate; GE is expected to stop after rolling.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.full_training import SMOKE_FULL_TRAINING_GA_CONFIG, run_full_training  # noqa: E402
from engine.pipeline.orchestrator import PIPELINE_ROOT, process_ticker, write_json  # noqa: E402

DEFAULT_TICKERS = ["NET", "NVDA", "GE"]


def _smoke_full_training(ticker: str, context: dict[str, Any] | None = None, run_id: str | None = None) -> dict[str, Any]:
    return run_full_training(
        ticker,
        context=context,
        ga_config=SMOKE_FULL_TRAINING_GA_CONFIG,
        max_members=20,
        run_id=run_id,
    )


def _row_from_result(result: dict[str, Any]) -> dict[str, Any]:
    full = result.get("full_training", {}) or {}
    rolling = result.get("rolling", {}) or {}
    dist = full.get("member_score_distribution", {}) or {}
    top = full.get("top_members", []) or []
    return {
        "ticker": result.get("ticker"),
        "final_status": result.get("final_status"),
        "final_stage": result.get("final_stage"),
        "stock_score": rolling.get("stock_score"),
        "rolling_excluded": rolling.get("excluded"),
        "full_training_executed": full.get("executed"),
        "full_training_reason_code": full.get("reason_code"),
        "member_count": full.get("member_count"),
        "qualified_count": full.get("qualified_count"),
        "member_score_min": dist.get("min"),
        "member_score_median": dist.get("median"),
        "member_score_max": dist.get("max"),
        "qualified_score_min": dist.get("qualified_min"),
        "qualified_score_median": dist.get("qualified_median"),
        "qualified_score_max": dist.get("qualified_max"),
        "top_members": top[:5],
        "elapsed_sec": result.get("elapsed_sec"),
        "outputs": result.get("outputs", {}),
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def _print_rows(rows: list[dict[str, Any]], run_id: str, elapsed_sec: float) -> None:
    print("=" * 132)
    print("Full-training small smoke run")
    print("=" * 132)
    print(f"run_id:      {run_id}")
    print(f"elapsed_sec: {elapsed_sec:.2f}")
    print()
    print("ticker | status | score | ft_exec | reason | members | qualified | score_min/med/max | sec")
    print("-" * 110)
    for r in rows:
        print(
            f"{str(r.get('ticker')):6s} | "
            f"{str(r.get('final_status')):18s} | "
            f"{_fmt(r.get('stock_score'), 3):>7s} | "
            f"{str(r.get('full_training_executed')):7s} | "
            f"{str(r.get('full_training_reason_code') or ''):12s} | "
            f"{str(r.get('member_count') or ''):7s} | "
            f"{str(r.get('qualified_count') or ''):9s} | "
            f"{_fmt(r.get('member_score_min'))}/{_fmt(r.get('member_score_median'))}/{_fmt(r.get('member_score_max'))} | "
            f"{_fmt(r.get('elapsed_sec'), 1)}"
        )
        if r.get("top_members"):
            print("  top members:")
            for m in r["top_members"][:3]:
                print(
                    "   - "
                    f"rank={m.get('rank')} qualified={m.get('qualified')} score={m.get('member_score')} "
                    f"trades={m.get('trade_count')} exp={m.get('expectancy_pct')} pf={m.get('profit_factor')} "
                    f"hash={str(m.get('member_hash'))[:12]}"
                )
    print()
    print(f"run_dir: {PIPELINE_ROOT / run_id}")


def run_small(tickers: list[str]) -> dict[str, Any]:
    run_id = str(uuid4())
    started = time.time()
    rows: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for ticker in tickers:
        result = process_ticker(
            ticker,
            run_id,
            run_full_training=True,
            full_training_fn=_smoke_full_training,
        )
        rows.append(_row_from_result(result))
        results.append(result)

    elapsed = time.time() - started
    payload = {
        "run_id": run_id,
        "tickers": tickers,
        "ga_config": "SMOKE_FULL_TRAINING_GA_CONFIG(pop20xgen15)",
        "elapsed_sec": elapsed,
        "rows": rows,
    }
    summary_path = PIPELINE_ROOT / run_id / "full_training_small_summary.json"
    write_json(summary_path, payload)
    _print_rows(rows, run_id, elapsed)
    print(f"summary: {summary_path}")
    return payload


def main(argv: list[str] | None = None) -> int:
    tickers = [x.strip().upper() for x in (argv or []) if x.strip()] or DEFAULT_TICKERS
    run_small(tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
