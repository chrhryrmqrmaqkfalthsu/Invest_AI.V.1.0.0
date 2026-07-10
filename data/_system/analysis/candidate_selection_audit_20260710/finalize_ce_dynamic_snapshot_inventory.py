from __future__ import annotations

"""CE 동적 스냅샷 감사의 비영속 코드 경로·상태 로그 인벤토리 보강."""

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SOURCE = OUT / "ce_dynamic_snapshot_source_coverage.csv"
QUALITY = OUT / "ce_dynamic_snapshot_quality.csv"
CE7 = OUT / "ce_dynamic_ce7_snapshot_coverage.csv"
SUMMARY = OUT / "ce_dynamic_backtestability_summary.json"
READOUT = OUT / "ce_dynamic_backtestability_readout.md"


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def stable_json(path: Path) -> Any:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    value = json.loads(path.read_text(encoding="utf-8"))
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return value


def row(
    name: str,
    path: str,
    kind: str,
    stage_scope: str,
    records: int,
    candidates: int,
    origin_candidates: int,
    score_threshold: int,
    components: int,
    core: int,
    top2: int,
    outcomes: int,
    historical: bool,
    point: bool,
    same_outcome: bool,
    durable: bool,
    duplicate: bool,
    suitability: str,
    note: str,
) -> dict[str, Any]:
    return {
        "source_name": name,
        "path": path,
        "source_kind": kind,
        "stage_scope": stage_scope,
        "record_n": records,
        "unique_candidate_n": candidates,
        "origin_candidate_n": origin_candidates,
        "score_threshold_n": score_threshold,
        "component_dict_n": components,
        "core_component_n": core,
        "top2_possible_n": top2,
        "outcome_linked_n": outcomes,
        "historical_entry": historical,
        "point_in_time": point,
        "same_entry_outcome_link": same_outcome,
        "durable": durable,
        "duplicate_or_derived": duplicate,
        "ce_ratio_reconstructable": score_threshold > 0,
        "ce_top2_reconstructable": top2 > 0,
        "ce_threshold_backtest_suitability": suitability,
        "note": note,
    }


