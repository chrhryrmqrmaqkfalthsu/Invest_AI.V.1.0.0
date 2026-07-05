#!/usr/bin/env python3
"""5일 lag + 전체 파라미터 + 최고가/최저가 기준선 분리 학습 GA wrapper.

3일 분리 학습 실험과 같은 구조를 유지하고, 최근 지표 기간만 5일로 바꾼다.

구조:
- 상방 유전자: 다음날 최고가가 +1ATR 이상 가는지 학습.
- 하방 유전자: 다음날 최저가 하락폭이 -0.7ATR 이내로 안전한지 학습.
- 최종 매수 후보: 상방 유전자 통과 AND 하방 유전자 통과.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_SEPARATE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_3d_separate_highlow70.py"
FEATURE_LOOKBACK_DAYS = 5
DEFAULT_OUT_DIR = "exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_separate_highlow70_20260706_001"


def _load_base_module():
    spec = importlib.util.spec_from_file_location("separate_highlow70_5d", BASE_SEPARATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_SEPARATE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["separate_highlow70_5d"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_base_module()
    mod.FEATURE_LOOKBACK_DAYS = FEATURE_LOOKBACK_DAYS

    argv = list(sys.argv[1:])
    if "--out-dir" not in argv:
        argv += ["--out-dir", DEFAULT_OUT_DIR]
    sys.argv = [sys.argv[0], *argv]
    return int(mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
