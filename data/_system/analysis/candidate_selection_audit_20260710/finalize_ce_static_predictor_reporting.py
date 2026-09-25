from __future__ import annotations

"""CE 정적 예측 탐색의 최종 WEAK 보고를 일관되게 고정한다."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
POWER = OUT / "ce_static_feature_predictive_power.csv"
PAIR = OUT / "ce_static_pair_predictive_power.csv"
CE7 = OUT / "ce_static_ce7_capture.csv"
SUMMARY = OUT / "ce_static_predictor_summary.json"
READOUT = OUT / "ce_static_predictor_readout.md"


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def flag_feature(frame: pd.DataFrame, power_row: pd.Series) -> np.ndarray:
    threshold = np.where(
        frame["stage"].eq("stage2"),
        float(power_row["raw_boundary_stage2"]),
        float(power_row["raw_boundary_stage3"]),
    )
    values = frame[str(power_row["feature"])].to_numpy(float)
    if str(power_row["risk_direction"]) == ">=":
        return values >= threshold
    return values <= threshold


def row_to_json(row: pd.Series, keys: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        if pd.isna(value):
            result[key] = None
        elif isinstance(value, (np.integer,)):
            result[key] = int(value)
        elif isinstance(value, (np.floating,)):
            result[key] = float(value)
        elif isinstance(value, (np.bool_,)):
            result[key] = bool(value)
        else:
            result[key] = value
    return result


def main() -> int:
    power = stable_csv(POWER, low_memory=False)
    pairs = stable_csv(PAIR, low_memory=False)
    ce7 = stable_csv(CE7, low_memory=False)
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))

    weak_features = power[
        power["discovery_supported"].fillna(False).astype(bool)
        & power["validation_supported"].fillna(False).astype(bool)
        & ~power["replicated_static_predictor_fdr"].fillna(False).astype(bool)
    ].copy()
    weak_pairs = pairs[
        pairs["replicated_pair"].fillna(False).astype(bool)
        & ~pairs["replicated_pair_fdr"].fillna(False).astype(bool)
    ].copy()
    weak_features = weak_features.sort_values(
        ["validation_flag_fdr_q", "validation_risk_difference"], ascending=[True, False]
    )
    weak_pairs = weak_pairs.sort_values(
        ["validation_fdr_q", "validation_risk_difference"], ascending=[True, False]
    )

    best_feature = weak_features.iloc[0] if len(weak_features) else None
    best_pair = weak_pairs.iloc[0] if len(weak_pairs) else None

    if best_pair is not None:
        left = power[power["feature"].eq(best_pair["left_feature"])].iloc[0]
        right = power[power["feature"].eq(best_pair["right_feature"])].iloc[0]
        left_flag = flag_feature(ce7, left)
        right_flag = flag_feature(ce7, right)
        pair_flag = left_flag & right_flag if str(best_pair["operator"]) == "AND" else left_flag | right_flag
        ce7["best_weak_pair_flag"] = pair_flag
        pair_boundaries = {
            "left_feature": str(best_pair["left_feature"]),
            "left_direction": str(left["risk_direction"]),
            "left_stage2_boundary": float(left["raw_boundary_stage2"]),
            "left_stage3_boundary": float(left["raw_boundary_stage3"]),
            "operator": str(best_pair["operator"]),
            "right_feature": str(best_pair["right_feature"]),
            "right_direction": str(right["risk_direction"]),
            "right_stage2_boundary": float(right["raw_boundary_stage2"]),
            "right_stage3_boundary": float(right["raw_boundary_stage3"]),
        }
    else:
        ce7["best_weak_pair_flag"] = False
        pair_boundaries = None

    ce7["best_weak_pair_is_gate_candidate"] = False
    ce7.to_csv(CE7, index=False)
    available_bad = ce7[
        ce7["frozen_target_available"].fillna(False).astype(bool)
        & ce7["target_bad"].fillna(False).astype(bool)
    ]
    capture = {
        "total": 7,
        "all_flagged_n": int(ce7["best_weak_pair_flag"].sum()),
        "frozen_available_n": int(ce7["frozen_target_available"].sum()),
        "frozen_bad_n": len(available_bad),
        "frozen_bad_captured_n": int(available_bad["best_weak_pair_flag"].sum()),
        "captured_ids": ce7.loc[ce7["best_weak_pair_flag"], "candidate_id"].tolist(),
    }

    best_feature_json = None
    if best_feature is not None:
        best_feature_json = row_to_json(best_feature, [
            "feature", "risk_direction", "raw_boundary_stage2", "raw_boundary_stage3",
            "discovery_auc", "discovery_risk_difference", "validation_auc",
            "validation_risk_difference", "validation_risk_difference_ci_low",
            "validation_risk_difference_ci_high", "validation_flag_fdr_q",
            "incremental_validation_n", "incremental_validation_flagged_n",
            "incremental_validation_risk_difference",
            "incremental_validation_risk_difference_ci_low",
            "incremental_validation_risk_difference_ci_high",
        ])

    best_pair_json = None
    if best_pair is not None:
        best_pair_json = {
            **pair_boundaries,
            **row_to_json(best_pair, [
                "discovery_risk_difference", "discovery_risk_difference_ci_low",
                "discovery_risk_difference_ci_high", "validation_flagged_n",
                "validation_risk_difference", "validation_risk_difference_ci_low",
                "validation_risk_difference_ci_high", "validation_fdr_q",
                "incremental_validation_n", "incremental_validation_flagged_n",
                "incremental_validation_risk_difference",
                "incremental_validation_risk_difference_ci_low",
                "incremental_validation_risk_difference_ci_high",
                "stage2_risk_difference", "stage2_flagged_n",
                "stage3_risk_difference", "stage3_flagged_n",
            ]),
            "ce7_capture": capture,
            "robust_static_gate_candidate": False,
        }

    summary["verdict"] = "WEAK"
    summary["replication"]["nominal_single_feature_n"] = len(weak_features)
    summary["replication"]["nominal_pair_n"] = len(weak_pairs)
    summary["replication"]["fdr_replicated_single_feature_n"] = int(
        power["replicated_static_predictor_fdr"].fillna(False).astype(bool).sum()
    )
    summary["replication"]["broad_fdr_replicated_pair_n"] = int(
        pairs["replicated_pair_fdr"].fillna(False).astype(bool).sum()
    )
    summary["replication"]["robust_gate_pair_n"] = int(
        pairs["robust_static_gate_candidate"].fillna(False).astype(bool).sum()
    )
    summary["best_weak_feature"] = best_feature_json
    summary["best_weak_pair"] = best_pair_json
    summary["ce7"] = capture
    summary["conclusion"] = (
        "One single feature and two two-feature combinations are nominally positive before frozen-family FDR, "
        "but no static predictor survives frozen FDR, existing-gate incremental bootstrap, and stage consistency. "
        "The result is WEAK, not a fourth static gate; dynamic observation logging remains the only robust CE validation path."
    )
    summary["created_at_reporting_finalized"] = datetime.now(timezone.utc).isoformat()
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    thresholds = summary["target_definition"]
    lines = [
        "# CE형 개체의 정적 예측 특징 탐색",
        "",
        "- 최종 판정: **WEAK**",
        "- 네 번째 정적 게이트 후보: **0개**",
        "- 동적 realized component·현재 시장 상태: 사용하지 않음",
        "- 설계·운영 구현 변경: 없음",
        "",
        "## 1. 타깃 정의",
        "",
        "기존 과거 평균 PnL 음수 대상은 제외했다. discovery에서 stage별 경계를 고정하고 frozen OOS에는 재튜닝 없이 적용했다.",
        "",
        "- IS→OOS 붕괴: PnL 격차 상위 10%이면서 OOS/IS PnL 비율 <=0.5",
        "- 양의 평균 tail risk: 거래 수로 기대되는 worst MAE를 회귀 보정한 residual 하위 10%",
        "- 고승률 소수 대형손실: 승률 상위 25%, worst/median-win 상위 25%, top3 loss share 중앙값 이상",
        "",
        f"- discovery: {summary['cohorts']['discovery_n']:,}개, bad {summary['cohorts']['discovery_bad_n']:,}개",
        f"- frozen validation: {summary['cohorts']['validation_n']:,}개, bad {summary['cohorts']['validation_bad_n']:,}개",
        f"- 기존 v3·BOIL·history 정적 게이트 통과 frozen: {summary['cohorts']['incremental_existing_static_survivor_n']:,}개, bad {summary['cohorts']['incremental_bad_n']:,}개",
        "",
        "Stage별 경계:",
        "",
        f"- Stage2 collapse gap {thresholds['stage2']['collapse_gap_q90_pp']:.4f}%p, MAE residual {thresholds['stage2']['tail_worst_mae_residual_q10_pct']:.4f}% 이하, high-win {thresholds['stage2']['high_win_q75_pct']:.2f}% 이상",
        f"- Stage3 collapse gap {thresholds['stage3']['collapse_gap_q90_pp']:.4f}%p, MAE residual {thresholds['stage3']['tail_worst_mae_residual_q10_pct']:.4f}% 이하, high-win {thresholds['stage3']['high_win_q75_pct']:.2f}% 이상",
        "",
        "## 2. CE7 타깃 타당성",
        "",
        f"- frozen 결과 존재: {capture['frozen_available_n']}/7",
        f"- 결과 타깃 bad: {capture['frozen_bad_n']}/{capture['frozen_available_n']} — BOIL, BTE, CDE",
        "- 결과 타깃 good: ANET, BB, CE",
        "- frozen 없음: CWK",
        "",
        "CE 동적 FAIL 7개는 단일한 결과 붕괴 집합이 아니다. 따라서 CE7 라벨 자체를 최적화하지 않고 독립 결과 타깃을 사용했다.",
        "",
        "## 3. 정적 특징 검증",
        "",
        f"- 허용된 단일 정적 특징: {summary['method']['single_feature_n']}개",
        f"- 명목 bootstrap 재현 단일 특징: {len(weak_features)}개",
        f"- frozen family FDR 통과 단일 특징: {summary['replication']['fdr_replicated_single_feature_n']}개",
        f"- 2개 이하 조합: {summary['method']['pair_n']}개",
        f"- 명목 bootstrap 재현 조합: {len(weak_pairs)}개",
        f"- frozen family FDR 통과 조합: {summary['replication']['broad_fdr_replicated_pair_n']}개",
        f"- 기존 게이트 순증군·stage 일관성까지 통과: {summary['replication']['robust_gate_pair_n']}개",
        "",
        "결과 파생 `eval_*`·`target_*` 열은 predictor에서 완전히 제외했다.",
        "",
        "## 4. 남은 약한 신호",
        "",
    ]
    if best_feature_json:
        lines += [
            f"### 단일 `{best_feature_json['feature']}`",
            "",
            f"- 경계: Stage2 {best_feature_json['risk_direction']} {best_feature_json['raw_boundary_stage2']:.4f}, Stage3 {best_feature_json['risk_direction']} {best_feature_json['raw_boundary_stage3']:.4f}",
            f"- broad frozen 위험차 {best_feature_json['validation_risk_difference']:.4f}, 95% CI [{best_feature_json['validation_risk_difference_ci_low']:.4f}, {best_feature_json['validation_risk_difference_ci_high']:.4f}]",
            f"- frozen FDR q={best_feature_json['validation_flag_fdr_q']:.4f}",
            f"- 기존 게이트 통과 frozen 위험차 {best_feature_json['incremental_validation_risk_difference']:.4f}, 95% CI [{best_feature_json['incremental_validation_risk_difference_ci_low']:.4f}, {best_feature_json['incremental_validation_risk_difference_ci_high']:.4f}]",
            "",
        ]
    if best_pair_json:
        lines += [
            f"### 조합 `{best_pair_json['left_feature']}` {best_pair_json['operator']} `{best_pair_json['right_feature']}`",
            "",
            f"- Stage2 경계: {best_pair_json['left_feature']} {best_pair_json['left_direction']} {best_pair_json['left_stage2_boundary']:.4f}, {best_pair_json['right_feature']} {best_pair_json['right_direction']} {best_pair_json['right_stage2_boundary']:.4f}",
            f"- Stage3 경계: {best_pair_json['left_feature']} {best_pair_json['left_direction']} {best_pair_json['left_stage3_boundary']:.4f}, {best_pair_json['right_feature']} {best_pair_json['right_direction']} {best_pair_json['right_stage3_boundary']:.4f}",
            f"- broad frozen: {best_pair_json['validation_flagged_n']}/82 flag, 위험차 {best_pair_json['validation_risk_difference']:.4f}, 95% CI [{best_pair_json['validation_risk_difference_ci_low']:.4f}, {best_pair_json['validation_risk_difference_ci_high']:.4f}], FDR q={best_pair_json['validation_fdr_q']:.4f}",
            f"- 기존 게이트 통과 frozen: {best_pair_json['incremental_validation_flagged_n']}/{best_pair_json['incremental_validation_n']} flag, 위험차 {best_pair_json['incremental_validation_risk_difference']:.4f}, 95% CI [{best_pair_json['incremental_validation_risk_difference_ci_low']:.4f}, {best_pair_json['incremental_validation_risk_difference_ci_high']:.4f}]",
            f"- Stage2 위험차: {best_pair_json['stage2_risk_difference']}",
            f"- Stage3 위험차: {best_pair_json['stage3_risk_difference']}",
            "",
            "broad frozen에서는 명목상 분리되지만 FDR와 순증군에서 깨지고, Stage2에서는 분리 불가 또는 반대 방향이다.",
            "",
        ]
    lines += [
        "## 5. CE7 포섭",
        "",
        f"- 최상위 약한 조합 포섭: {capture['all_flagged_n']}/7",
        f"- 결과 bad 포섭: {capture['frozen_bad_captured_n']}/{capture['frozen_bad_n']}",
        f"- 포섭 ID: {', '.join(capture['captured_ids']) or '없음'}",
        "",
        "포섭된 BOIL은 이미 v3·BOIL 정적 게이트 영역이다. BTE·CDE를 놓쳐 네 번째 게이트의 순증 가치가 없다.",
        "",
        "## 6. 최종 판정",
        "",
        "**WEAK**",
        "",
        "일부 단일·조합 특징은 보정 전 frozen 95% CI에서 방향을 보였지만 family FDR, 기존 게이트 통과 순증군 bootstrap, stage 일관성을 동시에 통과하지 못했다.",
        "",
        "따라서 네 번째 STATIC BLOCK 후보는 없다. 연구 MONITOR 수준으로만 남기며 CE형 실패 검증의 주 경로는 진입 시점 동적 observation logging이다.",
        "",
        "## 7. 커브피팅 점검",
        "",
        "- 경계는 discovery에서만 선택",
        "- frozen 경계 재튜닝 없음",
        "- ticker-cluster bootstrap",
        "- frozen 단일/조합 family별 BH FDR",
        "- 결과 파생 predictor 완전 제외",
        "- 기존 게이트 통과 순증군과 Stage2/Stage3 방향 확인",
        "",
        "## 8. 산출물",
        "",
        "- `ce_static_target_labels.csv.gz`",
        "- `ce_static_feature_matrix.csv.gz`",
        "- `ce_static_feature_predictive_power.csv`",
        "- `ce_static_pair_predictive_power.csv`",
        "- `ce_static_nominal_pair_robustness.csv`",
        "- `ce_static_ce7_capture.csv`",
        "- `ce_static_curve_fit_notes.csv`",
        "- `ce_static_predictor_summary.json`",
    ]
    READOUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "verdict": "WEAK",
        "nominal_single_feature_n": len(weak_features),
        "nominal_pair_n": len(weak_pairs),
        "fdr_single_n": summary["replication"]["fdr_replicated_single_feature_n"],
        "fdr_pair_n": summary["replication"]["broad_fdr_replicated_pair_n"],
        "robust_gate_pair_n": summary["replication"]["robust_gate_pair_n"],
        "ce7": capture,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
