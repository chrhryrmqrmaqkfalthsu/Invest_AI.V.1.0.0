#!/usr/bin/env python3
"""Finalize full replay universe, bias comparison, and CRS reference result."""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.adapters.factory import get_adapter
from engine.central.entity_loader import EntityRecord
from engine.central.signal_collector import CacheOnlyDataProvider, SignalCollector
from engine.live.elite_shadow_report import build_elite_shadow_report

OUT = Path(__file__).resolve().parent
PRIOR = ROOT / "data/_system/analysis/entry_filter_2d3pct_20260712"
CORE_PATH = PRIOR / "run_entry_filter_2d3pct.py"

spec = importlib.util.spec_from_file_location("entry_filter_2d3pct_core_final", CORE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("failed to load prior GA core")
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)

START = pd.Timestamp("2021-01-01")
END = pd.Timestamp("2026-07-02")
FIELDS = [
    "candidate_id", "stage", "ticker", "rulebook_hash", "signal_date", "regime",
    "label_status", "replay_should_buy", "replay_score", "replay_raw_score",
    "replay_threshold", "replay_strength", "replay_market_adjustment", "replay_price",
    "replay_components", "replay_reasons", "signal_price", "future_high_1",
    "future_high_2", "future_max_high", "forward_max_return_pct", "label_2d3pct",
    "high5", "low5", "close_d1", *core.NUMERIC_FEATURES, core.BINARY_FEATURE,
]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            encoded = {}
            for key in fields:
                value = row.get(key)
                encoded[key] = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else value
            writer.writerow(encoded)


def entity_from_candidate(candidate: dict[str, Any]) -> EntityRecord:
    return EntityRecord(
        entity_id=str(candidate["candidate_id"]), ticker=str(candidate["ticker"]),
        rulebook=dict(candidate["rulebook"]), rulebook_hash=str(candidate["rulebook_hash"]),
        validation_metrics={}, validation_periods=[], tags={},
        confidence=float(candidate.get("fitness") or 0.0),
        source_path=str(candidate.get("source_file") or ""),
    )


def full_universe() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    candidates = list(report.get("candidates") or [])
    provider = CacheOnlyDataProvider(
        cache_roots=[ROOT / "data/_system/research", ROOT / "exp_batch_stage123_2009_20260616_full"],
        recompute_indicators=True,
    )
    collector = SignalCollector(provider, use_llm_events=False)
    rows: list[dict[str, Any]] = []
    unlabeled: list[dict[str, Any]] = []
    for candidate in candidates:
        entity = entity_from_candidate(candidate)
        df = core.normalize_ohlcv(provider.load_price_df(entity.ticker))
        days = [pd.Timestamp(v).normalize() for v in df.index if START <= pd.Timestamp(v).normalize() <= END]
        for day in days:
            snap = collector.signal_for_date(entity, day)
            if snap is None or not snap.should_buy:
                continue
            labeled = core.build_labeled_row(candidate, df, snap.date)
            if labeled is not None:
                row = labeled
                status = "LABELED"
            else:
                features = core.extract_features(df, snap.date)
                row = {
                    "candidate_id": candidate["candidate_id"],
                    "stage": candidate["stage"],
                    "ticker": candidate["ticker"],
                    "rulebook_hash": candidate["rulebook_hash"],
                    "signal_date": snap.date,
                    "regime": core.regime_for_date(snap.date),
                    "signal_price": snap.price,
                }
                if features:
                    row.update(features)
                    status = "UNLABELED_FUTURE_2_NOT_AVAILABLE"
                else:
                    status = "UNLABELED_FEATURE_OR_HISTORY_UNAVAILABLE"
            row.update(
                {
                    "label_status": status,
                    "replay_should_buy": True,
                    "replay_score": float(snap.score),
                    "replay_raw_score": float(snap.raw_score),
                    "replay_threshold": float(snap.threshold),
                    "replay_strength": float(snap.strength),
                    "replay_market_adjustment": float(snap.market_adjustment),
                    "replay_price": float(snap.price),
                    "replay_components": dict(snap.components),
                    "replay_reasons": list(snap.reasons),
                }
            )
            rows.append(row)
            if status != "LABELED":
                unlabeled.append(row)
    rows.sort(key=lambda row: (row["candidate_id"], row["signal_date"]))
    unlabeled.sort(key=lambda row: (row["candidate_id"], row["signal_date"]))
    return rows, unlabeled


