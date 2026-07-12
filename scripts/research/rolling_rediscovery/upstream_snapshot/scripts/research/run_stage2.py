#!/usr/bin/env python3
"""Stage2/3 rolling rediscovery rerun: 50 symbols, six workers.

Research-only working copy.  It reuses the exact prior 50-symbol list, restores
three independent Stage2 train splits at population 100 / generations 50 /
patience 15, validates only on stress and OOS, and compares three exit methods.
No live candidate, sorter, daemon, position, market-state or .env module is
imported or written.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from collections import Counter, defaultdict
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
    rolling_target_backtest,
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
PREVIOUS_RUN_DIR = OUT_DIR / "_prev_run_gaShrunk_exitBug"
WORKER_DIR = OUT_DIR / "_worker_tmp"
WORKERS = 6
SELECTION_SEED = 20260712
START_DATE = pd.Timestamp("2020-01-01")
STRESS_END = pd.Timestamp("2022-06-30")
TRAIN_START = pd.Timestamp("2022-07-01")
TRAIN_END = pd.Timestamp("2025-06-30")
OOS_START = pd.Timestamp("2025-07-01")
TARGET_PCT = 3.0
HORIZON_SESSIONS = 2
ROUND_TRIP_COST_BPS = 10.0

TRAIN_SPLITS: list[dict[str, str]] = [
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
]

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


def load_previous_symbols() -> list[dict[str, Any]]:
    path = PREVIOUS_RUN_DIR / "symbol_list.csv"
    if not path.exists():
        raise RuntimeError(f"previous symbol list missing: {path}")
    frame = pd.read_csv(path)
    if len(frame) != 50 or frame["ticker"].nunique() != 50:
        raise RuntimeError(f"previous symbol list must contain exact 50 unique symbols: rows={len(frame)}")
    rows = frame.to_dict("records")
    for row in rows:
        source = KINGMAKER_ROOT / str(row["source_path"])
        if not source.exists():
            raise RuntimeError(f"NOT_STORED source missing: {row['ticker']} {source}")
        actual = sha256(source)
        if str(row.get("source_sha256", "")) and actual != str(row["source_sha256"]):
            raise RuntimeError(f"source SHA changed for {row['ticker']}: {actual}")
        row["reused_from"] = str(path.relative_to(KINGMAKER_ROOT))
        row["status"] = "SELECTED_REUSED_EXACTLY"
    return rows


def regime_for_date(value: pd.Timestamp) -> str | None:
    day = pd.Timestamp(value).normalize()
    if day < START_DATE:
        return None
    if day <= STRESS_END:
        return "stress"
    if day <= TRAIN_END:
        return "train"
    return "oos"


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
        ticker = str(item["ticker"])
        path = KINGMAKER_ROOT / str(item["source_path"])
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
            entry_high = float(frame.iloc[idx]["High"])
            entry_low = float(frame.iloc[idx]["Low"])
            entry_close = float(frame.iloc[idx]["Close"])
            future_high_1 = float(frame.iloc[idx + 1]["High"])
            future_high_2 = float(frame.iloc[idx + 2]["High"])
            prices = [entry_open, entry_high, entry_low, entry_close, future_high_1, future_high_2]
            if not all(math.isfinite(value) and value > 0 for value in prices):
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
                    "entry_high_d0": entry_high,
                    "entry_low_d0": entry_low,
                    "entry_close_d0": entry_close,
                    "future_high_d1": future_high_1,
                    "future_high_d2": future_high_2,
                    "forward_max_return_pct": forward_pct,
                    "label_2d3pct": int(forward_pct >= TARGET_PCT),
                    **features,
                }
            )
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["ticker", "date"]).reset_index(drop=True)
    return out, errors


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


def split_frame(frame: pd.DataFrame, split: dict[str, str]) -> pd.DataFrame:
    start = pd.Timestamp(split["train_start"])
    end = pd.Timestamp(split["train_end"])
    return frame[(frame["date"] >= start) & (frame["date"] <= end)].copy()


def train_min_pass(n: int) -> int:
    return max(20, int(math.ceil(max(0, n) * 0.02)))


def validation_min_pass(n: int) -> int:
    # [추정] retained pilot gate: validation 1.5%, minimum 8 rows.
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


def ticker_seed(ticker: str, split_index: int = 0) -> int:
    raw = f"rolling-rediscovery:{SELECTION_SEED}:{ticker}:split:{split_index}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)


def _model_hash(ticker: str, split_label: str, domain_low: np.ndarray, domain_high: np.ndarray, low: np.ndarray, high: np.ndarray) -> str:
    payload = json.dumps(
        {
            "ticker": ticker,
            "split": split_label,
            "features": FEATURES,
            "domain_low": np.round(domain_low, 8).tolist(),
            "domain_high": np.round(domain_high, 8).tolist(),
            "low": np.round(low, 8).tolist(),
            "high": np.round(high, 8).tolist(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def evaluate_candidate(
    *,
    ticker: str,
    frame: pd.DataFrame,
    split: dict[str, str],
    split_index: int,
    domain_low: np.ndarray,
    domain_high: np.ndarray,
    ga: Any,
    cfg: IntervalGAConfig,
) -> dict[str, Any]:
    best = ga.best
    valid, valid_reason = validate_interval_gene(best, cfg)
    model_hash = _model_hash(ticker, split["label"], domain_low, domain_high, best.low, best.high)
    origin_train = split_frame(frame, split)

    metric_rows: list[dict[str, Any]] = []
    metrics_by_regime: dict[str, dict[str, Any]] = {}
    masks_by_regime: dict[str, np.ndarray] = {}
    for regime, subset in [
        ("train", origin_train),
        ("stress", frame[frame["regime"] == "stress"].copy()),
        ("oos", frame[frame["regime"] == "oos"].copy()),
    ]:
        x = normalize(subset, domain_low, domain_high)
        mask = individual_mask(best, x)
        scores = probability_scores(mask, best.pass_probability)
        metrics = classification_metrics(subset["label_2d3pct"].to_numpy(int), scores >= best.decision_threshold)
        metrics_by_regime[regime] = metrics
        masks_by_regime[regime] = mask

    train_metrics = metrics_by_regime["train"]
    train_floor = train_min_pass(int(train_metrics["signal_count"]))
    train_gate = bool(valid and train_metrics["passed_count"] >= train_floor and train_metrics["precision"] >= best.decision_threshold)
    stress_gate, stress_reasons, stress_precision_floor, stress_sample_floor = validation_gate(metrics_by_regime["stress"], float(train_metrics["precision"]))
    oos_gate, oos_reasons, oos_precision_floor, oos_sample_floor = validation_gate(metrics_by_regime["oos"], float(train_metrics["precision"]))
    survivor = bool(train_gate and stress_gate and oos_gate and valid)

    for regime in ["train", "stress", "oos"]:
        metrics = metrics_by_regime[regime]
        if regime == "train":
            passed_gate = train_gate
            reasons = [] if train_gate else ["train_precision_or_sample_or_gene_gate"]
            precision_floor = best.decision_threshold
            sample_floor = train_floor
            period_start = split["train_start"]
            period_end = split["train_end"]
        elif regime == "stress":
            passed_gate = stress_gate
            reasons = stress_reasons
            precision_floor = stress_precision_floor
            sample_floor = stress_sample_floor
            period_start = str(frame.loc[frame["regime"] == "stress", "date"].min().date())
            period_end = str(frame.loc[frame["regime"] == "stress", "date"].max().date())
        else:
            passed_gate = oos_gate
            reasons = oos_reasons
            precision_floor = oos_precision_floor
            sample_floor = oos_sample_floor
            period_start = str(frame.loc[frame["regime"] == "oos", "date"].min().date())
            period_end = str(frame.loc[frame["regime"] == "oos", "date"].max().date())
        metric_rows.append(
            {
                "ticker": ticker,
                "model_hash": model_hash,
                "origin_train_label": split["label"],
                "regime": regime,
                "period_start": period_start,
                "period_end": period_end,
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

    span = domain_high - domain_low
    fallback_by_feature = Counter(event["feature"] for event in ga.fallback_events if event.get("applied"))
    bounds_rows: list[dict[str, Any]] = []
    for index, feature in enumerate(FEATURES):
        width = float(best.high[index] - best.low[index])
        bounds_rows.append(
            {
                "ticker": ticker,
                "model_hash": model_hash,
                "origin_train_label": split["label"],
                "feature": feature,
                "feature_definition": FEATURE_DEFINITIONS[feature],
                "domain_min_origin_train": float(domain_low[index]),
                "domain_max_origin_train": float(domain_high[index]),
                "low_norm": float(best.low[index]),
                "high_norm": float(best.high[index]),
                "width_norm": width,
                "low_value": float(domain_low[index] + best.low[index] * span[index]),
                "high_value": float(domain_low[index] + best.high[index] * span[index]),
                "finite_low": bool(math.isfinite(float(best.low[index]))),
                "finite_high": bool(math.isfinite(float(best.high[index]))),
                "bilateral": bool(best.high[index] > best.low[index]),
                "min_width_pass": bool(width + 1e-12 >= cfg.min_width_norm),
                "near_full_noise_gene": bool(width >= cfg.max_near_full_width_norm),
                "fallback_applied_any_generation": bool(fallback_by_feature[feature]),
                "fallback_event_count": int(fallback_by_feature[feature]),
                "all_feature_and": True,
            }
        )

    training_rows = []
    for row in ga.history:
        training_rows.append(
            {
                "ticker": ticker,
                "model_hash": model_hash,
                "origin_train_label": split["label"],
                "train_start": split["train_start"],
                "train_end": split["train_end"],
                "seed": ticker_seed(ticker, split_index),
                **row,
                "generations_run": ga.generations_run,
                "population": cfg.population,
                "configured_generations": cfg.generations,
                "patience": cfg.patience,
                "train_rows": len(origin_train),
                "train_min_sample_gate": train_floor,
                "min_width_norm": cfg.min_width_norm,
                "strict_all_feature_and": True,
                "weighted_sum_path": False,
            }
        )

    survivor_row = {
        "ticker": ticker,
        "model_hash": model_hash,
        "origin_train_label": split["label"],
        "status": "SURVIVOR" if survivor else "REJECTED",
        "survivor": survivor,
        "train_gate": train_gate,
        "stress_gate": stress_gate,
        "oos_gate": oos_gate,
        "valid_bilateral_gene": valid,
        "gene_validation_reason": valid_reason,
        "train_fitness": best.fitness,
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
        "domain_low_json": {feature: float(domain_low[i]) for i, feature in enumerate(FEATURES)},
        "domain_high_json": {feature: float(domain_high[i]) for i, feature in enumerate(FEATURES)},
        "reject_reasons": ([] if train_gate else ["train_gate"]) + stress_reasons + oos_reasons,
    }

    return {
        "split": split,
        "split_index": split_index,
        "ga": ga,
        "best": best,
        "domain_low": domain_low,
        "domain_high": domain_high,
        "model_hash": model_hash,
        "training_rows": training_rows,
        "bounds_rows": bounds_rows,
        "fallback_rows": [
            {"ticker": ticker, "model_hash": model_hash, "origin_train_label": split["label"], **event}
            for event in ga.fallback_events
        ],
        "metric_rows": metric_rows,
        "survivor_row": survivor_row,
        "overfit_row": {
            "ticker": ticker,
            "model_hash": model_hash,
            "origin_train_label": split["label"],
            "survivor": survivor,
            "train_precision": train_metrics["precision"],
            "stress_precision": metrics_by_regime["stress"]["precision"],
            "oos_precision": metrics_by_regime["oos"]["precision"],
            "train_to_stress_precision_gap": float(train_metrics["precision"] - metrics_by_regime["stress"]["precision"]),
            "train_to_oos_precision_gap": float(train_metrics["precision"] - metrics_by_regime["oos"]["precision"]),
            "strict_all_feature_and": True,
            "weighted_sum_path_present": False,
            "bilateral_gene_pass": valid,
            "all_min_width_pass": all(row["min_width_pass"] for row in bounds_rows),
            "near_full_gene_count": sum(row["near_full_noise_gene"] for row in bounds_rows),
            "thin_sample_rejected": survivor_row["thin_sample_rejected"],
        },
    }


def method_rows_for_champion(ticker: str, frame: pd.DataFrame, candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    best = candidate["best"]
    domain_low = candidate["domain_low"]
    domain_high = candidate["domain_high"]
    model_hash = candidate["model_hash"]
    survivor = bool(candidate["survivor_row"]["survivor"])
    comparison_rows: list[dict[str, Any]] = []
    whipsaw_rows: list[dict[str, Any]] = []
    holding_rows: list[dict[str, Any]] = []

    for regime in ["train", "stress", "oos"]:
        subset = frame[frame["regime"] == regime].copy().reset_index(drop=True)
        x = normalize(subset, domain_low, domain_high)
        mask = individual_mask(best, x)
        scores = probability_scores(mask, best.pass_probability)
        off_metrics, off_trades = rolling_target_backtest(
            subset,
            scores,
            best.decision_threshold,
            target_horizon_sessions=HORIZON_SESSIONS,
            early_take_profit=False,
            take_profit_pct=TARGET_PCT,
            round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        )
        on_metrics, on_trades = rolling_target_backtest(
            subset,
            scores,
            best.decision_threshold,
            target_horizon_sessions=HORIZON_SESSIONS,
            early_take_profit=True,
            take_profit_pct=TARGET_PCT,
            round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        )
        fixed_metrics, fixed_trades = fixed_two_day_backtest(
            subset,
            scores,
            best.decision_threshold,
            round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        )
        methods = [
            ("rolling_target_2_sessions_tp_off", off_metrics, off_trades, False),
            ("rolling_target_2_sessions_tp_on", on_metrics, on_trades, True),
            ("fixed_2_sessions", fixed_metrics, fixed_trades, False),
        ]
        for method, metrics, trades, early_tp in methods:
            comparison_rows.append(
                {
                    "ticker": ticker,
                    "model_hash": model_hash,
                    "champion_origin_train_label": candidate["split"]["label"],
                    "champion_selected_by": "highest_origin_train_fitness_only",
                    "regime": regime,
                    "method": method,
                    "early_take_profit": early_tp,
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
                    "champion_origin_train_label": candidate["split"]["label"],
                    "regime": regime,
                    "survivor": survivor,
                    "decision_threshold": best.decision_threshold,
                    **whipsaw_statistics(subset, scores, best.decision_threshold, trades, method=method),
                }
            )
            counts = Counter((int(t["holding_sessions"]), str(t.get("exit_reason", "UNKNOWN"))) for t in trades)
            for (holding, reason), count in sorted(counts.items()):
                holding_rows.append(
                    {
                        "ticker": ticker,
                        "model_hash": model_hash,
                        "champion_origin_train_label": candidate["split"]["label"],
                        "regime": regime,
                        "method": method,
                        "holding_sessions": holding,
                        "exit_reason": reason,
                        "trade_count": count,
                        "trade_share": count / len(trades) if trades else 0.0,
                    }
                )
    return comparison_rows, whipsaw_rows, holding_rows


def train_symbol_worker(payload: dict[str, Any]) -> dict[str, Any]:
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    ticker = str(payload["ticker"])
    input_path = Path(payload["input_path"])
    output_path = Path(payload["output_path"])
    pid = os.getpid()
    process_name = mp.current_process().name
    try:
        frame = pd.read_csv(input_path)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame = frame.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        cfg = IntervalGAConfig()
        candidates: list[dict[str, Any]] = []
        for split_index, split in enumerate(TRAIN_SPLITS, 1):
            origin_train = split_frame(frame, split)
            if len(origin_train) < 100:
                raise RuntimeError(f"INSUFFICIENT_DATA {split['label']} rows={len(origin_train)}")
            domain_low, domain_high = fit_domain(origin_train)
            x_train = normalize(origin_train, domain_low, domain_high)
            y_train = origin_train["label_2d3pct"].to_numpy(int)
            ga = train_interval_ga(
                x_train,
                y_train,
                FEATURES,
                seed=ticker_seed(ticker, split_index),
                config=cfg,
            )
            candidates.append(
                evaluate_candidate(
                    ticker=ticker,
                    frame=frame,
                    split=split,
                    split_index=split_index,
                    domain_low=domain_low,
                    domain_high=domain_high,
                    ga=ga,
                    cfg=cfg,
                )
            )

        champion = max(candidates, key=lambda row: float(row["best"].fitness))
        comparison_rows, whipsaw_rows, holding_rows = method_rows_for_champion(ticker, frame, champion)
        result = {
            "status": "OK",
            "ticker": ticker,
            "champion_model_hash": champion["model_hash"],
            "champion_origin_train_label": champion["split"]["label"],
            "training_rows": [item for candidate in candidates for item in candidate["training_rows"]],
            "bounds_rows": [item for candidate in candidates for item in candidate["bounds_rows"]],
            "fallback_rows": [item for candidate in candidates for item in candidate["fallback_rows"]],
            "metric_rows": [item for candidate in candidates for item in candidate["metric_rows"]],
            "survivor_rows": [candidate["survivor_row"] for candidate in candidates],
            "comparison_rows": comparison_rows,
            "whipsaw_rows": whipsaw_rows,
            "holding_rows": holding_rows,
            "overfit_rows": [candidate["overfit_row"] for candidate in candidates],
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
        "seed_scheme": "sha256(ticker, split_index)",
        "train_split_count": len(TRAIN_SPLITS),
        "population_per_split": IntervalGAConfig().population,
        "generations_per_split": IntervalGAConfig().generations,
        "patience_per_split": IntervalGAConfig().patience,
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


def merge_worker_results(worker_returns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    merged = {
        "training": [],
        "bounds": [],
        "fallback": [],
        "metrics": [],
        "survivors": [],
        "comparison": [],
        "whipsaw": [],
        "holding": [],
        "overfit": [],
        "parallel": [],
        "errors": [],
    }
    for returned in worker_returns:
        data = json.loads(Path(returned["output_path"]).read_text(encoding="utf-8"))
        merged["parallel"].append(data.get("parallel_row", returned.get("parallel_row", {})))
        if data.get("status") != "OK":
            merged["errors"].append({"ticker": data.get("ticker"), "status": data.get("status"), "error": data.get("error", "UNKNOWN")})
            continue
        for target, source in [
            ("training", "training_rows"),
            ("bounds", "bounds_rows"),
            ("fallback", "fallback_rows"),
            ("metrics", "metric_rows"),
            ("survivors", "survivor_rows"),
            ("comparison", "comparison_rows"),
            ("whipsaw", "whipsaw_rows"),
            ("holding", "holding_rows"),
            ("overfit", "overfit_rows"),
        ]:
            merged[target].extend(data.get(source, []))
    return merged


def label_distribution(feature_set: pd.DataFrame) -> list[dict[str, Any]]:
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


def ga_config_rows() -> list[dict[str, Any]]:
    cfg = IntervalGAConfig()
    rows = [
        {"item": "population", "original_stage2": 100, "rerun_copy": cfg.population, "match": cfg.population == 100, "source": "scripts/research/run_stage2.py POPULATION"},
        {"item": "generations", "original_stage2": 50, "rerun_copy": cfg.generations, "match": cfg.generations == 50, "source": "scripts/research/run_stage2.py GENERATIONS"},
        {"item": "patience", "original_stage2": 15, "rerun_copy": cfg.patience, "match": cfg.patience == 15, "source": "scripts/research/run_stage2.py PATIENCE"},
        {"item": "train_split_count", "original_stage2": 3, "rerun_copy": len(TRAIN_SPLITS), "match": len(TRAIN_SPLITS) == 3, "source": "scripts/research/run_stage2.py TRAIN_SPLITS"},
    ]
    for index, split in enumerate(TRAIN_SPLITS, 1):
        rows.extend(
            [
                {"item": f"train_{index}_start", "original_stage2": split["train_start"], "rerun_copy": split["train_start"], "match": True, "source": "TRAIN_SPLITS"},
                {"item": f"train_{index}_end", "original_stage2": split["train_end"], "rerun_copy": split["train_end"], "match": True, "source": "TRAIN_SPLITS"},
            ]
        )
    return rows


def hhi_by_symbol(survivors: list[dict[str, Any]]) -> float | None:
    by_ticker: dict[str, float] = defaultdict(float)
    for row in survivors:
        if row.get("survivor"):
            by_ticker[str(row["ticker"])] += float(row.get("oos_passed_count", 0))
    total = sum(by_ticker.values())
    if total <= 0:
        return None
    return float(sum((value / total) ** 2 for value in by_ticker.values()))


def aggregate_comparison(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    out: list[dict[str, Any]] = []
    numeric = [
        "trade_count",
        "win_rate",
        "avg_return_pct",
        "compounded_return_pct",
        "max_drawdown_pct",
        "avg_holding_sessions",
        "max_holding_sessions",
        "early_take_profit_count",
        "target_date_exit_count",
        "mean_target_extension_count",
    ]
    for (regime, method), group in frame.groupby(["regime", "method"], sort=True):
        row: dict[str, Any] = {
            "ticker": "ALL_50_MEAN",
            "model_hash": "AGGREGATE",
            "champion_origin_train_label": "MIXED",
            "champion_selected_by": "highest_origin_train_fitness_only",
            "regime": regime,
            "method": method,
            "early_take_profit": bool(group["early_take_profit"].iloc[0]),
            "survivor": bool(group["survivor"].any()),
            "decision_threshold": float(group["decision_threshold"].mean()),
            "pass_probability": float(group["pass_probability"].mean()),
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        }
        for column in numeric:
            row[column] = float(group[column].mean())
        row["median_return_pct"] = float(group["median_return_pct"].mean())
        row["median_holding_sessions"] = float(group["median_holding_sessions"].mean())
        row["p95_holding_sessions"] = float(group["p95_holding_sessions"].mean())
        row["open_at_period_end_count"] = float(group["open_at_period_end_count"].mean())
        row["max_target_extension_count"] = int(group["max_target_extension_count"].max())
        out.append(row)
    return out


def previous_oos_whipsaw_mean() -> float | None:
    path = PREVIOUS_RUN_DIR / "whipsaw_stats.csv"
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    subset = frame[frame["regime"] == "oos"]
    return float(subset["whipsaw_rate"].mean()) if len(subset) else None


def integrity_probes() -> dict[str, Any]:
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
        "strict_and_compensation_blocked": int(compensation_mask.sum()) == 0,
    }


def write_readout(
    *,
    selected: list[dict[str, Any]],
    feature_set: pd.DataFrame,
    merged: dict[str, list[dict[str, Any]]],
    verdict: str,
    fail_reasons: list[str],
    hhi: float | None,
    previous_whipsaw: float | None,
    current_whipsaw: float | None,
    elapsed: float,
) -> None:
    survivor_candidates = [row for row in merged["survivors"] if row.get("survivor")]
    survivor_tickers = sorted({row["ticker"] for row in survivor_candidates})
    comparison = pd.DataFrame(merged["comparison"])
    oos_means: dict[str, dict[str, float]] = {}
    if not comparison.empty:
        for method, group in comparison[comparison["regime"] == "oos"].groupby("method"):
            oos_means[str(method)] = {
                "avg_return_pct": float(group["avg_return_pct"].mean()),
                "compounded_return_pct": float(group["compounded_return_pct"].mean()),
                "max_drawdown_pct": float(group["max_drawdown_pct"].mean()),
                "avg_holding_sessions": float(group["avg_holding_sessions"].mean()),
                "max_holding_sessions": float(group["max_holding_sessions"].max()),
            }
    lines = [
        "# Stage2/3 rolling 재발견 재실행 — 목표일 청산 + 원본 GA 크기",
        "",
        f"- 판정: **{verdict}**",
        f"- 실행 시각(UTC): {utc_now()}",
        f"- 총 실행시간: {elapsed:.2f}초",
        f"- 종목: {len(selected)}개, 직전 symbol_list.csv 정확히 재사용",
        f"- 일별 평가 행: {len(feature_set):,}",
        f"- 학습 후보: {len(merged['survivors'])}개 (50종목 × 3 train split)",
        f"- survivor 후보: {len(survivor_candidates)}개 / survivor 종목: {len(survivor_tickers)}개",
        f"- survivor 종목: {', '.join(survivor_tickers) if survivor_tickers else '없음'}",
        f"- survivor 신호 HHI: {hhi if hhi is not None else 'INSUFFICIENT_DATA'}",
        "",
        "## 핵심 결함 수정 확인",
        "",
        "- strict-AND 점수 붕괴 즉시 청산 조건을 제거했다.",
        "- 진입일 목표를 2거래일 뒤로 설정하고, 보유일에 유효 점수가 나오면 그날+2로 목표를 연장한다.",
        "- 점수가 끊기면 기존 목표를 유지하며 마지막 유효 목표일까지 보유한다.",
        "- TP OFF는 목표일까지 보유하고, TP ON은 D0 high가 진입가+3%에 닿으면 목표가격으로 즉시 익절한다.",
        "- GA는 각 종목에서 train_1/train_2/train_3을 독립 실행하며 각 실행은 population 100, generation 50, patience 15다.",
        "- 비교용 champion은 stress/OOS를 보지 않고 origin-train fitness만으로 종목당 1개 선정했다.",
        "",
        "## 휩쏘 변화",
        "",
        f"- 직전 OOS 평균 1세션 이하 비율: {previous_whipsaw if previous_whipsaw is not None else 'UNAVAILABLE'}",
        f"- 이번 OOS 목표일 TP OFF 평균 1세션 이하 비율: {current_whipsaw if current_whipsaw is not None else 'UNAVAILABLE'}",
        f"- 감소폭: {(previous_whipsaw-current_whipsaw) if previous_whipsaw is not None and current_whipsaw is not None else 'UNAVAILABLE'}",
        "- TP OFF에서 정상 목표일 청산은 최소 2세션이다. 0~1세션 거래가 남는다면 구간말 강제평가뿐이다.",
        "",
        "## OOS 청산 방식 비교 — 50종목 평균",
        "",
    ]
    for method in ["rolling_target_2_sessions_tp_off", "rolling_target_2_sessions_tp_on", "fixed_2_sessions"]:
        metrics = oos_means.get(method)
        if not metrics:
            lines.append(f"- {method}: INSUFFICIENT_DATA")
            continue
        lines.append(
            f"- {method}: 거래당 {metrics['avg_return_pct']:.4f}%, 복리 {metrics['compounded_return_pct']:.4f}%, "
            f"MDD {metrics['max_drawdown_pct']:.4f}%, 평균보유 {metrics['avg_holding_sessions']:.3f}, 최장 {metrics['max_holding_sessions']:.0f}"
        )
    lines.extend(["", "## 판정 사유", ""])
    lines.extend([f"- {reason}" for reason in fail_reasons] if fail_reasons else ["- 청산·GA·병렬·게이트·무결성 검증 통과"])
    lines.extend(
        [
            "",
            "## 해석",
            "",
            "- survivor가 나오면 직전 survivor 0에는 축소 탐색량의 영향이 있었다고 판정한다.",
            "- 원본 크기 3-split 탐색 후에도 survivor가 0이면 탐색부족보다 train→stress/OOS 일반화 실패가 주원인이다.",
            "- [추정] 거래수 게이트는 train max(20, 2%), stress/OOS max(8, 1.5%)를 유지했다.",
            "- D0 high/low/open/close는 체결·익절 계산 전용이며 12개 GA feature에는 포함되지 않는다.",
            "- STK_gap_d0, ETF_gap_d0, flow, order_book는 사용하지 않았다.",
        ]
    )
    (OUT_DIR / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_pilot() -> dict[str, Any]:
    started = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    WORKER_DIR.mkdir(parents=True, exist_ok=True)

    selected = load_previous_symbols()
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
    write_csv(OUT_DIR / "label_distribution.csv", label_distribution(feature_set))
    write_csv(OUT_DIR / "universe_errors.csv", universe_errors)
    write_csv(OUT_DIR / "ga_config_check.csv", ga_config_rows())

    tasks: list[dict[str, Any]] = []
    for row in selected:
        ticker = str(row["ticker"])
        ticker_frame = feature_set[feature_set["ticker"] == ticker].copy()
        input_path = WORKER_DIR / f"input_{ticker}.csv"
        output_path = WORKER_DIR / f"result_{ticker}.json"
        ticker_frame.to_csv(input_path, index=False)
        tasks.append({"ticker": ticker, "input_path": str(input_path), "output_path": str(output_path)})

    context = mp.get_context("spawn")
    with context.Pool(processes=WORKERS) as pool:
        worker_returns = pool.map(train_symbol_worker, tasks)
    merged = merge_worker_results(worker_returns)

    write_csv(OUT_DIR / "training_log.csv", merged["training"])
    write_csv(OUT_DIR / "gene_bounds_check.csv", merged["bounds"])
    write_csv(OUT_DIR / "parallel_run_log.csv", merged["parallel"])
    write_csv(OUT_DIR / "upper_bound_fallback_log.csv", merged["fallback"])
    write_csv(OUT_DIR / "per_regime_metrics.csv", merged["metrics"])
    write_csv(OUT_DIR / "survivor_summary.csv", merged["survivors"])
    comparison_rows = merged["comparison"] + aggregate_comparison(merged["comparison"])
    write_csv(OUT_DIR / "exit_method_comparison.csv", comparison_rows)
    write_csv(OUT_DIR / "whipsaw_stats.csv", merged["whipsaw"])
    write_csv(OUT_DIR / "holding_period_dist.csv", merged["holding"])

    probes = integrity_probes()
    survivor_candidates = [row for row in merged["survivors"] if row.get("survivor")]
    survivor_tickers = sorted({row["ticker"] for row in survivor_candidates})
    hhi = hhi_by_symbol(merged["survivors"])
    total_bounds = len(merged["bounds"])
    valid_bounds = sum(bool(row.get("bilateral")) and bool(row.get("min_width_pass")) for row in merged["bounds"])
    unique_pids = len({row.get("pid") for row in merged["parallel"] if row.get("pid")})
    previous_whipsaw = previous_oos_whipsaw_mean()
    current_rows = [
        row for row in merged["whipsaw"]
        if row.get("regime") == "oos" and row.get("method") == "rolling_target_2_sessions_tp_off"
    ]
    current_whipsaw = float(np.mean([row["whipsaw_rate"] for row in current_rows])) if current_rows else None

    global_overfit = {
        "ticker": "ALL_CANDIDATES",
        "model_hash": "GLOBAL",
        "origin_train_label": "ALL_SPLITS",
        "survivor": bool(survivor_candidates),
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
        "candidate_count": len(merged["survivors"]),
        "survivor_candidate_count": len(survivor_candidates),
        "survivor_symbol_count": len(survivor_tickers),
        "worker_error_count": len(merged["errors"]),
        "configured_workers": WORKERS,
        "observed_unique_worker_pids": unique_pids,
        "previous_oos_whipsaw_mean": previous_whipsaw,
        "current_oos_target_tp_off_whipsaw_mean": current_whipsaw,
    }
    write_csv(OUT_DIR / "overfit_check.csv", merged["overfit"] + [global_overfit])

    fail_reasons: list[str] = []
    if merged["errors"]:
        fail_reasons.append(f"종목 worker 오류 {len(merged['errors'])}건")
    if len(merged["survivors"]) != 50 * len(TRAIN_SPLITS):
        fail_reasons.append(f"완료 후보가 150개가 아님: {len(merged['survivors'])}")
    if total_bounds != 50 * len(TRAIN_SPLITS) * len(FEATURES) or valid_bounds != total_bounds:
        fail_reasons.append(f"양방향/최소폭 gene 검증 실패: {valid_bounds}/{total_bounds}")
    if not probes["strict_and_compensation_blocked"] or not probes["open_gene_rejected"] or not probes["narrow_gene_rejected"]:
        fail_reasons.append("strict-AND 또는 gene 무결성 probe 실패")
    if unique_pids > WORKERS:
        fail_reasons.append(f"worker PID가 6개를 초과: {unique_pids}")
    if not survivor_candidates:
        fail_reasons.append("원본 크기 3-split GA 후에도 stress·OOS 이중 게이트 survivor가 0개 — 일반화 실패")
    if previous_whipsaw is not None and current_whipsaw is not None and current_whipsaw >= previous_whipsaw:
        fail_reasons.append(f"목표일 TP OFF 휩쏘가 감소하지 않음: {previous_whipsaw:.6f} → {current_whipsaw:.6f}")
    normal_short = sum(
        int(row.get("one_session_whipsaw_count", 0))
        for row in current_rows
    )
    if normal_short > 0:
        fail_reasons.append(f"목표일 방식에서 정상 1세션 청산 잔존: {normal_short}건")

    verdict = "PILOT_PASS" if not fail_reasons else "PILOT_FAIL"
    elapsed = time.perf_counter() - started
    write_readout(
        selected=selected,
        feature_set=feature_set,
        merged=merged,
        verdict=verdict,
        fail_reasons=fail_reasons,
        hhi=hhi,
        previous_whipsaw=previous_whipsaw,
        current_whipsaw=current_whipsaw,
        elapsed=elapsed,
    )

    summary = {
        "verdict": verdict,
        "fail_reasons": fail_reasons,
        "selected_symbols": [row["ticker"] for row in selected],
        "feature_rows": len(feature_set),
        "candidate_count": len(merged["survivors"]),
        "survivor_candidate_count": len(survivor_candidates),
        "survivor_symbol_count": len(survivor_tickers),
        "survivor_symbols": survivor_tickers,
        "worker_error_count": len(merged["errors"]),
        "worker_errors": merged["errors"],
        "configured_workers": WORKERS,
        "observed_unique_worker_pids": unique_pids,
        "ga_population": IntervalGAConfig().population,
        "ga_generations": IntervalGAConfig().generations,
        "ga_patience": IntervalGAConfig().patience,
        "train_split_count": len(TRAIN_SPLITS),
        "gene_bounds_valid": valid_bounds,
        "gene_bounds_total": total_bounds,
        "hhi": hhi,
        "previous_oos_whipsaw_mean": previous_whipsaw,
        "current_oos_target_tp_off_whipsaw_mean": current_whipsaw,
        "elapsed_seconds": elapsed,
        "generated_at_utc": utc_now(),
    }
    (OUT_DIR / "pilot_summary.json").write_text(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2/3 rolling rediscovery rerun")
    parser.add_argument("--workers", type=int, default=WORKERS, help="must equal 6")
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
