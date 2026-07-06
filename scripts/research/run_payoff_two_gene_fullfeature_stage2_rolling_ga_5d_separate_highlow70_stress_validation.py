#!/usr/bin/env python3
"""5일 lag + 최고가/최저가 분리 학습 + stress 검증 전용 wrapper.

핵심:
- 개체 생성/진화: train1, train2, train3
- 학습 생존 평가: train1, train2, train3만 사용
- stress: 학습/분위수 기준에서 제외하고 검증 구간으로만 사용
- oos: 최종 확인 구간으로만 사용

기존 5일 분리 학습 wrapper와 목적 함수는 그대로 두고,
stress가 학습 기준에 섞이지 않게 분리한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_SEPARATE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_3d_separate_highlow70.py"
FEATURE_LOOKBACK_DAYS = 5
TRAIN_EVALUATION_PERIODS = ["train1", "train2", "train3"]
VALIDATION_PERIOD = "stress"
FINAL_CHECK_PERIOD = "oos"
DEFAULT_OUT_DIR = "exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_separate_highlow70_stress_validation_20260706_001"


def _load_separate_module():
    spec = importlib.util.spec_from_file_location("separate_highlow70_5d_stress_validation", BASE_SEPARATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_SEPARATE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["separate_highlow70_5d_stress_validation"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    separate_mod = _load_separate_module()
    separate_mod.FEATURE_LOOKBACK_DAYS = FEATURE_LOOKBACK_DAYS

    original_load_fullfeature_module = separate_mod._load_fullfeature_module

    def load_fullfeature_module_stress_validation():
        full_mod = original_load_fullfeature_module()
        full_mod.SURVIVAL_PERIODS = list(TRAIN_EVALUATION_PERIODS)
        full_mod.IMPORTANT_PERIODS = [VALIDATION_PERIOD, FINAL_CHECK_PERIOD]
        return full_mod

    separate_mod._load_fullfeature_module = load_fullfeature_module_stress_validation

    argv = list(sys.argv[1:])
    if "--out-dir" not in argv:
        argv += ["--out-dir", DEFAULT_OUT_DIR]
    sys.argv = [sys.argv[0], *argv]
    return int(separate_mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
