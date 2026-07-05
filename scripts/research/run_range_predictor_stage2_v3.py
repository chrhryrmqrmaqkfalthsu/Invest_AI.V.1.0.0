#!/usr/bin/env python3
"""
Original-Stage2-style TRUE 3-bin multi-condition dual-head GA runner.

이번 버전은 6-bin을 예측한 뒤 나중에 묶는 방식이 아니다.
유전자 자체가 처음부터 HIGH/LOW 각각 0, 1, 2 세 구간만 예측한다.

3-bin 의미:
- coarse bin 0 = 기존 6-bin 0~1: 작은 움직임
- coarse bin 1 = 기존 6-bin 2~3: 중간 움직임
- coarse bin 2 = 기존 6-bin 4~5: 큰 움직임

중요:
- 3-bin은 이미 범위가 크기 때문에 adjacent/근사 성공을 쓰지 않는다.
- 같은 3-bin이면 성공, 아니면 실패다.
- 위험 방향 오차는 별도 리스크 지표로 강하게 본다.
- 안전 방향 오차도 성공으로 치지 않는다. 다만 위험 오차보다 약하게 취급할 수 있다.

Stage2 흐름:
train_1 독립 GA 100개 + train_2 독립 GA 100개 + train_3 독립 GA 100개
-> stress_pre_2022h1 -> train_3_eval -> train_2_eval -> train_1_eval -> oos_2025h2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREV_COMMIT = "07c0bcf"
SELF_PATH = "scripts/research/run_range_predictor_stage2_v3.py"
TARGET_MODE = "next_day_hilo_true_coarse3_multicond_dual_head_original_stage2"
COARSE_BIN_COUNT = 3

SAFE_OVERFLOW_FREE_BINS = 0.0
HIGH_DANGEROUS_BIN_ERROR_WEIGHT = 1.0
HIGH_SAFE_BIN_ERROR_WEIGHT = 0.05
LOW_DANGEROUS_BIN_ERROR_WEIGHT = 1.7
LOW_SAFE_BIN_ERROR_WEIGHT = 0.20
ASYMMETRIC_BIN_ERROR_WEIGHT = 1.25

HIGH_COARSE_EXACT_WEIGHT = 0.75
LOW_COARSE_EXACT_WEIGHT = 1.00
BOTH_COARSE_EXACT_WEIGHT = 1.20
COMBINED_COARSE_EXACT_WEIGHT = 0.45
HIGH_NO_DANGER_WEIGHT = 0.25
LOW_NO_DANGER_WEIGHT = 0.45
BOTH_NO_DANGER_WEIGHT = 0.35
COARSE_ERROR_WEIGHT = 0.35
HEAD_IMBALANCE_PENALTY = 0.08

MIN_HIGH_COARSE_ACC = -999.0
MIN_LOW_COARSE_ACC = -999.0
MIN_BOTH_COARSE_ACC = -999.0
MIN_COMBINED_COARSE_ACC = -999.0
MIN_HIGH_COARSE_LIFT = -999.0
MIN_LOW_COARSE_LIFT = -999.0
MIN_BOTH_COARSE_LIFT = -999.0
MIN_HIGH_NO_DANGER = -999.0
MIN_LOW_NO_DANGER = -999.0
MIN_BOTH_NO_DANGER = -999.0
MAX_HIGH_DANGEROUS_BIN_ERROR = 999.0
MAX_LOW_DANGEROUS_BIN_ERROR = 999.0
MAX_HIGH_SAFE_BIN_ERROR = 999.0
MAX_LOW_SAFE_BIN_ERROR = 999.0
MAX_TOTAL_PENALTY_TRUE3 = 999.0
MAX_HIGH_PRED_SHARE_TRUE3 = 100.0
MAX_LOW_PRED_SHARE_TRUE3 = 100.0


def _load_prev_module() -> types.ModuleType:
    code = subprocess.check_output(["git", "show", f"{PREV_COMMIT}:{SELF_PATH}"], cwd=str(PROJECT_ROOT), text=True)
    mod = types.ModuleType("_km_range_predictor_v3_07c0bcf")
    mod.__file__ = str(PROJECT_ROOT / SELF_PATH)
    mod.__name__ = "_km_range_predictor_v3_07c0bcf"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


P = _load_prev_module()
P.TARGET_MODE = TARGET_MODE
P.BIN_TOLERANCE = 0

_ORIG_INSTALL_DUAL_HEAD_TARGET = P.install_dual_head_target
_ORIG_PARSE_ARGS = P.parse_args
_ORIG_RUN_ORIGINAL_STAGE2_PREDICTOR = P.run_original_stage2_predictor
_ORIG_MAKE_BASELINE_SPEC = P.LEGACY_MAKE_BASELINE_SPEC
_ORIG_CLONE_RULE = P.clone_rule
_ORIG_RANDOM_RULE_FOR_TARGET = P.random_rule_for_target
_ORIG_RANDOM_INDIVIDUAL = P.random_individual
_ORIG_REPAIR_GENE = P.repair_gene
_ORIG_MUTATE_RULE = P.mutate_rule
_ORIG_CROSSOVER = P.crossover
_ORIG_INDIVIDUAL_TO_DICT = P.individual_to_dict


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if not np.isfinite(v):
            return default
        return v
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _coarse(arr: np.ndarray | list[int]) -> np.ndarray:
    return np.clip(np.asarray(arr, dtype=int) // 2, 0, COARSE_BIN_COUNT - 1)


def _pct(mask: np.ndarray) -> float:
    return float(np.mean(mask) * 100.0) if len(mask) else 0.0


def _normalize_coarse_bin(value: Any) -> int:
    return int(max(0, min(COARSE_BIN_COUNT - 1, _safe_int(value))))


def true3_bin_metrics(
    yh3: np.ndarray,
    yl3: np.ndarray,
    ph3: np.ndarray,
    pl3: np.ndarray,
    base_ph3: np.ndarray | None = None,
    base_pl3: np.ndarray | None = None,
) -> dict[str, float]:
    yh3 = np.asarray(yh3, dtype=int)
    yl3 = np.asarray(yl3, dtype=int)
    ph3 = np.asarray(ph3, dtype=int)
    pl3 = np.asarray(pl3, dtype=int)
    n = max(1, int(len(yh3)))

    high_hit = ph3 == yh3
    low_hit = pl3 == yl3
    both_hit = high_hit & low_hit
    high_err = np.abs(ph3 - yh3).astype(float)
    low_err = np.abs(pl3 - yl3).astype(float)

    high_danger = np.maximum(0, ph3 - yh3).astype(float)
    high_safe = np.maximum(0, yh3 - ph3).astype(float)
    low_danger = np.maximum(0, yl3 - pl3).astype(float)
    low_safe = np.maximum(0, pl3 - yl3).astype(float)
    high_safe_overflow = np.maximum(0.0, high_safe - SAFE_OVERFLOW_FREE_BINS)
    low_safe_overflow = np.maximum(0.0, low_safe - SAFE_OVERFLOW_FREE_BINS)

    high_no_danger = high_danger == 0
    low_no_danger = low_danger == 0
    both_no_danger = high_no_danger & low_no_danger

    high_asym = high_danger * HIGH_DANGEROUS_BIN_ERROR_WEIGHT + high_safe_overflow * HIGH_SAFE_BIN_ERROR_WEIGHT
    low_asym = low_danger * LOW_DANGEROUS_BIN_ERROR_WEIGHT + low_safe_overflow * LOW_SAFE_BIN_ERROR_WEIGHT

    high_acc = _pct(high_hit)
    low_acc = _pct(low_hit)
    both_acc = _pct(both_hit)
    combined_acc = (high_acc + low_acc) / 2.0

    out = {
        "sample_count": int(len(yh3)),
        "bin_count": float(COARSE_BIN_COUNT),
        "true_coarse3_mode": 1.0,
        "high_coarse_acc_pct": high_acc,
        "low_coarse_acc_pct": low_acc,
        "both_coarse_acc_pct": both_acc,
        "combined_coarse_acc_pct": combined_acc,
        # compatibility: true 3-bin에서는 exact/adjacent를 같은 값으로 둔다. 근사 성공은 없다.
        "high_exact_acc_pct": high_acc,
        "low_exact_acc_pct": low_acc,
        "both_exact_acc_pct": both_acc,
        "combined_exact_acc_pct": combined_acc,
        "high_adjacent_acc_pct": high_acc,
        "low_adjacent_acc_pct": low_acc,
        "both_adjacent_acc_pct": both_acc,
        "combined_adjacent_acc_pct": combined_acc,
        "high_coarse_error_mean": float(np.mean(high_err)) if n else 0.0,
        "low_coarse_error_mean": float(np.mean(low_err)) if n else 0.0,
        "combined_coarse_error_mean": float((np.mean(high_err) + np.mean(low_err)) / 2.0) if n else 0.0,
        "high_dangerous_bin_error_mean": float(np.mean(high_danger)) if n else 0.0,
        "low_dangerous_bin_error_mean": float(np.mean(low_danger)) if n else 0.0,
        "combined_dangerous_bin_error_mean": float((np.mean(high_danger) + np.mean(low_danger)) / 2.0) if n else 0.0,
        "high_safe_bin_error_mean": float(np.mean(high_safe)) if n else 0.0,
        "low_safe_bin_error_mean": float(np.mean(low_safe)) if n else 0.0,
        "combined_safe_bin_error_mean": float((np.mean(high_safe) + np.mean(low_safe)) / 2.0) if n else 0.0,
        "high_safe_overflow_bin_error_mean": float(np.mean(high_safe_overflow)) if n else 0.0,
        "low_safe_overflow_bin_error_mean": float(np.mean(low_safe_overflow)) if n else 0.0,
        "high_asymmetric_bin_error_mean": float(np.mean(high_asym)) if n else 0.0,
        "low_asymmetric_bin_error_mean": float(np.mean(low_asym)) if n else 0.0,
        "combined_asymmetric_bin_error_mean": float((np.mean(high_asym) + np.mean(low_asym)) / 2.0) if n else 0.0,
        "high_no_danger_acc_pct": _pct(high_no_danger),
        "low_no_danger_acc_pct": _pct(low_no_danger),
        "both_no_danger_acc_pct": _pct(both_no_danger),
        "combined_no_danger_acc_pct": (_pct(high_no_danger) + _pct(low_no_danger)) / 2.0,
        "high_directional_tolerant_acc_pct": _pct(high_no_danger),
        "low_directional_tolerant_acc_pct": _pct(low_no_danger),
        "both_directional_tolerant_acc_pct": _pct(both_no_danger),
        "combined_directional_tolerant_acc_pct": (_pct(high_no_danger) + _pct(low_no_danger)) / 2.0,
        "high_mae_pct": float(np.mean(high_err)) if n else 0.0,
        "low_mae_pct": float(np.mean(low_err)) if n else 0.0,
        "combined_mae_pct": float((np.mean(high_err) + np.mean(low_err)) / 2.0) if n else 0.0,
    }

    if base_ph3 is not None and base_pl3 is not None:
        b = true3_bin_metrics(yh3, yl3, base_ph3, base_pl3)
        acc_keys = [
            "high_coarse_acc_pct", "low_coarse_acc_pct", "both_coarse_acc_pct", "combined_coarse_acc_pct",
            "high_exact_acc_pct", "low_exact_acc_pct", "both_exact_acc_pct", "combined_exact_acc_pct",
            "high_adjacent_acc_pct", "low_adjacent_acc_pct", "both_adjacent_acc_pct", "combined_adjacent_acc_pct",
            "high_no_danger_acc_pct", "low_no_danger_acc_pct", "both_no_danger_acc_pct", "combined_no_danger_acc_pct",
        ]
        for key in acc_keys:
            out[key.replace("_acc_pct", "_lift_pp")] = out[key] - b[key]
            out["baseline_" + key] = b[key]
        for key in [
            "high_coarse_error_mean", "low_coarse_error_mean", "combined_coarse_error_mean",
            "high_dangerous_bin_error_mean", "low_dangerous_bin_error_mean", "combined_dangerous_bin_error_mean",
            "high_asymmetric_bin_error_mean", "low_asymmetric_bin_error_mean", "combined_asymmetric_bin_error_mean",
            "high_mae_pct", "low_mae_pct", "combined_mae_pct",
        ]:
            out["baseline_" + key] = b[key]
        out["high_coarse_error_lift"] = b["high_coarse_error_mean"] - out["high_coarse_error_mean"]
        out["low_coarse_error_lift"] = b["low_coarse_error_mean"] - out["low_coarse_error_mean"]
        out["combined_coarse_error_lift"] = b["combined_coarse_error_mean"] - out["combined_coarse_error_mean"]
        out["high_dangerous_bin_error_lift"] = b["high_dangerous_bin_error_mean"] - out["high_dangerous_bin_error_mean"]
        out["low_dangerous_bin_error_lift"] = b["low_dangerous_bin_error_mean"] - out["low_dangerous_bin_error_mean"]
        out["combined_dangerous_bin_error_lift"] = b["combined_dangerous_bin_error_mean"] - out["combined_dangerous_bin_error_mean"]
        out["high_asymmetric_bin_error_lift"] = b["high_asymmetric_bin_error_mean"] - out["high_asymmetric_bin_error_mean"]
        out["low_asymmetric_bin_error_lift"] = b["low_asymmetric_bin_error_mean"] - out["low_asymmetric_bin_error_mean"]
        out["combined_asymmetric_bin_error_lift"] = b["combined_asymmetric_bin_error_mean"] - out["combined_asymmetric_bin_error_mean"]
        out["high_mae_lift_pct"] = b["high_mae_pct"] - out["high_mae_pct"]
        out["low_mae_lift_pct"] = b["low_mae_pct"] - out["low_mae_pct"]
        out["combined_mae_lift_pct"] = b["combined_mae_pct"] - out["combined_mae_pct"]
    return out


def make_true3_baseline_spec(train_df):
    spec = dict(_ORIG_MAKE_BASELINE_SPEC(train_df))
    yh3 = _coarse(train_df["high_bin"].to_numpy(dtype=int))
    yl3 = _coarse(train_df["low_bin"].to_numpy(dtype=int))
    high_counts = np.bincount(yh3, minlength=COARSE_BIN_COUNT)
    low_counts = np.bincount(yl3, minlength=COARSE_BIN_COUNT)
    spec.update({
        "target_mode": TARGET_MODE,
        "coarse_bin_count": COARSE_BIN_COUNT,
        "coarse_bin_mapping": {"0": [0, 1], "1": [2, 3], "2": [4, 5]},
        "exact_high_coarse_bin": int(np.argmax(high_counts)),
        "exact_low_coarse_bin": int(np.argmax(low_counts)),
        "exact_high_bin": int(np.argmax(high_counts)),
        "exact_low_bin": int(np.argmax(low_counts)),
        "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
        "true_coarse3_model": True,
    })
    return spec


def clone_rule(rule: Any):
    r = _ORIG_CLONE_RULE(rule)
    r.bin = _normalize_coarse_bin(getattr(r, "bin", 0))
    return r


def random_rule_for_target(target: str, rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    r = _ORIG_RANDOM_RULE_FOR_TARGET(target, rng, qspec)
    r.bin = int(rng.randrange(COARSE_BIN_COUNT))
    return r


def repair_gene(rule: Any, target: str, rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    r = _ORIG_REPAIR_GENE(rule, target, rng, qspec)
    r.bin = _normalize_coarse_bin(getattr(r, "bin", 0))
    return r


def repair_head_rules(rules: list[Any], target: str, count: int, rng: random.Random, qspec: dict[str, dict[str, list[float]]]):
    out = [repair_gene(r, target, rng, qspec) for r in rules]
    while len(out) < count and qspec:
        out.append(random_rule_for_target(target, rng, qspec))
    return out[:count]


def mutate_rule(rule: Any, rng: random.Random, qspec: dict[str, dict[str, list[float]]], target: str):
    r = _ORIG_MUTATE_RULE(rule, rng, qspec, target)
    r.bin = _normalize_coarse_bin(getattr(r, "bin", 0))
    if rng.random() < 0.25:
        r.bin = int(rng.randrange(COARSE_BIN_COUNT))
    return r


def random_individual(rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any]):
    ind = _ORIG_RANDOM_INDIVIDUAL(rng, qspec, baseline_spec)
    ind.high_rules = repair_head_rules(ind.high_rules, "HIGH", P.HIGH_RULE_COUNT, rng, qspec)
    ind.low_rules = repair_head_rules(ind.low_rules, "LOW", P.LOW_RULE_COUNT, rng, qspec)
    ind.default_high_bin = _normalize_coarse_bin(baseline_spec.get("exact_high_coarse_bin", baseline_spec.get("exact_high_bin", 1)))
    ind.default_low_bin = _normalize_coarse_bin(baseline_spec.get("exact_low_coarse_bin", baseline_spec.get("exact_low_bin", 1)))
    ind.baseline_spec = dict(baseline_spec)
    return ind


def mutate(ind: Any, rng: random.Random, qspec: dict[str, dict[str, list[float]]], baseline_spec: dict[str, Any] | None = None):
    child = P.clone_individual(ind)
    child.fitness = -1e9
    child.metrics = None
    child.signature = None
    if baseline_spec is not None:
        child.baseline_spec = dict(baseline_spec)
        child.default_high_bin = _normalize_coarse_bin(baseline_spec.get("exact_high_coarse_bin", child.default_high_bin))
        child.default_low_bin = _normalize_coarse_bin(baseline_spec.get("exact_low_coarse_bin", child.default_low_bin))
    child.high_rules = repair_head_rules(child.high_rules, "HIGH", P.HIGH_RULE_COUNT, rng, qspec)
    child.low_rules = repair_head_rules(child.low_rules, "LOW", P.LOW_RULE_COUNT, rng, qspec)
    for i, rule in enumerate(child.high_rules):
        if rng.random() <= P.L.MUTATION_RATE:
            child.high_rules[i] = mutate_rule(rule, rng, qspec, "HIGH")
    for i, rule in enumerate(child.low_rules):
        if rng.random() <= P.L.MUTATION_RATE:
            child.low_rules[i] = mutate_rule(rule, rng, qspec, "LOW")
    return child


def crossover(a: Any, b: Any, rng: random.Random, baseline_spec: dict[str, Any]):
    child = _ORIG_CROSSOVER(a, b, rng, baseline_spec)
    child.high_rules = repair_head_rules(child.high_rules, "HIGH", P.HIGH_RULE_COUNT, rng, {})
    child.low_rules = repair_head_rules(child.low_rules, "LOW", P.LOW_RULE_COUNT, rng, {})
    child.default_high_bin = _normalize_coarse_bin(baseline_spec.get("exact_high_coarse_bin", child.default_high_bin))
    child.default_low_bin = _normalize_coarse_bin(baseline_spec.get("exact_low_coarse_bin", child.default_low_bin))
    child.baseline_spec = dict(baseline_spec)
    return child


def predictor_signature(ind: Any) -> str:
    payload = json.dumps({
        "version": "true_coarse3_multicond_dual_head_v1",
        "default_high_bin": int(ind.default_high_bin),
        "default_low_bin": int(ind.default_low_bin),
        "high_rules": [P.rule_payload(clone_rule(r)) for r in ind.high_rules],
        "low_rules": [P.rule_payload(clone_rule(r)) for r in ind.low_rules],
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def individual_to_dict(ind: Any) -> dict[str, Any]:
    d = _ORIG_INDIVIDUAL_TO_DICT(ind)
    d["type"] = "true_coarse3_multi_condition_dual_head_hilo_predictor"
    d["target_mode"] = TARGET_MODE
    d["coarse_bin_count"] = COARSE_BIN_COUNT
    d["signature"] = ind.signature or predictor_signature(ind)
    return d


def predict(ind: Any, X, qspec: dict[str, dict[str, list[float]]]):
    n = len(X)
    hs = np.zeros((n, COARSE_BIN_COUNT), dtype=float)
    ls = np.zeros((n, COARSE_BIN_COUNT), dtype=float)
    hs[:, _normalize_coarse_bin(ind.default_high_bin)] = 1.0
    ls[:, _normalize_coarse_bin(ind.default_low_bin)] = 1.0

    high_active = low_active = high_condition_count = low_condition_count = 0
    high_strength_sum = low_strength_sum = 0.0

    for raw_gene in ind.high_rules:
        gene = clone_rule(raw_gene)
        strength, _widths, cond_count = P.gene_strength(gene, X, qspec)
        if strength is None or not np.any(strength > 0):
            continue
        hs[:, _normalize_coarse_bin(gene.bin)] += strength * float(gene.weight)
        high_active += 1
        high_condition_count += cond_count
        high_strength_sum += float(np.mean(strength))

    for raw_gene in ind.low_rules:
        gene = clone_rule(raw_gene)
        strength, _widths, cond_count = P.gene_strength(gene, X, qspec)
        if strength is None or not np.any(strength > 0):
            continue
        ls[:, _normalize_coarse_bin(gene.bin)] += strength * float(gene.weight)
        low_active += 1
        low_condition_count += cond_count
        low_strength_sum += float(np.mean(strength))

    active = high_active + low_active
    diag = {
        "active_rule_count": int(active),
        "active_gene_count": int(active),
        "active_condition_count": int(high_condition_count + low_condition_count),
        "high_active_rule_count": int(high_active),
        "low_active_rule_count": int(low_active),
        "high_active_gene_count": int(high_active),
        "low_active_gene_count": int(low_active),
        "high_active_condition_count": int(high_condition_count),
        "low_active_condition_count": int(low_condition_count),
        "avg_rule_match_strength": float((high_strength_sum + low_strength_sum) / max(1, active)),
        "avg_gene_match_strength": float((high_strength_sum + low_strength_sum) / max(1, active)),
        "high_avg_gene_match_strength": float(high_strength_sum / max(1, high_active)),
        "low_avg_gene_match_strength": float(low_strength_sum / max(1, low_active)),
        "avg_conditions_per_active_gene": float((high_condition_count + low_condition_count) / max(1, active)),
        "high_avg_conditions_per_active_gene": float(high_condition_count / max(1, high_active)),
        "low_avg_conditions_per_active_gene": float(low_condition_count / max(1, low_active)),
    }
    return hs.argmax(axis=1), ls.argmax(axis=1), diag


def _share_by_bin(pred: np.ndarray) -> list[float]:
    counts = np.bincount(pred.astype(int), minlength=COARSE_BIN_COUNT)
    total = max(1, int(counts.sum()))
    return [float(c / total * 100.0) for c in counts]


def prediction_penalty(ind: Any, yh3: np.ndarray, yl3: np.ndarray, ph3: np.ndarray, pl3: np.ndarray) -> dict[str, Any]:
    hp = _share_by_bin(ph3)
    lp = _share_by_bin(pl3)
    high_conc = max(0.0, max(hp) - 70.0)
    low_conc = max(0.0, max(lp) - 70.0)
    total = high_conc * 0.20 + low_conc * 0.25
    return {
        "high_concentration_penalty": high_conc * 0.20,
        "low_concentration_penalty": low_conc * 0.25,
        "high_rare_bin_penalty": 0.0,
        "low_rare_bin_penalty": 0.0,
        "high_narrow_band_penalty": 0.0,
        "high_wide_band_penalty": 0.0,
        "low_narrow_band_penalty": 0.0,
        "low_wide_band_penalty": 0.0,
        "high_total_penalty": high_conc * 0.20,
        "low_total_penalty": low_conc * 0.25,
        "total_penalty": total,
        "max_pred_share_high_pct": max(hp) if hp else 0.0,
        "max_pred_share_low_pct": max(lp) if lp else 0.0,
        "pred_distribution_high_pct": hp,
        "pred_distribution_low_pct": lp,
    }


def predictor_fitness(metrics: Mapping[str, Any]) -> float:
    high_component = (
        _safe_float(metrics.get("high_coarse_lift_pp")) * HIGH_COARSE_EXACT_WEIGHT
        + _safe_float(metrics.get("high_no_danger_lift_pp")) * HIGH_NO_DANGER_WEIGHT
        + _safe_float(metrics.get("high_asymmetric_bin_error_lift")) * ASYMMETRIC_BIN_ERROR_WEIGHT
        + _safe_float(metrics.get("high_coarse_error_lift")) * COARSE_ERROR_WEIGHT
    )
    low_component = (
        _safe_float(metrics.get("low_coarse_lift_pp")) * LOW_COARSE_EXACT_WEIGHT
        + _safe_float(metrics.get("low_no_danger_lift_pp")) * LOW_NO_DANGER_WEIGHT
        + _safe_float(metrics.get("low_asymmetric_bin_error_lift")) * ASYMMETRIC_BIN_ERROR_WEIGHT
        + _safe_float(metrics.get("low_coarse_error_lift")) * COARSE_ERROR_WEIGHT
    )
    both_component = (
        _safe_float(metrics.get("both_coarse_lift_pp")) * BOTH_COARSE_EXACT_WEIGHT
        + _safe_float(metrics.get("combined_coarse_lift_pp")) * COMBINED_COARSE_EXACT_WEIGHT
        + _safe_float(metrics.get("both_no_danger_lift_pp")) * BOTH_NO_DANGER_WEIGHT
        + _safe_float(metrics.get("combined_asymmetric_bin_error_lift")) * ASYMMETRIC_BIN_ERROR_WEIGHT
        + _safe_float(metrics.get("combined_coarse_error_lift")) * COARSE_ERROR_WEIGHT
    )
    imbalance = abs(high_component - low_component) * HEAD_IMBALANCE_PENALTY
    return float(high_component + low_component + both_component - imbalance - _safe_float(metrics.get("total_penalty")))


def evaluate_predictor(ind: Any, df, features: list[str], qspec: dict[str, dict[str, list[float]]]) -> dict[str, Any]:
    yh3 = _coarse(df["high_bin"].to_numpy(dtype=int))
    yl3 = _coarse(df["low_bin"].to_numpy(dtype=int))
    ph3, pl3, pred_diag = predict(ind, df[features], qspec)
    base_ph3 = np.full(len(df), _normalize_coarse_bin(ind.baseline_spec.get("exact_high_coarse_bin", ind.default_high_bin)), dtype=int)
    base_pl3 = np.full(len(df), _normalize_coarse_bin(ind.baseline_spec.get("exact_low_coarse_bin", ind.default_low_bin)), dtype=int)
    metrics = true3_bin_metrics(yh3, yl3, ph3, pl3, base_ph3, base_pl3)
    penalty = prediction_penalty(ind, yh3, yl3, ph3, pl3)
    metrics.update({
        "target_mode": TARGET_MODE,
        "sample_count": int(len(df)),
        "high_rule_count": len(ind.high_rules),
        "low_rule_count": len(ind.low_rules),
        "coarse_bin_count": COARSE_BIN_COUNT,
        **penalty,
        **pred_diag,
    })
    metrics["high_component_score"] = _safe_float(metrics.get("high_coarse_lift_pp")) * HIGH_COARSE_EXACT_WEIGHT
    metrics["low_component_score"] = _safe_float(metrics.get("low_coarse_lift_pp")) * LOW_COARSE_EXACT_WEIGHT
    metrics["both_component_score"] = _safe_float(metrics.get("both_coarse_lift_pp")) * BOTH_COARSE_EXACT_WEIGHT
    metrics["fitness"] = predictor_fitness(metrics)
    return metrics


def dual_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    checks = [
        ("sample_count", _safe_int(metrics.get("sample_count")), 100, ">="),
        ("high_coarse_acc_pct", _safe_float(metrics.get("high_coarse_acc_pct")), MIN_HIGH_COARSE_ACC, ">="),
        ("low_coarse_acc_pct", _safe_float(metrics.get("low_coarse_acc_pct")), MIN_LOW_COARSE_ACC, ">="),
        ("both_coarse_acc_pct", _safe_float(metrics.get("both_coarse_acc_pct")), MIN_BOTH_COARSE_ACC, ">="),
        ("combined_coarse_acc_pct", _safe_float(metrics.get("combined_coarse_acc_pct")), MIN_COMBINED_COARSE_ACC, ">="),
        ("high_coarse_lift_pp", _safe_float(metrics.get("high_coarse_lift_pp")), MIN_HIGH_COARSE_LIFT, ">="),
        ("low_coarse_lift_pp", _safe_float(metrics.get("low_coarse_lift_pp")), MIN_LOW_COARSE_LIFT, ">="),
        ("both_coarse_lift_pp", _safe_float(metrics.get("both_coarse_lift_pp")), MIN_BOTH_COARSE_LIFT, ">="),
        ("high_no_danger_acc_pct", _safe_float(metrics.get("high_no_danger_acc_pct")), MIN_HIGH_NO_DANGER, ">="),
        ("low_no_danger_acc_pct", _safe_float(metrics.get("low_no_danger_acc_pct")), MIN_LOW_NO_DANGER, ">="),
        ("both_no_danger_acc_pct", _safe_float(metrics.get("both_no_danger_acc_pct")), MIN_BOTH_NO_DANGER, ">="),
        ("high_dangerous_bin_error_mean", _safe_float(metrics.get("high_dangerous_bin_error_mean")), MAX_HIGH_DANGEROUS_BIN_ERROR, "<="),
        ("low_dangerous_bin_error_mean", _safe_float(metrics.get("low_dangerous_bin_error_mean")), MAX_LOW_DANGEROUS_BIN_ERROR, "<="),
        ("high_safe_bin_error_mean", _safe_float(metrics.get("high_safe_bin_error_mean")), MAX_HIGH_SAFE_BIN_ERROR, "<="),
        ("low_safe_bin_error_mean", _safe_float(metrics.get("low_safe_bin_error_mean")), MAX_LOW_SAFE_BIN_ERROR, "<="),
        ("total_penalty", _safe_float(metrics.get("total_penalty")), MAX_TOTAL_PENALTY_TRUE3, "<="),
        ("max_pred_share_high_pct", _safe_float(metrics.get("max_pred_share_high_pct")), MAX_HIGH_PRED_SHARE_TRUE3, "<="),
        ("max_pred_share_low_pct", _safe_float(metrics.get("max_pred_share_low_pct")), MAX_LOW_PRED_SHARE_TRUE3, "<="),
    ]
    out = []
    for metric, value, threshold, rule in checks:
        failed = (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold)
        if failed:
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def dual_head_params() -> dict[str, Any]:
    return {
        "mode": TARGET_MODE,
        "coarse_bin_count": COARSE_BIN_COUNT,
        "coarse_bin_mapping": {"0": [0, 1], "1": [2, 3], "2": [4, 5]},
        "no_adjacent_success": True,
        "high_rule_count": P.HIGH_RULE_COUNT,
        "low_rule_count": P.LOW_RULE_COUNT,
        "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
        "weights": {
            "high_coarse_exact": HIGH_COARSE_EXACT_WEIGHT,
            "low_coarse_exact": LOW_COARSE_EXACT_WEIGHT,
            "both_coarse_exact": BOTH_COARSE_EXACT_WEIGHT,
            "combined_coarse_exact": COMBINED_COARSE_EXACT_WEIGHT,
            "high_no_danger": HIGH_NO_DANGER_WEIGHT,
            "low_no_danger": LOW_NO_DANGER_WEIGHT,
            "both_no_danger": BOTH_NO_DANGER_WEIGHT,
            "asymmetric_bin_error": ASYMMETRIC_BIN_ERROR_WEIGHT,
            "coarse_error": COARSE_ERROR_WEIGHT,
        },
        "gate": {
            "min_high_coarse_acc": MIN_HIGH_COARSE_ACC,
            "min_low_coarse_acc": MIN_LOW_COARSE_ACC,
            "min_both_coarse_acc": MIN_BOTH_COARSE_ACC,
            "min_combined_coarse_acc": MIN_COMBINED_COARSE_ACC,
            "min_high_coarse_lift": MIN_HIGH_COARSE_LIFT,
            "min_low_coarse_lift": MIN_LOW_COARSE_LIFT,
            "min_both_coarse_lift": MIN_BOTH_COARSE_LIFT,
            "min_high_no_danger": MIN_HIGH_NO_DANGER,
            "min_low_no_danger": MIN_LOW_NO_DANGER,
            "min_both_no_danger": MIN_BOTH_NO_DANGER,
            "max_high_dangerous_bin_error": MAX_HIGH_DANGEROUS_BIN_ERROR,
            "max_low_dangerous_bin_error": MAX_LOW_DANGEROUS_BIN_ERROR,
            "max_high_safe_bin_error": MAX_HIGH_SAFE_BIN_ERROR,
            "max_low_safe_bin_error": MAX_LOW_SAFE_BIN_ERROR,
            "max_total_penalty": MAX_TOTAL_PENALTY_TRUE3,
            "max_high_pred_share": MAX_HIGH_PRED_SHARE_TRUE3,
            "max_low_pred_share": MAX_LOW_PRED_SHARE_TRUE3,
        },
    }


def _parse_wrapper_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--safe-overflow-free-bins", type=float, default=SAFE_OVERFLOW_FREE_BINS)
    parser.add_argument("--true3-high-dangerous-bin-error-weight", type=float, default=HIGH_DANGEROUS_BIN_ERROR_WEIGHT)
    parser.add_argument("--true3-high-safe-bin-error-weight", type=float, default=HIGH_SAFE_BIN_ERROR_WEIGHT)
    parser.add_argument("--true3-low-dangerous-bin-error-weight", type=float, default=LOW_DANGEROUS_BIN_ERROR_WEIGHT)
    parser.add_argument("--true3-low-safe-bin-error-weight", type=float, default=LOW_SAFE_BIN_ERROR_WEIGHT)
    parser.add_argument("--true3-asymmetric-bin-error-weight", type=float, default=ASYMMETRIC_BIN_ERROR_WEIGHT)
    parser.add_argument("--min-high-coarse-acc", type=float, default=MIN_HIGH_COARSE_ACC)
    parser.add_argument("--min-low-coarse-acc", type=float, default=MIN_LOW_COARSE_ACC)
    parser.add_argument("--min-both-coarse-acc", type=float, default=MIN_BOTH_COARSE_ACC)
    parser.add_argument("--min-combined-coarse-acc", type=float, default=MIN_COMBINED_COARSE_ACC)
    parser.add_argument("--min-high-coarse-lift", type=float, default=MIN_HIGH_COARSE_LIFT)
    parser.add_argument("--min-low-coarse-lift", type=float, default=MIN_LOW_COARSE_LIFT)
    parser.add_argument("--min-both-coarse-lift", type=float, default=MIN_BOTH_COARSE_LIFT)
    parser.add_argument("--min-high-no-danger", type=float, default=MIN_HIGH_NO_DANGER)
    parser.add_argument("--min-low-no-danger", type=float, default=MIN_LOW_NO_DANGER)
    parser.add_argument("--min-both-no-danger", type=float, default=MIN_BOTH_NO_DANGER)
    parser.add_argument("--max-high-dangerous-bin-error", type=float, default=MAX_HIGH_DANGEROUS_BIN_ERROR)
    parser.add_argument("--max-low-dangerous-bin-error", type=float, default=MAX_LOW_DANGEROUS_BIN_ERROR)
    parser.add_argument("--max-high-safe-bin-error", type=float, default=MAX_HIGH_SAFE_BIN_ERROR)
    parser.add_argument("--max-low-safe-bin-error", type=float, default=MAX_LOW_SAFE_BIN_ERROR)
    parser.add_argument("--true3-max-total-penalty", type=float, default=MAX_TOTAL_PENALTY_TRUE3)
    parser.add_argument("--true3-max-high-pred-share", type=float, default=MAX_HIGH_PRED_SHARE_TRUE3)
    parser.add_argument("--true3-max-low-pred-share", type=float, default=MAX_LOW_PRED_SHARE_TRUE3)
    return parser.parse_known_args(argv)


def _apply_wrapper_args(wrapper_args: argparse.Namespace) -> None:
    global SAFE_OVERFLOW_FREE_BINS, HIGH_DANGEROUS_BIN_ERROR_WEIGHT, HIGH_SAFE_BIN_ERROR_WEIGHT
    global LOW_DANGEROUS_BIN_ERROR_WEIGHT, LOW_SAFE_BIN_ERROR_WEIGHT, ASYMMETRIC_BIN_ERROR_WEIGHT
    global MIN_HIGH_COARSE_ACC, MIN_LOW_COARSE_ACC, MIN_BOTH_COARSE_ACC, MIN_COMBINED_COARSE_ACC
    global MIN_HIGH_COARSE_LIFT, MIN_LOW_COARSE_LIFT, MIN_BOTH_COARSE_LIFT
    global MIN_HIGH_NO_DANGER, MIN_LOW_NO_DANGER, MIN_BOTH_NO_DANGER
    global MAX_HIGH_DANGEROUS_BIN_ERROR, MAX_LOW_DANGEROUS_BIN_ERROR, MAX_HIGH_SAFE_BIN_ERROR, MAX_LOW_SAFE_BIN_ERROR
    global MAX_TOTAL_PENALTY_TRUE3, MAX_HIGH_PRED_SHARE_TRUE3, MAX_LOW_PRED_SHARE_TRUE3

    SAFE_OVERFLOW_FREE_BINS = max(0.0, float(wrapper_args.safe_overflow_free_bins))
    HIGH_DANGEROUS_BIN_ERROR_WEIGHT = float(wrapper_args.true3_high_dangerous_bin_error_weight)
    HIGH_SAFE_BIN_ERROR_WEIGHT = float(wrapper_args.true3_high_safe_bin_error_weight)
    LOW_DANGEROUS_BIN_ERROR_WEIGHT = float(wrapper_args.true3_low_dangerous_bin_error_weight)
    LOW_SAFE_BIN_ERROR_WEIGHT = float(wrapper_args.true3_low_safe_bin_error_weight)
    ASYMMETRIC_BIN_ERROR_WEIGHT = float(wrapper_args.true3_asymmetric_bin_error_weight)
    MIN_HIGH_COARSE_ACC = float(wrapper_args.min_high_coarse_acc)
    MIN_LOW_COARSE_ACC = float(wrapper_args.min_low_coarse_acc)
    MIN_BOTH_COARSE_ACC = float(wrapper_args.min_both_coarse_acc)
    MIN_COMBINED_COARSE_ACC = float(wrapper_args.min_combined_coarse_acc)
    MIN_HIGH_COARSE_LIFT = float(wrapper_args.min_high_coarse_lift)
    MIN_LOW_COARSE_LIFT = float(wrapper_args.min_low_coarse_lift)
    MIN_BOTH_COARSE_LIFT = float(wrapper_args.min_both_coarse_lift)
    MIN_HIGH_NO_DANGER = float(wrapper_args.min_high_no_danger)
    MIN_LOW_NO_DANGER = float(wrapper_args.min_low_no_danger)
    MIN_BOTH_NO_DANGER = float(wrapper_args.min_both_no_danger)
    MAX_HIGH_DANGEROUS_BIN_ERROR = float(wrapper_args.max_high_dangerous_bin_error)
    MAX_LOW_DANGEROUS_BIN_ERROR = float(wrapper_args.max_low_dangerous_bin_error)
    MAX_HIGH_SAFE_BIN_ERROR = float(wrapper_args.max_high_safe_bin_error)
    MAX_LOW_SAFE_BIN_ERROR = float(wrapper_args.max_low_safe_bin_error)
    MAX_TOTAL_PENALTY_TRUE3 = float(wrapper_args.true3_max_total_penalty)
    MAX_HIGH_PRED_SHARE_TRUE3 = float(wrapper_args.true3_max_high_pred_share)
    MAX_LOW_PRED_SHARE_TRUE3 = float(wrapper_args.true3_max_low_pred_share)


_ORIG_INSTALL_DUAL_HEAD_TARGET = P.install_dual_head_target
_ORIG_PARSE_ARGS = P.parse_args
_ORIG_RUN_ORIGINAL_STAGE2_PREDICTOR = P.run_original_stage2_predictor


def install_dual_head_target(args: Any) -> None:
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    _ORIG_INSTALL_DUAL_HEAD_TARGET(args)
    P.TARGET_MODE = TARGET_MODE
    P.BIN_TOLERANCE = 0
    P.COARSE_BIN_COUNT = COARSE_BIN_COUNT
    replacements = {
        "make_baseline_spec": make_true3_baseline_spec,
        "clone_rule": clone_rule,
        "random_rule_for_target": random_rule_for_target,
        "random_individual": random_individual,
        "repair_gene": repair_gene,
        "repair_head_rules": repair_head_rules,
        "mutate_rule": mutate_rule,
        "mutate": mutate,
        "crossover": crossover,
        "predict": predict,
        "evaluate_predictor": evaluate_predictor,
        "predictor_fitness": predictor_fitness,
        "prediction_penalty": prediction_penalty,
        "dual_fail_reasons": dual_fail_reasons,
        "predictor_signature": predictor_signature,
        "individual_to_dict": individual_to_dict,
        "dual_head_params": dual_head_params,
    }
    for name, value in replacements.items():
        setattr(P, name, value)
    P.L.make_baseline_spec = make_true3_baseline_spec
    P.L.random_individual = random_individual
    P.L.mutate = mutate
    P.L.crossover = crossover
    P.L.predict = predict
    P.L.evaluate_predictor = evaluate_predictor
    P.L.predictor_signature = predictor_signature
    P.L.individual_to_dict = individual_to_dict


def parse_args(argv: list[str] | None = None):
    wrapper_args, remaining = _parse_wrapper_args(sys.argv[1:] if argv is None else argv)
    _apply_wrapper_args(wrapper_args)
    args = _ORIG_PARSE_ARGS(remaining)
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return args


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: Any):
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return _ORIG_RUN_ORIGINAL_STAGE2_PREDICTOR(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


for _name in dir(P):
    if _name.startswith("__"):
        continue
    if _name in globals():
        continue
    globals()[_name] = getattr(P, _name)

for _name, _value in {
    "TARGET_MODE": TARGET_MODE,
    "COARSE_BIN_COUNT": COARSE_BIN_COUNT,
    "true3_bin_metrics": true3_bin_metrics,
    "make_true3_baseline_spec": make_true3_baseline_spec,
    "clone_rule": clone_rule,
    "random_rule_for_target": random_rule_for_target,
    "random_individual": random_individual,
    "repair_gene": repair_gene,
    "repair_head_rules": repair_head_rules,
    "mutate_rule": mutate_rule,
    "mutate": mutate,
    "crossover": crossover,
    "predict": predict,
    "evaluate_predictor": evaluate_predictor,
    "predictor_fitness": predictor_fitness,
    "prediction_penalty": prediction_penalty,
    "dual_fail_reasons": dual_fail_reasons,
    "predictor_signature": predictor_signature,
    "individual_to_dict": individual_to_dict,
    "dual_head_params": dual_head_params,
    "install_dual_head_target": install_dual_head_target,
    "parse_args": parse_args,
    "run_original_stage2_predictor": run_original_stage2_predictor,
}.items():
    globals()[_name] = _value


def default_seed_base(ticker: str) -> int:
    if hasattr(P, "default_seed_base"):
        return int(P.default_seed_base(ticker))
    return int(P.L.default_seed_base(ticker))


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
