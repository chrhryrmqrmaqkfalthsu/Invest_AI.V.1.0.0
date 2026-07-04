#!/usr/bin/env python3
"""
Rolling Stage2 + prior-5-day GA, next-day large-range volatility label version.

핵심:
- 방향성 LONG/SHORT 진입 성공을 직접 맞히지 않는다.
- 다음날 전체 변동폭이 큰 날인지 예측한다.
- range_pct = high_pct_label + low_mag_pct_label
- rolling train 기준 range_pct 상위 quantile을 large range 라벨로 둔다.
- 기본값: train 상위 30%, 즉 --range-quantile 0.70.

목표:
- “내일 크게 움직일 날인가”를 먼저 찾는다.
- Stage2 방향 신호와 결합하기 전 단계의 변동성 후보 필터로 쓴다.
- fitness는 precision lower bound, base-rate 대비 lift lower bound, 최소 신호 수를 본다.

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
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_COMMIT = "b03f39b"
LEGACY_PATH = "scripts/research/run_range_predictor_stage2_v3.py"

TARGET_MODE = "next_day_large_range"
RANGE_QUANTILE = 0.70
SIGNAL_RANGE_BIN_SUM_THRESHOLD = 4
WILSON_Z = 1.0
MIN_SIGNAL_RATE = 3.0
MAX_SIGNAL_RATE = 40.0
MIN_SIGNAL_COUNT_TRAIN = 12
MIN_SIGNAL_COUNT_FINAL = 10
MIN_HIT_COUNT_TRAIN = 4
MIN_HIT_COUNT_FINAL = 3


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
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_large_range_q{q}_{time.strftime('%Y%m%d')}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("cannot allocate output directory")


def build_rolling_splits(data, train_days: int, step_days: int, start: str | None, end: str | None) -> list[dict[str, Any]]:
    dates = list(data["date"].astype(str).drop_duplicates())
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    if len(dates) < train_days:
        raise ValueError(f"not enough dates for rolling train_days={train_days}: available={len(dates)}")
    out: list[dict[str, Any]] = []
    idx = 0
    split_no = 1
    while idx + train_days <= len(dates):
        s = dates[idx]
        e = dates[idx + train_days - 1]
        out.append({"label": f"roll_{split_no:03d}_{s}_{e}", "train_start": s, "train_end": e, "roll_index": split_no, "train_days": train_days, "step_days": step_days})
        idx += step_days
        split_no += 1
    return out


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


def label_arrays(df, spec: Mapping[str, Any]):
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
        "source": "rolling train split next-day large range baseline",
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


def range_evaluate_population(pop, df, features: list[str], qspec: dict[str, dict[str, list[float]]], label: str, kind: str) -> list[dict[str, Any]]:
    raw = []
    for rank, ind in enumerate(pop, 1):
        m = evaluate_range_predictor(ind, df, features, qspec)
        raw.append({"rank_before_score": rank, "signature": ind.signature or predictor_signature(ind), "period_label": label, "period_kind": kind, **m})
    scored = range_score_period_candidates(raw)
    for row in scored:
        row["fail_reasons"] = range_fail_reasons(row, kind)
        row["passed_gate"] = not row["fail_reasons"]
    return scored


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
    L.evaluate_population = range_evaluate_population


def run_rolling_stage2_predictor(
    ticker: str,
    out_dir: Path,
    seed_base: int,
    survivor_count: int,
    rolling_train_days: int,
    rolling_step_days: int,
    rolling_start: str | None,
    rolling_end: str | None,
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
    rolling_splits = build_rolling_splits(data, rolling_train_days, rolling_step_days, rolling_start, rolling_end)

    seed_pop = None
    all_predictor_rows: list[dict[str, Any]] = []
    all_history_rows: list[dict[str, Any]] = []
    train_gate_rows: list[dict[str, Any]] = []
    stage_survivor_rows: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    features_used_union: set[str] = set()
    final_qspec: dict[str, Any] = {}

    for split_idx, split in enumerate(rolling_splits, 1):
        rng = random.Random(seed_base + split_idx * 1000)
        train_df = L.period_frame(data, split["train_start"], split["train_end"])
        qspec = L.make_quantile_spec(train_df, all_features)
        usable_features = [f for f in all_features if f in qspec]
        features_used_union.update(usable_features)
        baseline_spec = L.make_baseline_spec(train_df)
        init_pop = L.prepare_population_for_split(seed_pop, rng, qspec, baseline_spec)
        pop, history = L.run_ga_on_split(init_pop, train_df, usable_features, qspec, split, seed_base + split_idx)
        for ind in pop:
            ind.metrics = L.evaluate_predictor(ind, train_df, usable_features, qspec)
            ind.fitness = safe_float(ind.metrics.get("fitness"))
            ind.signature = predictor_signature(ind)
        scored = L.evaluate_population(pop, train_df, usable_features, qspec, split["label"], "train")
        survivors, selected_rows = L.select_survivors(pop, scored, survivor_count)

        for rank, ind in enumerate(pop, 1):
            all_predictor_rows.append({"ticker": ticker, "target_mode": TARGET_MODE, "train_label": split["label"], "origin_rank": rank, "signature": ind.signature or predictor_signature(ind), "fitness": safe_float(ind.fitness), "metrics": ind.metrics, "predictor": L.individual_to_dict(ind), "stage": split_idx, "rolling_split": split})
        for h in history:
            h.update({"generations_run": len(history), "early_stop_triggered": len(history) < L.GENERATIONS, "rolling_split": split, "target_mode": TARGET_MODE})
        all_history_rows.extend(history)
        for row in scored:
            train_gate_rows.append({**dict(row), "ticker": ticker, "target_mode": TARGET_MODE, "stage": split_idx, "train_start": split["train_start"], "train_end": split["train_end"], "rolling_split": split})
        for rank, row in enumerate(selected_rows, 1):
            stage_survivor_rows.append({"ticker": ticker, "target_mode": TARGET_MODE, "stage": split_idx, "train_label": split["label"], "survivor_rank": rank, "rolling_split": split, **row})
        gate_passed_count = sum(1 for r in scored if r.get("passed_gate"))
        trace.append({"stage": split_idx, "target_mode": TARGET_MODE, "train_label": split["label"], "train_start": split["train_start"], "train_end": split["train_end"], "input_seed_count": len(seed_pop or []), "population": len(pop), "gate_passed_count": gate_passed_count, "selected_survivor_count": len(survivors), "fallback_used": gate_passed_count == 0, "best_fitness": safe_float(pop[0].fitness), "best_signature": pop[0].signature, "feature_count": len(usable_features), "train_large_range_rate_pct": safe_float(baseline_spec.get("train_large_range_rate_pct")), "train_range_threshold_pct": safe_float(baseline_spec.get("range_large_threshold_pct")), "train_range_mean_pct": safe_float(baseline_spec.get("train_range_mean_pct")), "train_range_q90_pct": safe_float(baseline_spec.get("train_range_q90_pct"))})
        seed_pop = survivors
        final_qspec = qspec

    final_pop = seed_pop or []
    final_periods = L.build_final_periods(data)
    final_eval_rows: list[dict[str, Any]] = []
    alive = final_pop
    final_trace: list[dict[str, Any]] = []
    features_final = sorted(f for f in features_used_union if f in final_qspec)

    for period in final_periods:
        pdf = L.period_frame(data, period["start"], period["end"])
        scored = L.evaluate_population(alive, pdf, features_final, final_qspec, period["label"], period["kind"])
        passed_sigs = {str(r.get("signature")) for r in scored if r.get("passed_gate")}
        for row in scored:
            final_eval_rows.append({**dict(row), "ticker": ticker, "target_mode": TARGET_MODE, "period_start": period["start"], "period_end": period["end"]})
        final_trace.append({"period_label": period["label"], "period_kind": period["kind"], "reached": len(alive), "passed": len(passed_sigs), "failed": len(alive) - len(passed_sigs)})
        alive = [ind for ind in alive if (ind.signature or predictor_signature(ind)) in passed_sigs]

    final_survivor_rows = [{"ticker": ticker, "target_mode": TARGET_MODE, "signature": ind.signature or predictor_signature(ind), "predictor": L.individual_to_dict(ind)} for ind in alive]
    distributions = {}
    for p in final_periods:
        pdf = L.period_frame(data, p["start"], p["end"])
        rng_arr = range_pct_array(pdf)
        distributions[p["label"]] = {"high_bin": L.distribution(pdf["high_bin"].to_numpy(dtype=int)), "low_bin": L.distribution(pdf["low_bin"].to_numpy(dtype=int)), "range_mean_pct": float(np.nanmean(rng_arr)) if len(rng_arr) else 0.0, "range_median_pct": float(np.nanmedian(rng_arr)) if len(rng_arr) else 0.0, "range_q70_pct": float(np.nanquantile(rng_arr, 0.70)) if len(rng_arr) else 0.0, "range_q90_pct": float(np.nanquantile(rng_arr, 0.90)) if len(rng_arr) else 0.0}

    write_jsonl(out_dir / "predictors_all.jsonl", all_predictor_rows)
    write_jsonl(out_dir / "ga_history.jsonl", all_history_rows)
    write_jsonl(out_dir / "train_gate_metrics.jsonl", train_gate_rows)
    write_jsonl(out_dir / "stage_survivors.jsonl", stage_survivor_rows)
    write_jsonl(out_dir / "final_period_metrics.jsonl", final_eval_rows)
    write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)

    source_counts = Counter(m.get("source", "unknown") for m in feature_meta if m.get("feature") in features_final)
    target_desc = {"mode": TARGET_MODE, "range_pct": "high_pct_label + low_mag_pct_label", "range_quantile": RANGE_QUANTILE, "label": "range_pct >= rolling_train_quantile_threshold", "signal": f"predicted_high_bin + predicted_low_bin >= {SIGNAL_RANGE_BIN_SUM_THRESHOLD}", "objective": "large-range precision lower bound + base-rate lift lower bound + minimum signal count", "wilson_z": WILSON_Z, "min_signal_rate_pct_soft": MIN_SIGNAL_RATE, "max_signal_rate_pct_soft": MAX_SIGNAL_RATE, "min_signal_count_train": MIN_SIGNAL_COUNT_TRAIN, "min_signal_count_final": MIN_SIGNAL_COUNT_FINAL, "min_hit_count_train": MIN_HIT_COUNT_TRAIN, "min_hit_count_final": MIN_HIT_COUNT_FINAL}
    config = {"ticker": ticker, "runner": "scripts/research/run_range_predictor_stage2_v3.py", "legacy_feature_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}", "mode": "rolling_stage2_plus_prior5_large_range", "rolling": {"train_days": rolling_train_days, "step_days": rolling_step_days, "start": rolling_start, "end": rolling_end, "split_count": len(rolling_splits), "splits": rolling_splits}, "final_periods": final_periods, "ga": {"population": L.POPULATION, "generations": L.GENERATIONS, "patience": L.PATIENCE, "elite_ratio": L.ELITE_RATIO, "mutation_rate": L.MUTATION_RATE, "rule_count": L.RULE_COUNT, "survivor_count": survivor_count, "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO, "seed_base": seed_base, "min_band_width_q": L.MIN_BAND_WIDTH_Q, "max_band_width_q": L.MAX_BAND_WIDTH_Q, "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS]}, "target": target_desc, "lookahead_report": {"pass": True, "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap", "flow_features": "optional D-1 orderbook/flow columns if cache provides them", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "label_columns": "D-day high_pct_label/low_mag_pct_label are labels only, not features", "final_eval_threshold_reference": "individual baseline threshold from rolling train split; no final-period distribution fitting", "excluded": ["D0 high/low/close as features", "future trading results as features"]}, "feature_count": len(features_final), "feature_sources": dict(source_counts), "bin_labels": L.BIN_LABELS, "distributions": distributions}
    write_json(out_dir / "config.json", config)
    summary = {"ticker": ticker, "mode": "rolling_stage2_plus_prior5_large_range", "target": target_desc, "rolling_split_count": len(rolling_splits), "stage_trace": trace, "final_trace": final_trace, "final_survivor_count": len(alive), "final_survivor_signatures": [ind.signature or predictor_signature(ind) for ind in alive], "elapsed_sec": time.time() - started, "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "train_gate_metrics": str(out_dir / "train_gate_metrics.jsonl"), "stage_survivors": str(out_dir / "stage_survivors.jsonl"), "final_period_metrics": str(out_dir / "final_period_metrics.jsonl"), "final_survivors": str(out_dir / "final_survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")}}
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(L.json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rolling Stage2 + prior 5 days GA for next-day large range volatility labels")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=L.SURVIVOR_COUNT)
    p.add_argument("--rolling-train-days", type=int, default=252)
    p.add_argument("--rolling-step-days", type=int, default=21)
    p.add_argument("--rolling-start", default="2022-07-01")
    p.add_argument("--rolling-end", default="2025-06-30")
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
    run_rolling_stage2_predictor(
        ticker=ticker,
        out_dir=out_dir,
        seed_base=seed_base,
        survivor_count=max(1, int(args.survivor_count)),
        rolling_train_days=max(50, int(args.rolling_train_days)),
        rolling_step_days=max(1, int(args.rolling_step_days)),
        rolling_start=args.rolling_start,
        rolling_end=args.rolling_end,
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
