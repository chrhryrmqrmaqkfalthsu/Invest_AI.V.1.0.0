#!/usr/bin/env python3
"""
Rolling Stage2 + prior-5-day GA, precision-focused 2% event version.

타깃:
- 다음날 시가 대비 +2% 이상 상방 이벤트(HIGH) 또는 -2% 이상 하방 이벤트(LOW)를 binary로 분류한다.
- --event-side HIGH: 상방 이벤트만 따로 학습/평가한다.
- --event-side LOW: 하방 이벤트만 따로 학습/평가한다.
- 실거래 후보화를 위해 precision을 최우선으로 두고, 신호 빈도는 기본 5~20%로 조인다.

유지:
- 기존 Stage2 + 이전 5일 feature 생성 로직은 b03f39b 버전을 그대로 사용한다.
- 252거래일 rolling window를 21거래일씩 앞으로 밀며 survivor만 다음 창으로 전달한다.
- rule은 분위수 band(q_low~q_high) + softness를 진화시킨다.

Read/write scope:
- OHLCV/cache/news csv는 read-only로 읽는다.
- 결과는 out_dir 아래 연구 산출물만 생성한다.
- run_live, 실거래, 캐시 갱신 없음.
"""
from __future__ import annotations

import argparse
import json
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
EVENT_BIN_THRESHOLD = 3  # bin 3 이상 = 2% 이상 움직임
EVENT_SIDE = "HIGH"
MIN_PRECISION_TRAIN = 65.0
MIN_PRECISION_FINAL = 65.0
MIN_SIGNAL_RATE = 5.0
MAX_SIGNAL_RATE = 20.0


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


def json_safe(value: Any) -> Any:
    return L.json_safe(value)


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


def auto_out_dir(ticker: str, event_side: str) -> Path:
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_precision_event2pct_{event_side.lower()}_{time.strftime('%Y%m%d')}_"
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


def _event_arrays(df):
    high_event = (df["high_bin"].to_numpy(dtype=int) >= EVENT_BIN_THRESHOLD)
    low_event = (df["low_bin"].to_numpy(dtype=int) >= EVENT_BIN_THRESHOLD)
    return high_event, low_event


def _bool_metrics(y: np.ndarray, p: np.ndarray, prefix: str) -> dict[str, float]:
    y = y.astype(bool)
    p = p.astype(bool)
    tp = float(np.sum(y & p))
    tn = float(np.sum(~y & ~p))
    fp = float(np.sum(~y & p))
    fn = float(np.sum(y & ~p))
    n = max(1.0, float(len(y)))
    acc = (tp + tn) / n * 100.0
    recall = tp / max(1.0, tp + fn) * 100.0
    specificity = tn / max(1.0, tn + fp) * 100.0
    precision = tp / max(1.0, tp + fp) * 100.0
    f1 = (2.0 * precision * recall / max(1e-12, precision + recall)) if (precision + recall) > 0 else 0.0
    bal = (recall + specificity) / 2.0
    pred_rate = float(np.mean(p) * 100.0) if len(p) else 0.0
    actual_rate = float(np.mean(y) * 100.0) if len(y) else 0.0
    return {
        f"{prefix}_event_acc_pct": acc,
        f"{prefix}_event_bal_acc_pct": bal,
        f"{prefix}_event_precision_pct": precision,
        f"{prefix}_event_recall_pct": recall,
        f"{prefix}_event_specificity_pct": specificity,
        f"{prefix}_event_f1_pct": f1,
        f"{prefix}_event_pred_rate_pct": pred_rate,
        f"{prefix}_event_actual_rate_pct": actual_rate,
        f"{prefix}_event_tp": tp,
        f"{prefix}_event_fp": fp,
        f"{prefix}_event_tn": tn,
        f"{prefix}_event_fn": fn,
    }


def make_event_baseline_spec(train_df) -> dict[str, Any]:
    high_event, low_event = _event_arrays(train_df)
    high_major = bool(np.mean(high_event) >= 0.5)
    low_major = bool(np.mean(low_event) >= 0.5)
    return {
        "event_bin_threshold": EVENT_BIN_THRESHOLD,
        "event_side": EVENT_SIDE,
        "high_event_default": high_major,
        "low_event_default": low_major,
        "exact_high_bin": EVENT_BIN_THRESHOLD if high_major else 0,
        "exact_low_bin": EVENT_BIN_THRESHOLD if low_major else 0,
        "adjacent_high_bin": EVENT_BIN_THRESHOLD if high_major else 0,
        "adjacent_low_bin": EVENT_BIN_THRESHOLD if low_major else 0,
        "source": "current rolling train split majority binary event baseline",
    }


