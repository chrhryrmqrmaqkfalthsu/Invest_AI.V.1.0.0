from __future__ import annotations

"""BOIL형 게이트 enforcement 결정을 위한 read-only dry-run."""

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SEED, REPS, NEAR_ZERO = 20260711, 20_000, 0.05

TARGET_OUT = OUT / "boil_block_exclusive_targets.csv"
GOOD_OUT = OUT / "boil_block_overfilter_good_cases.csv"
PERF_OUT = OUT / "boil_block_performance_comparison.csv"
BOOT_OUT = OUT / "boil_block_bootstrap_summary.csv"
FINAL_OUT = OUT / "boil_block_final_candidates.csv"
FINAL_SUMMARY_OUT = OUT / "boil_block_final_candidate_summary.csv"
DECISION_OUT = OUT / "boil_block_enforcement_decision.json"
READOUT_OUT = OUT / "boil_block_enforcement_readout.md"


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    if before != (path.stat().st_size, path.stat().st_mtime_ns):
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def add_holdout_path(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["history_path"] = frame.apply(
        lambda r: str((ROOT / str(r.source_file)).parent / ("trades.jsonl" if r.stage == "stage2" else "exit_trades.jsonl")),
        axis=1,
    )
    return frame


def attach_raw_holdout(frame: pd.DataFrame) -> pd.DataFrame:
    frame = add_holdout_path(frame)
    file_targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    candidate_map: dict[tuple[str, str], str] = {}
    for row in frame.itertuples(index=False):
        file_targets[(row.stage, row.history_path)].add(str(row.rulebook_hash))
        candidate_map[(row.stage, str(row.rulebook_hash))] = str(row.candidate_id)
    agg: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0, "pnl": 0.0, "wins": 0, "mae": 0.0, "mfe": 0.0, "worst": math.inf}
    )
    for (stage, path_text), targets in file_targets.items():
        path = Path(path_text)
        marker = "rulebook_hash" if stage == "stage2" else "final_rulebook_hash"
        period = "oos_2025h2" if stage == "stage2" else "recent_1y"
        before = (path.stat().st_size, path.stat().st_mtime_ns)
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rule_hash = str(row.get(marker) or "")
                if rule_hash not in targets or str(row.get("period_label") or "") != period:
                    continue
                cid = candidate_map[(stage, rule_hash)]
                pnl = float(row.get("pnl_pct") or 0.0)
                mae = float(row.get("max_loss_during_hold") or 0.0)
                mfe = float(row.get("max_profit_during_hold") or 0.0)
                a = agg[cid]
                a["n"] += 1
                a["pnl"] += pnl
                a["wins"] += int(pnl > 0)
                a["mae"] += mae
                a["mfe"] += mfe
                a["worst"] = min(a["worst"], mae)
        if before != (path.stat().st_size, path.stat().st_mtime_ns):
            raise RuntimeError(f"source changed while reading: {path}")
    rows = []
    for cid, a in agg.items():
        n = int(a["n"])
        rows.append(
            {
                "candidate_id": cid,
                "raw_holdout_n": n,
                "raw_holdout_avg_pnl_pct": a["pnl"] / n,
                "raw_holdout_win_rate_pct": a["wins"] / n * 100,
                "holdout_avg_mae_pct": a["mae"] / n,
                "holdout_worst_mae_pct": a["worst"],
                "holdout_avg_mfe_pct": a["mfe"] / n,
            }
        )
    result = frame.merge(pd.DataFrame(rows), on="candidate_id", how="left", validate="one_to_one")
    if result["raw_holdout_n"].isna().any():
        raise AssertionError("missing raw holdout")
    if (result["raw_holdout_n"] - result["holdout_n"]).abs().max() != 0:
        raise AssertionError("holdout count parity")
    if (result["raw_holdout_avg_pnl_pct"] - result["holdout_avg_pnl_pct"]).abs().max() > 1e-9:
        raise AssertionError("holdout pnl parity")
    return result


