#!/usr/bin/env python3
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
TARGET_MODE = "next_day_hilo_true_coarse3_pattern_detector_stage2"
COARSE_BIN_COUNT = 3

# True 3-bin risk scoring. 3-bin은 이미 넓은 구간이라 adjacent/근사 성공은 쓰지 않는다.
SAFE_OVERFLOW_FREE_BINS = 0.0
HIGH_DANGEROUS_BIN_ERROR_WEIGHT = 1.0
HIGH_SAFE_BIN_ERROR_WEIGHT = 0.05
LOW_DANGEROUS_BIN_ERROR_WEIGHT = 1.7
LOW_SAFE_BIN_ERROR_WEIGHT = 0.20
ASYMMETRIC_BIN_ERROR_WEIGHT = 1.25

# Signal day fitness weights.
HIGH_COARSE_EXACT_WEIGHT = 0.75
LOW_COARSE_EXACT_WEIGHT = 1.00
BOTH_COARSE_EXACT_WEIGHT = 1.20
COMBINED_COARSE_EXACT_WEIGHT = 0.45
HIGH_NO_DANGER_WEIGHT = 0.25
LOW_NO_DANGER_WEIGHT = 0.45
BOTH_NO_DANGER_WEIGHT = 0.35
COARSE_ERROR_WEIGHT = 0.35
HEAD_IMBALANCE_PENALTY = 0.08

# Pattern detector thresholds. Signal이 없으면 예측하지 않는다.
SIGNAL_MIN_ACTIVE_GENES_PER_HEAD = 2
SIGNAL_MIN_HEAD_SCORE = 0.20
SIGNAL_MIN_MARGIN = 0.04
MIN_SIGNAL_COUNT = 8
MIN_SIGNAL_COVERAGE_PCT = 3.0
MAX_SIGNAL_COVERAGE_PCT = 35.0
TARGET_SIGNAL_COVERAGE_PCT = 12.0

# Stage2 gate defaults. CLI로 조정한다.
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
_ORIG_INSTALL = P.install_dual_head_target
_ORIG_PARSE_ARGS = P.parse_args
_ORIG_RUN = P.run_original_stage2_predictor
_ORIG_BASELINE = P.LEGACY_MAKE_BASELINE_SPEC
_ORIG_CLONE_RULE = P.clone_rule
_ORIG_RANDOM_RULE = P.random_rule_for_target
_ORIG_RANDOM_INDIVIDUAL = P.random_individual
_ORIG_REPAIR_GENE = P.repair_gene
_ORIG_MUTATE_RULE = P.mutate_rule
_ORIG_CROSSOVER = P.crossover
_ORIG_INDIVIDUAL_TO_DICT = P.individual_to_dict


