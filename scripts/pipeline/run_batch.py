#!/usr/bin/env python3
"""CLI for the staged pipeline batch orchestrator."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.batch import run_batch  # noqa: E402

DEFAULT_TICKERS = [
    # Mega/large-cap controls
    "MSFT", "AAPL", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
    # Financials / industrials / energy / defensives
    "JPM", "BAC", "GE", "CAT", "BA", "XOM", "KO", "WMT",
    # Mixed maturity / lower score candidates
    "PFE", "DIS", "INTC", "AMD", "IBM", "T", "F", "GM",
    # Edge case from Task AN/AP: sentiment 0 rows and ADV below threshold
    "BIS",
]


def parse_tickers(value: str | None) -> list[str]:
    if not value:
        return DEFAULT_TICKERS
    path = Path(value)
    if path.exists() and path.is_file():
        text = path.read_text(encoding="utf-8")
        raw = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw.extend(line.replace("\t", ",").split(","))
    else:
        raw = value.split(",")
    out = []
    seen = set()
    for item in raw:
        ticker = item.strip().upper()
        if ticker and ticker not in seen:
            out.append(ticker)
            seen.add(ticker)
    return out


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def print_summary(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) or {}
    dist = summary.get("stock_score_distribution", {}) or {}
    print("=" * 120)
    print("Pipeline batch summary")
    print("=" * 120)
    print(f"run_id:          {payload.get('run_id')}")
    print(f"elapsed_sec:     {_fmt(payload.get('elapsed_sec'), 2)}")
    print(f"max_workers:     {payload.get('max_workers')}")
    print(f"progress_path:   {payload.get('progress_path')}")
    print()
    print("Counts")
    print(f"  total:         {summary.get('total')}")
    print(f"  rolling_done:  {summary.get('rolling_done_count')}")
    print(f"  screened_out:  {summary.get('screened_out_count')}")
    print(f"  errors:        {summary.get('error_count')}")
    print(f"  status_counts: {summary.get('status_counts')}")
    print(f"  reasons:       {summary.get('screening_reason_counts')}")
    print()
    print("Stock score distribution")
    for key in ("count", "min", "p10", "p25", "p50", "p75", "p90", "max", "zero_score_count", "excluded_count"):
        print(f"  {key:16s}: {dist.get(key)}")
    print()
    print("ticker | status | reason | screen | score | pass | excluded | sec")
    print("-" * 78)
    for row in payload.get("results", []):
        print(
            f"{str(row.get('ticker')):6s} | "
            f"{str(row.get('final_status')):12s} | "
            f"{str(row.get('reason_code') or row.get('screening_reason_code') or ''):18s} | "
            f"{str(row.get('screening_status') or ''):6s} | "
            f"{_fmt(row.get('stock_score'), 3):>7s} | "
            f"{str(row.get('pass_count') or ''):4s} | "
            f"{str(row.get('excluded') or ''):8s} | "
            f"{_fmt(row.get('elapsed_sec'), 1):>5s}"
        )
    print()
    print(f"summary_path: {ROOT / 'data/_system/pipeline/v1/runs' / str(payload.get('run_id')) / 'batch_summary.json'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run staged pipeline batch: screening -> rolling.")
    parser.add_argument("--tickers", help="Comma-separated tickers or a file path. Defaults to a 24-ticker smoke set.")
    parser.add_argument("--run-id", help="Existing run id for resume, or explicit run id for a new run.")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--no-resume", action="store_true", help="Start from scratch instead of skipping terminal rows.")
    parser.add_argument("--full-training", action="store_true", help="Reserved hook. Current implementation does not run full training.")
    args = parser.parse_args(argv)

    tickers = parse_tickers(args.tickers)
    payload = run_batch(
        tickers,
        run_id=args.run_id,
        max_workers=args.max_workers,
        resume=not args.no_resume,
        run_full_training=args.full_training,
    )
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
