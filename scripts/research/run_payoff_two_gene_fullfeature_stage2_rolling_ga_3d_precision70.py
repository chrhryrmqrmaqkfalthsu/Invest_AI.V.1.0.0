#!/usr/bin/env python3
"""3일 lag + 전체 파라미터 GA + 적중률 70% 강제 목적 wrapper.

목적:
- 개체 진화 목적을 적중률 최우선으로 고정한다.
- 생성 구간에서 적중률 70% 미만이면 진화 점수 무효.
- 위험 발생률이 기준을 넘으면 진화 점수 무효.
- 평균 보상폭이 0 이하이면 진화 점수 무효.
- 검증 구간은 진화에 쓰지 않고 마지막 확인으로만 둔다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_FULLFEATURE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga.py"
FEATURE_LOOKBACK_DAYS = 3
INVALID_FITNESS = -1e9


def _force_lookback_3(runner: Any) -> Any:
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
    spec = importlib.util.spec_from_file_location("fullfeature_stage2_ga_precision70", BASE_FULLFEATURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_FULLFEATURE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fullfeature_stage2_ga_precision70"] = mod
    spec.loader.exec_module(mod)
    return mod


def strict_precision_fitness(m: dict[str, Any], ind: Any, args: Any) -> float:
    signal_count = float(m["신호발생일"])
    coverage = float(m["신호발생비율"])
    precision = float(m["적중률"])
    bad_rate = float(m["위험발생률"])
    bad_hits = float(m["위험발생일"])
    payoff = float(m["평균보상폭"])
    avg_high = float(m["평균상방폭"])
    avg_low = float(m["평균하방폭"])

    if signal_count < float(args.min_signal_count):
        return INVALID_FITNESS
    if coverage < float(args.min_coverage_pct):
        return INVALID_FITNESS
    if coverage > float(args.max_coverage_pct):
        return INVALID_FITNESS
    if precision < float(args.min_precision_pct):
        return INVALID_FITNESS
    if bad_rate > float(args.max_bad_rate_pct):
        return INVALID_FITNESS
    if payoff <= 0.0:
        return INVALID_FITNESS

    active_total = int(ind.up.active.sum() + ind.low.active.sum())
    too_many_penalty = max(0, active_total - args.max_active_total) * args.active_count_penalty

    # 적중률을 최우선으로 둔다. 위험 0, 보상폭, 신호수는 그 다음 순위다.
    return float(
        precision * 12.0
        - bad_rate * 10.0
        - bad_hits * 35.0
        + payoff * 25.0
        + avg_high * 5.0
        - avg_low * 2.0
        + min(signal_count, 20.0) * 0.5
        - too_many_penalty
    )


def main() -> int:
    mod = _load_fullfeature_module()
    mod.source_fitness = strict_precision_fitness
    mod.INVALID_FITNESS = INVALID_FITNESS

    original_load_base = mod.load_base

    def load_base_lookback3():
        base = original_load_base()
        original_load_runner = base.load_runner

        def load_runner_lookback3():
            return _force_lookback_3(original_load_runner())

        base.load_runner = load_runner_lookback3
        return base

    mod.load_base = load_base_lookback3

    argv = list(sys.argv[1:])
    if "--min-precision-pct" not in argv:
        argv += ["--min-precision-pct", "70"]
    if "--min-mean-precision-pct" not in argv:
        argv += ["--min-mean-precision-pct", "70"]
    if "--max-bad-rate-pct" not in argv:
        argv += ["--max-bad-rate-pct", "15"]
    if "--max-mean-bad-rate-pct" not in argv:
        argv += ["--max-mean-bad-rate-pct", "10"]
    if "--out-dir" not in argv:
        argv += ["--out-dir", "exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_3d_precision70_20260706_001"]
    return int(mod.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
