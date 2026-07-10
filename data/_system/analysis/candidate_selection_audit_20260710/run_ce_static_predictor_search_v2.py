from __future__ import annotations

"""CE 정적 예측 특징 탐색 v2.

주 분석은 지시서대로 기존 history 평균 PnL 음수만 제외한다.
v3·BOIL 이후 표본은 네 번째 게이트의 순증 참고군으로 별도 집계한다.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_ce_static_predictor_search as core  # noqa: E402

OUT = SCRIPT_DIR


def eligible_history(frame: pd.DataFrame) -> pd.Series:
    return frame["base_avg_pnl_pct"].ge(0) & frame["eval_n"].ge(8)


def existing_static_survivor(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["recommended_static_status"].eq("PASS")
        & frame["final_p99_weightless_block_status"].eq("PASS")
        & ~frame["boil_block"]
    )


def feature_columns(discovery: pd.DataFrame, validation: pd.DataFrame) -> list[str]:
    excluded = {
        "candidate_id", "stage", "ticker", "rulebook_hash", "source_file", "source_row_index", "done_marker",
        "profile_eligible", "origin_complete", "period_count", "all_history_n", "all_history_avg_pnl_pct",
        "all_history_win_rate_pct", "base_n", "base_avg_pnl_pct", "base_win_rate_pct", "holdout_n",
        "holdout_avg_pnl_pct", "holdout_win_rate_pct", "history_avg_atr_pct", "vol_group", "weight_volume_surge",
        "check_complete", "check_history", "check_boil", "check_ce", "ce_ratio", "ce_top2_share_pct",
        "static_status", "static_fail_reasons", "static_hold_reasons", "elite_static_pass", "elite_filter_reason",
        "elite_score", "denylisted", "selected_static", "selected_stage_rank", "oos_expectancy_pct", "oos_fitness",
        "oos_win_rate", "oos_trade_count", "worst_drawdown_pct", "signal_threshold", "volume_surge_ratio",
        "recommended_static_status", "history_win_monitor", "boil_monitor", "ce_monitor",
        "final_p99_weightless_block_status", "activity_rule_hash", "boil_block", "evaluation_split",
        "history_eligible", "existing_static_survivor", "eval_n", "eval_avg_pnl_pct", "eval_win_rate_pct",
        "eval_min_pnl_pct", "eval_p05_pnl_pct", "eval_avg_mae_pct", "eval_worst_mae_pct", "eval_avg_mfe_pct",
        "eval_median_win_pct", "eval_worst_to_median_win", "eval_top3_loss_share", "eval_max_loss_share",
        "is_oos_gap_pp", "oos_to_is_pnl_ratio", "target_is_oos_collapse", "target_positive_tail_risk",
        "target_high_win_large_loss", "target_bad", "target_reason", "matrix_split",
    }
    return [
        column for column in discovery.columns
        if column not in excluded
        and pd.api.types.is_numeric_dtype(discovery[column])
        and discovery[column].notna().mean() >= 0.85
        and validation[column].notna().mean() >= 0.85
        and discovery[column].nunique(dropna=True) > 1
    ]


def add_incremental_metrics(
    power: pd.DataFrame,
    fitted: dict,
    validation: pd.DataFrame,
) -> pd.DataFrame:
    result = power.copy()
    mask = validation["existing_static_survivor"].to_numpy(bool)
    metrics_by_feature = {}
    for index, (feature, fitted_values) in enumerate(fitted.items()):
        _, _, v_score, _, v_flag = fitted_values
        metrics = core.classification_metrics(
            validation.loc[mask, "target_bad"].to_numpy(int),
            v_score[mask],
            v_flag[mask],
        )
        ci = core.bootstrap_ci(
            validation.loc[mask].reset_index(drop=True),
            "target_bad",
            v_score[mask],
            v_flag[mask],
            core.SEED + 20_000 + index,
        )
        metrics_by_feature[feature] = {**metrics, **ci}
    for prefix, key in (
        ("incremental_validation_n", "n"),
        ("incremental_validation_bad_n", "bad_n"),
        ("incremental_validation_flagged_n", "flagged_n"),
        ("incremental_validation_auc", "auc"),
        ("incremental_validation_auc_ci_low", "auc_ci_low"),
        ("incremental_validation_auc_ci_high", "auc_ci_high"),
        ("incremental_validation_risk_difference", "risk_difference"),
        ("incremental_validation_risk_difference_ci_low", "risk_difference_ci_low"),
        ("incremental_validation_risk_difference_ci_high", "risk_difference_ci_high"),
    ):
        result[prefix] = result["feature"].map(lambda f: metrics_by_feature.get(f, {}).get(key, math.nan))
    return result


def main() -> int:
    base = core.stable_csv(OUT / "integrated_gate_candidate_dryrun.csv", low_memory=False)
    v3 = core.stable_csv(
        OUT / "threshold_p99_weightless_block_candidate_decisions.csv",
        usecols=["candidate_id", "final_p99_weightless_block_status"],
    )
    boil = core.stable_csv(OUT / "boil_block_exclusive_targets.csv", usecols=["candidate_id"])
    activity = core.stable_csv(
        OUT / "threshold_reachability_stage3_full_indicator_detail.csv.gz",
        usecols=["candidate_id", "activity_rule_hash"], low_memory=False,
    ).drop_duplicates("candidate_id")
    ce7 = core.stable_csv(OUT / "ce_origin_fail_rejudged.csv", usecols=["candidate_id", "stage", "ticker"])

    base = base.merge(v3, on="candidate_id", validate="one_to_one").merge(activity, on="candidate_id", validate="one_to_one")
    base["boil_block"] = base["candidate_id"].isin(set(boil["candidate_id"]))

    internal = core.history_metrics(base)
    frozen = core.frozen_metrics()
    static = core.source_features(base)
    threshold = core.threshold_features(set(base["candidate_id"]))
    features = base.merge(static, on="candidate_id", validate="one_to_one").merge(threshold, on="candidate_id", validate="one_to_one")

    discovery_all = base.merge(internal, on="candidate_id", how="left", validate="one_to_one")
    discovery_all["evaluation_split"] = "INTERNAL_DISCOVERY"
    discovery_all["history_eligible"] = eligible_history(discovery_all)
    discovery_all["existing_static_survivor"] = existing_static_survivor(discovery_all)
    discovery_seed = discovery_all[discovery_all["history_eligible"]].copy()
    target_thresholds = core.derive_target_thresholds(discovery_seed)
    discovery_all = core.apply_target(discovery_all, target_thresholds)
    discovery_cohort = discovery_all[discovery_all["history_eligible"]].copy()

    validation_all = base[base["candidate_id"].isin(set(frozen["candidate_id"]))].merge(
        frozen, on="candidate_id", validate="one_to_one"
    )
    validation_all["evaluation_split"] = "FROZEN_OOS_VALIDATION"
    validation_all["history_eligible"] = eligible_history(validation_all)
    validation_all["existing_static_survivor"] = existing_static_survivor(validation_all)
    validation_all = core.apply_target(validation_all, target_thresholds)
    validation_cohort = validation_all[validation_all["history_eligible"]].copy()

    target_columns = [
        "evaluation_split", "candidate_id", "stage", "ticker", "rulebook_hash", "activity_rule_hash",
        "recommended_static_status", "final_p99_weightless_block_status", "boil_block", "history_eligible",
        "existing_static_survivor", "base_n", "base_avg_pnl_pct", "base_win_rate_pct", "eval_n",
        "eval_avg_pnl_pct", "eval_win_rate_pct", "eval_min_pnl_pct", "eval_p05_pnl_pct", "eval_avg_mae_pct",
        "eval_worst_mae_pct", "eval_avg_mfe_pct", "eval_worst_to_median_win", "eval_top3_loss_share",
        "eval_max_loss_share", "is_oos_gap_pp", "oos_to_is_pnl_ratio", "target_is_oos_collapse",
        "target_positive_tail_risk", "target_high_win_large_loss", "target_bad", "target_reason",
    ]
    pd.concat([discovery_all[target_columns], validation_all[target_columns]], ignore_index=True).to_csv(
        core.TARGET_OUT, index=False, compression="gzip"
    )

    discovery = discovery_cohort.merge(
        features.drop(columns=[column for column in features.columns if column in discovery_cohort.columns and column != "candidate_id"]),
        on="candidate_id", how="left", validate="one_to_one",
    )
    validation = validation_cohort.merge(
        features.drop(columns=[column for column in features.columns if column in validation_cohort.columns and column != "candidate_id"]),
        on="candidate_id", how="left", validate="one_to_one",
    )
    discovery["matrix_split"] = "INTERNAL_DISCOVERY"
    validation["matrix_split"] = "FROZEN_OOS_VALIDATION"
    pd.concat([discovery, validation], ignore_index=True).to_csv(
        core.FEATURE_MATRIX_OUT, index=False, compression="gzip"
    )

    tested_features = feature_columns(discovery, validation)
    power, fitted = core.univariate_analysis(discovery, validation, tested_features)
    power = add_incremental_metrics(power, fitted, validation)
    power.to_csv(core.FEATURE_POWER_OUT, index=False)
    pairs = core.pair_analysis(discovery, validation, power, fitted)
    pairs.to_csv(core.PAIR_POWER_OUT, index=False)

    replicated_features = power[power["replicated_static_predictor"]]
    replicated_pairs = pairs[pairs.get("replicated_pair", pd.Series(False, index=pairs.index))] if len(pairs) else pairs
    weak_features = power[
        (
            power["discovery_supported"]
            & power["validation_auc"].ge(0.55)
            & power["validation_risk_difference"].gt(0)
        )
        |
        (
            power["validation_supported"]
            & power["discovery_auc"].ge(0.52)
            & power["discovery_risk_difference"].gt(0)
        )
    ]
    if len(replicated_features) or len(replicated_pairs):
        verdict = "STATIC_PREDICTOR_FOUND"
    elif len(weak_features):
        verdict = "WEAK"
    else:
        verdict = "NO_STATIC_PREDICTOR"

    ce7_result = ce7.merge(features, on=["candidate_id", "stage", "ticker"], how="left", validate="one_to_one")
    ce7_result = ce7_result.merge(
        validation_all[["candidate_id", "target_bad", "target_reason", "history_eligible", "existing_static_survivor"]],
        on="candidate_id", how="left", validate="one_to_one",
    )
    ce7_result["frozen_target_available"] = ce7_result["target_bad"].notna()
    top_features = power["feature"].head(10).tolist()
    for feature in top_features:
        boundary = fitted[feature][0]
        flags = []
        for row in ce7_result.itertuples(index=False):
            value = core.safe_float(getattr(row, feature))
            threshold_value = core.safe_float(boundary.raw_thresholds.get(str(row.stage)))
            if not math.isfinite(value) or not math.isfinite(threshold_value):
                flags.append(False)
            elif boundary.direction == ">=":
                flags.append(value >= threshold_value)
            else:
                flags.append(value <= threshold_value)
        ce7_result[f"flag_{feature}"] = flags
    ce7_result.to_csv(core.CE7_OUT, index=False)

    curve_rows = []
    for row in power.itertuples(index=False):
        if row.discovery_supported and not row.validation_supported:
            curve_rows.append({
                "type": "IS_ONLY_FEATURE", "feature_or_pair": row.feature,
                "discovery_auc": row.discovery_auc, "discovery_risk_difference": row.discovery_risk_difference,
                "validation_auc": row.validation_auc, "validation_risk_difference": row.validation_risk_difference,
                "validation_ci_low": row.validation_risk_difference_ci_low,
                "note": "IS separation failed frozen OOS",
            })
    if len(pairs):
        for row in pairs.itertuples(index=False):
            if row.discovery_risk_difference_ci_low > 0 and not row.validation_supported:
                curve_rows.append({
                    "type": "IS_ONLY_PAIR",
                    "feature_or_pair": f"{row.left_feature} {row.operator} {row.right_feature}",
                    "discovery_auc": math.nan, "discovery_risk_difference": row.discovery_risk_difference,
                    "validation_auc": math.nan, "validation_risk_difference": row.validation_risk_difference,
                    "validation_ci_low": row.validation_risk_difference_ci_low,
                    "note": "two-feature IS combination failed frozen OOS",
                })
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(core.CURVE_FIT_OUT, index=False)

    frozen_ce = ce7_result[ce7_result["frozen_target_available"]].copy()
    best = power.iloc[0] if len(power) else None
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "analysis_version": "v2-history-gate-only-primary",
        "verdict": verdict,
        "method": {
            "primary_discovery": "all 17,071 origins with base_avg_pnl>=0 and internal holdout n>=8",
            "primary_validation": "frozen live93 with base_avg_pnl>=0 and OOS n>=8",
            "incremental_reference": "v3 PASS + BOIL PASS + recommended static PASS subset",
            "threshold_selection": "discovery only; stage empirical-percentile boundary",
            "pair_limit": 2,
            "bootstrap": f"ticker-cluster {core.BOOTSTRAP_REPS} reps",
        },
        "target_definition": target_thresholds,
        "cohorts": {
            "discovery_total_n": len(discovery),
            "discovery_bad_n": int(discovery["target_bad"].sum()),
            "discovery_existing_static_survivor_n": int(discovery["existing_static_survivor"].sum()),
            "validation_total_n": len(validation),
            "validation_bad_n": int(validation["target_bad"].sum()),
            "validation_existing_static_survivor_n": int(validation["existing_static_survivor"].sum()),
            "validation_existing_static_survivor_bad_n": int(validation.loc[validation["existing_static_survivor"], "target_bad"].sum()),
            "frozen_raw_total_n": len(validation_all),
        },
        "features_tested": len(power),
        "pairs_tested": len(pairs),
        "replicated_feature_n": len(replicated_features),
        "replicated_pair_n": len(replicated_pairs),
        "weak_feature_n": len(weak_features),
        "best_feature": None if best is None else best.to_dict(),
        "ce7": {
            "total": 7,
            "frozen_available": int(frozen_ce["frozen_target_available"].sum()),
            "frozen_target_bad": int(frozen_ce["target_bad"].sum()),
            "frozen_target_good": int((~frozen_ce["target_bad"].astype(bool)).sum()),
            "missing_frozen_ids": ce7_result.loc[~ce7_result["frozen_target_available"], "candidate_id"].tolist(),
            "target_bad_ids": frozen_ce.loc[frozen_ce["target_bad"].astype(bool), "candidate_id"].tolist(),
        },
        "conclusion": (
            "Replicated static predictor found on the broad frozen validation cohort."
            if verdict == "STATIC_PREDICTOR_FOUND"
            else "Only weak partial direction remains; evidence is insufficient for a fourth static gate."
            if verdict == "WEAK"
            else "No rulebook-static feature or two-feature combination survived broad frozen validation; CE requires dynamic observation logging."
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
        "- 주 검증군: 과거 평균 PnL 음수만 제외한 frozen OOS",
        "- v3·BOIL 통과군: 순증 참고 분석으로 별도 집계",
        "- 설계·구현 변경: 없음",
        "",
        "## 1. 타깃 정의와 표본",
        "",
        "내부 discovery 결과분포에서 stage별 경계를 고정한 뒤 frozen OOS에는 재튜닝 없이 적용했다.",
        "",
        "- IS→OOS 붕괴: PnL 격차 상위 10%이면서 OOS/IS 비율<=0.5",
        "- 양의 평균 극단 tail: 평균>0, worst MAE 하위 10%",
        "- 고승률 대형손실: 승률 상위 25%, worst/median-win 상위 25%, top3 loss share 중앙값 이상",
        "",
        f"- discovery: {len(discovery):,}개, bad {int(discovery['target_bad'].sum()):,}개",
        f"- frozen validation: {len(validation):,}개, bad {int(validation['target_bad'].sum()):,}개",
        f"- frozen 중 기존 v3·BOIL·정적 history 통과: {int(validation['existing_static_survivor'].sum()):,}개, bad {int(validation.loc[validation['existing_static_survivor'],'target_bad'].sum()):,}개",
        "",
        "## 2. CE 7개 타깃 타당성",
        "",
        f"- frozen 존재: {int(frozen_ce['frozen_target_available'].sum())}/7",
        f"- 결과 타깃 bad: {int(frozen_ce['target_bad'].sum())}/{len(frozen_ce)}",
        f"- frozen 없음: {', '.join(summary['ce7']['missing_frozen_ids']) or '없음'}",
        "",
        "CE 동적 FAIL 집합과 실제 frozen 붕괴 집합은 동일하지 않다. 따라서 CE7 포섭률만 최적화하지 않고 결과 타깃을 독립적으로 사용했다.",
        "",
        "## 3. 단일 특징 상위 결과",
        "",
        "| 특징 | IS AUC | IS RD | frozen AUC | frozen RD | frozen RD 95% CI | 재현 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in power.head(12).itertuples(index=False):
        lines.append(
            f"| {row.feature} | {row.discovery_auc:.3f} | {row.discovery_risk_difference:.3f} | "
            f"{row.validation_auc:.3f} | {row.validation_risk_difference:.3f} | "
            f"[{row.validation_risk_difference_ci_low:.3f}, {row.validation_risk_difference_ci_high:.3f}] | "
            f"{'YES' if row.replicated_static_predictor else 'NO'} |"
        )
    lines += [
        "",
        "## 4. 2개 특징 조합",
        "",
        f"- 조합 수: {len(pairs):,}",
        f"- frozen 재현: {len(replicated_pairs):,}",
        "- discovery 상위 5개 특징의 AND/OR만 허용했다.",
        "",
        "## 5. 판정",
        "",
    ]
    if verdict == "STATIC_PREDICTOR_FOUND":
        lines += ["**STATIC_PREDICTOR_FOUND**", "", "frozen OOS에서 통계적으로 재현된 정적 경계가 확인됐다."]
    elif verdict == "WEAK":
        lines += [
            "**WEAK**", "",
            "방향성이 일부 유지된 특징은 있으나 discovery FDR·bootstrap 또는 frozen CI 기준을 동시에 충족하지 못했다.",
            "네 번째 정적 BLOCK 근거로는 부족하며 MONITOR 탐색 수준을 넘지 못한다.",
        ]
    else:
        lines += [
            "**NO_STATIC_PREDICTOR**", "",
            "IS에서 보인 정적 특징의 분리력이 frozen OOS에서 재현되지 않았다.",
            "CE형 실패는 룰북 정적 정보만으로 안정적으로 차단할 수 없으며 동적 관측 로깅이 유일한 검증 경로다.",
        ]
    lines += [
        "",
        "## 6. 커브피팅 점검",
        "",
        f"- IS에서만 유효하고 frozen에서 실패한 특징·조합: {len(curve):,}개",
        "- frozen 경계 재튜닝 없음",
        "- stage별 척도는 discovery empirical percentile로만 정규화",
        "- bootstrap은 ticker cluster 단위",
        "",
        "## 7. 산출물",
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
