from __future__ import annotations

import itertools
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
AUDIT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
for path in (ROOT, AUDIT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import integrated_gate_sim_core as core

FULL = AUDIT / "sparse_indicator_entry_structure_full.csv"
N1 = AUDIT / "sparse_indicator_entry_n1_market_cap.csv"
N2 = AUDIT / "sparse_indicator_entry_n2_market_cap.csv"
N1_NEUTRAL = AUDIT / "sparse_indicator_entry_n1_neutral.csv"
N2_NEUTRAL = AUDIT / "sparse_indicator_entry_n2_neutral.csv"
BOIL = AUDIT / "sparse_indicator_entry_boil_parity.csv"
CE = AUDIT / "sparse_indicator_entry_ce_parity.csv"
PERF = AUDIT / "sparse_indicator_entry_performance_summary.csv"
SCEN = AUDIT / "sparse_indicator_entry_scenario_summary.csv"
SUMMARY = AUDIT / "sparse_indicator_entry_summary.json"

COMPONENTS = (
    ("ma", "weight_ma_align"),
    ("macd", "weight_macd_golden"),
    ("rsi", "weight_rsi_zone"),
    ("bb", "weight_bb_near_lower"),
    ("volume", "weight_volume_surge"),
)


def num(value: Any) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else 0.0
    except Exception:
        return 0.0


def flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def subset_result(weights: dict[str, float], threshold: float, multiplier: float) -> dict[str, Any]:
    passing: list[tuple[int, float, tuple[str, ...]]] = []
    names = tuple(weights)
    for size in range(1, len(names) + 1):
        for subset in itertools.combinations(names, size):
            raw = sum(max(0.0, weights[name]) for name in subset)
            final = raw * multiplier
            if final + 1e-12 >= threshold:
                passing.append((size, raw, subset))
        if passing:
            break
    positive = sorted(
        ((name, max(0.0, value)) for name, value in weights.items() if value > 0.0),
        key=lambda item: (-item[1], item[0]),
    )
    greedy_count = None
    greedy_sum = 0.0
    greedy_names: list[str] = []
    for name, value in positive:
        greedy_names.append(name)
        greedy_sum += value
        if greedy_sum * multiplier + 1e-12 >= threshold:
            greedy_count = len(greedy_names)
            break
    if not passing:
        return {
            "min_count": None,
            "subset": "",
            "raw_score": sum(max(0.0, value) for value in weights.values()),
            "final_score": sum(max(0.0, value) for value in weights.values()) * multiplier,
            "margin": sum(max(0.0, value) for value in weights.values()) * multiplier - threshold,
            "greedy_count": greedy_count,
            "greedy_subset": "+".join(greedy_names),
            "enumeration_greedy_match": greedy_count is None,
            "status": "IMPOSSIBLE_CORE_ONLY",
        }
    size = min(item[0] for item in passing)
    best = sorted(
        (item for item in passing if item[0] == size),
        key=lambda item: (-item[1], "+".join(item[2])),
    )[0]
    raw = best[1]
    return {
        "min_count": size,
        "subset": "+".join(best[2]),
        "raw_score": raw,
        "final_score": raw * multiplier,
        "margin": raw * multiplier - threshold,
        "greedy_count": greedy_count,
        "greedy_subset": "+".join(greedy_names[:greedy_count] if greedy_count else greedy_names),
        "enumeration_greedy_match": greedy_count == size,
        "status": "PASSABLE_CORE_ONLY",
    }


def group_row(frame: pd.DataFrame, scenario: str, bucket: str, mask: pd.Series, scope: str) -> dict[str, Any]:
    selected = frame[mask].copy()
    holdout = selected[selected["holdout_n"].fillna(0).gt(0)]
    return {
        "scope": scope,
        "scenario": scenario,
        "bucket": bucket,
        "candidate_count": len(selected),
        "complete_count": int(selected["origin_complete_bool"].sum()),
        "base_avg_pnl_candidate_mean_pct": selected["base_avg_pnl_pct"].mean(),
        "base_win_rate_candidate_mean_pct": selected["base_win_rate_pct"].mean(),
        "holdout_candidate_count": len(holdout),
        "holdout_trade_count": int(holdout["holdout_n"].sum()) if len(holdout) else 0,
        "holdout_avg_pnl_candidate_mean_pct": holdout["holdout_avg_pnl_pct"].mean(),
        "holdout_win_rate_candidate_mean_pct": holdout["holdout_win_rate_pct"].mean(),
        "holdout_avg_pnl_trade_weighted_pct": (
            (holdout["holdout_avg_pnl_pct"] * holdout["holdout_n"]).sum() / holdout["holdout_n"].sum()
            if len(holdout) and holdout["holdout_n"].sum() else math.nan
        ),
    }


def main() -> int:
    origins, _ = core.load_origins()
    performance = pd.read_csv(AUDIT / "integrated_gate_candidate_dryrun.csv", low_memory=False)
    performance = performance.set_index("candidate_id", drop=False)
    boil_ids = set(performance[performance["check_boil"].astype(str).eq("FAIL")]["candidate_id"])
    ce_ids = set(pd.read_csv(AUDIT / "ce_origin_fail_rejudged.csv")["candidate_id"].astype(str))

    rows: list[dict[str, Any]] = []
    for origin in origins:
        candidate_id = origin["candidate_id"]
        rb = origin["rulebook"]
        weights = {name: num(rb.get(field)) for name, field in COMPONENTS}
        nonvolume = {name: value for name, value in weights.items() if name != "volume"}
        threshold = num(rb.get("signal_threshold"))
        strength = min(1.0, max(0.0, num(rb.get("market_adjustment_strength"))))
        use_market = flag(rb.get("use_market_entry_adjustment"))
        max_multiplier = 1.0 + strength if use_market else 1.0
        neutral = subset_result(weights, threshold, 1.0)
        market = subset_result(weights, threshold, max_multiplier)
        neutral_nv = subset_result(nonvolume, threshold, 1.0)
        market_nv = subset_result(nonvolume, threshold, max_multiplier)
        perf = performance.loc[candidate_id]
        rows.append({
            "candidate_id": candidate_id,
            "stage": origin["stage"],
            "ticker": origin["ticker"],
            "rulebook_hash": origin["rulebook_hash"],
            "source_file": origin["source_file"],
            "source_row_index": origin["source_row_index"],
            "signal_threshold": threshold,
            **{field: weights[name] for name, field in COMPONENTS},
            "core_weight_sum": sum(weights.values()),
            "positive_core_weight_count": sum(value > 0 for value in weights.values()),
            "use_market_entry_adjustment": use_market,
            "market_adjustment_strength": strength,
            "market_multiplier_neutral": 1.0,
            "market_multiplier_cap": max_multiplier,
            "noncore_additive_assumption": "news=0|topics=0|events=0|crash_bonus=0",
            "neutral_min_indicator_count": neutral["min_count"],
            "neutral_min_subset": neutral["subset"],
            "neutral_subset_raw_score": neutral["raw_score"],
            "neutral_subset_final_score": neutral["final_score"],
            "neutral_threshold_margin": neutral["margin"],
            "neutral_status": neutral["status"],
            "neutral_n1": neutral["min_count"] == 1,
            "neutral_n2_or_less": neutral["min_count"] is not None and neutral["min_count"] <= 2,
            "market_cap_min_indicator_count": market["min_count"],
            "market_cap_min_subset": market["subset"],
            "market_cap_subset_raw_score": market["raw_score"],
            "market_cap_subset_final_score": market["final_score"],
            "market_cap_threshold_margin": market["margin"],
            "market_cap_status": market["status"],
            "market_cap_n1": market["min_count"] == 1,
            "market_cap_n2_or_less": market["min_count"] is not None and market["min_count"] <= 2,
            "neutral_nonvolume_min_count": neutral_nv["min_count"],
            "neutral_nonvolume_subset": neutral_nv["subset"],
            "market_cap_nonvolume_min_count": market_nv["min_count"],
            "market_cap_nonvolume_subset": market_nv["subset"],
            "enumeration_greedy_match_neutral": neutral["enumeration_greedy_match"],
            "enumeration_greedy_match_market_cap": market["enumeration_greedy_match"],
            "boil_existing": candidate_id in boil_ids,
            "boil_captured_n1_market_cap": candidate_id in boil_ids and market_nv["min_count"] == 1,
            "boil_captured_n2_market_cap": candidate_id in boil_ids and market_nv["min_count"] is not None and market_nv["min_count"] <= 2,
            "ce_existing_fail": candidate_id in ce_ids,
            "ce_captured_n1_market_cap": candidate_id in ce_ids and market["min_count"] == 1,
            "ce_captured_n2_market_cap": candidate_id in ce_ids and market["min_count"] is not None and market["min_count"] <= 2,
            "origin_complete_bool": flag(perf.get("origin_complete")),
            "base_n": perf.get("base_n"),
            "base_avg_pnl_pct": perf.get("base_avg_pnl_pct"),
            "base_win_rate_pct": perf.get("base_win_rate_pct"),
            "holdout_n": perf.get("holdout_n"),
            "holdout_avg_pnl_pct": perf.get("holdout_avg_pnl_pct"),
            "holdout_win_rate_pct": perf.get("holdout_win_rate_pct"),
        })

    frame = pd.DataFrame(rows).sort_values(["stage", "ticker", "candidate_id"])
    assert len(frame) == 17_071 and frame["candidate_id"].nunique() == 17_071
    assert frame["enumeration_greedy_match_neutral"].all()
    assert frame["enumeration_greedy_match_market_cap"].all()
    frame.to_csv(FULL, index=False)
    frame[frame["market_cap_n1"]].to_csv(N1, index=False)
    frame[frame["market_cap_n2_or_less"]].to_csv(N2, index=False)
    frame[frame["neutral_n1"]].to_csv(N1_NEUTRAL, index=False)
    frame[frame["neutral_n2_or_less"]].to_csv(N2_NEUTRAL, index=False)
    frame[frame["boil_existing"]].to_csv(BOIL, index=False)
    frame[frame["ce_existing_fail"]].to_csv(CE, index=False)

    scenarios: list[dict[str, Any]] = []
    for stage in ("ALL", "stage2", "stage3"):
        scoped = frame if stage == "ALL" else frame[frame["stage"].eq(stage)]
        for scenario, prefix in (("neutral", "neutral"), ("market_cap", "market_cap")):
            counts = scoped[f"{prefix}_min_indicator_count"].value_counts(dropna=False).to_dict()
            for value in (1, 2, 3, 4, 5):
                scenarios.append({
                    "scope": stage,
                    "scenario": scenario,
                    "bucket": f"MIN_COUNT_{value}",
                    "count": int(counts.get(float(value), counts.get(value, 0))),
                    "total": len(scoped),
                })
            scenarios.extend([
                {"scope": stage, "scenario": scenario, "bucket": "N1", "count": int(scoped[f"{prefix}_n1"].sum()), "total": len(scoped)},
                {"scope": stage, "scenario": scenario, "bucket": "N2_OR_LESS", "count": int(scoped[f"{prefix}_n2_or_less"].sum()), "total": len(scoped)},
                {"scope": stage, "scenario": scenario, "bucket": "IMPOSSIBLE_CORE_ONLY", "count": int(scoped[f"{prefix}_status"].eq("IMPOSSIBLE_CORE_ONLY").sum()), "total": len(scoped)},
            ])
    pd.DataFrame(scenarios).to_csv(SCEN, index=False)

    perf_rows: list[dict[str, Any]] = []
    for scope in ("ALL", "COMPLETE_ONLY"):
        base = frame if scope == "ALL" else frame[frame["origin_complete_bool"]]
        for scenario, prefix in (("neutral", "neutral"), ("market_cap", "market_cap")):
            perf_rows.append(group_row(base, scenario, "N1", base[f"{prefix}_n1"], scope))
            perf_rows.append(group_row(base, scenario, "N2_OR_LESS", base[f"{prefix}_n2_or_less"], scope))
            perf_rows.append(group_row(base, scenario, "MORE_THAN_2_OR_IMPOSSIBLE", ~base[f"{prefix}_n2_or_less"], scope))
            for value in (1, 2, 3, 4, 5):
                perf_rows.append(group_row(base, scenario, f"MIN_COUNT_{value}", base[f"{prefix}_min_indicator_count"].eq(value), scope))
            perf_rows.append(group_row(base, scenario, "IMPOSSIBLE_CORE_ONLY", base[f"{prefix}_status"].eq("IMPOSSIBLE_CORE_ONLY"), scope))
    pd.DataFrame(perf_rows).to_csv(PERF, index=False)

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "origin_count": len(frame),
        "stage_counts": frame["stage"].value_counts().to_dict(),
        "formula": {
            "core_raw_score": "sum(active core weights)",
            "neutral_final_score": "core_raw_score * 1.0",
            "market_cap_final_score": "core_raw_score * (1 + clamp(market_adjustment_strength,0,1)) when enabled, else *1",
            "entry_comparison": "final_score >= signal_threshold",
            "noncore_additives": "news/topic/events/crash_bonus fixed at zero to isolate core-only structure",
        },
        "neutral": {
            "n1": int(frame["neutral_n1"].sum()),
            "n2_or_less": int(frame["neutral_n2_or_less"].sum()),
            "impossible_core_only": int(frame["neutral_status"].eq("IMPOSSIBLE_CORE_ONLY").sum()),
            "min_count_distribution": {str(key): int(value) for key, value in frame["neutral_min_indicator_count"].value_counts(dropna=False).items()},
        },
        "market_cap": {
            "n1": int(frame["market_cap_n1"].sum()),
            "n2_or_less": int(frame["market_cap_n2_or_less"].sum()),
            "impossible_core_only": int(frame["market_cap_status"].eq("IMPOSSIBLE_CORE_ONLY").sum()),
            "min_count_distribution": {str(key): int(value) for key, value in frame["market_cap_min_indicator_count"].value_counts(dropna=False).items()},
        },
        "boil_parity": {
            "existing_boil_count": int(frame["boil_existing"].sum()),
            "captured_n1_market_cap": int(frame["boil_captured_n1_market_cap"].sum()),
            "captured_n2_market_cap": int(frame["boil_captured_n2_market_cap"].sum()),
            "missed_n2_market_cap": int((frame["boil_existing"] & ~frame["boil_captured_n2_market_cap"]).sum()),
        },
        "ce_parity": {
            "existing_ce_fail_count": int(frame["ce_existing_fail"].sum()),
            "captured_n1_market_cap": int(frame["ce_captured_n1_market_cap"].sum()),
            "captured_n2_market_cap": int(frame["ce_captured_n2_market_cap"].sum()),
            "missed_n2_market_cap": int((frame["ce_existing_fail"] & ~frame["ce_captured_n2_market_cap"]).sum()),
        },
        "unjudged_count": 0,
        "enumeration_greedy_parity_failures": int((~frame["enumeration_greedy_match_neutral"] | ~frame["enumeration_greedy_match_market_cap"]).sum()),
        "limitations": [
            "Market-cap multiplier is a code-level upper bound, not proof that a contemporaneous market context attains it.",
            "Positive news/topic/event/crash additions are fixed at zero by the core-only definition; they can lower actual required core count further.",
            "Indicator activation combinations are treated combinatorially; temporal co-occurrence probability is not modeled.",
        ],
        "no_source_mutation": True,
        "no_live_candidate_change": True,
        "no_training": True,
        "no_order": True,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