def score_event_predictions(df, ph: np.ndarray, pl: np.ndarray, spec: Mapping[str, Any]) -> dict[str, float]:
    yh, yl = _event_arrays(df)
    pred_h = ph.astype(int) >= EVENT_BIN_THRESHOLD
    pred_l = pl.astype(int) >= EVENT_BIN_THRESHOLD
    high = _bool_metrics(yh, pred_h, "high")
    low = _bool_metrics(yl, pred_l, "low")
    return {
        **high,
        **low,
        "combined_event_acc_pct": (high["high_event_acc_pct"] + low["low_event_acc_pct"]) / 2.0,
        "combined_event_bal_acc_pct": (high["high_event_bal_acc_pct"] + low["low_event_bal_acc_pct"]) / 2.0,
        "combined_event_f1_pct": (high["high_event_f1_pct"] + low["low_event_f1_pct"]) / 2.0,
        "both_side_match_acc_pct": float((pred_h == yh).mean() * 50.0 + (pred_l == yl).mean() * 50.0) if len(yh) else 0.0,
    }


def fixed_event_scores(df, high_event_default: bool, low_event_default: bool, spec: Mapping[str, Any]) -> dict[str, float]:
    hb = EVENT_BIN_THRESHOLD if high_event_default else 0
    lb = EVENT_BIN_THRESHOLD if low_event_default else 0
    return score_event_predictions(df, np.full(len(df), hb, dtype=int), np.full(len(df), lb, dtype=int), spec)


def side_prefix() -> str:
    return "high" if EVENT_SIDE == "HIGH" else "low" if EVENT_SIDE == "LOW" else "high"


def side_metric_names() -> dict[str, str]:
    p = side_prefix()
    return {
        "acc": f"{p}_event_acc_pct",
        "bal": f"{p}_event_bal_acc_pct",
        "bal_lift": f"{p}_event_bal_acc_lift_pp",
        "f1": f"{p}_event_f1_pct",
        "f1_lift": f"{p}_event_f1_lift_pp",
        "precision": f"{p}_event_precision_pct",
        "recall": f"{p}_event_recall_pct",
        "pred_rate": f"{p}_event_pred_rate_pct",
        "actual_rate": f"{p}_event_actual_rate_pct",
        "tp": f"{p}_event_tp",
        "fp": f"{p}_event_fp",
    }


def precision_signal_penalty(metrics: Mapping[str, Any]) -> dict[str, float]:
    k = side_metric_names()
    pred_rate = safe_float(metrics.get(k["pred_rate"]))
    precision = safe_float(metrics.get(k["precision"]))
    rate_low = max(0.0, MIN_SIGNAL_RATE - pred_rate)
    rate_high = max(0.0, pred_rate - MAX_SIGNAL_RATE)
    precision_shortfall = max(0.0, MIN_PRECISION_TRAIN - precision)
    # fitness에서는 train 기준 precision 부족도 부드럽게 벌점화한다. 최종 gate는 별도로 더 엄격히 본다.
    return {
        "signal_rate_low_penalty": rate_low * 1.20,
        "signal_rate_high_penalty": rate_high * 1.00,
        "precision_shortfall_penalty": precision_shortfall * 0.25,
        "total_penalty": rate_low * 1.20 + rate_high * 1.00 + precision_shortfall * 0.25,
    }


def event_fitness(metrics: Mapping[str, Any]) -> float:
    k = side_metric_names()
    precision = safe_float(metrics.get(k["precision"]))
    bal_lift = safe_float(metrics.get(k["bal_lift"]))
    f1 = safe_float(metrics.get(k["f1"]))
    pred_rate = safe_float(metrics.get(k["pred_rate"]))
    # precision 최우선. recall/F1은 보조. 신호 빈도는 5~20% 중심으로 강하게 조인다.
    raw = precision * 1.45 + bal_lift * 0.80 + f1 * 0.25
    # 10~15% 신호 빈도 근처를 약하게 선호한다.
    raw -= abs(pred_rate - 12.5) * 0.12
    return raw - safe_float(metrics.get("total_penalty"))


