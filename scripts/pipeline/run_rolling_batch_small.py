#!/usr/bin/env python3
"""Run small multi-ticker rolling validation smoke test.

This script is intentionally resilient: one ticker failure is recorded and the
batch continues. It is a pre-check for the future batch orchestrator.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.rolling_validation import run_rolling_validation  # noqa: E402

# Selected after read-only sentiment/data probe:
# - MSFT, AAPL: high-sentiment mega-cap controls
# - GE: no legacy parameters.json dependency; validates new path can train from scratch
# - BIS: sentiment CSV has 0 rows and limited history; validates no-sentiment / no-OOS exclusion path
# - NVDA: optional known-good baseline from Task AM
DEFAULT_TICKERS = ["MSFT", "AAPL", "GE", "BIS", "NVDA"]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
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


def _summarize_success(ticker: str, result: dict[str, Any], elapsed_sec: float, saved_path: Path) -> dict[str, Any]:
    score = result.get("stock_score", {}) if isinstance(result, dict) else {}
    periods = score.get("periods", []) if isinstance(score, dict) else []
    pass_count = sum(1 for p in periods if p.get("pass"))
    return {
        "ticker": ticker,
        "status": "OK",
        "elapsed_sec": elapsed_sec,
        "run_id": result.get("run_id"),
        "data_start": result.get("data_start"),
        "data_end": result.get("data_end"),
        "sentiment_days": result.get("sentiment_days"),
        "adv_usd_252d": result.get("adv_usd_252d"),
        "period_count": len(result.get("periods", []) or []),
        "pass_count": pass_count,
        "excluded": score.get("excluded"),
        "exclude_reason": score.get("exclude_reason"),
        "consistency_score": score.get("consistency_score"),
        "quality_score": score.get("quality_score"),
        "liquidity_weight": score.get("liquidity_weight"),
        "stock_score": score.get("stock_score"),
        "saved_path": str(saved_path),
    }


def _summarize_error(ticker: str, error: BaseException, elapsed_sec: float, saved_path: Path) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "status": "ERROR",
        "elapsed_sec": elapsed_sec,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "saved_path": str(saved_path),
    }


def _print_summary(rows: list[dict[str, Any]], batch_elapsed_sec: float, batch_run_id: str) -> None:
    print("=" * 120)
    print("Small rolling validation batch smoke test")
    print("=" * 120)
    print(f"batch_run_id: {batch_run_id}")
    print(f"elapsed_sec:  {batch_elapsed_sec:.2f}")
    print()
    header = (
        "ticker | status | sec | sentiment | ADV | periods | pass | excluded | "
        "consistency | quality | liq | stock_score | error"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        if row.get("status") == "OK":
            print(
                f"{row['ticker']:6s} | "
                f"{row['status']:6s} | "
                f"{row['elapsed_sec']:6.1f} | "
                f"{str(row.get('sentiment_days')):9s} | "
                f"{float(row.get('adv_usd_252d') or 0.0):,.0f} | "
                f"{int(row.get('period_count') or 0):7d} | "
                f"{int(row.get('pass_count') or 0):4d} | "
                f"{str(row.get('excluded')):8s} | "
                f"{str(row.get('consistency_score')):11s} | "
                f"{str(row.get('quality_score')):7s} | "
                f"{str(row.get('liquidity_weight')):3s} | "
                f"{str(row.get('stock_score')):11s} | "
                ""
            )
        else:
            print(
                f"{row['ticker']:6s} | "
                f"{row['status']:6s} | "
                f"{row['elapsed_sec']:6.1f} | "
                f"{'':9s} | {'':>3s} | {'':7s} | {'':4s} | {'':8s} | {'':11s} | {'':7s} | {'':3s} | {'':11s} | "
                f"{row.get('error_type')}: {row.get('error_message')}"
            )
    print()
    print("Saved files:")
    for row in rows:
        print(f"  {row['ticker']}: {row.get('saved_path')}")


def run_batch(tickers: list[str]) -> dict[str, Any]:
    batch_run_id = str(uuid4())
    batch_started = time.time()
    rows: list[dict[str, Any]] = []

    for ticker in tickers:
        started = time.time()
        try:
            result = run_rolling_validation(ticker)
            elapsed = time.time() - started
            run_id = result.get("run_id") or result.get("_meta", {}).get("run_id") or batch_run_id
            out_path = ROOT / "data/_system/pipeline/v1/runs" / str(run_id) / ticker / "rolling_validation.json"
            _save_json(out_path, result)
            rows.append(_summarize_success(ticker, result, elapsed, out_path))
        except Exception as exc:
            elapsed = time.time() - started
            error_payload = {
                "ticker": ticker,
                "batch_run_id": batch_run_id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
            out_path = ROOT / "data/_system/pipeline/v1/runs" / batch_run_id / ticker / "rolling_validation_error.json"
            _save_json(out_path, error_payload)
            rows.append(_summarize_error(ticker, exc, elapsed, out_path))

    batch_elapsed = time.time() - batch_started
    batch_summary = {
        "batch_run_id": batch_run_id,
        "tickers": tickers,
        "elapsed_sec": batch_elapsed,
        "rows": rows,
    }
    summary_path = ROOT / "data/_system/pipeline/v1/runs" / batch_run_id / "small_batch_summary.json"
    _save_json(summary_path, batch_summary)
    _print_summary(rows, batch_elapsed, batch_run_id)
    print(f"batch_summary: {summary_path}")
    return batch_summary


def main(argv: list[str]) -> int:
    tickers = [t.strip().upper() for t in argv if t.strip()] or DEFAULT_TICKERS
    run_batch(tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
