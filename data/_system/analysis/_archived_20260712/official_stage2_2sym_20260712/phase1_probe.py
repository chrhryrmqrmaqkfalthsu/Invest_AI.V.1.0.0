#!/usr/bin/env python3
"""Phase 1 connection probe for official Stage 2.

Requires the sibling _runtime/sitecustomize.py on PYTHONPATH. The probe calls
prepare_ticker_context() but runs no GA or training. Results are printed as JSON
to stdout only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.feature_lag import lagged_date_key, lookup_market_at_lagged
from engine.pipeline.context import prepare_ticker_context

ROOT = Path(__file__).resolve().parents[4]
MARKET_PATH = ROOT / "data/_system/market_history.csv"
EXPECTED_SHA256 = "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38"
TICKERS = ["AAP", "POWI"]
ANCHORS = [
    pd.Timestamp("2021-06-15"),
    pd.Timestamp("2022-12-15"),
    pd.Timestamp("2023-12-15"),
    pd.Timestamp("2024-12-16"),
    pd.Timestamp("2025-12-15"),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def choose_trade_date(index: pd.DatetimeIndex, anchor: pd.Timestamp) -> pd.Timestamp:
    normalized = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce")).tz_localize(None).normalize()
    valid = normalized[~normalized.isna()]
    future = valid[valid >= anchor]
    if len(future):
        return pd.Timestamp(future[0])
    return pd.Timestamp(valid[-1])


def selected_market_date(history: pd.DataFrame, cutoff_key: str) -> pd.Timestamp | None:
    cutoff = pd.Timestamp(cutoff_key)
    index = pd.DatetimeIndex(pd.to_datetime(history.index, errors="coerce")).tz_localize(None).normalize()
    pos = index.searchsorted(cutoff, side="right") - 1
    if pos < 0:
        return None
    return pd.Timestamp(index[pos])


def main() -> None:
    sha_before = sha256(MARKET_PATH)
    results: dict[str, Any] = {
        "market_path": str(MARKET_PATH),
        "market_sha_before": sha_before,
        "expected_market_sha": EXPECTED_SHA256,
        "tickers": {},
    }

    for ticker in TICKERS:
        context = prepare_ticker_context(ticker)
        history = context.get("market_history_df")
        frame = context.get("df")
        if not isinstance(history, pd.DataFrame):
            raise RuntimeError(f"{ticker}: market_history_df is not a DataFrame")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise RuntimeError(f"{ticker}: ticker dataframe is empty")

        history = history.copy()
        history.index = pd.DatetimeIndex(pd.to_datetime(history.index, errors="coerce")).tz_localize(None).normalize()
        history = history[~history.index.isna()].sort_index()
        sector_name = str(context.get("sector_name") or "")
        sector_column = f"sector_{sector_name}"

        lookups: list[dict[str, Any]] = []
        ticker_index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce")).tz_localize(None).normalize()
        for anchor in ANCHORS:
            trade_date = choose_trade_date(ticker_index, anchor)
            cutoff_key = lagged_date_key(trade_date, 1)
            source_date = selected_market_date(history, cutoff_key)
            row = lookup_market_at_lagged(history, trade_date, lag_days=1)
            score = float(row.get("score", 50.0))
            sector = float(row.get(sector_column, 50.0))
            vix = float(row.get("vix", 18.0))
            lookups.append(
                {
                    "anchor": anchor.strftime("%Y-%m-%d"),
                    "trade_date": trade_date.strftime("%Y-%m-%d"),
                    "cutoff_date": cutoff_key,
                    "selected_market_date": source_date.strftime("%Y-%m-%d") if source_date is not None else None,
                    "score": score,
                    "sector_column": sector_column,
                    "sector_value": sector,
                    "vix": vix,
                    "is_exact_default_triplet": score == 50.0 and sector == 50.0 and vix == 18.0,
                    "d1_safe": source_date is not None and source_date <= pd.Timestamp(cutoff_key) < trade_date,
                }
            )

        score_unique = len({round(item["score"], 10) for item in lookups})
        sector_unique = len({round(item["sector_value"], 10) for item in lookups})
        vix_unique = len({round(item["vix"], 10) for item in lookups})
        gates = {
            "market_history_not_none": history is not None,
            "market_history_rows_1759": len(history) == 1759,
            "market_history_period_valid": history.index.min() <= pd.Timestamp("2019-07-31") and history.index.max() >= pd.Timestamp("2026-07-01"),
            "sector_mapping_nonempty": bool(sector_name),
            "sector_column_present": sector_column in history.columns,
            "five_lookups_present": len(lookups) == 5,
            "lookup_not_dead_default": not all(item["is_exact_default_triplet"] for item in lookups),
            "score_varies": score_unique >= 2,
            "sector_varies": sector_unique >= 2,
            "vix_varies": vix_unique >= 2,
            "d1_cutoff_all_safe": all(item["d1_safe"] for item in lookups),
        }
        results["tickers"][ticker] = {
            "company_name": str(getattr(context.get("meta"), "name", "")),
            "ticker_rows": int(len(frame)),
            "ticker_first_date": ticker_index.min().strftime("%Y-%m-%d"),
            "ticker_last_date": ticker_index.max().strftime("%Y-%m-%d"),
            "market_rows": int(len(history)),
            "market_first_date": history.index.min().strftime("%Y-%m-%d"),
            "market_last_date": history.index.max().strftime("%Y-%m-%d"),
            "market_column_count": int(len(history.columns)),
            "market_columns": history.columns.tolist(),
            "sector_name": sector_name,
            "sector_column": sector_column,
            "lookups": lookups,
            "unique_values": {
                "score": score_unique,
                "sector": sector_unique,
                "vix": vix_unique,
            },
            "gates": gates,
            "gate_pass": all(gates.values()),
        }

    sha_after = sha256(MARKET_PATH)
    results["market_sha_after"] = sha_after
    results["market_sha_unchanged"] = sha_before == sha_after == EXPECTED_SHA256
    results["phase1_pass"] = bool(
        results["market_sha_unchanged"]
        and all(item["gate_pass"] for item in results["tickers"].values())
    )
    print(json.dumps(json_safe(results), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
