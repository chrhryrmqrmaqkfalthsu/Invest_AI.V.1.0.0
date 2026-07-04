#!/usr/bin/env python3
"""
Rolling Stage2 + prior-5-day GA, TP/SL 실전형 LONG label version.

핵심 변경:
- 기존 `다음날 high >= +2%` 단순 hit label을 버린다.
- 다음날 시가 진입 기준 TP/SL 라벨을 만든다.
- 기본 TP +2%, SL -1%, horizon 1일.
- 일봉 OHLC만 있으므로 같은 날 TP와 SL이 둘 다 닿으면 보수적으로 loss 처리한다.
- timeout, 즉 TP 미도달도 목적 실패로 보고 loss 처리한다.
- fitness는 raw precision이 아니라 EV lower bound, precision lift lower bound, 최소 신호 수를 우선한다.

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

EVENT_SIDE = "HIGH"
SIGNAL_BIN_THRESHOLD = 3
TAKE_PROFIT_PCT = 2.0
STOP_LOSS_PCT = 1.0
COST_PCT = 0.15
WILSON_Z = 1.0
MIN_SIGNAL_RATE = 3.0
MAX_SIGNAL_RATE = 25.0
MIN_SIGNAL_COUNT_TRAIN = 12
MIN_SIGNAL_COUNT_FINAL = 10
MIN_TP_COUNT_TRAIN = 4
MIN_TP_COUNT_FINAL = 3


def _load_legacy_module() -> types.ModuleType:
    code = subprocess.check_output(["git", "show", f"{LEGACY_COMMIT}:{LEGACY_PATH}"], cwd=str(PROJECT_ROOT), text=True)
    mod = types.ModuleType("_km_range_predictor_v3_b03f39b")
    mod.__file__ = str(PROJECT_ROOT / LEGACY_PATH)
    mod.__name__ = "_km_range_predictor_v3_b03f39b"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


L = _load_legacy_module()
_ORIG_RANDOM_RULE = L.random_rule
_ORIG_MUTATE_RULE = L.mutate_rule


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
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_tpsl_tp{TAKE_PROFIT_PCT:g}_sl{STOP_LOSS_PCT:g}_{time.strftime('%Y%m%d')}_".replace(".", "p")
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
    out = []
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


def trade_label_arrays(df):
    high_pct = df["high_pct_label"].to_numpy(dtype=float)
    low_mag_pct = df["low_mag_pct_label"].to_numpy(dtype=float)
    tp_hit = high_pct >= TAKE_PROFIT_PCT
    sl_hit = low_mag_pct >= STOP_LOSS_PCT
    ambiguous_both_hit = tp_hit & sl_hit
    timeout = ~tp_hit & ~sl_hit
    win = tp_hit & ~sl_hit
    loss = ~win
    return win.astype(bool), loss.astype(bool), tp_hit.astype(bool), sl_hit.astype(bool), ambiguous_both_hit.astype(bool), timeout.astype(bool)


def signal_array(ph: np.ndarray) -> np.ndarray:
    return ph.astype(int) >= SIGNAL_BIN_THRESHOLD


def trade_metrics(y_win: np.ndarray, signal: np.ndarray) -> dict[str, float]:
    y_win = y_win.astype(bool)
    signal = signal.astype(bool)
    tp = float(np.sum(y_win & signal))
    fp = float(np.sum(~y_win & signal))
    tn = float(np.sum(~y_win & ~signal))
    fn = float(np.sum(y_win & ~signal))
    n = max(1.0, float(len(y_win)))
    signal_count = tp + fp
    actual_win_count = tp + fn
    precision = tp / max(1.0, signal_count) * 100.0
    recall = tp / max(1.0, actual_win_count) * 100.0
    specificity = tn / max(1.0, tn + fp) * 100.0
    acc = (tp + tn) / n * 100.0
    bal = (recall + specificity) / 2.0
    f1 = (2.0 * precision * recall / max(1e-12, precision + recall)) if (precision + recall) > 0 else 0.0
    signal_rate = signal_count / n * 100.0
    actual_win_rate = actual_win_count / n * 100.0
    precision_lcb = wilson_lower_bound_pct(tp, signal_count)
    base_win_lcb = wilson_lower_bound_pct(actual_win_count, n)
    ev_pct = (precision / 100.0) * TAKE_PROFIT_PCT - (1.0 - precision / 100.0) * STOP_LOSS_PCT - COST_PCT if signal_count > 0 else -COST_PCT
    ev_lcb_pct = (precision_lcb / 100.0) * TAKE_PROFIT_PCT - (1.0 - precision_lcb / 100.0) * STOP_LOSS_PCT - COST_PCT if signal_count > 0 else -COST_PCT
    return {
        "trade_acc_pct": acc,
        "trade_bal_acc_pct": bal,
        "trade_precision_pct": precision,
        "trade_precision_lcb_pct": precision_lcb,
        "trade_recall_pct": recall,
        "trade_specificity_pct": specificity,
        "trade_f1_pct": f1,
        "trade_signal_rate_pct": signal_rate,
        "trade_actual_win_rate_pct": actual_win_rate,
        "trade_base_win_lcb_pct": base_win_lcb,
        "trade_precision_lift_pp": precision - actual_win_rate,
        "trade_precision_lift_lcb_pp": precision_lcb - actual_win_rate,
        "trade_precision_lcb_vs_base_lcb_pp": precision_lcb - base_win_lcb,
        "trade_ev_pct": ev_pct,
        "trade_ev_lcb_pct": ev_lcb_pct,
        "trade_tp": tp,
        "trade_fp": fp,
        "trade_tn": tn,
        "trade_fn": fn,
        "trade_signal_count": signal_count,
        "trade_actual_win_count": actual_win_count,
    }


def make_trade_baseline_spec(train_df) -> dict[str, Any]:
    win, _, tp_hit, sl_hit, ambiguous, timeout = trade_label_arrays(train_df)
    return {
        "target_mode": "long_tpsl_path_aware_daily_ohlc_conservative",
        "event_side": EVENT_SIDE,
        "signal_bin_threshold": SIGNAL_BIN_THRESHOLD,
        "take_profit_pct": TAKE_PROFIT_PCT,
        "stop_loss_pct": STOP_LOSS_PCT,
        "cost_pct": COST_PCT,
        "wilson_z": WILSON_Z,
        "train_win_rate_pct": float(np.mean(win) * 100.0) if len(win) else 0.0,
        "train_tp_hit_rate_pct": float(np.mean(tp_hit) * 100.0) if len(tp_hit) else 0.0,
        "train_sl_hit_rate_pct": float(np.mean(sl_hit) * 100.0) if len(sl_hit) else 0.0,
        "train_ambiguous_both_hit_rate_pct": float(np.mean(ambiguous) * 100.0) if len(ambiguous) else 0.0,
        "train_timeout_rate_pct": float(np.mean(timeout) * 100.0) if len(timeout) else 0.0,
        "exact_high_bin": 0,
        "exact_low_bin": 0,
        "adjacent_high_bin": 0,
        "adjacent_low_bin": 0,
        "source": "rolling train split TP/SL long trade label baseline",
    }


def score_trade_predictions(df, ph: np.ndarray, pl: np.ndarray, spec: Mapping[str, Any]) -> dict[str, float]:
    win, loss, tp_hit, sl_hit, ambiguous, timeout = trade_label_arrays(df)
    sig = signal_array(ph)
    m = trade_metrics(win, sig)
    m.update(
        {
            "trade_loss_rate_pct": float(np.mean(loss) * 100.0) if len(loss) else 0.0,
            "trade_tp_hit_rate_pct": float(np.mean(tp_hit) * 100.0) if len(tp_hit) else 0.0,
            "trade_sl_hit_rate_pct": float(np.mean(sl_hit) * 100.0) if len(sl_hit) else 0.0,
            "trade_ambiguous_both_hit_rate_pct": float(np.mean(ambiguous) * 100.0) if len(ambiguous) else 0.0,
            "trade_timeout_rate_pct": float(np.mean(timeout) * 100.0) if len(timeout) else 0.0,
        }
    )
    return m


def trade_penalty(metrics: Mapping[str, Any]) -> dict[str, float]:
    signal_rate = safe_float(metrics.get("trade_signal_rate_pct"))
    signal_count = safe_float(metrics.get("trade_signal_count"))
    tp_count = safe_float(metrics.get("trade_tp"))
    rate_low = max(0.0, MIN_SIGNAL_RATE - signal_rate)
    rate_high = max(0.0, signal_rate - MAX_SIGNAL_RATE)
    signal_shortfall = max(0.0, float(MIN_SIGNAL_COUNT_TRAIN) - signal_count)
    tp_shortfall = max(0.0, float(MIN_TP_COUNT_TRAIN) - tp_count)
    total = rate_low * 0.80 + rate_high * 0.35 + signal_shortfall * 1.50 + tp_shortfall * 2.00
    return {
        "signal_rate_low_penalty": rate_low * 0.80,
        "signal_rate_high_penalty": rate_high * 0.35,
        "signal_count_shortfall_penalty": signal_shortfall * 1.50,
        "tp_count_shortfall_penalty": tp_shortfall * 2.00,
        "total_penalty": total,
    }


def trade_fitness(metrics: Mapping[str, Any]) -> float:
    ev_lcb = safe_float(metrics.get("trade_ev_lcb_pct"))
    lift_lcb = safe_float(metrics.get("trade_precision_lift_lcb_pp"))
    lcb_vs_base = safe_float(metrics.get("trade_precision_lcb_vs_base_lcb_pp"))
    precision_lcb = safe_float(metrics.get("trade_precision_lcb_pct"))
    bal = safe_float(metrics.get("trade_bal_acc_pct"))
    signal_rate = safe_float(metrics.get("trade_signal_rate_pct"))
    raw = ev_lcb * 35.0 + lift_lcb * 1.20 + lcb_vs_base * 0.80 + precision_lcb * 0.20 + (bal - 50.0) * 0.30
    raw -= abs(signal_rate - 13.0) * 0.10
    return raw - safe_float(metrics.get("total_penalty"))


def evaluate_trade_predictor(ind, df, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    ph, pl, pred_diag = L.predict(ind, df[features], qspec)
    scores = score_trade_predictions(df, ph, pl, ind.baseline_spec)
    penalty = trade_penalty(scores)
    metrics = {
        **scores,
        "event_side": EVENT_SIDE,
        "target_mode": "long_tpsl",
        "sample_count": int(len(df)),
        "take_profit_pct": TAKE_PROFIT_PCT,
        "stop_loss_pct": STOP_LOSS_PCT,
        "cost_pct": COST_PCT,
        "signal_bin_threshold": SIGNAL_BIN_THRESHOLD,
        **penalty,
        **pred_diag,
    }
    metrics["fitness"] = trade_fitness(metrics)
    return metrics


def trade_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    final_kind = str(kind).lower() in {"stress", "oos"}
    min_signal_count = MIN_SIGNAL_COUNT_FINAL if final_kind else MIN_SIGNAL_COUNT_TRAIN
    min_tp_count = MIN_TP_COUNT_FINAL if final_kind else MIN_TP_COUNT_TRAIN
    checks = [
        ("sample_count", safe_int(metrics.get("sample_count")), 100, ">="),
        ("member_score", safe_float(metrics.get("member_score")), 10.0, ">="),
        ("trade_signal_count", safe_float(metrics.get("trade_signal_count")), float(min_signal_count), ">="),
        ("trade_tp", safe_float(metrics.get("trade_tp")), float(min_tp_count), ">="),
        ("trade_precision_lift_lcb_pp", safe_float(metrics.get("trade_precision_lift_lcb_pp")), 0.0, ">"),
        ("trade_ev_lcb_pct", safe_float(metrics.get("trade_ev_lcb_pct")), 0.0, ">"),
        ("total_penalty", safe_float(metrics.get("total_penalty")), 12.0, "<="),
    ]
    out = []
    for metric, value, threshold, rule in checks:
        failed = (rule == ">=" and value < threshold) or (rule == ">" and value <= threshold) or (rule == "<=" and value > threshold)
        if failed:
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def trade_score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows:
        return []
    ev_r = L.percentile_ranks([safe_float(r.get("trade_ev_lcb_pct")) for r in rows])
    lift_r = L.percentile_ranks([safe_float(r.get("trade_precision_lift_lcb_pp")) for r in rows])
    precision_r = L.percentile_ranks([safe_float(r.get("trade_precision_lcb_pct")) for r in rows])
    count_r = L.percentile_ranks([safe_float(r.get("trade_signal_count")) for r in rows])
    penalty_r = L.percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(0.0, min(1.0, ev_r[i] * 0.40 + lift_r[i] * 0.25 + precision_r[i] * 0.20 + count_r[i] * 0.05 + penalty_r[i] * 0.10)) * 100.0
        r = dict(row)
        r["member_score"] = round(score, 6)
        r["member_score_components"] = {
            "ev_lcb_percentile": round(ev_r[i], 6),
            "precision_lift_lcb_percentile": round(lift_r[i], 6),
            "precision_lcb_percentile": round(precision_r[i], 6),
            "signal_count_percentile": round(count_r[i], 6),
            "low_penalty_percentile": round(penalty_r[i], 6),
        }
        out.append(r)
    return out


def trade_evaluate_population(pop, df, features: list[str], qspec: dict[str, dict[str, list[float]]], label: str, kind: str) -> list[dict[str, Any]]:
    raw = []
    for rank, ind in enumerate(pop, 1):
        m = evaluate_trade_predictor(ind, df, features, qspec)
        raw.append({"rank_before_score": rank, "signature": ind.signature or predictor_signature(ind), "period_label": label, "period_kind": kind, **m})
    scored = trade_score_period_candidates(raw)
    for row in scored:
        row["fail_reasons"] = trade_fail_reasons(row, kind)
        row["passed_gate"] = not row["fail_reasons"]
    return scored


def forced_random_rule(rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    r = _ORIG_RANDOM_RULE(rng, qspec)
    r.target = EVENT_SIDE
    return r


def forced_mutate_rule(rule, rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    r = _ORIG_MUTATE_RULE(rule, rng, qspec)
    r.target = EVENT_SIDE
    return r


def force_population_side(pop) -> None:
    for ind in pop:
        for rule in ind.rules:
            rule.target = EVENT_SIDE
        ind.signature = None


def install_trade_target(
    take_profit_pct: float,
    stop_loss_pct: float,
    cost_pct: float,
    signal_bin_threshold: int,
    wilson_z: float,
    min_signal_rate: float,
    max_signal_rate: float,
    min_signal_count_train: int,
    min_signal_count_final: int,
    min_tp_count_train: int,
    min_tp_count_final: int,
) -> None:
    global TAKE_PROFIT_PCT, STOP_LOSS_PCT, COST_PCT, SIGNAL_BIN_THRESHOLD, WILSON_Z
    global MIN_SIGNAL_RATE, MAX_SIGNAL_RATE, MIN_SIGNAL_COUNT_TRAIN, MIN_SIGNAL_COUNT_FINAL, MIN_TP_COUNT_TRAIN, MIN_TP_COUNT_FINAL
    TAKE_PROFIT_PCT = float(take_profit_pct)
    STOP_LOSS_PCT = float(stop_loss_pct)
    COST_PCT = float(cost_pct)
    SIGNAL_BIN_THRESHOLD = int(signal_bin_threshold)
    WILSON_Z = float(wilson_z)
    MIN_SIGNAL_RATE = float(min_signal_rate)
    MAX_SIGNAL_RATE = float(max_signal_rate)
    MIN_SIGNAL_COUNT_TRAIN = int(min_signal_count_train)
    MIN_SIGNAL_COUNT_FINAL = int(min_signal_count_final)
    MIN_TP_COUNT_TRAIN = int(min_tp_count_train)
    MIN_TP_COUNT_FINAL = int(min_tp_count_final)
    L.make_baseline_spec = make_trade_baseline_spec
    L.evaluate_predictor = evaluate_trade_predictor
    L.evaluate_population = trade_evaluate_population
    L.random_rule = forced_random_rule
    L.mutate_rule = forced_mutate_rule


def run_rolling_stage2_predictor(
    ticker: str,
    out_dir: Path,
    seed_base: int,
    survivor_count: int,
    rolling_train_days: int,
    rolling_step_days: int,
    rolling_start: str | None,
    rolling_end: str | None,
    take_profit_pct: float,
    stop_loss_pct: float,
    cost_pct: float,
    signal_bin_threshold: int,
    wilson_z: float,
    min_signal_rate: float,
    max_signal_rate: float,
    min_signal_count_train: int,
    min_signal_count_final: int,
    min_tp_count_train: int,
    min_tp_count_final: int,
) -> dict[str, Any]:
    install_trade_target(take_profit_pct, stop_loss_pct, cost_pct, signal_bin_threshold, wilson_z, min_signal_rate, max_signal_rate, min_signal_count_train, min_signal_count_final, min_tp_count_train, min_tp_count_final)
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = L.build_dataset(ticker)
    required_cols = {"high_pct_label", "low_mag_pct_label"}
    missing = sorted(required_cols - set(data.columns))
    if missing:
        raise ValueError(f"missing required TP/SL columns: {missing}")
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
        force_population_side(init_pop)
        pop, history = L.run_ga_on_split(init_pop, train_df, usable_features, qspec, split, seed_base + split_idx)
        force_population_side(pop)
        for ind in pop:
            ind.metrics = L.evaluate_predictor(ind, train_df, usable_features, qspec)
            ind.fitness = safe_float(ind.metrics.get("fitness"))
            ind.signature = predictor_signature(ind)
        scored = L.evaluate_population(pop, train_df, usable_features, qspec, split["label"], "train")
        survivors, selected_rows = L.select_survivors(pop, scored, survivor_count)

        for rank, ind in enumerate(pop, 1):
            all_predictor_rows.append({"ticker": ticker, "event_side": EVENT_SIDE, "target_mode": "long_tpsl", "train_label": split["label"], "origin_rank": rank, "signature": ind.signature or predictor_signature(ind), "fitness": safe_float(ind.fitness), "metrics": ind.metrics, "predictor": L.individual_to_dict(ind), "stage": split_idx, "rolling_split": split})
        for h in history:
            h.update({"generations_run": len(history), "early_stop_triggered": len(history) < L.GENERATIONS, "rolling_split": split, "event_side": EVENT_SIDE, "target_mode": "long_tpsl"})
        all_history_rows.extend(history)
        for row in scored:
            train_gate_rows.append({**dict(row), "ticker": ticker, "event_side": EVENT_SIDE, "target_mode": "long_tpsl", "stage": split_idx, "train_start": split["train_start"], "train_end": split["train_end"], "rolling_split": split})
        for rank, row in enumerate(selected_rows, 1):
            stage_survivor_rows.append({"ticker": ticker, "event_side": EVENT_SIDE, "target_mode": "long_tpsl", "stage": split_idx, "train_label": split["label"], "survivor_rank": rank, "rolling_split": split, **row})
        gate_passed_count = sum(1 for r in scored if r.get("passed_gate"))
        trace.append({"stage": split_idx, "event_side": EVENT_SIDE, "target_mode": "long_tpsl", "train_label": split["label"], "train_start": split["train_start"], "train_end": split["train_end"], "input_seed_count": len(seed_pop or []), "population": len(pop), "gate_passed_count": gate_passed_count, "selected_survivor_count": len(survivors), "fallback_used": gate_passed_count == 0, "best_fitness": safe_float(pop[0].fitness), "best_signature": pop[0].signature, "feature_count": len(usable_features), "train_win_rate_pct": safe_float(baseline_spec.get("train_win_rate_pct")), "train_tp_hit_rate_pct": safe_float(baseline_spec.get("train_tp_hit_rate_pct")), "train_sl_hit_rate_pct": safe_float(baseline_spec.get("train_sl_hit_rate_pct"))})
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
            final_eval_rows.append({**dict(row), "ticker": ticker, "event_side": EVENT_SIDE, "target_mode": "long_tpsl", "period_start": period["start"], "period_end": period["end"]})
        final_trace.append({"period_label": period["label"], "period_kind": period["kind"], "reached": len(alive), "passed": len(passed_sigs), "failed": len(alive) - len(passed_sigs)})
        alive = [ind for ind in alive if (ind.signature or predictor_signature(ind)) in passed_sigs]

    final_survivor_rows = [{"ticker": ticker, "event_side": EVENT_SIDE, "target_mode": "long_tpsl", "signature": ind.signature or predictor_signature(ind), "predictor": L.individual_to_dict(ind)} for ind in alive]
    distributions = {}
    for p in final_periods:
        pdf = L.period_frame(data, p["start"], p["end"])
        win, _, tp_hit, sl_hit, ambiguous, timeout = trade_label_arrays(pdf)
        distributions[p["label"]] = {"high_bin": L.distribution(pdf["high_bin"].to_numpy(dtype=int)), "low_bin": L.distribution(pdf["low_bin"].to_numpy(dtype=int)), "trade_win_rate_pct": float(np.mean(win) * 100.0) if len(win) else 0.0, "tp_hit_rate_pct": float(np.mean(tp_hit) * 100.0) if len(tp_hit) else 0.0, "sl_hit_rate_pct": float(np.mean(sl_hit) * 100.0) if len(sl_hit) else 0.0, "ambiguous_both_hit_rate_pct": float(np.mean(ambiguous) * 100.0) if len(ambiguous) else 0.0, "timeout_rate_pct": float(np.mean(timeout) * 100.0) if len(timeout) else 0.0}

    write_jsonl(out_dir / "predictors_all.jsonl", all_predictor_rows)
    write_jsonl(out_dir / "ga_history.jsonl", all_history_rows)
    write_jsonl(out_dir / "train_gate_metrics.jsonl", train_gate_rows)
    write_jsonl(out_dir / "stage_survivors.jsonl", stage_survivor_rows)
    write_jsonl(out_dir / "final_period_metrics.jsonl", final_eval_rows)
    write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)

    source_counts = Counter(m.get("source", "unknown") for m in feature_meta if m.get("feature") in features_final)
    target_desc = {"mode": "long_tpsl_path_aware_daily_ohlc_conservative", "entry": "D-day open", "take_profit_pct": TAKE_PROFIT_PCT, "stop_loss_pct": STOP_LOSS_PCT, "cost_pct": COST_PCT, "horizon_days": 1, "win": "high_pct_label >= TP and low_mag_pct_label < SL", "loss": "low_mag_pct_label >= SL, TP/SL same-day ambiguous, or timeout no TP", "ambiguous_policy": "same-day TP and SL both touched => loss because daily OHLC has no intraday order", "signal": f"legacy predicted HIGH bin >= {SIGNAL_BIN_THRESHOLD}", "objective": "EV lower bound + precision lift lower bound + minimum signal count", "wilson_z": WILSON_Z, "min_signal_rate_pct_soft": MIN_SIGNAL_RATE, "max_signal_rate_pct_soft": MAX_SIGNAL_RATE, "min_signal_count_train": MIN_SIGNAL_COUNT_TRAIN, "min_signal_count_final": MIN_SIGNAL_COUNT_FINAL, "min_tp_count_train": MIN_TP_COUNT_TRAIN, "min_tp_count_final": MIN_TP_COUNT_FINAL}
    config = {"ticker": ticker, "runner": "scripts/research/run_range_predictor_stage2_v3.py", "legacy_feature_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}", "mode": "rolling_stage2_plus_prior5_long_tpsl", "rolling": {"train_days": rolling_train_days, "step_days": rolling_step_days, "start": rolling_start, "end": rolling_end, "split_count": len(rolling_splits), "splits": rolling_splits}, "final_periods": final_periods, "ga": {"population": L.POPULATION, "generations": L.GENERATIONS, "patience": L.PATIENCE, "elite_ratio": L.ELITE_RATIO, "mutation_rate": L.MUTATION_RATE, "rule_count": L.RULE_COUNT, "survivor_count": survivor_count, "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO, "seed_base": seed_base, "min_band_width_q": L.MIN_BAND_WIDTH_Q, "max_band_width_q": L.MAX_BAND_WIDTH_Q, "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS]}, "target": target_desc, "lookahead_report": {"pass": True, "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap", "flow_features": "optional D-1 orderbook/flow columns if cache provides them", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "label_columns": "D-day high_pct_label/low_mag_pct_label are labels only, not features", "final_eval_quantile_reference": "last rolling train qspec only; no final-period distribution fitting", "excluded": ["D0 high/low/close as features", "future trading results as features"]}, "feature_count": len(features_final), "feature_sources": dict(source_counts), "bin_labels": L.BIN_LABELS, "distributions": distributions}
    write_json(out_dir / "config.json", config)
    summary = {"ticker": ticker, "mode": "rolling_stage2_plus_prior5_long_tpsl", "event_side": EVENT_SIDE, "target": target_desc, "rolling_split_count": len(rolling_splits), "stage_trace": trace, "final_trace": final_trace, "final_survivor_count": len(alive), "final_survivor_signatures": [ind.signature or predictor_signature(ind) for ind in alive], "elapsed_sec": time.time() - started, "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "train_gate_metrics": str(out_dir / "train_gate_metrics.jsonl"), "stage_survivors": str(out_dir / "stage_survivors.jsonl"), "final_period_metrics": str(out_dir / "final_period_metrics.jsonl"), "final_survivors": str(out_dir / "final_survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")}}
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(L.json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rolling Stage2 + prior 5 days GA for TP/SL long trade labels")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=L.SURVIVOR_COUNT)
    p.add_argument("--rolling-train-days", type=int, default=252)
    p.add_argument("--rolling-step-days", type=int, default=21)
    p.add_argument("--rolling-start", default="2022-07-01")
    p.add_argument("--rolling-end", default="2025-06-30")
    p.add_argument("--take-profit-pct", type=float, default=2.0)
    p.add_argument("--stop-loss-pct", type=float, default=1.0)
    p.add_argument("--cost-pct", type=float, default=0.15)
    p.add_argument("--signal-bin-threshold", type=int, default=3)
    p.add_argument("--wilson-z", type=float, default=1.0)
    p.add_argument("--min-signal-rate", type=float, default=3.0)
    p.add_argument("--max-signal-rate", type=float, default=25.0)
    p.add_argument("--min-signal-count-train", type=int, default=12)
    p.add_argument("--min-signal-count-final", type=int, default=10)
    p.add_argument("--min-tp-count-train", type=int, default=4)
    p.add_argument("--min-tp-count-final", type=int, default=3)
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
        take_profit_pct=float(args.take_profit_pct),
        stop_loss_pct=float(args.stop_loss_pct),
        cost_pct=float(args.cost_pct),
        signal_bin_threshold=int(args.signal_bin_threshold),
        wilson_z=float(args.wilson_z),
        min_signal_rate=float(args.min_signal_rate),
        max_signal_rate=float(args.max_signal_rate),
        min_signal_count_train=int(args.min_signal_count_train),
        min_signal_count_final=int(args.min_signal_count_final),
        min_tp_count_train=int(args.min_tp_count_train),
        min_tp_count_final=int(args.min_tp_count_final),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