def rebuild_crs() -> dict[str, Any]:
    universe = pd.read_csv(OUT / "replay_signal_universe.csv")
    crs_id = "stage3:CRS:8695c9ce3320"
    group = universe[(universe["candidate_id"] == crs_id) & (universe["label_status"] == "LABELED")].copy()
    result: dict[str, Any] = {
        "candidate_id": crs_id,
        "signal_time_et": "2026-07-09T13:20:33.590054-04:00",
        "forensic_signal_price": 600.8599853515625,
        "feature_boundary": "sessions strictly before 2026-07-09",
        "actual_label_status": "NOT_STORED_SECOND_SESSION_NOT_COMPLETE_AS_OF_2026-07-12",
        "universe_contract": "current rulebook re-evaluated with current SignalCollector; not original log reconstruction",
    }
    champion, quantiles, _ = core.train_entity(group, crs_id)
    survivor_rows = list(csv.DictReader((OUT / "survivor_summary.csv").open(encoding="utf-8")))
    entity_row = next((row for row in survivor_rows if row["candidate_id"] == crs_id), None)
    df = core.normalize_ohlcv(get_adapter("CRS").load_history(years=7))
    features = core.extract_features(df, "2026-07-09")
    if champion is None or quantiles is None or features is None:
        result.update({"gene_available": champion is not None, "selector_pass": None, "status": "UNRECOVERABLE"})
        return result
    X = np.array([[features[name] for name in core.NUMERIC_FEATURES]], dtype=float)
    turn = np.array([features[core.BINARY_FEATURE]], dtype=int)
    result.update(features)
    result.update(
        {
            "gene_available": True,
            "selector_pass": bool(core.individual_mask(champion, X, turn, quantiles)[0]),
            "survivor_entity": bool(entity_row and entity_row.get("survivor", "").lower() == "true"),
            "gene": core.gene_dict(champion, quantiles),
            "status": "RECOVERED_SELECTOR_DECISION",
        }
    )
    return result


def bias_comparison() -> list[dict[str, Any]]:
    prior = list(csv.DictReader((PRIOR / "label_distribution.csv").open(encoding="utf-8")))
    current = list(csv.DictReader((OUT / "label_distribution.csv").open(encoding="utf-8")))
    rows: list[dict[str, Any]] = []
    for regime in ["stress", "train", "oos", "all"]:
        p = next(row for row in prior if row["scope"] == "ALL" and row["regime"] == regime)
        c = next(row for row in current if row["scope"] == "ALL" and row["regime"] == regime)
        p_count, c_count = int(p["signal_count"]), int(c["signal_count"])
        p_rate, c_rate = float(p["positive_rate"]), float(c["positive_rate"])
        rows.append(
            {
                "regime": regime,
                "log_based_signal_count": p_count,
                "replay_labeled_signal_count": c_count,
                "signal_count_delta": c_count - p_count,
                "signal_count_ratio": c_count / p_count if p_count else None,
                "log_based_positive_rate": p_rate,
                "replay_positive_rate": c_rate,
                "positive_rate_delta_pp": 100.0 * (c_rate - p_rate),
                "interpretation": "replay removes position/entry-log selection but uses current evaluator/context, so delta is not pure causal bias estimate",
            }
        )
    return rows


def main() -> int:
    rows, unlabeled = full_universe()
    write_csv(OUT / "replay_signal_universe.csv", rows, FIELDS)
    write_csv(OUT / "unlabeled_replay_signals.csv", unlabeled, FIELDS)
    write_csv(OUT / "crs_filter_result.csv", [rebuild_crs()])
    write_csv(OUT / "bias_comparison.csv", bias_comparison())

    summary_path = OUT / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["replay_should_buy_count"] = len(rows)
    summary["labeled_signal_count"] = sum(row.get("label_status") == "LABELED" for row in rows)
    summary["unlabeled_signal_count"] = len(unlabeled)
    summary["unlabeled_status_counts"] = {
        status: sum(row.get("label_status") == status for row in unlabeled)
        for status in sorted({str(row.get("label_status")) for row in unlabeled})
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    comparison_path = OUT / "comparison_to_log_based.csv"
    comparison = list(csv.DictReader(comparison_path.open(encoding="utf-8")))
    comparison.insert(
        0,
        {
            "metric": "full_replay_should_buy_count",
            "log_based": 3430,
            "replay_based": len(rows),
            "delta": len(rows) - 3430,
            "ratio": len(rows) / 3430,
            "log_based_detail": "entry-biased saved log",
            "replay_based_detail": f"labeled={len(rows)-len(unlabeled)}, unlabeled={len(unlabeled)}",
        },
    )
    write_csv(comparison_path, comparison)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
