#!/usr/bin/env python3
"""Replay-based 5-day GA entry-filter R&D.

This is a research-only pipeline. It re-evaluates the currently eligible
rulebooks over historical dates with the current SignalCollector contract,
collects every independent should_buy=True date, attaches D-1-only path
features and a D+1/D+2 +3% binary label, and trains one GA filter per entity.

This universe is NOT a reconstruction of the original historical signal log.
It is a new historical re-evaluation of current rulebooks with the current
SignalCollector/evaluator/context implementation.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.central.entity_loader import EntityRecord
from engine.central.signal_collector import CacheOnlyDataProvider, SignalCollector
from engine.live.elite_shadow_report import build_elite_shadow_report

OUT = Path(__file__).resolve().parent
PRIOR_DIR = ROOT / "data/_system/analysis/entry_filter_2d3pct_20260712"
CORE_PATH = PRIOR_DIR / "run_entry_filter_2d3pct.py"

spec = importlib.util.spec_from_file_location("entry_filter_2d3pct_log_core", CORE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"failed to import prior GA core: {CORE_PATH}")
core = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = core
spec.loader.exec_module(core)

START = pd.Timestamp("2021-01-01")
END = pd.Timestamp("2026-07-02")
UNIVERSE_FIELDS = [
    "candidate_id", "stage", "ticker", "rulebook_hash", "signal_date", "regime",
    "replay_should_buy", "replay_score", "replay_raw_score", "replay_threshold",
    "replay_strength", "replay_market_adjustment", "replay_price",
    "replay_components", "replay_reasons",
    "signal_price", "future_high_1", "future_high_2", "future_max_high",
    "forward_max_return_pct", "label_2d3pct", "high5", "low5", "close_d1",
    *core.NUMERIC_FEATURES, core.BINARY_FEATURE,
]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out: dict[str, Any] = {}
            for key in fields:
                value = row.get(key)
                if isinstance(value, (dict, list, tuple)):
                    out[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    out[key] = value
            writer.writerow(out)


def make_entity(candidate: dict[str, Any]) -> EntityRecord:
    return EntityRecord(
        entity_id=str(candidate["candidate_id"]),
        ticker=str(candidate["ticker"]),
        rulebook=dict(candidate["rulebook"]),
        rulebook_hash=str(candidate["rulebook_hash"]),
        validation_metrics={},
        validation_periods=[],
        tags={},
        confidence=float(candidate.get("fitness") or 0.0),
        source_path=str(candidate.get("source_file") or ""),
    )


def build_universe() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, pd.DataFrame], list[dict[str, Any]]]:
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    candidates = list(report.get("candidates") or [])
    entities = [make_entity(candidate) for candidate in candidates]
    provider = CacheOnlyDataProvider(
        cache_roots=[
            ROOT / "data/_system/research",
            ROOT / "exp_batch_stage123_2009_20260616_full",
        ],
        recompute_indicators=True,
    )
    collector = SignalCollector(provider, use_llm_events=False)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    contexts: dict[str, pd.DataFrame] = {}

    for candidate, entity in zip(candidates, entities):
        try:
            df = core.normalize_ohlcv(provider.load_price_df(entity.ticker))
            contexts[entity.ticker] = df
        except Exception as exc:
            errors.append({"candidate_id": entity.entity_id, "ticker": entity.ticker, "error": f"history:{exc}"})
            continue
        days = [
            pd.Timestamp(value).normalize()
            for value in df.index
            if START <= pd.Timestamp(value).normalize() <= END
        ]
        for day in days:
            try:
                snapshot = collector.signal_for_date(entity, day)
            except Exception as exc:
                errors.append({
                    "candidate_id": entity.entity_id,
                    "ticker": entity.ticker,
                    "signal_date": day.strftime("%Y-%m-%d"),
                    "error": f"signal:{exc}",
                })
                continue
            if snapshot is None or not snapshot.should_buy:
                continue
            labeled = core.build_labeled_row(candidate, df, snapshot.date)
            if labeled is None:
                continue
            labeled.update(
                {
                    "replay_should_buy": True,
                    "replay_score": float(snapshot.score),
                    "replay_raw_score": float(snapshot.raw_score),
                    "replay_threshold": float(snapshot.threshold),
                    "replay_strength": float(snapshot.strength),
                    "replay_market_adjustment": float(snapshot.market_adjustment),
                    "replay_price": float(snapshot.price),
                    "replay_components": dict(snapshot.components),
                    "replay_reasons": list(snapshot.reasons),
                }
            )
            rows.append(labeled)

    rows.sort(key=lambda row: (row["candidate_id"], row["signal_date"]))
    return rows, errors, contexts, candidates


def distribution_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scopes = [("ALL", "", "", frame)] + [
        ("ENTITY", candidate_id, str(group.iloc[0]["ticker"]), group)
        for candidate_id, group in frame.groupby("candidate_id")
    ]
    for scope, candidate_id, ticker, group in scopes:
        for regime in ["stress", "train", "oos", "all"]:
            part = group if regime == "all" else group[group["regime"] == regime]
            n = len(part)
            positive = int(part["label_2d3pct"].sum()) if n else 0
            rows.append(
                {
                    "scope": scope,
                    "candidate_id": candidate_id,
                    "ticker": ticker,
                    "regime": regime,
                    "signal_count": n,
                    "positive_count": positive,
                    "positive_rate": positive / n if n else 0.0,
                    "mean_forward_max_return_pct": float(part["forward_max_return_pct"].mean()) if n else None,
                    "median_forward_max_return_pct": float(part["forward_max_return_pct"].median()) if n else None,
                    "mean_replay_strength": float(part["replay_strength"].mean()) if n else None,
                }
            )
    return rows


def train_all(frame: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, tuple[dict[str, Any], np.ndarray, Any]], list[dict[str, Any]]]:
    training_log: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    survivor_rows: list[dict[str, Any]] = []
    registry: dict[str, tuple[dict[str, Any], np.ndarray, Any]] = {}
    passed_signals: list[dict[str, Any]] = []

    for candidate_id, group in frame.groupby("candidate_id"):
        group = group.sort_values("signal_date")
        ticker = str(group.iloc[0]["ticker"])
        stage = str(group.iloc[0]["stage"])
        champion, quantiles, history = core.train_entity(group, candidate_id)
        training_log.extend({**row, "ticker": ticker, "stage": stage} for row in history)
        if champion is None or quantiles is None:
            survivor_rows.append(
                {
                    "candidate_id": candidate_id,
                    "ticker": ticker,
                    "stage": stage,
                    "survivor": False,
                    "status": "INSUFFICIENT_TRAIN_SIGNALS",
                    "stress_signal_count": int((group["regime"] == "stress").sum()),
                    "train_signal_count": int((group["regime"] == "train").sum()),
                    "oos_signal_count": int((group["regime"] == "oos").sum()),
                }
            )
            continue

        metrics: dict[str, dict[str, Any]] = {}
        for regime in ["stress", "train", "oos"]:
            part = group[group["regime"] == regime]
            X = part[core.NUMERIC_FEATURES].to_numpy(float)
            turn = part[core.BINARY_FEATURE].to_numpy(int)
            y = part["label_2d3pct"].to_numpy(int)
            mask = core.individual_mask(champion, X, turn, quantiles) if len(part) else np.zeros(0, bool)
            metrics[regime] = core.metric_dict(y, mask)
            for mode, mode_mask in [("baseline", np.ones(len(part), bool)), ("filtered", mask)]:
                metric_rows.append(
                    {
                        "candidate_id": candidate_id,
                        "ticker": ticker,
                        "stage": stage,
                        "regime": regime,
                        "mode": mode,
                        **core.metric_dict(y, mode_mask),
                    }
                )
            for _, selected in part.loc[mask].iterrows():
                passed_signals.append(
                    {
                        "candidate_id": candidate_id,
                        "ticker": ticker,
                        "stage": stage,
                        "regime": regime,
                        "signal_date": selected["signal_date"],
                        "label_2d3pct": int(selected["label_2d3pct"]),
                        "forward_max_return_pct": float(selected["forward_max_return_pct"]),
                    }
                )

        train_metrics = metrics["train"]
        stress_ok, stress_reasons = core.validation_pass(metrics["stress"], float(train_metrics["precision"]))
        oos_ok, oos_reasons = core.validation_pass(metrics["oos"], float(train_metrics["precision"]))
        train_reasons: list[str] = []
        train_floor = max(0.50, float(train_metrics["base_rate"]) + 0.10)
        train_min = core.train_min_pass(int(train_metrics["signal_count"]))
        if int(train_metrics["passed_count"]) < train_min:
            train_reasons.append(f"passed_count_lt_{train_min}")
        if float(train_metrics["precision"]) < train_floor:
            train_reasons.append(f"precision_lt_{train_floor:.4f}")
        survivor = not train_reasons and stress_ok and oos_ok
        row: dict[str, Any] = {
            "candidate_id": candidate_id,
            "ticker": ticker,
            "stage": stage,
            "survivor": survivor,
            "status": "SURVIVOR" if survivor else "FAILED_GATE",
            "train_reasons": train_reasons,
            "stress_reasons": stress_reasons,
            "oos_reasons": oos_reasons,
            "train_fitness": float(champion.fitness),
            "gene": core.gene_dict(champion, quantiles),
        }
        for regime in ["stress", "train", "oos"]:
            for key, value in metrics[regime].items():
                row[f"{regime}_{key}"] = value
        row["train_stress_precision_gap"] = float(train_metrics["precision"]) - float(metrics["stress"]["precision"])
        row["train_oos_precision_gap"] = float(train_metrics["precision"]) - float(metrics["oos"]["precision"])
        survivor_rows.append(row)
        registry[candidate_id] = (row, quantiles, champion)

    return training_log, metric_rows, survivor_rows, registry, passed_signals


def overfit_rows(survivor_rows: list[dict[str, Any]], passed_signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in survivor_rows:
        if row.get("status") == "INSUFFICIENT_TRAIN_SIGNALS":
            continue
        selected = [signal for signal in passed_signals if signal["candidate_id"] == row["candidate_id"]]
        positive_returns = sorted((max(0.0, float(signal["forward_max_return_pct"])) for signal in selected), reverse=True)
        total_positive_return = sum(positive_returns)
        top3_share = sum(positive_returns[:3]) / total_positive_return if total_positive_return else 0.0
        out.append(
            {
                "scope": "ENTITY",
                "candidate_id": row["candidate_id"],
                "ticker": row["ticker"],
                "survivor": row["survivor"],
                "train_precision": row.get("train_precision"),
                "stress_precision": row.get("stress_precision"),
                "oos_precision": row.get("oos_precision"),
                "train_stress_precision_gap": row.get("train_stress_precision_gap"),
                "train_oos_precision_gap": row.get("train_oos_precision_gap"),
                "all_passed_count": len(selected),
                "top3_positive_return_share": top3_share,
                "extreme_value_concentration_flag": bool(top3_share > 0.60 and len(selected) >= 5),
            }
        )

    survivor_ids = {str(row["candidate_id"]) for row in survivor_rows if row.get("survivor")}
    pooled = [signal for signal in passed_signals if signal["candidate_id"] in survivor_ids]
    counts = Counter(signal["ticker"] for signal in pooled)
    total = sum(counts.values())
    top_count = max(counts.values()) if counts else 0
    out.append(
        {
            "scope": "SURVIVOR_POOL",
            "survivor": bool(survivor_ids),
            "survivor_entity_count": len(survivor_ids),
            "pooled_passed_count": total,
            "top_ticker_passed_share": top_count / total if total else 0.0,
            "ticker_hhi": sum((count / total) ** 2 for count in counts.values()) if total else 0.0,
            "ticker_concentration_flag": bool(total and top_count / total > 0.25),
        }
    )
    return out


def pooled_metrics(metric_rows: list[dict[str, Any]], survivor_ids: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for regime in ["stress", "train", "oos"]:
        for mode in ["baseline", "filtered"]:
            rows = [
                row for row in metric_rows
                if row["candidate_id"] in survivor_ids and row["regime"] == regime and row["mode"] == mode
            ]
            signal_count = sum(int(row["signal_count"]) for row in rows)
            positive_count = sum(int(row["positive_count"]) for row in rows)
            passed_count = sum(int(row["passed_count"]) for row in rows)
            passed_positive = sum(int(row["passed_positive_count"]) for row in rows)
            baseline = positive_count / signal_count if signal_count else 0.0
            precision = passed_positive / passed_count if passed_count else 0.0
            out.append(
                {
                    "regime": regime,
                    "mode": mode,
                    "survivor_entity_count": len(rows),
                    "signal_count": signal_count,
                    "positive_count": positive_count,
                    "passed_count": passed_count,
                    "passed_positive_count": passed_positive,
                    "precision": precision,
                    "coverage": passed_count / signal_count if signal_count else 0.0,
                    "precision_lift_pp": 100.0 * (precision - baseline),
                }
            )
    return out


def crs_result(registry: dict[str, tuple[dict[str, Any], np.ndarray, Any]], contexts: dict[str, pd.DataFrame]) -> dict[str, Any]:
    candidate_id = "stage3:CRS:8695c9ce3320"
    result: dict[str, Any] = {
        "candidate_id": candidate_id,
        "signal_time_et": "2026-07-09T13:20:33.590054-04:00",
        "forensic_signal_price": 600.8599853515625,
        "feature_boundary": "sessions strictly before 2026-07-09",
        "actual_label_status": "NOT_STORED_SECOND_SESSION_NOT_COMPLETE_AS_OF_2026-07-12",
        "universe_contract": "current rulebook re-evaluated with current SignalCollector; not original log reconstruction",
    }
    registered = registry.get(candidate_id)
    df = contexts.get("CRS")
    features = core.extract_features(df, "2026-07-09") if df is not None else None
    if registered and features:
        row, quantiles, champion = registered
        X = np.array([[features[name] for name in core.NUMERIC_FEATURES]], float)
        turn = np.array([features[core.BINARY_FEATURE]], int)
        result.update(features)
        result.update(
            {
                "gene_available": True,
                "selector_pass": bool(core.individual_mask(champion, X, turn, quantiles)[0]),
                "survivor_entity": bool(row.get("survivor")),
                "gene": row.get("gene"),
                "status": "RECOVERED_SELECTOR_DECISION",
            }
        )
    else:
        result.update({"gene_available": bool(registered), "selector_pass": None, "status": "UNRECOVERABLE"})
    return result


def comparison_rows(summary: dict[str, Any], pooled: list[dict[str, Any]], survivor_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prior_summary = json.loads((PRIOR_DIR / "summary.json").read_text(encoding="utf-8"))
    prior_pooled = list(csv.DictReader((PRIOR_DIR / "pooled_survivor_metrics.csv").open(encoding="utf-8")))
    current_survivors = sorted(row["candidate_id"] for row in survivor_rows if row.get("survivor"))
    rows: list[dict[str, Any]] = [
        {
            "metric": "dataset_signal_count",
            "log_based": prior_summary["dataset_signal_count"],
            "replay_based": summary["dataset_signal_count"],
            "delta": summary["dataset_signal_count"] - prior_summary["dataset_signal_count"],
            "ratio": summary["dataset_signal_count"] / prior_summary["dataset_signal_count"],
        },
        {
            "metric": "survivor_count",
            "log_based": prior_summary["survivor_count"],
            "replay_based": summary["survivor_count"],
            "delta": summary["survivor_count"] - prior_summary["survivor_count"],
            "ratio": None,
            "log_based_detail": prior_summary["survivor_ids"],
            "replay_based_detail": current_survivors,
        },
    ]
    for regime in ["stress", "train", "oos"]:
        prior_row = next(row for row in prior_pooled if row["regime"] == regime and row["mode"] == "filtered")
        current_row = next((row for row in pooled if row["regime"] == regime and row["mode"] == "filtered"), None)
        rows.append(
            {
                "metric": f"{regime}_survivor_pool_filtered_precision",
                "log_based": float(prior_row["precision"]),
                "replay_based": float(current_row["precision"]) if current_row else None,
                "delta": (float(current_row["precision"]) - float(prior_row["precision"])) if current_row else None,
                "ratio": None,
                "log_based_detail": f"passed={prior_row['passed_count']}",
                "replay_based_detail": f"passed={current_row['passed_count']}" if current_row else "no survivors",
            }
        )
    return rows


def main() -> int:
    started = time.time()
    universe, errors, contexts, candidates = build_universe()
    write_csv(OUT / "replay_signal_universe.csv", universe, UNIVERSE_FIELDS)
    if errors:
        write_csv(OUT / "data_errors.csv", errors)
    frame = pd.DataFrame(universe)
    distributions = distribution_rows(frame)
    write_csv(OUT / "label_distribution.csv", distributions)

    training_log, metric_rows, survivor_rows, registry, passed_signals = train_all(frame)
    write_csv(OUT / "training_log.csv", training_log)
    write_csv(OUT / "per_regime_metrics.csv", metric_rows)
    write_csv(OUT / "survivor_summary.csv", survivor_rows)
    with (OUT / "survivors.jsonl").open("w", encoding="utf-8") as fp:
        for row in survivor_rows:
            if row.get("survivor"):
                fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    overfit = overfit_rows(survivor_rows, passed_signals)
    write_csv(OUT / "overfit_check.csv", overfit)
    survivor_ids = {str(row["candidate_id"]) for row in survivor_rows if row.get("survivor")}
    pooled = pooled_metrics(metric_rows, survivor_ids)
    write_csv(OUT / "pooled_survivor_metrics.csv", pooled)
    write_csv(OUT / "crs_filter_result.csv", [crs_result(registry, contexts)])

    summary = {
        "generated_at_unix": time.time(),
        "elapsed_sec": time.time() - started,
        "universe_contract": "current eligible rulebooks historically re-evaluated with current SignalCollector/evaluator/context; not original signal-log reconstruction",
        "use_llm_events": False,
        "universe_entity_count": len(candidates),
        "dataset_signal_count": len(frame),
        "stress_signal_count": int((frame["regime"] == "stress").sum()),
        "train_signal_count": int((frame["regime"] == "train").sum()),
        "oos_signal_count": int((frame["regime"] == "oos").sum()),
        "stress_positive_rate": float(frame.loc[frame["regime"] == "stress", "label_2d3pct"].mean()),
        "train_positive_rate": float(frame.loc[frame["regime"] == "train", "label_2d3pct"].mean()),
        "oos_positive_rate": float(frame.loc[frame["regime"] == "oos", "label_2d3pct"].mean()),
        "all_positive_rate": float(frame["label_2d3pct"].mean()),
        "trained_entity_count": sum(row.get("status") != "INSUFFICIENT_TRAIN_SIGNALS" for row in survivor_rows),
        "survivor_count": len(survivor_ids),
        "survivor_ids": sorted(survivor_ids),
        "data_error_count": len(errors),
        "target": {"horizon_sessions": 2, "return_threshold": 0.03},
        "splits": {
            "stress": ["2020-01-01", "2022-06-30"],
            "train": ["2022-07-01", "2025-06-30"],
            "oos": ["2025-07-01", "2026-07-02"],
        },
        "ga": {
            "population": core.POPULATION,
            "generations": core.GENERATIONS,
            "elite_count": core.ELITE_COUNT,
            "patience": core.PATIENCE,
            "max_active_features": core.MAX_ACTIVE_FEATURES,
        },
        "live_connected": False,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    write_csv(OUT / "comparison_to_log_based.csv", comparison_rows(summary, pooled, survivor_rows))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
