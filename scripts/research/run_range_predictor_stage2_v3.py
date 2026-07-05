#!/usr/bin/env python3
"""
Original-Stage2-style multi-condition dual-head GA runner for next-day HIGH/LOW bin prediction.

이번 파일은 검증 커밋(07c0bcf)의 멀티컨디션 dual-head GA를 로드한 뒤,
실전 방향 비대칭 bin 오차와 3단계 coarse bin 정확도 정책을 덧씌운다.

핵심 규칙:
- 기존 6-bin 예측은 유지한다. 진단/세부 판단에 필요하기 때문이다.
- 학습 fitness와 Stage2 gate에는 3-bin coarse 정확도를 추가한다.
  6-bin 0~1 -> coarse 0 / 6-bin 2~3 -> coarse 1 / 6-bin 4~5 -> coarse 2.
- HIGH/LOW를 6개 세부 구간으로 바로 맞히기보다, 먼저 작음/중간/큼 3단계 큰 구간을 맞히는 쪽으로 GA를 유도한다.

위험/안전 오차 규칙:
- HIGH: 예측 bin이 실제 high bin보다 높으면 위험 오차다.
  예: +2 예상, 실제 +1만 감 => 위험 오차/벌점.
  예: +2 예상, 실제 +3 감 => 안전 방향 오차.
- LOW: 실제 low-magnitude bin이 예측 bin보다 높으면 위험 오차다.
  예: -2 예상, 실제 -3까지 빠짐 => 위험 오차/벌점.
  예: -2 예상, 실제 -1만 빠짐 => 안전 방향 오차.
- 위험 방향 오차는 1칸도 허용하지 않는다.
- 안전 방향 오차는 기본 1칸까지 무료다.
- 안전 방향 2칸 이상 과한 오차만 약한 비용을 준다.

Stage2 흐름은 유지한다:
train_1 독립 GA 100개 + train_2 독립 GA 100개 + train_3 독립 GA 100개
-> stress_pre_2022h1 -> train_3_eval -> train_2_eval -> train_1_eval -> oos_2025h2
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREV_COMMIT = "07c0bcf"
SELF_PATH = "scripts/research/run_range_predictor_stage2_v3.py"
TARGET_MODE = "next_day_hilo_multicond_dual_head_original_stage2_coarse3_asymmetric_stage2_gate"

SAFE_OVERFLOW_FREE_BINS = 1.0
PROTECTED_NO_DANGER_THRESHOLD = 60.0
LOW_PROTECTED_ADJACENT_WEIGHT = 0.45
LOW_PROTECTED_EXACT_WEIGHT = 0.20
BOTH_PROTECTED_ADJACENT_WEIGHT = 0.15
LOW_MAX_BIN_SHARE_SOFT = 55.0
LOW_OVERCONCENTRATION_EXTRA_PENALTY = 0.15

# 3-bin coarse 정확도 보상. 6-bin 정확도보다 이쪽이 우선이다.
HIGH_COARSE_LIFT_WEIGHT = 0.35
LOW_COARSE_LIFT_WEIGHT = 0.75
BOTH_COARSE_LIFT_WEIGHT = 0.85
COMBINED_COARSE_LIFT_WEIGHT = 0.35
COARSE_SEVERE_PENALTY_WEIGHT = 0.20

MIN_HIGH_NO_DANGER = -999.0
MIN_LOW_NO_DANGER = -999.0
MIN_BOTH_NO_DANGER = -999.0
MIN_COMBINED_NO_DANGER = -999.0
MAX_LOW_DANGEROUS_BIN_ERROR = 999.0
MAX_LOW_SAFE_OVERFLOW_BIN_ERROR = 999.0

MIN_HIGH_COARSE_ACC = -999.0
MIN_LOW_COARSE_ACC = -999.0
MIN_BOTH_COARSE_ACC = -999.0
MIN_COMBINED_COARSE_ACC = -999.0
MIN_HIGH_COARSE_LIFT = -999.0
MIN_LOW_COARSE_LIFT = -999.0
MIN_BOTH_COARSE_LIFT = -999.0
MAX_HIGH_COARSE_SEVERE_PCT = 999.0
MAX_LOW_COARSE_SEVERE_PCT = 999.0
MAX_BOTH_COARSE_SEVERE_PCT = 999.0


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


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _coarse(arr: np.ndarray) -> np.ndarray:
    # 6-bin -> 3-bin: 0/1, 2/3, 4/5
    return np.clip(arr.astype(int) // 2, 0, 2)


def coarse_bin_metrics(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, float]:
    chy = _coarse(yh)
    cly = _coarse(yl)
    chp = _coarse(ph)
    clp = _coarse(pl)
    n = max(1, int(len(chy)))

    high_hit = chp == chy
    low_hit = clp == cly
    both_hit = high_hit & low_hit
    high_err = np.abs(chp - chy).astype(float)
    low_err = np.abs(clp - cly).astype(float)
    high_severe = high_err >= 2
    low_severe = low_err >= 2
    both_severe = high_severe | low_severe

    # coarse 위험 방향도 같이 남긴다. gate/해석용이다.
    high_danger = np.maximum(0, chp - chy).astype(float)
    low_danger = np.maximum(0, cly - clp).astype(float)

    return {
        "high_coarse_acc_pct": float(np.mean(high_hit) * 100.0) if n else 0.0,
        "low_coarse_acc_pct": float(np.mean(low_hit) * 100.0) if n else 0.0,
        "both_coarse_acc_pct": float(np.mean(both_hit) * 100.0) if n else 0.0,
        "combined_coarse_acc_pct": float((np.mean(high_hit) + np.mean(low_hit)) / 2.0 * 100.0) if n else 0.0,
        "high_coarse_error_mean": float(np.mean(high_err)) if n else 0.0,
        "low_coarse_error_mean": float(np.mean(low_err)) if n else 0.0,
        "combined_coarse_error_mean": float((np.mean(high_err) + np.mean(low_err)) / 2.0) if n else 0.0,
        "high_coarse_severe_pct": float(np.mean(high_severe) * 100.0) if n else 0.0,
        "low_coarse_severe_pct": float(np.mean(low_severe) * 100.0) if n else 0.0,
        "both_coarse_severe_pct": float(np.mean(both_severe) * 100.0) if n else 0.0,
        "high_coarse_dangerous_error_mean": float(np.mean(high_danger)) if n else 0.0,
        "low_coarse_dangerous_error_mean": float(np.mean(low_danger)) if n else 0.0,
    }


def asymmetric_bin_metrics(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, float]:
    """위험 방향은 즉시 벌점, 안전 방향은 1칸 무료 후 초과분만 비용 처리."""
    yh = yh.astype(int)
    yl = yl.astype(int)
    ph = ph.astype(int)
    pl = pl.astype(int)
    n = max(1, int(len(yh)))

    high_danger = np.maximum(0, ph - yh).astype(float)
    high_safe = np.maximum(0, yh - ph).astype(float)
    low_danger = np.maximum(0, yl - pl).astype(float)
    low_safe = np.maximum(0, pl - yl).astype(float)

    high_safe_overflow = np.maximum(0.0, high_safe - SAFE_OVERFLOW_FREE_BINS)
    low_safe_overflow = np.maximum(0.0, low_safe - SAFE_OVERFLOW_FREE_BINS)

    high_asym = high_danger * P.HIGH_DANGEROUS_BIN_ERROR_WEIGHT + high_safe_overflow * P.HIGH_SAFE_BIN_ERROR_WEIGHT
    low_asym = low_danger * P.LOW_DANGEROUS_BIN_ERROR_WEIGHT + low_safe_overflow * P.LOW_SAFE_BIN_ERROR_WEIGHT

    high_no_danger = high_danger == 0
    low_no_danger = low_danger == 0
    both_no_danger = high_no_danger & low_no_danger

    high_no_danger_pct = float(np.mean(high_no_danger) * 100.0) if n else 0.0
    low_no_danger_pct = float(np.mean(low_no_danger) * 100.0) if n else 0.0
    both_no_danger_pct = float(np.mean(both_no_danger) * 100.0) if n else 0.0
    combined_no_danger_pct = float((np.mean(high_no_danger) + np.mean(low_no_danger)) / 2.0 * 100.0) if n else 0.0

    out = {
        "bin_tolerance": 0.0,
        "safe_overflow_free_bins": float(SAFE_OVERFLOW_FREE_BINS),
        "high_dangerous_bin_error_mean": float(np.mean(high_danger)) if n else 0.0,
        "low_dangerous_bin_error_mean": float(np.mean(low_danger)) if n else 0.0,
        "combined_dangerous_bin_error_mean": float((np.mean(high_danger) + np.mean(low_danger)) / 2.0) if n else 0.0,
        "high_safe_bin_error_mean": float(np.mean(high_safe)) if n else 0.0,
        "low_safe_bin_error_mean": float(np.mean(low_safe)) if n else 0.0,
        "combined_safe_bin_error_mean": float((np.mean(high_safe) + np.mean(low_safe)) / 2.0) if n else 0.0,
        "high_safe_overflow_bin_error_mean": float(np.mean(high_safe_overflow)) if n else 0.0,
        "low_safe_overflow_bin_error_mean": float(np.mean(low_safe_overflow)) if n else 0.0,
        "combined_safe_overflow_bin_error_mean": float((np.mean(high_safe_overflow) + np.mean(low_safe_overflow)) / 2.0) if n else 0.0,
        "high_asymmetric_bin_error_mean": float(np.mean(high_asym)) if n else 0.0,
        "low_asymmetric_bin_error_mean": float(np.mean(low_asym)) if n else 0.0,
        "combined_asymmetric_bin_error_mean": float((np.mean(high_asym) + np.mean(low_asym)) / 2.0) if n else 0.0,
        "high_directional_tolerant_acc_pct": high_no_danger_pct,
        "low_directional_tolerant_acc_pct": low_no_danger_pct,
        "both_directional_tolerant_acc_pct": both_no_danger_pct,
        "combined_directional_tolerant_acc_pct": combined_no_danger_pct,
        "high_no_danger_acc_pct": high_no_danger_pct,
        "low_no_danger_acc_pct": low_no_danger_pct,
        "both_no_danger_acc_pct": both_no_danger_pct,
        "combined_no_danger_acc_pct": combined_no_danger_pct,
        "high_safe_or_exact_acc_pct": high_no_danger_pct,
        "low_safe_or_exact_acc_pct": low_no_danger_pct,
        "both_safe_or_exact_acc_pct": both_no_danger_pct,
        "combined_safe_or_exact_acc_pct": combined_no_danger_pct,
    }
    out.update(coarse_bin_metrics(yh, yl, ph, pl))
    return out


_orig_install_dual_head_target = P.install_dual_head_target
_orig_parse_args = P.parse_args
_orig_dual_head_params = P.dual_head_params
_orig_make_dual_baseline_spec = P.make_dual_baseline_spec
_orig_run_original_stage2_predictor = P.run_original_stage2_predictor
_orig_predictor_fitness = P.predictor_fitness
_orig_dual_fail_reasons = P.dual_fail_reasons


def score_hilo_predictions(df, ph: np.ndarray, pl: np.ndarray, spec: Mapping[str, Any]) -> dict[str, float]:
    scores = P.L.score_predictions(df, ph, pl, spec)
    yh = df["high_bin"].to_numpy(dtype=int)
    yl = df["low_bin"].to_numpy(dtype=int)
    asym = asymmetric_bin_metrics(yh, yl, ph, pl)

    base_ph = np.full(len(df), P.safe_int(spec.get("exact_high_bin")), dtype=int)
    base_pl = np.full(len(df), P.safe_int(spec.get("exact_low_bin")), dtype=int)
    coarse_base = coarse_bin_metrics(yh, yl, base_ph, base_pl)

    scores["head_exact_gap_abs_pp"] = abs(_safe_float(scores.get("high_exact_acc_pct")) - _safe_float(scores.get("low_exact_acc_pct")))
    scores["head_adjacent_gap_abs_pp"] = abs(_safe_float(scores.get("high_adjacent_acc_pct")) - _safe_float(scores.get("low_adjacent_acc_pct")))
    scores["combined_mae_sum_pct"] = _safe_float(scores.get("high_mae_pct")) + _safe_float(scores.get("low_mae_pct"))
    scores.update(asym)
    for key in ["high_coarse_acc_pct", "low_coarse_acc_pct", "both_coarse_acc_pct", "combined_coarse_acc_pct"]:
        scores[key.replace("_acc_pct", "_lift_pp")] = _safe_float(scores.get(key)) - _safe_float(coarse_base.get(key))
        scores["baseline_" + key] = _safe_float(coarse_base.get(key))
    for key in ["high_coarse_error_mean", "low_coarse_error_mean", "combined_coarse_error_mean", "high_coarse_severe_pct", "low_coarse_severe_pct", "both_coarse_severe_pct"]:
        scores["baseline_" + key] = _safe_float(coarse_base.get(key))
    return scores


def protected_predictor_fitness(metrics: Mapping[str, Any]) -> float:
    base = float(_orig_predictor_fitness(metrics))
    low_no_danger = _safe_float(metrics.get("low_no_danger_acc_pct", metrics.get("low_safe_or_exact_acc_pct")))
    both_no_danger = _safe_float(metrics.get("both_no_danger_acc_pct", metrics.get("both_safe_or_exact_acc_pct")))
    low_adj = _safe_float(metrics.get("low_adjacent_acc_pct"))
    low_exact = _safe_float(metrics.get("low_exact_acc_pct"))
    both_adj = _safe_float(metrics.get("both_adjacent_acc_pct"))
    low_max_share = _safe_float(metrics.get("max_pred_share_low_pct"))

    high_coarse_lift = _safe_float(metrics.get("high_coarse_lift_pp"))
    low_coarse_lift = _safe_float(metrics.get("low_coarse_lift_pp"))
    both_coarse_lift = _safe_float(metrics.get("both_coarse_lift_pp"))
    combined_coarse_lift = _safe_float(metrics.get("combined_coarse_lift_pp"))
    high_coarse_severe = _safe_float(metrics.get("high_coarse_severe_pct"))
    low_coarse_severe = _safe_float(metrics.get("low_coarse_severe_pct"))
    both_coarse_severe = _safe_float(metrics.get("both_coarse_severe_pct"))

    low_shield = _clamp((low_no_danger - PROTECTED_NO_DANGER_THRESHOLD) / max(1.0, 100.0 - PROTECTED_NO_DANGER_THRESHOLD), 0.0, 1.0)
    both_shield = _clamp((both_no_danger - PROTECTED_NO_DANGER_THRESHOLD) / max(1.0, 100.0 - PROTECTED_NO_DANGER_THRESHOLD), 0.0, 1.0)

    low_protected_adjacent_bonus = low_shield * max(0.0, low_adj - 50.0) * LOW_PROTECTED_ADJACENT_WEIGHT
    low_protected_exact_bonus = low_shield * max(0.0, low_exact - 18.0) * LOW_PROTECTED_EXACT_WEIGHT
    both_protected_adjacent_bonus = both_shield * max(0.0, both_adj - 30.0) * BOTH_PROTECTED_ADJACENT_WEIGHT
    low_overconcentration_extra_penalty = max(0.0, low_max_share - LOW_MAX_BIN_SHARE_SOFT) * LOW_OVERCONCENTRATION_EXTRA_PENALTY

    coarse_lift_bonus = (
        high_coarse_lift * HIGH_COARSE_LIFT_WEIGHT
        + low_coarse_lift * LOW_COARSE_LIFT_WEIGHT
        + both_coarse_lift * BOTH_COARSE_LIFT_WEIGHT
        + combined_coarse_lift * COMBINED_COARSE_LIFT_WEIGHT
    )
    coarse_severe_penalty = (high_coarse_severe * 0.35 + low_coarse_severe * 0.45 + both_coarse_severe * 0.20) * COARSE_SEVERE_PENALTY_WEIGHT

    fitness = (
        base
        + low_protected_adjacent_bonus
        + low_protected_exact_bonus
        + both_protected_adjacent_bonus
        + coarse_lift_bonus
        - low_overconcentration_extra_penalty
        - coarse_severe_penalty
    )

    if isinstance(metrics, dict):
        metrics["protected_no_danger_threshold"] = PROTECTED_NO_DANGER_THRESHOLD
        metrics["low_protected_adjacent_bonus"] = low_protected_adjacent_bonus
        metrics["low_protected_exact_bonus"] = low_protected_exact_bonus
        metrics["both_protected_adjacent_bonus"] = both_protected_adjacent_bonus
        metrics["low_overconcentration_extra_penalty"] = low_overconcentration_extra_penalty
        metrics["coarse_lift_bonus"] = coarse_lift_bonus
        metrics["coarse_severe_penalty"] = coarse_severe_penalty
        metrics["protected_precision_fitness_adjustment"] = fitness - base
        metrics["protected_precision_base_fitness"] = base
    return float(fitness)


def dual_fail_reasons(metrics: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    reasons = list(_orig_dual_fail_reasons(metrics, kind))
    checks = [
        ("high_no_danger_acc_pct", _safe_float(metrics.get("high_no_danger_acc_pct", metrics.get("high_safe_or_exact_acc_pct"))), MIN_HIGH_NO_DANGER, ">="),
        ("low_no_danger_acc_pct", _safe_float(metrics.get("low_no_danger_acc_pct", metrics.get("low_safe_or_exact_acc_pct"))), MIN_LOW_NO_DANGER, ">="),
        ("both_no_danger_acc_pct", _safe_float(metrics.get("both_no_danger_acc_pct", metrics.get("both_safe_or_exact_acc_pct"))), MIN_BOTH_NO_DANGER, ">="),
        ("combined_no_danger_acc_pct", _safe_float(metrics.get("combined_no_danger_acc_pct", metrics.get("combined_safe_or_exact_acc_pct"))), MIN_COMBINED_NO_DANGER, ">="),
        ("low_dangerous_bin_error_mean", _safe_float(metrics.get("low_dangerous_bin_error_mean")), MAX_LOW_DANGEROUS_BIN_ERROR, "<="),
        ("low_safe_overflow_bin_error_mean", _safe_float(metrics.get("low_safe_overflow_bin_error_mean")), MAX_LOW_SAFE_OVERFLOW_BIN_ERROR, "<="),
        ("high_coarse_acc_pct", _safe_float(metrics.get("high_coarse_acc_pct")), MIN_HIGH_COARSE_ACC, ">="),
        ("low_coarse_acc_pct", _safe_float(metrics.get("low_coarse_acc_pct")), MIN_LOW_COARSE_ACC, ">="),
        ("both_coarse_acc_pct", _safe_float(metrics.get("both_coarse_acc_pct")), MIN_BOTH_COARSE_ACC, ">="),
        ("combined_coarse_acc_pct", _safe_float(metrics.get("combined_coarse_acc_pct")), MIN_COMBINED_COARSE_ACC, ">="),
        ("high_coarse_lift_pp", _safe_float(metrics.get("high_coarse_lift_pp")), MIN_HIGH_COARSE_LIFT, ">="),
        ("low_coarse_lift_pp", _safe_float(metrics.get("low_coarse_lift_pp")), MIN_LOW_COARSE_LIFT, ">="),
        ("both_coarse_lift_pp", _safe_float(metrics.get("both_coarse_lift_pp")), MIN_BOTH_COARSE_LIFT, ">="),
        ("high_coarse_severe_pct", _safe_float(metrics.get("high_coarse_severe_pct")), MAX_HIGH_COARSE_SEVERE_PCT, "<="),
        ("low_coarse_severe_pct", _safe_float(metrics.get("low_coarse_severe_pct")), MAX_LOW_COARSE_SEVERE_PCT, "<="),
        ("both_coarse_severe_pct", _safe_float(metrics.get("both_coarse_severe_pct")), MAX_BOTH_COARSE_SEVERE_PCT, "<="),
    ]
    for metric, value, threshold, rule in checks:
        failed = (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold)
        if failed:
            reasons.append({"metric": metric, "value": value, "threshold": threshold, "rule": rule})
    return reasons


def _parse_wrapper_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--safe-overflow-free-bins", type=float, default=SAFE_OVERFLOW_FREE_BINS)
    parser.add_argument("--protected-no-danger-threshold", type=float, default=PROTECTED_NO_DANGER_THRESHOLD)
    parser.add_argument("--low-protected-adjacent-weight", type=float, default=LOW_PROTECTED_ADJACENT_WEIGHT)
    parser.add_argument("--low-protected-exact-weight", type=float, default=LOW_PROTECTED_EXACT_WEIGHT)
    parser.add_argument("--both-protected-adjacent-weight", type=float, default=BOTH_PROTECTED_ADJACENT_WEIGHT)
    parser.add_argument("--low-max-bin-share-soft", type=float, default=LOW_MAX_BIN_SHARE_SOFT)
    parser.add_argument("--low-overconcentration-extra-penalty", type=float, default=LOW_OVERCONCENTRATION_EXTRA_PENALTY)
    parser.add_argument("--high-coarse-lift-weight", type=float, default=HIGH_COARSE_LIFT_WEIGHT)
    parser.add_argument("--low-coarse-lift-weight", type=float, default=LOW_COARSE_LIFT_WEIGHT)
    parser.add_argument("--both-coarse-lift-weight", type=float, default=BOTH_COARSE_LIFT_WEIGHT)
    parser.add_argument("--combined-coarse-lift-weight", type=float, default=COMBINED_COARSE_LIFT_WEIGHT)
    parser.add_argument("--coarse-severe-penalty-weight", type=float, default=COARSE_SEVERE_PENALTY_WEIGHT)
    parser.add_argument("--min-high-no-danger", type=float, default=MIN_HIGH_NO_DANGER)
    parser.add_argument("--min-low-no-danger", type=float, default=MIN_LOW_NO_DANGER)
    parser.add_argument("--min-both-no-danger", type=float, default=MIN_BOTH_NO_DANGER)
    parser.add_argument("--min-combined-no-danger", type=float, default=MIN_COMBINED_NO_DANGER)
    parser.add_argument("--max-low-dangerous-bin-error", type=float, default=MAX_LOW_DANGEROUS_BIN_ERROR)
    parser.add_argument("--max-low-safe-overflow-bin-error", type=float, default=MAX_LOW_SAFE_OVERFLOW_BIN_ERROR)
    parser.add_argument("--min-high-coarse-acc", type=float, default=MIN_HIGH_COARSE_ACC)
    parser.add_argument("--min-low-coarse-acc", type=float, default=MIN_LOW_COARSE_ACC)
    parser.add_argument("--min-both-coarse-acc", type=float, default=MIN_BOTH_COARSE_ACC)
    parser.add_argument("--min-combined-coarse-acc", type=float, default=MIN_COMBINED_COARSE_ACC)
    parser.add_argument("--min-high-coarse-lift", type=float, default=MIN_HIGH_COARSE_LIFT)
    parser.add_argument("--min-low-coarse-lift", type=float, default=MIN_LOW_COARSE_LIFT)
    parser.add_argument("--min-both-coarse-lift", type=float, default=MIN_BOTH_COARSE_LIFT)
    parser.add_argument("--max-high-coarse-severe-pct", type=float, default=MAX_HIGH_COARSE_SEVERE_PCT)
    parser.add_argument("--max-low-coarse-severe-pct", type=float, default=MAX_LOW_COARSE_SEVERE_PCT)
    parser.add_argument("--max-both-coarse-severe-pct", type=float, default=MAX_BOTH_COARSE_SEVERE_PCT)
    return parser.parse_known_args(argv)


def _apply_wrapper_args(wrapper_args: argparse.Namespace) -> None:
    global SAFE_OVERFLOW_FREE_BINS, PROTECTED_NO_DANGER_THRESHOLD
    global LOW_PROTECTED_ADJACENT_WEIGHT, LOW_PROTECTED_EXACT_WEIGHT, BOTH_PROTECTED_ADJACENT_WEIGHT
    global LOW_MAX_BIN_SHARE_SOFT, LOW_OVERCONCENTRATION_EXTRA_PENALTY
    global HIGH_COARSE_LIFT_WEIGHT, LOW_COARSE_LIFT_WEIGHT, BOTH_COARSE_LIFT_WEIGHT, COMBINED_COARSE_LIFT_WEIGHT, COARSE_SEVERE_PENALTY_WEIGHT
    global MIN_HIGH_NO_DANGER, MIN_LOW_NO_DANGER, MIN_BOTH_NO_DANGER, MIN_COMBINED_NO_DANGER
    global MAX_LOW_DANGEROUS_BIN_ERROR, MAX_LOW_SAFE_OVERFLOW_BIN_ERROR
    global MIN_HIGH_COARSE_ACC, MIN_LOW_COARSE_ACC, MIN_BOTH_COARSE_ACC, MIN_COMBINED_COARSE_ACC
    global MIN_HIGH_COARSE_LIFT, MIN_LOW_COARSE_LIFT, MIN_BOTH_COARSE_LIFT
    global MAX_HIGH_COARSE_SEVERE_PCT, MAX_LOW_COARSE_SEVERE_PCT, MAX_BOTH_COARSE_SEVERE_PCT

    SAFE_OVERFLOW_FREE_BINS = max(0.0, float(wrapper_args.safe_overflow_free_bins))
    PROTECTED_NO_DANGER_THRESHOLD = float(wrapper_args.protected_no_danger_threshold)
    LOW_PROTECTED_ADJACENT_WEIGHT = float(wrapper_args.low_protected_adjacent_weight)
    LOW_PROTECTED_EXACT_WEIGHT = float(wrapper_args.low_protected_exact_weight)
    BOTH_PROTECTED_ADJACENT_WEIGHT = float(wrapper_args.both_protected_adjacent_weight)
    LOW_MAX_BIN_SHARE_SOFT = float(wrapper_args.low_max_bin_share_soft)
    LOW_OVERCONCENTRATION_EXTRA_PENALTY = float(wrapper_args.low_overconcentration_extra_penalty)
    HIGH_COARSE_LIFT_WEIGHT = float(wrapper_args.high_coarse_lift_weight)
    LOW_COARSE_LIFT_WEIGHT = float(wrapper_args.low_coarse_lift_weight)
    BOTH_COARSE_LIFT_WEIGHT = float(wrapper_args.both_coarse_lift_weight)
    COMBINED_COARSE_LIFT_WEIGHT = float(wrapper_args.combined_coarse_lift_weight)
    COARSE_SEVERE_PENALTY_WEIGHT = float(wrapper_args.coarse_severe_penalty_weight)
    MIN_HIGH_NO_DANGER = float(wrapper_args.min_high_no_danger)
    MIN_LOW_NO_DANGER = float(wrapper_args.min_low_no_danger)
    MIN_BOTH_NO_DANGER = float(wrapper_args.min_both_no_danger)
    MIN_COMBINED_NO_DANGER = float(wrapper_args.min_combined_no_danger)
    MAX_LOW_DANGEROUS_BIN_ERROR = float(wrapper_args.max_low_dangerous_bin_error)
    MAX_LOW_SAFE_OVERFLOW_BIN_ERROR = float(wrapper_args.max_low_safe_overflow_bin_error)
    MIN_HIGH_COARSE_ACC = float(wrapper_args.min_high_coarse_acc)
    MIN_LOW_COARSE_ACC = float(wrapper_args.min_low_coarse_acc)
    MIN_BOTH_COARSE_ACC = float(wrapper_args.min_both_coarse_acc)
    MIN_COMBINED_COARSE_ACC = float(wrapper_args.min_combined_coarse_acc)
    MIN_HIGH_COARSE_LIFT = float(wrapper_args.min_high_coarse_lift)
    MIN_LOW_COARSE_LIFT = float(wrapper_args.min_low_coarse_lift)
    MIN_BOTH_COARSE_LIFT = float(wrapper_args.min_both_coarse_lift)
    MAX_HIGH_COARSE_SEVERE_PCT = float(wrapper_args.max_high_coarse_severe_pct)
    MAX_LOW_COARSE_SEVERE_PCT = float(wrapper_args.max_low_coarse_severe_pct)
    MAX_BOTH_COARSE_SEVERE_PCT = float(wrapper_args.max_both_coarse_severe_pct)


def install_dual_head_target(args: Any) -> None:
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    P.asymmetric_bin_metrics = asymmetric_bin_metrics
    P.score_hilo_predictions = score_hilo_predictions
    P.predictor_fitness = protected_predictor_fitness
    P.dual_fail_reasons = dual_fail_reasons
    _orig_install_dual_head_target(args)
    P.BIN_TOLERANCE = 0
    P.TARGET_MODE = TARGET_MODE
    P.asymmetric_bin_metrics = asymmetric_bin_metrics
    P.score_hilo_predictions = score_hilo_predictions
    P.predictor_fitness = protected_predictor_fitness
    P.dual_fail_reasons = dual_fail_reasons
    P.L.evaluate_predictor = P.evaluate_predictor


def parse_args(argv: list[str] | None = None):
    wrapper_args, remaining = _parse_wrapper_args(sys.argv[1:] if argv is None else argv)
    _apply_wrapper_args(wrapper_args)
    args = _orig_parse_args(remaining)
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    args.safe_overflow_free_bins = SAFE_OVERFLOW_FREE_BINS
    args.protected_no_danger_threshold = PROTECTED_NO_DANGER_THRESHOLD
    args.low_protected_adjacent_weight = LOW_PROTECTED_ADJACENT_WEIGHT
    args.low_protected_exact_weight = LOW_PROTECTED_EXACT_WEIGHT
    args.both_protected_adjacent_weight = BOTH_PROTECTED_ADJACENT_WEIGHT
    args.low_max_bin_share_soft = LOW_MAX_BIN_SHARE_SOFT
    args.low_overconcentration_extra_penalty = LOW_OVERCONCENTRATION_EXTRA_PENALTY
    args.high_coarse_lift_weight = HIGH_COARSE_LIFT_WEIGHT
    args.low_coarse_lift_weight = LOW_COARSE_LIFT_WEIGHT
    args.both_coarse_lift_weight = BOTH_COARSE_LIFT_WEIGHT
    args.combined_coarse_lift_weight = COMBINED_COARSE_LIFT_WEIGHT
    args.coarse_severe_penalty_weight = COARSE_SEVERE_PENALTY_WEIGHT
    args.min_high_no_danger = MIN_HIGH_NO_DANGER
    args.min_low_no_danger = MIN_LOW_NO_DANGER
    args.min_both_no_danger = MIN_BOTH_NO_DANGER
    args.min_combined_no_danger = MIN_COMBINED_NO_DANGER
    args.max_low_dangerous_bin_error = MAX_LOW_DANGEROUS_BIN_ERROR
    args.max_low_safe_overflow_bin_error = MAX_LOW_SAFE_OVERFLOW_BIN_ERROR
    args.min_high_coarse_acc = MIN_HIGH_COARSE_ACC
    args.min_low_coarse_acc = MIN_LOW_COARSE_ACC
    args.min_both_coarse_acc = MIN_BOTH_COARSE_ACC
    args.min_combined_coarse_acc = MIN_COMBINED_COARSE_ACC
    args.min_high_coarse_lift = MIN_HIGH_COARSE_LIFT
    args.min_low_coarse_lift = MIN_LOW_COARSE_LIFT
    args.min_both_coarse_lift = MIN_BOTH_COARSE_LIFT
    args.max_high_coarse_severe_pct = MAX_HIGH_COARSE_SEVERE_PCT
    args.max_low_coarse_severe_pct = MAX_LOW_COARSE_SEVERE_PCT
    args.max_both_coarse_severe_pct = MAX_BOTH_COARSE_SEVERE_PCT
    return args


def dual_head_params() -> dict[str, Any]:
    params = _orig_dual_head_params()
    params.setdefault("bin_scoring", {})
    params["bin_scoring"].update(
        {
            "bin_tolerance": 0,
            "coarse_bin_mapping": {"0": [0, 1], "1": [2, 3], "2": [4, 5]},
            "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
            "danger_tolerance_removed": True,
            "dangerous_direction_error_allowed": False,
            "safe_direction_error_allowed": True,
            "safe_direction_error_free_until_bins": SAFE_OVERFLOW_FREE_BINS,
            "safe_direction_overflow_penalized": True,
        }
    )
    params.setdefault("coarse_objective", {})
    params["coarse_objective"].update(
        {
            "high_coarse_lift_weight": HIGH_COARSE_LIFT_WEIGHT,
            "low_coarse_lift_weight": LOW_COARSE_LIFT_WEIGHT,
            "both_coarse_lift_weight": BOTH_COARSE_LIFT_WEIGHT,
            "combined_coarse_lift_weight": COMBINED_COARSE_LIFT_WEIGHT,
            "coarse_severe_penalty_weight": COARSE_SEVERE_PENALTY_WEIGHT,
        }
    )
    params.setdefault("protected_precision", {})
    params["protected_precision"].update(
        {
            "protected_no_danger_threshold": PROTECTED_NO_DANGER_THRESHOLD,
            "low_protected_adjacent_weight": LOW_PROTECTED_ADJACENT_WEIGHT,
            "low_protected_exact_weight": LOW_PROTECTED_EXACT_WEIGHT,
            "both_protected_adjacent_weight": BOTH_PROTECTED_ADJACENT_WEIGHT,
            "low_max_bin_share_soft": LOW_MAX_BIN_SHARE_SOFT,
            "low_overconcentration_extra_penalty": LOW_OVERCONCENTRATION_EXTRA_PENALTY,
        }
    )
    params.setdefault("gate", {})
    params["gate"].update(
        {
            "min_high_no_danger": MIN_HIGH_NO_DANGER,
            "min_low_no_danger": MIN_LOW_NO_DANGER,
            "min_both_no_danger": MIN_BOTH_NO_DANGER,
            "min_combined_no_danger": MIN_COMBINED_NO_DANGER,
            "max_low_dangerous_bin_error": MAX_LOW_DANGEROUS_BIN_ERROR,
            "max_low_safe_overflow_bin_error": MAX_LOW_SAFE_OVERFLOW_BIN_ERROR,
            "min_high_coarse_acc": MIN_HIGH_COARSE_ACC,
            "min_low_coarse_acc": MIN_LOW_COARSE_ACC,
            "min_both_coarse_acc": MIN_BOTH_COARSE_ACC,
            "min_combined_coarse_acc": MIN_COMBINED_COARSE_ACC,
            "min_high_coarse_lift": MIN_HIGH_COARSE_LIFT,
            "min_low_coarse_lift": MIN_LOW_COARSE_LIFT,
            "min_both_coarse_lift": MIN_BOTH_COARSE_LIFT,
            "max_high_coarse_severe_pct": MAX_HIGH_COARSE_SEVERE_PCT,
            "max_low_coarse_severe_pct": MAX_LOW_COARSE_SEVERE_PCT,
            "max_both_coarse_severe_pct": MAX_BOTH_COARSE_SEVERE_PCT,
        }
    )
    return params


def make_dual_baseline_spec(train_df):
    spec = _orig_make_dual_baseline_spec(train_df)
    spec["target_mode"] = TARGET_MODE
    spec["bin_tolerance"] = 0
    spec["safe_overflow_free_bins"] = SAFE_OVERFLOW_FREE_BINS
    spec["coarse_bin_mapping"] = {"0": [0, 1], "1": [2, 3], "2": [4, 5]}
    spec["asymmetric_bin_rule"] = {
        "high": "penalize predicted bin above actual bin; safe-side overflow is free for the configured first bins",
        "low": "penalize actual low-magnitude bin above predicted bin; safe-side overflow is free for the configured first bins",
        "danger_tolerance_removed": True,
        "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
    }
    spec["protected_precision"] = {
        "protected_no_danger_threshold": PROTECTED_NO_DANGER_THRESHOLD,
        "low_protected_adjacent_weight": LOW_PROTECTED_ADJACENT_WEIGHT,
        "low_protected_exact_weight": LOW_PROTECTED_EXACT_WEIGHT,
    }
    spec["coarse_objective"] = {
        "high_coarse_lift_weight": HIGH_COARSE_LIFT_WEIGHT,
        "low_coarse_lift_weight": LOW_COARSE_LIFT_WEIGHT,
        "both_coarse_lift_weight": BOTH_COARSE_LIFT_WEIGHT,
    }
    return spec


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: Any):
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return _orig_run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


# 이전 모듈의 함수 참조를 현재 정책으로 교체한다.
P.asymmetric_bin_metrics = asymmetric_bin_metrics
P.score_hilo_predictions = score_hilo_predictions
P.predictor_fitness = protected_predictor_fitness
P.dual_fail_reasons = dual_fail_reasons
P.install_dual_head_target = install_dual_head_target
P.parse_args = parse_args
P.dual_head_params = dual_head_params
P.make_dual_baseline_spec = make_dual_baseline_spec
P.run_original_stage2_predictor = run_original_stage2_predictor
P.TARGET_MODE = TARGET_MODE
P.BIN_TOLERANCE = 0

# 외부 import 호환을 위해 주요 이름을 노출한다.
for _name in dir(P):
    if _name.startswith("__"):
        continue
    if _name in globals():
        continue
    globals()[_name] = getattr(P, _name)

# 노출 후에도 교체 함수가 덮이지 않도록 다시 지정한다.
globals()["coarse_bin_metrics"] = coarse_bin_metrics
globals()["score_hilo_predictions"] = score_hilo_predictions
globals()["asymmetric_bin_metrics"] = asymmetric_bin_metrics
globals()["protected_predictor_fitness"] = protected_predictor_fitness
globals()["predictor_fitness"] = protected_predictor_fitness
globals()["dual_fail_reasons"] = dual_fail_reasons
globals()["install_dual_head_target"] = install_dual_head_target
globals()["parse_args"] = parse_args
globals()["dual_head_params"] = dual_head_params
globals()["make_dual_baseline_spec"] = make_dual_baseline_spec
globals()["run_original_stage2_predictor"] = run_original_stage2_predictor
globals()["TARGET_MODE"] = TARGET_MODE
globals()["BIN_TOLERANCE"] = 0


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
