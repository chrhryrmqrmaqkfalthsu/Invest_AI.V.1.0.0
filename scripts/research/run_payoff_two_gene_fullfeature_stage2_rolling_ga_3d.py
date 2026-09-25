#!/usr/bin/env python3
"""3일 lag 전용 전체 파라미터 Stage2 rolling 2유전자 payoff GA wrapper.

기존 전체 파라미터 GA는 그대로 재사용하되, 데이터셋 생성 직전에
run_range_predictor_stage2_v3의 LOOKBACK을 3으로 강제한다.

결과적으로 STK_lag1~3, STAGE2_lag1~3만 생성되고,
그 안의 전체 파라미터를 상방/하방 유전자 슬롯으로 전부 사용한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_FULLFEATURE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga.py"
FEATURE_LOOKBACK_DAYS = 3


def _force_lookback_3(runner: Any) -> Any:
    """Force all visible runner/base dataset modules to generate lag1~3 only."""
    setattr(runner, "FEATURE_LOOKBACK_DAYS", FEATURE_LOOKBACK_DAYS)
    targets = [runner, getattr(runner, "P", None), getattr(runner, "L", None)]
    p = getattr(runner, "P", None)
    if p is not None:
        targets.append(getattr(p, "L", None))
    for target in targets:
        if target is None:
            continue
        setattr(target, "FEATURE_LOOKBACK_DAYS", FEATURE_LOOKBACK_DAYS)
        if hasattr(target, "LOOKBACK"):
            setattr(target, "LOOKBACK", FEATURE_LOOKBACK_DAYS)
    apply_fn = getattr(runner, "_apply_feature_lookback_days", None)
    if callable(apply_fn):
        apply_fn()
    return runner


def _load_fullfeature_module():
    spec = importlib.util.spec_from_file_location("fullfeature_stage2_ga", BASE_FULLFEATURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_FULLFEATURE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fullfeature_stage2_ga"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_fullfeature_module()
    original_load_base = mod.load_base

    def load_base_lookback3():
        base = original_load_base()
        original_load_runner = base.load_runner

        def load_runner_lookback3():
            return _force_lookback_3(original_load_runner())

        base.load_runner = load_runner_lookback3
        return base

    mod.load_base = load_base_lookback3
    return int(mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
