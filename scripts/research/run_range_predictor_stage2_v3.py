#!/usr/bin/env python3
"""
Rolling Stage2 + prior-5-day GA, lowered and side-separated target version.

타깃:
- 정확한 HIGH/LOW 6-bin이나 고저폭 %를 맞히지 않는다.
- 다음날 시가 대비 +2% 이상 상방 이벤트(HIGH) 또는 -2% 이상 하방 이벤트(LOW)를 binary로 분류한다.
- --event-side HIGH: 상방 이벤트만 따로 학습/평가한다.
- --event-side LOW: 하방 이벤트만 따로 학습/평가한다.
- --event-side BOTH: 기존처럼 상방/하방 동시 binary를 본다.

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
EVENT_SIDE = "BOTH"


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
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_rolling_event2pct_{event_side.lower()}_{time.strftime('%Y%m%d')}_"
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
    both_actual = yh & yl
    both_pred = pred_h & pred_l
    either_actual = yh | yl
    either_pred = pred_h | pred_l
    both = _bool_metrics(both_actual, both_pred, "both")
    either = _bool_metrics(either_actual, either_pred, "either")
    both_side_match = float((pred_h == yh).mean() * 50.0 + (pred_l == yl).mean() * 50.0) if len(yh) else 0.0
    return {
        **high,
        **low,
        **both,
        **either,
        "combined_event_acc_pct": (high["high_event_acc_pct"] + low["low_event_acc_pct"]) / 2.0,
        "combined_event_bal_acc_pct": (high["high_event_bal_acc_pct"] + low["low_event_bal_acc_pct"]) / 2.0,
        "combined_event_f1_pct": (high["high_event_f1_pct"] + low["low_event_f1_pct"]) / 2.0,
        "both_side_match_acc_pct": both_side_match,
    }


def fixed_event_scores(df, high_event_default: bool, low_event_default: bool, spec: Mapping[str, Any]) -> dict[str, float]:
    hb = EVENT_BIN_THRESHOLD if high_event_default else 0
    lb = EVENT_BIN_THRESHOLD if low_event_default else 0
    return score_event_predictions(df, np.full(len(df), hb, dtype=int), np.full(len(df), lb, dtype=int), spec)


def event_penalty(df, ph: np.ndarray, pl: np.ndarray) -> dict[str, Any]:
    yh, yl = _event_arrays(df)
    pred_h = ph.astype(int) >= EVENT_BIN_THRESHOLD
    pred_l = pl.astype(int) >= EVENT_BIN_THRESHOLD
    high_gap = abs(float(np.mean(pred_h) - np.mean(yh)) * 100.0) if len(yh) else 0.0
    low_gap = abs(float(np.mean(pred_l) - np.mean(yl)) * 100.0) if len(yl) else 0.0
    high_penalty = max(0.0, high_gap - 20.0) * 0.08
    low_penalty = max(0.0, low_gap - 20.0) * 0.08
    high_no_signal = 3.0 if len(yh) and np.mean(yh) >= 0.10 and np.mean(pred_h) <= 0.02 else 0.0
    low_no_signal = 3.0 if len(yl) and np.mean(yl) >= 0.10 and np.mean(pred_l) <= 0.02 else 0.0
    if EVENT_SIDE == "HIGH":
        total = high_penalty + high_no_signal
    elif EVENT_SIDE == "LOW":
        total = low_penalty + low_no_signal
    else:
        total = high_penalty + low_penalty + high_no_signal + low_no_signal
    return {
        "event_rate_gap_penalty": total,
        "event_high_rate_gap_penalty": high_penalty,
        "event_low_rate_gap_penalty": low_penalty,
        "event_no_signal_penalty": high_no_signal + low_no_signal,
        "total_penalty": total,
        "max_pred_share_high_pct": max(float(np.mean(pred_h) * 100.0), float((1.0 - np.mean(pred_h)) * 100.0)) if len(pred_h) else 0.0,
        "max_pred_share_low_pct": max(float(np.mean(pred_l) * 100.0), float((1.0 - np.mean(pred_l)) * 100.0)) if len(pred_l) else 0.0,
    }


def side_metric_names() -> tuple[str, str, str, str]:
    if EVENT_SIDE == "HIGH":
        return "high_event_bal_acc_lift_pp", "high_event_f1_lift_pp", "high_event_acc_pct", "high_event_pred_rate_pct"
    if EVENT_SIDE == "LOW":
        return "low_event_bal_acc_lift_pp", "low_event_f1_lift_pp", "low_event_acc_pct", "low_event_pred_rate_pct"
    return "combined_event_bal_acc_lift_pp", "combined_event_f1_lift_pp", "combined_event_acc_pct", "either_event_pred_rate_pct"


def event_fitness(metrics: Mapping[str, Any]) -> float:
    bal_key, f1_key, _, _ = side_metric_names()
    if EVENT_SIDE in {"HIGH", "LOW"}:
        raw = safe_float(metrics.get(bal_key)) * 1.60 + safe_float(metrics.get(f1_key)) * 0.90
    else:
        raw = (
            safe_float(metrics.get("combined_event_bal_acc_lift_pp")) * 1.40
            + safe_float(metrics.get("combined_event_f1_lift_pp")) * 0.70
            + safe_float(metrics.get("both_side_match_lift_pp")) * 0.45
            + safe_float(metrics.get("either_event_f1_lift_pp")) * 0.35
            + safe_float(metrics.get("both_event_f1_lift_pp")) * 0.20
        )
    return raw - safe_float(metrics.get("total_penalty"))


def evaluate_event_predictor(ind, df, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    ph, pl, pred_diag = L.predict(ind, df[features], qspec)
    scores = score_event_predictions(df, ph, pl, ind.baseline_spec)
    base = fixed_event_scores(df, bool(ind.baseline_spec.get("high_event_default")), bool(ind.baseline_spec.get("low_event_default")), ind.baseline_spec)
    penalty = event_penalty(df, ph, pl)
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
        "both_side_match_lift_pp": scores["both_side_match_acc_pct"] - base["both_side_match_acc_pct"],
        "both_event_f1_lift_pp": scores["both_event_f1_pct"] - base["both_event_f1_pct"],
        "either_event_f1_lift_pp": scores["either_event_f1_pct"] - base["either_event_f1_pct"],
        "baseline_combined_event_bal_acc_pct": base["combined_event_bal_acc_pct"],
        "baseline_combined_event_f1_pct": base["combined_event_f1_pct"],
        "baseline_both_side_match_acc_pct": base["both_side_match_acc_pct"],
        **penalty,
        **pred_diag,
    }
    metrics["fitness"] = event_fitness(metrics)
    return metrics


def event_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    bal_key, f1_key, _, _ = side_metric_names()
    checks = [
        ("sample_count", safe_int(metrics.get("sample_count")), 100, ">="),
        ("member_score", safe_float(metrics.get("member_score")), 10.0, ">="),
        (bal_key, safe_float(metrics.get(bal_key)), 0.0, ">="),
        ("total_penalty", safe_float(metrics.get("total_penalty")), 8.0, "<="),
    ]
    if EVENT_SIDE == "BOTH":
        checks.append(("both_side_match_lift_pp", safe_float(metrics.get("both_side_match_lift_pp")), 0.0, ">="))
    if str(kind).lower() in {"stress", "oos"}:
        checks.append((f1_key, safe_float(metrics.get(f1_key)), 0.0, ">="))
    out = []
    for metric, value, threshold, rule in checks:
        if (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold):
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def event_score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows:
        return []
    bal_key, f1_key, acc_key, _ = side_metric_names()
    br = L.percentile_ranks([safe_float(r.get(bal_key)) for r in rows])
    fr = L.percentile_ranks([safe_float(r.get(f1_key)) for r in rows])
    ar = L.percentile_ranks([safe_float(r.get(acc_key)) for r in rows])
    pr = L.percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(0.0, min(1.0, br[i] * 0.50 + fr[i] * 0.30 + ar[i] * 0.10 + pr[i] * 0.10)) * 100.0
        r = dict(row)
        r["member_score"] = round(score, 6)
        r["member_score_components"] = {"side_bal_acc_lift_percentile": round(br[i], 6), "side_f1_lift_percentile": round(fr[i], 6), "side_acc_percentile": round(ar[i], 6), "low_penalty_percentile": round(pr[i], 6)}
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


def install_low_target(event_side: str) -> None:
    global EVENT_SIDE
    side = str(event_side or "BOTH").strip().upper()
    if side not in {"HIGH", "LOW", "BOTH"}:
        raise ValueError("event_side must be HIGH, LOW, or BOTH")
    EVENT_SIDE = side
    L.make_baseline_spec = make_event_baseline_spec
    L.evaluate_predictor = evaluate_event_predictor
    L.evaluate_population = event_evaluate_population
    L.random_rule = forced_random_rule if EVENT_SIDE in {"HIGH", "LOW"} else _ORIG_RANDOM_RULE
    L.mutate_rule = forced_mutate_rule if EVENT_SIDE in {"HIGH", "LOW"} else _ORIG_MUTATE_RULE


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
) -> dict[str, Any]:
    install_low_target(event_side)
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
        "mode": "binary_event_side_separated",
        "event_side": EVENT_SIDE,
        "high_event": "D-day high_pct >= 2% from D open",
        "low_event": "D-day low_mag_pct >= 2% from D open",
        "event_bin_threshold": EVENT_BIN_THRESHOLD,
        "objective": "selected side balanced accuracy + selected side F1 vs majority baseline",
        "note": "separate HIGH/LOW runs are diagnostics; final trade logic must combine upside probability and downside risk later",
    }
    config = {"ticker": ticker, "runner": "scripts/research/run_range_predictor_stage2_v3.py", "legacy_feature_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}", "mode": f"rolling_stage2_plus_prior5_event2pct_{EVENT_SIDE.lower()}_only", "rolling": {"train_days": rolling_train_days, "step_days": rolling_step_days, "start": rolling_start, "end": rolling_end, "split_count": len(rolling_splits), "splits": rolling_splits}, "final_periods": final_periods, "ga": {"population": L.POPULATION, "generations": L.GENERATIONS, "patience": L.PATIENCE, "elite_ratio": L.ELITE_RATIO, "mutation_rate": L.MUTATION_RATE, "rule_count": L.RULE_COUNT, "survivor_count": survivor_count, "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO, "seed_base": seed_base, "min_band_width_q": L.MIN_BAND_WIDTH_Q, "max_band_width_q": L.MAX_BAND_WIDTH_Q, "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS]}, "target": target_desc, "lookahead_report": {"pass": True, "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap", "flow_features": "optional D-1 orderbook/flow columns if cache provides them", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "final_eval_quantile_reference": "last rolling train qspec only; no final-period distribution fitting", "excluded": ["D0 high/low/close as features", "future trading results"]}, "feature_count": len(features_final), "feature_sources": dict(source_counts), "bin_labels": L.BIN_LABELS, "distributions": distributions}
    write_json(out_dir / "config.json", config)
    summary = {"ticker": ticker, "mode": f"rolling_stage2_plus_prior5_event2pct_{EVENT_SIDE.lower()}_only", "event_side": EVENT_SIDE, "rolling_split_count": len(rolling_splits), "stage_trace": trace, "final_trace": final_trace, "final_survivor_count": len(alive), "final_survivor_signatures": [ind.signature or predictor_signature(ind) for ind in alive], "elapsed_sec": time.time() - started, "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "train_gate_metrics": str(out_dir / "train_gate_metrics.jsonl"), "stage_survivors": str(out_dir / "stage_survivors.jsonl"), "final_period_metrics": str(out_dir / "final_period_metrics.jsonl"), "final_survivors": str(out_dir / "final_survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")}}
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rolling Stage2 + prior 5 days GA for separated 2pct HIGH/LOW event classification")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=L.SURVIVOR_COUNT)
    p.add_argument("--rolling-train-days", type=int, default=252)
    p.add_argument("--rolling-step-days", type=int, default=21)
    p.add_argument("--rolling-start", default="2022-07-01")
    p.add_argument("--rolling-end", default="2025-06-30")
    p.add_argument("--event-side", choices=["HIGH", "LOW", "BOTH"], default="BOTH")
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
    run_rolling_stage2_predictor(ticker, out_dir, seed_base, max(1, int(args.survivor_count)), max(50, int(args.rolling_train_days)), max(1, int(args.rolling_step_days)), args.rolling_start, args.rolling_end, event_side)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
