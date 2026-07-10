from __future__ import annotations

"""CE 정적 예측 탐색의 명목 재현 조합을 다중검정·순증군으로 재검증한다."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SEED = 20260711
REPS = 10_000

POWER = OUT / "ce_static_feature_predictive_power.csv"
PAIR = OUT / "ce_static_pair_predictive_power.csv"
MATRIX = OUT / "ce_static_feature_matrix.csv.gz"
CE7 = OUT / "ce_static_ce7_capture.csv"
CURVE = OUT / "ce_static_curve_fit_notes.csv"
SUMMARY = OUT / "ce_static_predictor_summary.json"
READOUT = OUT / "ce_static_predictor_readout.md"
ROBUSTNESS = OUT / "ce_static_nominal_pair_robustness.csv"


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def boundary_flag(frame: pd.DataFrame, row: pd.Series) -> np.ndarray:
    threshold = np.where(
        frame["stage"].eq("stage2"),
        float(row["raw_boundary_stage2"]),
        float(row["raw_boundary_stage3"]),
    )
    values = frame[str(row["feature"])].to_numpy(float)
    if str(row["risk_direction"]) == ">=":
        return values >= threshold
    return values <= threshold


def risk_metrics(frame: pd.DataFrame, flag: np.ndarray) -> dict[str, Any]:
    y = frame["target_bad"].astype(bool).to_numpy()
    flagged = np.asarray(flag, dtype=bool)
    if flagged.sum() == 0 or (~flagged).sum() == 0:
        return {
            "n": len(frame), "bad_n": int(y.sum()), "flagged_n": int(flagged.sum()),
            "flagged_bad_n": int(y[flagged].sum()), "bad_rate_flagged": math.nan,
            "bad_rate_unflagged": math.nan, "risk_difference": math.nan,
        }
    return {
        "n": len(frame),
        "bad_n": int(y.sum()),
        "flagged_n": int(flagged.sum()),
        "flagged_bad_n": int(y[flagged].sum()),
        "bad_rate_flagged": float(y[flagged].mean()),
        "bad_rate_unflagged": float(y[~flagged].mean()),
        "risk_difference": float(y[flagged].mean() - y[~flagged].mean()),
    }


def ticker_cluster_bootstrap(frame: pd.DataFrame, flag: np.ndarray, seed: int) -> dict[str, float]:
    work = frame[["ticker", "target_bad"]].copy().reset_index(drop=True)
    work["flag"] = np.asarray(flag, dtype=bool)
    groups = {ticker: group.index.to_numpy() for ticker, group in work.groupby("ticker", sort=False)}
    tickers = np.asarray(list(groups), dtype=object)
    rng = np.random.default_rng(seed)
    values = np.empty(REPS, dtype=float)
    valid = 0
    for _ in range(REPS):
        sampled = rng.choice(tickers, size=len(tickers), replace=True)
        indices = np.concatenate([groups[ticker] for ticker in sampled])
        sample = work.loc[indices]
        flagged = sample["flag"].to_numpy(bool)
        if flagged.sum() == 0 or (~flagged).sum() == 0:
            continue
        y = sample["target_bad"].to_numpy(bool)
        values[valid] = y[flagged].mean() - y[~flagged].mean()
        valid += 1
    values = values[:valid]
    return {
        "bootstrap_reps": valid,
        "ci95_low": float(np.quantile(values, 0.025)),
        "ci95_high": float(np.quantile(values, 0.975)),
        "bonferroni20_ci_low": float(np.quantile(values, 0.00125)),
        "bonferroni20_ci_high": float(np.quantile(values, 0.99875)),
        "bootstrap_nonpositive_rate": float((values <= 0).mean()),
    }


def leave_one_ticker_out(frame: pd.DataFrame, flag: np.ndarray) -> dict[str, float]:
    work = frame[["ticker", "target_bad"]].copy().reset_index(drop=True)
    work["flag"] = np.asarray(flag, dtype=bool)
    values: list[float] = []
    for ticker in work["ticker"].drop_duplicates():
        sample = work[~work["ticker"].eq(ticker)]
        flagged = sample["flag"].to_numpy(bool)
        if flagged.sum() == 0 or (~flagged).sum() == 0:
            continue
        y = sample["target_bad"].to_numpy(bool)
        values.append(float(y[flagged].mean() - y[~flagged].mean()))
    return {
        "loto_min_risk_difference": min(values) if values else math.nan,
        "loto_max_risk_difference": max(values) if values else math.nan,
        "loto_negative_count": int(sum(value <= 0 for value in values)),
        "loto_ticker_n": len(values),
    }


def main() -> int:
    power = stable_csv(POWER, low_memory=False)
    pairs = stable_csv(PAIR, low_memory=False)
    matrix = stable_csv(MATRIX, low_memory=False)
    ce7 = stable_csv(CE7, low_memory=False)
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))

    nominal = pairs[pairs["replicated_pair"].fillna(False).astype(bool)]
    if len(nominal) != 1:
        raise AssertionError(f"expected one nominal pair, got {len(nominal)}")
    pair = nominal.iloc[0]
    left = power[power["feature"].eq(pair["left_feature"])].iloc[0]
    right = power[power["feature"].eq(pair["right_feature"])].iloc[0]

    matrix["nominal_pair_flag"] = boundary_flag(matrix, left) & boundary_flag(matrix, right)
    scopes: list[tuple[str, pd.DataFrame]] = [
        ("DISCOVERY_ALL", matrix[matrix["matrix_split"].eq("INTERNAL_DISCOVERY")]),
        (
            "DISCOVERY_EXISTING_GATE_SURVIVORS",
            matrix[matrix["matrix_split"].eq("INTERNAL_DISCOVERY") & matrix["existing_static_survivor"].fillna(False).astype(bool)],
        ),
        ("FROZEN_ALL", matrix[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION")]),
        (
            "FROZEN_EXISTING_GATE_SURVIVORS",
            matrix[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION") & matrix["existing_static_survivor"].fillna(False).astype(bool)],
        ),
        (
            "FROZEN_STAGE2",
            matrix[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION") & matrix["stage"].eq("stage2")],
        ),
        (
            "FROZEN_STAGE3",
            matrix[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION") & matrix["stage"].eq("stage3")],
        ),
    ]

    rows = []
    for index, (scope, frame) in enumerate(scopes):
        flags = frame["nominal_pair_flag"].to_numpy(bool)
        metrics = risk_metrics(frame, flags)
        bootstrap = ticker_cluster_bootstrap(frame, flags, SEED + index)
        loto = leave_one_ticker_out(frame, flags)
        rows.append({"scope": scope, **metrics, **bootstrap, **loto})
    robustness = pd.DataFrame(rows)

    frozen = matrix[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION")].copy()
    frozen_flags = frozen["nominal_pair_flag"].to_numpy(bool)
    for target in ("target_is_oos_collapse", "target_positive_tail_risk", "target_high_win_large_loss"):
        y = frozen[target].fillna(False).astype(bool).to_numpy()
        flagged = frozen_flags
        rows.append(
            {
                "scope": f"FROZEN_SUBTYPE_{target}",
                "n": len(frozen),
                "bad_n": int(y.sum()),
                "flagged_n": int(flagged.sum()),
                "flagged_bad_n": int(y[flagged].sum()),
                "bad_rate_flagged": float(y[flagged].mean()) if flagged.sum() else math.nan,
                "bad_rate_unflagged": float(y[~flagged].mean()) if (~flagged).sum() else math.nan,
                "risk_difference": float(y[flagged].mean() - y[~flagged].mean()) if flagged.sum() and (~flagged).sum() else math.nan,
            }
        )
    robustness = pd.DataFrame(rows)
    robustness.to_csv(ROBUSTNESS, index=False)

    frozen_all = robustness[robustness["scope"].eq("FROZEN_ALL")].iloc[0]
    frozen_incremental = robustness[robustness["scope"].eq("FROZEN_EXISTING_GATE_SURVIVORS")].iloc[0]
    stage2 = robustness[robustness["scope"].eq("FROZEN_STAGE2")].iloc[0]
    stage3 = robustness[robustness["scope"].eq("FROZEN_STAGE3")].iloc[0]

    pair_mask = pairs.index == nominal.index[0]
    pairs.loc[pair_mask, "nominal_95pct_replicated"] = True
    pairs.loc[pair_mask, "multiple_test_adjusted_replicated"] = bool(frozen_all["bonferroni20_ci_low"] > 0)
    pairs.loc[pair_mask, "frozen_bonferroni20_ci_low"] = frozen_all["bonferroni20_ci_low"]
    pairs.loc[pair_mask, "frozen_bonferroni20_ci_high"] = frozen_all["bonferroni20_ci_high"]
    pairs.loc[pair_mask, "incremental_validation_n"] = frozen_incremental["n"]
    pairs.loc[pair_mask, "incremental_validation_flagged_n"] = frozen_incremental["flagged_n"]
    pairs.loc[pair_mask, "incremental_validation_risk_difference"] = frozen_incremental["risk_difference"]
    pairs.loc[pair_mask, "incremental_validation_ci_low"] = frozen_incremental["ci95_low"]
    pairs.loc[pair_mask, "incremental_validation_ci_high"] = frozen_incremental["ci95_high"]
    pairs.loc[pair_mask, "stage2_validation_risk_difference"] = stage2["risk_difference"]
    pairs.loc[pair_mask, "stage3_validation_risk_difference"] = stage3["risk_difference"]
    pairs.loc[pair_mask, "robust_gate_candidate"] = False
    pairs.to_csv(PAIR, index=False)

    ce7["nominal_pair_flag"] = boundary_flag(ce7, left) & boundary_flag(ce7, right)
    ce7["nominal_pair_is_gate_candidate"] = False
    ce7.to_csv(CE7, index=False)
    available_bad = ce7[ce7["frozen_target_available"].fillna(False).astype(bool) & ce7["target_bad"].fillna(False).astype(bool)]
    ce7_capture = {
        "all_7_flagged_n": int(ce7["nominal_pair_flag"].sum()),
        "frozen_bad_available_n": len(available_bad),
        "frozen_bad_captured_n": int(available_bad["nominal_pair_flag"].sum()),
        "frozen_bad_capture_rate_pct": float(available_bad["nominal_pair_flag"].mean() * 100) if len(available_bad) else math.nan,
        "captured_ids": ce7.loc[ce7["nominal_pair_flag"], "candidate_id"].tolist(),
    }

    curve = stable_csv(CURVE, low_memory=False)
    extra = pd.DataFrame([
        {
            "type": "NOMINAL_PAIR_MULTIPLICITY_FAILURE",
            "feature_or_pair": f"{pair['left_feature']} AND {pair['right_feature']}",
            "discovery_auc": math.nan,
            "discovery_risk_difference": pair["discovery_risk_difference"],
            "validation_auc": math.nan,
            "validation_risk_difference": frozen_all["risk_difference"],
            "validation_ci_low": frozen_all["bonferroni20_ci_low"],
            "note": "nominal 95% frozen CI excludes zero, but 20-pair Bonferroni-family CI does not",
        },
        {
            "type": "NOMINAL_PAIR_INCREMENTAL_REVERSAL",
            "feature_or_pair": f"{pair['left_feature']} AND {pair['right_feature']}",
            "discovery_auc": math.nan,
            "discovery_risk_difference": pair["discovery_risk_difference"],
            "validation_auc": math.nan,
            "validation_risk_difference": frozen_incremental["risk_difference"],
            "validation_ci_low": frozen_incremental["ci95_low"],
            "note": "risk direction reverses among candidates surviving existing static gates",
        },
        {
            "type": "NOMINAL_PAIR_STAGE_INSTABILITY",
            "feature_or_pair": f"{pair['left_feature']} AND {pair['right_feature']}",
            "discovery_auc": math.nan,
            "discovery_risk_difference": pair["discovery_risk_difference"],
            "validation_auc": math.nan,
            "validation_risk_difference": stage2["risk_difference"],
            "validation_ci_low": stage2["ci95_low"],
            "note": f"frozen Stage2 RD={stage2['risk_difference']:.4f}, Stage3 RD={stage3['risk_difference']:.4f}",
        },
    ])
    curve = curve[~curve["type"].isin(extra["type"])].copy()
    curve = pd.concat([curve, extra], ignore_index=True)
    curve.to_csv(CURVE, index=False)

    final_verdict = "WEAK"
    summary["verdict"] = final_verdict
    summary["replicated_feature_n"] = 0
    summary["replicated_pair_n_nominal_95pct"] = 1
    summary["replicated_pair_n_multiplicity_adjusted"] = int(frozen_all["bonferroni20_ci_low"] > 0)
    summary["gate_candidate_n"] = 0
    summary["nominal_pair"] = {
        "left_feature": str(pair["left_feature"]),
        "operator": str(pair["operator"]),
        "right_feature": str(pair["right_feature"]),
        "boundaries": {
            "left_direction": str(left["risk_direction"]),
            "left_stage2": float(left["raw_boundary_stage2"]),
            "left_stage3": float(left["raw_boundary_stage3"]),
            "right_direction": str(right["risk_direction"]),
            "right_stage2": float(right["raw_boundary_stage2"]),
            "right_stage3": float(right["raw_boundary_stage3"]),
        },
        "frozen_all": frozen_all.to_dict(),
        "frozen_existing_gate_survivors": frozen_incremental.to_dict(),
        "frozen_stage2": stage2.to_dict(),
        "frozen_stage3": stage3.to_dict(),
        "ce7_capture": ce7_capture,
        "robust_gate_candidate": False,
    }
    summary["curve_fitting_checks"] = {
        "features_tested": int(summary.get("features_tested", len(power))),
        "pairs_tested": int(summary.get("pairs_tested", len(pairs))),
        "nominal_pair_ci95_excludes_zero": bool(frozen_all["ci95_low"] > 0),
        "bonferroni20_family_ci_excludes_zero": bool(frozen_all["bonferroni20_ci_low"] > 0),
        "incremental_survivor_direction_positive": bool(frozen_incremental["risk_difference"] > 0),
        "stage_direction_consistent": bool(stage2["risk_difference"] > 0 and stage3["risk_difference"] > 0),
    }
    summary["conclusion"] = (
        "One two-feature rule is nominally positive on broad frozen OOS, but it fails multiplicity-adjusted, "
        "stage-consistency and existing-gate-survivor checks and captures only one of three CE7 outcome-bad cases. "
        "It is WEAK monitoring evidence, not a fourth static gate."
    )
    summary["created_at_finalized"] = datetime.now(timezone.utc).isoformat()
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)), encoding="utf-8")

    top = power.head(12)
    target_definition = summary["target_definition"]
    lines = [
        "# CE형 개체의 정적 예측 특징 탐색",
        "",
        "- 최종 판정: **WEAK**",
        "- 게이트 후보: **0개**",
        "- 데이터: 룰북 정적 특징 + 내부 discovery 결과 + untouched frozen OOS",
        "- 동적 realized component·현재 시장 상태: 사용하지 않음",
        "- 설계·구현 변경: 없음",
        "",
        "## 1. 타깃 정의",
        "",
        "기존 과거 평균 PnL 음수 대상은 제외했다. discovery 결과분포에서 stage별 경계를 고정하고 frozen OOS에는 재튜닝 없이 적용했다.",
        "",
        "- IS→OOS 붕괴: PnL 격차 stage 상위 10%이면서 OOS/IS PnL 비율 <= 0.5",
        "- 양의 평균 extreme tail: 평균 PnL>0이면서 worst MAE stage 하위 10%",
        "- 고승률 대형손실: 승률 상위 25%, worst/median-win 상위 25%, top3 loss share 중앙값 이상",
        "",
        f"- discovery: {summary['cohorts']['discovery_total_n']:,}개, bad {summary['cohorts']['discovery_bad_n']:,}개",
        f"- frozen validation: {summary['cohorts']['validation_total_n']:,}개, bad {summary['cohorts']['validation_bad_n']:,}개",
        f"- 기존 v3·BOIL·history 정적 게이트 통과 frozen 순증군: {summary['cohorts']['validation_existing_static_survivor_n']:,}개, bad {summary['cohorts']['validation_existing_static_survivor_bad_n']:,}개",
        "",
        "Stage별 고정 타깃 경계:",
        "",
        f"- Stage2: collapse gap {target_definition['stage2']['collapse_gap_q90_pp']:.4f}%p, tail MAE {target_definition['stage2']['tail_worst_mae_q10_pct']:.4f}% 이하",
        f"- Stage3: collapse gap {target_definition['stage3']['collapse_gap_q90_pp']:.4f}%p, tail MAE {target_definition['stage3']['tail_worst_mae_q10_pct']:.4f}% 이하",
        "",
        "## 2. CE 7개 타깃 타당성",
        "",
        f"- frozen 결과 존재: {summary['ce7']['frozen_available']}/7",
        f"- 결과 타깃 bad: {summary['ce7']['frozen_target_bad']}/{summary['ce7']['frozen_available']}",
        f"- 결과 타깃 good: {summary['ce7']['frozen_target_good']}/{summary['ce7']['frozen_available']}",
        f"- frozen 없음: {', '.join(summary['ce7']['missing_frozen_ids'])}",
        "",
        "결과상 bad는 BOIL·BTE·CDE이고, ANET·BB·CE는 이 결과 타깃에 걸리지 않았다. CWK는 frozen 결과가 없다. 따라서 CE 동적 FAIL 7개는 단일한 outcome-collapse 집합이 아니다.",
        "",
        "## 3. 단일 특징",
        "",
        "57개 단일 정적 특징 중 IS와 frozen bootstrap 기준을 동시에 만족한 특징은 **0개**다.",
        "",
        "| 특징 | IS AUC | IS 위험차 | frozen AUC | frozen 위험차 | frozen 위험차 95% CI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in top.itertuples(index=False):
        lines.append(
            f"| {row.feature} | {row.discovery_auc:.3f} | {row.discovery_risk_difference:.3f} | "
            f"{row.validation_auc:.3f} | {row.validation_risk_difference:.3f} | "
            f"[{row.validation_risk_difference_ci_low:.3f}, {row.validation_risk_difference_ci_high:.3f}] |"
        )
    lines += [
        "",
        "IS에서 강했던 stored expectancy·fitness·조건 fire-rate 계열은 frozen에서 CI가 0을 포함하거나 방향이 반전됐다.",
        "",
        "## 4. 명목 2개 특징 조합",
        "",
        f"`stored_validation_fitness >= stage 65백분위` AND `IS-validation fitness gap <= stage 40백분위`",
        "",
        f"- Stage2 경계: validation fitness >= {left['raw_boundary_stage2']:.4f}, gap <= {right['raw_boundary_stage2']:.4f}",
        f"- Stage3 경계: validation fitness >= {left['raw_boundary_stage3']:.4f}, gap <= {right['raw_boundary_stage3']:.4f}",
        f"- broad frozen: {int(frozen_all['flagged_n'])}/{int(frozen_all['n'])} flag, 위험차 {frozen_all['risk_difference']:.4f}, nominal 95% CI [{frozen_all['ci95_low']:.4f}, {frozen_all['ci95_high']:.4f}]",
        f"- 20개 조합 다중검정 family CI: [{frozen_all['bonferroni20_ci_low']:.4f}, {frozen_all['bonferroni20_ci_high']:.4f}]",
        f"- 기존 정적 게이트 통과 frozen 18개: {int(frozen_incremental['flagged_n'])} flag, 위험차 **{frozen_incremental['risk_difference']:.4f}**, 95% CI [{frozen_incremental['ci95_low']:.4f}, {frozen_incremental['ci95_high']:.4f}]",
        f"- Stage2 frozen 위험차: {stage2['risk_difference']:.4f}",
        f"- Stage3 frozen 위험차: {stage3['risk_difference']:.4f}",
        "",
        "broad frozen에서는 명목상 양수지만, 실제 네 번째 게이트 순증군에서는 방향이 음수로 반전되고 stage별 방향도 일치하지 않는다. 따라서 재현된 정적 게이트로 인정하지 않는다.",
        "",
        "## 5. CE7 포섭",
        "",
        f"- CE7 전체 포섭: {ce7_capture['all_7_flagged_n']}/7",
        f"- frozen 결과상 bad 포섭: {ce7_capture['frozen_bad_captured_n']}/{ce7_capture['frozen_bad_available_n']} ({ce7_capture['frozen_bad_capture_rate_pct']:.2f}%)",
        f"- 포섭 후보: {', '.join(ce7_capture['captured_ids']) or '없음'}",
        "",
        "명목 조합은 BOIL만 잡고 BTE·CDE를 놓친다. BOIL은 이미 v3/BOIL 정적 게이트 영역이므로 네 번째 게이트의 순증 가치가 없다.",
        "",
        "## 6. 최종 판정",
        "",
        "**WEAK**",
        "",
        "- 단일 특징 재현 0개",
        "- 2개 특징 조합은 broad frozen에서만 명목 유의",
        "- 다중검정 보정·순증군·stage 일관성 검증 실패",
        "- CE7 outcome-bad 포섭 1/3이며 포섭 대상은 기존 게이트 영역인 BOIL",
        "",
        "따라서 네 번째 STATIC BLOCK 후보로 검토할 근거는 없다. 연구 MONITOR 수준의 약한 패턴일 뿐이며, CE 검증의 주 경로는 동적 observation logging으로 유지한다.",
        "",
        "## 7. 커브피팅 점검",
        "",
        f"- 단일 특징 {summary['features_tested']}개, 2개 특징 조합 {summary['pairs_tested']}개 탐색",
        "- frozen 경계 재튜닝 없음",
        "- ticker-cluster bootstrap 수행",
        "- 20개 조합 family-wise CI 별도 확인",
        "- 기존 게이트 통과 순증군과 Stage2/Stage3 방향 일관성 확인",
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
        "verdict": final_verdict,
        "frozen_nominal_rd": frozen_all["risk_difference"],
        "frozen_nominal_ci95": [frozen_all["ci95_low"], frozen_all["ci95_high"]],
        "frozen_bonferroni20_ci": [frozen_all["bonferroni20_ci_low"], frozen_all["bonferroni20_ci_high"]],
        "incremental_rd": frozen_incremental["risk_difference"],
        "ce7_bad_capture": ce7_capture,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
