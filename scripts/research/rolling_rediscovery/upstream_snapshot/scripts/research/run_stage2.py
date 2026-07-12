#!/usr/bin/env python3
"""Stage2/3 rolling rediscovery 50-symbol pilot orchestration.

This file is the directly modified working copy of the original Stage2
orchestration.  It keeps the original research flow—universe preparation,
train-only GA, stress/OOS validation, survivor gating and backtesting—but
changes the entity definition to bilateral D-5..D-1 interval genes and daily
rolling entry/exit decisions.

Research only.  It never imports or writes the live candidate pool, live sorter,
daemon state, market_state, positions or .env.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ISOLATED_ROOT = Path(__file__).resolve().parents[2]
KINGMAKER_ROOT = Path(__file__).resolve().parents[6]
if str(ISOLATED_ROOT) not in sys.path:
    sys.path.insert(0, str(ISOLATED_ROOT))

import numpy as np
import pandas as pd

from engine.learning.execution_mode_backtest import (
    classification_metrics,
    fixed_two_day_backtest,
    probability_scores,
    rolling_score_backtest,
    whipsaw_statistics,
)
from engine.learning.genetic import (
    IntervalGAConfig,
    IntervalIndividual,
    individual_mask,
    train_interval_ga,
    validate_interval_gene,
)

OUT_DIR = KINGMAKER_ROOT / "data/_system/analysis/stage2_3_rediscovery_pilot_20260712"
SNAPSHOT_DIR = KINGMAKER_ROOT / "data/_system/analysis/ohlc_snapshot_20260707"
WORKER_DIR = OUT_DIR / "_worker_tmp"
SELECTION_SEED = 20260712
WORKERS = 6
CURRENT_LIVE_10 = ["ADMA", "CRS", "ALGT", "AEIS", "ARKW", "CBRL", "BTU", "BB", "BN", "ACMR"]
START_DATE = pd.Timestamp("2020-01-01")
STRESS_END = pd.Timestamp("2022-06-30")
TRAIN_START = pd.Timestamp("2022-07-01")
TRAIN_END = pd.Timestamp("2025-06-30")
OOS_START = pd.Timestamp("2025-07-01")
TARGET_PCT = 3.0
HORIZON_SESSIONS = 2
ROUND_TRIP_COST_BPS = 10.0

FEATURES = [
    "ret_d5_pct",
    "ret_d4_pct",
    "ret_d3_pct",
    "ret_d2_pct",
    "ret_d1_pct",
    "cumulative_ret5_pct",
    "up_days5",
    "days_since_high5",
    "close_pos5",
    "pullback_from_high5_pct",
    "single_up_day5_pct",
    "fade_after_surge_score",
]
FEATURE_DEFINITIONS = {
    "ret_d5_pct": "D-6 close 대비 D-5 close 수익률(%)",
    "ret_d4_pct": "D-5 close 대비 D-4 close 수익률(%)",
    "ret_d3_pct": "D-4 close 대비 D-3 close 수익률(%)",
    "ret_d2_pct": "D-3 close 대비 D-2 close 수익률(%)",
    "ret_d1_pct": "D-2 close 대비 D-1 close 수익률(%)",
    "cumulative_ret5_pct": "D-6 close 대비 D-1 close 누적수익률(%)",
    "up_days5": "D-5~D-1 양의 일수익률 일수",
    "days_since_high5": "D-5~D-1 고가 최고점 이후 거래일 수(0=D-1)",
    "close_pos5": "D-1 close의 D-5~D-1 high/low 범위 내 위치(0~1)",
    "pullback_from_high5_pct": "D-5~D-1 최고가 대비 D-1 close 하락률(%)",
    "single_up_day5_pct": "D-5~D-1 최대 단일 일수익률(%)",
    "fade_after_surge_score": "초반 최대상승률 + 최근 2일 음의 누적수익률 절대값",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str] | None = None) -> None:
    materialized = list(rows)
    if fields is None:
        fields = list(materialized[0].keys()) if materialized else ["status"]
        for row in materialized:
            for key in row:
                if key not in fields:
                    fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            cooked: dict[str, Any] = {}
            for key in fields:
                value = row.get(key)
                if isinstance(value, (dict, list, tuple, set)):
                    cooked[key] = json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True)
                elif isinstance(value, np.generic):
                    cooked[key] = value.item()
                else:
                    cooked[key] = value
            writer.writerow(cooked)


def read_ohlcv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    date_col = "Date" if "Date" in frame.columns else ("date" if "date" in frame.columns else frame.columns[0])
    frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce").dt.tz_localize(None)
    frame = frame.dropna(subset=[date_col]).set_index(date_col).sort_index()
    frame.index = pd.DatetimeIndex(frame.index).normalize()
    frame = frame[~frame.index.duplicated(keep="last")]
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        if column not in frame.columns:
            raise ValueError(f"missing OHLCV column {column}")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["Open", "High", "Low", "Close"])


def regime_for_date(value: pd.Timestamp) -> str | None:
    day = pd.Timestamp(value).normalize()
    if day < START_DATE:
        return None
    if day <= STRESS_END:
        return "stress"
    if day <= TRAIN_END:
        return "train"
    return "oos"


def select_symbols() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for path in sorted(SNAPSHOT_DIR.glob("*_ohlcv.csv")):
        ticker = path.name.replace("_ohlcv.csv", "")
        if ticker.startswith("benchmark_") or ticker == "ohlc_snapshot_manifest.csv":
            continue
        try:
            frame = read_ohlcv(path)
            first = pd.Timestamp(frame.index.min()).normalize()
            last = pd.Timestamp(frame.index.max()).normalize()
            eligible = first <= pd.Timestamp("2020-01-31") and last >= OOS_START and len(frame) >= 500
            row = {
                "ticker": ticker,
                "source_path": str(path.relative_to(KINGMAKER_ROOT)),
                "source_sha256": sha256(path),
                "history_first_date": first.strftime("%Y-%m-%d"),
                "history_last_date": last.strftime("%Y-%m-%d"),
                "history_rows": len(frame),
            }
            if eligible:
                candidates.append(row)
            else:
                rejected.append({**row, "status": "INSUFFICIENT_HISTORY"})
        except Exception as exc:
            rejected.append({"ticker": ticker, "source_path": str(path), "status": "UNRECOVERABLE", "error": str(exc)})

    by_ticker = {row["ticker"]: row for row in candidates}
    missing_current = [ticker for ticker in CURRENT_LIVE_10 if ticker not in by_ticker]
    if missing_current:
        raise RuntimeError(f"current live symbols missing eligible frozen history: {missing_current}")

    pool = [row for row in candidates if row["ticker"] not in CURRENT_LIVE_10]
    rng = random.Random(SELECTION_SEED)
    rng.shuffle(pool)
    chosen = [by_ticker[ticker] for ticker in CURRENT_LIVE_10] + pool[:40]
    if len(chosen) != 50:
        raise RuntimeError(f"50 symbols not available: {len(chosen)}")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(chosen, 1):
        rows.append(
            {
                "selection_order": index,
                "selection_type": "CURRENT_LIVE_10" if row["ticker"] in CURRENT_LIVE_10 else "DETERMINISTIC_RANDOM_40",
                "selection_seed": SELECTION_SEED,
                **row,
                "status": "SELECTED",
            }
        )
    return rows, rejected


def _path_features(frame: pd.DataFrame, entry_idx: int) -> dict[str, float] | None:
    if entry_idx < 6:
        return None
    prior6 = frame.iloc[entry_idx - 6 : entry_idx]
    path5 = frame.iloc[entry_idx - 5 : entry_idx]
    closes = prior6["Close"].to_numpy(float)
    highs = path5["High"].to_numpy(float)
    lows = path5["Low"].to_numpy(float)
    if len(closes) != 6 or len(highs) != 5 or len(lows) != 5:
        return None
    if not np.isfinite(closes).all() or not np.isfinite(highs).all() or not np.isfinite(lows).all() or (closes <= 0).any():
        return None
    daily = 100.0 * (closes[1:] / closes[:-1] - 1.0)
    high5 = float(np.max(highs))
    low5 = float(np.min(lows))
    close_d1 = float(closes[-1])
    span = high5 - low5
    close_pos = 0.5 if span <= 0 else float(np.clip((close_d1 - low5) / span, 0.0, 1.0))
    first3_max = float(np.max(daily[:3]))
    last2_ret = 100.0 * (closes[-1] / closes[-3] - 1.0)
    return {
        "ret_d5_pct": float(daily[0]),
        "ret_d4_pct": float(daily[1]),
        "ret_d3_pct": float(daily[2]),
        "ret_d2_pct": float(daily[3]),
        "ret_d1_pct": float(daily[4]),
        "cumulative_ret5_pct": float(100.0 * (closes[-1] / closes[0] - 1.0)),
        "up_days5": float(np.sum(daily > 0.0)),
        "days_since_high5": float(4 - int(np.argmax(highs))),
        "close_pos5": close_pos,
        "pullback_from_high5_pct": float(max(0.0, 100.0 * (high5 / close_d1 - 1.0))),
        "single_up_day5_pct": float(np.max(daily)),
        "fade_after_surge_score": float(max(0.0, first3_max) + max(0.0, -last2_ret)),
    }


def build_daily_universe(symbol_rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in symbol_rows:
        ticker = item["ticker"]
        path = KINGMAKER_ROOT / item["source_path"]
        try:
            frame = read_ohlcv(path)
        except Exception as exc:
            errors.append({"ticker": ticker, "status": "UNRECOVERABLE", "error": str(exc)})
            continue
        for idx in range(6, len(frame) - HORIZON_SESSIONS):
            day = pd.Timestamp(frame.index[idx]).normalize()
            regime = regime_for_date(day)
            if regime is None:
                continue
            features = _path_features(frame, idx)
            if features is None:
                errors.append({"ticker": ticker, "date": day.strftime("%Y-%m-%d"), "status": "UNRECOVERABLE", "error": "D-5~D-1 feature unavailable"})
                continue
            entry_open = float(frame.iloc[idx]["Open"])
            entry_close = float(frame.iloc[idx]["Close"])
            future_high_1 = float(frame.iloc[idx + 1]["High"])
            future_high_2 = float(frame.iloc[idx + 2]["High"])
            if not all(math.isfinite(value) and value > 0 for value in [entry_open, entry_close, future_high_1, future_high_2]):
                errors.append({"ticker": ticker, "date": day.strftime("%Y-%m-%d"), "status": "UNRECOVERABLE", "error": "D0/future label price unavailable"})
                continue
            max_high = max(future_high_1, future_high_2)
            forward_pct = 100.0 * (max_high / entry_open - 1.0)
            rows.append(
                {
                    "ticker": ticker,
                    "date": day.strftime("%Y-%m-%d"),
                    "regime": regime,
                    "position_independent_daily_evaluation": True,
                    "holding_state_ignored_for_candidate_generation": True,
                    "feature_cutoff": "D-1",
                    "entry_open_d0": entry_open,
                    "entry_close_d0": entry_close,
                    "future_high_d1": future_high_1,
                    "future_high_d2": future_high_2,
                    "forward_max_return_pct": forward_pct,
                    "label_2d3pct": int(forward_pct >= TARGET_PCT),
                    **features,
                }
            )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values(["ticker", "date"]).reset_index(drop=True)
    return frame, errors


def fit_domain(train: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    values = train[FEATURES].to_numpy(float)
    low = np.nanmin(values, axis=0)
    high = np.nanmax(values, axis=0)
    bad = ~np.isfinite(low) | ~np.isfinite(high)
    low[bad] = 0.0
    high[bad] = 1.0
    constant = (high - low) <= 1e-12
    high[constant] = low[constant] + 1.0
    return low, high


def normalize(frame: pd.DataFrame, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    span = np.maximum(high - low, 1e-12)
    return (frame[FEATURES].to_numpy(float) - low) / span


def validation_min_pass(n: int) -> int:
    # [추정] 파일럿 게이트: 검증 구간의 1.5%, 최소 8건.
    return max(8, int(math.ceil(max(0, n) * 0.015)))


def validation_gate(metrics: dict[str, Any], train_precision: float) -> tuple[bool, list[str], float, int]:
    minimum = validation_min_pass(int(metrics["signal_count"]))
    precision_floor = max(0.30, float(metrics["base_rate"]) + 0.03, train_precision - 0.15)
    reasons: list[str] = []
    if int(metrics["passed_count"]) < minimum:
        reasons.append(f"passed_count<{minimum}")
    if float(metrics["precision"]) < precision_floor:
        reasons.append(f"precision<{precision_floor:.4f}")
    return not reasons, reasons, precision_floor, minimum


def ticker_seed(ticker: str) -> int:
    return int(hashlib.sha256(f"rolling-rediscovery:{SELECTION_SEED}:{ticker}".encode()).hexdigest()[:8], 16)


def _model_hash(ticker: str, low: np.ndarray, high: np.ndarray) -> str:
    payload = json.dumps(
        {"ticker": ticker, "features": FEATURES, "low": np.round(low, 8).tolist(), "high": np.round(high, 8).tolist()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def train_symbol_worker(payload: dict[str, Any]) -> dict[str, Any]:
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    ticker = str(payload["ticker"])
    input_path = Path(payload["input_path"])
    output_path = Path(payload["output_path"])
    pid = os.getpid()
    process_name = mp.current_process().name
    result: dict[str, Any]
    try:
        frame = pd.read_csv(input_path)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        train = frame[frame["regime"] == "train"].copy()
        if len(train) < 100:
            raise RuntimeError(f"INSUFFICIENT_DATA train rows={len(train)}")
        domain_low, domain_high = fit_domain(train)
        x_train = normalize(train, domain_low, domain_high)
        y_train = train["label_2d3pct"].to_numpy(int)
        ga_config = IntervalGAConfig()
        ga = train_interval_ga(x_train, y_train, FEATURES, seed=ticker_seed(ticker), config=ga_config)
        best = ga.best
        valid, valid_reason = validate_interval_gene(best, ga_config)
        model_hash = _model_hash(ticker, best.low, best.high)

        training_rows: list[dict[str, Any]] = []
        for row in ga.history:
            training_rows.append(
                {
                    "ticker": ticker,
                    "model_hash": model_hash,
                    "seed": ticker_seed(ticker),
                    "pid": pid,
                    "process_name": process_name,
                    **row,
                    "generations_run": ga.generations_run,
                    "train_rows": len(train),
                    "train_min_sample_gate": max(20, int(math.ceil(len(train) * 0.02))),
                    "min_width_norm": ga_config.min_width_norm,
                    "strict_all_feature_and": True,
                    "weighted_sum_path": False,
                }
            )

        fallback_by_feature = Counter(event["feature"] for event in ga.fallback_events if event.get("applied"))
        bounds_rows: list[dict[str, Any]] = []
        span = domain_high - domain_low
        for index, feature in enumerate(FEATURES):
            width = float(best.high[index] - best.low[index])
            bounds_rows.append(
                {
                    "ticker": ticker,
                    "model_hash": model_hash,
                    "feature": feature,
                    "feature_definition": FEATURE_DEFINITIONS[feature],
                    "domain_min_train": float(domain_low[index]),
                    "domain_max_train": float(domain_high[index]),
                    "low_norm": float(best.low[index]),
                    "high_norm": float(best.high[index]),
                    "width_norm": width,
                    "low_value": float(domain_low[index] + best.low[index] * span[index]),
                    "high_value": float(domain_low[index] + best.high[index] * span[index]),
                    "finite_low": bool(math.isfinite(float(best.low[index]))),
                    "finite_high": bool(math.isfinite(float(best.high[index]))),
                    "bilateral": bool(best.high[index] > best.low[index]),
                    "min_width_pass": bool(width + 1e-12 >= ga_config.min_width_norm),
                    "near_full_noise_gene": bool(width >= ga_config.max_near_full_width_norm),
                    "fallback_applied_any_generation": bool(fallback_by_feature[feature]),
                    "fallback_event_count": int(fallback_by_feature[feature]),
                    "all_feature_and": True,
                }
            )

        fallback_rows = [{"ticker": ticker, "model_hash": model_hash, **event} for event in ga.fallback_events]
        metric_rows: list[dict[str, Any]] = []
        masks: dict[str, np.ndarray] = {}
        scores_by_regime: dict[str, np.ndarray] = {}
        metrics_by_regime: dict[str, dict[str, Any]] = {}
        for regime in ["train", "stress", "oos"]:
            subset = frame[frame["regime"] == regime].copy()
            x = normalize(subset, domain_low, domain_high)
            mask = individual_mask(best, x)
            scores = probability_scores(mask, best.pass_probability)
            metrics = classification_metrics(subset["label_2d3pct"].to_numpy(int), scores >= best.decision_threshold)
            masks[regime] = mask
            scores_by_regime[regime] = scores
            metrics_by_regime[regime] = metrics

        train_metrics = metrics_by_regime["train"]
        train_gate = bool(valid and train_metrics["passed_count"] >= max(20, int(math.ceil(train_metrics["signal_count"] * 0.02))) and train_metrics["precision"] >= best.decision_threshold)
        stress_gate, stress_reasons, stress_precision_floor, stress_sample_floor = validation_gate(metrics_by_regime["stress"], float(train_metrics["precision"]))
        oos_gate, oos_reasons, oos_precision_floor, oos_sample_floor = validation_gate(metrics_by_regime["oos"], float(train_metrics["precision"]))
        survivor = bool(train_gate and stress_gate and oos_gate and valid)

        for regime in ["train", "stress", "oos"]:
            metrics = metrics_by_regime[regime]
            if regime == "train":
                passed_gate = train_gate
                reasons = [] if train_gate else ["train_precision_or_sample_or_gene_gate"]
                precision_floor = best.decision_threshold
                sample_floor = max(20, int(math.ceil(metrics["signal_count"] * 0.02)))
            elif regime == "stress":
                passed_gate = stress_gate
                reasons = stress_reasons
                precision_floor = stress_precision_floor
                sample_floor = stress_sample_floor
            else:
                passed_gate = oos_gate
                reasons = oos_reasons
                precision_floor = oos_precision_floor
                sample_floor = oos_sample_floor
            metric_rows.append(
                {
                    "ticker": ticker,
                    "model_hash": model_hash,
                    "regime": regime,
                    **metrics,
                    "pass_probability": best.pass_probability,
                    "decision_threshold": best.decision_threshold,
                    "precision_floor": precision_floor,
                    "sample_floor": sample_floor,
                    "passed_gate": passed_gate,
                    "gate_fail_reasons": reasons,
                    "survivor": survivor,
                }
            )

        survivor_row = {
            "ticker": ticker,
            "model_hash": model_hash,
            "status": "SURVIVOR" if survivor else "REJECTED",
            "survivor": survivor,
            "train_gate": train_gate,
            "stress_gate": stress_gate,
            "oos_gate": oos_gate,
            "valid_bilateral_gene": valid,
            "gene_validation_reason": valid_reason,
            "pass_probability": best.pass_probability,
            "decision_threshold": best.decision_threshold,
            "train_passed_count": train_metrics["passed_count"],
            "train_precision": train_metrics["precision"],
            "stress_passed_count": metrics_by_regime["stress"]["passed_count"],
            "stress_precision": metrics_by_regime["stress"]["precision"],
            "oos_passed_count": metrics_by_regime["oos"]["passed_count"],
            "oos_precision": metrics_by_regime["oos"]["precision"],
            "thin_sample_rejected": bool(any("passed_count" in reason for reason in stress_reasons + oos_reasons) or not train_gate),
            "rejected_narrow_individual_count": ga.rejected_narrow_count,
            "rejected_open_individual_count": ga.rejected_open_count,
            "rejected_near_full_individual_count": ga.rejected_near_full_count,
            "upper_fallback_event_count": len(ga.fallback_events),
            "genes_json": {feature: [float(best.low[i]), float(best.high[i])] for i, feature in enumerate(FEATURES)},
            "reject_reasons": ([] if train_gate else ["train_gate"]) + stress_reasons + oos_reasons,
        }

        backtest_rows: list[dict[str, Any]] = []
        whipsaw_rows: list[dict[str, Any]] = []
        for regime in ["train", "stress", "oos"]:
            subset = frame[frame["regime"] == regime].copy().reset_index(drop=True)
            scores = scores_by_regime[regime]
            rolling_metrics, rolling_trades = rolling_score_backtest(subset, scores, best.decision_threshold, round_trip_cost_bps=ROUND_TRIP_COST_BPS)
            fixed_metrics, fixed_trades = fixed_two_day_backtest(subset, scores, best.decision_threshold, round_trip_cost_bps=ROUND_TRIP_COST_BPS)
            for method, metrics, trades in [
                ("rolling_same_threshold_no_holding_cap", rolling_metrics, rolling_trades),
                ("fixed_2_sessions", fixed_metrics, fixed_trades),
            ]:
                backtest_rows.append(
                    {
                        "ticker": ticker,
                        "model_hash": model_hash,
                        "regime": regime,
                        "method": method,
                        "survivor": survivor,
                        "decision_threshold": best.decision_threshold,
                        "pass_probability": best.pass_probability,
                        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
                        **metrics,
                    }
                )
            whipsaw_rows.append(
                {
                    "ticker": ticker,
                    "model_hash": model_hash,
                    "regime": regime,
                    "survivor": survivor,
                    "decision_threshold": best.decision_threshold,
                    **whipsaw_statistics(subset, scores, best.decision_threshold, rolling_trades),
                }
            )

        precision_gap_stress = float(train_metrics["precision"] - metrics_by_regime["stress"]["precision"])
        precision_gap_oos = float(train_metrics["precision"] - metrics_by_regime["oos"]["precision"])
        result = {
            "status": "OK",
            "ticker": ticker,
            "model_hash": model_hash,
            "training_rows": training_rows,
            "bounds_rows": bounds_rows,
            "fallback_rows": fallback_rows,
            "metric_rows": metric_rows,
            "survivor_row": survivor_row,
            "backtest_rows": backtest_rows,
            "whipsaw_rows": whipsaw_rows,
            "overfit_row": {
                "ticker": ticker,
                "model_hash": model_hash,
                "survivor": survivor,
                "train_precision": train_metrics["precision"],
                "stress_precision": metrics_by_regime["stress"]["precision"],
                "oos_precision": metrics_by_regime["oos"]["precision"],
                "train_to_stress_precision_gap": precision_gap_stress,
                "train_to_oos_precision_gap": precision_gap_oos,
                "strict_all_feature_and": True,
                "weighted_sum_path_present": False,
                "bilateral_gene_pass": valid,
                "all_min_width_pass": all(row["min_width_pass"] for row in bounds_rows),
                "near_full_gene_count": sum(row["near_full_noise_gene"] for row in bounds_rows),
                "thin_sample_rejected": survivor_row["thin_sample_rejected"],
            },
        }
    except Exception as exc:
        result = {"status": "ERROR", "ticker": ticker, "error": f"{type(exc).__name__}: {exc}"}

    wall = time.perf_counter() - started_wall
    cpu = time.process_time() - started_cpu
    result["parallel_row"] = {
        "ticker": ticker,
        "status": result.get("status"),
        "pid": pid,
        "process_name": process_name,
        "seed": ticker_seed(ticker),
        "wall_seconds": wall,
        "cpu_seconds": cpu,
        "cpu_percent_single_core": 100.0 * cpu / wall if wall > 0 else 0.0,
        "worker_limit": WORKERS,
        "input_path": str(input_path),
        "output_path": str(output_path),
        "error": result.get("error", ""),
    }
    output_path.write_text(json.dumps(json_safe(result), ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return {"ticker": ticker, "status": result.get("status"), "output_path": str(output_path), "parallel_row": result["parallel_row"]}


def _merge_worker_results(worker_returns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    merged = {
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
            merged["errors"].append({"ticker": data.get("ticker"), "status": data.get("status"), "error": data.get("error", "UNKNOWN")})
            continue
        merged["training"].extend(data.get("training_rows", []))
        merged["bounds"].extend(data.get("bounds_rows", []))
        merged["fallback"].extend(data.get("fallback_rows", []))
        merged["metrics"].extend(data.get("metric_rows", []))
        merged["survivors"].append(data.get("survivor_row", {}))
        merged["backtests"].extend(data.get("backtest_rows", []))
        merged["whipsaw"].extend(data.get("whipsaw_rows", []))
        merged["overfit"].append(data.get("overfit_row", {}))
    return merged


def _label_distribution(feature_set: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (ticker, regime), group in feature_set.groupby(["ticker", "regime"], sort=True):
        rows.append(
            {
                "ticker": ticker,
                "regime": regime,
                "sample_count": len(group),
                "positive_count": int(group["label_2d3pct"].sum()),
                "negative_count": int((1 - group["label_2d3pct"]).sum()),
                "positive_rate": float(group["label_2d3pct"].mean()),
                "label_definition": "D0 open 대비 D+1~D+2 high 최대값 +3% 이상",
            }
        )
    for regime, group in feature_set.groupby("regime", sort=True):
        rows.append(
            {
                "ticker": "ALL_50",
                "regime": regime,
                "sample_count": len(group),
                "positive_count": int(group["label_2d3pct"].sum()),
                "negative_count": int((1 - group["label_2d3pct"]).sum()),
                "positive_rate": float(group["label_2d3pct"].mean()),
                "label_definition": "D0 open 대비 D+1~D+2 high 최대값 +3% 이상",
            }
        )
    return rows


def _hhi(survivors: list[dict[str, Any]]) -> float | None:
    counts = [float(row.get("oos_passed_count", 0)) for row in survivors if row.get("survivor") and float(row.get("oos_passed_count", 0)) > 0]
    total = sum(counts)
    if total <= 0:
        return None
    return float(sum((value / total) ** 2 for value in counts))


def _integrity_probes() -> dict[str, Any]:
    cfg = IntervalGAConfig()
    open_gene = IntervalIndividual(np.array([0.1, 0.1]), np.array([np.nan, 0.9]))
    narrow_gene = IntervalIndividual(np.array([0.2, 0.2]), np.array([0.21, 0.8]))
    open_valid, open_reason = validate_interval_gene(open_gene, cfg)
    narrow_valid, narrow_reason = validate_interval_gene(narrow_gene, cfg)
    compensation_x = np.array([[0.5, 0.8], [0.8, 0.5]], dtype=float)
    compensation_gene = IntervalIndividual(np.array([0.4, 0.4]), np.array([0.6, 0.6]))
    compensation_mask = individual_mask(compensation_gene, compensation_x)
    return {
        "open_gene_rejected": not open_valid,
        "open_gene_reason": open_reason,
        "narrow_gene_rejected": not narrow_valid,
        "narrow_gene_reason": narrow_reason,
        "cross_feature_compensation_pass_count": int(compensation_mask.sum()),
        "strict_and_compensation_blocked": int(compensation_mask.sum()) == 0,
    }


def _write_readout(
    *,
    selected: list[dict[str, Any]],
    feature_set: pd.DataFrame,
    merged: dict[str, list[dict[str, Any]]],
    probes: dict[str, Any],
    verdict: str,
    fail_reasons: list[str],
    hhi: float | None,
    elapsed: float,
) -> None:
    survivor_rows = [row for row in merged["survivors"] if row.get("survivor")]
    fallback_count = len([row for row in merged["fallback"] if row.get("applied")])
    oos_backtests = [row for row in merged["backtests"] if row.get("regime") == "oos"]
    rolling = [row for row in oos_backtests if row.get("method") == "rolling_same_threshold_no_holding_cap" and row.get("survivor")]
    fixed = [row for row in oos_backtests if row.get("method") == "fixed_2_sessions" and row.get("survivor")]
    rolling_avg = float(np.mean([row["avg_return_pct"] for row in rolling])) if rolling else None
    fixed_avg = float(np.mean([row["avg_return_pct"] for row in fixed])) if fixed else None
    max_hold = max([int(row.get("max_holding_sessions", 0)) for row in merged["whipsaw"]] or [0])
    lines = [
        "# Stage2/3 rolling 재발견 — 50종목 파일럿",
        "",
        f"- 판정: **{verdict}**",
        f"- 실행 시각(UTC): {utc_now()}",
        f"- 실행시간: {elapsed:.1f}초",
        f"- 표본: {len(selected)}종목 (현 라이브 10 + seed {SELECTION_SEED} 결정적 무작위 40)",
        f"- 매일 독립 평가 행: {len(feature_set):,}",
        f"- survivor: {len(survivor_rows)} / {len(selected)}",
        f"- 종목 쏠림 HHI: {hhi if hhi is not None else 'INSUFFICIENT_DATA'}",
        f"- upper-bound fallback 적용 이벤트: {fallback_count}",
        f"- rolling 최장 보유 세션: {max_hold}",
        "",
        "## 구현 확인",
        "",
        "- 복사본 자체를 직접 수정·실행했다. 별도 독립 runner를 새로 만들지 않았다.",
        "- 모든 거래일을 보유 여부와 무관하게 독립 진입 후보로 만들었다.",
        "- feature는 D-5~D-1의 해석 가능한 path_filter 12개만 사용했다.",
        "- 모든 gene은 정규화 train 범위의 [하한, 상한]이며 최소폭 10%를 강제했다.",
        "- 진입 통과는 12개 지표가 각각 자기 구간을 만족하는 strict AND다. 가중합·다른 지표 상쇄·호재 예외가 없다.",
        "- 점수는 strict-AND 통과 train 표본의 +3% 정밀도이며, 동일 임계선을 진입·유지·청산에 사용했다.",
        "- rolling 백테스트에는 인위적 보유일 상한이 없고 구간 말에만 평가용 mark-to-market을 적용했다.",
        "- GA는 train만 사용했고 stress와 OOS는 검증 전용 이중 게이트다.",
        "- 50개 종목 GA는 최대 6개 spawn worker로 실행했고 종목별 seed를 고정했다.",
        "",
        "## 과적합 완충",
        "",
        f"- 열린 gene probe 차단: {probes['open_gene_rejected']} ({probes['open_gene_reason']})",
        f"- 최소폭 미달 probe 차단: {probes['narrow_gene_rejected']} ({probes['narrow_gene_reason']})",
        f"- 지표 간 합산 상쇄 probe 통과 건수: {probes['cross_feature_compensation_pass_count']} (0이어야 정상)",
        "- [추정] train 거래수 게이트는 max(20, train 행의 2%), stress/OOS는 max(8, 검증 행의 1.5%)로 파일럿 실행했다.",
        "- [추정] 검증 정밀도 하한은 max(30%, 해당 regime 양성률+3%p, train 정밀도-15%p)다.",
        "",
        "## Rolling vs 고정 2일",
        "",
        f"- survivor OOS rolling 거래당 평균수익률: {rolling_avg if rolling_avg is not None else 'INSUFFICIENT_DATA'}",
        f"- survivor OOS 고정 2일 거래당 평균수익률: {fixed_avg if fixed_avg is not None else 'INSUFFICIENT_DATA'}",
        "- 상세 성과와 보유기간은 `rolling_vs_fixed_backtest.csv`, 휩쏘와 장기보유 위험은 `whipsaw_stats.csv`에 기록했다.",
        "",
        "## 판정 사유",
        "",
    ]
    if fail_reasons:
        lines.extend([f"- {reason}" for reason in fail_reasons])
    else:
        lines.append("- 코드·출력·양방향 gene·최소폭·strict AND·6병렬·rolling 검증이 모두 정상이며 전체 확대 가능하다.")
    lines.extend(
        [
            "",
            "## 누수·저장 제약",
            "",
            "- feature 열에는 D0 gap, STK_gap_d0, ETF_gap_d0, flow, order_book가 없다.",
            "- `entry_open_d0`, 미래 고가는 라벨과 백테스트 체결 계산 전용이며 GA feature 목록에 들어가지 않는다.",
            "- 데이터가 없는 종목/날짜는 `NOT_STORED` 또는 `UNRECOVERABLE` 오류 목록으로 남긴다.",
            "- 라이브 후보 풀·정렬·daemon·설정은 호출하거나 수정하지 않았다.",
        ]
    )
    (OUT_DIR / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pilot() -> dict[str, Any]:
    started = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_DIR.mkdir(parents=True, exist_ok=True)

    selected, rejected = select_symbols()
    write_csv(OUT_DIR / "symbol_list.csv", selected)
    feature_set, universe_errors = build_daily_universe(selected)
    if feature_set.empty:
        raise RuntimeError("UNRECOVERABLE: feature universe empty")

    pilot_universe = feature_set[
        ["ticker", "date", "regime", "position_independent_daily_evaluation", "holding_state_ignored_for_candidate_generation", "feature_cutoff", "label_2d3pct"]
    ].copy()
    pilot_universe["status"] = "ELIGIBLE"
    pilot_universe.to_csv(OUT_DIR / "pilot_universe.csv", index=False)
    feature_set.to_csv(OUT_DIR / "feature_set.csv", index=False)
    write_csv(OUT_DIR / "label_distribution.csv", _label_distribution(feature_set))
    write_csv(OUT_DIR / "universe_errors.csv", rejected + universe_errors)

    tasks: list[dict[str, Any]] = []
    for row in selected:
        ticker = row["ticker"]
        ticker_frame = feature_set[feature_set["ticker"] == ticker].copy()
        input_path = WORKER_DIR / f"input_{ticker}.csv"
        output_path = WORKER_DIR / f"result_{ticker}.json"
        ticker_frame.to_csv(input_path, index=False)
        tasks.append({"ticker": ticker, "input_path": str(input_path), "output_path": str(output_path)})

    context = mp.get_context("spawn")
    with context.Pool(processes=WORKERS) as pool:
        worker_returns = pool.map(train_symbol_worker, tasks)
    merged = _merge_worker_results(worker_returns)

    write_csv(OUT_DIR / "training_log.csv", merged["training"])
    write_csv(OUT_DIR / "gene_bounds_check.csv", merged["bounds"])
    write_csv(OUT_DIR / "parallel_run_log.csv", merged["parallel"])
    write_csv(OUT_DIR / "upper_bound_fallback_log.csv", merged["fallback"])
    write_csv(OUT_DIR / "per_regime_metrics.csv", merged["metrics"])
    write_csv(OUT_DIR / "survivor_summary.csv", merged["survivors"])
    write_csv(OUT_DIR / "rolling_vs_fixed_backtest.csv", merged["backtests"])
    write_csv(OUT_DIR / "whipsaw_stats.csv", merged["whipsaw"])

    probes = _integrity_probes()
    survivor_rows = [row for row in merged["survivors"] if row.get("survivor")]
    hhi = _hhi(merged["survivors"])
    total_bounds = len(merged["bounds"])
    valid_bounds = sum(bool(row.get("bilateral")) and bool(row.get("min_width_pass")) for row in merged["bounds"])
    fallback_applied = sum(bool(row.get("applied")) for row in merged["fallback"])
    unique_pids = len({row.get("pid") for row in merged["parallel"] if row.get("pid")})
    max_hold = max([int(row.get("max_holding_sessions", 0)) for row in merged["whipsaw"]] or [0])

    rolling_oos_survivors = [row for row in merged["backtests"] if row.get("survivor") and row.get("regime") == "oos" and row.get("method") == "rolling_same_threshold_no_holding_cap"]
    fixed_oos_survivors = [row for row in merged["backtests"] if row.get("survivor") and row.get("regime") == "oos" and row.get("method") == "fixed_2_sessions"]
    rolling_oos_avg = float(np.mean([row["avg_return_pct"] for row in rolling_oos_survivors])) if rolling_oos_survivors else None
    fixed_oos_avg = float(np.mean([row["avg_return_pct"] for row in fixed_oos_survivors])) if fixed_oos_survivors else None

    global_overfit = {
        "ticker": "ALL_50",
        "model_hash": "GLOBAL",
        "survivor": bool(survivor_rows),
        "train_precision": float(np.mean([row["train_precision"] for row in merged["overfit"]])) if merged["overfit"] else None,
        "stress_precision": float(np.mean([row["stress_precision"] for row in merged["overfit"]])) if merged["overfit"] else None,
        "oos_precision": float(np.mean([row["oos_precision"] for row in merged["overfit"]])) if merged["overfit"] else None,
        "train_to_stress_precision_gap": float(np.mean([row["train_to_stress_precision_gap"] for row in merged["overfit"]])) if merged["overfit"] else None,
        "train_to_oos_precision_gap": float(np.mean([row["train_to_oos_precision_gap"] for row in merged["overfit"]])) if merged["overfit"] else None,
        "strict_all_feature_and": probes["strict_and_compensation_blocked"],
        "weighted_sum_path_present": False,
        "bilateral_gene_pass": total_bounds == valid_bounds and total_bounds > 0,
        "all_min_width_pass": total_bounds == valid_bounds and total_bounds > 0,
        "near_full_gene_count": sum(bool(row.get("near_full_noise_gene")) for row in merged["bounds"]),
        "thin_sample_rejected": sum(bool(row.get("thin_sample_rejected")) for row in merged["survivors"]),
        "symbol_hhi_oos_passed_count": hhi,
        "completed_symbols": len(merged["survivors"]),
        "worker_error_count": len(merged["errors"]),
        "configured_workers": WORKERS,
        "observed_unique_worker_pids": unique_pids,
        "upper_fallback_applied_count": fallback_applied,
        "rolling_max_holding_sessions": max_hold,
        "rolling_oos_avg_return_pct_survivors": rolling_oos_avg,
        "fixed_oos_avg_return_pct_survivors": fixed_oos_avg,
    }
    overfit_rows = merged["overfit"] + [global_overfit]
    write_csv(OUT_DIR / "overfit_check.csv", overfit_rows)

    fail_reasons: list[str] = []
    if merged["errors"]:
        fail_reasons.append(f"종목 worker 오류 {len(merged['errors'])}건")
    if len(merged["survivors"]) != 50:
        fail_reasons.append(f"완료 종목이 50개가 아님: {len(merged['survivors'])}")
    if total_bounds != 50 * len(FEATURES) or valid_bounds != total_bounds:
        fail_reasons.append(f"양방향/최소폭 gene 검증 실패: {valid_bounds}/{total_bounds}")
    if not probes["strict_and_compensation_blocked"]:
        fail_reasons.append("지표 간 합산 상쇄 probe가 차단되지 않음")
    if not probes["open_gene_rejected"] or not probes["narrow_gene_rejected"]:
        fail_reasons.append("열린 gene 또는 최소폭 미달 gene probe 차단 실패")
    if fallback_applied <= 0:
        fail_reasons.append("upper-bound fallback 실제 적용 사례가 없음")
    if not survivor_rows:
        fail_reasons.append("stress·OOS 이중 게이트 survivor가 0개")
    if unique_pids > WORKERS:
        fail_reasons.append(f"worker PID가 6개를 초과: {unique_pids}")
    if max_hold > 252:
        fail_reasons.append(f"rolling 무한보유 위험: 최장 {max_hold} 세션")
    if rolling_oos_avg is not None and fixed_oos_avg is not None and rolling_oos_avg < fixed_oos_avg - 0.5:
        fail_reasons.append(f"survivor OOS rolling 평균수익이 고정 2일보다 0.5%p 초과 열위: {rolling_oos_avg:.4f} vs {fixed_oos_avg:.4f}")

    verdict = "PILOT_PASS" if not fail_reasons else "PILOT_FAIL"
    elapsed = time.perf_counter() - started
    _write_readout(selected=selected, feature_set=feature_set, merged=merged, probes=probes, verdict=verdict, fail_reasons=fail_reasons, hhi=hhi, elapsed=elapsed)

    summary = {
        "verdict": verdict,
        "fail_reasons": fail_reasons,
        "selected_symbols": [row["ticker"] for row in selected],
        "feature_rows": len(feature_set),
        "completed_symbols": len(merged["survivors"]),
        "survivor_count": len(survivor_rows),
        "worker_error_count": len(merged["errors"]),
        "worker_errors": merged["errors"],
        "configured_workers": WORKERS,
        "observed_unique_worker_pids": unique_pids,
        "upper_fallback_applied_count": fallback_applied,
        "gene_bounds_valid": valid_bounds,
        "gene_bounds_total": total_bounds,
        "hhi": hhi,
        "max_holding_sessions": max_hold,
        "rolling_oos_avg_return_pct_survivors": rolling_oos_avg,
        "fixed_oos_avg_return_pct_survivors": fixed_oos_avg,
        "elapsed_seconds": elapsed,
        "generated_at_utc": utc_now(),
    }
    (OUT_DIR / "pilot_summary.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2/3 rolling rediscovery 50-symbol pilot")
    parser.add_argument("--workers", type=int, default=WORKERS, help="must be <= 6; pilot contract uses 6")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.workers != WORKERS:
        raise SystemExit("This pilot is fixed to multiprocessing.Pool(6); --workers must be 6")
    summary = run_pilot()
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
