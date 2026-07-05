#!/usr/bin/env python3
"""
Original-Stage2-style GA runner for next-day range prediction research.

원본 scripts/research/run_stage2.py 기준으로 복구한 흐름:
- train_1, train_2, train_3 각각 독립 GA를 실행한다.
- 각 train split에서 final population 100개를 후보로 모은다.
- 총 후보는 기본 300개다. 같은 signature가 중복되면 대표 1개로 합친다.
- 그 후보들을 early-cut 순서로 평가한다.

원본 Stage2 early-cut 순서:
1. stress_pre_2022h1
2. train_3_eval
3. train_2_eval
4. train_1_eval
5. oos_2025h2

중요:
- train_1 survivor가 train_2로 넘어가는 구조가 아니다.
- train_2/3에서 train_1 survivor를 seed로 재진화하는 구조도 아니다.
- 세 train split은 독립 GA다.
- 평가 단계에서는 새 개체 생성, 재진화, 변이, 교배, 랜덤 유입이 없다.

현재 연구 타깃:
- range_pct = high_pct_label + low_mag_pct_label
- large_range = range_pct >= 각 origin train split의 range_pct quantile threshold
- 기본값: --range-quantile 0.70, 즉 origin train 기준 상위 30% 변동성 날.
- signal = predicted_high_bin + predicted_low_bin >= --signal-range-bin-sum-threshold

Read/write scope:
- OHLCV/cache/news csv는 read-only.
- 결과는 out_dir 아래 연구 산출물만 생성.
- run_live, 실거래, 캐시 갱신 없음.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
import types
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_COMMIT = "b03f39b"
LEGACY_PATH = "scripts/research/run_range_predictor_stage2_v3.py"

TARGET_MODE = "next_day_large_range_original_stage2"
RANGE_QUANTILE = 0.70
SIGNAL_RANGE_BIN_SUM_THRESHOLD = 4
WILSON_Z = 1.0
MIN_SIGNAL_RATE = 3.0
MAX_SIGNAL_RATE = 40.0
MIN_SIGNAL_COUNT_TRAIN = 12
MIN_SIGNAL_COUNT_FINAL = 10
MIN_HIT_COUNT_TRAIN = 4
MIN_HIT_COUNT_FINAL = 3

TRAIN_SPLITS = (
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
)

PERIODS_TEMPLATE = (
    {"label": "stress_pre_2022h1", "kind": "stress", "start": "1900-01-01", "end": "2022-06-30", "order": 1},
    {"label": "train_3_eval", "kind": "train", "start": "2024-07-01", "end": "2025-06-30", "order": 2},
    {"label": "train_2_eval", "kind": "train", "start": "2023-07-01", "end": "2024-06-30", "order": 3},
    {"label": "train_1_eval", "kind": "train", "start": "2022-07-01", "end": "2023-06-30", "order": 4},
    {"label": "oos_2025h2", "kind": "oos", "start": "2025-07-01", "end": "2099-12-31", "order": 5},
)


def _load_legacy_module() -> types.ModuleType:
    code = subprocess.check_output(["git", "show", f"{LEGACY_COMMIT}:{LEGACY_PATH}"], cwd=str(PROJECT_ROOT), text=True)
    mod = types.ModuleType("_km_range_predictor_v3_b03f39b")
    mod.__file__ = str(PROJECT_ROOT / LEGACY_PATH)
    mod.__name__ = "_km_range_predictor_v3_b03f39b"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


L = _load_legacy_module()


def safe_float(value: Any, default: float = 0.0) -> float:
    return L.safe_float(value, default)


def safe_int(value: Any, default: int = 0) -> int:
    return L.safe_int(value, default)


def predictor_signature(ind: Any) -> str:
    return L.predictor_signature(ind)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    L.write_json(path, payload)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    L.write_jsonl(path, rows)


def auto_out_dir(ticker: str) -> Path:
    q = int(round(RANGE_QUANTILE * 100))
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_original_stage2_large_range_q{q}_{time.strftime('%Y%m%d')}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("cannot allocate output directory")


def wilson_lower_bound_pct(success: float, total: float, z: float | None = None) -> float:
    z = WILSON_Z if z is None else float(z)
    n = float(total)
    if n <= 0:
        return 0.0
    p = float(success) / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return max(0.0, (centre - margin) / denom * 100.0)


def range_pct_array(df) -> np.ndarray:
    return df["high_pct_label"].to_numpy(dtype=float) + df["low_mag_pct_label"].to_numpy(dtype=float)


def period_frame_checked(data, start: str, end: str, label: str):
    df = L.period_frame(data, start, end)
    if df.empty:
        raise ValueError(f"empty period: {label} {start}~{end}")
    return df


def label_arrays(df, spec: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    threshold = safe_float(spec.get("range_large_threshold_pct"))
    rng = range_pct_array(df)
    large = rng >= threshold
    return large.astype(bool), rng


def signal_array(ph: np.ndarray, pl: np.ndarray) -> np.ndarray:
    return (ph.astype(int) + pl.astype(int)) >= SIGNAL_RANGE_BIN_SUM_THRESHOLD


def binary_metrics(y: np.ndarray, signal: np.ndarray) -> dict[str, float]:
    y = y.astype(bool)
    signal = signal.astype(bool)
    tp = float(np.sum(y & signal))
    fp = float(np.sum(~y & signal))
    tn = float(np.sum(~y & ~signal))
    fn = float(np.sum(y & ~signal))
    n = max(1.0, float(len(y)))
    signal_count = tp + fp
    actual_count = tp + fn
    precision = tp / max(1.0, signal_count) * 100.0
    recall = tp / max(1.0, actual_count) * 100.0
    specificity = tn / max(1.0, tn + fp) * 100.0
    acc = (tp + tn) / n * 100.0
    bal = (recall + specificity) / 2.0
    f1 = (2.0 * precision * recall / max(1e-12, precision + recall)) if (precision + recall) > 0 else 0.0
    signal_rate = signal_count / n * 100.0
    actual_rate = actual_count / n * 100.0
    precision_lcb = wilson_lower_bound_pct(tp, signal_count)
    base_lcb = wilson_lower_bound_pct(actual_count, n)
    return {
        "range_acc_pct": acc,
        "range_bal_acc_pct": bal,
        "range_precision_pct": precision,
        "range_precision_lcb_pct": precision_lcb,
        "range_recall_pct": recall,
        "range_specificity_pct": specificity,
        "range_f1_pct": f1,
        "range_signal_rate_pct": signal_rate,
        "range_actual_rate_pct": actual_rate,
        "range_base_lcb_pct": base_lcb,
        "range_precision_lift_pp": precision - actual_rate,
        "range_precision_lift_lcb_pp": precision_lcb - actual_rate,
        "range_precision_lcb_vs_base_lcb_pp": precision_lcb - base_lcb,
        "range_tp": tp,
        "range_fp": fp,
        "range_tn": tn,
        "range_fn": fn,
        "range_signal_count": signal_count,
        "range_actual_count": actual_count,
    }


def make_range_baseline_spec(train_df) -> dict[str, Any]:
    rng = range_pct_array(train_df)
    threshold = float(np.nanquantile(rng, RANGE_QUANTILE)) if len(rng) else 0.0
    y = rng >= threshold
    return {
        "target_mode": TARGET_MODE,
        "range_quantile": RANGE_QUANTILE,
        "range_large_threshold_pct": threshold,
        "signal_range_bin_sum_threshold": SIGNAL_RANGE_BIN_SUM_THRESHOLD,
        "wilson_z": WILSON_Z,
        "train_large_range_rate_pct": float(np.mean(y) * 100.0) if len(y) else 0.0,
        "train_range_mean_pct": float(np.nanmean(rng)) if len(rng) else 0.0,
        "train_range_median_pct": float(np.nanmedian(rng)) if len(rng) else 0.0,
        "train_range_q90_pct": float(np.nanquantile(rng, 0.90)) if len(rng) else 0.0,
        "exact_high_bin": 0,
        "exact_low_bin": 0,
        "adjacent_high_bin": 0,
        "adjacent_low_bin": 0,
        "source": "origin train split baseline for original Stage2 large range label",
    }


def score_range_predictions(df, ph: np.ndarray, pl: np.ndarray, spec: Mapping[str, Any]) -> dict[str, float]:
    y, rng = label_arrays(df, spec)
    sig = signal_array(ph, pl)
    m = binary_metrics(y, sig)
    selected = rng[sig]
    actual_large = rng[y]
    m.update(
        {
            "range_threshold_pct": safe_float(spec.get("range_large_threshold_pct")),
            "range_mean_pct": float(np.nanmean(rng)) if len(rng) else 0.0,
            "range_median_pct": float(np.nanmedian(rng)) if len(rng) else 0.0,
            "range_q90_pct": float(np.nanquantile(rng, 0.90)) if len(rng) else 0.0,
            "range_selected_mean_pct": float(np.nanmean(selected)) if len(selected) else 0.0,
            "range_selected_median_pct": float(np.nanmedian(selected)) if len(selected) else 0.0,
            "range_actual_large_mean_pct": float(np.nanmean(actual_large)) if len(actual_large) else 0.0,
        }
    )
    return m


def range_penalty(metrics: Mapping[str, Any]) -> dict[str, float]:
    signal_rate = safe_float(metrics.get("range_signal_rate_pct"))
    signal_count = safe_float(metrics.get("range_signal_count"))
    hit_count = safe_float(metrics.get("range_tp"))
    rate_low = max(0.0, MIN_SIGNAL_RATE - signal_rate)
    rate_high = max(0.0, signal_rate - MAX_SIGNAL_RATE)
    signal_shortfall = max(0.0, float(MIN_SIGNAL_COUNT_TRAIN) - signal_count)
    hit_shortfall = max(0.0, float(MIN_HIT_COUNT_TRAIN) - hit_count)
    total = rate_low * 0.60 + rate_high * 0.20 + signal_shortfall * 1.20 + hit_shortfall * 1.50
    return {
        "signal_rate_low_penalty": rate_low * 0.60,
        "signal_rate_high_penalty": rate_high * 0.20,
        "signal_count_shortfall_penalty": signal_shortfall * 1.20,
        "hit_count_shortfall_penalty": hit_shortfall * 1.50,
        "total_penalty": total,
    }


def range_fitness(metrics: Mapping[str, Any]) -> float:
    lift_lcb = safe_float(metrics.get("range_precision_lift_lcb_pp"))
    lcb_vs_base = safe_float(metrics.get("range_precision_lcb_vs_base_lcb_pp"))
    precision = safe_float(metrics.get("range_precision_pct"))
    precision_lcb = safe_float(metrics.get("range_precision_lcb_pct"))
    f1 = safe_float(metrics.get("range_f1_pct"))
    bal = safe_float(metrics.get("range_bal_acc_pct"))
    signal_rate = safe_float(metrics.get("range_signal_rate_pct"))
    selected_mean = safe_float(metrics.get("range_selected_mean_pct"))
    threshold = safe_float(metrics.get("range_threshold_pct"))
    selected_bonus = max(0.0, selected_mean - threshold) * 0.25
    raw = precision_lcb * 0.75 + lift_lcb * 1.30 + lcb_vs_base * 0.90 + precision * 0.15 + f1 * 0.15 + (bal - 50.0) * 0.20 + selected_bonus
    raw -= abs(signal_rate - 18.0) * 0.06
    return raw - safe_float(metrics.get("total_penalty"))


def evaluate_range_predictor(ind, df, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    ph, pl, pred_diag = L.predict(ind, df[features], qspec)
    scores = score_range_predictions(df, ph, pl, ind.baseline_spec)
    penalty = range_penalty(scores)
    metrics = {
        **scores,
        "target_mode": TARGET_MODE,
        "sample_count": int(len(df)),
        "range_quantile": RANGE_QUANTILE,
        "signal_range_bin_sum_threshold": SIGNAL_RANGE_BIN_SUM_THRESHOLD,
        **penalty,
        **pred_diag,
    }
    metrics["fitness"] = range_fitness(metrics)
    return metrics


def range_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    final_kind = str(kind).lower() in {"stress", "oos"}
    min_signal_count = MIN_SIGNAL_COUNT_FINAL if final_kind else MIN_SIGNAL_COUNT_TRAIN
    min_hit_count = MIN_HIT_COUNT_FINAL if final_kind else MIN_HIT_COUNT_TRAIN
    checks = [
        ("sample_count", safe_int(metrics.get("sample_count")), 100, ">="),
        ("member_score", safe_float(metrics.get("member_score")), 10.0, ">="),
        ("range_signal_count", safe_float(metrics.get("range_signal_count")), float(min_signal_count), ">="),
        ("range_tp", safe_float(metrics.get("range_tp")), float(min_hit_count), ">="),
        ("range_precision_lift_lcb_pp", safe_float(metrics.get("range_precision_lift_lcb_pp")), 0.0, ">"),
        ("range_precision_lcb_vs_base_lcb_pp", safe_float(metrics.get("range_precision_lcb_vs_base_lcb_pp")), 0.0, ">"),
        ("total_penalty", safe_float(metrics.get("total_penalty")), 12.0, "<="),
    ]
    out = []
    for metric, value, threshold, rule in checks:
        failed = (rule == ">=" and value < threshold) or (rule == ">" and value <= threshold) or (rule == "<=" and value > threshold)
        if failed:
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def range_score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows:
        return []
    lift_r = L.percentile_ranks([safe_float(r.get("range_precision_lift_lcb_pp")) for r in rows])
    base_r = L.percentile_ranks([safe_float(r.get("range_precision_lcb_vs_base_lcb_pp")) for r in rows])
    precision_r = L.percentile_ranks([safe_float(r.get("range_precision_lcb_pct")) for r in rows])
    raw_precision_r = L.percentile_ranks([safe_float(r.get("range_precision_pct")) for r in rows])
    count_r = L.percentile_ranks([safe_float(r.get("range_signal_count")) for r in rows])
    selected_r = L.percentile_ranks([safe_float(r.get("range_selected_mean_pct")) for r in rows])
    penalty_r = L.percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(0.0, min(1.0, lift_r[i] * 0.28 + base_r[i] * 0.20 + precision_r[i] * 0.22 + raw_precision_r[i] * 0.08 + selected_r[i] * 0.07 + count_r[i] * 0.05 + penalty_r[i] * 0.10)) * 100.0
        r = dict(row)
        r["member_score"] = round(score, 6)
        r["member_score_components"] = {
            "precision_lift_lcb_percentile": round(lift_r[i], 6),
            "precision_lcb_vs_base_lcb_percentile": round(base_r[i], 6),
            "precision_lcb_percentile": round(precision_r[i], 6),
            "raw_precision_percentile": round(raw_precision_r[i], 6),
            "selected_range_mean_percentile": round(selected_r[i], 6),
            "signal_count_percentile": round(count_r[i], 6),
            "low_penalty_percentile": round(penalty_r[i], 6),
        }
        out.append(r)
    return out


def install_range_target(
    range_quantile: float,
    signal_range_bin_sum_threshold: int,
    wilson_z: float,
    min_signal_rate: float,
    max_signal_rate: float,
    min_signal_count_train: int,
    min_signal_count_final: int,
    min_hit_count_train: int,
    min_hit_count_final: int,
) -> None:
    global RANGE_QUANTILE, SIGNAL_RANGE_BIN_SUM_THRESHOLD, WILSON_Z
    global MIN_SIGNAL_RATE, MAX_SIGNAL_RATE, MIN_SIGNAL_COUNT_TRAIN, MIN_SIGNAL_COUNT_FINAL, MIN_HIT_COUNT_TRAIN, MIN_HIT_COUNT_FINAL
    RANGE_QUANTILE = max(0.50, min(0.95, float(range_quantile)))
    SIGNAL_RANGE_BIN_SUM_THRESHOLD = int(signal_range_bin_sum_threshold)
    WILSON_Z = float(wilson_z)
    MIN_SIGNAL_RATE = float(min_signal_rate)
    MAX_SIGNAL_RATE = float(max_signal_rate)
    MIN_SIGNAL_COUNT_TRAIN = int(min_signal_count_train)
    MIN_SIGNAL_COUNT_FINAL = int(min_signal_count_final)
    MIN_HIT_COUNT_TRAIN = int(min_hit_count_train)
    MIN_HIT_COUNT_FINAL = int(min_hit_count_final)
    L.make_baseline_spec = make_range_baseline_spec
    L.evaluate_predictor = evaluate_range_predictor


def train_one_split(
    *,
    ticker: str,
    split_idx: int,
    split: Mapping[str, str],
    seed_base: int,
    data,
    all_features: list[str],
) -> dict[str, Any]:
    started = time.time()
    rng = random.Random(seed_base + split_idx)
    train_df = period_frame_checked(data, split["train_start"], split["train_end"], split["label"])
    qspec = L.make_quantile_spec(train_df, all_features)
    usable_features = [f for f in all_features if f in qspec]
    baseline_spec = L.make_baseline_spec(train_df)
    init_pop = L.prepare_population_for_split(None, rng, qspec, baseline_spec)
    split_meta = {
        "label": split["label"],
        "train_start": split["train_start"],
        "train_end": split["train_end"],
        "stage": split_idx,
    }
    pop, history = L.run_ga_on_split(init_pop, train_df, usable_features, qspec, split_meta, seed_base + split_idx)
    rows: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for ind in pop:
        ind.metrics = L.evaluate_predictor(ind, train_df, usable_features, qspec)
        ind.fitness = safe_float(ind.metrics.get("fitness"))
        ind.signature = predictor_signature(ind)
    pop_sorted = sorted(pop, key=lambda ind: safe_float(getattr(ind, "fitness", 0.0)), reverse=True)
    for rank, ind in enumerate(pop_sorted, 1):
        sig = ind.signature or predictor_signature(ind)
        row = {
            "ticker": ticker,
            "target_mode": TARGET_MODE,
            "train_label": split["label"],
            "train_start": split["train_start"],
            "train_end": split["train_end"],
            "origin_rank": rank,
            "signature": sig,
            "train_fitness": safe_float(getattr(ind, "fitness", 0.0)),
            "train_metrics": ind.metrics,
            "predictor": L.individual_to_dict(ind),
        }
        rows.append(row)
        entries.append({
            "signature": sig,
            "individual": ind,
            "qspec": qspec,
            "features": usable_features,
            "baseline_spec": baseline_spec,
            "origin": {k: row[k] for k in ["train_label", "train_start", "train_end", "origin_rank", "train_fitness"]},
        })
    for h in history:
        h.update({
            "train_label": split["label"],
            "train_start": split["train_start"],
            "train_end": split["train_end"],
            "generations_run": len(history),
            "early_stop_triggered": len(history) < L.GENERATIONS,
            "train_elapsed_sec": time.time() - started,
            "target_mode": TARGET_MODE,
        })
    return {
        "split": dict(split),
        "rows": rows,
        "entries": entries,
        "history": history,
        "elapsed_sec": time.time() - started,
        "generations_run": len(history),
        "early_stop": len(history) < L.GENERATIONS,
        "baseline_spec": baseline_spec,
        "feature_count": len(usable_features),
    }


def build_representatives(train_results: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    representative_by_sig: dict[str, dict[str, Any]] = {}
    origins_by_sig: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in train_results:
        for entry in result["entries"]:
            sig = str(entry["signature"])
            origins_by_sig[sig].append(dict(entry["origin"]))
            current = representative_by_sig.get(sig)
            if current is None or safe_float(entry["origin"].get("train_fitness")) > safe_float(current["origin"].get("train_fitness")):
                representative_by_sig[sig] = entry
    return representative_by_sig, origins_by_sig


def evaluate_mixed_population(
    *,
    ticker: str,
    alive_sigs: set[str],
    representative_by_sig: Mapping[str, dict[str, Any]],
    origins_by_sig: Mapping[str, list[dict[str, Any]]],
    df,
    period: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for rank, sig in enumerate(sorted(alive_sigs), 1):
        entry = representative_by_sig[sig]
        ind = entry["individual"]
        metrics = evaluate_range_predictor(ind, df, entry["features"], entry["qspec"])
        origin_labels = sorted({str(origin["train_label"]) for origin in origins_by_sig[sig]})
        raw.append({
            "ticker": ticker,
            "signature": sig,
            "rank_is": rank,
            "period_label": period["label"],
            "period_kind": period["kind"],
            "period_order": period["order"],
            "period_start": period["start"],
            "period_end": period["end"],
            "origin_count": len(origins_by_sig[sig]),
            "origin_train_labels": origin_labels,
            "representative_train_label": entry["origin"]["train_label"],
            "representative_train_fitness": safe_float(entry["origin"].get("train_fitness")),
            **metrics,
        })
    scored = range_score_period_candidates(raw)
    for row in scored:
        reasons = range_fail_reasons(row, str(period["kind"]))
        row["fail_reasons"] = reasons
        row["passed_gate"] = not reasons
        row["status"] = "evaluated"
    return scored


def run_original_stage2_predictor(
    ticker: str,
    out_dir: Path,
    seed_base: int,
    range_quantile: float,
    signal_range_bin_sum_threshold: int,
    wilson_z: float,
    min_signal_rate: float,
    max_signal_rate: float,
    min_signal_count_train: int,
    min_signal_count_final: int,
    min_hit_count_train: int,
    min_hit_count_final: int,
) -> dict[str, Any]:
    install_range_target(range_quantile, signal_range_bin_sum_threshold, wilson_z, min_signal_rate, max_signal_rate, min_signal_count_train, min_signal_count_final, min_hit_count_train, min_hit_count_final)
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = L.build_dataset(ticker)
    required_cols = {"high_pct_label", "low_mag_pct_label"}
    missing = sorted(required_cols - set(data.columns))
    if missing:
        raise ValueError(f"missing required range columns: {missing}")
    all_features = L.feature_columns(data)

    train_results: list[dict[str, Any]] = []
    for idx, split in enumerate(TRAIN_SPLITS, 1):
        print(f"TRAIN_START label={split['label']}", flush=True)
        result = train_one_split(ticker=ticker, split_idx=idx, split=split, seed_base=seed_base, data=data, all_features=all_features)
        print(f"TRAIN_DONE label={split['label']} rows={len(result['rows'])} generations={result['generations_run']} early_stop={result['early_stop']} elapsed={result['elapsed_sec']:.1f}", flush=True)
        train_results.append(result)

    predictor_rows: list[dict[str, Any]] = []
    ga_history_rows: list[dict[str, Any]] = []
    for result in train_results:
        predictor_rows.extend(result["rows"])
        ga_history_rows.extend(result["history"])

    representative_by_sig, origins_by_sig = build_representatives(train_results)
    unique_sigs = set(representative_by_sig)
    alive = set(unique_sigs)
    period_metric_rows: list[dict[str, Any]] = []
    early_cut_rows: list[dict[str, Any]] = []
    survivor_rows_by_stage: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    first_fail_by_sig: dict[str, dict[str, Any]] = {}
    evaluated_periods_by_sig: dict[str, list[str]] = defaultdict(list)
    metrics_by_sig_period: dict[tuple[str, str], dict[str, Any]] = {}
    actual_eval_count = 0
    max_eval_count = len(unique_sigs) * len(PERIODS_TEMPLATE)

    for period in PERIODS_TEMPLATE:
        reached = set(alive)
        print(f"EVAL_START period={period['label']} reached={len(reached)}", flush=True)
        pdf = period_frame_checked(data, str(period["start"]), str(period["end"]), str(period["label"]))
        scored = evaluate_mixed_population(
            ticker=ticker,
            alive_sigs=reached,
            representative_by_sig=representative_by_sig,
            origins_by_sig=origins_by_sig,
            df=pdf,
            period=period,
        )
        actual_eval_count += len(scored)
        passed_sigs = {str(row["signature"]) for row in scored if row.get("passed_gate")}
        for row in scored:
            sig = str(row["signature"])
            evaluated_periods_by_sig[sig].append(str(period["label"]))
            metrics_by_sig_period[(sig, str(period["label"]))] = dict(row)
            period_metric_rows.append(dict(row))
            if not row.get("passed_gate") and sig not in first_fail_by_sig:
                first_fail_by_sig[sig] = {
                    "signature": sig,
                    "failed_period_label": period["label"],
                    "failed_period_order": period["order"],
                    "failed_period_kind": period["kind"],
                    "fail_reasons": row.get("fail_reasons") or [],
                }
        top_rows = sorted(scored, key=lambda r: safe_float(r.get("fitness")), reverse=True)[:20]
        for rank, row in enumerate([r for r in top_rows if r.get("passed_gate")], 1):
            survivor_rows_by_stage.append({"stage_period_label": period["label"], "stage_rank": rank, **dict(row)})
        alive = passed_sigs
        print(f"EVAL_DONE period={period['label']} pass={len(alive)}", flush=True)
        trace.append({
            "period_label": period["label"],
            "period_kind": period["kind"],
            "period_order": period["order"],
            "reached": len(reached),
            "passed": len(alive),
            "failed": len(reached) - len(alive),
            "ga_ran": False,
            "new_individuals_created": 0,
        })
        if not alive:
            break

    for sig in sorted(unique_sigs):
        reached_periods = evaluated_periods_by_sig.get(sig, [])
        skipped = [period["label"] for period in PERIODS_TEMPLATE if period["label"] not in reached_periods]
        failed = first_fail_by_sig.get(sig)
        early_cut_rows.append({
            "ticker": ticker,
            "signature": sig,
            "origin_count": len(origins_by_sig[sig]),
            "origin_train_labels": sorted({origin["train_label"] for origin in origins_by_sig[sig]}),
            "representative_train_label": representative_by_sig[sig]["origin"]["train_label"],
            "evaluated_period_count": len(reached_periods),
            "evaluated_periods": reached_periods,
            "skipped_period_count": len(skipped),
            "skipped_periods": skipped,
            "survived_all_5": sig in alive,
            "failed_period_label": failed.get("failed_period_label") if failed else None,
            "failed_period_order": failed.get("failed_period_order") if failed else None,
            "failed_period_kind": failed.get("failed_period_kind") if failed else None,
            "fail_reasons": failed.get("fail_reasons") if failed else [],
        })
        if failed:
            for period in PERIODS_TEMPLATE:
                if period["label"] in skipped:
                    period_metric_rows.append({
                        "ticker": ticker,
                        "signature": sig,
                        "period_label": period["label"],
                        "period_kind": period["kind"],
                        "period_order": period["order"],
                        "period_start": period["start"],
                        "period_end": period["end"],
                        "status": "skipped_after_early_cut",
                        "passed_gate": False,
                        "fail_reasons": [],
                        "origin_count": len(origins_by_sig[sig]),
                        "origin_train_labels": sorted({origin["train_label"] for origin in origins_by_sig[sig]}),
                    })

    final_survivor_rows = []
    for sig in sorted(alive):
        entry = representative_by_sig[sig]
        final_survivor_rows.append({
            "ticker": ticker,
            "target_mode": TARGET_MODE,
            "signature": sig,
            "origin_count": len(origins_by_sig[sig]),
            "origin_train_labels": sorted({origin["train_label"] for origin in origins_by_sig[sig]}),
            "origins": origins_by_sig[sig],
            "representative_train_label": entry["origin"]["train_label"],
            "representative_train_fitness": safe_float(entry["origin"].get("train_fitness")),
            "predictor": L.individual_to_dict(entry["individual"]),
            "periods": [metrics_by_sig_period.get((sig, period["label"]), {}) for period in PERIODS_TEMPLATE],
        })

    distributions = {}
    for period in PERIODS_TEMPLATE:
        pdf = period_frame_checked(data, str(period["start"]), str(period["end"]), str(period["label"]))
        rng_arr = range_pct_array(pdf)
        distributions[period["label"]] = {
            "start": period["start"],
            "end": period["end"],
            "kind": period["kind"],
            "range_mean_pct": float(np.nanmean(rng_arr)) if len(rng_arr) else 0.0,
            "range_median_pct": float(np.nanmedian(rng_arr)) if len(rng_arr) else 0.0,
            "range_q70_pct": float(np.nanquantile(rng_arr, 0.70)) if len(rng_arr) else 0.0,
            "range_q90_pct": float(np.nanquantile(rng_arr, 0.90)) if len(rng_arr) else 0.0,
            "high_bin": L.distribution(pdf["high_bin"].to_numpy(dtype=int)),
            "low_bin": L.distribution(pdf["low_bin"].to_numpy(dtype=int)),
        }
    train_baselines = {result["split"]["label"]: result["baseline_spec"] for result in train_results}
    source_counts = Counter(m.get("source", "unknown") for m in feature_meta)

    write_jsonl(out_dir / "predictors_all.jsonl", predictor_rows)
    write_jsonl(out_dir / "ga_history.jsonl", ga_history_rows)
    write_jsonl(out_dir / "period_metrics_all.jsonl", period_metric_rows)
    write_jsonl(out_dir / "early_cut_log.jsonl", early_cut_rows)
    write_jsonl(out_dir / "stage_survivors.jsonl", survivor_rows_by_stage)
    write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)

    fail_counts = Counter(str(row.get("failed_period_label") or "SURVIVED") for row in early_cut_rows)
    target_desc = {
        "mode": TARGET_MODE,
        "range_pct": "high_pct_label + low_mag_pct_label",
        "range_quantile": RANGE_QUANTILE,
        "label": "range_pct >= representative origin train split quantile threshold",
        "signal": f"predicted_high_bin + predicted_low_bin >= {SIGNAL_RANGE_BIN_SUM_THRESHOLD}",
        "objective": "large-range precision lower bound + base-rate lift lower bound + minimum signal count",
        "wilson_z": WILSON_Z,
        "min_signal_rate_pct_soft": MIN_SIGNAL_RATE,
        "max_signal_rate_pct_soft": MAX_SIGNAL_RATE,
        "min_signal_count_train": MIN_SIGNAL_COUNT_TRAIN,
        "min_signal_count_final": MIN_SIGNAL_COUNT_FINAL,
        "min_hit_count_train": MIN_HIT_COUNT_TRAIN,
        "min_hit_count_final": MIN_HIT_COUNT_FINAL,
    }
    config = {
        "ticker": ticker,
        "runner": "scripts/research/run_range_predictor_stage2_v3.py",
        "legacy_feature_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}",
        "mode": "original_stage2_train123_independent_ga_then_early_cut_large_range",
        "stage2_original_reference": "scripts/research/run_stage2.py: TRAIN_SPLITS independent GA; PERIODS_TEMPLATE early-cut stress -> train_3 -> train_2 -> train_1 -> oos",
        "train_splits": list(TRAIN_SPLITS),
        "evaluation_periods": list(PERIODS_TEMPLATE),
        "early_cut_order": [period["label"] for period in PERIODS_TEMPLATE],
        "ga": {
            "population_per_train_split": L.POPULATION,
            "train_split_count": len(TRAIN_SPLITS),
            "expected_candidate_rows": L.POPULATION * len(TRAIN_SPLITS),
            "generations": L.GENERATIONS,
            "patience": L.PATIENCE,
            "elite_ratio": L.ELITE_RATIO,
            "mutation_rate": L.MUTATION_RATE,
            "rule_count": L.RULE_COUNT,
            "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO,
            "seed_base": seed_base,
            "train_splits_independent": True,
            "post_train_re_evolution": False,
            "post_train_new_individuals": 0,
            "min_band_width_q": L.MIN_BAND_WIDTH_Q,
            "max_band_width_q": L.MAX_BAND_WIDTH_Q,
            "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS],
        },
        "target": target_desc,
        "train_baselines": train_baselines,
        "lookahead_report": {
            "pass": True,
            "feature_quantile_spec": "each candidate uses its representative origin train split qspec; no eval-period refit",
            "label_threshold": "each candidate uses its representative origin train split range threshold; no eval-period threshold refit",
            "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap",
            "flow_features": "optional D-1 orderbook/flow columns if cache provides them",
            "market_features": "ETF D0 gap or D-1 confirmed values only",
            "news_features": "market_history rows joined from D-1 date only",
            "label_columns": "D-day high_pct_label/low_mag_pct_label are labels only, not features",
            "excluded": ["D0 high/low/close as features", "future trading results as features", "eval-period qspec refit", "eval-period threshold refit"],
        },
        "feature_sources": dict(source_counts),
        "bin_labels": L.BIN_LABELS,
        "distributions": distributions,
    }
    write_json(out_dir / "config.json", config)
    actual_eval_ratio = float(actual_eval_count / max_eval_count) if max_eval_count else 0.0
    summary = {
        "ticker": ticker,
        "mode": "original_stage2_train123_independent_ga_then_early_cut_large_range",
        "target": target_desc,
        "generated_candidate_rows": len(predictor_rows),
        "unique_signatures": len(unique_sigs),
        "survivor_count": len(final_survivor_rows),
        "survivor_signatures": [row["signature"] for row in final_survivor_rows],
        "stage_trace": trace,
        "fail_counts_by_first_failed_period": dict(fail_counts),
        "actual_period_evaluations": actual_eval_count,
        "max_period_evaluations": max_eval_count,
        "actual_eval_ratio": actual_eval_ratio,
        "period_eval_saved_ratio": 1.0 - actual_eval_ratio,
        "ga_generations_run_by_train": {result["split"]["label"]: result["generations_run"] for result in train_results},
        "ga_early_stop_triggered_by_train": {result["split"]["label"]: result["early_stop"] for result in train_results},
        "elapsed_sec": time.time() - started,
        "outputs": {
            "predictors_all": str(out_dir / "predictors_all.jsonl"),
            "ga_history": str(out_dir / "ga_history.jsonl"),
            "period_metrics_all": str(out_dir / "period_metrics_all.jsonl"),
            "early_cut_log": str(out_dir / "early_cut_log.jsonl"),
            "stage_survivors": str(out_dir / "stage_survivors.jsonl"),
            "final_survivors": str(out_dir / "final_survivors.jsonl"),
            "config": str(out_dir / "config.json"),
            "summary": str(out_dir / "summary.json"),
        },
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(L.json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Original Stage2 train1/train2/train3 independent GA + early-cut evaluation for next-day large-range labels")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=None, help="accepted for compatibility; original Stage2 does not pre-limit train split populations")
    p.add_argument("--range-quantile", type=float, default=0.70)
    p.add_argument("--signal-range-bin-sum-threshold", type=int, default=4)
    p.add_argument("--wilson-z", type=float, default=1.0)
    p.add_argument("--min-signal-rate", type=float, default=3.0)
    p.add_argument("--max-signal-rate", type=float, default=40.0)
    p.add_argument("--min-signal-count-train", type=int, default=12)
    p.add_argument("--min-signal-count-final", type=int, default=10)
    p.add_argument("--min-hit-count-train", type=int, default=4)
    p.add_argument("--min-hit-count-final", type=int, default=3)
    p.add_argument("--parallel", action="store_true", help="accepted for interface parity; not used")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else L.default_seed_base(ticker)
    run_original_stage2_predictor(
        ticker=ticker,
        out_dir=out_dir,
        seed_base=seed_base,
        range_quantile=float(args.range_quantile),
        signal_range_bin_sum_threshold=int(args.signal_range_bin_sum_threshold),
        wilson_z=float(args.wilson_z),
        min_signal_rate=float(args.min_signal_rate),
        max_signal_rate=float(args.max_signal_rate),
        min_signal_count_train=int(args.min_signal_count_train),
        min_signal_count_final=int(args.min_signal_count_final),
        min_hit_count_train=int(args.min_hit_count_train),
        min_hit_count_final=int(args.min_hit_count_final),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
