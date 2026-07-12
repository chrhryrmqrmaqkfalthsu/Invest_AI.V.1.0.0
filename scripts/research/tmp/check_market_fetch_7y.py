#!/usr/bin/env python3
"""Fetch-only probe for Yahoo 7-year market data coverage.

This script calls only engine.market.context._fetch_index(), prints results to
stdout, and never writes market data or CSV files.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.market.context import _fetch_index

SYMBOLS = ["^GSPC", "^VIX", "XLK", "XLF", "XLE", "XLV", "XLY", "XLI"]
PERIOD = "7y"
MIN_ROWS_FOR_7Y = 1700


def summarize(symbol: str) -> dict[str, object]:
    try:
        frame = _fetch_index(symbol, period=PERIOD)
        if frame is None or frame.empty:
            return {
                "symbol": symbol,
                "success": False,
                "rows": 0,
                "first_date": None,
                "last_date": None,
                "coverage_years": 0.0,
                "covers_7y_by_row_threshold": False,
                "error": "EMPTY_OR_NONE",
            }

        index = pd.to_datetime(frame.index, errors="coerce")
        index = index[~index.isna()]
        first = index.min()
        last = index.max()
        coverage_years = (
            float((last - first).days) / 365.2425
            if first is not pd.NaT and last is not pd.NaT
            else 0.0
        )
        rows = int(len(frame))
        return {
            "symbol": symbol,
            "success": True,
            "rows": rows,
            "first_date": first.strftime("%Y-%m-%d") if first is not pd.NaT else None,
            "last_date": last.strftime("%Y-%m-%d") if last is not pd.NaT else None,
            "coverage_years": round(coverage_years, 4),
            "covers_7y_by_row_threshold": rows >= MIN_ROWS_FOR_7Y,
            "error": None,
        }
    except Exception as exc:
        return {
            "symbol": symbol,
            "success": False,
            "rows": 0,
            "first_date": None,
            "last_date": None,
            "coverage_years": 0.0,
            "covers_7y_by_row_threshold": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    results: list[dict[str, object]] = []
    for index, symbol in enumerate(SYMBOLS):
        result = summarize(symbol)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if index < len(SYMBOLS) - 1:
            time.sleep(1.0)

    all_success = all(bool(item["success"]) for item in results)
    all_cover = all(bool(item["covers_7y_by_row_threshold"]) for item in results)
    print(
        json.dumps(
            {
                "summary": {
                    "symbols": len(results),
                    "all_success": all_success,
                    "all_cover_7y_by_row_threshold": all_cover,
                    "min_rows_for_7y": MIN_ROWS_FOR_7Y,
                    "period": PERIOD,
                }
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