def evaluate_event_predictor(ind, df, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    ph, pl, pred_diag = L.predict(ind, df[features], qspec)
    scores = score_event_predictions(df, ph, pl, ind.baseline_spec)
    base = fixed_event_scores(df, bool(ind.baseline_spec.get("high_event_default")), bool(ind.baseline_spec.get("low_event_default")), ind.baseline_spec)
    metrics = {
        **scores,
        "event_side": EVENT_SIDE,
        "sample_count": int(len(df)),
        "combined_event_acc_lift_pp": scores["combined_event_acc_pct"] - base["combined_event_acc_pct"],
        "combined_event_bal_acc_lift_pp": scores["combined_event_bal_acc_pct"] - base["combined_event_bal_acc_pct"],
        "combined_event_f1_lift_pp": scores["combined_event_f1_pct"] - base["combined_event_f1_pct"],
        "high_event_acc_lift_pp": scores["high_event_acc_pct"] - base["high_event_acc_pct"],
        "low_event_acc_lift_pp": scores["low_event_acc_pct"] - base["low_event_acc_pct"],
        "high_event_bal_acc_lift_pp": scores["high_event_bal_acc_pct"] - base["high_event_bal_acc_pct"],
        "low_event_bal_acc_lift_pp": scores["low_event_bal_acc_pct"] - base["low_event_bal_acc_pct"],
        "high_event_f1_lift_pp": scores["high_event_f1_pct"] - base["high_event_f1_pct"],
        "low_event_f1_lift_pp": scores["low_event_f1_pct"] - base["low_event_f1_pct"],
        "baseline_combined_event_bal_acc_pct": base["combined_event_bal_acc_pct"],
        "baseline_combined_event_f1_pct": base["combined_event_f1_pct"],
        **pred_diag,
    }
    metrics.update(precision_signal_penalty(metrics))
    metrics["fitness"] = event_fitness(metrics)
    return metrics


def event_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    k = side_metric_names()
    final_kind = str(kind).lower() in {"stress", "oos"}
    min_precision = MIN_PRECISION_FINAL if final_kind else MIN_PRECISION_TRAIN
    checks = [
        ("sample_count", safe_int(metrics.get("sample_count")), 100, ">="),
        ("member_score", safe_float(metrics.get("member_score")), 10.0, ">="),
        (k["precision"], safe_float(metrics.get(k["precision"])), min_precision, ">="),
        (k["pred_rate"], safe_float(metrics.get(k["pred_rate"])), MIN_SIGNAL_RATE, ">="),
        (k["pred_rate"], safe_float(metrics.get(k["pred_rate"])), MAX_SIGNAL_RATE, "<="),
        (k["bal_lift"], safe_float(metrics.get(k["bal_lift"])), 0.0, ">="),
        ("total_penalty", safe_float(metrics.get("total_penalty")), 8.0, "<="),
    ]
    out = []
    for metric, value, threshold, rule in checks:
        if (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold):
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def event_score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows:
        return []
    k = side_metric_names()
    precision_r = L.percentile_ranks([safe_float(r.get(k["precision"])) for r in rows])
    bal_r = L.percentile_ranks([safe_float(r.get(k["bal_lift"])) for r in rows])
    f1_r = L.percentile_ranks([safe_float(r.get(k["f1"])) for r in rows])
    penalty_r = L.percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    rate_center_r = L.percentile_ranks([-abs(safe_float(r.get(k["pred_rate"])) - 12.5) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(0.0, min(1.0, precision_r[i] * 0.55 + bal_r[i] * 0.15 + f1_r[i] * 0.10 + penalty_r[i] * 0.10 + rate_center_r[i] * 0.10)) * 100.0
        r = dict(row)
        r["member_score"] = round(score, 6)
        r["member_score_components"] = {
            "precision_percentile": round(precision_r[i], 6),
            "bal_lift_percentile": round(bal_r[i], 6),
            "f1_percentile": round(f1_r[i], 6),
            "low_penalty_percentile": round(penalty_r[i], 6),
            "signal_rate_center_percentile": round(rate_center_r[i], 6),
        }
        out.append(r)
    return out


def event_evaluate_population(pop, df, features: list[str], qspec: dict[str, dict[str, list[float]]], label: str, kind: str) -> list[dict[str, Any]]:
    raw = []
    for rank, ind in enumerate(pop, 1):
        m = evaluate_event_predictor(ind, df, features, qspec)
        raw.append({"rank_before_score": rank, "signature": ind.signature or predictor_signature(ind), "period_label": label, "period_kind": kind, **m})
    scored = event_score_period_candidates(raw)
    for row in scored:
        row["fail_reasons"] = event_fail_reasons(row, kind)
        row["passed_gate"] = not row["fail_reasons"]
    return scored


def forced_random_rule(rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    r = _ORIG_RANDOM_RULE(rng, qspec)
    if EVENT_SIDE in {"HIGH", "LOW"}:
        r.target = EVENT_SIDE
    return r


def forced_mutate_rule(rule, rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    r = _ORIG_MUTATE_RULE(rule, rng, qspec)
    if EVENT_SIDE in {"HIGH", "LOW"}:
        r.target = EVENT_SIDE
    return r


def force_population_side(pop) -> None:
    if EVENT_SIDE not in {"HIGH", "LOW"}:
        return
    for ind in pop:
        for rule in ind.rules:
            rule.target = EVENT_SIDE
        ind.signature = None


def install_precision_target(event_side: str, min_precision_train: float, min_precision_final: float, min_signal_rate: float, max_signal_rate: float) -> None:
    global EVENT_SIDE, MIN_PRECISION_TRAIN, MIN_PRECISION_FINAL, MIN_SIGNAL_RATE, MAX_SIGNAL_RATE
    side = str(event_side or "HIGH").strip().upper()
    if side not in {"HIGH", "LOW"}:
        raise ValueError("precision target supports HIGH or LOW only")
    EVENT_SIDE = side
    MIN_PRECISION_TRAIN = float(min_precision_train)
    MIN_PRECISION_FINAL = float(min_precision_final)
    MIN_SIGNAL_RATE = float(min_signal_rate)
    MAX_SIGNAL_RATE = float(max_signal_rate)
    L.make_baseline_spec = make_event_baseline_spec
    L.evaluate_predictor = evaluate_event_predictor
    L.evaluate_population = event_evaluate_population
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
    event_side: str,
    min_precision_train: float,
    min_precision_final: float,
    min_signal_rate: float,
    max_signal_rate: float,
) -> dict[str, Any]:
    install_precision_target(event_side, min_precision_train, min_precision_final, min_signal_rate, max_signal_rate)
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = L.build_dataset(ticker)
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
            all_predictor_rows.append({"ticker": ticker, "event_side": EVENT_SIDE, "train_label": split["label"], "origin_rank": rank, "signature": ind.signature or predictor_signature(ind), "fitness": safe_float(ind.fitness), "metrics": ind.metrics, "predictor": L.individual_to_dict(ind), "stage": split_idx, "rolling_split": split})
        for h in history:
            h["generations_run"] = len(history)
            h["early_stop_triggered"] = len(history) < L.GENERATIONS
            h["rolling_split"] = split
            h["event_side"] = EVENT_SIDE
        all_history_rows.extend(history)
        for row in scored:
            train_gate_rows.append({**dict(row), "ticker": ticker, "event_side": EVENT_SIDE, "stage": split_idx, "train_start": split["train_start"], "train_end": split["train_end"], "rolling_split": split})
        for rank, row in enumerate(selected_rows, 1):
            stage_survivor_rows.append({"ticker": ticker, "event_side": EVENT_SIDE, "stage": split_idx, "train_label": split["label"], "survivor_rank": rank, "rolling_split": split, **row})
        gate_passed_count = sum(1 for r in scored if r.get("passed_gate"))
        trace.append({"stage": split_idx, "event_side": EVENT_SIDE, "train_label": split["label"], "train_start": split["train_start"], "train_end": split["train_end"], "input_seed_count": len(seed_pop or []), "population": len(pop), "gate_passed_count": gate_passed_count, "selected_survivor_count": len(survivors), "fallback_used": gate_passed_count == 0, "best_fitness": safe_float(pop[0].fitness), "best_signature": pop[0].signature, "feature_count": len(usable_features)})
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
            final_eval_rows.append({**dict(row), "ticker": ticker, "event_side": EVENT_SIDE, "period_start": period["start"], "period_end": period["end"]})
        final_trace.append({"period_label": period["label"], "period_kind": period["kind"], "reached": len(alive), "passed": len(passed_sigs), "failed": len(alive) - len(passed_sigs)})
        alive = [ind for ind in alive if (ind.signature or predictor_signature(ind)) in passed_sigs]

    final_survivor_rows = [{"ticker": ticker, "event_side": EVENT_SIDE, "signature": ind.signature or predictor_signature(ind), "predictor": L.individual_to_dict(ind)} for ind in alive]
    distributions = {p["label"]: {"high": L.distribution(L.period_frame(data, p["start"], p["end"])["high_bin"].to_numpy(dtype=int)), "low": L.distribution(L.period_frame(data, p["start"], p["end"])["low_bin"].to_numpy(dtype=int))} for p in final_periods}
    write_jsonl(out_dir / "predictors_all.jsonl", all_predictor_rows)
    write_jsonl(out_dir / "ga_history.jsonl", all_history_rows)
    write_jsonl(out_dir / "train_gate_metrics.jsonl", train_gate_rows)
    write_jsonl(out_dir / "stage_survivors.jsonl", stage_survivor_rows)
    write_jsonl(out_dir / "final_period_metrics.jsonl", final_eval_rows)
    write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)

    source_counts = Counter(m.get("source", "unknown") for m in feature_meta if m.get("feature") in features_final)
    target_desc = {
        "mode": "precision_focused_binary_event",
        "event_side": EVENT_SIDE,
        "high_event": "D-day high_pct >= 2% from D open",
        "low_event": "D-day low_mag_pct >= 2% from D open",
        "event_bin_threshold": EVENT_BIN_THRESHOLD,
        "objective": "selected side precision first, signal rate constrained, balanced accuracy as support",
        "min_precision_train_pct": MIN_PRECISION_TRAIN,
        "min_precision_final_pct": MIN_PRECISION_FINAL,
        "min_signal_rate_pct": MIN_SIGNAL_RATE,
        "max_signal_rate_pct": MAX_SIGNAL_RATE,
        "note": "diagnostic signal only; final trade logic still needs actual TP/SL simulation",
    }
    config = {"ticker": ticker, "runner": "scripts/research/run_range_predictor_stage2_v3.py", "legacy_feature_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}", "mode": f"rolling_stage2_plus_prior5_event2pct_{EVENT_SIDE.lower()}_precision", "rolling": {"train_days": rolling_train_days, "step_days": rolling_step_days, "start": rolling_start, "end": rolling_end, "split_count": len(rolling_splits), "splits": rolling_splits}, "final_periods": final_periods, "ga": {"population": L.POPULATION, "generations": L.GENERATIONS, "patience": L.PATIENCE, "elite_ratio": L.ELITE_RATIO, "mutation_rate": L.MUTATION_RATE, "rule_count": L.RULE_COUNT, "survivor_count": survivor_count, "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO, "seed_base": seed_base, "min_band_width_q": L.MIN_BAND_WIDTH_Q, "max_band_width_q": L.MAX_BAND_WIDTH_Q, "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS]}, "target": target_desc, "lookahead_report": {"pass": True, "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap", "flow_features": "optional D-1 orderbook/flow columns if cache provides them", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "final_eval_quantile_reference": "last rolling train qspec only; no final-period distribution fitting", "excluded": ["D0 high/low/close as features", "future trading results"]}, "feature_count": len(features_final), "feature_sources": dict(source_counts), "bin_labels": L.BIN_LABELS, "distributions": distributions}
    write_json(out_dir / "config.json", config)
    summary = {"ticker": ticker, "mode": f"rolling_stage2_plus_prior5_event2pct_{EVENT_SIDE.lower()}_precision", "event_side": EVENT_SIDE, "rolling_split_count": len(rolling_splits), "stage_trace": trace, "final_trace": final_trace, "final_survivor_count": len(alive), "final_survivor_signatures": [ind.signature or predictor_signature(ind) for ind in alive], "elapsed_sec": time.time() - started, "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "train_gate_metrics": str(out_dir / "train_gate_metrics.jsonl"), "stage_survivors": str(out_dir / "stage_survivors.jsonl"), "final_period_metrics": str(out_dir / "final_period_metrics.jsonl"), "final_survivors": str(out_dir / "final_survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")}}
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rolling Stage2 + prior 5 days precision-focused GA for separated 2pct HIGH/LOW event classification")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=L.SURVIVOR_COUNT)
    p.add_argument("--rolling-train-days", type=int, default=252)
    p.add_argument("--rolling-step-days", type=int, default=21)
    p.add_argument("--rolling-start", default="2022-07-01")
    p.add_argument("--rolling-end", default="2025-06-30")
    p.add_argument("--event-side", choices=["HIGH", "LOW"], default="HIGH")
    p.add_argument("--min-precision-train", type=float, default=65.0)
    p.add_argument("--min-precision-final", type=float, default=65.0)
    p.add_argument("--min-signal-rate", type=float, default=5.0)
    p.add_argument("--max-signal-rate", type=float, default=20.0)
    p.add_argument("--parallel", action="store_true", help="accepted for interface parity; not used")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    event_side = str(args.event_side).strip().upper()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker, event_side)
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
        event_side=event_side,
        min_precision_train=float(args.min_precision_train),
        min_precision_final=float(args.min_precision_final),
        min_signal_rate=float(args.min_signal_rate),
        max_signal_rate=float(args.max_signal_rate),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