def _sf(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else d
    except Exception:
        return d


def _si(v: Any, d: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return d


def _coarse(v: Any) -> np.ndarray:
    return np.clip(np.asarray(v, dtype=int) // 2, 0, COARSE_BIN_COUNT - 1)


def _cb(v: Any) -> int:
    return int(max(0, min(COARSE_BIN_COUNT - 1, _si(v))))


def _pct(mask: np.ndarray) -> float:
    return float(np.mean(mask) * 100.0) if len(mask) else 0.0


def _empty_int() -> np.ndarray:
    return np.asarray([], dtype=int)


def true3_bin_metrics(yh3, yl3, ph3, pl3, base_ph3=None, base_pl3=None) -> dict[str, float]:
    yh3 = np.asarray(yh3, dtype=int)
    yl3 = np.asarray(yl3, dtype=int)
    ph3 = np.asarray(ph3, dtype=int)
    pl3 = np.asarray(pl3, dtype=int)
    n = len(yh3)
    hh = ph3 == yh3
    lh = pl3 == yl3
    bh = hh & lh
    he = np.abs(ph3 - yh3).astype(float)
    le = np.abs(pl3 - yl3).astype(float)
    hd = np.maximum(0, ph3 - yh3).astype(float)
    ld = np.maximum(0, yl3 - pl3).astype(float)
    hs = np.maximum(0, yh3 - ph3).astype(float)
    ls = np.maximum(0, pl3 - yl3).astype(float)
    hso = np.maximum(0.0, hs - SAFE_OVERFLOW_FREE_BINS)
    lso = np.maximum(0.0, ls - SAFE_OVERFLOW_FREE_BINS)
    hnd = hd == 0
    lnd = ld == 0
    bnd = hnd & lnd
    ha = hd * HIGH_DANGEROUS_BIN_ERROR_WEIGHT + hso * HIGH_SAFE_BIN_ERROR_WEIGHT
    la = ld * LOW_DANGEROUS_BIN_ERROR_WEIGHT + lso * LOW_SAFE_BIN_ERROR_WEIGHT
    hacc, lacc, bacc = _pct(hh), _pct(lh), _pct(bh)
    cacc = (hacc + lacc) / 2.0
    out = {
        "sample_count": int(n),
        "bin_count": float(COARSE_BIN_COUNT),
        "true_coarse3_mode": 1.0,
        "pattern_detector_mode": 1.0,
        "high_coarse_acc_pct": hacc,
        "low_coarse_acc_pct": lacc,
        "both_coarse_acc_pct": bacc,
        "combined_coarse_acc_pct": cacc,
        # Compatibility: true 3-bin에서는 exact == adjacent. 근사 성공은 없다.
        "high_exact_acc_pct": hacc,
        "low_exact_acc_pct": lacc,
        "both_exact_acc_pct": bacc,
        "combined_exact_acc_pct": cacc,
        "high_adjacent_acc_pct": hacc,
        "low_adjacent_acc_pct": lacc,
        "both_adjacent_acc_pct": bacc,
        "combined_adjacent_acc_pct": cacc,
        "high_coarse_error_mean": float(np.mean(he)) if n else 0.0,
        "low_coarse_error_mean": float(np.mean(le)) if n else 0.0,
        "combined_coarse_error_mean": float((np.mean(he) + np.mean(le)) / 2.0) if n else 0.0,
        "high_dangerous_bin_error_mean": float(np.mean(hd)) if n else 0.0,
        "low_dangerous_bin_error_mean": float(np.mean(ld)) if n else 0.0,
        "combined_dangerous_bin_error_mean": float((np.mean(hd) + np.mean(ld)) / 2.0) if n else 0.0,
        "high_safe_bin_error_mean": float(np.mean(hs)) if n else 0.0,
        "low_safe_bin_error_mean": float(np.mean(ls)) if n else 0.0,
        "combined_safe_bin_error_mean": float((np.mean(hs) + np.mean(ls)) / 2.0) if n else 0.0,
        "high_safe_overflow_bin_error_mean": float(np.mean(hso)) if n else 0.0,
        "low_safe_overflow_bin_error_mean": float(np.mean(lso)) if n else 0.0,
        "high_asymmetric_bin_error_mean": float(np.mean(ha)) if n else 0.0,
        "low_asymmetric_bin_error_mean": float(np.mean(la)) if n else 0.0,
        "combined_asymmetric_bin_error_mean": float((np.mean(ha) + np.mean(la)) / 2.0) if n else 0.0,
        "high_no_danger_acc_pct": _pct(hnd),
        "low_no_danger_acc_pct": _pct(lnd),
        "both_no_danger_acc_pct": _pct(bnd),
        "combined_no_danger_acc_pct": (_pct(hnd) + _pct(lnd)) / 2.0,
        "high_directional_tolerant_acc_pct": _pct(hnd),
        "low_directional_tolerant_acc_pct": _pct(lnd),
        "both_directional_tolerant_acc_pct": _pct(bnd),
        "combined_directional_tolerant_acc_pct": (_pct(hnd) + _pct(lnd)) / 2.0,
        "high_mae_pct": float(np.mean(he)) if n else 0.0,
        "low_mae_pct": float(np.mean(le)) if n else 0.0,
        "combined_mae_pct": float((np.mean(he) + np.mean(le)) / 2.0) if n else 0.0,
    }
    if base_ph3 is not None and base_pl3 is not None:
        b = true3_bin_metrics(yh3, yl3, base_ph3, base_pl3)
        acc_keys = [
            "high_coarse_acc_pct", "low_coarse_acc_pct", "both_coarse_acc_pct", "combined_coarse_acc_pct",
            "high_exact_acc_pct", "low_exact_acc_pct", "both_exact_acc_pct", "combined_exact_acc_pct",
            "high_adjacent_acc_pct", "low_adjacent_acc_pct", "both_adjacent_acc_pct", "combined_adjacent_acc_pct",
            "high_no_danger_acc_pct", "low_no_danger_acc_pct", "both_no_danger_acc_pct", "combined_no_danger_acc_pct",
        ]
        for k in acc_keys:
            out[k.replace("_acc_pct", "_lift_pp")] = out[k] - b[k]
            out["baseline_" + k] = b[k]
        for k in [
            "high_coarse_error_mean", "low_coarse_error_mean", "combined_coarse_error_mean",
            "high_dangerous_bin_error_mean", "low_dangerous_bin_error_mean", "combined_dangerous_bin_error_mean",
            "high_asymmetric_bin_error_mean", "low_asymmetric_bin_error_mean", "combined_asymmetric_bin_error_mean",
            "high_mae_pct", "low_mae_pct", "combined_mae_pct",
        ]:
            out["baseline_" + k] = b[k]
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
    spec = dict(_ORIG_BASELINE(train_df))
    yh3 = _coarse(train_df["high_bin"].to_numpy(dtype=int))
    yl3 = _coarse(train_df["low_bin"].to_numpy(dtype=int))
    spec.update({
        "target_mode": TARGET_MODE,
        "coarse_bin_count": COARSE_BIN_COUNT,
        "coarse_bin_mapping": {"0": [0, 1], "1": [2, 3], "2": [4, 5]},
        "exact_high_coarse_bin": int(np.argmax(np.bincount(yh3, minlength=COARSE_BIN_COUNT))),
        "exact_low_coarse_bin": int(np.argmax(np.bincount(yl3, minlength=COARSE_BIN_COUNT))),
        "true_coarse3_model": True,
        "pattern_detector_model": True,
        "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
    })
    spec["exact_high_bin"] = spec["exact_high_coarse_bin"]
    spec["exact_low_bin"] = spec["exact_low_coarse_bin"]
    return spec


def clone_rule(rule):
    r = _ORIG_CLONE_RULE(rule)
    r.bin = _cb(getattr(r, "bin", 0))
    return r


def random_rule_for_target(target, rng, qspec):
    r = _ORIG_RANDOM_RULE(target, rng, qspec)
    r.bin = int(rng.randrange(COARSE_BIN_COUNT))
    return r


def repair_gene(rule, target, rng, qspec):
    r = _ORIG_REPAIR_GENE(rule, target, rng, qspec)
    r.bin = _cb(getattr(r, "bin", 0))
    return r


def repair_head_rules(rules, target, count, rng, qspec):
    out = [repair_gene(r, target, rng, qspec) for r in rules]
    while len(out) < count and qspec:
        out.append(random_rule_for_target(target, rng, qspec))
    return out[:count]


def mutate_rule(rule, rng, qspec, target):
    r = _ORIG_MUTATE_RULE(rule, rng, qspec, target)
    r.bin = _cb(getattr(r, "bin", 0))
    if rng.random() < 0.25:
        r.bin = int(rng.randrange(COARSE_BIN_COUNT))
    return r


def random_individual(rng, qspec, baseline_spec):
    ind = _ORIG_RANDOM_INDIVIDUAL(rng, qspec, baseline_spec)
    ind.high_rules = repair_head_rules(ind.high_rules, "HIGH", P.HIGH_RULE_COUNT, rng, qspec)
    ind.low_rules = repair_head_rules(ind.low_rules, "LOW", P.LOW_RULE_COUNT, rng, qspec)
    ind.default_high_bin = _cb(baseline_spec.get("exact_high_coarse_bin", 1))
    ind.default_low_bin = _cb(baseline_spec.get("exact_low_coarse_bin", 1))
    ind.baseline_spec = dict(baseline_spec)
    return ind


def mutate(ind, rng, qspec, baseline_spec=None):
    child = P.clone_individual(ind)
    child.fitness = -1e9
    child.metrics = None
    child.signature = None
    if baseline_spec is not None:
        child.baseline_spec = dict(baseline_spec)
        child.default_high_bin = _cb(baseline_spec.get("exact_high_coarse_bin", child.default_high_bin))
        child.default_low_bin = _cb(baseline_spec.get("exact_low_coarse_bin", child.default_low_bin))
    child.high_rules = repair_head_rules(child.high_rules, "HIGH", P.HIGH_RULE_COUNT, rng, qspec)
    child.low_rules = repair_head_rules(child.low_rules, "LOW", P.LOW_RULE_COUNT, rng, qspec)
    for i, rule in enumerate(child.high_rules):
        if rng.random() <= P.L.MUTATION_RATE:
            child.high_rules[i] = mutate_rule(rule, rng, qspec, "HIGH")
    for i, rule in enumerate(child.low_rules):
        if rng.random() <= P.L.MUTATION_RATE:
            child.low_rules[i] = mutate_rule(rule, rng, qspec, "LOW")
    return child


def crossover(a, b, rng, baseline_spec):
    child = _ORIG_CROSSOVER(a, b, rng, baseline_spec)
    child.high_rules = repair_head_rules(child.high_rules, "HIGH", P.HIGH_RULE_COUNT, rng, {})
    child.low_rules = repair_head_rules(child.low_rules, "LOW", P.LOW_RULE_COUNT, rng, {})
    child.default_high_bin = _cb(baseline_spec.get("exact_high_coarse_bin", child.default_high_bin))
    child.default_low_bin = _cb(baseline_spec.get("exact_low_coarse_bin", child.default_low_bin))
    child.baseline_spec = dict(baseline_spec)
    return child


def predictor_signature(ind):
    payload = json.dumps({
        "version": "true_coarse3_pattern_detector_v1",
        "default_high_bin": int(ind.default_high_bin),
        "default_low_bin": int(ind.default_low_bin),
        "high_rules": [P.rule_payload(clone_rule(r)) for r in ind.high_rules],
        "low_rules": [P.rule_payload(clone_rule(r)) for r in ind.low_rules],
        "signal": {
            "min_active_genes_per_head": SIGNAL_MIN_ACTIVE_GENES_PER_HEAD,
            "min_head_score": SIGNAL_MIN_HEAD_SCORE,
            "min_margin": SIGNAL_MIN_MARGIN,
        },
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def individual_to_dict(ind):
    d = _ORIG_INDIVIDUAL_TO_DICT(ind)
    d["type"] = "true_coarse3_pattern_detector"
    d["target_mode"] = TARGET_MODE
    d["coarse_bin_count"] = COARSE_BIN_COUNT
    d["signal_rule"] = {
        "min_active_genes_per_head": SIGNAL_MIN_ACTIVE_GENES_PER_HEAD,
        "min_head_score": SIGNAL_MIN_HEAD_SCORE,
        "min_margin": SIGNAL_MIN_MARGIN,
        "requires_both_heads": True,
    }
    d["signature"] = ind.signature or predictor_signature(ind)
    return d


def _top_margin(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pred = scores.argmax(axis=1)
    top = scores.max(axis=1)
    if scores.shape[1] <= 1:
        second = np.zeros_like(top)
    else:
        second = np.partition(scores, -2, axis=1)[:, -2]
    return pred, top, top - second


def predict_signal(ind, X, qspec):
    n = len(X)
    hs = np.zeros((n, COARSE_BIN_COUNT), dtype=float)
    ls = np.zeros((n, COARSE_BIN_COUNT), dtype=float)
    h_active = np.zeros(n, dtype=int)
    l_active = np.zeros(n, dtype=int)
    h_strength_sum = np.zeros(n, dtype=float)
    l_strength_sum = np.zeros(n, dtype=float)
    high_gene_count = low_gene_count = 0
    high_cond_count = low_cond_count = 0

    for raw in ind.high_rules:
        gene = clone_rule(raw)
        strength, _widths, cond_count = P.gene_strength(gene, X, qspec)
        if strength is None or not np.any(strength > 0):
            continue
        weighted = strength * float(gene.weight)
        active_mask = strength > 0
        hs[:, _cb(gene.bin)] += weighted
        h_active += active_mask.astype(int)
        h_strength_sum += strength
        high_gene_count += 1
        high_cond_count += cond_count

    for raw in ind.low_rules:
        gene = clone_rule(raw)
        strength, _widths, cond_count = P.gene_strength(gene, X, qspec)
        if strength is None or not np.any(strength > 0):
            continue
        weighted = strength * float(gene.weight)
        active_mask = strength > 0
        ls[:, _cb(gene.bin)] += weighted
        l_active += active_mask.astype(int)
        l_strength_sum += strength
        low_gene_count += 1
        low_cond_count += cond_count

    ph, h_top, h_margin = _top_margin(hs)
    pl, l_top, l_margin = _top_margin(ls)
    high_signal = (
        (h_active >= SIGNAL_MIN_ACTIVE_GENES_PER_HEAD)
        & (h_top >= SIGNAL_MIN_HEAD_SCORE)
        & (h_margin >= SIGNAL_MIN_MARGIN)
    )
    low_signal = (
        (l_active >= SIGNAL_MIN_ACTIVE_GENES_PER_HEAD)
        & (l_top >= SIGNAL_MIN_HEAD_SCORE)
        & (l_margin >= SIGNAL_MIN_MARGIN)
    )
    signal = high_signal & low_signal
    signal_count = int(np.sum(signal))
    period_count = int(n)

    diag = {
        "period_sample_count": period_count,
        "sample_count": signal_count,
        "signal_count": signal_count,
        "no_signal_count": int(period_count - signal_count),
        "signal_coverage_pct": float(signal_count / max(1, period_count) * 100.0),
        "high_signal_count": int(np.sum(high_signal)),
        "low_signal_count": int(np.sum(low_signal)),
        "high_signal_coverage_pct": float(np.mean(high_signal) * 100.0) if period_count else 0.0,
        "low_signal_coverage_pct": float(np.mean(low_signal) * 100.0) if period_count else 0.0,
        "active_rule_count": int(high_gene_count + low_gene_count),
        "active_gene_count": int(high_gene_count + low_gene_count),
        "active_condition_count": int(high_cond_count + low_cond_count),
        "high_active_rule_count": int(high_gene_count),
        "low_active_rule_count": int(low_gene_count),
        "high_active_gene_count": int(high_gene_count),
        "low_active_gene_count": int(low_gene_count),
        "high_active_condition_count": int(high_cond_count),
        "low_active_condition_count": int(low_cond_count),
        "signal_high_active_gene_mean": float(np.mean(h_active[signal])) if signal_count else 0.0,
        "signal_low_active_gene_mean": float(np.mean(l_active[signal])) if signal_count else 0.0,
        "signal_high_score_mean": float(np.mean(h_top[signal])) if signal_count else 0.0,
        "signal_low_score_mean": float(np.mean(l_top[signal])) if signal_count else 0.0,
        "signal_high_margin_mean": float(np.mean(h_margin[signal])) if signal_count else 0.0,
        "signal_low_margin_mean": float(np.mean(l_margin[signal])) if signal_count else 0.0,
        "avg_rule_match_strength": float((np.mean(h_strength_sum) + np.mean(l_strength_sum)) / 2.0) if period_count else 0.0,
        "avg_gene_match_strength": float((np.mean(h_strength_sum) + np.mean(l_strength_sum)) / 2.0) if period_count else 0.0,
        "high_avg_gene_match_strength": float(np.mean(h_strength_sum)) if period_count else 0.0,
        "low_avg_gene_match_strength": float(np.mean(l_strength_sum)) if period_count else 0.0,
        "avg_conditions_per_active_gene": float((high_cond_count + low_cond_count) / max(1, high_gene_count + low_gene_count)),
        "high_avg_conditions_per_active_gene": float(high_cond_count / max(1, high_gene_count)),
        "low_avg_conditions_per_active_gene": float(low_cond_count / max(1, low_gene_count)),
    }
    return ph, pl, signal, high_signal, low_signal, diag


def predict(ind, X, qspec):
    ph, pl, _signal, _hs, _ls, diag = predict_signal(ind, X, qspec)
    return ph, pl, diag


def _share(pred):
    c = np.bincount(np.asarray(pred, dtype=int), minlength=COARSE_BIN_COUNT)
    total = max(1, int(c.sum()))
    return [float(x / total * 100.0) for x in c]


def prediction_penalty(ind, yh3, yl3, ph3, pl3):
    hp, lp = _share(ph3), _share(pl3)
    h = max(0.0, max(hp) - 70.0)
    l = max(0.0, max(lp) - 70.0)
    total = h * 0.20 + l * 0.25
    return {
        "high_concentration_penalty": h * 0.20,
        "low_concentration_penalty": l * 0.25,
        "high_rare_bin_penalty": 0.0,
        "low_rare_bin_penalty": 0.0,
        "high_narrow_band_penalty": 0.0,
        "high_wide_band_penalty": 0.0,
        "low_narrow_band_penalty": 0.0,
        "low_wide_band_penalty": 0.0,
        "high_total_penalty": h * 0.20,
        "low_total_penalty": l * 0.25,
        "total_penalty": total,
        "max_pred_share_high_pct": max(hp) if hp else 0.0,
        "max_pred_share_low_pct": max(lp) if lp else 0.0,
        "pred_distribution_high_pct": hp,
        "pred_distribution_low_pct": lp,
    }


def coverage_penalty(m: Mapping[str, Any]) -> float:
    sc = _sf(m.get("signal_count"))
    cov = _sf(m.get("signal_coverage_pct"))
    penalty = max(0.0, MIN_SIGNAL_COUNT - sc) * 4.0
    penalty += max(0.0, MIN_SIGNAL_COVERAGE_PCT - cov) * 3.0
    penalty += max(0.0, cov - MAX_SIGNAL_COVERAGE_PCT) * 1.5
    penalty += abs(cov - TARGET_SIGNAL_COVERAGE_PCT) * 0.05
    return float(penalty)


def predictor_fitness(m: Mapping[str, Any]) -> float:
    high = _sf(m.get("high_coarse_lift_pp")) * HIGH_COARSE_EXACT_WEIGHT + _sf(m.get("high_no_danger_lift_pp")) * HIGH_NO_DANGER_WEIGHT + _sf(m.get("high_asymmetric_bin_error_lift")) * ASYMMETRIC_BIN_ERROR_WEIGHT + _sf(m.get("high_coarse_error_lift")) * COARSE_ERROR_WEIGHT
    low = _sf(m.get("low_coarse_lift_pp")) * LOW_COARSE_EXACT_WEIGHT + _sf(m.get("low_no_danger_lift_pp")) * LOW_NO_DANGER_WEIGHT + _sf(m.get("low_asymmetric_bin_error_lift")) * ASYMMETRIC_BIN_ERROR_WEIGHT + _sf(m.get("low_coarse_error_lift")) * COARSE_ERROR_WEIGHT
    both = _sf(m.get("both_coarse_lift_pp")) * BOTH_COARSE_EXACT_WEIGHT + _sf(m.get("combined_coarse_lift_pp")) * COMBINED_COARSE_EXACT_WEIGHT + _sf(m.get("both_no_danger_lift_pp")) * BOTH_NO_DANGER_WEIGHT + _sf(m.get("combined_asymmetric_bin_error_lift")) * ASYMMETRIC_BIN_ERROR_WEIGHT + _sf(m.get("combined_coarse_error_lift")) * COARSE_ERROR_WEIGHT
    return float(high + low + both - abs(high - low) * HEAD_IMBALANCE_PENALTY - _sf(m.get("total_penalty")) - coverage_penalty(m))


def evaluate_predictor(ind, df, features, qspec):
    yh3_all = _coarse(df["high_bin"].to_numpy(dtype=int))
    yl3_all = _coarse(df["low_bin"].to_numpy(dtype=int))
    ph3_all, pl3_all, signal, _high_signal, _low_signal, diag = predict_signal(ind, df[features], qspec)
    if int(np.sum(signal)) > 0:
        yh3 = yh3_all[signal]
        yl3 = yl3_all[signal]
        ph3 = ph3_all[signal]
        pl3 = pl3_all[signal]
        bph = np.full(len(yh3), _cb(ind.baseline_spec.get("exact_high_coarse_bin", ind.default_high_bin)), dtype=int)
        bpl = np.full(len(yl3), _cb(ind.baseline_spec.get("exact_low_coarse_bin", ind.default_low_bin)), dtype=int)
    else:
        yh3 = yl3 = ph3 = pl3 = bph = bpl = _empty_int()
    m = true3_bin_metrics(yh3, yl3, ph3, pl3, bph, bpl)
    m.update({
        "target_mode": TARGET_MODE,
        "period_sample_count": int(len(df)),
        "sample_count": int(np.sum(signal)),
        "signal_count": int(np.sum(signal)),
        "high_rule_count": len(ind.high_rules),
        "low_rule_count": len(ind.low_rules),
        "coarse_bin_count": COARSE_BIN_COUNT,
        **prediction_penalty(ind, yh3, yl3, ph3, pl3),
        **diag,
    })
    m["high_component_score"] = _sf(m.get("high_coarse_lift_pp")) * HIGH_COARSE_EXACT_WEIGHT
    m["low_component_score"] = _sf(m.get("low_coarse_lift_pp")) * LOW_COARSE_EXACT_WEIGHT
    m["both_component_score"] = _sf(m.get("both_coarse_lift_pp")) * BOTH_COARSE_EXACT_WEIGHT
    m["coverage_penalty"] = coverage_penalty(m)
    m["fitness"] = predictor_fitness(m)
    return m


def dual_fail_reasons(m, kind):
    checks = [
        ("signal_count", _si(m.get("signal_count")), MIN_SIGNAL_COUNT, ">="),
        ("signal_coverage_pct", _sf(m.get("signal_coverage_pct")), MIN_SIGNAL_COVERAGE_PCT, ">="),
        ("signal_coverage_pct", _sf(m.get("signal_coverage_pct")), MAX_SIGNAL_COVERAGE_PCT, "<="),
        ("high_coarse_acc_pct", _sf(m.get("high_coarse_acc_pct")), MIN_HIGH_COARSE_ACC, ">="),
        ("low_coarse_acc_pct", _sf(m.get("low_coarse_acc_pct")), MIN_LOW_COARSE_ACC, ">="),
        ("both_coarse_acc_pct", _sf(m.get("both_coarse_acc_pct")), MIN_BOTH_COARSE_ACC, ">="),
        ("combined_coarse_acc_pct", _sf(m.get("combined_coarse_acc_pct")), MIN_COMBINED_COARSE_ACC, ">="),
        ("high_coarse_lift_pp", _sf(m.get("high_coarse_lift_pp")), MIN_HIGH_COARSE_LIFT, ">="),
        ("low_coarse_lift_pp", _sf(m.get("low_coarse_lift_pp")), MIN_LOW_COARSE_LIFT, ">="),
        ("both_coarse_lift_pp", _sf(m.get("both_coarse_lift_pp")), MIN_BOTH_COARSE_LIFT, ">="),
        ("high_no_danger_acc_pct", _sf(m.get("high_no_danger_acc_pct")), MIN_HIGH_NO_DANGER, ">="),
        ("low_no_danger_acc_pct", _sf(m.get("low_no_danger_acc_pct")), MIN_LOW_NO_DANGER, ">="),
        ("both_no_danger_acc_pct", _sf(m.get("both_no_danger_acc_pct")), MIN_BOTH_NO_DANGER, ">="),
        ("high_dangerous_bin_error_mean", _sf(m.get("high_dangerous_bin_error_mean")), MAX_HIGH_DANGEROUS_BIN_ERROR, "<="),
        ("low_dangerous_bin_error_mean", _sf(m.get("low_dangerous_bin_error_mean")), MAX_LOW_DANGEROUS_BIN_ERROR, "<="),
        ("high_safe_bin_error_mean", _sf(m.get("high_safe_bin_error_mean")), MAX_HIGH_SAFE_BIN_ERROR, "<="),
        ("low_safe_bin_error_mean", _sf(m.get("low_safe_bin_error_mean")), MAX_LOW_SAFE_BIN_ERROR, "<="),
        ("total_penalty", _sf(m.get("total_penalty")), MAX_TOTAL_PENALTY_TRUE3, "<="),
        ("max_pred_share_high_pct", _sf(m.get("max_pred_share_high_pct")), MAX_HIGH_PRED_SHARE_TRUE3, "<="),
        ("max_pred_share_low_pct", _sf(m.get("max_pred_share_low_pct")), MAX_LOW_PRED_SHARE_TRUE3, "<="),
    ]
    out = []
    for metric, value, threshold, rule in checks:
        if (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold):
            out.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return out


def dual_head_params():
    return {
        "mode": TARGET_MODE,
        "coarse_bin_count": COARSE_BIN_COUNT,
        "coarse_bin_mapping": {"0": [0, 1], "1": [2, 3], "2": [4, 5]},
        "pattern_detector": True,
        "no_adjacent_success": True,
        "no_signal_is_not_prediction": True,
        "high_rule_count": P.HIGH_RULE_COUNT,
        "low_rule_count": P.LOW_RULE_COUNT,
        "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
        "signal": {
            "min_active_genes_per_head": SIGNAL_MIN_ACTIVE_GENES_PER_HEAD,
            "min_head_score": SIGNAL_MIN_HEAD_SCORE,
            "min_margin": SIGNAL_MIN_MARGIN,
            "requires_both_heads": True,
            "min_signal_count": MIN_SIGNAL_COUNT,
            "min_signal_coverage_pct": MIN_SIGNAL_COVERAGE_PCT,
            "max_signal_coverage_pct": MAX_SIGNAL_COVERAGE_PCT,
            "target_signal_coverage_pct": TARGET_SIGNAL_COVERAGE_PCT,
        },
        "gate": {
            "min_signal_count": MIN_SIGNAL_COUNT,
            "min_signal_coverage_pct": MIN_SIGNAL_COVERAGE_PCT,
            "max_signal_coverage_pct": MAX_SIGNAL_COVERAGE_PCT,
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


def _parse_wrapper_args(argv):
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--safe-overflow-free-bins", type=float, default=SAFE_OVERFLOW_FREE_BINS)
    p.add_argument("--true3-high-dangerous-bin-error-weight", type=float, default=HIGH_DANGEROUS_BIN_ERROR_WEIGHT)
    p.add_argument("--true3-high-safe-bin-error-weight", type=float, default=HIGH_SAFE_BIN_ERROR_WEIGHT)
    p.add_argument("--true3-low-dangerous-bin-error-weight", type=float, default=LOW_DANGEROUS_BIN_ERROR_WEIGHT)
    p.add_argument("--true3-low-safe-bin-error-weight", type=float, default=LOW_SAFE_BIN_ERROR_WEIGHT)
    p.add_argument("--true3-asymmetric-bin-error-weight", type=float, default=ASYMMETRIC_BIN_ERROR_WEIGHT)
    p.add_argument("--signal-min-active-genes-per-head", type=int, default=SIGNAL_MIN_ACTIVE_GENES_PER_HEAD)
    p.add_argument("--signal-min-head-score", type=float, default=SIGNAL_MIN_HEAD_SCORE)
    p.add_argument("--signal-min-margin", type=float, default=SIGNAL_MIN_MARGIN)
    p.add_argument("--min-signal-count", type=int, default=MIN_SIGNAL_COUNT)
    p.add_argument("--min-signal-coverage-pct", type=float, default=MIN_SIGNAL_COVERAGE_PCT)
    p.add_argument("--max-signal-coverage-pct", type=float, default=MAX_SIGNAL_COVERAGE_PCT)
    p.add_argument("--target-signal-coverage-pct", type=float, default=TARGET_SIGNAL_COVERAGE_PCT)
    for name, default in [
        ("min-high-coarse-acc", MIN_HIGH_COARSE_ACC), ("min-low-coarse-acc", MIN_LOW_COARSE_ACC),
        ("min-both-coarse-acc", MIN_BOTH_COARSE_ACC), ("min-combined-coarse-acc", MIN_COMBINED_COARSE_ACC),
        ("min-high-coarse-lift", MIN_HIGH_COARSE_LIFT), ("min-low-coarse-lift", MIN_LOW_COARSE_LIFT),
        ("min-both-coarse-lift", MIN_BOTH_COARSE_LIFT), ("min-high-no-danger", MIN_HIGH_NO_DANGER),
        ("min-low-no-danger", MIN_LOW_NO_DANGER), ("min-both-no-danger", MIN_BOTH_NO_DANGER),
        ("max-high-dangerous-bin-error", MAX_HIGH_DANGEROUS_BIN_ERROR), ("max-low-dangerous-bin-error", MAX_LOW_DANGEROUS_BIN_ERROR),
        ("max-high-safe-bin-error", MAX_HIGH_SAFE_BIN_ERROR), ("max-low-safe-bin-error", MAX_LOW_SAFE_BIN_ERROR),
        ("true3-max-total-penalty", MAX_TOTAL_PENALTY_TRUE3), ("true3-max-high-pred-share", MAX_HIGH_PRED_SHARE_TRUE3),
        ("true3-max-low-pred-share", MAX_LOW_PRED_SHARE_TRUE3),
    ]:
        p.add_argument("--" + name, type=float, default=default)
    return p.parse_known_args(argv)


def _apply_wrapper_args(a):
    global SAFE_OVERFLOW_FREE_BINS, HIGH_DANGEROUS_BIN_ERROR_WEIGHT, HIGH_SAFE_BIN_ERROR_WEIGHT
    global LOW_DANGEROUS_BIN_ERROR_WEIGHT, LOW_SAFE_BIN_ERROR_WEIGHT, ASYMMETRIC_BIN_ERROR_WEIGHT
    global SIGNAL_MIN_ACTIVE_GENES_PER_HEAD, SIGNAL_MIN_HEAD_SCORE, SIGNAL_MIN_MARGIN
    global MIN_SIGNAL_COUNT, MIN_SIGNAL_COVERAGE_PCT, MAX_SIGNAL_COVERAGE_PCT, TARGET_SIGNAL_COVERAGE_PCT
    global MIN_HIGH_COARSE_ACC, MIN_LOW_COARSE_ACC, MIN_BOTH_COARSE_ACC, MIN_COMBINED_COARSE_ACC
    global MIN_HIGH_COARSE_LIFT, MIN_LOW_COARSE_LIFT, MIN_BOTH_COARSE_LIFT
    global MIN_HIGH_NO_DANGER, MIN_LOW_NO_DANGER, MIN_BOTH_NO_DANGER
    global MAX_HIGH_DANGEROUS_BIN_ERROR, MAX_LOW_DANGEROUS_BIN_ERROR, MAX_HIGH_SAFE_BIN_ERROR, MAX_LOW_SAFE_BIN_ERROR
    global MAX_TOTAL_PENALTY_TRUE3, MAX_HIGH_PRED_SHARE_TRUE3, MAX_LOW_PRED_SHARE_TRUE3
    SAFE_OVERFLOW_FREE_BINS = max(0.0, float(a.safe_overflow_free_bins))
    HIGH_DANGEROUS_BIN_ERROR_WEIGHT = float(a.true3_high_dangerous_bin_error_weight)
    HIGH_SAFE_BIN_ERROR_WEIGHT = float(a.true3_high_safe_bin_error_weight)
    LOW_DANGEROUS_BIN_ERROR_WEIGHT = float(a.true3_low_dangerous_bin_error_weight)
    LOW_SAFE_BIN_ERROR_WEIGHT = float(a.true3_low_safe_bin_error_weight)
    ASYMMETRIC_BIN_ERROR_WEIGHT = float(a.true3_asymmetric_bin_error_weight)
    SIGNAL_MIN_ACTIVE_GENES_PER_HEAD = int(a.signal_min_active_genes_per_head)
    SIGNAL_MIN_HEAD_SCORE = float(a.signal_min_head_score)
    SIGNAL_MIN_MARGIN = float(a.signal_min_margin)
    MIN_SIGNAL_COUNT = int(a.min_signal_count)
    MIN_SIGNAL_COVERAGE_PCT = float(a.min_signal_coverage_pct)
    MAX_SIGNAL_COVERAGE_PCT = float(a.max_signal_coverage_pct)
    TARGET_SIGNAL_COVERAGE_PCT = float(a.target_signal_coverage_pct)
    MIN_HIGH_COARSE_ACC = float(a.min_high_coarse_acc)
    MIN_LOW_COARSE_ACC = float(a.min_low_coarse_acc)
    MIN_BOTH_COARSE_ACC = float(a.min_both_coarse_acc)
    MIN_COMBINED_COARSE_ACC = float(a.min_combined_coarse_acc)
    MIN_HIGH_COARSE_LIFT = float(a.min_high_coarse_lift)
    MIN_LOW_COARSE_LIFT = float(a.min_low_coarse_lift)
    MIN_BOTH_COARSE_LIFT = float(a.min_both_coarse_lift)
    MIN_HIGH_NO_DANGER = float(a.min_high_no_danger)
    MIN_LOW_NO_DANGER = float(a.min_low_no_danger)
    MIN_BOTH_NO_DANGER = float(a.min_both_no_danger)
    MAX_HIGH_DANGEROUS_BIN_ERROR = float(a.max_high_dangerous_bin_error)
    MAX_LOW_DANGEROUS_BIN_ERROR = float(a.max_low_dangerous_bin_error)
    MAX_HIGH_SAFE_BIN_ERROR = float(a.max_high_safe_bin_error)
    MAX_LOW_SAFE_BIN_ERROR = float(a.max_low_safe_bin_error)
    MAX_TOTAL_PENALTY_TRUE3 = float(a.true3_max_total_penalty)
    MAX_HIGH_PRED_SHARE_TRUE3 = float(a.true3_max_high_pred_share)
    MAX_LOW_PRED_SHARE_TRUE3 = float(a.true3_max_low_pred_share)


def install_dual_head_target(args):
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    _ORIG_INSTALL(args)
    P.TARGET_MODE = TARGET_MODE
    P.BIN_TOLERANCE = 0
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


def parse_args(argv=None):
    wa, rem = _parse_wrapper_args(sys.argv[1:] if argv is None else argv)
    _apply_wrapper_args(wa)
    args = _ORIG_PARSE_ARGS(rem)
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return args


def run_original_stage2_predictor(ticker, out_dir, seed_base, args):
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    P.install_dual_head_target = install_dual_head_target
    P.dual_head_params = dual_head_params
    return _ORIG_RUN(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


P.install_dual_head_target = install_dual_head_target
P.parse_args = parse_args
P.run_original_stage2_predictor = run_original_stage2_predictor
P.dual_head_params = dual_head_params
for _name in dir(P):
    if not _name.startswith("__") and _name not in globals():
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
    "predict_signal": predict_signal,
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


def default_seed_base(ticker):
    return int(P.default_seed_base(ticker)) if hasattr(P, "default_seed_base") else int(P.L.default_seed_base(ticker))


def main(argv=None) -> int:
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
