#!/usr/bin/env python3
"""Run small screening smoke test for the staged pipeline."""
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

from engine.pipeline.screening import run_screening  # noqa: E402

DEFAULT_TICKERS = ["MSFT", "AAPL", "GE", "BIS", "NVDA"]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if str(k) != "_context"}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return _json_safe({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
    return str(value)


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _summary_row(ticker: str, result: dict[str, Any], elapsed_sec: float, saved_path: Path) -> dict[str, Any]:
    data = result.get("data", {}) or {}
    sentiment = result.get("sentiment", {}) or {}
    viability = result.get("viability", {}) or {}
    return {
        "ticker": ticker,
        "elapsed_sec": elapsed_sec,
        "status": result.get("status"),
        "passed": result.get("passed"),
        "reason_code": result.get("reason_code"),
        "adv_usd_252d": result.get("adv_usd_252d"),
        "liquidity_weight": result.get("liquidity_weight"),
        "rows": data.get("rows"),
        "data_start": data.get("data_start"),
        "data_end": data.get("data_end"),
        "split_count": data.get("split_count"),
        "valid_close_ratio": data.get("valid_close_ratio"),
        "valid_volume_ratio": data.get("valid_volume_ratio"),
        "sentiment_days": sentiment.get("sentiment_days"),
        "viability_executed": viability.get("executed"),
        "viability_trade_count": viability.get("trade_count"),
        "viability_expectancy_pct": viability.get("expectancy_pct"),
        "viability_profit_factor": viability.get("profit_factor"),
        "saved_path": str(saved_path),
    }


def _print_summary(rows: list[dict[str, Any]], elapsed_sec: float, run_id: str) -> None:
    print("=" * 130)
    print("Small screening smoke test")
    print("=" * 130)
    print(f"run_id:      {run_id}")
    print(f"elapsed_sec: {elapsed_sec:.2f}")
    print()
    header = (
        "ticker | passed | reason | sec | ADV | rows | splits | sent | "
        "viability | trades | exp | pf"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['ticker']:6s} | "
            f"{str(row.get('passed')):6s} | "
            f"{str(row.get('reason_code') or ''):18s} | "
            f"{float(row.get('elapsed_sec') or 0.0):5.2f} | "
            f"{float(row.get('adv_usd_252d') or 0.0):,.0f} | "
            f"{int(row.get('rows') or 0):4d} | "
            f"{int(row.get('split_count') or 0):6d} | "
            f"{int(row.get('sentiment_days') or 0):4d} | "
            f"{str(row.get('viability_executed')):9s} | "
            f"{str(row.get('viability_trade_count')):6s} | "
            f"{'' if row.get('viability_expectancy_pct') is None else round(float(row.get('viability_expectancy_pct')), 3)} | "
            f"{'' if row.get('viability_profit_factor') is None else round(float(row.get('viability_profit_factor')), 3)}"
        )


def run_small(tickers: list[str]) -> dict[str, Any]:
    run_id = str(uuid4())
    started = time.time()
    rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {}

    for ticker in tickers:
        t0 = time.time()
        result = run_screening(ticker)
        elapsed = time.time() - t0
        out_path = ROOT / "data/_system/pipeline/v1/runs" / run_id / ticker / "screening.json"
        _save_json(out_path, result)
        rows.append(_summary_row(ticker, result, elapsed, out_path))
        results[ticker] = result

    elapsed = time.time() - started
    summary = {
        "run_id": run_id,
        "tickers": tickers,
        "elapsed_sec": elapsed,
        "rows": rows,
    }
    summary_path = ROOT / "data/_system/pipeline/v1/runs" / run_id / "screening_small_summary.json"
    _save_json(summary_path, summary)
    _print_summary(rows, elapsed, run_id)
    print()
    print(f"summary: {summary_path}")
    return summary


def main(argv: list[str]) -> int:
    tickers = [x.strip().upper() for x in argv if x.strip()] or DEFAULT_TICKERS
    run_small(tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