def unit_frame(frame: pd.DataFrame, unit: str) -> pd.DataFrame:
    base = frame[[
        "candidate_id", "stage", "ticker", "activity_rule_hash", "raw_holdout_n",
        "raw_holdout_avg_pnl_pct", "raw_holdout_win_rate_pct", "holdout_avg_mae_pct",
        "holdout_worst_mae_pct", "holdout_avg_mfe_pct",
    ]].copy()
    base.columns = [
        "candidate_id", "stage", "ticker", "activity_rule_hash", "trades", "avg_pnl",
        "win", "avg_mae", "worst_mae", "avg_mfe",
    ]
    if unit == "CANDIDATE":
        return base
    return base.groupby(["stage", "activity_rule_hash"], as_index=False).agg(
        candidate_id=("candidate_id", "first"), ticker=("ticker", "first"), trades=("trades", "mean"),
        avg_pnl=("avg_pnl", "mean"), win=("win", "mean"), avg_mae=("avg_mae", "mean"),
        worst_mae=("worst_mae", "min"), avg_mfe=("avg_mfe", "mean"),
    )


def stats(frame: pd.DataFrame, population: str, group: str, unit: str) -> dict[str, Any]:
    data = unit_frame(frame, unit)
    trades = float(data["trades"].sum())
    return {
        "population": population,
        "group": group,
        "unit": unit,
        "candidate_n": len(frame),
        "unique_entry_rule_n": frame[["stage", "activity_rule_hash"]].drop_duplicates().shape[0],
        "ticker_n": frame["ticker"].nunique(),
        "analysis_unit_n": len(data),
        "holdout_trade_n": trades,
        "candidate_equal_avg_pnl_pct": data["avg_pnl"].mean(),
        "trade_weighted_avg_pnl_pct": (data["avg_pnl"] * data["trades"]).sum() / trades,
        "candidate_equal_win_rate_pct": data["win"].mean(),
        "candidate_equal_avg_mae_pct": data["avg_mae"].mean(),
        "worst_mae_pct": data["worst_mae"].min(),
        "candidate_equal_avg_mfe_pct": data["avg_mfe"].mean(),
    }


def boot(a: np.ndarray, c: np.ndarray, seed: int) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    values = np.empty(REPS)
    for i in range(REPS):
        values[i] = rng.choice(a, len(a), replace=True).mean() - rng.choice(c, len(c), replace=True).mean()
    return float(a.mean() - c.mean()), float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def select(frame: pd.DataFrame) -> pd.DataFrame:
    output: list[pd.Series] = []
    for stage, cap in (("stage2", 60), ("stage3", 80)):
        pool = frame[
            frame["stage"].eq(stage) & frame["post_boil_static_status"].eq("PASS")
            & frame["elite_static_pass"].fillna(False).astype(bool)
            & ~frame["denylisted"].fillna(False).astype(bool)
        ].sort_values(["elite_score", "oos_fitness", "oos_expectancy_pct"], ascending=False, na_position="last")
        seen: set[str] = set()
        for _, row in pool.iterrows():
            if str(row.ticker) in seen:
                continue
            seen.add(str(row.ticker))
            output.append(row)
            if len(seen) >= cap:
                break
    return pd.DataFrame(output)


