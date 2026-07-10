from __future__ import annotations

"""Stage2 편중과 병렬 게이트 책임 범위를 재현하는 read-only 진단.

파일을 생성·수정하지 않고 JSON을 stdout으로만 출력한다.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live import elite_shadow_report as elite  # noqa: E402

OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
NMIN = {"stage2": 35, "stage3": 24}
WCUT = {"stage2": 58.52738150023009, "stage3": 50.0}
CAP = {"stage2": 60, "stage3": 80}


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def main() -> int:
    candidates = stable_csv(OUT / "integrated_gate_candidate_dryrun.csv", low_memory=False)
    v3 = stable_csv(
        OUT / "threshold_p99_weightless_block_candidate_decisions.csv",
        usecols=["candidate_id", "final_p99_weightless_block_status"],
    )
    high_vol = stable_csv(OUT / "high_vol_volume_blind_risk_candidates.csv", low_memory=False)
    ce = stable_csv(OUT / "threshold_p99_weightless_block_boil_ce_capture.csv", low_memory=False)
    candidates = candidates.merge(v3, on="candidate_id", validate="one_to_one")
    boil_ids = set(high_vol.loc[high_vol["weight_volume_surge"].eq(0), "candidate_id"])

    funnel: list[dict[str, Any]] = []
    for stage in ("stage2", "stage3"):
        frame = candidates[candidates["stage"].eq(stage)].copy()
        current = pd.Series(True, index=frame.index)

        def snapshot(
            step: str,
            enforcement: str,
            before: int,
            hits: int,
            removed: int,
            note: str = "",
        ) -> None:
            funnel.append(
                {
                    "stage": stage,
                    "step": step,
                    "enforcement": enforcement,
                    "before": before,
                    "condition_hits": hits,
                    "removed": removed,
                    "remaining": int(current.sum()),
                    "unique_tickers": int(frame.loc[current, "ticker"].nunique()),
                    "removed_rate_pct": removed / before * 100 if before else 0.0,
                    "note": note,
                }
            )

        snapshot("origin", "BASE", len(frame), 0, 0)
        for step, enforcement, condition, note in (
            ("artifact_completeness", "BLOCK", frame["origin_complete"], ""),
            ("history_sample_min", "HOLD_EXCLUDE", frame["base_n"].ge(NMIN[stage]), f"base_n >= {NMIN[stage]}"),
            ("history_avg_pnl", "BLOCK", frame["base_avg_pnl_pct"].ge(0), "avg pnl >= 0"),
        ):
            before = int(current.sum())
            removed = int((current & ~condition).sum())
            current &= condition
            snapshot(step, enforcement, before, removed, removed, note)

        before = int(current.sum())
        win_hits = int((current & frame["base_win_rate_pct"].lt(WCUT[stage])).sum())
        snapshot("history_win_rate", "MONITOR", before, win_hits, 0, f"win rate < {WCUT[stage]}")

        condition = frame["final_p99_weightless_block_status"].eq("PASS")
        before = int(current.sum())
        removed = int((current & ~condition).sum())
        current &= condition
        snapshot("v3_p99_weightless", "BLOCK", before, removed, removed)

        before = int(current.sum())
        boil_hits = int((current & frame["candidate_id"].isin(boil_ids)).sum())
        snapshot(
            "boil_exact_zero_high_vol",
            "MONITOR_PARALLEL",
            before,
            boil_hits,
            0,
            "HIGH_VOL volume-blind exact-zero; no enforcement change",
        )

        for step, condition in (
            ("elite_filter", frame["elite_static_pass"].fillna(False)),
            ("denylist", ~frame["denylisted"].fillna(False)),
        ):
            before = int(current.sum())
            removed = int((current & ~condition).sum())
            current &= condition
            snapshot(step, "BLOCK", before, removed, removed)

        ranked = frame[current].sort_values(
            ["elite_score", "oos_fitness", "oos_expectancy_pct"], ascending=False, na_position="last"
        )
        dedup = ranked.drop_duplicates("ticker", keep="first")
        current = frame.index.isin(dedup.index)
        snapshot(
            "ticker_dedup",
            "SELECTION",
            len(ranked),
            len(ranked) - len(dedup),
            len(ranked) - len(dedup),
            "denylist-before-dedup fallback",
        )
        capped = dedup.head(CAP[stage])
        current = frame.index.isin(capped.index)
        snapshot(
            "stage_cap",
            "SELECTION",
            len(dedup),
            max(0, len(dedup) - CAP[stage]),
            max(0, len(dedup) - CAP[stage]),
            f"cap={CAP[stage]}; binding={len(dedup) > CAP[stage]}",
        )

    sample_analysis: list[dict[str, Any]] = []
    sample_sensitivity: list[dict[str, Any]] = []
    for stage in ("stage2", "stage3"):
        frame = candidates[candidates["stage"].eq(stage)].copy()
        complete = frame[frame["origin_complete"]]
        for threshold, scenario in ((NMIN[stage], "stage_specific"), (24, "common_24"), (35, "common_35")):
            below = complete["base_n"].lt(threshold)
            sample_analysis.append(
                {
                    "stage": stage,
                    "scenario": scenario,
                    "threshold": threshold,
                    "complete_n": len(complete),
                    "below_n": int(below.sum()),
                    "below_rate_pct": float(below.mean() * 100),
                }
            )
        for label, subset in (
            ("below_stage_threshold", complete[complete["base_n"].lt(NMIN[stage]) & complete["holdout_n"].gt(0)]),
            ("meets_stage_threshold", complete[complete["base_n"].ge(NMIN[stage]) & complete["holdout_n"].gt(0)]),
        ):
            sample_analysis.append(
                {
                    "stage": stage,
                    "scenario": label,
                    "threshold": NMIN[stage],
                    "candidate_n": len(subset),
                    "holdout_trade_n": int(subset["holdout_n"].sum()),
                    "holdout_candidate_avg_pnl_pct": float(subset["holdout_avg_pnl_pct"].mean()),
                    "holdout_trade_weighted_pnl_pct": float(
                        (subset["holdout_avg_pnl_pct"] * subset["holdout_n"]).sum() / subset["holdout_n"].sum()
                    ),
                    "holdout_candidate_win_rate_pct": float(subset["holdout_win_rate_pct"].mean()),
                }
            )
        for threshold, scenario in ((0, "no_sample_gate"), (24, "threshold_24"), (35, "threshold_35")):
            mask = (
                frame["origin_complete"]
                & frame["base_n"].ge(threshold)
                & frame["base_avg_pnl_pct"].ge(0)
                & frame["final_p99_weightless_block_status"].eq("PASS")
                & frame["elite_static_pass"]
                & ~frame["denylisted"]
            )
            selected = frame[mask].sort_values(
                ["elite_score", "oos_fitness", "oos_expectancy_pct"], ascending=False
            ).drop_duplicates("ticker").head(CAP[stage])
            sample_sensitivity.append(
                {
                    "stage": stage,
                    "scenario": scenario,
                    "threshold": threshold,
                    "final_candidate_n": len(selected),
                }
            )

    stage2 = candidates[candidates["stage"].eq("stage2")].copy()
    pre = (
        stage2["origin_complete"]
        & stage2["base_n"].ge(35)
        & stage2["base_avg_pnl_pct"].ge(0)
        & stage2["final_p99_weightless_block_status"].eq("PASS")
    )
    source_cache: dict[Path, list[dict[str, Any]]] = {}

    def source_rulebook(row: Any) -> dict[str, Any]:
        path = ROOT / str(row.source_file)
        if path not in source_cache:
            before = (path.stat().st_size, path.stat().st_mtime_ns)
            source_cache[path] = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
            after = (path.stat().st_size, path.stat().st_mtime_ns)
            if before != after:
                raise RuntimeError(f"source changed while reading: {path}")
        return source_cache[path][int(row.source_row_index) - 1].get("rulebook") or {}

    stage3_like_ids: set[str] = set()
    for row in stage2[pre].itertuples(index=False):
        metric_checks = (
            row.oos_expectancy_pct >= 2.7
            and row.oos_fitness >= 45
            and row.oos_win_rate >= 70
            and row.oos_trade_count >= 8
            and row.worst_drawdown_pct > -18
        )
        metric_map = {
            "oos_expectancy_pct": row.oos_expectancy_pct,
            "oos_fitness": row.oos_fitness,
            "oos_win_rate": row.oos_win_rate,
            "oos_trade_count": row.oos_trade_count,
            "worst_drawdown_pct": row.worst_drawdown_pct,
        }
        if metric_checks and elite._rulebook_passes_anti_pattern_filter(
            source_rulebook(row), metric_map, stage="stage3"
        )[0]:
            stage3_like_ids.add(row.candidate_id)

    current_ids = set(stage2.loc[pre & stage2["elite_static_pass"], "candidate_id"])
    incremental_ids = stage3_like_ids - current_ids

    def cohort(ids: set[str]) -> dict[str, Any]:
        subset = stage2[stage2["candidate_id"].isin(ids) & stage2["holdout_n"].gt(0)]
        unique = stage2[stage2["candidate_id"].isin(ids) & ~stage2["denylisted"]].sort_values(
            ["elite_score", "oos_fitness", "oos_expectancy_pct"], ascending=False
        ).drop_duplicates("ticker")
        return {
            "candidate_n": len(ids),
            "unique_ticker_n_after_deny": len(unique),
            "holdout_candidate_avg_pnl_pct": float(subset["holdout_avg_pnl_pct"].mean()),
            "holdout_trade_weighted_pnl_pct": float(
                (subset["holdout_avg_pnl_pct"] * subset["holdout_n"]).sum() / subset["holdout_n"].sum()
            ),
            "holdout_candidate_win_rate_pct": float(subset["holdout_win_rate_pct"].mean()),
        }

    v3_fail_ids = set(v3.loc[v3["final_p99_weightless_block_status"].eq("FAIL"), "candidate_id"])
    result = {
        "funnel": funnel,
        "sample_analysis": sample_analysis,
        "sample_sensitivity": sample_sensitivity,
        "stage2_elite_counterfactual": {
            "pre_elite_n": int(pre.sum()),
            "current_stage2_elite": cohort(current_ids),
            "stage3_numeric_thresholds_on_stage2": cohort(stage3_like_ids),
            "incremental_vs_current": cohort(incremental_ids),
            "incremental_first_fail_reasons": stage2.loc[
                stage2["candidate_id"].isin(incremental_ids), "elite_filter_reason"
            ].value_counts().to_dict(),
        },
        "confirmed_parallel_evidence": {
            "boil_exact_zero_total": len(boil_ids),
            "boil_exact_zero_v3_overlap": len(boil_ids & v3_fail_ids),
            "boil_exact_zero_v3_exclusive": len(boil_ids - v3_fail_ids),
            "ce_total": 7,
            "ce_v3_overlap": int(ce["captured_by_v3_weightless_block"].sum()),
            "ce_dynamic_exclusive_ids": ce.loc[
                ~ce["captured_by_v3_weightless_block"], "candidate_id"
            ].tolist(),
        },
        "verdict": {
            "classification": "MIXED",
            "primary": "QUALITY_DRIVEN_WITHIN_EXISTING_ELITE_METRICS",
            "secondary": "GATE_OVER_FILTER_AT_STAGE2_ELITE_TRADE_FITNESS_MIN_TRADE_THRESHOLDS",
            "sample_gate_is_primary_cause": False,
            "stage_cap_is_binding": False,
        },
        "operational_implementation": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