def main() -> int:
    sources = stable_csv(SOURCE, low_memory=False)
    origin_ids = set(stable_csv(OUT / "integrated_gate_candidate_dryrun.csv", usecols=["candidate_id"])["candidate_id"].astype(str))

    shadow_state = stable_json(ROOT / "data/_system/elite_shadow_state.json")
    open_positions = list((shadow_state.get("open_positions") or {}).values())
    open_ids = {str(x.get("candidate_id")) for x in open_positions if x.get("candidate_id")}
    open_score = sum(
        x.get("entry_score") is not None and x.get("entry_threshold") is not None
        for x in open_positions
    )

    slot_events = []
    event_path = ROOT / "data/_system/live_slots_events.jsonl"
    before = (event_path.stat().st_size, event_path.stat().st_mtime_ns)
    with event_path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                slot_events.append(value)
    after = (event_path.stat().st_size, event_path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {event_path}")
    slot_event_ids = {str(x.get("candidate_id")) for x in slot_events if x.get("candidate_id")}

    additions = pd.DataFrame([
        row(
            "ELITE_SHADOW_OPEN_STATE",
            "data/_system/elite_shadow_state.json#open_positions",
            "OPEN_SHADOW_ENTRY_STATE",
            "stage2+stage3",
            len(open_positions), len(open_ids), len(open_ids & origin_ids), open_score,
            0, 0, 0, 0, True, True, False, True, True,
            "RATIO_ONLY_NO_COMPONENTS",
            "open entry score/threshold retained; entry_concentration components are allocation-quality scores, not MA/MACD/RSI/BB/Volume signal contributions",
        ),
        row(
            "LIVE_SLOTS_EVENTS",
            "data/_system/live_slots_events.jsonl",
            "OPERATIONAL_EVENT_LOG",
            "stage2+stage3",
            len(slot_events), len(slot_event_ids), len(slot_event_ids & origin_ids), 0,
            0, 0, 0, 0, False, True, False, True, False,
            "NO_STRUCTURED_CE_FIELDS",
            "2,848 operational refresh/buy-intent events; no structured final_score/threshold/components payload",
        ),
        row(
            "EVALUATE_SIGNAL_RETURN_SCHEMA",
            "engine/strategies/evaluator.py#SignalResult",
            "RUNTIME_RETURN_NOT_LOG",
            "all evaluator callers",
            0, 0, 0, 0, 0, 0, 0, 0, False, True, False, False, False,
            "CAPABLE_BUT_NOT_PERSISTED",
            "runtime object contains score, raw_score, threshold, market_adjustment and components; persistence depends on caller",
        ),
        row(
            "ELITE_SHADOW_REPORT_RUNTIME",
            "engine/live/elite_shadow_report.py",
            "RUNTIME_REPORT_NOT_DURABLE_SNAPSHOT",
            "stage2+stage3 elite",
            0, 0, 0, 0, 0, 0, 0, 0, False, True, False, False, True,
            "NOT_A_DURABLE_CE_LOG",
            "report is rebuilt from artifacts; live93 audit is the durable derived CE point snapshot",
        ),
        row(
            "ELITE_SIGNAL_HISTORY_RUNTIME",
            "engine/live/elite_signal_history.py",
            "ON_DEMAND_REPLAY_NOT_PERSISTED",
            "single queried candidate",
            0, 0, 0, 0, 0, 0, 0, 0, True, False, False, False, True,
            "RATIO_REPLAY_CODE_OMITS_COMPONENTS",
            "on-demand rows contain score/raw/threshold/ratio but omit components and use current market context replay",
        ),
        row(
            "APPLICATION_TEXT_LOGS",
            "data/logs/*.log; logs/*.log",
            "UNSTRUCTURED_TEXT_LOG",
            "mixed",
            0, 0, 0, 0, 0, 0, 0, 0, False, False, False, True, False,
            "NO_STRUCTURED_CE_SNAPSHOT_FOUND",
            "inventory grep found no durable structured score+threshold+component entry records",
        ),
    ])
    sources = sources[~sources["source_name"].isin(additions["source_name"])].copy()
    sources = pd.concat([sources, additions], ignore_index=True)
    sources.to_csv(SOURCE, index=False)

    quality = sources[[
        "source_name", "path", "source_kind", "stage_scope", "historical_entry", "point_in_time",
        "same_entry_outcome_link", "durable", "duplicate_or_derived", "ce_ratio_reconstructable",
        "ce_top2_reconstructable", "ce_threshold_backtest_suitability", "note",
    ]].copy()
    quality["quality_class"] = quality.apply(
        lambda r: (
            "FULL_BACKTEST_INPUT"
            if r.same_entry_outcome_link and r.ce_ratio_reconstructable and r.ce_top2_reconstructable
            else "FULL_POINT_DIAGNOSTIC"
            if r.point_in_time and r.ce_ratio_reconstructable and r.ce_top2_reconstructable
            else "RATIO_ONLY"
            if r.ce_ratio_reconstructable and not r.ce_top2_reconstructable
            else "OUTCOME_ONLY"
            if "OUTCOME" in str(r.source_kind) or "EXIT_TRADE" in str(r.source_kind)
            else "RUNTIME_CAPABILITY_NOT_PERSISTED"
            if not r.durable and "CAPABLE" in str(r.ce_threshold_backtest_suitability)
            else "NO_CE_DYNAMIC_DATA"
        ),
        axis=1,
    )
    quality.to_csv(QUALITY, index=False)

    ce7 = stable_csv(CE7, low_memory=False)
    event_counts = pd.Series(
        [str(x.get("candidate_id")) for x in shadow_state.get("events", []) if x.get("candidate_id")]
    ).value_counts()
    ce7["elite_shadow_open_ratio_snapshot"] = ce7["candidate_id"].isin(open_ids)
    ce7["elite_shadow_state_event_n"] = ce7["candidate_id"].map(event_counts).fillna(0).astype(int)
    ce7["durable_full_historical_entry_snapshot_n"] = ce7["canonical_full_ce_snapshot_rows"]
    ce7["systemwide_threshold_validation_ready"] = False
    ce7.to_csv(CE7, index=False)

    summary = stable_json(SUMMARY)
    summary["source_count"] = len(sources)
    summary["inventory_addendum"] = {
        "elite_shadow_open_state_records": len(open_positions),
        "elite_shadow_open_state_origin_candidates": len(open_ids & origin_ids),
        "live_slots_event_records": len(slot_events),
        "live_slots_event_candidate_ids": len(slot_event_ids),
        "runtime_evaluator_has_components": True,
        "runtime_evaluator_persistence_is_caller_dependent": True,
        "structured_application_log_snapshot_found": False,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    text = READOUT.read_text(encoding="utf-8")
    marker = "## 8. 추가 인벤토리 — 상태·런타임·텍스트 로그"
    if marker not in text:
        text += (
            "\n" + marker + "\n\n"
            f"- `elite_shadow_state.json#open_positions`: {len(open_positions)}개 open entry의 score/threshold/ratio는 있으나 technical component는 없다. "
            "`entry_concentration.components`는 배분 품질 점수이며 CE의 MA/MACD/RSI/BB/Volume 기여도가 아니다.\n"
            f"- `live_slots_events.jsonl`: {len(slot_events):,}개 운영 이벤트 중 구조화된 score+threshold+component 진입 스냅샷은 0건이다.\n"
            "- `evaluate_signal` 반환 객체 자체에는 필요한 component가 모두 있지만, 호출자가 이를 저장하지 않으면 과거 검증 자료로 남지 않는다.\n"
            "- `elite_shadow_report`와 `elite_signal_history`는 런타임/온디맨드 계산 경로이며 durable full CE 로그가 아니다. 특히 signal history는 component를 출력하지 않고 현재 시장 컨텍스트를 과거 날짜에 고정 재생한다.\n"
            "- `data/logs/*.log`, `logs/*.log`에서는 구조화된 CE 진입 스냅샷을 찾지 못했다.\n"
        )
        READOUT.write_text(text, encoding="utf-8")

    print(json.dumps({
        "source_count": len(sources),
        "elite_shadow_open_records": len(open_positions),
        "live_slots_event_records": len(slot_events),
        "ce7_shadow_open": int(ce7["elite_shadow_open_ratio_snapshot"].sum()),
        "verdict": summary["verdict"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
