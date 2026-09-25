#!/usr/bin/env python3
"""3일 lag + 전체 파라미터 + 최고가/최저가 기준선 분리 학습 GA.

구조:
- 유전자 2개 유지.
- 상방 유전자: 다음날 최고가가 +1ATR 이상 가는지 학습.
- 하방 유전자: 다음날 최저가 하락폭이 -0.7ATR 이내로 안전한지 학습.
- 최종 매수 후보: 상방 유전자 통과 AND 하방 유전자 통과.

진화 목적:
- 상방 유전자 적중률 70% 미만이면 무효.
- 하방 유전자 안전률 70% 미만이면 무효.
- 하방 유전자 위험 발생률 15% 초과이면 무효.
- 최종 겹침 신호는 생성 구간에서는 1일 이상만 요구하고, stress/oos에서 따로 확인한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_FULLFEATURE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga.py"
FEATURE_LOOKBACK_DAYS = 3
INVALID_FITNESS = -1e9
HIGH_TARGET_ATR = 1.0
LOW_SAFE_ATR = 0.7
BAD_LOW_ATR = 1.0
MIN_GENE_PRECISION = 70.0
MIN_FINAL_SIGNAL = 1


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
    spec = importlib.util.spec_from_file_location("fullfeature_stage2_ga_separate_highlow70", BASE_FULLFEATURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_FULLFEATURE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fullfeature_stage2_ga_separate_highlow70"] = mod
    spec.loader.exec_module(mod)
    return mod


def _empty_metric(period_df: Any, n: int) -> dict[str, Any]:
    return {
        "구간일수": n,
        "신호발생일": 0,
        "신호발생비율": 0.0,
        "목표달성일": 0,
        "적중률": 0.0,
        "위험발생일": 0,
        "위험발생률": 0.0,
        "전체목표발생률": float(period_df["GOOD_SIGNAL"].mean() * 100.0),
        "전체위험발생률": float(period_df["BAD_RISK"].mean() * 100.0),
        "평균상방폭": 0.0,
        "평균하방폭": 0.0,
        "평균보상폭": 0.0,
        "상방유전자_신호발생일": 0,
        "상방유전자_최고가돌파일": 0,
        "상방유전자_적중률": 0.0,
        "하방유전자_신호발생일": 0,
        "하방유전자_안전일": 0,
        "하방유전자_안전률": 0.0,
        "하방유전자_위험발생일": 0,
        "하방유전자_위험발생률": 0.0,
        "최종신호_최고가돌파일": 0,
        "최종신호_최고가돌파율": 0.0,
        "최종신호_최저가안전일": 0,
        "최종신호_최저가안전률": 0.0,
        "날짜": [],
    }


def make_separate_eval(mod: Any):
    def eval_period(ind: Any, qmat: np.ndarray, row_idx: np.ndarray, data: Any) -> dict[str, Any]:
        up_score = mod.gene_score(ind.up, qmat, row_idx)
        low_score = mod.gene_score(ind.low, qmat, row_idx)
        up_sig = up_score >= ind.up.cut
        low_sig = low_score >= ind.low.cut
        final_sig = up_sig & low_sig
        n = int(len(row_idx))
        period_df = data.iloc[row_idx]
        out = _empty_metric(period_df, n)

        next_high = period_df["next_high_atr"].astype(float).to_numpy()
        next_low = period_df["next_low_atr"].astype(float).to_numpy()
        payoff = period_df["PAYOFF_SCORE"].astype(float).to_numpy()
        high_hit = next_high >= HIGH_TARGET_ATR
        low_safe = next_low <= LOW_SAFE_ATR
        bad = next_low >= BAD_LOW_ATR
        good = high_hit & low_safe

        up_count = int(up_sig.sum())
        if up_count > 0:
            up_hits = int(high_hit[up_sig].sum())
            out["상방유전자_신호발생일"] = up_count
            out["상방유전자_최고가돌파일"] = up_hits
            out["상방유전자_적중률"] = float(up_hits / up_count * 100.0)

        low_count = int(low_sig.sum())
        if low_count > 0:
            low_hits = int(low_safe[low_sig].sum())
            low_bad = int(bad[low_sig].sum())
            out["하방유전자_신호발생일"] = low_count
            out["하방유전자_안전일"] = low_hits
            out["하방유전자_안전률"] = float(low_hits / low_count * 100.0)
            out["하방유전자_위험발생일"] = low_bad
            out["하방유전자_위험발생률"] = float(low_bad / low_count * 100.0)

        final_count = int(final_sig.sum())
        if final_count <= 0:
            return out
        sel = final_sig
        out["신호발생일"] = final_count
        out["신호발생비율"] = float(final_count / max(1, n) * 100.0)
        out["목표달성일"] = int(good[sel].sum())
        out["적중률"] = float(good[sel].mean() * 100.0)
        out["위험발생일"] = int(bad[sel].sum())
        out["위험발생률"] = float(bad[sel].mean() * 100.0)
        out["평균상방폭"] = float(next_high[sel].mean())
        out["평균하방폭"] = float(next_low[sel].mean())
        out["평균보상폭"] = float(payoff[sel].mean())
        out["최종신호_최고가돌파일"] = int(high_hit[sel].sum())
        out["최종신호_최고가돌파율"] = float(high_hit[sel].mean() * 100.0)
        out["최종신호_최저가안전일"] = int(low_safe[sel].sum())
        out["최종신호_최저가안전률"] = float(low_safe[sel].mean() * 100.0)
        out["날짜"] = [str(x)[:10] for x in period_df.iloc[np.where(sel)[0]]["date"].tolist()]
        return out
    return eval_period


def separate_highlow_fitness(m: dict[str, Any], ind: Any, args: Any) -> float:
    up_count = float(m["상방유전자_신호발생일"])
    low_count = float(m["하방유전자_신호발생일"])
    final_count = float(m["신호발생일"])
    up_precision = float(m["상방유전자_적중률"])
    low_safe = float(m["하방유전자_안전률"])
    low_bad = float(m["하방유전자_위험발생률"])
    final_precision = float(m["적중률"])
    final_bad = float(m["위험발생률"])
    payoff = float(m["평균보상폭"])

    if up_count < float(args.min_signal_count):
        return INVALID_FITNESS
    if low_count < float(args.min_signal_count):
        return INVALID_FITNESS
    if up_precision < MIN_GENE_PRECISION:
        return INVALID_FITNESS
    if low_safe < MIN_GENE_PRECISION:
        return INVALID_FITNESS
    if low_bad > float(args.max_bad_rate_pct):
        return INVALID_FITNESS
    if final_count < MIN_FINAL_SIGNAL:
        return INVALID_FITNESS
    if final_bad > float(args.max_bad_rate_pct):
        return INVALID_FITNESS

    active_total = int(ind.up.active.sum() + ind.low.active.sum())
    too_many_penalty = max(0, active_total - args.max_active_total) * args.active_count_penalty

    return float(
        up_precision * 5.0
        + low_safe * 4.0
        + final_precision * 6.0
        - low_bad * 7.0
        - final_bad * 10.0
        + payoff * 25.0
        + min(final_count, 20.0) * 1.0
        + min(up_count, 30.0) * 0.2
        + min(low_count, 30.0) * 0.2
        - too_many_penalty
    )


def main() -> int:
    mod = _load_fullfeature_module()
    mod.INVALID_FITNESS = INVALID_FITNESS
    mod.eval_period = make_separate_eval(mod)
    mod.source_fitness = separate_highlow_fitness

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
        argv += ["--out-dir", "exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_3d_separate_highlow70_20260706_001"]
    return int(mod.main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
