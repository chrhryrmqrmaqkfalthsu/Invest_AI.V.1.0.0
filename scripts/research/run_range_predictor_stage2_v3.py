#!/usr/bin/env python3
"""
Original-Stage2-style multi-condition dual-head GA runner for next-day HIGH/LOW bin prediction.

이번 파일은 직전 검증 커밋(07c0bcf)의 멀티컨디션 dual-head GA를 로드한 뒤,
실전 방향 비대칭 bin 오차 규칙만 교체한다.

핵심 규칙:
- HIGH: 예측 bin이 실제 high bin보다 높으면 위험 오차다.
  예: +2 예상, 실제 +1만 감 => 위험 오차/벌점.
  예: +2 예상, 실제 +3 감 => 안전 오차/벌점 없음.

- LOW: 실제 low-magnitude bin이 예측 bin보다 높으면 위험 오차다.
  예: -2 예상, 실제 -3까지 빠짐 => 위험 오차/벌점.
  예: -2 예상, 실제 -1만 빠짐 => 안전 오차/벌점 없음.

- 위험하지 않은 방향으로 틀린 것은 허용한다.
- 위험한 방향으로 틀린 것은 1칸도 허용하지 않는다.
- 기존 --bin-tolerance 인자는 호환성 때문에 파싱만 되며, 내부에서는 항상 0으로 강제된다.

Stage2 흐름은 유지한다:
train_1 독립 GA 100개 + train_2 독립 GA 100개 + train_3 독립 GA 100개
-> stress_pre_2022h1 -> train_3_eval -> train_2_eval -> train_1_eval -> oos_2025h2
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREV_COMMIT = "07c0bcf"
SELF_PATH = "scripts/research/run_range_predictor_stage2_v3.py"
TARGET_MODE = "next_day_hilo_multicond_dual_head_original_stage2_asymmetric_no_danger_tolerance"


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


def asymmetric_bin_metrics(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, float]:
    """위험하지 않은 방향 오차만 허용하고, 위험 방향 오차는 1칸도 허용하지 않는다.

    HIGH bin은 숫자가 클수록 더 높은 고가 구간이다.
    - ph > yh: +2를 예상했는데 +1만 찍은 꼴이라 위험 오차.
    - ph < yh: +2를 예상했는데 +3을 찍은 꼴이라 안전 오차.

    LOW bin은 숫자가 클수록 더 큰 하락폭 구간이다.
    - pl < yl: -2를 예상했는데 -3까지 빠진 꼴이라 위험 오차.
    - pl > yl: -2를 예상했는데 -1만 빠진 꼴이라 안전 오차.
    """
    yh = yh.astype(int)
    yl = yl.astype(int)
    ph = ph.astype(int)
    pl = pl.astype(int)
    n = max(1, int(len(yh)))

    high_danger = np.maximum(0, ph - yh).astype(float)
    high_safe = np.maximum(0, yh - ph).astype(float)
    low_danger = np.maximum(0, yl - pl).astype(float)
    low_safe = np.maximum(0, pl - yl).astype(float)

    high_asym = high_danger * P.HIGH_DANGEROUS_BIN_ERROR_WEIGHT + high_safe * P.HIGH_SAFE_BIN_ERROR_WEIGHT
    low_asym = low_danger * P.LOW_DANGEROUS_BIN_ERROR_WEIGHT + low_safe * P.LOW_SAFE_BIN_ERROR_WEIGHT

    # 핵심 변경: 위험 방향 오차는 1칸도 허용하지 않는다.
    # 즉 danger == 0 인 경우만 OK다. 안전 방향 오차는 OK다.
    high_no_danger = high_danger == 0
    low_no_danger = low_danger == 0
    both_no_danger = high_no_danger & low_no_danger

    high_no_danger_pct = float(np.mean(high_no_danger) * 100.0) if n else 0.0
    low_no_danger_pct = float(np.mean(low_no_danger) * 100.0) if n else 0.0
    both_no_danger_pct = float(np.mean(both_no_danger) * 100.0) if n else 0.0
    combined_no_danger_pct = float((np.mean(high_no_danger) + np.mean(low_no_danger)) / 2.0 * 100.0) if n else 0.0

    return {
        "bin_tolerance": 0.0,
        "high_dangerous_bin_error_mean": float(np.mean(high_danger)) if n else 0.0,
        "low_dangerous_bin_error_mean": float(np.mean(low_danger)) if n else 0.0,
        "combined_dangerous_bin_error_mean": float((np.mean(high_danger) + np.mean(low_danger)) / 2.0) if n else 0.0,
        "high_safe_bin_error_mean": float(np.mean(high_safe)) if n else 0.0,
        "low_safe_bin_error_mean": float(np.mean(low_safe)) if n else 0.0,
        "combined_safe_bin_error_mean": float((np.mean(high_safe) + np.mean(low_safe)) / 2.0) if n else 0.0,
        "high_asymmetric_bin_error_mean": float(np.mean(high_asym)) if n else 0.0,
        "low_asymmetric_bin_error_mean": float(np.mean(low_asym)) if n else 0.0,
        "combined_asymmetric_bin_error_mean": float((np.mean(high_asym) + np.mean(low_asym)) / 2.0) if n else 0.0,
        # 기존 evaluate_predictor/fitness와 호환되는 이름. 이제 tolerant가 아니라 no-danger 의미다.
        "high_directional_tolerant_acc_pct": high_no_danger_pct,
        "low_directional_tolerant_acc_pct": low_no_danger_pct,
        "both_directional_tolerant_acc_pct": both_no_danger_pct,
        "combined_directional_tolerant_acc_pct": combined_no_danger_pct,
        # 사람이 읽기 쉬운 새 이름.
        "high_no_danger_acc_pct": high_no_danger_pct,
        "low_no_danger_acc_pct": low_no_danger_pct,
        "both_no_danger_acc_pct": both_no_danger_pct,
        "combined_no_danger_acc_pct": combined_no_danger_pct,
        # 기존 safe_or_exact도 동일한 의미로 유지.
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


def install_dual_head_target(args: Any) -> None:
    # --bin-tolerance를 사용자가 줘도 내부에서는 항상 0으로 강제한다.
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    _orig_install_dual_head_target(args)
    P.BIN_TOLERANCE = 0
    P.TARGET_MODE = TARGET_MODE
    P.asymmetric_bin_metrics = asymmetric_bin_metrics


def parse_args(argv: list[str] | None = None):
    args = _orig_parse_args(argv)
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return args


def dual_head_params() -> dict[str, Any]:
    params = _orig_dual_head_params()
    params.setdefault("bin_scoring", {})
    params["bin_scoring"].update(
        {
            "bin_tolerance": 0,
            "danger_tolerance_removed": True,
            "high_rule": "predicted high bin above actual high bin is dangerous; actual high above prediction is safe",
            "low_rule": "actual low-magnitude bin above predicted low bin is dangerous; shallower low than prediction is safe",
            "dangerous_direction_error_allowed": False,
            "safe_direction_error_allowed": True,
        }
    )
    params.setdefault("score_weights", {})
    params["score_weights"]["high_directional_tolerance"] = P.HIGH_DIRECTIONAL_TOLERANCE_WEIGHT
    params["score_weights"]["low_directional_tolerance"] = P.LOW_DIRECTIONAL_TOLERANCE_WEIGHT
    params["score_weights"]["both_directional_tolerance"] = P.BOTH_DIRECTIONAL_TOLERANCE_WEIGHT
    params["score_weights"]["asymmetric_bin_error"] = P.ASYMMETRIC_BIN_ERROR_WEIGHT
    return params


def make_dual_baseline_spec(train_df):
    spec = _orig_make_dual_baseline_spec(train_df)
    spec["target_mode"] = TARGET_MODE
    spec["bin_tolerance"] = 0
    spec["asymmetric_bin_rule"] = {
        "high": "penalize predicted bin above actual bin; do not penalize actual high above prediction",
        "low": "penalize actual low-magnitude bin above predicted bin; do not penalize shallower low than prediction",
        "danger_tolerance_removed": True,
    }
    return spec


def run_original_stage2_predictor(ticker: str, out_dir: Path, seed_base: int, args: Any):
    if hasattr(args, "bin_tolerance"):
        args.bin_tolerance = 0
    return _orig_run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)


# 이전 모듈의 함수 참조를 현재 정책으로 교체한다.
P.asymmetric_bin_metrics = asymmetric_bin_metrics
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
globals()["install_dual_head_target"] = install_dual_head_target
globals()["parse_args"] = parse_args
globals()["dual_head_params"] = dual_head_params
globals()["make_dual_baseline_spec"] = make_dual_baseline_spec
globals()["run_original_stage2_predictor"] = run_original_stage2_predictor
globals()["TARGET_MODE"] = TARGET_MODE
globals()["BIN_TOLERANCE"] = 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else P.auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else P.default_seed_base(ticker)
    run_original_stage2_predictor(ticker=ticker, out_dir=out_dir, seed_base=seed_base, args=args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
