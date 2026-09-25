from __future__ import annotations

"""CE 정적 예측 탐색 최종: split·stage 상대 결과 타깃 + frozen FDR.

결과 라벨은 각 평가 split의 상대적 극단 구간으로 정의한다.
정적 predictor 경계는 discovery에서만 선택하고 frozen에는 재튜닝 없이 적용한다.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
if str(OUT) not in sys.path:
    sys.path.insert(0, str(OUT))

import run_ce_static_predictor_search as core  # noqa: E402
import run_ce_static_predictor_search_v2 as v2  # noqa: E402


def fit_relative_thresholds(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    thresholds: dict[str, dict[str, float]] = {}
    for stage, group in frame.groupby("stage"):
        positive = group[group["eval_avg_pnl_pct"].gt(0)].copy()
        x = np.log1p(positive["eval_n"].to_numpy(float))
        y = positive["eval_worst_mae_pct"].to_numpy(float)
        if len(positive) >= 8 and np.std(x) > 1e-12:
            design = np.column_stack([np.ones(len(x)), x])
            intercept, slope = np.linalg.lstsq(design, y, rcond=None)[0]
        else:
            intercept, slope = float(np.median(y)), 0.0
        residual = y - (intercept + slope * x)
        gap = group["base_avg_pnl_pct"] - group["eval_avg_pnl_pct"]
        thresholds[stage] = {
            "collapse_gap_q90_pp": float(gap.quantile(0.90)),
            "tail_worst_mae_log_n_intercept": float(intercept),
            "tail_worst_mae_log_n_slope": float(slope),
            "tail_worst_mae_residual_q10_pct": float(np.quantile(residual, 0.10)),
            "high_win_q75_pct": float(positive["eval_win_rate_pct"].quantile(0.75)),
            "worst_to_median_win_q75": float(positive["eval_worst_to_median_win"].quantile(0.75)),
        }
    return thresholds


def apply_relative_target(frame: pd.DataFrame, thresholds: dict[str, dict[str, float]]) -> pd.DataFrame:
    result = frame.copy()
    result["is_oos_gap_pp"] = result["base_avg_pnl_pct"] - result["eval_avg_pnl_pct"]
    result["oos_to_is_pnl_ratio"] = result["eval_avg_pnl_pct"] / result["base_avg_pnl_pct"].replace(0, np.nan)
    expected, residual, collapse, tail, high_win = [], [], [], [], []
    for row in result.itertuples(index=False):
        t = thresholds[str(row.stage)]
        exp = t["tail_worst_mae_log_n_intercept"] + t["tail_worst_mae_log_n_slope"] * math.log1p(float(row.eval_n))
        res = float(row.eval_worst_mae_pct) - exp
        expected.append(exp)
        residual.append(res)
        collapse.append(bool(
            row.base_avg_pnl_pct > 0
            and row.is_oos_gap_pp >= t["collapse_gap_q90_pp"]
            and row.oos_to_is_pnl_ratio <= 0.50
        ))
        tail.append(bool(row.eval_avg_pnl_pct > 0 and res <= t["tail_worst_mae_residual_q10_pct"]))
        high_win.append(bool(
            row.eval_avg_pnl_pct > 0
            and row.eval_win_rate_pct >= t["high_win_q75_pct"]
            and row.eval_worst_to_median_win >= t["worst_to_median_win_q75"]
        ))
    result["eval_worst_mae_expected_for_n_pct"] = expected
    result["eval_worst_mae_sample_adjusted_residual_pct"] = residual
    result["target_is_oos_collapse"] = collapse
    result["target_positive_tail_risk"] = tail
    result["target_high_win_large_loss"] = high_win
    result["target_bad"] = result[[
        "target_is_oos_collapse", "target_positive_tail_risk", "target_high_win_large_loss"
    ]].any(axis=1)
    result["target_reason"] = result.apply(
        lambda r: "|".join(
            name for name, flag in (
                ("IS_OOS_COLLAPSE", r.target_is_oos_collapse),
                ("POSITIVE_MEAN_SAMPLE_ADJUSTED_TAIL", r.target_positive_tail_risk),
                ("HIGH_WIN_LARGE_LOSS", r.target_high_win_large_loss),
            ) if flag
        ), axis=1,
    )
    return result


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


def adjust_univariate(power: pd.DataFrame, fitted: dict, discovery: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    result = power.copy()
    d_ps, v_ps = [], []
    for feature in result["feature"]:
        fit = fitted[feature]
        d_ps.append(fisher_p(discovery["target_bad"].to_numpy(bool), fit[3]))
        v_ps.append(fisher_p(validation["target_bad"].to_numpy(bool), fit[4]))
    result["discovery_flag_fisher_p"] = d_ps
    result["validation_flag_fisher_p"] = v_ps
    result["discovery_flag_fdr_q"] = core.bh_adjust(result["discovery_flag_fisher_p"])
    result["validation_flag_fdr_q"] = core.bh_adjust(result["validation_flag_fisher_p"])
    result["validation_supported_fdr"] = result["validation_supported"] & result["validation_flag_fdr_q"].lt(0.05)
    result["replicated_static_predictor_fdr"] = result["discovery_supported"] & result["validation_supported_fdr"]
    return result.sort_values(
        ["replicated_static_predictor_fdr", "validation_flag_fdr_q", "validation_auc", "discovery_balanced_accuracy"],
        ascending=[False, True, False, False],
    )


def adjust_pairs(pairs: pd.DataFrame, fitted: dict, discovery: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty:
        return pairs
    result = pairs.copy()
    d_ps, v_ps = [], []
    for row in result.itertuples(index=False):
        left, right = fitted[row.left_feature], fitted[row.right_feature]
        d_flag = left[3] & right[3] if row.operator == "AND" else left[3] | right[3]
        v_flag = left[4] & right[4] if row.operator == "AND" else left[4] | right[4]
        d_ps.append(fisher_p(discovery["target_bad"].to_numpy(bool), d_flag))
        v_ps.append(fisher_p(validation["target_bad"].to_numpy(bool), v_flag))
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
    return result.sort_values(
        ["replicated_pair_fdr", "validation_fdr_q", "validation_balanced_accuracy"],
        ascending=[False, True, False],
    )


def main() -> int:
    matrix = core.stable_csv(core.FEATURE_MATRIX_OUT, low_memory=False)
    ce7_base = core.stable_csv(OUT / "ce_origin_fail_rejudged.csv", usecols=["candidate_id", "stage", "ticker"])
    discovery_raw = matrix[matrix["matrix_split"].eq("INTERNAL_DISCOVERY")].copy().reset_index(drop=True)
    validation_raw = matrix[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION")].copy().reset_index(drop=True)

    discovery_thresholds = fit_relative_thresholds(discovery_raw)
    validation_thresholds = fit_relative_thresholds(validation_raw)
    discovery = apply_relative_target(discovery_raw, discovery_thresholds)
    validation = apply_relative_target(validation_raw, validation_thresholds)
    pd.concat([discovery, validation], ignore_index=True).to_csv(core.FEATURE_MATRIX_OUT, index=False, compression="gzip")

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
    targets = pd.concat([
        discovery[target_columns].rename(columns={"matrix_split": "evaluation_split"}),
        validation[target_columns].rename(columns={"matrix_split": "evaluation_split"}),
    ], ignore_index=True)
    targets.to_csv(core.TARGET_OUT, index=False, compression="gzip")

    tested_features = v2.feature_columns(discovery, validation)
    power, fitted = core.univariate_analysis(discovery, validation, tested_features)
    power = v2.add_incremental_metrics(power, fitted, validation)
    power = adjust_univariate(power, fitted, discovery, validation)
    power.to_csv(core.FEATURE_POWER_OUT, index=False)

    pairs = core.pair_analysis(discovery, validation, power, fitted)
    pairs = adjust_pairs(pairs, fitted, discovery, validation)
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
    feature_values = pd.concat([discovery, validation], ignore_index=True).drop_duplicates("candidate_id")
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
        "analysis_version": "v4-relative-outcome-label-frozen-fdr-final",
        "verdict": verdict,
        "method": {
            "discovery_n": len(discovery),
            "validation_n": len(validation),
            "target": "same stage-relative outcome definition in each split",
            "tail_adjustment": "within split/stage expected worst MAE from log1p(trade_count), bottom-decile residual",
            "predictor_threshold": "selected on discovery only and frozen without retuning",
            "features_tested": len(power),
            "pairs_tested": len(pairs),
            "bootstrap": f"ticker-cluster {core.BOOTSTRAP_REPS} reps",
            "multiple_testing": "Benjamini-Hochberg FDR on frozen single-feature and pair families",
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
        "conclusion": (
            "A rulebook-static predictor survived frozen bootstrap and FDR."
            if verdict == "STATIC_PREDICTOR_FOUND"
            else "Some unadjusted direction remains, but no predictor survives all controls; static BLOCK is not justified."
            if verdict == "WEAK"
            else "No static predictor survived; dynamic observation logging is the only defensible CE validation path."
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
        "- 데이터: 룰북·내부 holdout·frozen OOS read-only",
        "- 설계·구현 변경: 없음",
        "",
        "## 1. 타깃 정의",
        "",
        "기간 길이와 변동성 차이로 absolute worst MAE를 직접 비교하지 않았다. 각 split·stage에서 같은 상대적 결과 정의를 적용했다.",
        "",
        "- IS→OOS 붕괴: gap 상위 10% + OOS/IS<=0.5",
        "- positive tail: `worst MAE ~ log1p(거래수)` 기대값 대비 residual 하위 10%",
        "- high-win large-loss: 승률 상위 25% + worst-loss/median-win 상위 25%",
        "",
        f"- discovery: {len(discovery):,}개, bad {int(discovery['target_bad'].sum()):,}개 ({discovery['target_bad'].mean()*100:.2f}%)",
        f"- frozen: {len(validation):,}개, bad {int(validation['target_bad'].sum()):,}개 ({validation['target_bad'].mean()*100:.2f}%)",
        "",
        "결과 라벨의 frozen quantile 계산은 predictor 튜닝이 아니다. 정적 predictor 경계는 discovery에서만 선택했다.",
        "",
        "## 2. CE 7개 타깃 타당성",
        "",
        f"- frozen 존재: {len(frozen_ce)}/7",
        f"- 결과상 bad: {int(frozen_ce['target_bad'].sum())}/{len(frozen_ce)}",
        f"- frozen 없음: {', '.join(summary['ce7']['missing_frozen_ids']) or '없음'}",
        "",
        "CE 동적 FAIL과 frozen 결과상 bad는 동일한 집합이 아니다. CE7 포섭을 맞추는 방향으로 특징 경계를 조정하지 않았다.",
        "",
        "## 3. 단일 특징 검증",
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
        "## 4. 2개 특징 조합",
        "",
        f"- 시도: {len(pairs):,}",
        f"- 미보정 frozen hit: {len(unadjusted_pairs):,}",
        f"- bootstrap+FDR 재현: {len(replicated_pairs):,}",
        "- 특징 2개 초과와 복잡 모델은 시도하지 않았다.",
        "",
        "## 5. 최종 판정",
        "",
    ]
    if verdict == "STATIC_PREDICTOR_FOUND":
        lines += ["**STATIC_PREDICTOR_FOUND**", "", "frozen bootstrap과 FDR까지 통과한 정적 경계가 확인됐다."]
    elif verdict == "WEAK":
        lines += [
            "**WEAK**", "",
            "일부 방향성이나 미보정 hit는 있으나 bootstrap CI와 frozen 다중검정을 동시에 통과하지 못했다.",
            "네 번째 정적 BLOCK 근거로는 부족하며 MONITOR 연구 후보 이상으로 올리면 안 된다.",
        ]
    else:
        lines += [
            "**NO_STATIC_PREDICTOR**", "",
            "단일 정적 특징과 2개 조합 모두 frozen OOS에서 재현되지 않았다.",
            "CE는 동적 observation logging이 유일한 검증 경로다.",
        ]
    lines += [
        "",
        "## 6. 커브피팅 점검",
        "",
        f"- IS 전용 또는 미보정 전용 특징·조합: {len(curve):,}개",
        "- frozen 경계 재튜닝 없음",
        "- frozen 단일 59개·pair 20개 family별 FDR 적용",
        "- v3·BOIL 통과 18개는 표본이 작아 순증 참고치로만 기록",
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
