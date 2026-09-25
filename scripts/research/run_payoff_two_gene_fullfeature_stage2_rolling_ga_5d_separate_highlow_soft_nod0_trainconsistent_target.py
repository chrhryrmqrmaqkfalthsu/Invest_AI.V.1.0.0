#!/usr/bin/env python3
"""D0 gap 제거 soft GA에 학습구간 일관성 판정을 추가한 wrapper.

기존 nod0 finalaudit wrapper 위에 아래 최종 판정 조건을 추가한다.
- 학습1/학습2/학습3 각각 적중률이 해당 구간 전체 기준 발생률을 초과해야 한다.
- 학습1/학습2/학습3 각각 평균 보상폭이 0을 초과해야 한다.
- stress/oos는 기존처럼 각각 최소 신호 10일 이상, 기준 대비 적중률 우위, 위험률 기준 이하, 평균 보상폭 0 초과를 요구한다.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_WRAPPER_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_separate_highlow_soft_nod0_finalaudit_target.py"
DEFAULT_TAG = "trainconsistent"


def _load_base_wrapper():
    spec = importlib.util.spec_from_file_location("nod0_finalaudit_trainconsistent", BASE_WRAPPER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_WRAPPER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nod0_finalaudit_trainconsistent"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse_wrapper_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--high-target-atr", type=float, default=0.7)
    parser.add_argument("--ticker", default="HSBC")
    known, remaining = parser.parse_known_args(argv)
    return known, remaining


def _safe_metric(row: dict[str, Any], period: str, key: str, default: float = 0.0) -> float:
    try:
        return float(row["구간별성능"][period].get(key, default))
    except Exception:
        return default


def _train_consistency_pass(row: dict[str, Any], train_periods: list[str]) -> bool:
    if not row.get("생존평가전체통과"):
        return False
    for period in train_periods:
        precision = _safe_metric(row, period, "적중률")
        base_precision = _safe_metric(row, period, "전체목표발생률")
        payoff = _safe_metric(row, period, "평균보상폭")
        if precision <= base_precision:
            return False
        if payoff <= 0.0:
            return False
    return True


def main() -> int:
    wrapper_args, _remaining = _parse_wrapper_args(sys.argv[1:])
    high_target_atr = float(wrapper_args.high_target_atr)
    ticker = str(wrapper_args.ticker).upper()
    target_tag = str(high_target_atr).replace(".", "p")

    argv = list(sys.argv[1:])
    if "--out-dir" not in argv:
        argv += [
            "--out-dir",
            f"exp_{ticker.lower()}_payoff_two_gene_fullfeature_stage2_rolling_ga_5d_high{target_tag}_separate_highlow_soft_nod0_{DEFAULT_TAG}_20260706_001",
        ]
    sys.argv = [sys.argv[0], *argv]

    base_mod = _load_base_wrapper()
    train_periods = list(getattr(base_mod, "TRAIN_EVALUATION_PERIODS", ["train1", "train2", "train3"]))
    validation_period = str(getattr(base_mod, "VALIDATION_PERIOD", "stress"))
    final_period = str(getattr(base_mod, "FINAL_CHECK_PERIOD", "oos"))

    def strict_true_validation_pass(row: dict[str, Any], final_min_signal_count: int) -> bool:
        if not _train_consistency_pass(row, train_periods):
            return False
        for period in (validation_period, final_period):
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

    original_postprocess = base_mod._postprocess

    def postprocess_with_train_consistency(out_dir: Path, final_min_signal_count: int, high_target_atr_value: float, keep_d0_gap: bool) -> None:
        original_postprocess(out_dir, final_min_signal_count, high_target_atr_value, keep_d0_gap)
        summary_path = out_dir / "summary.json"
        candidates_path = out_dir / "all_candidates.jsonl"
        if not summary_path.exists() or not candidates_path.exists():
            return
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in candidates_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        train_consistent = [r for r in rows if _train_consistency_pass(r, train_periods)]
        true_pass = [r for r in rows if strict_true_validation_pass(r, final_min_signal_count)]

        for r in rows:
            r["학습일관성통과"] = bool(_train_consistency_pass(r, train_periods))
            r["진짜검증통과"] = bool(strict_true_validation_pass(r, final_min_signal_count))

        candidates_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        (out_dir / "train_consistency_pass.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train_consistent), encoding="utf-8")
        (out_dir / "true_validation_pass.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in true_pass), encoding="utf-8")

        def sort_key(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
            lifts = [_safe_metric(row, p, "적중률") - _safe_metric(row, p, "전체목표발생률") for p in [*train_periods, validation_period, final_period]]
            risks = [_safe_metric(row, p, "전체위험발생률") - _safe_metric(row, p, "위험발생률") for p in [validation_period, final_period]]
            payoffs = [_safe_metric(row, p, "평균보상폭") for p in [*train_periods, validation_period, final_period]]
            signals = [_safe_metric(row, p, "신호발생일") for p in [validation_period, final_period]]
            return (min(lifts), min(risks), min(payoffs), sum(payoffs), sum(signals))

        train_consistent.sort(key=sort_key, reverse=True)
        true_pass.sort(key=sort_key, reverse=True)
        summary.update(
            {
                "판정명칭주의": "생존평가전체통과는 soft 학습 통과이며, 학습일관성통과와 진짜검증통과를 별도로 봐야 함",
                "학습일관성기준": {
                    "학습구간": train_periods,
                    "각학습구간적중률": "해당 구간 전체 기준 발생률 초과",
                    "각학습구간평균보상폭": "0 초과",
                    "soft학습통과필수": True,
                },
                "진짜검증기준": {
                    "학습구간": train_periods,
                    "각학습구간적중률": "해당 구간 전체 기준 발생률 초과",
                    "각학습구간평균보상폭": "0 초과",
                    "검증구간": validation_period,
                    "최종확인구간": final_period,
                    "검증최종확인_각구간최소신호일": final_min_signal_count,
                    "검증최종확인_각구간적중률": "전체 기준 발생률 초과",
                    "검증최종확인_각구간위험발생률": "전체 위험 발생률 이하",
                    "검증최종확인_각구간평균보상폭": "0 초과",
                },
                "학습일관성통과개체수": len(train_consistent),
                "진짜검증통과개체수": len(true_pass),
                "학습일관성통과개체미리보기": train_consistent[:10],
                "진짜검증통과개체미리보기": true_pass[:10],
                "추가출력": {
                    **summary.get("추가출력", {}),
                    "train_consistency_pass": str(out_dir / "train_consistency_pass.jsonl"),
                    "true_validation_pass": str(out_dir / "true_validation_pass.jsonl"),
                },
            }
        )
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    base_mod._is_true_validation_pass = strict_true_validation_pass
    base_mod._postprocess = postprocess_with_train_consistency
    return int(base_mod.main())


if __name__ == "__main__":
    raise SystemExit(main())
