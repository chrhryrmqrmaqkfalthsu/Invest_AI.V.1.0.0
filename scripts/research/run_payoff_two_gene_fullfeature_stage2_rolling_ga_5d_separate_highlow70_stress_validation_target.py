#!/usr/bin/env python3
"""5일 lag + 최고가/최저가 분리 학습 + stress 검증 전용 + 상방 ATR 기준선 변경 wrapper.

사용 예:
  python scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_separate_highlow70_stress_validation_target.py \
    --ticker MPC --high-target-atr 0.7 --per-period-count 100 --generations 80

핵심:
- 개체 생성/진화: train1, train2, train3
- 학습 생존 평가: train1, train2, train3만 사용
- stress: 검증 구간
- oos: 최종 확인 구간
- 상방 유전자 목표: 다음날 최고가가 +N ATR 이상 가는지
- 하방 유전자 목표: 다음날 최저가 하락폭이 -0.7 ATR 이내로 안전한지
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_SEPARATE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_3d_separate_highlow70.py"
FEATURE_LOOKBACK_DAYS = 5
TRAIN_EVALUATION_PERIODS = ["train1", "train2", "train3"]
VALIDATION_PERIOD = "stress"
FINAL_CHECK_PERIOD = "oos"


def _load_separate_module():
    spec = importlib.util.spec_from_file_location("separate_highlow70_5d_stress_validation_target", BASE_SEPARATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_SEPARATE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["separate_highlow70_5d_stress_validation_target"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_wrapper_args(argv: list[str]) -> tuple[float, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--high-target-atr", type=float, default=0.7)
    known, remaining = parser.parse_known_args(argv)
    return float(known.high_target_atr), remaining


def main() -> int:
    high_target_atr, remaining = _parse_wrapper_args(sys.argv[1:])
    separate_mod = _load_separate_module()
    separate_mod.FEATURE_LOOKBACK_DAYS = FEATURE_LOOKBACK_DAYS
    separate_mod.HIGH_TARGET_ATR = float(high_target_atr)

    original_load_fullfeature_module = separate_mod._load_fullfeature_module

    def load_fullfeature_module_stress_validation():
        full_mod = original_load_fullfeature_module()
        full_mod.SURVIVAL_PERIODS = list(TRAIN_EVALUATION_PERIODS)
        full_mod.IMPORTANT_PERIODS = [VALIDATION_PERIOD, FINAL_CHECK_PERIOD]
        return full_mod

    separate_mod._load_fullfeature_module = load_fullfeature_module_stress_validation

    target_tag = str(high_target_atr).replace(".", "p")
    default_out_dir = f"exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_high{target_tag}_separate_highlow70_stress_validation_20260706_001"
    argv = list(remaining)
    if "--good-high-atr" not in argv:
        argv += ["--good-high-atr", str(high_target_atr)]
    if "--out-dir" not in argv:
        argv += ["--out-dir", default_out_dir]
    sys.argv = [sys.argv[0], *argv]
    return int(separate_mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
