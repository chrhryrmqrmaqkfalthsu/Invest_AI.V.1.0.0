#!/usr/bin/env python3
"""
Original-Stage2-style multi-condition dual-head GA runner for next-day HIGH/LOW bin prediction.

이번 파일은 검증 커밋(07c0bcf)의 멀티컨디션 dual-head GA를 로드한 뒤,
실전 방향 비대칭 bin 오차와 정확도 보상 정책만 교체한다.

핵심 규칙:
- HIGH: 예측 bin이 실제 high bin보다 높으면 위험 오차다.
  예: +2 예상, 실제 +1만 감 => 위험 오차/벌점.
  예: +2 예상, 실제 +3 감 => 안전 방향 오차.

- LOW: 실제 low-magnitude bin이 예측 bin보다 높으면 위험 오차다.
  예: -2 예상, 실제 -3까지 빠짐 => 위험 오차/벌점.
  예: -2 예상, 실제 -1만 빠짐 => 안전 방향 오차.

- 위험 방향 오차는 1칸도 허용하지 않는다.
- 안전 방향 오차는 기본 1칸까지 무료다.
- 안전 방향 2칸 이상 과한 오차만 약한 비용을 준다.
- no-danger가 일정 수준 이상인 개체는 adjacent/exact 정확도 보상을 더 받는다.
- Stage2 흐름은 유지한다:
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
TARGET_MODE = "next_day_hilo_multicond_dual_head_original_stage2_asymmetric_safe_overflow_precision_gate"

SAFE_OVERFLOW_FREE_BINS = 1.0
PROTECTED_NO_DANGER_THRESHOLD = 60.0
LOW_PROTECTED_ADJACENT_WEIGHT = 0.45
LOW_PROTECTED_EXACT_WEIGHT = 0.20
BOTH_PROTECTED_ADJACENT_WEIGHT = 0.15
LOW_MAX_BIN_SHARE_SOFT = 55.0
LOW_OVERCONCENTRATION_EXTRA_PENALTY = 0.15

MIN_HIGH_NO_DANGER = -999.0
MIN_LOW_NO_DANGER = -999.0
MIN_BOTH_NO_DANGER = -999.0
MIN_COMBINED_NO_DANGER = -999.0
MAX_LOW_DANGEROUS_BIN_ERROR = 999.0
MAX_LOW_SAFE_OVERFLOW_BIN_ERROR = 999.0


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

    return {
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
        # 기존 evaluate_predictor/fitness와 호환되는 이름. 이제 tolerant가 아니라 no-danger 의미다.
        "high_directional_tolerant_acc_pct": high_no_danger_pct,
        "low_directional_tolerant_acc_pct": low_no_danger_pct,
        "both_directional_tolerant_acc_pct": both_no_danger_pct,
        "combined_directional_tolerant_acc_pct": combined_no_danger_pct,
        # 사람이 읽기 쉬운 이름.
        "high_no_danger_acc_pct": high_no_danger_pct,
        "low_no_danger_acc_pct": low_no_danger_pct,
        "both_no_danger_acc_pct": both_no_danger_pct,
        "combined_no_danger_acc_pct": combined_no_danger_pct,
        "high_safe_or_exact_acc_pct": high_no_danger_pct,
        "low_safe_or_exact_acc_pct": low_no_danger_pct,
        "both_safe_or_exact_acc_pct": both_no_danger_pct,
        "combined_safe_or_exact_acc_pct": combined_no_danger_pct,
    }


_orig_install_dual_head_target = P.install_dual_head_target
_orig_parse_args = P.parse_args
_orig_dual_head_params = P.dual_head_params
_orig_make_dual_baseline_spec = P.make_dual_baseline_spec
_orig_run_original_stage2_predictor = P.run_original_stage2_predictor
_orig_predictor_fitness = P.predictor_fitness
_orig_dual_fail_reasons = P.dual_fail_reasons


def protected_predictor_fitness(metrics: Mapping[str, Any]) -> float:
    base = float(_orig_predictor_fitness(metrics))
    low_no_danger = _safe_float(metrics.get("low_no_danger_acc_pct", metrics.get("low_safe_or_exact_acc_pct")))
    both_no_danger = _safe_float(metrics.get("both_no_danger_acc_pct", metrics.get("both_safe_or_exact_acc_pct")))
    low_adj = _safe_float(metrics.get("low_adjacent_acc_pct"))
    low_exact = _safe_float(metrics.get("low_exact_acc_pct"))
    both_adj = _safe_float(metrics.get("both_adjacent_acc_pct"))
    low_max_share = _safe_float(metrics.get("max_pred_share_low_pct"))

    low_shield = _clamp((low_no_danger - PROTECTED_NO_DANGER_THRESHOLD) / max(1.0, 100.0 - PROTECTED_NO_DANGER_THRESHOLD), 0.0, 1.0)
    both_shield = _clamp((both_no_danger - PROTECTED_NO_DANGER_THRESHOLD) / max(1.0, 100.0 - PROTECTED_NO_DANGER_THRESHOLD), 0.0, 1.0)

    low_protected_adjacent_bonus = low_shield * max(0.0, low_adj - 50.0) * LOW_PROTECTED_ADJACENT_WEIGHT
    low_protected_exact_bonus = low_shield * max(0.0, low_exact - 18.0) * LOW_PROTECTED_EXACT_WEIGHT
    both_protected_adjacent_bonus = both_shield * max(0.0, both_adj - 30.0) * BOTH_PROTECTED_ADJACENT_WEIGHT
    low_overconcentration_extra_penalty = max(0.0, low_max_share - LOW_MAX_BIN_SHARE_SOFT) * LOW_OVERCONCENTRATION_EXTRA_PENALTY

    fitness = base + low_protected_adjacent_bonus + low_protected_exact_bonus + both_protected_adjacent_bonus - low_overconcentration_extra_penalty

    if isinstance(metrics, dict):
        metrics["protected_no_danger_threshold"] = PROTECTED_NO_DANGER_THRESHOLD
        metrics["low_protected_adjacent_bonus"] = low_protected_adjacent_bonus
        metrics["low_protected_exact_bonus"] = low_protected_exact_bonus
        metrics["both_protected_adjacent_bonus"] = both_protected_adjacent_bonus
        metrics["low_overconcentration_extra_penalty"] = low_overconcentration_extra_penalty
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
    parser.add_argument("--min-high-no-danger", type=float, default=MIN_HIGH_NO_DANGER)
    parser.add_argument("--min-low-no-danger", type=float, default=MIN_LOW_NO_DANGER)
    parser.add_argument("--min-both-no-danger", type=float, default=MIN_BOTH_NO_DANGER)
    parser.add_argument("--min-combined-no-danger", type=float, default=MIN_COMBINED_NO_DANGER)
    parser.add_argument("--max-low-dangerous-bin-error", type=float, default=MAX_LOW_DANGEROUS_BIN_ERROR)
    parser.add_argument("--max-low-safe-overflow-bin-error", type=float, default=MAX_LOW_SAFE_OVERFLOW_BIN_ERROR)
    return parser.parse_known_args(argv)


def _apply_wrapper_args(wrapper_args: argparse.Namespace) -> None:
    global SAFE_OVERFLOW_FREE_BINS, PROTECTED_NO_DANGER_THRESHOLD
    global LOW_PROTECTED_ADJACENT_WEIGHT, LOW_PROTECTED_EXACT_WEIGHT, BOTH_PROTECTED_ADJACENT_WEIGHT
    global LOW_MAX_BIN_SHARE_SOFT, LOW_OVERCONCENTRATION_EXTRA_PENALTY
    global MIN_HIGH_NO_DANGER, MIN_LOW_NO_DANGER, MIN_BOTH_NO_DANGER, MIN_COMBINED_NO_DANGER
    global MAX_LOW_DANGEROUS_BIN_ERROR, MAX_LOW_SAFE_OVERFLOW_BIN_ERROR

    SAFE_OVERFLOW_FREE_BINS = max(0.0, float(wrapper_args.safe_overflow_free_bins))
    PROTECTED_NO_DANGER_THRESHOLD = float(wrapper_args.protected_no_danger_threshold)
    LOW_PROTECTED_ADJACENT_WEIGHT = float(wrapper_args.low_protected_adjacent_weight)
    LOW_PROTECTED_EXACT_WEIGHT = float(wrapper_args.low_protected_exact_weight)
    BOTH_PROTECTED_ADJACENT_WEIGHT = float(wrapper_args.both_protected_adjacent_weight)
    LOW_MAX_BIN_SHARE_SOFT = float(wrapper_args.low_max_bin_share_soft)
    LOW_OVERCONCENTRATION_EXTRA_PENALTY = float(wrapper_args.low_overconcentration_extra_penalty)
    MIN_HIGH_NO_DANGER = float(wrapper_args.min_high_no_danger)
    MIN_LOW_NO_DANGER = float(wrapper_args.min_low_no_danger)
    MIN_BOTH_NO_DANGER = float(wrapper_args.min_both_no_danger)
    MIN_COMBINED_NO_DANGER = float(wrapper_args.min_combined_no_danger)
    MAX_LOW_DANGEROUS_BIN_ERROR = float(wrapper_args.max_low_dangerous_bin_error)
    MAX_LOW_SAFE_OVERFLOW_BIN_ERROR = float(wrapper_args.max_low_safe_overflow_bin_error)


def install_dual_head_target(args: Any) -> None:
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    P.asymmetric_bin_metrics = asymmetric_bin_metrics
    P.predictor_fitness = protected_predictor_fitness
    P.dual_fail_reasons = dual_fail_reasons
    _orig_install_dual_head_target(args)
    P.BIN_TOLERANCE = 0
    P.TARGET_MODE = TARGET_MODE
    P.asymmetric_bin_metrics = asymmetric_bin_metrics
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
    args.min_high_no_danger = MIN_HIGH_NO_DANGER
    args.min_low_no_danger = MIN_LOW_NO_DANGER
    args.min_both_no_danger = MIN_BOTH_NO_DANGER
    args.min_combined_no_danger = MIN_COMBINED_NO_DANGER
    args.max_low_dangerous_bin_error = MAX_LOW_DANGEROUS_BIN_ERROR
    args.max_low_safe_overflow_bin_error = MAX_LOW_SAFE_OVERFLOW_BIN_ERROR
    return args


def dual_head_params() -> dict[str, Any]:
    params = _orig_dual_head_params()
    params.setdefault("bin_scoring", {})
    params["bin_scoring"].update(
        {
            "bin_tolerance": 0,
            "safe_overflow_free_bins": SAFE_OVERFLOW_FREE_BINS,
            "danger_tolerance_removed": True,
            "dangerous_direction_error_allowed": False,
            "safe_direction_error_allowed": True,
            "safe_direction_error_free_until_bins": SAFE_OVERFLOW_FREE_BINS,
            "safe_direction_overflow_penalized": True,
            "high_rule": "predicted high bin above actual high bin is dangerous; actual high above prediction is safe up to free overflow bins",
            "low_rule": "actual low-magnitude bin above predicted low bin is dangerous; shallower low than prediction is safe up to free overflow bins",
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
        }
    )
    return params


def make_dual_baseline_spec(train_df):
    spec = _orig_make_dual_baseline_spec(train_df)
    spec["target_mode"] = TARGET_MODE
    spec["bin_tolerance"] = 0
    spec["safe_overflow_free_bins"] = SAFE_OVERFLOW_FREE_BINS
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
    return spec


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: Any):
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return _orig_run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


# 이전 모듈의 함수 참조를 현재 정책으로 교체한다.
P.asymmetric_bin_metrics = asymmetric_bin_metrics
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