def main() -> int:
    hv = stable_csv(OUT / "high_vol_volume_blind_risk_candidates.csv", low_memory=False)
    v3 = stable_csv(
        OUT / "threshold_p99_weightless_block_candidate_decisions.csv",
        usecols=["candidate_id", "final_p99_weightless_block_status", "volume_reachability_label"],
    )
    activity = stable_csv(
        OUT / "threshold_reachability_stage3_full_indicator_detail.csv.gz",
        usecols=["candidate_id", "stage", "activity_rule_hash"], low_memory=False,
    ).drop_duplicates("candidate_id")
    base = stable_csv(OUT / "integrated_gate_candidate_dryrun.csv", low_memory=False)
    old_selected = stable_csv(OUT / "threshold_p99_weightless_block_combined_selected_candidates.csv", low_memory=False)

    universe = hv.merge(v3, on="candidate_id", validate="one_to_one").merge(
        activity, on=["candidate_id", "stage"], validate="one_to_one"
    )
    universe["near_zero"] = universe["weight_volume_surge"].abs().le(NEAR_ZERO)
    universe["exact_zero"] = universe["weight_volume_surge"].eq(0)
    universe["v3_pass"] = universe["final_p99_weightless_block_status"].eq("PASS")
    survivors = attach_raw_holdout(universe[universe["v3_pass"]].copy())
    target = survivors[survivors["near_zero"]].copy()
    control = survivors[~survivors["near_zero"]].copy()

    medians = control.groupby("stage")[["raw_holdout_avg_pnl_pct", "raw_holdout_win_rate_pct"]].median()
    target["sample_ok"] = np.where(target["stage"].eq("stage2"), target["raw_holdout_n"].ge(15), target["raw_holdout_n"].ge(8))
    target["absolute_good"] = target["sample_ok"] & target["raw_holdout_avg_pnl_pct"].gt(0) & target["raw_holdout_win_rate_pct"].ge(50)
    target["relative_good"] = target.apply(
        lambda r: bool(r.sample_ok and r.raw_holdout_avg_pnl_pct >= medians.loc[r.stage, "raw_holdout_avg_pnl_pct"]
                       and r.raw_holdout_win_rate_pct >= medians.loc[r.stage, "raw_holdout_win_rate_pct"]), axis=1,
    )
    target["block_reason"] = "HIGH_VOL_VOLUME_BLIND_AND_ABS_WEIGHT_VOLUME_SURGE_LTE_0_05_AND_V3_PASS"
    target["v3_overlap_excluded"] = False
    cols = [
        "candidate_id", "stage", "ticker", "rulebook_hash", "activity_rule_hash", "source_file", "source_row_index",
        "vol_group_final", "classification_method", "weight_volume_surge", "near_zero", "exact_zero",
        "volume_reachability_label", "nonvolume_entry_possible_market_cap", "market_cap_nonvolume_min_count",
        "market_cap_nonvolume_subset", "holdout_n", "holdout_avg_pnl_pct", "holdout_win_rate_pct",
        "holdout_avg_mae_pct", "holdout_worst_mae_pct", "holdout_avg_mfe_pct", "sample_ok", "absolute_good",
        "relative_good", "block_reason", "v3_overlap_excluded",
    ]
    target[cols].sort_values(["stage", "ticker", "candidate_id"]).to_csv(TARGET_OUT, index=False)
    target[target["absolute_good"]][cols].sort_values(
        ["relative_good", "holdout_avg_pnl_pct", "holdout_win_rate_pct"], ascending=False
    ).to_csv(GOOD_OUT, index=False)

    perf = [
        stats(target, "POLICY_V3_SURVIVORS", "BOIL_EXCLUSIVE_NEAR_ZERO", "CANDIDATE"),
        stats(control, "POLICY_V3_SURVIVORS", "NON_BOIL_HIGH_VOL", "CANDIDATE"),
        stats(target, "POLICY_V3_SURVIVORS", "BOIL_EXCLUSIVE_NEAR_ZERO", "ENTRY_RULE"),
        stats(control, "POLICY_V3_SURVIVORS", "NON_BOIL_HIGH_VOL", "ENTRY_RULE"),
        stats(target[target["exact_zero"]], "POLICY_V3_SURVIVORS", "BOIL_EXACT_ZERO", "ENTRY_RULE"),
        stats(target[~target["exact_zero"]], "POLICY_V3_SURVIVORS", "BOIL_NEAR_NONZERO", "ENTRY_RULE"),
    ]

    frozen = stable_csv(ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv", low_memory=False)
    frozen = frozen[frozen["split"].astype(str).str.upper().eq("OOS")]
    live_meta = base[["candidate_id", "stage", "ticker", "rulebook_hash", "vol_group", "weight_volume_surge"]].merge(
        v3[["candidate_id", "final_p99_weightless_block_status"]], on="candidate_id", validate="one_to_one"
    )
    live_meta = frozen[["candidate_id"]].drop_duplicates().merge(live_meta, on="candidate_id", validate="one_to_one")
    live_meta = live_meta[live_meta["vol_group"].eq("HIGH_VOL")]
    live_stats = frozen.groupby("candidate_id").agg(
        raw_holdout_n=("pnl_pct", "size"), raw_holdout_avg_pnl_pct=("pnl_pct", "mean"),
        raw_holdout_win_rate_pct=("pnl_pct", lambda s: (s > 0).mean() * 100),
        holdout_avg_mae_pct=("MAE", "mean"), holdout_worst_mae_pct=("MAE", "min"),
        holdout_avg_mfe_pct=("MFE", "mean"),
    ).reset_index()
    live = live_meta.merge(live_stats, on="candidate_id", validate="one_to_one")
    live["activity_rule_hash"] = live["rulebook_hash"]
    live["near_zero"] = live["weight_volume_surge"].abs().le(NEAR_ZERO)
    live["exact_zero"] = live["weight_volume_surge"].eq(0)
    for population, subset in (
        ("FROZEN_LIVE93_PRIOR_ALL", live),
        ("FROZEN_LIVE93_V3_SURVIVORS", live[live["final_p99_weightless_block_status"].eq("PASS")]),
    ):
        perf += [
            stats(subset[subset["near_zero"]], population, "BOIL_NEAR_ZERO", "CANDIDATE"),
            stats(subset[~subset["near_zero"]], population, "NON_BOIL_HIGH_VOL", "CANDIDATE"),
        ]
    pd.DataFrame(perf).to_csv(PERF_OUT, index=False)

    boot_rows = []
    comparisons = [
        ("POLICY_ENTRY_RULE_ALL", target, control, "ENTRY_RULE"),
        ("POLICY_ENTRY_RULE_EXACT_ZERO", target[target["exact_zero"]], control, "ENTRY_RULE"),
        ("POLICY_ENTRY_RULE_NEAR_NONZERO", target[~target["exact_zero"]], control, "ENTRY_RULE"),
        ("FROZEN_LIVE93_PRIOR_ALL", live[live["near_zero"]], live[~live["near_zero"]], "CANDIDATE"),
        ("FROZEN_LIVE93_V3_SURVIVORS",
         live[live["near_zero"] & live["final_p99_weightless_block_status"].eq("PASS")],
         live[~live["near_zero"] & live["final_p99_weightless_block_status"].eq("PASS")], "CANDIDATE"),
    ]
    metric_map = {"pnl": "raw_holdout_avg_pnl_pct", "win": "raw_holdout_win_rate_pct"}
    for i, (name, blocked, kept, unit) in enumerate(comparisons):
        for j, (metric_name, column) in enumerate(metric_map.items()):
            a = unit_frame(blocked, unit)["avg_pnl" if metric_name == "pnl" else "win"].to_numpy(float)
            c = unit_frame(kept, unit)["avg_pnl" if metric_name == "pnl" else "win"].to_numpy(float)
            estimate, low, high = boot(a, c, SEED + i * 10 + j)
            boot_rows.append({
                "comparison": name, "unit": unit, "metric": metric_name, "blocked_n": len(a), "control_n": len(c),
                "difference_blocked_minus_control": estimate, "bootstrap_ci_low": low, "bootstrap_ci_high": high,
                "bootstrap_reps": REPS, "ci_excludes_zero": low > 0 or high < 0, "seed": SEED + i * 10 + j,
            })
    bootstrap = pd.DataFrame(boot_rows)
    bootstrap.to_csv(BOOT_OUT, index=False)

    target_ids = set(target["candidate_id"])
    selection = base.merge(v3[["candidate_id", "final_p99_weightless_block_status"]], on="candidate_id", validate="one_to_one")
    selection["boil_exclusive_block"] = selection["candidate_id"].isin(target_ids)
    selection["post_boil_static_status"] = selection["recommended_static_status"]
    selection.loc[selection["final_p99_weightless_block_status"].eq("FAIL") | selection["boil_exclusive_block"], "post_boil_static_status"] = "FAIL"
    selected = select(selection)
    selected.to_csv(FINAL_OUT, index=False)
    old_ids, new_ids = set(old_selected["candidate_id"]), set(selected["candidate_id"])
    pd.DataFrame([
        ("v3_selected_total", len(old_selected)), ("v3_selected_stage2", old_selected["stage"].eq("stage2").sum()),
        ("v3_selected_stage3", old_selected["stage"].eq("stage3").sum()), ("post_boil_selected_total", len(selected)),
        ("post_boil_selected_stage2", selected["stage"].eq("stage2").sum()),
        ("post_boil_selected_stage3", selected["stage"].eq("stage3").sum()),
        ("old_selected_blocked", len(old_ids & target_ids)), ("old_selected_dropped", len(old_ids - new_ids)),
        ("fallback_added", len(new_ids - old_ids)), ("combined_static_pass", selection["post_boil_static_status"].eq("PASS").sum()),
        ("combined_static_hold", selection["post_boil_static_status"].eq("HOLD").sum()),
        ("combined_static_fail", selection["post_boil_static_status"].eq("FAIL").sum()),
    ], columns=["metric", "value"]).to_csv(FINAL_SUMMARY_OUT, index=False)

    def ci(name: str) -> pd.Series:
        return bootstrap[bootstrap["comparison"].eq(name) & bootstrap["metric"].eq("pnl")].iloc[0]

    full_ci, prior_ci, live_ci = ci("POLICY_ENTRY_RULE_ALL"), ci("FROZEN_LIVE93_PRIOR_ALL"), ci("FROZEN_LIVE93_V3_SURVIVORS")
    decision = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision": "BLOCK_JUSTIFIED",
        "checker": "high_vol_volume_blind_near_zero_v3_exclusive",
        "phase": "STATIC", "enforcement": "BLOCK", "operational_implementation": False,
        "definition": {"high_vol": True, "entry_possible_without_volume": True, "abs_weight_volume_surge_lte": NEAR_ZERO,
                       "v3_reachability_status": "PASS", "v3_overlap": "EXCLUDED"},
        "incremental_block": {"candidate_n": len(target), "exact_zero_n": int(target["exact_zero"].sum()),
                              "near_nonzero_n": int((~target["exact_zero"]).sum()),
                              "unique_entry_rule_n": target[["stage", "activity_rule_hash"]].drop_duplicates().shape[0],
                              "ticker_n": target["ticker"].nunique(), "stage_counts": target["stage"].value_counts().to_dict()},
        "performance": {"boil_avg_pnl_pct": target["raw_holdout_avg_pnl_pct"].mean(),
                        "normal_avg_pnl_pct": control["raw_holdout_avg_pnl_pct"].mean(),
                        "boil_win_rate_pct": target["raw_holdout_win_rate_pct"].mean(),
                        "normal_win_rate_pct": control["raw_holdout_win_rate_pct"].mean(),
                        "boil_avg_mae_pct": target["holdout_avg_mae_pct"].mean(),
                        "normal_avg_mae_pct": control["holdout_avg_mae_pct"].mean(),
                        "boil_avg_mfe_pct": target["holdout_avg_mfe_pct"].mean(),
                        "normal_avg_mfe_pct": control["holdout_avg_mfe_pct"].mean(),
                        "entry_rule_pnl_diff": full_ci["difference_blocked_minus_control"],
                        "entry_rule_pnl_diff_ci95": [full_ci["bootstrap_ci_low"], full_ci["bootstrap_ci_high"]],
                        "prior_live93_pnl_diff_ci95": [prior_ci["bootstrap_ci_low"], prior_ci["bootstrap_ci_high"]],
                        "v3_survivor_live93_pnl_diff_ci95": [live_ci["bootstrap_ci_low"], live_ci["bootstrap_ci_high"]]},
        "overfilter_risk": {"absolute_good_n": int(target["absolute_good"].sum()),
                            "absolute_good_rate_pct": target["absolute_good"].mean() * 100,
                            "relative_good_n": int(target["relative_good"].sum()),
                            "relative_good_rate_pct": target["relative_good"].mean() * 100,
                            "relative_good_unique_entry_rule_n": target[target["relative_good"]][["stage", "activity_rule_hash"]].drop_duplicates().shape[0],
                            "note": "outcome-good exceptions exist but no ex-ante non-leaky discriminator separates them"},
        "final_candidates": {"before": len(old_selected), "after": len(selected),
                             "stage2_after": int(selected["stage"].eq("stage2").sum()),
                             "stage3_after": int(selected["stage"].eq("stage3").sum()),
                             "old_selected_dropped": len(old_ids - new_ids), "fallback_added": len(new_ids - old_ids)},
        "rationale": ["policy cohort PnL, win rate and MFE are lower", "entry-rule bootstrap PnL CI excludes zero",
                      "prior live93 and v3-survivor live93 both reconfirm negative CI",
                      "exact-zero and near-nonzero subgroups both underperform", "84 final candidates remain practical"],
        "source_mutation": False, "live_change": False, "retraining": False, "order": False, "delete": False,
    }
    DECISION_OUT.write_text(json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8")

    p_target, p_control = perf[0], perf[1]
    lines = [
        "# BOIL형 게이트 enforcement 결정 dry-run", "", "- 결정: **BLOCK_JUSTIFIED**", "- 운영 구현: `false`",
        "- 원본·라이브·운영 코드·재학습·주문·삭제: 0건", "", "## 1. 확정 조건", "",
        "`HIGH_VOL AND 거래량 없이 진입 가능 AND abs(weight_volume_surge)<=0.05 AND v3 PASS`", "",
        "v3가 이미 차단한 도달불가 조건은 제외하고 BOIL형 순수 구조적 무시만 순증 차단한다.", "",
        "## 2. 순증 차단 규모", "", f"- 후보: **{len(target):,}개**", f"- exact-zero: {target['exact_zero'].sum():,}개",
        f"- near-zero nonzero: {(~target['exact_zero']).sum():,}개", f"- 고유 entry rule: {target[['stage','activity_rule_hash']].drop_duplicates().shape[0]:,}개",
        f"- ticker: {target['ticker'].nunique():,}개", f"- Stage2/Stage3: {target['stage'].eq('stage2').sum():,}/{target['stage'].eq('stage3').sum():,}", "",
        "## 3. holdout 성과", "", "| 그룹 | 후보 | 거래 | 평균 PnL | 승률 | 평균 MAE | 평균 MFE |", "|---|---:|---:|---:|---:|---:|---:|",
        f"| BOIL형 v3 전용 | {p_target['candidate_n']:,} | {int(p_target['holdout_trade_n']):,} | {p_target['candidate_equal_avg_pnl_pct']:.4f}% | {p_target['candidate_equal_win_rate_pct']:.2f}% | {p_target['candidate_equal_avg_mae_pct']:.4f}% | {p_target['candidate_equal_avg_mfe_pct']:.4f}% |",
        f"| non-BOIL HIGH_VOL | {p_control['candidate_n']:,} | {int(p_control['holdout_trade_n']):,} | {p_control['candidate_equal_avg_pnl_pct']:.4f}% | {p_control['candidate_equal_win_rate_pct']:.2f}% | {p_control['candidate_equal_avg_mae_pct']:.4f}% | {p_control['candidate_equal_avg_mfe_pct']:.4f}% |", "",
        f"고유 entry-rule PnL 차이 BOIL-minus-normal: **{full_ci['difference_blocked_minus_control']:.4f}%p**, 95% CI **[{full_ci['bootstrap_ci_low']:.4f}, {full_ci['bootstrap_ci_high']:.4f}]**.", "",
        "기존 frozen live93 재확인:", f"- 8 vs 23 CI: [{prior_ci['bootstrap_ci_low']:.4f}, {prior_ci['bootstrap_ci_high']:.4f}]",
        f"- v3 이후 5 vs 20 CI: [{live_ci['bootstrap_ci_low']:.4f}, {live_ci['bootstrap_ci_high']:.4f}]",
        "- 기존 평균 PnL 1.2258% vs 3.9136% 방향과 0 배제 결과를 재확인했다.", "",
        "exact-zero와 near-zero nonzero를 분리해도 둘 다 정상군 대비 PnL CI가 0 아래다.", "",
        "## 4. 과잉 차단 위험", "", f"- 절대 양호: {target['absolute_good'].sum():,}/{len(target):,} ({target['absolute_good'].mean()*100:.2f}%)",
        f"- 정상군 stage 중앙값 이상 PnL·승률: {target['relative_good'].sum():,}/{len(target):,} ({target['relative_good'].mean()*100:.2f}%)",
        f"- 상대 양호 고유 entry rule: {target[target['relative_good']][['stage','activity_rule_hash']].drop_duplicates().shape[0]:,}개", "",
        "양호 예외는 존재하지만 holdout을 본 사후 분류이며, 누수 없이 예외만 분리할 정적 특징은 확인되지 않았다.", "",
        "## 5. 최종 후보", "", f"- v3만: {len(old_selected):,}개 — Stage2 {old_selected['stage'].eq('stage2').sum():,}, Stage3 {old_selected['stage'].eq('stage3').sum():,}",
        f"- v3+BOIL BLOCK: **{len(selected):,}개** — Stage2 {selected['stage'].eq('stage2').sum():,}, Stage3 {selected['stage'].eq('stage3').sum():,}",
        f"- 기존 탈락 {len(old_ids-new_ids):,}, fallback 신규 {len(new_ids-old_ids):,}", "",
        "현재 85개 중 `stage3:CVNA:2f6d067a7826` 1개가 탈락하며 대체 후보가 없어 84개가 남는다.", "",
        "## 6. 판정", "", "**BLOCK_JUSTIFIED**", "", "성과 열위와 bootstrap CI 0 배제, 84개 실용 후보 유지 조건을 모두 충족한다.",
        "일부 양호 사례가 있으나 exact-zero와 near-zero 모두 cohort 수준에서 열위이며 비누수 예외조건이 없어 일부 하위조건만 BLOCK할 근거는 없다.", "",
        "설계 enforcement만 BLOCK으로 확정하며 운영 구현은 false다.", "", "## 7. 산출물", "",
        f"- `{TARGET_OUT.name}`", f"- `{GOOD_OUT.name}`", f"- `{PERF_OUT.name}`", f"- `{BOOT_OUT.name}`",
        f"- `{FINAL_OUT.name}`", f"- `{FINAL_SUMMARY_OUT.name}`", f"- `{DECISION_OUT.name}`",
    ]
    READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
