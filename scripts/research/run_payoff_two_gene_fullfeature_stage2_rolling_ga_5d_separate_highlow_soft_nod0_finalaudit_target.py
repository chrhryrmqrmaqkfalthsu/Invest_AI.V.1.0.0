#!/usr/bin/env python3
"""5일 lag + 전체 파라미터 2유전자 + D0 gap 제거 + soft fitness + 최종 검증 감사 wrapper.

목적:
- 장전 기준에서 누수 의심이 있는 D0 gap 계열 feature를 제거한다.
- stress는 검증 구간, oos는 최종 확인 구간으로 유지한다.
- 학습 구간 통과와 진짜 검증 통과를 분리해서 저장한다.
- 진짜 검증 통과는 stress/oos 각각 최소 신호 10일 이상을 요구한다.

구조:
- 상방 유전자: 다음날 최고가가 +N ATR 이상 가는 날을 찾는다.
- 하방 유전자: 다음날 저가 위험을 줄이는 필터 역할을 한다.
- 최종 매수 후보: 상방 유전자 통과 AND 하방 유전자 통과.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_FULLFEATURE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga.py"
FEATURE_LOOKBACK_DAYS = 5
TRAIN_EVALUATION_PERIODS = ["train1", "train2", "train3"]
VALIDATION_PERIOD = "stress"
FINAL_CHECK_PERIOD = "oos"
INVALID_FITNESS = -1e9
LOW_SAFE_ATR = 0.7
BAD_LOW_ATR = 1.0


def _load_fullfeature_module():
    spec = importlib.util.spec_from_file_location("fullfeature_soft_nod0_finalaudit", BASE_FULLFEATURE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_FULLFEATURE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fullfeature_soft_nod0_finalaudit"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_wrapper_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--high-target-atr", type=float, default=0.7)
    parser.add_argument("--final-min-signal-count", type=int, default=10)
    parser.add_argument("--keep-d0-gap", action="store_true", help="기본은 D0 gap 제거. 이 옵션을 주면 유지한다.")
    known, remaining = parser.parse_known_args(argv)
    return known, remaining


def _force_lookback_5(runner: Any) -> Any:
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


def _is_d0_gap_feature(name: str) -> bool:
    n = str(name).lower()
    return "gap_d0" in n or n.endswith("_d0") or "_d0_" in n


def make_soft_eval(full_mod: Any, high_target_atr: float):
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
        high_hit = next_high >= float(high_target_atr)
        low_safe = next_low <= LOW_SAFE_ATR
        bad = next_low >= BAD_LOW_ATR
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


def _safe_metric(row: dict[str, Any], period: str, key: str, default: float = 0.0) -> float:
    try:
        return float(row["구간별성능"][period].get(key, default))
    except Exception:
        return default


def _is_true_validation_pass(row: dict[str, Any], final_min_signal_count: int) -> bool:
    if not row.get("생존평가전체통과"):
        return False
    for period in (VALIDATION_PERIOD, FINAL_CHECK_PERIOD):
        signal_count = _safe_metric(row, period, "신호발생일")
        precision = _safe_metric(row, period, "적중률")
        base_precision = _safe_metric(row, period, "전체목표발생률")
        bad_rate = _safe_metric(row, period, "위험발생률")
        base_bad = _safe_metric(row, period, "전체위험발생률")
        payoff = _safe_metric(row, period, "평균보상폭")
        if signal_count < final_min_signal_count:
            return False
        if precision <= base_precision:
            return False
        if bad_rate > base_bad:
            return False
        if payoff <= 0.0:
            return False
    return True


def _validation_sort_key(row: dict[str, Any]) -> tuple[float, float, float, float]:
    stress_lift = _safe_metric(row, "stress", "적중률") - _safe_metric(row, "stress", "전체목표발생률")
    oos_lift = _safe_metric(row, "oos", "적중률") - _safe_metric(row, "oos", "전체목표발생률")
    stress_risk = _safe_metric(row, "stress", "전체위험발생률") - _safe_metric(row, "stress", "위험발생률")
    oos_risk = _safe_metric(row, "oos", "전체위험발생률") - _safe_metric(row, "oos", "위험발생률")
    payoff = _safe_metric(row, "stress", "평균보상폭") + _safe_metric(row, "oos", "평균보상폭")
    signals = _safe_metric(row, "stress", "신호발생일") + _safe_metric(row, "oos", "신호발생일")
    return (min(stress_lift, oos_lift), min(stress_risk, oos_risk), payoff, signals)


def _postprocess(out_dir: Path, final_min_signal_count: int, high_target_atr: float, keep_d0_gap: bool) -> None:
    summary_path = out_dir / "summary.json"
    candidates_path = out_dir / "all_candidates.jsonl"
    if not summary_path.exists() or not candidates_path.exists():
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    soft_pass = [r for r in rows if r.get("생존평가전체통과")]
    validation_pool = [
        r for r in soft_pass
        if _safe_metric(r, "stress", "신호발생일") >= final_min_signal_count
        and _safe_metric(r, "oos", "신호발생일") >= final_min_signal_count
    ]
    true_pass = [r for r in validation_pool if _is_true_validation_pass(r, final_min_signal_count)]
    validation_pool.sort(key=_validation_sort_key, reverse=True)
    true_pass.sort(key=_validation_sort_key, reverse=True)

    for r in rows:
        r["soft학습통과"] = bool(r.get("생존평가전체통과"))
        r["진짜검증통과"] = bool(_is_true_validation_pass(r, final_min_signal_count))

    candidates_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (out_dir / "true_validation_pass.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in true_pass), encoding="utf-8")
    (out_dir / "validation_pool_min_signal.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in validation_pool), encoding="utf-8")

    summary.update(
        {
            "상방ATR기준": high_target_atr,
            "D0_gap_제거": not keep_d0_gap,
            "판정명칭주의": "생존평가전체통과는 soft 학습 통과이며, 진짜 검증 통과가 아님",
            "진짜검증기준": {
                "검증구간": "stress",
                "최종확인구간": "oos",
                "각구간최소신호일": final_min_signal_count,
                "각구간적중률": "전체 기준 발생률 초과",
                "각구간위험발생률": "전체 위험 발생률 이하",
                "각구간평균보상폭": "0 초과",
                "학습통과필수": True,
            },
            "soft학습통과개체수": len(soft_pass),
            "검증최소신호충족개체수": len(validation_pool),
            "진짜검증통과개체수": len(true_pass),
            "검증최소신호충족_상위개체미리보기": [r for r in validation_pool[:10]],
            "진짜검증통과개체미리보기": [r for r in true_pass[:10]],
            "추가출력": {
                "true_validation_pass": str(out_dir / "true_validation_pass.jsonl"),
                "validation_pool_min_signal": str(out_dir / "validation_pool_min_signal.jsonl"),
            },
        }
    )
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    wrapper_args, remaining = _parse_wrapper_args(sys.argv[1:])
    high_target_atr = float(wrapper_args.high_target_atr)
    final_min_signal_count = int(wrapper_args.final_min_signal_count)
    keep_d0_gap = bool(wrapper_args.keep_d0_gap)

    full_mod = _load_fullfeature_module()
    full_mod.INVALID_FITNESS = INVALID_FITNESS
    full_mod.SURVIVAL_PERIODS = list(TRAIN_EVALUATION_PERIODS)
    full_mod.IMPORTANT_PERIODS = [VALIDATION_PERIOD, FINAL_CHECK_PERIOD]
    full_mod.eval_period = make_soft_eval(full_mod, high_target_atr)
    full_mod.source_fitness = soft_fitness

    original_load_base = full_mod.load_base

    def load_base_patched():
        base = original_load_base()
        original_load_runner = base.load_runner
        original_safe_features = base.safe_features

        def load_runner_lookback5():
            return _force_lookback_5(original_load_runner())

        def safe_features_nod0(raw_features: Any, df: Any):
            features, audit = original_safe_features(raw_features, df)
            removed = []
            kept = []
            for f in features:
                if (not keep_d0_gap) and _is_d0_gap_feature(str(f)):
                    removed.append(str(f))
                else:
                    kept.append(str(f))
            audit = dict(audit)
            audit["D0_gap_제거"] = not keep_d0_gap
            audit["D0_gap_제거파라미터수"] = len(removed)
            audit["D0_gap_제거파라미터"] = removed
            audit["최종파라미터수"] = len(kept)
            audit["진입시점가정"] = "장전 판단 기준: D0 gap 제거" if not keep_d0_gap else "장 시작 후 판단 기준: D0 gap 유지"
            return kept, audit

        base.load_runner = load_runner_lookback5
        base.safe_features = safe_features_nod0
        return base

    full_mod.load_base = load_base_patched

    target_tag = str(high_target_atr).replace(".", "p")
    default_out_dir = f"exp_hsbc_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_high{target_tag}_separate_highlow_soft_nod0_finalaudit_20260706_001"
    argv = list(remaining)
    if "--good-high-atr" not in argv:
        argv += ["--good-high-atr", str(high_target_atr)]
    if "--min-precision-pct" not in argv:
        argv += ["--min-precision-pct", "0"]
    if "--min-mean-precision-pct" not in argv:
        argv += ["--min-mean-precision-pct", "0"]
    if "--out-dir" not in argv:
        argv += ["--out-dir", default_out_dir]

    out_dir_value = argv[argv.index("--out-dir") + 1] if "--out-dir" in argv else default_out_dir
    rc = int(full_mod.main(argv))
    _postprocess(PROJECT_ROOT / out_dir_value, final_min_signal_count, high_target_atr, keep_d0_gap)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
