#!/usr/bin/env python3
"""5일 lag + 최고가/최저가 분리 학습 + soft fitness + stress 검증 전용 wrapper.

핵심 변경:
- 상방 유전자 적중률 70% 미만 즉시 무효를 제거한다.
- 진화 점수는 기준 대비 개선폭, 평균 보상폭, 위험률 감소를 중심으로 계산한다.
- stress는 학습/분위수 기준에서 제외하고 검증 구간으로만 사용한다.
- oos는 최종 확인 구간으로만 사용한다.

권장 실행:
  --high-target-atr 0.5
  --high-target-atr 0.7
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_SEPARATE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_3d_separate_highlow70.py"
FEATURE_LOOKBACK_DAYS = 5
TRAIN_EVALUATION_PERIODS = ["train1", "train2", "train3"]
VALIDATION_PERIOD = "stress"
FINAL_CHECK_PERIOD = "oos"
INVALID_FITNESS = -1e9


def _load_separate_module():
    spec = importlib.util.spec_from_file_location("separate_highlow_soft_5d_stress_validation_target", BASE_SEPARATE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_SEPARATE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["separate_highlow_soft_5d_stress_validation_target"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_wrapper_args(argv: list[str]) -> tuple[float, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--high-target-atr", type=float, default=0.5)
    known, remaining = parser.parse_known_args(argv)
    return float(known.high_target_atr), remaining


def make_soft_eval(separate_mod: Any, full_mod: Any):
    def eval_period(ind: Any, qmat: np.ndarray, row_idx: np.ndarray, data: Any) -> dict[str, Any]:
        up_score = full_mod.gene_score(ind.up, qmat, row_idx)
        low_score = full_mod.gene_score(ind.low, qmat, row_idx)
        up_sig = up_score >= ind.up.cut
        low_sig = low_score >= ind.low.cut
        final_sig = up_sig & low_sig
        n = int(len(row_idx))
        period_df = data.iloc[row_idx]

        next_high = period_df["next_high_atr"].astype(float).to_numpy()
        next_low = period_df["next_low_atr"].astype(float).to_numpy()
        payoff = period_df["PAYOFF_SCORE"].astype(float).to_numpy()
        high_hit = next_high >= float(separate_mod.HIGH_TARGET_ATR)
        low_safe = next_low <= float(separate_mod.LOW_SAFE_ATR)
        bad = next_low >= float(separate_mod.BAD_LOW_ATR)
        good = high_hit & low_safe

        base_high = float(high_hit.mean() * 100.0) if n else 0.0
        base_safe = float(low_safe.mean() * 100.0) if n else 0.0
        base_good = float(good.mean() * 100.0) if n else 0.0
        base_bad = float(bad.mean() * 100.0) if n else 0.0

        out: dict[str, Any] = {
            "구간일수": n,
            "신호발생일": 0,
            "신호발생비율": 0.0,
            "목표달성일": 0,
            "적중률": 0.0,
            "위험발생일": 0,
            "위험발생률": 0.0,
            "전체목표발생률": base_good,
            "전체상방발생률": base_high,
            "전체하방안전률": base_safe,
            "전체위험발생률": base_bad,
            "평균상방폭": 0.0,
            "평균하방폭": 0.0,
            "평균보상폭": 0.0,
            "상방유전자_신호발생일": 0,
            "상방유전자_최고가돌파일": 0,
            "상방유전자_적중률": 0.0,
            "상방유전자_기준대비개선": 0.0,
            "하방유전자_신호발생일": 0,
            "하방유전자_안전일": 0,
            "하방유전자_안전률": 0.0,
            "하방유전자_기준대비개선": 0.0,
            "하방유전자_위험발생일": 0,
            "하방유전자_위험발생률": 0.0,
            "하방유전자_위험감소": 0.0,
            "최종신호_최고가돌파일": 0,
            "최종신호_최고가돌파율": 0.0,
            "최종신호_최저가안전일": 0,
            "최종신호_최저가안전률": 0.0,
            "최종신호_기준대비개선": 0.0,
            "최종신호_위험감소": 0.0,
            "날짜": [],
        }

        up_count = int(up_sig.sum())
        if up_count > 0:
            up_hits = int(high_hit[up_sig].sum())
            up_precision = float(up_hits / up_count * 100.0)
            out["상방유전자_신호발생일"] = up_count
            out["상방유전자_최고가돌파일"] = up_hits
            out["상방유전자_적중률"] = up_precision
            out["상방유전자_기준대비개선"] = float(up_precision - base_high)

        low_count = int(low_sig.sum())
        if low_count > 0:
            low_hits = int(low_safe[low_sig].sum())
            low_bad = int(bad[low_sig].sum())
            low_safe_rate = float(low_hits / low_count * 100.0)
            low_bad_rate = float(low_bad / low_count * 100.0)
            out["하방유전자_신호발생일"] = low_count
            out["하방유전자_안전일"] = low_hits
            out["하방유전자_안전률"] = low_safe_rate
            out["하방유전자_기준대비개선"] = float(low_safe_rate - base_safe)
            out["하방유전자_위험발생일"] = low_bad
            out["하방유전자_위험발생률"] = low_bad_rate
            out["하방유전자_위험감소"] = float(base_bad - low_bad_rate)

        final_count = int(final_sig.sum())
        if final_count <= 0:
            return out
        sel = final_sig
        final_good = int(good[sel].sum())
        final_bad_hits = int(bad[sel].sum())
        final_precision = float(good[sel].mean() * 100.0)
        final_bad_rate = float(bad[sel].mean() * 100.0)
        out["신호발생일"] = final_count
        out["신호발생비율"] = float(final_count / max(1, n) * 100.0)
        out["목표달성일"] = final_good
        out["적중률"] = final_precision
        out["위험발생일"] = final_bad_hits
        out["위험발생률"] = final_bad_rate
        out["평균상방폭"] = float(next_high[sel].mean())
        out["평균하방폭"] = float(next_low[sel].mean())
        out["평균보상폭"] = float(payoff[sel].mean())
        out["최종신호_최고가돌파일"] = int(high_hit[sel].sum())
        out["최종신호_최고가돌파율"] = float(high_hit[sel].mean() * 100.0)
        out["최종신호_최저가안전일"] = int(low_safe[sel].sum())
        out["최종신호_최저가안전률"] = float(low_safe[sel].mean() * 100.0)
        out["최종신호_기준대비개선"] = float(final_precision - base_good)
        out["최종신호_위험감소"] = float(base_bad - final_bad_rate)
        out["날짜"] = [str(x)[:10] for x in period_df.iloc[np.where(sel)[0]]["date"].tolist()]
        return out
    return eval_period


def soft_fitness(m: dict[str, Any], ind: Any, args: Any) -> float:
    final_count = float(m["신호발생일"])
    final_rate = float(m["신호발생비율"])
    high_count = float(m["상방유전자_신호발생일"])
    low_count = float(m["하방유전자_신호발생일"])
    payoff = float(m["평균보상폭"])
    final_precision = float(m["적중률"])
    final_lift = float(m["최종신호_기준대비개선"])
    high_lift = float(m["상방유전자_기준대비개선"])
    low_risk_reduction = float(m["하방유전자_위험감소"])
    final_risk_reduction = float(m["최종신호_위험감소"])
    final_bad = float(m["위험발생률"])
    base_bad = float(m["전체위험발생률"])

    # 최소한 학습할 표본은 있어야 한다. 적중률 70% 같은 hard gate는 제거한다.
    if high_count < float(args.min_signal_count):
        return INVALID_FITNESS
    if low_count < float(args.min_signal_count):
        return INVALID_FITNESS
    if final_count < float(args.min_signal_count):
        return INVALID_FITNESS
    if final_rate < float(args.min_coverage_pct):
        return INVALID_FITNESS
    if final_rate > float(args.max_coverage_pct):
        return INVALID_FITNESS
    if payoff <= 0.0:
        return INVALID_FITNESS

    active_total = int(ind.up.active.sum() + ind.low.active.sum())
    too_many_penalty = max(0, active_total - args.max_active_total) * args.active_count_penalty
    risk_excess = max(0.0, final_bad - base_bad)
    weak_lift_penalty = max(0.0, -final_lift) * 6.0 + max(0.0, -high_lift) * 3.0
    signal_target = 8.0
    signal_shape_penalty = abs(final_rate - signal_target) * 0.15

    return float(
        final_lift * 8.0
        + high_lift * 5.0
        + final_precision * 1.2
        + payoff * 55.0
        + final_risk_reduction * 6.0
        + low_risk_reduction * 3.0
        + min(final_count, 25.0) * 0.8
        - risk_excess * 10.0
        - weak_lift_penalty
        - signal_shape_penalty
        - too_many_penalty
    )


def main() -> int:
    high_target_atr, remaining = _parse_wrapper_args(sys.argv[1:])
    separate_mod = _load_separate_module()
    separate_mod.FEATURE_LOOKBACK_DAYS = FEATURE_LOOKBACK_DAYS
    separate_mod.HIGH_TARGET_ATR = float(high_target_atr)
    separate_mod.MIN_GENE_PRECISION = 0.0
    separate_mod.MIN_FINAL_SIGNAL = 5

    original_load_fullfeature_module = separate_mod._load_fullfeature_module

    def load_fullfeature_module_soft_stress_validation():
        full_mod = original_load_fullfeature_module()
        full_mod.SURVIVAL_PERIODS = list(TRAIN_EVALUATION_PERIODS)
        full_mod.IMPORTANT_PERIODS = [VALIDATION_PERIOD, FINAL_CHECK_PERIOD]
        full_mod.INVALID_FITNESS = INVALID_FITNESS
        full_mod.eval_period = make_soft_eval(separate_mod, full_mod)
        full_mod.source_fitness = soft_fitness
        return full_mod

    separate_mod._load_fullfeature_module = load_fullfeature_module_soft_stress_validation

    target_tag = str(high_target_atr).replace(".", "p")
    default_out_dir = f"exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_high{target_tag}_separate_highlow_soft_stress_validation_20260706_001"
    argv = list(remaining)
    if "--good-high-atr" not in argv:
        argv += ["--good-high-atr", str(high_target_atr)]
    if "--min-precision-pct" not in argv:
        argv += ["--min-precision-pct", "0"]
    if "--min-mean-precision-pct" not in argv:
        argv += ["--min-mean-precision-pct", "0"]
    if "--out-dir" not in argv:
        argv += ["--out-dir", default_out_dir]
    sys.argv = [sys.argv[0], *argv]
    return int(separate_mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
