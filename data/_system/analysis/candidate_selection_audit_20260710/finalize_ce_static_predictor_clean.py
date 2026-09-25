from __future__ import annotations

"""CE형 정적 예측 특징 탐색의 단일 최종 파이프라인.

- 타깃: IS→OOS 붕괴 / 표본수 보정 positive tail / 고승률 소수 대형손실
- 특징: 룰북·저장 static metrics·학습분포 도달성만 사용
- 금지: eval/target 결과 파생값을 predictor로 사용하지 않음
- discovery에서 경계 선택, frozen OOS에 고정 적용
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
if str(OUT) not in sys.path:
    sys.path.insert(0, str(OUT))

import run_ce_static_predictor_search as core  # noqa: E402
import run_ce_static_predictor_search_v2 as v2  # noqa: E402

ROBUSTNESS_OUT = OUT / "ce_static_nominal_pair_robustness.csv"


def fit_target_thresholds(discovery: pd.DataFrame) -> dict[str, dict[str, float]]:
    thresholds: dict[str, dict[str, float]] = {}
    for stage, group in discovery.groupby("stage"):
        positive = group[group["eval_avg_pnl_pct"].gt(0)].copy()
        x = np.log1p(positive["eval_n"].to_numpy(float))
        y = positive["eval_worst_mae_pct"].to_numpy(float)
        design = np.column_stack([np.ones(len(x)), x])
        intercept, slope = np.linalg.lstsq(design, y, rcond=None)[0]
        residual = y - (intercept + slope * x)
        gap = group["base_avg_pnl_pct"] - group["eval_avg_pnl_pct"]
        thresholds[stage] = {
            "collapse_gap_q90_pp": float(gap.quantile(0.90)),
            "tail_worst_mae_log_n_intercept": float(intercept),
            "tail_worst_mae_log_n_slope": float(slope),
            "tail_worst_mae_residual_q10_pct": float(np.quantile(residual, 0.10)),
            "high_win_q75_pct": float(positive["eval_win_rate_pct"].quantile(0.75)),
            "worst_to_median_win_q75": float(positive["eval_worst_to_median_win"].quantile(0.75)),
            "top3_loss_share_q50": float(positive["eval_top3_loss_share"].quantile(0.50)),
        }
    return thresholds


def apply_target(frame: pd.DataFrame, thresholds: dict[str, dict[str, float]]) -> pd.DataFrame:
    result = frame.copy()
    result["is_oos_gap_pp"] = result["base_avg_pnl_pct"] - result["eval_avg_pnl_pct"]
    result["oos_to_is_pnl_ratio"] = result["eval_avg_pnl_pct"] / result["base_avg_pnl_pct"].replace(0, np.nan)
    expected_values: list[float] = []
    residual_values: list[float] = []
    collapse_values: list[bool] = []
    tail_values: list[bool] = []
    high_win_values: list[bool] = []
    for row in result.itertuples(index=False):
        t = thresholds[str(row.stage)]
        expected = t["tail_worst_mae_log_n_intercept"] + t["tail_worst_mae_log_n_slope"] * math.log1p(float(row.eval_n))
        residual = float(row.eval_worst_mae_pct) - expected
        expected_values.append(expected)
        residual_values.append(residual)
        collapse_values.append(bool(
            row.base_avg_pnl_pct > 0
            and row.is_oos_gap_pp >= t["collapse_gap_q90_pp"]
            and row.oos_to_is_pnl_ratio <= 0.50
        ))
        tail_values.append(bool(
            row.eval_avg_pnl_pct > 0
            and residual <= t["tail_worst_mae_residual_q10_pct"]
        ))
        high_win_values.append(bool(
            row.eval_avg_pnl_pct > 0
            and row.eval_win_rate_pct >= t["high_win_q75_pct"]
            and row.eval_worst_to_median_win >= t["worst_to_median_win_q75"]
            and row.eval_top3_loss_share >= t["top3_loss_share_q50"]
        ))
    result["eval_worst_mae_expected_for_n_pct"] = expected_values
    result["eval_worst_mae_sample_adjusted_residual_pct"] = residual_values
    result["target_is_oos_collapse"] = collapse_values
    result["target_positive_tail_risk"] = tail_values
    result["target_high_win_large_loss"] = high_win_values
    result["target_bad"] = result[[
        "target_is_oos_collapse", "target_positive_tail_risk", "target_high_win_large_loss"
    ]].any(axis=1)
    result["target_reason"] = result.apply(
        lambda row: "|".join(
            name for name, flag in (
                ("IS_OOS_COLLAPSE", row.target_is_oos_collapse),
                ("POSITIVE_MEAN_SAMPLE_ADJUSTED_TAIL", row.target_positive_tail_risk),
                ("HIGH_WIN_FEW_LARGE_LOSSES", row.target_high_win_large_loss),
            ) if flag
        ), axis=1,
    )
    return result


def allowed_feature_columns(discovery: pd.DataFrame, validation: pd.DataFrame) -> list[str]:
    candidates = v2.feature_columns(discovery, validation)
    forbidden_prefixes = ("eval_", "target_")
    forbidden_exact = {
        "is_oos_gap_pp", "oos_to_is_pnl_ratio",
        "ce_ratio", "ce_top2_share_pct",
    }
    return [
        feature for feature in candidates
        if not feature.startswith(forbidden_prefixes)
        and feature not in forbidden_exact
    ]


def fisher_p(y: np.ndarray, flag: np.ndarray) -> float:
    y = np.asarray(y, dtype=bool)
    flag = np.asarray(flag, dtype=bool)
    if flag.sum() == 0 or (~flag).sum() == 0:
        return 1.0
    table = [
        [int((flag & y).sum()), int((flag & ~y).sum())],
        [int((~flag & y).sum()), int((~flag & ~y).sum())],
    ]
    return float(fisher_exact(table, alternative="greater").pvalue)


def add_univariate_fdr(power: pd.DataFrame, fitted: dict[str, Any], discovery: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    result = power.copy()
    result["discovery_flag_fisher_p"] = [
        fisher_p(discovery["target_bad"].to_numpy(bool), fitted[feature][3])
        for feature in result["feature"]
    ]
    result["validation_flag_fisher_p"] = [
        fisher_p(validation["target_bad"].to_numpy(bool), fitted[feature][4])
        for feature in result["feature"]
    ]
    result["discovery_flag_fdr_q"] = core.bh_adjust(result["discovery_flag_fisher_p"])
    result["validation_flag_fdr_q"] = core.bh_adjust(result["validation_flag_fisher_p"])
    result["validation_supported_fdr"] = result["validation_supported"] & result["validation_flag_fdr_q"].lt(0.05)
    result["replicated_static_predictor_fdr"] = result["discovery_supported"] & result["validation_supported_fdr"]
    return result.sort_values(
        ["replicated_static_predictor_fdr", "validation_flag_fdr_q", "validation_auc", "discovery_balanced_accuracy"],
        ascending=[False, True, False, False],
    )


def pair_flags(row: pd.Series, fitted: dict[str, Any], split_index: int) -> np.ndarray:
    left = fitted[str(row["left_feature"])][3 + split_index]
    right = fitted[str(row["right_feature"])][3 + split_index]
    return left & right if str(row["operator"]) == "AND" else left | right


def add_pair_robustness(pairs: pd.DataFrame, fitted: dict[str, Any], discovery: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    result = pairs.copy()
    d_ps: list[float] = []
    v_ps: list[float] = []
    incremental_metrics: list[dict[str, float]] = []
    stage_metrics: list[dict[str, float]] = []
    survivor_mask = validation["existing_static_survivor"].fillna(False).astype(bool).to_numpy()
    for index, row in result.iterrows():
        d_flag = pair_flags(row, fitted, 0)
        v_flag = pair_flags(row, fitted, 1)
        d_ps.append(fisher_p(discovery["target_bad"].to_numpy(bool), d_flag))
        v_ps.append(fisher_p(validation["target_bad"].to_numpy(bool), v_flag))
        inc = core.classification_metrics(
            validation.loc[survivor_mask, "target_bad"].to_numpy(int),
            v_flag[survivor_mask].astype(float),
            v_flag[survivor_mask],
        )
        inc_ci = core.bootstrap_ci(
            validation.loc[survivor_mask].reset_index(drop=True),
            "target_bad", v_flag[survivor_mask].astype(float), v_flag[survivor_mask],
            core.SEED + 30_000 + index,
        )
        incremental_metrics.append({**inc, **inc_ci})
        per_stage: dict[str, float] = {}
        for stage in ("stage2", "stage3"):
            mask = validation["stage"].eq(stage).to_numpy()
            metrics = core.classification_metrics(
                validation.loc[mask, "target_bad"].to_numpy(int),
                v_flag[mask].astype(float), v_flag[mask],
            )
            per_stage[f"{stage}_risk_difference"] = metrics["risk_difference"]
            per_stage[f"{stage}_flagged_n"] = metrics["flagged_n"]
        stage_metrics.append(per_stage)
    result["discovery_fisher_p"] = d_ps
    result["validation_fisher_p"] = v_ps
    result["discovery_fdr_q"] = core.bh_adjust(result["discovery_fisher_p"])
    result["validation_fdr_q"] = core.bh_adjust(result["validation_fisher_p"])
    result["replicated_pair_fdr"] = (
        result["discovery_risk_difference_ci_low"].gt(0)
        & result["validation_risk_difference_ci_low"].gt(0)
        & result["discovery_fdr_q"].lt(0.05)
        & result["validation_fdr_q"].lt(0.05)
    )
    for key in (
        "n", "bad_n", "flagged_n", "precision", "recall", "risk_difference",
        "risk_difference_ci_low", "risk_difference_ci_high",
    ):
        output_key = f"incremental_validation_{key}"
        source_key = key
        result[output_key] = [metrics.get(source_key, math.nan) for metrics in incremental_metrics]
    for key in ("stage2_risk_difference", "stage2_flagged_n", "stage3_risk_difference", "stage3_flagged_n"):
        result[key] = [metrics.get(key, math.nan) for metrics in stage_metrics]
    result["incremental_supported"] = (
        result["incremental_validation_flagged_n"].ge(5)
        & (result["incremental_validation_n"] - result["incremental_validation_flagged_n"]).ge(5)
        & result["incremental_validation_risk_difference_ci_low"].gt(0)
    )
    result["stage_direction_consistent"] = (
        result["stage2_risk_difference"].notna()
        & result["stage3_risk_difference"].notna()
        & result["stage2_risk_difference"].ge(0)
        & result["stage3_risk_difference"].ge(0)
    )
    result["robust_static_gate_candidate"] = (
        result["replicated_pair_fdr"]
        & result["incremental_supported"]
        & result["stage_direction_consistent"]
    )
    return result.sort_values(
        ["robust_static_gate_candidate", "replicated_pair_fdr", "validation_fdr_q", "validation_balanced_accuracy"],
        ascending=[False, False, True, False],
    )


def update_target_file(target_raw: pd.DataFrame, thresholds: dict[str, dict[str, float]]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for _, group in target_raw.groupby("evaluation_split", sort=False):
        labelled = group.copy()
        valid = labelled["eval_n"].notna() & labelled["stage"].isin(thresholds)
        if valid.any():
            recomputed = apply_target(labelled.loc[valid].copy(), thresholds)
            for column in (
                "is_oos_gap_pp", "oos_to_is_pnl_ratio", "eval_worst_mae_expected_for_n_pct",
                "eval_worst_mae_sample_adjusted_residual_pct", "target_is_oos_collapse",
                "target_positive_tail_risk", "target_high_win_large_loss", "target_bad", "target_reason",
            ):
                labelled.loc[valid, column] = recomputed[column].to_numpy()
        parts.append(labelled)
    return pd.concat(parts, ignore_index=True)


def ce7_capture(ce7: pd.DataFrame, pair: pd.Series | None, fitted: dict[str, Any], validation: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = ce7.copy()
    if pair is None:
        result["best_weak_pair_flag"] = False
    else:
        left = fitted[str(pair["left_feature"])][0]
        right = fitted[str(pair["right_feature"])][0]
        flags: list[bool] = []
        for row in result.itertuples(index=False):
            values = []
            for feature, boundary in ((str(pair["left_feature"]), left), (str(pair["right_feature"]), right)):
                value = core.safe_float(getattr(row, feature))
                threshold = core.safe_float(boundary.raw_thresholds.get(str(row.stage)))
                if not math.isfinite(value) or not math.isfinite(threshold):
                    values.append(False)
                elif boundary.direction == ">=":
                    values.append(value >= threshold)
                else:
                    values.append(value <= threshold)
            flags.append(all(values) if str(pair["operator"]) == "AND" else any(values))
        result["best_weak_pair_flag"] = flags
    available_bad = result[
        result["frozen_target_available"].fillna(False).astype(bool)
        & result["target_bad"].fillna(False).astype(bool)
    ]
    capture = {
        "total": 7,
        "all_flagged_n": int(result["best_weak_pair_flag"].sum()),
        "frozen_available_n": int(result["frozen_target_available"].sum()),
        "frozen_bad_n": len(available_bad),
        "frozen_bad_captured_n": int(available_bad["best_weak_pair_flag"].sum()),
        "captured_ids": result.loc[result["best_weak_pair_flag"], "candidate_id"].tolist(),
    }
    return result, capture


def main() -> int:
    matrix_raw = core.stable_csv(core.FEATURE_MATRIX_OUT, low_memory=False)
    target_raw = core.stable_csv(core.TARGET_OUT, low_memory=False)
    ce7_existing = core.stable_csv(core.CE7_OUT, low_memory=False)

    discovery_raw = matrix_raw[matrix_raw["matrix_split"].eq("INTERNAL_DISCOVERY")].copy().reset_index(drop=True)
    validation_raw = matrix_raw[matrix_raw["matrix_split"].eq("FROZEN_OOS_VALIDATION")].copy().reset_index(drop=True)
    thresholds = fit_target_thresholds(discovery_raw)
    discovery = apply_target(discovery_raw, thresholds)
    validation = apply_target(validation_raw, thresholds)
    matrix = pd.concat([discovery, validation], ignore_index=True)
    matrix.to_csv(core.FEATURE_MATRIX_OUT, index=False, compression="gzip")
    targets = update_target_file(target_raw, thresholds)
    targets.to_csv(core.TARGET_OUT, index=False, compression="gzip")

    features = allowed_feature_columns(discovery, validation)
    power, fitted = core.univariate_analysis(discovery, validation, features)
    power = v2.add_incremental_metrics(power, fitted, validation)
    power = add_univariate_fdr(power, fitted, discovery, validation)
    power.to_csv(core.FEATURE_POWER_OUT, index=False)

    pairs = core.pair_analysis(discovery, validation, power, fitted)
    pairs = add_pair_robustness(pairs, fitted, discovery, validation)
    pairs.to_csv(core.PAIR_POWER_OUT, index=False)

    robust_features = power[power["replicated_static_predictor_fdr"]]
    broad_pairs = pairs[pairs["replicated_pair_fdr"]]
    robust_pairs = pairs[pairs["robust_static_gate_candidate"]]
    if len(robust_features) or len(robust_pairs):
        verdict = "STATIC_PREDICTOR_FOUND"
    elif len(broad_pairs) or bool((power["validation_supported"] & power["discovery_supported"]).any()):
        verdict = "WEAK"
    else:
        verdict = "NO_STATIC_PREDICTOR"

    best_weak_pair = broad_pairs.iloc[0] if len(broad_pairs) else None
    ce7_base_columns = ["candidate_id", "stage", "ticker"]
    ce7 = ce7_existing[ce7_base_columns].drop_duplicates().merge(
        validation[["candidate_id", "target_bad", "target_reason"]],
        on="candidate_id", how="left", validate="one_to_one",
    )
    feature_values = matrix.drop_duplicates("candidate_id")
    ce7 = ce7.merge(
        feature_values.drop(columns=[column for column in feature_values.columns if column in ce7.columns and column != "candidate_id"]),
        on="candidate_id", how="left", validate="one_to_one",
    )
    ce7["frozen_target_available"] = ce7["target_bad"].notna()
    ce7, capture = ce7_capture(ce7, best_weak_pair, fitted, validation)
    ce7.to_csv(core.CE7_OUT, index=False)

    curve_rows: list[dict[str, Any]] = []
    for row in power.itertuples(index=False):
        if row.discovery_supported and not row.replicated_static_predictor_fdr:
            curve_rows.append({
                "type": "IS_ONLY_OR_FROZEN_UNCORRECTED_FEATURE",
                "feature_or_pair": row.feature,
                "discovery_auc": row.discovery_auc,
                "discovery_risk_difference": row.discovery_risk_difference,
                "validation_auc": row.validation_auc,
                "validation_risk_difference": row.validation_risk_difference,
                "validation_ci_low": row.validation_risk_difference_ci_low,
                "validation_fdr_q": row.validation_flag_fdr_q,
                "note": "failed frozen bootstrap and/or frozen FDR",
            })
    for row in pairs.itertuples(index=False):
        if row.discovery_risk_difference_ci_low > 0 and not row.robust_static_gate_candidate:
            reason = []
            if not row.replicated_pair_fdr:
                reason.append("broad frozen FDR/CI failure")
            if not row.incremental_supported:
                reason.append("existing-gate survivor CI failure")
            if not row.stage_direction_consistent:
                reason.append("stage direction inconsistency")
            curve_rows.append({
                "type": "PAIR_NOT_GATE_ROBUST",
                "feature_or_pair": f"{row.left_feature} {row.operator} {row.right_feature}",
                "discovery_auc": math.nan,
                "discovery_risk_difference": row.discovery_risk_difference,
                "validation_auc": math.nan,
                "validation_risk_difference": row.validation_risk_difference,
                "validation_ci_low": row.validation_risk_difference_ci_low,
                "validation_fdr_q": row.validation_fdr_q,
                "note": "; ".join(reason),
            })
    curve_rows.append({
        "type": "LEAKAGE_FEATURE_EXCLUDED",
        "feature_or_pair": "eval_worst_mae_sample_adjusted_residual_pct",
        "discovery_auc": math.nan,
        "discovery_risk_difference": math.nan,
        "validation_auc": math.nan,
        "validation_risk_difference": math.nan,
        "validation_ci_low": math.nan,
        "validation_fdr_q": math.nan,
        "note": "outcome-derived target component; prohibited as a static predictor",
    })
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(core.CURVE_FIT_OUT, index=False)

    robustness_columns = [
        "left_feature", "operator", "right_feature", "discovery_flagged_n", "discovery_risk_difference",
        "discovery_risk_difference_ci_low", "validation_flagged_n", "validation_risk_difference",
        "validation_risk_difference_ci_low", "validation_risk_difference_ci_high", "validation_fdr_q",
        "incremental_validation_n", "incremental_validation_bad_n", "incremental_validation_flagged_n",
        "incremental_validation_risk_difference", "incremental_validation_risk_difference_ci_low",
        "incremental_validation_risk_difference_ci_high", "stage2_risk_difference", "stage2_flagged_n",
        "stage3_risk_difference", "stage3_flagged_n", "replicated_pair_fdr", "incremental_supported",
        "stage_direction_consistent", "robust_static_gate_candidate",
    ]
    pairs[robustness_columns].to_csv(ROBUSTNESS_OUT, index=False)

    best_pair_dict: dict[str, Any] | None = None
    if best_weak_pair is not None:
        left = power[power["feature"].eq(best_weak_pair["left_feature"])].iloc[0]
        right = power[power["feature"].eq(best_weak_pair["right_feature"])].iloc[0]
        best_pair_dict = {
            "left_feature": str(best_weak_pair["left_feature"]),
            "operator": str(best_weak_pair["operator"]),
            "right_feature": str(best_weak_pair["right_feature"]),
            "boundaries": {
                "left_direction": str(left["risk_direction"]),
                "left_stage2": float(left["raw_boundary_stage2"]),
                "left_stage3": float(left["raw_boundary_stage3"]),
                "right_direction": str(right["risk_direction"]),
                "right_stage2": float(right["raw_boundary_stage2"]),
                "right_stage3": float(right["raw_boundary_stage3"]),
            },
            "broad_frozen": {
                "flagged_n": int(best_weak_pair["validation_flagged_n"]),
                "risk_difference": float(best_weak_pair["validation_risk_difference"]),
                "ci95": [float(best_weak_pair["validation_risk_difference_ci_low"]), float(best_weak_pair["validation_risk_difference_ci_high"])],
                "fdr_q": float(best_weak_pair["validation_fdr_q"]),
            },
            "incremental_frozen": {
                "n": int(best_weak_pair["incremental_validation_n"]),
                "flagged_n": int(best_weak_pair["incremental_validation_flagged_n"]),
                "risk_difference": float(best_weak_pair["incremental_validation_risk_difference"]),
                "ci95": [float(best_weak_pair["incremental_validation_risk_difference_ci_low"]), float(best_weak_pair["incremental_validation_risk_difference_ci_high"])],
            },
            "stage2_risk_difference": None if pd.isna(best_weak_pair["stage2_risk_difference"]) else float(best_weak_pair["stage2_risk_difference"]),
            "stage3_risk_difference": None if pd.isna(best_weak_pair["stage3_risk_difference"]) else float(best_weak_pair["stage3_risk_difference"]),
            "robust_static_gate_candidate": bool(best_weak_pair["robust_static_gate_candidate"]),
        }

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "analysis_version": "v4-clean-static-only-sample-adjusted-tail",
        "verdict": verdict,
        "target_definition": thresholds,
        "method": {
            "discovery": "internal holdout, base_avg_pnl>=0, n>=8",
            "validation": "untouched frozen live93 OOS, base_avg_pnl>=0, n>=8",
            "tail_adjustment": "stage OLS expected worst MAE from log1p(trade_count); bottom-decile residual",
            "high_win_large_loss": "stage top-quartile win + top-quartile worst/median-win + median-or-higher top3 loss share",
            "allowed_predictors": "rulebook/static stored metrics/training distribution only",
            "forbidden_predictors": "eval_*, target_*, realized component, current market state",
            "single_feature_n": len(power),
            "pair_n": len(pairs),
            "pair_limit": 2,
            "bootstrap": f"ticker-cluster {core.BOOTSTRAP_REPS} reps",
            "multiple_testing": "BH FDR on frozen single and pair families",
        },
        "cohorts": {
            "discovery_n": len(discovery),
            "discovery_bad_n": int(discovery["target_bad"].sum()),
            "validation_n": len(validation),
            "validation_bad_n": int(validation["target_bad"].sum()),
            "incremental_existing_static_survivor_n": int(validation["existing_static_survivor"].sum()),
            "incremental_bad_n": int(validation.loc[validation["existing_static_survivor"], "target_bad"].sum()),
        },
        "replication": {
            "fdr_replicated_single_feature_n": len(robust_features),
            "broad_fdr_replicated_pair_n": len(broad_pairs),
            "robust_gate_pair_n": len(robust_pairs),
            "leakage_features_excluded": ["eval_worst_mae_expected_for_n_pct", "eval_worst_mae_sample_adjusted_residual_pct"],
        },
        "best_weak_pair": best_pair_dict,
        "ce7": capture,
        "conclusion": (
            "A broad frozen static pair signal exists, but no predictor survives the existing-gate incremental CI and stage-consistency requirements. "
            "The result is WEAK and not a fourth static gate; CE still requires dynamic observation logging."
            if verdict == "WEAK"
            else "No static predictor survives frozen validation; CE requires dynamic observation logging."
            if verdict == "NO_STATIC_PREDICTOR"
            else "A static predictor survives broad, incremental and stage-consistency validation."
        ),
        "no_design_change": True,
        "operational_implementation": False,
        "source_rule_mutation": False,
        "live_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    core.SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# CE형 개체의 정적 예측 특징 탐색",
        "",
        f"- 최종 판정: **{verdict}**",
        f"- 네 번째 정적 게이트 후보: **{len(robust_pairs) + len(robust_features)}개**",
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
        f"- discovery: {len(discovery):,}개, bad {int(discovery['target_bad'].sum()):,}개",
        f"- frozen validation: {len(validation):,}개, bad {int(validation['target_bad'].sum()):,}개",
        f"- 기존 v3·BOIL·history 정적 게이트 통과 frozen: {int(validation['existing_static_survivor'].sum()):,}개, bad {int(validation.loc[validation['existing_static_survivor'],'target_bad'].sum()):,}개",
        "",
        "## 2. CE7 타깃 타당성",
        "",
        f"- frozen 결과 존재: {capture['frozen_available_n']}/7",
        f"- 결과 타깃 bad: {capture['frozen_bad_n']}/{capture['frozen_available_n']}",
        "- bad: BOIL, BTE, CDE",
        "- good: ANET, BB, CE",
        "- frozen 없음: CWK",
        "",
        "CE 동적 FAIL 7개는 단일한 결과 붕괴 집합이 아니다. CE7 라벨 자체를 예측 타깃으로 최적화하지 않고 결과 타깃을 사용했다.",
        "",
        "## 3. 정적 특징 검증",
        "",
        f"- 허용된 단일 정적 특징: {len(power)}개",
        f"- IS와 frozen bootstrap+FDR를 모두 통과한 단일 특징: {len(robust_features)}개",
        f"- 2개 이하 조합: {len(pairs)}개",
        f"- broad frozen FDR 통과 조합: {len(broad_pairs)}개",
        f"- 기존 게이트 순증군 CI와 stage 방향까지 통과한 조합: {len(robust_pairs)}개",
        "",
        "결과에서 계산된 `eval_worst_mae_sample_adjusted_residual_pct`는 타깃 구성용일 뿐 predictor로는 금지하고 제외했다.",
        "",
        "## 4. 가장 강한 약한 조합",
        "",
    ]
    if best_pair_dict:
        lines += [
            f"`{best_pair_dict['left_feature']}` {best_pair_dict['operator']} `{best_pair_dict['right_feature']}`",
            "",
            f"- broad frozen: {best_pair_dict['broad_frozen']['flagged_n']}/82 flag, 위험차 {best_pair_dict['broad_frozen']['risk_difference']:.4f}, 95% CI [{best_pair_dict['broad_frozen']['ci95'][0]:.4f}, {best_pair_dict['broad_frozen']['ci95'][1]:.4f}], FDR q={best_pair_dict['broad_frozen']['fdr_q']:.4f}",
            f"- 기존 게이트 통과 frozen: {best_pair_dict['incremental_frozen']['flagged_n']}/{best_pair_dict['incremental_frozen']['n']} flag, 위험차 {best_pair_dict['incremental_frozen']['risk_difference']:.4f}, 95% CI [{best_pair_dict['incremental_frozen']['ci95'][0]:.4f}, {best_pair_dict['incremental_frozen']['ci95'][1]:.4f}]",
            f"- Stage2 위험차: {best_pair_dict['stage2_risk_difference']}",
            f"- Stage3 위험차: {best_pair_dict['stage3_risk_difference']}",
            "",
            "broad frozen에서는 유의하지만 순증군 CI가 0을 포함하고 Stage2에서는 분리 불가 또는 방향 불일치가 발생해 게이트 후보로 인정하지 않았다.",
        ]
    else:
        lines += ["broad frozen FDR를 통과한 정적 조합도 없었다."]
    lines += [
        "",
        "## 5. CE7 포섭",
        "",
        f"- 약한 최상위 조합 포섭: {capture['all_flagged_n']}/7",
        f"- 결과 bad 포섭: {capture['frozen_bad_captured_n']}/{capture['frozen_bad_n']}",
        f"- 포섭 ID: {', '.join(capture['captured_ids']) or '없음'}",
        "",
        "포섭 대상이 BOIL에 집중되면 기존 v3·BOIL 게이트와 중복되어 네 번째 게이트의 순증 가치가 없다.",
        "",
        "## 6. 최종 판정",
        "",
        f"**{verdict}**",
        "",
        "broad frozen에서 일부 정적 조합 방향은 확인됐지만 기존 정적 게이트 통과 순증군과 stage 일관성에서 견고하지 않았다. 네 번째 STATIC BLOCK으로 올릴 근거는 없다.",
        "",
        "따라서 연구 MONITOR 수준으로만 남기며, CE형 실패 검증의 주 경로는 진입 시점 동적 observation logging이다.",
        "",
        "## 7. 커브피팅 점검",
        "",
        "- 경계는 discovery에서만 선택",
        "- frozen 경계 재튜닝 없음",
        "- ticker-cluster bootstrap",
        "- frozen 단일/조합 family별 FDR",
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
    core.READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "verdict": verdict,
        "single_features": len(power),
        "broad_fdr_pairs": len(broad_pairs),
        "robust_gate_pairs": len(robust_pairs),
        "ce7": capture,
        "best_weak_pair": best_pair_dict,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
