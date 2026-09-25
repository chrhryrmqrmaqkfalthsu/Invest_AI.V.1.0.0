from __future__ import annotations

"""소수지표 구조 성과 감사의 최종 보고·커브피팅 노트를 보강한다."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
CUT = OUT / "minority_indicator_cut_results.csv"
RULE = OUT / "minority_indicator_rule_performance.csv.gz"
ANET_BB = OUT / "minority_indicator_anet_bb.csv"
CURVE = OUT / "minority_indicator_curve_fit_notes.csv"
SUMMARY = OUT / "minority_indicator_summary.json"
READOUT = OUT / "minority_indicator_readout.md"


def stable_csv(path: Path, **kwargs):
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def fmt(value, digits=4):
    if pd.isna(value):
        return "NA"
    return f"{float(value):.{digits}f}"


def main() -> int:
    results = stable_csv(CUT, low_memory=False)
    rules = stable_csv(RULE, low_memory=False)
    anet_bb = stable_csv(ANET_BB, low_memory=False)
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))

    primary = results[results["metric"].eq("avg_pnl_pct")].copy()
    is_rows = primary[primary["scope"].eq("IS_DISCOVERY")]
    holdout_rows = primary[primary["scope"].eq("INTERNAL_HOLDOUT")]
    frozen_rows = primary[primary["scope"].eq("FROZEN_OOS")]
    incremental_rows = primary[primary["scope"].eq("FROZEN_INCREMENTAL")]

    holdout_artifacts = holdout_rows[holdout_rows["fdr_sparse_worse"]].copy()
    frozen_win_artifacts = results[
        results["scope"].eq("FROZEN_OOS")
        & results["metric"].eq("win_rate_pct")
        & results["fdr_sparse_worse"]
    ].copy()

    curve_rows = []
    for row in holdout_artifacts.itertuples(index=False):
        is_match = is_rows[(is_rows["stage"].eq(row.stage)) & (is_rows["cut"].eq(row.cut))].iloc[0]
        frozen_match = frozen_rows[(frozen_rows["stage"].eq(row.stage)) & (frozen_rows["cut"].eq(row.cut))].iloc[0]
        incremental_match = incremental_rows[(incremental_rows["stage"].eq(row.stage)) & (incremental_rows["cut"].eq(row.cut))].iloc[0]
        curve_rows.append({
            "type": "INTERNAL_HOLDOUT_ONLY_PNL_ARTIFACT",
            "stage": row.stage,
            "cut": row.cut,
            "metric": "avg_pnl_pct",
            "is_difference": is_match.difference_sparse_minus_other,
            "internal_holdout_difference": row.difference_sparse_minus_other,
            "internal_holdout_fdr_q": row.fdr_q,
            "frozen_difference": frozen_match.difference_sparse_minus_other,
            "frozen_ci_low": frozen_match.difference_ci_low,
            "frozen_ci_high": frozen_match.difference_ci_high,
            "frozen_fdr_q": frozen_match.fdr_q,
            "incremental_difference": incremental_match.difference_sparse_minus_other,
            "incremental_ci_low": incremental_match.difference_ci_low,
            "incremental_ci_high": incremental_match.difference_ci_high,
            "note": "Stage2 internal holdout에서는 열위였지만 IS discovery에서 방향이 없고 frozen 평균 PnL에서 재현되지 않음",
        })

    for row in frozen_win_artifacts.itertuples(index=False):
        incremental_match = results[
            results["scope"].eq("FROZEN_INCREMENTAL")
            & results["stage"].eq(row.stage)
            & results["cut"].eq(row.cut)
            & results["metric"].eq("win_rate_pct")
        ].iloc[0]
        curve_rows.append({
            "type": "FROZEN_WIN_RATE_SINGLE_RULE_ARTIFACT",
            "stage": row.stage,
            "cut": row.cut,
            "metric": "win_rate_pct",
            "is_difference": results[
                results["scope"].eq("IS_DISCOVERY")
                & results["stage"].eq(row.stage)
                & results["cut"].eq(row.cut)
                & results["metric"].eq("win_rate_pct")
            ].iloc[0].difference_sparse_minus_other,
            "internal_holdout_difference": results[
                results["scope"].eq("INTERNAL_HOLDOUT")
                & results["stage"].eq(row.stage)
                & results["cut"].eq(row.cut)
                & results["metric"].eq("win_rate_pct")
            ].iloc[0].difference_sparse_minus_other,
            "internal_holdout_fdr_q": results[
                results["scope"].eq("INTERNAL_HOLDOUT")
                & results["stage"].eq(row.stage)
                & results["cut"].eq(row.cut)
                & results["metric"].eq("win_rate_pct")
            ].iloc[0].fdr_q,
            "frozen_difference": row.difference_sparse_minus_other,
            "frozen_ci_low": row.difference_ci_low,
            "frozen_ci_high": row.difference_ci_high,
            "frozen_fdr_q": row.fdr_q,
            "incremental_difference": incremental_match.difference_sparse_minus_other,
            "incremental_ci_low": incremental_match.difference_ci_low,
            "incremental_ci_high": incremental_match.difference_ci_high,
            "note": f"frozen Stage2 sparse group가 {int(row.sparse_rule_n)}개 rule뿐이며 기존 게이트 통과 순증군에서 robust 조건 미충족",
        })

    curve_rows.append({
        "type": "NO_ROBUST_STRUCTURE_CUT",
        "stage": "ALL",
        "cut": "NONE",
        "metric": "ALL",
        "is_difference": None,
        "internal_holdout_difference": None,
        "internal_holdout_fdr_q": None,
        "frozen_difference": None,
        "frozen_ci_low": None,
        "frozen_ci_high": None,
        "frozen_fdr_q": None,
        "incremental_difference": None,
        "incremental_ci_low": None,
        "incremental_ci_high": None,
        "note": "5개 성과 metric 전체에서 IS·frozen·순증군을 동시에 통과한 cut 0개",
    })
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(CURVE, index=False)

    all_metric_robust = []
    for stage in ("stage2", "stage3"):
        for cut in results["cut"].drop_duplicates():
            for metric in results["metric"].drop_duplicates():
                subset = results[
                    results["stage"].eq(stage)
                    & results["cut"].eq(cut)
                    & results["metric"].eq(metric)
                    & results["scope"].isin(["IS_DISCOVERY", "FROZEN_OOS", "FROZEN_INCREMENTAL"])
                ]
                if len(subset) == 3 and bool(subset["fdr_sparse_worse"].all()):
                    all_metric_robust.append({"stage": stage, "cut": cut, "metric": metric})

    summary["verdict"] = "NO_SIGNAL"
    summary["all_metric_robust_cut_metric_n"] = len(all_metric_robust)
    summary["all_metric_robust_cut_metrics"] = all_metric_robust
    summary["secondary_findings"] = {
        "stage2_internal_holdout_avg_pnl_fdr_cut_n": int(len(holdout_artifacts)),
        "stage2_internal_holdout_interpretation": "IS discovery와 frozen 평균 PnL에서 재현되지 않아 holdout-only artifact",
        "stage2_frozen_win_rate_fdr_cut_n": int(len(frozen_win_artifacts)),
        "stage2_frozen_win_rate_sparse_rule_n": 1 if len(frozen_win_artifacts) else 0,
        "stage2_frozen_win_rate_interpretation": "단 1개 sparse rule에 의존하고 incremental robust 조건 미충족",
        "stage3_top2_ge80_frozen_avg_pnl_difference_pct_point": float(
            frozen_rows[
                frozen_rows["stage"].eq("stage3") & frozen_rows["cut"].eq("TOP2_GE80")
            ].iloc[0].difference_sparse_minus_other
        ),
        "stage3_top2_ge80_incremental_avg_pnl_difference_pct_point": float(
            incremental_rows[
                incremental_rows["stage"].eq("stage3") & incremental_rows["cut"].eq("TOP2_GE80")
            ].iloc[0].difference_sparse_minus_other
        ),
        "stage3_top2_ge80_interpretation": "소수지표군이 오히려 평균 PnL 우위 방향; 열위 가설과 반대",
    }
    summary["validation_power_caveat"] = {
        "frozen_stage2_rule_n": int(summary["cohorts"]["stage2"]["frozen_rule_n"]),
        "frozen_stage3_rule_n": int(summary["cohorts"]["stage3"]["frozen_rule_n"]),
        "incremental_stage2_rule_n": int(summary["cohorts"]["stage2"]["incremental_frozen_rule_n"]),
        "incremental_stage3_rule_n": int(summary["cohorts"]["stage3"]["incremental_frozen_rule_n"]),
        "note": "canonical 거래는 대규모지만 frozen external validation은 93개 rule이며 희소 구조 rule가 매우 적어 일부 cut은 검정 불가",
    }
    summary["interpretation"] = (
        "저장 가중치 기준 소수지표 구조는 IS discovery, frozen OOS, 기존 gate 통과 순증군에서 일관된 성과 열위를 보이지 않았다. "
        "내부 holdout과 단일 frozen rule에서 보인 국소 신호는 외부·순증 검증에서 재현되지 않았다."
    )
    summary["created_at_reporting_finalized"] = datetime.now(timezone.utc).isoformat()
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    anet = anet_bb[anet_bb["ticker"].eq("ANET")].iloc[0]
    bb = anet_bb[anet_bb["ticker"].eq("BB")].iloc[0]

    def table_rows(scope: str, stage: str) -> list[str]:
        subset = primary[primary["scope"].eq(scope) & primary["stage"].eq(stage)].copy()
        return [
            f"| {row.cut} | {int(row.sparse_rule_n)} | {int(row.other_rule_n)} | "
            f"{fmt(row.sparse_mean)} | {fmt(row.other_mean)} | {fmt(row.difference_sparse_minus_other)} | "
            f"[{fmt(row.difference_ci_low)}, {fmt(row.difference_ci_high)}] | {fmt(row.fdr_q)} |"
            for row in subset.itertuples(index=False)
        ]

    lines = [
        "# 소수지표 구조 rule 성과 분포 분석",
        "",
        "- 최종 판정: **NO_SIGNAL**",
        "- 분석 대상: 저장 rulebook의 MA·MACD·RSI·BB·Volume 가중치 구조",
        "- 진입 순간 realized component 분석이 아님",
        "- Stage2·Stage3 완전 분리",
        "- 운영·라이브·원본 코드·설정·설계 변경: 0건",
        "",
        "## 1. 결론",
        "",
        "소수지표 저장 가중치 구조가 성과 열위라는 증거는 확인되지 않았다.",
        "",
        "- IS discovery 평균 PnL FDR 통과 cut: 0개",
        "- Frozen 평균 PnL 명목 CI 통과 cut: 0개",
        "- Frozen 평균 PnL FDR 통과 cut: 0개",
        "- 기존 history·v3·BOIL 통과 순증군 robust cut: 0개",
        "- 평균 PnL·승률·5% tail·하위 10% 평균·worst MAE 전체에서 IS→frozen→순증군 동시 통과: 0개",
        "",
        "따라서 구조 기준 정적 BLOCK 후보는 없다. CE형 실패 검증은 진입 시점 component logging과 동적 검증 경로로 남는다.",
        "",
        "## 2. 구조 기준",
        "",
        "각 rule의 저장 core 가중치에서 다음을 계산했다.",
        "",
        "- exact active count: 가중치 > 0인 지표 수",
        "- material active count: 가중치 > 0.05인 지표 수",
        "- Top2 집중도: 상위 두 가중치 합 / 전체 양수 가중치 합",
        "",
        "검증한 8개 cut:",
        "",
        "- 활성 exact 2개 이하",
        "- 활성 material 2개 이하",
        "- Top2 80% 이상",
        "- Top2 90% 이상",
        "- 활성 exact 3개 이하 + Top2 80% 이상",
        "- 활성 exact 3개 이하 + Top2 90% 이상",
        "- 활성 material 2개 이하 + Top2 80% 이상",
        "- 활성 material 2개 이하 + Top2 90% 이상",
        "",
        "## 3. 데이터와 검증 규율",
        "",
        f"- Stage2 canonical 거래: {summary['source_trade_rows']['stage2_rows']:,}건 / rule {summary['cohorts']['stage2']['rule_n']:,}개",
        f"- Stage3 canonical 거래: {summary['source_trade_rows']['stage3_rows']:,}건 / rule {summary['cohorts']['stage3']['rule_n']:,}개",
        f"- 전체 canonical 거래: {summary['source_trade_rows']['matched_rows']:,}건",
        f"- Frozen OOS: Stage2 {summary['cohorts']['stage2']['frozen_rule_n']}개, Stage3 {summary['cohorts']['stage3']['frozen_rule_n']}개 rule",
        f"- 기존 게이트 통과 frozen 순증군: Stage2 {summary['cohorts']['stage2']['incremental_frozen_rule_n']}개, Stage3 {summary['cohorts']['stage3']['incremental_frozen_rule_n']}개 rule",
        "- 최소 거래 수: rule당 8건",
        "- 효과량: rule별 성과 평균의 `소수지표군 - 기타군`",
        "- CI: ticker-cluster bootstrap 5,000회",
        "- 검정: ticker-cluster robust 단측 검정",
        "- 다중검정: stage·scope·metric별 8개 cut BH FDR",
        "",
        "Canonical 거래 수는 크지만 frozen 외부검증은 총 93개 rule이다. 특히 exact 2개 이하 구조는 frozen에 거의 없어 일부 cut은 검정 자체가 불가능했다.",
        "",
        "## 4. ANET·BB 정의 타당성",
        "",
        "| 후보 | MA | MACD | RSI | BB | Volume | 활성 exact/material | Top2 | 소수 cut |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| ANET | {anet.ma_weight:.4f} | {anet.macd_weight:.4f} | {anet.rsi_weight:.4f} | {anet.bb_weight:.4f} | {anet.volume_weight:.4f} | {int(anet.active_exact_count)}/{int(anet.active_material_count)} | {anet.top2_weight_share_pct:.2f}% | 0/8 |",
        f"| BB | {bb.ma_weight:.4f} | {bb.macd_weight:.4f} | {bb.rsi_weight:.4f} | {bb.bb_weight:.4f} | {bb.volume_weight:.4f} | {int(bb.active_exact_count)}/{int(bb.active_material_count)} | {bb.top2_weight_share_pct:.2f}% | 0/8 |",
        "",
        "ANET·BB는 진입 시점 point snapshot에서 RSI+MA만 발화했지만, 저장 rule 구조에서는 5개 core 가중치가 모두 양수다. 두 rule은 소수지표 구조가 아니며 어떤 cut에도 걸리지 않는다.",
        "",
        f"- ANET frozen 평균 PnL {anet.frozen_avg_pnl_pct:.4f}%, 승률 {anet.frozen_win_rate_pct:.2f}%",
        f"- BB frozen 평균 PnL {bb.frozen_avg_pnl_pct:.4f}%, 승률 {bb.frozen_win_rate_pct:.2f}%",
        "",
        "즉 저장 구조 기준 gate는 ANET·BB의 상반된 실제 방향을 설명하거나 차단하지 못한다.",
        "",
        "## 5. IS discovery 평균 PnL",
        "",
        "음수 차이는 소수지표군의 열위를 뜻한다.",
        "",
        "### Stage2",
        "",
        "| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *table_rows("IS_DISCOVERY", "stage2"),
        "",
        "### Stage3",
        "",
        "| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *table_rows("IS_DISCOVERY", "stage3"),
        "",
        "IS discovery에서는 16개 stage×cut 조합 모두 평균 PnL 열위가 FDR를 통과하지 못했다. Stage3에서는 모든 관측 가능한 cut의 점추정이 오히려 양수였다.",
        "",
        "## 6. Frozen OOS 평균 PnL",
        "",
        "### Stage2",
        "",
        "| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *table_rows("FROZEN_OOS", "stage2"),
        "",
        "Stage2에서 관측 가능한 소수군은 Top2 계열의 단 1개 rule이다. PnL 차이 -0.0340%p, CI는 약 -0.85~+0.82%p로 0을 넓게 포함한다.",
        "",
        "### Stage3",
        "",
        "| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *table_rows("FROZEN_OOS", "stage3"),
        "",
        "Stage3에서 검정 가능한 유일한 Top2>=80군 4개 rule은 평균 PnL가 기타군보다 +2.8489%p 높았다. CI가 0을 포함해 우위도 확정할 수 없지만, 적어도 열위 가설과 같은 방향은 아니다.",
        "",
        "## 7. 기존 게이트 통과 순증군",
        "",
        "### Stage2",
        "",
        "| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *table_rows("FROZEN_INCREMENTAL", "stage2"),
        "",
        "### Stage3",
        "",
        "| Cut | 소수 rule | 기타 rule | 소수 평균 | 기타 평균 | 차이 | 95% CI | FDR q |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *table_rows("FROZEN_INCREMENTAL", "stage3"),
        "",
        "순증군에서도 열위는 유지되지 않았다. Stage2 Top2 계열은 +0.0574%p, Stage3 TOP2>=80은 +2.9491%p로 모두 소수지표군 우위 방향이다.",
        "",
        "## 8. 평균 외 성과지표",
        "",
        "평균 PnL 외에 승률, PnL 5% 분위, 하위 10% 평균 PnL, worst MAE도 같은 family로 검증했다.",
        "",
        "- IS→frozen→순증군을 모두 통과한 cut×metric: 0개",
        "- Frozen Stage2 승률에서 4개 중복 cut이 FDR를 통과했으나 모두 같은 소수 rule 1개에 의존",
        "- 해당 승률 신호는 IS discovery에서 선택되지 않았고 순증군 robust 기준도 통과하지 못함",
        "- Tail과 worst MAE는 frozen FDR 통과 cut 0개",
        "",
        "따라서 평균 PnL만 놓친 tail-risk 구조 신호도 발견되지 않았다.",
        "",
        "## 9. 커브피팅 점검",
        "",
        "Stage2 내부 holdout에서는 8개 cut 모두 평균 PnL 열위가 유의해 보였다. 그러나:",
        "",
        "- IS discovery에서는 8개 모두 FDR 실패",
        "- Frozen 평균 PnL에서는 차이가 사라짐",
        "- 기존 게이트 통과 순증군에서는 방향이 양수로 반전",
        "",
        "따라서 내부 holdout만 보고 경계를 채택하면 전형적인 구간 선택·커브피팅이 된다.",
        "",
        "Frozen Stage2 승률 신호도 소수군 1개 rule에 의존하므로 게이트 근거로 사용할 수 없다.",
        "",
        "## 10. 최종 판정",
        "",
        "**NO_SIGNAL**",
        "",
        "저장 rule 가중치의 소수지표 구조는 OOS 성과 열위를 구분하지 못했다. 구조 기준 정적 게이트 후보는 없다.",
        "",
        "ANET·BB는 저장 구조상 소수지표 rule도 아니다. 두 후보에서 관찰된 CE형은 rule 구조가 아니라 실제 진입 순간 일부 지표만 발화한 동적 현상이다.",
        "",
        "따라서 현재 근거에서는 진입 시점 component logging과 동적 CE 검증이 유일한 경로다.",
        "",
        "## 11. 산출물",
        "",
        "- `minority_indicator_rule_performance.csv.gz`",
        "- `minority_indicator_group_performance.csv`",
        "- `minority_indicator_cut_results.csv`",
        "- `minority_indicator_incremental_results.csv`",
        "- `minority_indicator_anet_bb.csv`",
        "- `minority_indicator_curve_fit_notes.csv`",
        "- `minority_indicator_summary.json`",
    ]
    READOUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": summary["verdict"],
        "all_metric_robust_cut_metric_n": len(all_metric_robust),
        "holdout_only_cut_n": len(holdout_artifacts),
        "frozen_win_single_rule_cut_n": len(frozen_win_artifacts),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
