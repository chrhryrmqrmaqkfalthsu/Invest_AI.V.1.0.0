#!/usr/bin/env python3
"""
True 3-bin pattern detector + dense feature-weight layer.

이 버전은 직전 pattern-detector 구현(d66a5ec)을 그대로 로드한 뒤,
스윙 룰북처럼 전체 feature pool을 동시에 보고 각 feature의 반영 강도를 GA가 학습하도록
HIGH/LOW dense weight vector를 추가한다.

핵심 차이:
- 기존: gene이 고른 일부 조건만 signal/prediction에 반영.
- 변경: 모든 qspec feature가 HIGH/LOW dense score에 참여하고, feature별 weight가 진화한다.
- gene rule은 패턴 조건 역할을 유지하고, dense layer는 전체 파라미터 반영 강도를 학습한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_COMMIT = "d66a5ec"
SELF_PATH = "scripts/research/run_range_predictor_stage2_v3.py"
TARGET_MODE = "next_day_hilo_true_coarse3_pattern_detector_dense_feature_weights_stage2"

DENSE_FEATURE_WEIGHTS_ENABLED = True
DENSE_INIT_SCALE = 0.12
DENSE_MAX_ABS_WEIGHT = 1.50
DENSE_MUTATION_PROB = 0.035
DENSE_MUTATION_STRENGTH = 0.12
DENSE_CROSSOVER_BLEND_PROB = 0.35
DENSE_SCORE_WEIGHT = 0.75
DENSE_ACTIVE_BONUS_GENES = 2
DENSE_MIN_CONFIDENCE = 0.18
DENSE_WEIGHT_L2_PENALTY = 0.015
DENSE_TOP_FEATURES_TO_RECORD = 25


def _load_base_module() -> types.ModuleType:
    code = subprocess.check_output(["git", "show", f"{BASE_COMMIT}:{SELF_PATH}"], cwd=str(PROJECT_ROOT), text=True)
    mod = types.ModuleType("_km_range_predictor_pattern_detector_d66a5ec")
    mod.__file__ = str(PROJECT_ROOT / SELF_PATH)
    mod.__name__ = "_km_range_predictor_pattern_detector_d66a5ec"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


P = _load_base_module()
P.TARGET_MODE = TARGET_MODE


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _clip_weight(value: float) -> float:
    return float(max(-DENSE_MAX_ABS_WEIGHT, min(DENSE_MAX_ABS_WEIGHT, value)))


def _dense_feature_names(qspec: dict[str, Any]) -> list[str]:
    return sorted(str(k) for k in qspec.keys())


def _ensure_dense_attrs(ind: Any, qspec: dict[str, Any] | None = None, rng: random.Random | None = None) -> Any:
    features = _dense_feature_names(qspec or {})
    rng = rng or random.Random(0)
    if not hasattr(ind, "high_feature_weights") or not isinstance(getattr(ind, "high_feature_weights"), dict):
        ind.high_feature_weights = {}
    if not hasattr(ind, "low_feature_weights") or not isinstance(getattr(ind, "low_feature_weights"), dict):
        ind.low_feature_weights = {}
    for f in features:
        ind.high_feature_weights.setdefault(f, rng.uniform(-DENSE_INIT_SCALE, DENSE_INIT_SCALE))
        ind.low_feature_weights.setdefault(f, rng.uniform(-DENSE_INIT_SCALE, DENSE_INIT_SCALE))
    # keep stale keys harmless but preserve learned weights when qspec is narrower/wider across calls
    if not hasattr(ind, "high_dense_bias"):
        ind.high_dense_bias = rng.uniform(-0.08, 0.08)
    if not hasattr(ind, "low_dense_bias"):
        ind.low_dense_bias = rng.uniform(-0.08, 0.08)
    if not hasattr(ind, "dense_high_cut1"):
        ind.dense_high_cut1 = rng.uniform(0.30, 0.40)
    if not hasattr(ind, "dense_high_cut2"):
        ind.dense_high_cut2 = rng.uniform(0.60, 0.72)
    if not hasattr(ind, "dense_low_cut1"):
        ind.dense_low_cut1 = rng.uniform(0.30, 0.40)
    if not hasattr(ind, "dense_low_cut2"):
        ind.dense_low_cut2 = rng.uniform(0.60, 0.72)
    _repair_dense_cuts(ind)
    return ind


def _copy_dense_attrs(dst: Any, src: Any) -> Any:
    for name in ["high_feature_weights", "low_feature_weights"]:
        if hasattr(src, name):
            setattr(dst, name, dict(getattr(src, name) or {}))
    for name in ["high_dense_bias", "low_dense_bias", "dense_high_cut1", "dense_high_cut2", "dense_low_cut1", "dense_low_cut2"]:
        if hasattr(src, name):
            setattr(dst, name, float(getattr(src, name)))
    return dst


def _repair_dense_cuts(ind: Any) -> None:
    h1 = float(max(0.05, min(0.85, getattr(ind, "dense_high_cut1", 0.35))))
    h2 = float(max(h1 + 0.05, min(0.95, getattr(ind, "dense_high_cut2", 0.66))))
    l1 = float(max(0.05, min(0.85, getattr(ind, "dense_low_cut1", 0.35))))
    l2 = float(max(l1 + 0.05, min(0.95, getattr(ind, "dense_low_cut2", 0.66))))
    ind.dense_high_cut1 = h1
    ind.dense_high_cut2 = h2
    ind.dense_low_cut1 = l1
    ind.dense_low_cut2 = l2


def _feature_quantile_matrix(X: Any, qspec: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    features = _dense_feature_names(qspec)
    n = len(X)
    if not features:
        return [], np.zeros((n, 0), dtype=float)
    mat = np.empty((n, len(features)), dtype=float)
    for idx, f in enumerate(features):
        if f not in X:
            mat[:, idx] = 0.5
            continue
        values = np.asarray(X[f], dtype=float)
        spec = qspec.get(f) or {}
        q_values = np.asarray(spec.get("values") or [], dtype=float)
        q_levels = np.asarray(spec.get("levels") or [], dtype=float)
        if len(q_values) >= 2 and len(q_values) == len(q_levels):
            # np.interp needs ascending x; quantile values can have duplicates, so stabilize slightly.
            order = np.argsort(q_values)
            xs = q_values[order]
            ys = q_levels[order]
            xs = np.maximum.accumulate(xs + np.arange(len(xs)) * 1e-12)
            mat[:, idx] = np.interp(values, xs, ys, left=0.0, right=1.0)
        else:
            finite = values[np.isfinite(values)]
            if len(finite):
                lo, hi = np.nanpercentile(finite, [1, 99])
                mat[:, idx] = np.clip((values - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
            else:
                mat[:, idx] = 0.5
        mat[:, idx] = np.nan_to_num(mat[:, idx], nan=0.5, posinf=1.0, neginf=0.0)
    return features, mat


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _dense_head(features: list[str], mat: np.ndarray, weights: dict[str, float], bias: float, cut1: float, cut2: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if mat.shape[1] == 0:
        n = mat.shape[0]
        return np.ones(n, dtype=int), np.zeros(n, dtype=float), np.zeros(n, dtype=float)
    w = np.asarray([_sf(weights.get(f), 0.0) for f in features], dtype=float)
    raw = (mat - 0.5) @ w / max(1.0, math.sqrt(float(len(features)))) + float(bias)
    score = _sigmoid(raw)
    pred = np.where(score < cut1, 0, np.where(score < cut2, 1, 2)).astype(int)
    center = np.where(pred == 0, cut1 / 2.0, np.where(pred == 1, (cut1 + cut2) / 2.0, (cut2 + 1.0) / 2.0))
    confidence = np.clip(np.abs(score - center) * 2.0, 0.0, 1.0)
    return pred, score, confidence


def _dense_l2(ind: Any) -> float:
    vals = []
    vals.extend(float(v) for v in getattr(ind, "high_feature_weights", {}).values())
    vals.extend(float(v) for v in getattr(ind, "low_feature_weights", {}).values())
    if not vals:
        return 0.0
    return float(np.mean(np.square(vals)))


def dense_top_features(ind: Any, top_n: int = DENSE_TOP_FEATURES_TO_RECORD) -> dict[str, list[dict[str, float | str]]]:
    def top(weights: dict[str, float]) -> list[dict[str, float | str]]:
        pairs = sorted(((k, float(v)) for k, v in (weights or {}).items()), key=lambda x: abs(x[1]), reverse=True)[:top_n]
        return [{"feature": k, "weight": round(v, 6)} for k, v in pairs]
    return {
        "high": top(getattr(ind, "high_feature_weights", {})),
        "low": top(getattr(ind, "low_feature_weights", {})),
    }


_BASE_random_individual = P.random_individual
_BASE_mutate = P.mutate
_BASE_crossover = P.crossover
_BASE_predict_signal = P.predict_signal
_BASE_predict = P.predict
_BASE_evaluate_predictor = P.evaluate_predictor
_BASE_predictor_fitness = P.predictor_fitness
_BASE_prediction_penalty = P.prediction_penalty
_BASE_predictor_signature = P.predictor_signature
_BASE_individual_to_dict = P.individual_to_dict
_BASE_dual_head_params = P.dual_head_params
_BASE_parse_args = P.parse_args
_BASE_install_dual_head_target = P.install_dual_head_target
_BASE_run_original_stage2_predictor = P.run_original_stage2_predictor


def random_individual(rng: random.Random, qspec: dict[str, Any], baseline_spec: dict[str, Any]):
    ind = _BASE_random_individual(rng, qspec, baseline_spec)
    return _ensure_dense_attrs(ind, qspec, rng)


def mutate(ind: Any, rng: random.Random, qspec: dict[str, Any], baseline_spec: dict[str, Any] | None = None):
    child = _BASE_mutate(ind, rng, qspec, baseline_spec)
    _copy_dense_attrs(child, ind)
    _ensure_dense_attrs(child, qspec, rng)
    for weights_name in ["high_feature_weights", "low_feature_weights"]:
        weights = getattr(child, weights_name)
        for f in _dense_feature_names(qspec):
            if rng.random() < DENSE_MUTATION_PROB:
                weights[f] = _clip_weight(float(weights.get(f, 0.0)) + rng.gauss(0.0, DENSE_MUTATION_STRENGTH))
    for name in ["high_dense_bias", "low_dense_bias"]:
        if rng.random() < 0.20:
            setattr(child, name, _clip_weight(float(getattr(child, name, 0.0)) + rng.gauss(0.0, 0.05)))
    for name in ["dense_high_cut1", "dense_high_cut2", "dense_low_cut1", "dense_low_cut2"]:
        if rng.random() < 0.15:
            setattr(child, name, float(getattr(child, name, 0.5)) + rng.gauss(0.0, 0.025))
    _repair_dense_cuts(child)
    return child


def _mix_weights(a: dict[str, float], b: dict[str, float], rng: random.Random, features: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for f in features:
        av = float(a.get(f, 0.0))
        bv = float(b.get(f, 0.0))
        if rng.random() < DENSE_CROSSOVER_BLEND_PROB:
            alpha = rng.random()
            out[f] = _clip_weight(av * alpha + bv * (1.0 - alpha))
        else:
            out[f] = _clip_weight(av if rng.random() < 0.5 else bv)
    return out


def crossover(a: Any, b: Any, rng: random.Random, baseline_spec: dict[str, Any]):
    child = _BASE_crossover(a, b, rng, baseline_spec)
    features = sorted(set(getattr(a, "high_feature_weights", {}).keys()) | set(getattr(b, "high_feature_weights", {}).keys()) | set(getattr(a, "low_feature_weights", {}).keys()) | set(getattr(b, "low_feature_weights", {}).keys()))
    child.high_feature_weights = _mix_weights(getattr(a, "high_feature_weights", {}), getattr(b, "high_feature_weights", {}), rng, features)
    child.low_feature_weights = _mix_weights(getattr(a, "low_feature_weights", {}), getattr(b, "low_feature_weights", {}), rng, features)
    for name in ["high_dense_bias", "low_dense_bias", "dense_high_cut1", "dense_high_cut2", "dense_low_cut1", "dense_low_cut2"]:
        setattr(child, name, float(getattr(a if rng.random() < 0.5 else b, name, 0.5 if "cut" in name else 0.0)))
    _repair_dense_cuts(child)
    return child


def predict_signal(ind: Any, X: Any, qspec: dict[str, Any]):
    _ensure_dense_attrs(ind, qspec, random.Random(0))
    ph, pl, signal, high_signal, low_signal, diag = _BASE_predict_signal(ind, X, qspec)
    if not DENSE_FEATURE_WEIGHTS_ENABLED:
        return ph, pl, signal, high_signal, low_signal, diag

    features, mat = _feature_quantile_matrix(X, qspec)
    h_pred, h_score, h_conf = _dense_head(features, mat, getattr(ind, "high_feature_weights", {}), getattr(ind, "high_dense_bias", 0.0), getattr(ind, "dense_high_cut1", 0.35), getattr(ind, "dense_high_cut2", 0.66))
    l_pred, l_score, l_conf = _dense_head(features, mat, getattr(ind, "low_feature_weights", {}), getattr(ind, "low_dense_bias", 0.0), getattr(ind, "dense_low_cut1", 0.35), getattr(ind, "dense_low_cut2", 0.66))

    # Dense layer does not replace rule signals; it votes with all parameters and can strengthen/alter prediction on signal days.
    dense_high_signal = h_conf >= DENSE_MIN_CONFIDENCE
    dense_low_signal = l_conf >= DENSE_MIN_CONFIDENCE
    dense_signal = dense_high_signal & dense_low_signal

    # Use dense prediction only when base pattern signal exists and dense confidence also exists.
    final_signal = signal & dense_signal
    ph2 = np.asarray(ph, dtype=int).copy()
    pl2 = np.asarray(pl, dtype=int).copy()
    strong_h = final_signal & (h_conf >= np.maximum(0.0, DENSE_MIN_CONFIDENCE))
    strong_l = final_signal & (l_conf >= np.maximum(0.0, DENSE_MIN_CONFIDENCE))
    ph2[strong_h] = h_pred[strong_h]
    pl2[strong_l] = l_pred[strong_l]

    diag.update({
        "dense_feature_weight_enabled": bool(DENSE_FEATURE_WEIGHTS_ENABLED),
        "dense_feature_count": int(len(features)),
        "dense_high_signal_count": int(np.sum(dense_high_signal)),
        "dense_low_signal_count": int(np.sum(dense_low_signal)),
        "dense_both_signal_count": int(np.sum(dense_signal)),
        "dense_high_score_mean": float(np.mean(h_score)) if len(h_score) else 0.0,
        "dense_low_score_mean": float(np.mean(l_score)) if len(l_score) else 0.0,
        "dense_high_conf_mean": float(np.mean(h_conf)) if len(h_conf) else 0.0,
        "dense_low_conf_mean": float(np.mean(l_conf)) if len(l_conf) else 0.0,
        "dense_high_cut1": float(getattr(ind, "dense_high_cut1", 0.35)),
        "dense_high_cut2": float(getattr(ind, "dense_high_cut2", 0.66)),
        "dense_low_cut1": float(getattr(ind, "dense_low_cut1", 0.35)),
        "dense_low_cut2": float(getattr(ind, "dense_low_cut2", 0.66)),
        "dense_weight_l2": _dense_l2(ind),
        "dense_signal_intersection_count": int(np.sum(final_signal)),
    })
    return ph2, pl2, final_signal, high_signal & dense_high_signal, low_signal & dense_low_signal, diag


def predict(ind: Any, X: Any, qspec: dict[str, Any]):
    ph, pl, _signal, _hs, _ls, diag = predict_signal(ind, X, qspec)
    return ph, pl, diag


def predictor_fitness(metrics: dict[str, Any]) -> float:
    base = float(_BASE_predictor_fitness(metrics))
    dense_penalty = _sf(metrics.get("dense_weight_l2"), 0.0) * DENSE_WEIGHT_L2_PENALTY
    if isinstance(metrics, dict):
        metrics["dense_weight_l2_penalty"] = dense_penalty
        metrics["dense_base_fitness"] = base
    return float(base - dense_penalty)


def evaluate_predictor(ind: Any, df: Any, features: list[str], qspec: dict[str, Any]) -> dict[str, Any]:
    metrics = _BASE_evaluate_predictor(ind, df, features, qspec)
    # Base evaluate calls global predict_signal after install patch, so dense metrics are already present.
    metrics["target_mode"] = TARGET_MODE
    metrics["dense_feature_weight_enabled"] = bool(DENSE_FEATURE_WEIGHTS_ENABLED)
    metrics["dense_weight_l2_penalty"] = _sf(metrics.get("dense_weight_l2"), 0.0) * DENSE_WEIGHT_L2_PENALTY
    metrics["fitness"] = predictor_fitness(metrics)
    return metrics


def predictor_signature(ind: Any) -> str:
    _ensure_dense_attrs(ind, {}, random.Random(0))
    dense_payload = {
        "high_feature_weights": {k: round(float(v), 6) for k, v in sorted(getattr(ind, "high_feature_weights", {}).items())},
        "low_feature_weights": {k: round(float(v), 6) for k, v in sorted(getattr(ind, "low_feature_weights", {}).items())},
        "high_dense_bias": round(float(getattr(ind, "high_dense_bias", 0.0)), 6),
        "low_dense_bias": round(float(getattr(ind, "low_dense_bias", 0.0)), 6),
        "dense_high_cut1": round(float(getattr(ind, "dense_high_cut1", 0.35)), 6),
        "dense_high_cut2": round(float(getattr(ind, "dense_high_cut2", 0.66)), 6),
        "dense_low_cut1": round(float(getattr(ind, "dense_low_cut1", 0.35)), 6),
        "dense_low_cut2": round(float(getattr(ind, "dense_low_cut2", 0.66)), 6),
    }
    payload = {
        "base_signature": _BASE_predictor_signature(ind),
        "target_mode": TARGET_MODE,
        "dense": dense_payload,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def individual_to_dict(ind: Any) -> dict[str, Any]:
    d = _BASE_individual_to_dict(ind)
    d["type"] = "true_coarse3_pattern_detector_dense_feature_weights"
    d["target_mode"] = TARGET_MODE
    d["dense_feature_weights"] = {
        "enabled": bool(DENSE_FEATURE_WEIGHTS_ENABLED),
        "feature_count_high": len(getattr(ind, "high_feature_weights", {}) or {}),
        "feature_count_low": len(getattr(ind, "low_feature_weights", {}) or {}),
        "high_feature_weights": {k: round(float(v), 8) for k, v in sorted(getattr(ind, "high_feature_weights", {}).items())},
        "low_feature_weights": {k: round(float(v), 8) for k, v in sorted(getattr(ind, "low_feature_weights", {}).items())},
        "high_dense_bias": round(float(getattr(ind, "high_dense_bias", 0.0)), 8),
        "low_dense_bias": round(float(getattr(ind, "low_dense_bias", 0.0)), 8),
        "dense_high_cut1": round(float(getattr(ind, "dense_high_cut1", 0.35)), 8),
        "dense_high_cut2": round(float(getattr(ind, "dense_high_cut2", 0.66)), 8),
        "dense_low_cut1": round(float(getattr(ind, "dense_low_cut1", 0.35)), 8),
        "dense_low_cut2": round(float(getattr(ind, "dense_low_cut2", 0.66)), 8),
        "top_features": dense_top_features(ind),
    }
    d["signature"] = ind.signature or predictor_signature(ind)
    return d


def dual_head_params() -> dict[str, Any]:
    params = _BASE_dual_head_params()
    params["mode"] = TARGET_MODE
    params["dense_feature_weights"] = {
        "enabled": bool(DENSE_FEATURE_WEIGHTS_ENABLED),
        "uses_all_qspec_features": True,
        "init_scale": DENSE_INIT_SCALE,
        "max_abs_weight": DENSE_MAX_ABS_WEIGHT,
        "mutation_prob": DENSE_MUTATION_PROB,
        "mutation_strength": DENSE_MUTATION_STRENGTH,
        "score_weight": DENSE_SCORE_WEIGHT,
        "min_confidence": DENSE_MIN_CONFIDENCE,
        "weight_l2_penalty": DENSE_WEIGHT_L2_PENALTY,
        "description": "Every qspec feature has a learned HIGH and LOW weight. Dense scores vote on signal days, so the GA learns how strongly each parameter should be reflected.",
    }
    return params


def _parse_dense_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dense-feature-weights", action="store_true", default=DENSE_FEATURE_WEIGHTS_ENABLED)
    parser.add_argument("--no-dense-feature-weights", action="store_true", default=False)
    parser.add_argument("--dense-init-scale", type=float, default=DENSE_INIT_SCALE)
    parser.add_argument("--dense-max-abs-weight", type=float, default=DENSE_MAX_ABS_WEIGHT)
    parser.add_argument("--dense-mutation-prob", type=float, default=DENSE_MUTATION_PROB)
    parser.add_argument("--dense-mutation-strength", type=float, default=DENSE_MUTATION_STRENGTH)
    parser.add_argument("--dense-score-weight", type=float, default=DENSE_SCORE_WEIGHT)
    parser.add_argument("--dense-min-confidence", type=float, default=DENSE_MIN_CONFIDENCE)
    parser.add_argument("--dense-weight-l2-penalty", type=float, default=DENSE_WEIGHT_L2_PENALTY)
    return parser.parse_known_args(argv)


def _apply_dense_args(args: argparse.Namespace) -> None:
    global DENSE_FEATURE_WEIGHTS_ENABLED, DENSE_INIT_SCALE, DENSE_MAX_ABS_WEIGHT
    global DENSE_MUTATION_PROB, DENSE_MUTATION_STRENGTH, DENSE_SCORE_WEIGHT
    global DENSE_MIN_CONFIDENCE, DENSE_WEIGHT_L2_PENALTY
    DENSE_FEATURE_WEIGHTS_ENABLED = bool(args.dense_feature_weights) and not bool(args.no_dense_feature_weights)
    DENSE_INIT_SCALE = max(0.0, float(args.dense_init_scale))
    DENSE_MAX_ABS_WEIGHT = max(0.01, float(args.dense_max_abs_weight))
    DENSE_MUTATION_PROB = max(0.0, min(1.0, float(args.dense_mutation_prob)))
    DENSE_MUTATION_STRENGTH = max(0.0, float(args.dense_mutation_strength))
    DENSE_SCORE_WEIGHT = max(0.0, float(args.dense_score_weight))
    DENSE_MIN_CONFIDENCE = max(0.0, min(1.0, float(args.dense_min_confidence)))
    DENSE_WEIGHT_L2_PENALTY = max(0.0, float(args.dense_weight_l2_penalty))


def install_dual_head_target(args: Any) -> None:
    _BASE_install_dual_head_target(args)
    replacements = {
        "random_individual": random_individual,
        "mutate": mutate,
        "crossover": crossover,
        "predict_signal": predict_signal,
        "predict": predict,
        "evaluate_predictor": evaluate_predictor,
        "predictor_fitness": predictor_fitness,
        "predictor_signature": predictor_signature,
        "individual_to_dict": individual_to_dict,
        "dual_head_params": dual_head_params,
    }
    for name, value in replacements.items():
        setattr(P, name, value)
    P.L.random_individual = random_individual
    P.L.mutate = mutate
    P.L.crossover = crossover
    P.L.predict = predict
    P.L.evaluate_predictor = evaluate_predictor
    P.L.predictor_signature = predictor_signature
    P.L.individual_to_dict = individual_to_dict
    P.TARGET_MODE = TARGET_MODE


def parse_args(argv: list[str] | None = None):
    dense_args, remaining = _parse_dense_args(sys.argv[1:] if argv is None else argv)
    _apply_dense_args(dense_args)
    return _BASE_parse_args(remaining)


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: Any):
    P.install_dual_head_target = install_dual_head_target
    P.dual_head_params = dual_head_params
    return _BASE_run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


# Patch base module and expose names.
P.random_individual = random_individual
P.mutate = mutate
P.crossover = crossover
P.predict_signal = predict_signal
P.predict = predict
P.evaluate_predictor = evaluate_predictor
P.predictor_fitness = predictor_fitness
P.predictor_signature = predictor_signature
P.individual_to_dict = individual_to_dict
P.dual_head_params = dual_head_params
P.install_dual_head_target = install_dual_head_target
P.parse_args = parse_args
P.run_original_stage2_predictor = run_original_stage2_predictor

for _name in dir(P):
    if not _name.startswith("__") and _name not in globals():
        globals()[_name] = getattr(P, _name)

for _name, _value in {
    "TARGET_MODE": TARGET_MODE,
    "dense_top_features": dense_top_features,
    "random_individual": random_individual,
    "mutate": mutate,
    "crossover": crossover,
    "predict_signal": predict_signal,
    "predict": predict,
    "evaluate_predictor": evaluate_predictor,
    "predictor_fitness": predictor_fitness,
    "predictor_signature": predictor_signature,
    "individual_to_dict": individual_to_dict,
    "dual_head_params": dual_head_params,
    "install_dual_head_target": install_dual_head_target,
    "parse_args": parse_args,
    "run_original_stage2_predictor": run_original_stage2_predictor,
}.items():
    globals()[_name] = _value


def default_seed_base(ticker: str) -> int:
    return int(P.default_seed_base(ticker)) if hasattr(P, "default_seed_base") else int(P.L.default_seed_base(ticker))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else P.auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
