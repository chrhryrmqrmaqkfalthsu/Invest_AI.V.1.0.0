from __future__ import annotations

import json, math, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
AUDIT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
if str(AUDIT) not in sys.path:
    sys.path.insert(0, str(AUDIT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import integrated_gate_sim_core as core

LIVE93 = AUDIT / "live93_three_symptom_scan.csv"
FULL = AUDIT / "ce_origin_full_calculation.csv"
UNJUDGED = AUDIT / "ce_origin_residual_unjudged.csv"
FAILS = AUDIT / "ce_origin_fail_rejudged.csv"
PARITY = AUDIT / "ce_origin_live93_parity.csv"
CAUSES = AUDIT / "ce_origin_unjudged_cause_summary.csv"
SUMMARY = AUDIT / "ce_origin_coverage_summary.json"

CORE = {
    "ma_align": "weight_ma_align",
    "macd": "weight_macd_golden",
    "rsi": "weight_rsi_zone",
    "bb": "weight_bb_near_lower",
    "volume": "weight_volume_surge",
}
RATIO_CUT = 1.25
TOP2_CUT = 90.0


def f(value: Any) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else math.nan
    except Exception:
        return math.nan


def b(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def top2(values: dict[str, float]) -> tuple[float, str, float]:
    positive = [(name, max(0.0, f(value))) for name, value in values.items() if f(value) > 0.0]
    positive.sort(key=lambda item: (-item[1], item[0]))
    total = sum(value for _, value in positive)
    if total <= 0.0:
        return math.nan, "", 0.0
    names = "+".join(name for name, _ in positive[:2])
    return sum(value for _, value in positive[:2]) / total * 100.0, names, total


def effective_stage2_schema() -> dict[str, int]:
    files = 0
    with_components = 0
    with_score = 0
    for directory in core.effective_stage2_dirs():
        path = directory / "trades.jsonl"
        if not path.is_file() or path.stat().st_size == 0:
            continue
        files += 1
        try:
            row = json.loads(next(line for line in path.open(errors="ignore") if line.strip()))
        except Exception:
            continue
        with_components += int(isinstance(row.get("entry_signal_components"), dict))
        with_score += int(row.get("entry_signal_score") is not None and row.get("entry_signal_threshold") is not None)
    return {"files": files, "with_components": with_components, "with_score": with_score}


def stage3_schema() -> dict[str, int]:
    files = 0
    with_components = 0
    with_score = 0
    for path in sorted((ROOT / "exp_batch_stage123_2009_20260616_full/tickers").glob("*/stage3/exit_trades.jsonl")):
        if not path.is_file() or path.stat().st_size == 0:
            continue
        files += 1
        try:
            row = json.loads(next(line for line in path.open(errors="ignore") if line.strip()))
        except Exception:
            continue
        with_components += int(isinstance(row.get("entry_signal_components"), dict))
        with_score += int(row.get("entry_signal_score") is not None and row.get("entry_signal_threshold") is not None)
    return {"files": files, "with_components": with_components, "with_score": with_score}


def main() -> int:
    origins, _ = core.load_origins()
    assert len(origins) == 17_071
    origin_map = {row["candidate_id"]: row for row in origins}
    live = pd.read_csv(LIVE93)
    live_map = {str(row.candidate_id): row._asdict() for row in live.itertuples(index=False)}

    full_rows: list[dict[str, Any]] = []
    parity_rows: list[dict[str, Any]] = []
    for candidate_id, origin in origin_map.items():
        rb = origin["rulebook"]
        weights = {name: f(rb.get(field)) for name, field in CORE.items()}
        static_top2, static_names, static_sum = top2(weights)
        threshold = f(rb.get("signal_threshold"))
        live_row = live_map.get(candidate_id)
        exact_ratio = exact_top2 = final_score = math.nan
        exact_top2_names = ""
        should_buy: bool | None = None
        coverage = "NO_DYNAMIC_SNAPSHOT"
        ce_status = "UNJUDGED_DYNAMIC_INPUT_REQUIRED"
        ce_fail: bool | None = None
        core_values = {name: math.nan for name in CORE}

        if live_row is not None:
            coverage = "LIVE93_DYNAMIC_SNAPSHOT"
            final_score = f(live_row.get("final_score"))
            live_threshold = f(live_row.get("threshold"))
            exact_ratio = final_score / live_threshold if math.isfinite(final_score) and live_threshold > 0 else math.nan
            core_values = {
                "ma_align": f(live_row.get("core_ma")),
                "macd": f(live_row.get("core_macd")),
                "rsi": f(live_row.get("core_rsi")),
                "bb": f(live_row.get("core_bb")),
                "volume": f(live_row.get("core_volume")),
            }
            exact_top2, exact_top2_names, core_sum = top2(core_values)
            should_buy = b(live_row.get("should_buy"))
            if not should_buy:
                ce_status = "NOT_APPLICABLE_NO_BUY"
                ce_fail = False
            elif math.isfinite(exact_ratio) and math.isfinite(exact_top2):
                ce_fail = bool(exact_ratio < RATIO_CUT and exact_top2 >= TOP2_CUT)
                ce_status = "FAIL" if ce_fail else "PASS"
            else:
                ce_status = "UNJUDGED_SNAPSHOT_VALUE_MISSING"

            stored_ratio = f(live_row.get("ratio"))
            stored_top2 = f(live_row.get("top2_share_pct"))
            component_weight_diffs = []
            for name, value in core_values.items():
                if math.isfinite(value) and value > 0 and math.isfinite(weights[name]):
                    component_weight_diffs.append(abs(value - weights[name]))
            parity_rows.append({
                "candidate_id": candidate_id,
                "stage": origin["stage"],
                "ticker": origin["ticker"],
                "should_buy_stored": should_buy,
                "should_buy_recomputed": bool(final_score >= live_threshold) if math.isfinite(final_score) and math.isfinite(live_threshold) else None,
                "threshold_origin": threshold,
                "threshold_snapshot": live_threshold,
                "threshold_abs_diff": abs(threshold - live_threshold),
                "ratio_stored": stored_ratio,
                "ratio_recomputed": exact_ratio,
                "ratio_abs_diff": abs(stored_ratio - exact_ratio),
                "top2_stored_pct": stored_top2,
                "top2_recomputed_pct": exact_top2,
                "top2_abs_diff": abs(stored_top2 - exact_top2) if math.isfinite(stored_top2) and math.isfinite(exact_top2) else math.nan,
                "top2_zero_core_both_undefined": bool(not math.isfinite(stored_top2) and not math.isfinite(exact_top2)),
                "active_component_vs_rule_weight_max_abs_diff": max(component_weight_diffs) if component_weight_diffs else 0.0,
                "ce_fail_recomputed": ce_fail,
                "static_weight_top2_pct": static_top2,
                "static_vs_realized_top2_abs_diff": abs(static_top2 - exact_top2) if math.isfinite(static_top2) and math.isfinite(exact_top2) else math.nan,
            })

        full_rows.append({
            "candidate_id": candidate_id,
            "stage": origin["stage"],
            "ticker": origin["ticker"],
            "rulebook_hash": origin["rulebook_hash"],
            "source_file": origin["source_file"],
            "source_row_index": origin["source_row_index"],
            "signal_threshold": threshold,
            **{field: weights[name] for name, field in CORE.items()},
            "static_positive_core_weight_sum": static_sum,
            "static_weight_top2_components": static_names,
            "static_weight_top2_share_pct": static_top2,
            "static_weight_metric_is_ce_equivalent": False,
            "origin_has_realized_final_score": False,
            "origin_has_realized_components": False,
            "origin_has_market_sector_vix_context": False,
            "origin_has_news_topic_event_context": False,
            "dynamic_snapshot_source": coverage,
            "snapshot_final_score": final_score,
            "snapshot_should_buy": should_buy,
            "ratio_exact": exact_ratio,
            "top2_exact_pct": exact_top2,
            "top2_exact_components": exact_top2_names,
            **{f"active_component_{name}": core_values[name] for name in CORE},
            "ce_status": ce_status,
            "ce_fail": ce_fail,
            "unjudged_reason": "CURRENT_OHLCV_ACTIVATION_AND_MARKET_NEWS_EVENT_CONTEXT_NOT_STORED_IN_ORIGIN" if coverage == "NO_DYNAMIC_SNAPSHOT" else "",
        })

    full = pd.DataFrame(full_rows).sort_values(["stage", "ticker", "candidate_id"])
    parity = pd.DataFrame(parity_rows).sort_values(["stage", "ticker", "candidate_id"])
    full.to_csv(FULL, index=False)
    parity.to_csv(PARITY, index=False)
    full[full["ce_status"].str.startswith("UNJUDGED")].to_csv(UNJUDGED, index=False)
    full[full["ce_status"].eq("FAIL")].to_csv(FAILS, index=False)

    s2_schema = effective_stage2_schema()
    s3_schema = stage3_schema()
    causes = [
        {"category": "ORIGIN_THRESHOLD_PRESENT", "count": int(full["signal_threshold"].notna().sum()), "meaning": "정적 임계 저장"},
        {"category": "ORIGIN_CORE_WEIGHTS_PRESENT", "count": int(full[list(CORE.values())].notna().all(axis=1).sum()), "meaning": "정적 가중치 저장"},
        {"category": "ORIGIN_REALIZED_FINAL_SCORE_PRESENT", "count": 0, "meaning": "원본에 현재 final_score 없음"},
        {"category": "ORIGIN_REALIZED_COMPONENTS_PRESENT", "count": 0, "meaning": "원본에 현재 활성 컴포넌트 없음"},
        {"category": "LIVE93_DYNAMIC_SNAPSHOT", "count": int(full["dynamic_snapshot_source"].eq("LIVE93_DYNAMIC_SNAPSHOT").sum()), "meaning": "read-only 동적 평가 snapshot"},
        {"category": "CE_JUDGED_PASS", "count": int(full["ce_status"].eq("PASS").sum()), "meaning": "should_buy이며 CE 통과"},
        {"category": "CE_JUDGED_FAIL", "count": int(full["ce_status"].eq("FAIL").sum()), "meaning": "should_buy이며 CE 차단"},
        {"category": "CE_NOT_APPLICABLE_NO_BUY", "count": int(full["ce_status"].eq("NOT_APPLICABLE_NO_BUY").sum()), "meaning": "동적 값은 있으나 should_buy=False"},
        {"category": "CE_RESIDUAL_UNJUDGED", "count": int(full["ce_status"].str.startswith("UNJUDGED").sum()), "meaning": "동적 입력 snapshot 없음"},
        {"category": "STAGE2_TRADE_FILES_WITH_HISTORICAL_COMPONENTS", "count": s2_schema["with_components"], "meaning": f"{s2_schema['files']}개 파일 중; 과거 진입시점 값이라 현재 CE와 비동일"},
        {"category": "STAGE3_EXIT_TRADE_FILES_WITH_HISTORICAL_COMPONENTS", "count": s3_schema["with_components"], "meaning": f"{s3_schema['files']}개 파일 중; Stage3 exit_trades에 점수 구성 없음"},
    ]
    pd.DataFrame(causes).to_csv(CAUSES, index=False)

    finite_top2 = parity["top2_abs_diff"].notna()
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_origin_count": len(full),
        "stage_counts": full["stage"].value_counts().to_dict(),
        "previous_all_block_ce_counts": {"PASS": 27, "FAIL": 7, "UNJUDGED": 17_037},
        "corrected_ce_counts": full["ce_status"].value_counts().to_dict(),
        "unjudged_reduction": 17_037 - int(full["ce_status"].str.startswith("UNJUDGED").sum()),
        "exact_dynamic_value_coverage": int(full["dynamic_snapshot_source"].eq("LIVE93_DYNAMIC_SNAPSHOT").sum()),
        "residual_unjudged": int(full["ce_status"].str.startswith("UNJUDGED").sum()),
        "ce_fail_count_same_semantics": int(full["ce_status"].eq("FAIL").sum()),
        "live93_ratio_top2_only_fail_without_should_buy_gate": int(((pd.to_numeric(live["ratio"], errors="coerce") < RATIO_CUT) & (pd.to_numeric(live["top2_share_pct"], errors="coerce") >= TOP2_CUT)).sum()),
        "parity": {
            "rows": len(parity),
            "threshold_max_abs_diff": float(parity["threshold_abs_diff"].max()),
            "ratio_max_abs_diff": float(parity["ratio_abs_diff"].max()),
            "top2_finite_rows": int(finite_top2.sum()),
            "top2_max_abs_diff": float(parity.loc[finite_top2, "top2_abs_diff"].max()),
            "top2_zero_core_both_undefined": int(parity["top2_zero_core_both_undefined"].sum()),
            "should_buy_match_count": int((parity["should_buy_stored"].astype(str) == parity["should_buy_recomputed"].astype(str)).sum()),
            "active_component_weight_max_abs_diff": float(parity["active_component_vs_rule_weight_max_abs_diff"].max()),
            "static_weight_top2_exact_match_count": int((parity["static_vs_realized_top2_abs_diff"].fillna(1e9) <= 1e-12).sum()),
        },
        "static_reconstruction_verdict": "IMPOSSIBLE_WITH_ORIGIN_ONLY",
        "reason": "final_score and realized core components require as-of OHLCV indicator activation plus market/sector/VIX/news/topic/event context; none are stored in survivors/final_rulebooks",
        "stage2_historical_trade_schema": s2_schema,
        "stage3_exit_trade_schema": s3_schema,
        "no_simulation": True,
        "no_source_mutation": True,
        "no_live_candidate_change": True,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
