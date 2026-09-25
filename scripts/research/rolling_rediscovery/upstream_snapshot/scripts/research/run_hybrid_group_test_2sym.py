#!/usr/bin/env python3
"""AAP/POWI four-group hybrid interval-GA research test.

Research-only copy.  It adds no live imports or deployment writes.  The runner
uses frozen OHLCV, D-1-complete features, the original Stage2 GA search scale,
three origin-train splits, stress/OOS validation, and the existing rolling
2-session target-date exit with early take profit disabled.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ISOLATED_ROOT = Path(__file__).resolve().parents[2]
KINGMAKER_ROOT = Path(__file__).resolve().parents[6]
if str(ISOLATED_ROOT) not in sys.path:
    sys.path.insert(0, str(ISOLATED_ROOT))

import numpy as np
import pandas as pd

from engine.core.indicators import calc_indicators
from engine.learning.execution_mode_backtest import (
    classification_metrics,
    probability_scores,
    rolling_target_backtest,
)
from engine.learning.genetic import IntervalGAConfig
from engine.learning.grouped_genetic import (
    GroupedIntervalIndividual,
    group_count_details,
    train_grouped_interval_ga,
    validate_grouped_gene,
)

OUT_DIR = KINGMAKER_ROOT / "data/_system/analysis/hybrid_group_test_2sym_20260712"
SOURCE_PILOT_DIR = KINGMAKER_ROOT / "data/_system/analysis/stage2_3_rediscovery_pilot_20260712"
STRICT_DETAIL_DIR = KINGMAKER_ROOT / "data/_system/analysis/pilot_survivor_detail_20260712"
TARGET_TICKERS = ["AAP", "POWI"]
SELECTION_SEED = 20260712
TARGET_PCT = 3.0
HORIZON_SESSIONS = 2
ROUND_TRIP_COST_BPS = 10.0
G3_MEMBER_PERCENTILE_FLOOR = 0.15  # [추정] fixed from grouping analysis.

START_DATE = pd.Timestamp("2020-01-01")
STRESS_END = pd.Timestamp("2022-06-30")
TRAIN_END = pd.Timestamp("2025-06-30")
TRAIN_SPLITS: list[dict[str, str]] = [
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
]

GROUPS: OrderedDict[str, list[str]] = OrderedDict(
    [
        (
            "G1_PULLBACK",
            [
                "pullback_from_high5_pct",
                "fade_after_surge_score",
                "inv_close_pos5",
                "inv_ret_d1_pct",
            ],
        ),
        (
            "G2_VOLATILITY",
            [
                "single_up_day5_pct",
                "atr14_pct",
                "realized_vol20_pct",
                "bb_width20_pct",
            ],
        ),
        (
            "G3_RANGE_EXPANSION",
            [
                "true_range_d1_pct",
                "range_vs_atr14",
                "range_vs_range20",
            ],
        ),
        (
            "G4_VOLUME_CONFIRMATION",
            [
                "volume_ratio5_prior",
                "volume_ratio20_prior",
                "volume_chg1_pct",
            ],
        ),
    ]
)
FEATURES = [feature for members in GROUPS.values() for feature in members]
GROUP_INDEXES = [
    np.array([FEATURES.index(feature) for feature in members], dtype=int)
    for members in GROUPS.values()
]
GROUP_NAMES = list(GROUPS)
G3_GROUP_INDEX = GROUP_NAMES.index("G3_RANGE_EXPANSION")
DEAD_FEATURES_REMOVED = [
    "ret_d5_pct",
    "ret_d4_pct",
    "ret_d3_pct",
    "ret_d2_pct",
    "cumulative_ret5_pct",
    "up_days5",
    "days_since_high5",
]
FEATURE_DEFINITIONS = {
    "pullback_from_high5_pct": "D-5~D-1 최고가 대비 D-1 close 하락률(%)",
    "fade_after_surge_score": "초반 최대상승률 + 최근 2일 음의 누적수익률 절대값",
    "inv_close_pos5": "1 - D-1 close의 D-5~D-1 range 위치",
    "inv_ret_d1_pct": "-(D-2 close 대비 D-1 close 수익률 %)",
    "single_up_day5_pct": "D-5~D-1 최대 단일 일수익률(%)",
    "atr14_pct": "100*ATR14[D-1]/Close[D-1]",
    "realized_vol20_pct": "D-20~D-1 일수익률 sample std(%)",
    "bb_width20_pct": "100*(BB upper-BB lower)/BB middle at D-1",
    "true_range_d1_pct": "D-1 true range / D-1 close(%)",
    "range_vs_atr14": "D-1 high-low / ATR14[D-1]",
    "range_vs_range20": "D-1 range% / D-21~D-2 mean range%",
    "volume_ratio5_prior": "D-1 volume / D-6~D-2 mean volume",
    "volume_ratio20_prior": "D-1 volume / D-21~D-2 mean volume",
    "volume_chg1_pct": "100*(D-1 volume/D-2 volume-1)",
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
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray)):
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
                if isinstance(value, (dict, list, tuple, set, np.ndarray)):
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
    return frame.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def regime_for_date(value: pd.Timestamp) -> str | None:
    day = pd.Timestamp(value).normalize()
    if day < START_DATE:
        return None
    if day <= STRESS_END:
        return "stress"
    if day <= TRAIN_END:
        return "train"
    return "oos"


def _safe_div(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator == 0:
        return float("nan")
    return float(numerator / denominator)


def _path_features(frame: pd.DataFrame, entry_idx: int) -> dict[str, float] | None:
    if entry_idx < 6:
        return None
    prior6 = frame.iloc[entry_idx - 6 : entry_idx]
    path5 = frame.iloc[entry_idx - 5 : entry_idx]
    closes = prior6["Close"].to_numpy(float)
    highs = path5["High"].to_numpy(float)
    lows = path5["Low"].to_numpy(float)
    if len(closes) != 6 or not np.isfinite(closes).all() or (closes <= 0).any():
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
        "pullback_from_high5_pct": float(max(0.0, 100.0 * (high5 / close_d1 - 1.0))),
        "fade_after_surge_score": float(max(0.0, first3_max) + max(0.0, -last2_ret)),
        "inv_close_pos5": float(1.0 - close_pos),
        "inv_ret_d1_pct": float(-daily[4]),
        "single_up_day5_pct": float(np.max(daily)),
    }


def build_feature_set(symbol_rows: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in symbol_rows:
        ticker = str(item["ticker"])
        source = KINGMAKER_ROOT / str(item["source_path"])
        try:
            frame = read_ohlcv(source)
            indicators = calc_indicators(frame.copy())
        except Exception as exc:
            errors.append({"ticker": ticker, "status": "UNRECOVERABLE", "error": str(exc)})
            continue

        close = frame["Close"].astype(float)
        high = frame["High"].astype(float)
        low = frame["Low"].astype(float)
        volume = frame["Volume"].astype(float)
        returns_pct = close.pct_change() * 100.0
        range_pct = 100.0 * (high - low) / close.replace(0, np.nan)
        previous_close = close.shift(1)
        true_range_pct = 100.0 * pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1) / close.replace(0, np.nan)

        # 21 completed prior sessions are required by realized vol20 and the
        # D-21~D-2 range/volume denominators.  D0 and future rows are labels only.
        for idx in range(21, len(frame) - HORIZON_SESSIONS):
            day = pd.Timestamp(frame.index[idx]).normalize()
            regime = regime_for_date(day)
            if regime is None:
                continue
            path = _path_features(frame, idx)
            if path is None:
                continue
            j = idx - 1
            atr = float(indicators.iloc[j]["ATR"])
            atr_pct = float(indicators.iloc[j]["ATR_pct"])
            bb_width_pct = float(indicators.iloc[j]["BB_width"] * 100.0)
            rv20 = float(returns_pct.iloc[j - 19 : j + 1].std(ddof=1))
            current_range_pct = float(range_pct.iloc[j])
            prior_range20 = float(range_pct.iloc[j - 20 : j].mean())
            current_volume = float(volume.iloc[j])
            prior_volume5 = float(volume.iloc[j - 5 : j].mean())
            prior_volume20 = float(volume.iloc[j - 20 : j].mean())
            previous_volume = float(volume.iloc[j - 1])

            features = {
                **path,
                "atr14_pct": atr_pct,
                "realized_vol20_pct": rv20,
                "bb_width20_pct": bb_width_pct,
                "true_range_d1_pct": float(true_range_pct.iloc[j]),
                "range_vs_atr14": _safe_div(float(high.iloc[j] - low.iloc[j]), atr),
                "range_vs_range20": _safe_div(current_range_pct, prior_range20),
                "volume_ratio5_prior": _safe_div(current_volume, prior_volume5),
                "volume_ratio20_prior": _safe_div(current_volume, prior_volume20),
                "volume_chg1_pct": 100.0 * (_safe_div(current_volume, previous_volume) - 1.0),
            }
            if not all(math.isfinite(float(features[name])) for name in FEATURES):
                errors.append(
                    {
                        "ticker": ticker,
                        "date": day.strftime("%Y-%m-%d"),
                        "status": "UNRECOVERABLE",
                        "error": "expanded D-1 feature nonfinite",
                    }
                )
                continue

            entry_open = float(frame.iloc[idx]["Open"])
            entry_high = float(frame.iloc[idx]["High"])
            entry_low = float(frame.iloc[idx]["Low"])
            entry_close = float(frame.iloc[idx]["Close"])
            future_high_1 = float(frame.iloc[idx + 1]["High"])
            future_high_2 = float(frame.iloc[idx + 2]["High"])
            prices = [entry_open, entry_high, entry_low, entry_close, future_high_1, future_high_2]
            if not all(math.isfinite(price) and price > 0 for price in prices):
                errors.append(
                    {
                        "ticker": ticker,
                        "date": day.strftime("%Y-%m-%d"),
                        "status": "UNRECOVERABLE",
                        "error": "D0/future price unavailable",
                    }
                )
                continue
            forward_max = 100.0 * (max(future_high_1, future_high_2) / entry_open - 1.0)
            rows.append(
                {
                    "ticker": ticker,
                    "date": day.strftime("%Y-%m-%d"),
                    "regime": regime,
                    "feature_cutoff": "D-1",
                    "feature_count": len(FEATURES),
                    "group_count": len(GROUPS),
                    "entry_open_d0": entry_open,
                    "entry_high_d0": entry_high,
                    "entry_low_d0": entry_low,
                    "entry_close_d0": entry_close,
                    "future_high_d1": future_high_1,
                    "future_high_d2": future_high_2,
                    "forward_max_return_pct": forward_max,
                    "label_2d3pct": int(forward_max >= TARGET_PCT),
                    **features,
                }
            )
    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values(["ticker", "date"]).reset_index(drop=True)
    return output, errors


def split_frame(frame: pd.DataFrame, split: dict[str, str]) -> pd.DataFrame:
    start = pd.Timestamp(split["train_start"])
    end = pd.Timestamp(split["train_end"])
    return frame[(frame["date"] >= start) & (frame["date"] <= end)].copy()


def fit_domain(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    values = frame[FEATURES].to_numpy(float)
    low = np.nanmin(values, axis=0)
    high = np.nanmax(values, axis=0)
    bad = ~np.isfinite(low) | ~np.isfinite(high)
    low[bad] = 0.0
    high[bad] = 1.0
    constant = (high - low) <= 1e-12
    high[constant] = low[constant] + 1.0
    return low, high


def normalize(frame: pd.DataFrame, low: np.ndarray, high: np.ndarray) -> np.ndarray:
    return (frame[FEATURES].to_numpy(float) - low) / np.maximum(high - low, 1e-12)


def train_min_pass(n: int) -> int:
    return max(20, int(math.ceil(max(0, n) * 0.02)))


def validation_min_pass(n: int) -> int:
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


def ticker_seed(ticker: str, split_index: int) -> int:
    raw = f"hybrid-group-test:{SELECTION_SEED}:{ticker}:split:{split_index}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)


def model_hash(
    ticker: str,
    split_label: str,
    domain_low: np.ndarray,
    domain_high: np.ndarray,
    individual: GroupedIntervalIndividual,
    g3_floor_norm: np.ndarray,
) -> str:
    payload = json.dumps(
        {
            "ticker": ticker,
            "split": split_label,
            "features": FEATURES,
            "groups": GROUPS,
            "domain_low": np.round(domain_low, 8).tolist(),
            "domain_high": np.round(domain_high, 8).tolist(),
            "low": np.round(individual.low, 8).tolist(),
            "high": np.round(individual.high, 8).tolist(),
            "group_thresholds": individual.group_thresholds.astype(int).tolist(),
            "g3_floor_norm": np.round(g3_floor_norm, 8).tolist(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def evaluate_candidate(
    ticker: str,
    frame: pd.DataFrame,
    split: dict[str, str],
    split_index: int,
    domain_low: np.ndarray,
    domain_high: np.ndarray,
    g3_floor_norm: np.ndarray,
    ga: Any,
    cfg: IntervalGAConfig,
) -> dict[str, Any]:
    best = ga.best
    valid, valid_reason = validate_grouped_gene(best, GROUP_INDEXES, cfg)
    candidate_hash = model_hash(ticker, split["label"], domain_low, domain_high, best, g3_floor_norm)
    origin_train = split_frame(frame, split)
    metrics_by_regime: dict[str, dict[str, Any]] = {}
    group_details_by_regime: dict[str, dict[str, Any]] = {}
    metric_rows: list[dict[str, Any]] = []
    threshold_rows: list[dict[str, Any]] = []

    subsets = [
        ("train", origin_train),
        ("stress", frame[frame["regime"] == "stress"].copy()),
        ("oos", frame[frame["regime"] == "oos"].copy()),
    ]
    for regime, subset in subsets:
        x = normalize(subset, domain_low, domain_high)
        mask, feature_pass, group_counts, group_pass = group_count_details(
            best,
            x,
            GROUP_INDEXES,
            g3_group_index=G3_GROUP_INDEX,
            g3_floor_norm=g3_floor_norm,
        )
        metrics = classification_metrics(subset["label_2d3pct"].to_numpy(int), mask)
        metrics_by_regime[regime] = metrics
        group_details_by_regime[regime] = {
            "subset": subset.reset_index(drop=True),
            "x": x,
            "mask": mask,
            "feature_pass": feature_pass,
            "group_counts": group_counts,
            "group_pass": group_pass,
        }
        for group_index, (group_name, members) in enumerate(GROUPS.items()):
            counts = group_counts[:, group_index]
            threshold_rows.append(
                {
                    "ticker": ticker,
                    "model_hash": candidate_hash,
                    "origin_train_label": split["label"],
                    "regime": regime,
                    "group": group_name,
                    "members": members,
                    "group_size": len(members),
                    "learned_threshold": int(best.group_thresholds[group_index]),
                    "threshold_valid": bool(1 <= best.group_thresholds[group_index] <= len(members)),
                    "group_passed_rows": int(group_pass[:, group_index].sum()),
                    "final_entry_rows": int(mask.sum()),
                    "count_distribution": {
                        str(value): int(np.sum(counts == value))
                        for value in range(len(members) + 1)
                    },
                    "g3_floor_percentile": G3_MEMBER_PERCENTILE_FLOOR if group_index == G3_GROUP_INDEX else None,
                    "g3_floor_norm": g3_floor_norm.tolist() if group_index == G3_GROUP_INDEX else None,
                }
            )

    train_metrics = metrics_by_regime["train"]
    train_floor = train_min_pass(int(train_metrics["signal_count"]))
    train_gate = bool(
        valid
        and train_metrics["passed_count"] >= train_floor
        and train_metrics["precision"] >= best.decision_threshold
    )
    stress_gate, stress_reasons, stress_precision_floor, stress_sample_floor = validation_gate(
        metrics_by_regime["stress"], float(train_metrics["precision"])
    )
    oos_gate, oos_reasons, oos_precision_floor, oos_sample_floor = validation_gate(
        metrics_by_regime["oos"], float(train_metrics["precision"])
    )
    survivor = bool(train_gate and stress_gate and oos_gate and valid)

    for regime in ["train", "stress", "oos"]:
        metrics = metrics_by_regime[regime]
        if regime == "train":
            gate = train_gate
            reasons = [] if gate else ["train_precision_or_sample_or_gene_gate"]
            precision_floor = best.decision_threshold
            sample_floor = train_floor
        elif regime == "stress":
            gate = stress_gate
            reasons = stress_reasons
            precision_floor = stress_precision_floor
            sample_floor = stress_sample_floor
        else:
            gate = oos_gate
            reasons = oos_reasons
            precision_floor = oos_precision_floor
            sample_floor = oos_sample_floor
        metric_rows.append(
            {
                "ticker": ticker,
                "model_hash": candidate_hash,
                "origin_train_label": split["label"],
                "regime": regime,
                **metrics,
                "precision_floor": precision_floor,
                "sample_floor": sample_floor,
                "passed_gate": gate,
                "gate_fail_reasons": reasons,
                "survivor": survivor,
            }
        )

    fallback_by_feature = Counter(
        event["feature"] for event in ga.fallback_events if event.get("applied")
    )
    span = domain_high - domain_low
    learned_rows: list[dict[str, Any]] = []
    for feature_index, feature in enumerate(FEATURES):
        group_name = next(name for name, members in GROUPS.items() if feature in members)
        width = float(best.high[feature_index] - best.low[feature_index])
        learned_rows.append(
            {
                "ticker": ticker,
                "model_hash": candidate_hash,
                "origin_train_label": split["label"],
                "gene_type": "FEATURE_INTERVAL",
                "group": group_name,
                "feature": feature,
                "feature_definition": FEATURE_DEFINITIONS[feature],
                "low_norm": float(best.low[feature_index]),
                "high_norm": float(best.high[feature_index]),
                "width_norm": width,
                "low_value": float(domain_low[feature_index] + best.low[feature_index] * span[feature_index]),
                "high_value": float(domain_low[feature_index] + best.high[feature_index] * span[feature_index]),
                "min_width_pass": bool(width + 1e-12 >= cfg.min_width_norm),
                "bilateral": bool(best.high[feature_index] > best.low[feature_index]),
                "fallback_applied_any_generation": bool(fallback_by_feature[feature]),
                "fallback_event_count": int(fallback_by_feature[feature]),
                "group_threshold": None,
                "group_size": len(GROUPS[group_name]),
                "g3_member_floor_norm": (
                    float(g3_floor_norm[list(GROUPS["G3_RANGE_EXPANSION"]).index(feature)])
                    if group_name == "G3_RANGE_EXPANSION"
                    else None
                ),
            }
        )
    for group_index, (group_name, members) in enumerate(GROUPS.items()):
        learned_rows.append(
            {
                "ticker": ticker,
                "model_hash": candidate_hash,
                "origin_train_label": split["label"],
                "gene_type": "GROUP_THRESHOLD",
                "group": group_name,
                "feature": "",
                "feature_definition": "unweighted feature-pass count required for group pass",
                "low_norm": None,
                "high_norm": None,
                "width_norm": None,
                "low_value": None,
                "high_value": None,
                "min_width_pass": None,
                "bilateral": None,
                "fallback_applied_any_generation": None,
                "fallback_event_count": None,
                "group_threshold": int(best.group_thresholds[group_index]),
                "group_size": len(members),
                "g3_member_floor_norm": g3_floor_norm.tolist() if group_index == G3_GROUP_INDEX else None,
            }
        )

    training_rows: list[dict[str, Any]] = []
    for history in ga.history:
        training_rows.append(
            {
                "ticker": ticker,
                "model_hash": candidate_hash,
                "origin_train_label": split["label"],
                "train_start": split["train_start"],
                "train_end": split["train_end"],
                "seed": ticker_seed(ticker, split_index),
                **history,
                "generations_run": ga.generations_run,
                "population": cfg.population,
                "configured_generations": cfg.generations,
                "patience": cfg.patience,
                "feature_count": len(FEATURES),
                "group_count": len(GROUPS),
                "unweighted_group_count": True,
                "group_between_and": True,
                "g3_member_percentile_floor": G3_MEMBER_PERCENTILE_FLOOR,
            }
        )

    survivor_row = {
        "ticker": ticker,
        "model_hash": candidate_hash,
        "origin_train_label": split["label"],
        "status": "SURVIVOR" if survivor else "REJECTED",
        "survivor": survivor,
        "train_gate": train_gate,
        "stress_gate": stress_gate,
        "oos_gate": oos_gate,
        "valid_gene": valid,
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
        "group_thresholds_json": {
            group: int(best.group_thresholds[index])
            for index, group in enumerate(GROUP_NAMES)
        },
        "g3_floor_norm_json": g3_floor_norm.tolist(),
        "genes_json": {
            feature: [float(best.low[index]), float(best.high[index])]
            for index, feature in enumerate(FEATURES)
        },
        "domain_low_json": {
            feature: float(domain_low[index]) for index, feature in enumerate(FEATURES)
        },
        "domain_high_json": {
            feature: float(domain_high[index]) for index, feature in enumerate(FEATURES)
        },
        "fallback_event_count": len(ga.fallback_events),
        "rejected_group_threshold_count": ga.rejected_group_threshold_count,
        "reject_reasons": ([] if train_gate else ["train_gate"]) + stress_reasons + oos_reasons,
        "selected_for_trade": False,
        "selection_rule": "",
    }
    return {
        "ticker": ticker,
        "split": split,
        "split_index": split_index,
        "model_hash": candidate_hash,
        "best": best,
        "domain_low": domain_low,
        "domain_high": domain_high,
        "g3_floor_norm": g3_floor_norm,
        "ga": ga,
        "training_rows": training_rows,
        "learned_rows": learned_rows,
        "threshold_rows": threshold_rows,
        "metric_rows": metric_rows,
        "survivor_row": survivor_row,
        "details_by_regime": group_details_by_regime,
    }


def select_trade_candidate(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    survivors = [candidate for candidate in candidates if candidate["survivor_row"]["survivor"]]
    if survivors:
        return max(survivors, key=lambda item: float(item["best"].fitness)), "highest_train_fitness_among_survivors"
    stress_pass = [candidate for candidate in candidates if candidate["survivor_row"]["stress_gate"]]
    if stress_pass:
        return max(stress_pass, key=lambda item: float(item["best"].fitness)), "NON_SURVIVOR_highest_train_fitness_among_stress_pass"
    return max(candidates, key=lambda item: float(item["best"].fitness)), "NON_SURVIVOR_highest_train_fitness"


def _group_state(
    row_index: int,
    feature_pass: np.ndarray,
    group_counts: np.ndarray,
    group_pass: np.ndarray,
    individual: GroupedIntervalIndividual,
) -> dict[str, Any]:
    passed_features = [FEATURES[index] for index in np.where(feature_pass[row_index])[0]]
    failed_features = [FEATURES[index] for index in np.where(~feature_pass[row_index])[0]]
    return {
        "group_counts": {
            group: int(group_counts[row_index, index]) for index, group in enumerate(GROUP_NAMES)
        },
        "group_thresholds": {
            group: int(individual.group_thresholds[index]) for index, group in enumerate(GROUP_NAMES)
        },
        "group_pass": {
            group: bool(group_pass[row_index, index]) for index, group in enumerate(GROUP_NAMES)
        },
        "passed_features": passed_features,
        "failed_features": failed_features,
        "total_feature_pass_count": int(feature_pass[row_index].sum()),
        "global_count_threshold": int(individual.group_thresholds.sum()),
        "global_count_pass": bool(
            int(feature_pass[row_index].sum()) >= int(individual.group_thresholds.sum())
        ),
        "hybrid_pass": bool(np.all(group_pass[row_index])),
    }


def trace_trades(
    ticker: str,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    details = candidate["details_by_regime"]["oos"]
    frame = details["subset"].copy().reset_index(drop=True)
    active = details["mask"]
    feature_pass = details["feature_pass"]
    group_counts = details["group_counts"]
    group_pass = details["group_pass"]
    best = candidate["best"]
    scores = probability_scores(active, best.pass_probability)
    performance, reference_trades = rolling_target_backtest(
        frame,
        scores,
        best.decision_threshold,
        target_horizon_sessions=HORIZON_SESSIONS,
        early_take_profit=False,
        take_profit_pct=TARGET_PCT,
        round_trip_cost_bps=ROUND_TRIP_COST_BPS,
    )

    traced: list[dict[str, Any]] = []
    entry_idx: int | None = None
    entry_price = 0.0
    target_idx: int | None = None
    initial_target_idx: int | None = None
    extensions: list[dict[str, Any]] = []

    def date_at(index: int) -> str:
        return str(pd.Timestamp(frame.iloc[index]["date"]).date()) if 0 <= index < len(frame) else "BEYOND_OOS_FRAME"

    def close_trade(exit_idx: int, reason: str, period_end: bool) -> None:
        nonlocal entry_idx, entry_price, target_idx, initial_target_idx, extensions
        assert entry_idx is not None and target_idx is not None and initial_target_idx is not None
        exit_price = float(frame.iloc[exit_idx]["entry_close_d0"])
        gross = 100.0 * (exit_price / entry_price - 1.0)
        net = gross - ROUND_TRIP_COST_BPS / 100.0
        holding_window = frame.iloc[entry_idx : exit_idx + 1]
        target_price = entry_price * (1.0 + TARGET_PCT / 100.0)
        hit = holding_window[holding_window["entry_high_d0"] >= target_price]
        plus3 = not hit.empty
        entry_state = _group_state(entry_idx, feature_pass, group_counts, group_pass, best)
        exit_state = _group_state(exit_idx, feature_pass, group_counts, group_pass, best)
        row: dict[str, Any] = {
            "ticker": ticker,
            "trade_no": len(traced) + 1,
            "model_hash": candidate["model_hash"],
            "origin_train_label": candidate["split"]["label"],
            "selection_rule": candidate["survivor_row"]["selection_rule"],
            "method": "rolling_target_2_sessions_tp_off",
            "entry_date": date_at(entry_idx),
            "entry_price": entry_price,
            "initial_target_date": date_at(initial_target_idx),
            "target_extension_count": len(extensions),
            "target_extension_history_json": extensions,
            "final_target_date": date_at(target_idx),
            "exit_date": date_at(exit_idx),
            "exit_price": exit_price,
            "exit_reason": reason,
            "period_end_exit": period_end,
            "holding_sessions": exit_idx - entry_idx,
            "gross_return_pct": gross,
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "net_return_pct": net,
            "plus3_reached_during_holding": plus3,
            "plus3_first_reach_date": str(pd.Timestamp(hit.iloc[0]["date"]).date()) if plus3 else "",
            "max_high_during_holding": float(holding_window["entry_high_d0"].max()),
            "entry_label_2d3pct": int(frame.iloc[entry_idx]["label_2d3pct"]),
            "entry_feature_passes_json": entry_state["passed_features"],
            "entry_feature_failures_json": entry_state["failed_features"],
            "entry_total_feature_pass_count": entry_state["total_feature_pass_count"],
            "entry_global_count_threshold": entry_state["global_count_threshold"],
            "entry_global_count_pass": entry_state["global_count_pass"],
            "entry_hybrid_pass": entry_state["hybrid_pass"],
            "exit_group_state_json": exit_state,
        }
        for group_index, group_name in enumerate(GROUP_NAMES):
            short = f"g{group_index + 1}"
            row[f"entry_{short}_count"] = int(group_counts[entry_idx, group_index])
            row[f"entry_{short}_threshold"] = int(best.group_thresholds[group_index])
            row[f"entry_{short}_pass"] = bool(group_pass[entry_idx, group_index])
            row[f"exit_{short}_count"] = int(group_counts[exit_idx, group_index])
            row[f"exit_{short}_pass"] = bool(group_pass[exit_idx, group_index])
        traced.append(row)
        entry_idx = None
        entry_price = 0.0
        target_idx = None
        initial_target_idx = None
        extensions = []

    for index in range(len(frame)):
        if entry_idx is None:
            if not active[index]:
                continue
            entry_idx = index
            entry_price = float(frame.iloc[index]["entry_open_d0"])
            target_idx = index + HORIZON_SESSIONS
            initial_target_idx = target_idx
            extensions = []
            continue
        assert target_idx is not None
        if active[index]:
            proposed = index + HORIZON_SESSIONS
            if proposed > target_idx:
                extensions.append(
                    {
                        "signal_date": date_at(index),
                        "old_target_date": date_at(target_idx),
                        "new_target_date": date_at(proposed),
                        "group_state": _group_state(index, feature_pass, group_counts, group_pass, best),
                    }
                )
                target_idx = proposed
        if index >= target_idx:
            close_trade(index, "TARGET_DATE_REACHED", False)

    if entry_idx is not None:
        close_trade(len(frame) - 1, "FORCED_PERIOD_END_MARK_TO_MARKET", True)

    if len(traced) != len(reference_trades):
        raise AssertionError(f"trade trace count mismatch {ticker}: {len(traced)} != {len(reference_trades)}")
    for traced_row, reference in zip(traced, reference_trades):
        if traced_row["entry_date"] != str(reference["entry_date"])[:10]:
            raise AssertionError(f"entry mismatch {ticker}")
        if traced_row["exit_date"] != str(reference["exit_date"])[:10]:
            raise AssertionError(f"exit mismatch {ticker}")
        if traced_row["holding_sessions"] != int(reference["holding_sessions"]):
            raise AssertionError(f"holding mismatch {ticker}")
        if abs(traced_row["net_return_pct"] - float(reference["return_pct"])) > 1e-8:
            raise AssertionError(f"return mismatch {ticker}")

    total_counts = feature_pass.sum(axis=1)
    global_threshold = int(best.group_thresholds.sum())
    global_count_pass = total_counts >= global_threshold
    hybrid_pass = active
    offset_blocked = global_count_pass & ~hybrid_pass
    boil_like = offset_blocked & (~group_pass[:, G3_GROUP_INDEX] | ~group_pass[:, 3])
    group_fractions = np.column_stack(
        [group_counts[:, index] / len(GROUPS[group]) for index, group in enumerate(GROUP_NAMES)]
    )
    ce_like = offset_blocked & (np.max(group_fractions, axis=1) >= 1.0) & np.any(~group_pass, axis=1)
    labels = frame["label_2d3pct"].to_numpy(int)

    def precision(mask: np.ndarray) -> float:
        return float(labels[mask].mean()) if int(mask.sum()) else 0.0

    offset = {
        "oos_rows": len(frame),
        "hybrid_pass_days": int(hybrid_pass.sum()),
        "global_count_pass_days": int(global_count_pass.sum()),
        "offset_blocked_days": int(offset_blocked.sum()),
        "offset_blocked_precision": precision(offset_blocked),
        "boil_like_blocked_days": int(boil_like.sum()),
        "boil_like_precision": precision(boil_like),
        "ce_like_blocked_days": int(ce_like.sum()),
        "ce_like_precision": precision(ce_like),
        "all_offset_cases_blocked": bool(not np.any(offset_blocked & hybrid_pass)),
    }
    return performance, traced, offset


def trade_metrics_from_csv(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    returns = frame["net_return_pct"].to_numpy(float) / 100.0
    equity = np.cumprod(1.0 + returns) if len(returns) else np.array([], dtype=float)
    running = np.maximum.accumulate(equity) if len(equity) else np.array([], dtype=float)
    drawdown = equity / running - 1.0 if len(equity) else np.array([], dtype=float)
    plus3 = frame["plus3_reached_during_holding"].astype(str).str.lower().eq("true")
    return {
        "trade_count": len(frame),
        "avg_return_pct": float(frame["net_return_pct"].mean()) if len(frame) else 0.0,
        "compounded_return_pct": float((equity[-1] - 1.0) * 100.0) if len(equity) else 0.0,
        "max_drawdown_pct": float(np.min(drawdown) * 100.0) if len(drawdown) else 0.0,
        "win_rate": float((frame["net_return_pct"] > 0).mean()) if len(frame) else 0.0,
        "plus3_reach_rate": float(plus3.mean()) if len(frame) else 0.0,
        "avg_holding_sessions": float(frame["holding_sessions"].mean()) if len(frame) else 0.0,
        "max_holding_sessions": int(frame["holding_sessions"].max()) if len(frame) else 0,
    }


def load_symbol_rows() -> list[dict[str, Any]]:
    symbol_path = SOURCE_PILOT_DIR / "symbol_list.csv"
    symbols = pd.read_csv(symbol_path)
    selected = symbols[symbols["ticker"].isin(TARGET_TICKERS)].copy()
    selected = selected.set_index("ticker").loc[TARGET_TICKERS].reset_index()
    if selected["ticker"].tolist() != TARGET_TICKERS:
        raise RuntimeError("AAP/POWI symbol rows not found exactly")
    for row in selected.to_dict("records"):
        source = KINGMAKER_ROOT / str(row["source_path"])
        if not source.exists():
            raise RuntimeError(f"NOT_STORED {row['ticker']} {source}")
        if sha256(source) != str(row["source_sha256"]):
            raise RuntimeError(f"source SHA mismatch {row['ticker']}")
    return selected.to_dict("records")


def label_distribution(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (ticker, regime), subset in frame.groupby(["ticker", "regime"], sort=True):
        rows.append(
            {
                "ticker": ticker,
                "regime": regime,
                "sample_count": len(subset),
                "positive_count": int(subset["label_2d3pct"].sum()),
                "negative_count": int((1 - subset["label_2d3pct"]).sum()),
                "positive_rate": float(subset["label_2d3pct"].mean()),
            }
        )
    for regime, subset in frame.groupby("regime", sort=True):
        rows.append(
            {
                "ticker": "ALL_2",
                "regime": regime,
                "sample_count": len(subset),
                "positive_count": int(subset["label_2d3pct"].sum()),
                "negative_count": int((1 - subset["label_2d3pct"]).sum()),
                "positive_rate": float(subset["label_2d3pct"].mean()),
            }
        )
    return rows


def comparison_rows(
    feature_set: pd.DataFrame,
    selected_by_ticker: dict[str, dict[str, Any]],
    hybrid_performance: dict[str, dict[str, Any]],
    hybrid_trades: dict[str, list[dict[str, Any]]],
    offset_stats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    strict_summary = pd.read_csv(SOURCE_PILOT_DIR / "survivor_summary.csv")
    rows: list[dict[str, Any]] = []
    for ticker in TARGET_TICKERS:
        oos_rows = int((feature_set["ticker"].eq(ticker) & feature_set["regime"].eq("oos")).sum())
        strict_survivor = strict_summary[
            strict_summary["ticker"].eq(ticker)
            & strict_summary["survivor"].astype(str).str.lower().eq("true")
        ].iloc[0]
        strict_trade_path = STRICT_DETAIL_DIR / f"{ticker.lower()}_trades.csv"
        strict_trade_metrics = trade_metrics_from_csv(strict_trade_path)
        strict_row = {
            "ticker": ticker,
            "method": "STRICT_AND_12_BASELINE",
            "model_hash": strict_survivor["model_hash"],
            "origin_train_label": strict_survivor["origin_train_label"],
            "survivor": True,
            "oos_rows": oos_rows,
            "oos_signal_count": int(strict_survivor["oos_passed_count"]),
            "oos_coverage": int(strict_survivor["oos_passed_count"]) / oos_rows,
            "oos_precision": float(strict_survivor["oos_precision"]),
            **strict_trade_metrics,
            "global_count_pass_days": None,
            "offset_blocked_days": None,
            "boil_like_blocked_days": None,
            "ce_like_blocked_days": None,
        }
        rows.append(strict_row)

        selected = selected_by_ticker[ticker]
        performance = hybrid_performance[ticker]
        trades = pd.DataFrame(hybrid_trades[ticker])
        plus3_rate = (
            float(trades["plus3_reached_during_holding"].astype(bool).mean())
            if len(trades)
            else 0.0
        )
        hybrid_row = {
            "ticker": ticker,
            "method": "HYBRID_GROUP_COUNT_AND",
            "model_hash": selected["model_hash"],
            "origin_train_label": selected["split"]["label"],
            "survivor": bool(selected["survivor_row"]["survivor"]),
            "oos_rows": oos_rows,
            "oos_signal_count": int(selected["survivor_row"]["oos_passed_count"]),
            "oos_coverage": int(selected["survivor_row"]["oos_passed_count"]) / oos_rows,
            "oos_precision": float(selected["survivor_row"]["oos_precision"]),
            "trade_count": int(performance["trade_count"]),
            "avg_return_pct": float(performance["avg_return_pct"]),
            "compounded_return_pct": float(performance["compounded_return_pct"]),
            "max_drawdown_pct": float(performance["max_drawdown_pct"]),
            "win_rate": float(performance["win_rate"]),
            "plus3_reach_rate": plus3_rate,
            "avg_holding_sessions": float(performance["avg_holding_sessions"]),
            "max_holding_sessions": int(performance["max_holding_sessions"]),
            "global_count_pass_days": offset_stats[ticker]["global_count_pass_days"],
            "offset_blocked_days": offset_stats[ticker]["offset_blocked_days"],
            "boil_like_blocked_days": offset_stats[ticker]["boil_like_blocked_days"],
            "ce_like_blocked_days": offset_stats[ticker]["ce_like_blocked_days"],
        }
        rows.append(hybrid_row)

    for method in ["STRICT_AND_12_BASELINE", "HYBRID_GROUP_COUNT_AND"]:
        subset = [row for row in rows if row["method"] == method]
        all_trade_returns: list[float] = []
        plus3_values: list[bool] = []
        holding_values: list[int] = []
        for ticker in TARGET_TICKERS:
            if method == "STRICT_AND_12_BASELINE":
                trades = pd.read_csv(STRICT_DETAIL_DIR / f"{ticker.lower()}_trades.csv")
                all_trade_returns.extend(trades["net_return_pct"].astype(float).tolist())
                plus3_values.extend(
                    trades["plus3_reached_during_holding"].astype(str).str.lower().eq("true").tolist()
                )
                holding_values.extend(trades["holding_sessions"].astype(int).tolist())
            else:
                all_trade_returns.extend(
                    [float(row["net_return_pct"]) for row in hybrid_trades[ticker]]
                )
                plus3_values.extend(
                    [bool(row["plus3_reached_during_holding"]) for row in hybrid_trades[ticker]]
                )
                holding_values.extend(
                    [int(row["holding_sessions"]) for row in hybrid_trades[ticker]]
                )
        returns = np.asarray(all_trade_returns, dtype=float) / 100.0
        equity = np.cumprod(1.0 + returns) if len(returns) else np.array([], dtype=float)
        running = np.maximum.accumulate(equity) if len(equity) else np.array([], dtype=float)
        drawdown = equity / running - 1.0 if len(equity) else np.array([], dtype=float)
        total_oos_rows = sum(int(row["oos_rows"]) for row in subset)
        total_signals = sum(int(row["oos_signal_count"]) for row in subset)
        weighted_positive = sum(
            float(row["oos_precision"]) * int(row["oos_signal_count"]) for row in subset
        )
        rows.append(
            {
                "ticker": "ALL_2_POOLED",
                "method": method,
                "model_hash": "POOLED",
                "origin_train_label": "MIXED",
                "survivor": bool(all(row["survivor"] for row in subset)),
                "oos_rows": total_oos_rows,
                "oos_signal_count": total_signals,
                "oos_coverage": total_signals / total_oos_rows if total_oos_rows else 0.0,
                "oos_precision": weighted_positive / total_signals if total_signals else 0.0,
                "trade_count": len(returns),
                "avg_return_pct": float(np.mean(returns) * 100.0) if len(returns) else 0.0,
                "compounded_return_pct": float((equity[-1] - 1.0) * 100.0) if len(equity) else 0.0,
                "max_drawdown_pct": float(np.min(drawdown) * 100.0) if len(drawdown) else 0.0,
                "win_rate": float(np.mean(returns > 0.0)) if len(returns) else 0.0,
                "plus3_reach_rate": float(np.mean(plus3_values)) if plus3_values else 0.0,
                "avg_holding_sessions": float(np.mean(holding_values)) if holding_values else 0.0,
                "max_holding_sessions": int(max(holding_values)) if holding_values else 0,
                "global_count_pass_days": (
                    sum(int(row["global_count_pass_days"] or 0) for row in subset)
                    if method == "HYBRID_GROUP_COUNT_AND"
                    else None
                ),
                "offset_blocked_days": (
                    sum(int(row["offset_blocked_days"] or 0) for row in subset)
                    if method == "HYBRID_GROUP_COUNT_AND"
                    else None
                ),
                "boil_like_blocked_days": (
                    sum(int(row["boil_like_blocked_days"] or 0) for row in subset)
                    if method == "HYBRID_GROUP_COUNT_AND"
                    else None
                ),
                "ce_like_blocked_days": (
                    sum(int(row["ce_like_blocked_days"] or 0) for row in subset)
                    if method == "HYBRID_GROUP_COUNT_AND"
                    else None
                ),
            }
        )
    return rows


def run() -> dict[str, Any]:
    started = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    symbol_rows = load_symbol_rows()
    feature_set, errors = build_feature_set(symbol_rows)
    if errors:
        write_csv(OUT_DIR / "feature_errors.csv", errors)
    if feature_set.empty or feature_set["ticker"].nunique() != 2:
        raise RuntimeError(f"UNRECOVERABLE feature set rows={len(feature_set)}")
    feature_set["date"] = pd.to_datetime(feature_set["date"])
    feature_set.to_csv(OUT_DIR / "feature_set_2sym.csv", index=False)
    write_csv(OUT_DIR / "label_distribution.csv", label_distribution(feature_set))

    cfg = IntervalGAConfig()
    all_candidates: list[dict[str, Any]] = []
    selected_by_ticker: dict[str, dict[str, Any]] = {}
    hybrid_performance: dict[str, dict[str, Any]] = {}
    hybrid_trades: dict[str, list[dict[str, Any]]] = {}
    offset_stats: dict[str, dict[str, Any]] = {}

    for ticker in TARGET_TICKERS:
        ticker_frame = feature_set[feature_set["ticker"] == ticker].copy().sort_values("date").reset_index(drop=True)
        candidates: list[dict[str, Any]] = []
        for split_index, split in enumerate(TRAIN_SPLITS, 1):
            origin_train = split_frame(ticker_frame, split)
            if len(origin_train) < 100:
                raise RuntimeError(f"INSUFFICIENT_DATA {ticker} {split['label']} {len(origin_train)}")
            domain_low, domain_high = fit_domain(origin_train)
            x_train = normalize(origin_train, domain_low, domain_high)
            y_train = origin_train["label_2d3pct"].to_numpy(int)
            g3_floor_norm = np.nanquantile(
                x_train[:, GROUP_INDEXES[G3_GROUP_INDEX]],
                G3_MEMBER_PERCENTILE_FLOOR,
                axis=0,
            )
            ga = train_grouped_interval_ga(
                x_train,
                y_train,
                FEATURES,
                GROUP_INDEXES,
                seed=ticker_seed(ticker, split_index),
                config=cfg,
                g3_group_index=G3_GROUP_INDEX,
                g3_floor_norm=g3_floor_norm,
            )
            candidate = evaluate_candidate(
                ticker,
                ticker_frame,
                split,
                split_index,
                domain_low,
                domain_high,
                g3_floor_norm,
                ga,
                cfg,
            )
            candidates.append(candidate)
            all_candidates.append(candidate)
        selected, selection_rule = select_trade_candidate(candidates)
        selected["survivor_row"]["selected_for_trade"] = True
        selected["survivor_row"]["selection_rule"] = selection_rule
        selected_by_ticker[ticker] = selected
        performance, trades, offset = trace_trades(ticker, selected)
        hybrid_performance[ticker] = performance
        hybrid_trades[ticker] = trades
        offset_stats[ticker] = offset

    training_rows = [row for candidate in all_candidates for row in candidate["training_rows"]]
    learned_rows = [row for candidate in all_candidates for row in candidate["learned_rows"]]
    threshold_rows = [row for candidate in all_candidates for row in candidate["threshold_rows"]]
    metric_rows = [row for candidate in all_candidates for row in candidate["metric_rows"]]
    survivor_rows = [candidate["survivor_row"] for candidate in all_candidates]

    write_csv(OUT_DIR / "training_log.csv", training_rows)
    write_csv(OUT_DIR / "learned_genes.csv", learned_rows)
    write_csv(OUT_DIR / "group_threshold_check.csv", threshold_rows)
    write_csv(OUT_DIR / "per_regime_metrics.csv", metric_rows)
    write_csv(OUT_DIR / "survivor_summary.csv", survivor_rows)
    write_csv(OUT_DIR / "aap_trades_hybrid.csv", hybrid_trades["AAP"])
    write_csv(OUT_DIR / "powi_trades_hybrid.csv", hybrid_trades["POWI"])
    comparison = comparison_rows(
        feature_set,
        selected_by_ticker,
        hybrid_performance,
        hybrid_trades,
        offset_stats,
    )
    write_csv(OUT_DIR / "comparison_vs_strict_and.csv", comparison)

    comparison_frame = pd.DataFrame(comparison)
    pooled = comparison_frame[comparison_frame["ticker"] == "ALL_2_POOLED"].set_index("method")
    strict = pooled.loc["STRICT_AND_12_BASELINE"]
    hybrid = pooled.loc["HYBRID_GROUP_COUNT_AND"]
    compound_delta = float(hybrid["compounded_return_pct"] - strict["compounded_return_pct"])
    precision_delta = float(hybrid["oos_precision"] - strict["oos_precision"])
    coverage_ratio = float(hybrid["oos_coverage"] / strict["oos_coverage"]) if strict["oos_coverage"] else None
    if compound_delta > 0.50 and precision_delta >= -0.02 and bool(hybrid["survivor"]):
        provisional_verdict = "HYBRID_BETTER"
    elif compound_delta < -0.50 or precision_delta < -0.08:
        provisional_verdict = "HYBRID_WORSE"
    else:
        provisional_verdict = "SIMILAR"

    summary = {
        "generated_at_utc": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        "tickers": TARGET_TICKERS,
        "declared_feature_count_in_request": 15,
        "explicit_group_feature_count": len(FEATURES),
        "feature_count_resolution": "explicit 4+4+3+3 group list applied; no invented fifteenth feature",
        "dead_features_removed": DEAD_FEATURES_REMOVED,
        "feature_rows": len(feature_set),
        "rows_by_ticker": feature_set.groupby("ticker").size().to_dict(),
        "oos_rows_by_ticker": feature_set[feature_set["regime"] == "oos"].groupby("ticker").size().to_dict(),
        "ga_config": {
            "population": cfg.population,
            "generations": cfg.generations,
            "patience": cfg.patience,
            "train_splits": TRAIN_SPLITS,
        },
        "selected_models": {
            ticker: {
                "model_hash": selected_by_ticker[ticker]["model_hash"],
                "origin_train_label": selected_by_ticker[ticker]["split"]["label"],
                "survivor": selected_by_ticker[ticker]["survivor_row"]["survivor"],
                "selection_rule": selected_by_ticker[ticker]["survivor_row"]["selection_rule"],
                "group_thresholds": {
                    group: int(selected_by_ticker[ticker]["best"].group_thresholds[index])
                    for index, group in enumerate(GROUP_NAMES)
                },
                "g3_floor_norm": selected_by_ticker[ticker]["g3_floor_norm"].tolist(),
            }
            for ticker in TARGET_TICKERS
        },
        "survivor_candidate_count": int(sum(bool(row["survivor"]) for row in survivor_rows)),
        "survivor_tickers": sorted({row["ticker"] for row in survivor_rows if row["survivor"]}),
        "offset_stats": offset_stats,
        "strict_pooled_compounded_return_pct": float(strict["compounded_return_pct"]),
        "hybrid_pooled_compounded_return_pct": float(hybrid["compounded_return_pct"]),
        "compound_delta_pctpoint": compound_delta,
        "strict_pooled_oos_precision": float(strict["oos_precision"]),
        "hybrid_pooled_oos_precision": float(hybrid["oos_precision"]),
        "precision_delta": precision_delta,
        "coverage_ratio": coverage_ratio,
        "provisional_verdict": provisional_verdict,
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
