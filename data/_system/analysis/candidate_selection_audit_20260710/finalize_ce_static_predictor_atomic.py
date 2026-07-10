from __future__ import annotations

"""CE 정적 predictor 원자적 최종화.

1) split/stage 상대 결과 타깃 재계산
2) eval/target/history outcome 누수 컬럼 제외
3) discovery 경계 고정 후 frozen bootstrap+FDR
4) 모든 산출물을 같은 실행에서 기록
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
if str(OUT) not in sys.path:
    sys.path.insert(0, str(OUT))

import run_ce_static_predictor_search as core  # noqa: E402
import run_ce_static_predictor_search_v2 as v2  # noqa: E402
import finalize_ce_static_predictor_relative_outcome as rel  # noqa: E402
import finalize_ce_static_predictor_no_leakage as noleak  # noqa: E402


def main() -> int:
    matrix_raw = core.stable_csv(core.FEATURE_MATRIX_OUT, low_memory=False)
    discovery_raw = matrix_raw[matrix_raw["matrix_split"].eq("INTERNAL_DISCOVERY")].copy().reset_index(drop=True)
    validation_raw = matrix_raw[matrix_raw["matrix_split"].eq("FROZEN_OOS_VALIDATION")].copy().reset_index(drop=True)
    ce7_base = core.stable_csv(OUT / "ce_origin_fail_rejudged.csv", usecols=["candidate_id", "stage", "ticker"])

    discovery_thresholds = rel.fit_relative_thresholds(discovery_raw)
    validation_thresholds = rel.fit_relative_thresholds(validation_raw)
    discovery = rel.apply_relative_target(discovery_raw, discovery_thresholds)
    validation = rel.apply_relative_target(validation_raw, validation_thresholds)

    matrix = pd.concat([discovery, validation], ignore_index=True)
    matrix.to_csv(core.FEATURE_MATRIX_OUT, index=False, compression="gzip")
    target_columns = [
        "matrix_split", "candidate_id", "stage", "ticker", "rulebook_hash", "activity_rule_hash",
        "recommended_static_status", "final_p99_weightless_block_status", "boil_block", "history_eligible",
        "existing_static_survivor", "base_n", "base_avg_pnl_pct", "base_win_rate_pct", "eval_n",
        "eval_avg_pnl_pct", "eval_win_rate_pct", "eval_min_pnl_pct", "eval_p05_pnl_pct", "eval_avg_mae_pct",
        "eval_worst_mae_pct", "eval_worst_mae_expected_for_n_pct", "eval_worst_mae_sample_adjusted_residual_pct",
        "eval_avg_mfe_pct", "eval_worst_to_median_win", "eval_top3_loss_share", "eval_max_loss_share",
        "is_oos_gap_pp", "oos_to_is_pnl_ratio", "target_is_oos_collapse", "target_positive_tail_risk",
        "target_high_win_large_loss", "target_bad", "target_reason",
    ]
    pd.concat([
        discovery[target_columns].rename(columns={"matrix_split": "evaluation_split"}),
        validation[target_columns].rename(columns={"matrix_split": "evaluation_split"}),
    ], ignore_index=True).to_csv(core.TARGET_OUT, index=False, compression="gzip")

    features = noleak.pure_static_features(discovery, validation)
    forbidden = [feature for feature in features if feature.startswith(("eval_", "target_", "is_oos_", "oos_to_is_"))]
    if forbidden:
        raise AssertionError(f"outcome leakage features remained: {forbidden}")

    power, fitted = core.univariate_analysis(discovery, validation, features)
    power = v2.add_incremental_metrics(power, fitted, validation)
    power = rel.adjust_univariate(power, fitted, discovery, validation)
    power.to_csv(core.FEATURE_POWER_OUT, index=False)

    pairs = core.pair_analysis(discovery, validation, power, fitted)
    pairs = rel.adjust_pairs(pairs, fitted, discovery, validation)
    pairs.to_csv(core.PAIR_POWER_OUT, index=False)

    replicated_features = power[power["replicated_static_predictor_fdr"]]
    replicated_pairs = pairs[pairs["replicated_pair_fdr"]] if not pairs.empty else pairs
    unadjusted_features = power[power["discovery_supported"] & power["validation_supported"]]
    unadjusted_pairs = pairs[pairs["replicated_pair"]] if not pairs.empty else pairs
    directional = power[
        power["discovery_supported"]
        & power["validation_auc"].ge(0.55)
        & power["validation_risk_difference"].gt(0)
    ]
    if len(replicated_features) or len(replicated_pairs):
        verdict = "STATIC_PREDICTOR_FOUND"
    elif len(unadjusted_features) or len(unadjusted_pairs) or len(directional):
        verdict = "WEAK"
    else:
        verdict = "NO_STATIC_PREDICTOR"

    ce7 = ce7_base.merge(
        validation[["candidate_id", "target_bad", "target_reason", "history_eligible", "existing_static_survivor"]],
        on="candidate_id", how="left", validate="one_to_one",
    )
    feature_values = matrix.drop_duplicates("candidate_id")
    ce7 = ce7.merge(
        feature_values.drop(columns=[c for c in feature_values.columns if c in ce7.columns and c != "candidate_id"]),
        on="candidate_id", how="left", validate="one_to_one",
    )
    ce7["frozen_target_available"] = ce7["target_bad"].notna()
    for feature in power["feature"].head(10):
        boundary = fitted[feature][0]
        flags = []
        for row in ce7.itertuples(index=False):
            value = core.safe_float(getattr(row, feature))
            threshold = core.safe_float(boundary.raw_thresholds.get(str(row.stage)))
            if not math.isfinite(value) or not math.isfinite(threshold):
                flags.append(False)
            elif boundary.direction == ">=":
                flags.append(value >= threshold)
            else:
                flags.append(value <= threshold)
        ce7[f"flag_{feature}"] = flags
    ce7.to_csv(core.CE7_OUT, index=False)

    curve_rows = []
    for row in power.itertuples(index=False):
        if row.discovery_supported and not row.replicated_static_predictor_fdr:
            curve_rows.append({
                "type": "IS_ONLY_OR_UNCORRECTED_FEATURE", "feature_or_pair": row.feature,
                "discovery_auc": row.discovery_auc, "discovery_risk_difference": row.discovery_risk_difference,
                "validation_auc": row.validation_auc, "validation_risk_difference": row.validation_risk_difference,
                "validation_ci_low": row.validation_risk_difference_ci_low,
                "validation_fdr_q": row.validation_flag_fdr_q,
                "note": "failed frozen CI and/or FDR",
            })
    if not pairs.empty:
        for row in pairs.itertuples(index=False):
            if row.discovery_risk_difference_ci_low > 0 and not row.replicated_pair_fdr:
                curve_rows.append({
                    "type": "IS_ONLY_OR_UNCORRECTED_PAIR",
                    "feature_or_pair": f"{row.left_feature} {row.operator} {row.right_feature}",
                    "discovery_auc": math.nan, "discovery_risk_difference": row.discovery_risk_difference,
                    "validation_auc": math.nan, "validation_risk_difference": row.validation_risk_difference,
                    "validation_ci_low": row.validation_risk_difference_ci_low,
                    "validation_fdr_q": row.validation_fdr_q,
                    "note": "failed frozen CI and/or pair-search FDR",
                })
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(core.CURVE_FIT_OUT, index=False)

    frozen_ce = ce7[ce7["frozen_target_available"]].copy()
    best = power.iloc[0] if len(power) else None
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "analysis_version": "v6-atomic-relative-target-no-leakage-final",
        "verdict": verdict,
        "method": {
            "discovery_n": len(discovery),
            "validation_n": len(validation),
            "target": "same split/stage-relative collapse, sample-adjusted tail, high-win large-loss definition",
            "predictor_threshold": "discovery only; frozen no retuning",
            "pure_static_feature_rule": "eval_*, target_*, IS-OOS gap and raw history outcomes prohibited",
            "features_tested": len(power),
            "pairs_tested": len(pairs),
            "bootstrap": f"ticker-cluster {core.BOOTSTRAP_REPS} reps",
            "multiple_testing": "Benjamini-Hochberg FDR on frozen single and pair families",
            "atomic_write": True,
        },
        "target_thresholds": {
            "discovery": discovery_thresholds,
            "frozen_validation": validation_thresholds,
        },
        "cohorts": {
            "discovery_bad_n": int(discovery["target_bad"].sum()),
            "discovery_bad_rate_pct": float(discovery["target_bad"].mean() * 100),
            "validation_bad_n": int(validation["target_bad"].sum()),
            "validation_bad_rate_pct": float(validation["target_bad"].mean() * 100),
            "incremental_existing_static_survivor_n": int(validation["existing_static_survivor"].sum()),
            "incremental_bad_n": int(validation.loc[validation["existing_static_survivor"], "target_bad"].sum()),
        },
        "replication": {
            "fdr_replicated_feature_n": len(replicated_features),
            "fdr_replicated_pair_n": len(replicated_pairs),
            "unadjusted_feature_hit_n": len(unadjusted_features),
            "unadjusted_pair_hit_n": len(unadjusted_pairs),
            "directional_weak_n": len(directional),
            "is_only_or_uncorrected_n": len(curve),
        },
        "best_feature": None if best is None else best.to_dict(),
        "ce7": {
            "total": 7,
            "frozen_available": len(frozen_ce),
            "frozen_target_bad": int(frozen_ce["target_bad"].sum()),
            "frozen_target_good": int((~frozen_ce["target_bad"].astype(bool)).sum()),
            "missing_frozen_ids": ce7.loc[~ce7["frozen_target_available"], "candidate_id"].tolist(),
            "target_bad_ids": frozen_ce.loc[frozen_ce["target_bad"].astype(bool), "candidate_id"].tolist(),
        },
        "leakage_audit": {
            "forbidden_prefixes": ["eval_", "target_", "is_oos_", "oos_to_is_"],
            "tested_feature_names": power["feature"].tolist(),
            "forbidden_feature_count": 0,
            "preliminary_leaked_result_used": False,
        },
        "conclusion": (
            "A pure static predictor survived frozen bootstrap and FDR."
            if verdict == "STATIC_PREDICTOR_FOUND"
            else "Only weak unadjusted evidence remains; no fourth static BLOCK is justified."
            if verdict == "WEAK"
            else "No pure static predictor survived; dynamic observation logging is required."
        ),
        "no_design_change": True,
        "operational_implementation": False,
        "source_rule_mutation": False,
        "live_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    core.SUMMARY_OUT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)),
        encoding="utf-8",
    )

    lines = [
        "# CE형 개체의 정적 예측 특징 탐색",
        "",
        f"- 최종 판정: **{verdict}**",
        "- 룰북·저장 정적 파라미터만 predictor로 사용",
        "- 결과·동적 realized component·현재 시장 상태는 predictor에서 제외",
        "- 설계·구현 변경: 없음",
        "",
        "## 1. 타깃 정의",
        "",
        "각 split·stage에서 같은 상대 결과 정의를 적용했다.",
        "",
        "- IS→OOS 붕괴: PnL gap 상위 10% + OOS/IS<=0.5",
        "- positive tail: `worst MAE ~ log1p(거래수)` 기대 residual 하위 10%",
        "- high-win large-loss: 승률 상위 25% + worst-loss/median-win 상위 25%",
        "",
        f"- IS discovery: {len(discovery):,}개, bad {int(discovery['target_bad'].sum()):,}개 ({discovery['target_bad'].mean()*100:.2f}%)",
        f"- frozen OOS: {len(validation):,}개, bad {int(validation['target_bad'].sum()):,}개 ({validation['target_bad'].mean()*100:.2f}%)",
        "",
        "결과 라벨의 frozen 상대경계는 결과 정의이며 predictor 경계 튜닝이 아니다. Predictor 경계는 IS에서만 선택했다.",
        "",
        "## 2. 누수 감사",
        "",
        "예비 과정에서 발견한 target-derived worst-MAE residual 특징은 폐기했다. 최종 predictor에서 다음을 모두 강제 제외했다.",
        "",
        "- `eval_*`, `target_*`",
        "- `is_oos_gap_pp`, `oos_to_is_pnl_ratio`",
        "- raw base/holdout 결과 컬럼",
        "",
        f"최종 순수 정적 특징: {len(power)}개, 2-feature 조합: {len(pairs)}개",
        "",
        "## 3. CE 7개 타깃 타당성",
        "",
        f"- frozen 존재: {len(frozen_ce)}/7",
        f"- 결과상 bad: {int(frozen_ce['target_bad'].sum())}/{len(frozen_ce)}",
        f"- frozen 없음: {', '.join(summary['ce7']['missing_frozen_ids']) or '없음'}",
        "",
        "CE 동적 FAIL과 실제 frozen bad는 동일하지 않으므로 CE7 포섭률에 맞춰 경계를 조정하지 않았다.",
        "",
        "## 4. 단일 특징",
        "",
        "| 특징 | IS AUC | frozen AUC | frozen RD | RD 95% CI | frozen FDR q | 재현 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in power.head(12).itertuples(index=False):
        lines.append(
            f"| {row.feature} | {row.discovery_auc:.3f} | {row.validation_auc:.3f} | "
            f"{row.validation_risk_difference:.3f} | [{row.validation_risk_difference_ci_low:.3f}, {row.validation_risk_difference_ci_high:.3f}] | "
            f"{row.validation_flag_fdr_q:.3f} | {'YES' if row.replicated_static_predictor_fdr else 'NO'} |"
        )
    lines += [
        "",
        "## 5. 2개 특징 조합",
        "",
        f"- 시도: {len(pairs):,}",
        f"- 미보정 frozen hit: {len(unadjusted_pairs):,}",
        f"- bootstrap+FDR 재현: {len(replicated_pairs):,}",
        "- 특징 2개 초과·복잡 모델은 시도하지 않았다.",
        "",
        "## 6. 최종 판정",
        "",
    ]
    if verdict == "STATIC_PREDICTOR_FOUND":
        lines += ["**STATIC_PREDICTOR_FOUND**", "", "순수 정적 특징이 frozen bootstrap과 FDR까지 통과했다."]
    elif verdict == "WEAK":
        lines += [
            "**WEAK**", "",
            "일부 방향성이나 미보정 hit는 있으나 bootstrap CI와 frozen FDR을 동시에 통과하지 못했다.",
            "네 번째 정적 BLOCK 근거로는 부족하며 MONITOR 연구 후보 이상으로 올리면 안 된다.",
        ]
    else:
        lines += [
            "**NO_STATIC_PREDICTOR**", "",
            "단일 순수 정적 특징과 2개 조합 모두 frozen에서 재현되지 않았다.",
            "CE는 동적 observation logging이 유일한 검증 경로다.",
        ]
    lines += [
        "",
        "## 7. 커브피팅 점검",
        "",
        f"- IS 전용 또는 미보정 전용 특징·조합: {len(curve):,}개",
        "- frozen 단일·pair family별 FDR 적용",
        "- v3·BOIL 통과 frozen 18개는 순증 참고치로만 기록",
        "- 누수 예비 결과는 최종 판정에서 제외",
        "",
        "## 8. 산출물",
        "",
        f"- `{core.TARGET_OUT.name}`",
        f"- `{core.FEATURE_MATRIX_OUT.name}`",
        f"- `{core.FEATURE_POWER_OUT.name}`",
        f"- `{core.PAIR_POWER_OUT.name}`",
        f"- `{core.CE7_OUT.name}`",
        f"- `{core.CURVE_FIT_OUT.name}`",
        f"- `{core.SUMMARY_OUT.name}`",
    ]
    core.READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
