from __future__ import annotations

"""CE 동적 게이트의 스냅샷 백테스트 가능성 read-only 감사.

원본·라이브·운영 코드·재학습·주문·삭제를 수행하지 않는다.
분석 산출물만 작성한다.
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"

SOURCE_OUT = OUT / "ce_dynamic_snapshot_source_coverage.csv"
CANDIDATE_OUT = OUT / "ce_dynamic_snapshot_candidate_coverage.csv.gz"
CE7_OUT = OUT / "ce_dynamic_ce7_snapshot_coverage.csv"
QUALITY_OUT = OUT / "ce_dynamic_snapshot_quality.csv"
SUMMARY_OUT = OUT / "ce_dynamic_backtestability_summary.json"
READOUT_OUT = OUT / "ce_dynamic_backtestability_readout.md"

CORE_KEYS = ("ma_align", "macd", "rsi", "bb", "volume")


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


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                yield row
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")


def numeric(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def core_dict_complete(value: Any) -> bool:
    return isinstance(value, dict) and all(key in value and numeric(value.get(key)) for key in CORE_KEYS)


def core_top2_possible(value: Any) -> bool:
    if not core_dict_complete(value):
        return False
    positive = [max(0.0, float(value.get(key) or 0.0)) for key in CORE_KEYS]
    return sum(positive) > 0.0


def source_row(
    *,
    source_name: str,
    path: str,
    source_kind: str,
    stage_scope: str,
    record_n: int,
    unique_candidate_n: int,
    origin_candidate_n: int,
    score_threshold_n: int,
    component_dict_n: int,
    core_component_n: int,
    top2_possible_n: int,
    outcome_linked_n: int,
    historical_entry: bool,
    point_in_time: bool,
    same_entry_outcome_link: bool,
    durable: bool,
    duplicate_or_derived: bool,
    suitability: str,
    note: str,
) -> dict[str, Any]:
    return {
        "source_name": source_name,
        "path": path,
        "source_kind": source_kind,
        "stage_scope": stage_scope,
        "record_n": int(record_n),
        "unique_candidate_n": int(unique_candidate_n),
        "origin_candidate_n": int(origin_candidate_n),
        "score_threshold_n": int(score_threshold_n),
        "component_dict_n": int(component_dict_n),
        "core_component_n": int(core_component_n),
        "top2_possible_n": int(top2_possible_n),
        "outcome_linked_n": int(outcome_linked_n),
        "historical_entry": bool(historical_entry),
        "point_in_time": bool(point_in_time),
        "same_entry_outcome_link": bool(same_entry_outcome_link),
        "durable": bool(durable),
        "duplicate_or_derived": bool(duplicate_or_derived),
        "ce_ratio_reconstructable": int(score_threshold_n) > 0,
        "ce_top2_reconstructable": int(top2_possible_n) > 0,
        "ce_threshold_backtest_suitability": suitability,
        "note": note,
    }


def flatten_candidate_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("candidate_id"):
            records.append(value)
        for child in value.values():
            records.extend(flatten_candidate_records(child))
    elif isinstance(value, list):
        for child in value:
            records.extend(flatten_candidate_records(child))
    return records


def summarize_records(
    records: list[dict[str, Any]],
    *,
    score_keys: tuple[str, ...],
    threshold_keys: tuple[str, ...],
    component_keys: tuple[str, ...],
    outcome_keys: tuple[str, ...],
) -> dict[str, Any]:
    unique_ids = {str(r.get("candidate_id")) for r in records if r.get("candidate_id")}
    score_threshold = 0
    components = 0
    core = 0
    top2 = 0
    outcomes = 0
    for row in records:
        score = next((row.get(k) for k in score_keys if row.get(k) is not None), None)
        threshold = next((row.get(k) for k in threshold_keys if row.get(k) is not None), None)
        comp = next((row.get(k) for k in component_keys if isinstance(row.get(k), dict)), None)
        if numeric(score) and numeric(threshold) and float(threshold) > 0:
            score_threshold += 1
        if isinstance(comp, dict):
            components += 1
        if core_dict_complete(comp):
            core += 1
        if core_top2_possible(comp):
            top2 += 1
        if any(row.get(k) is not None for k in outcome_keys):
            outcomes += 1
    return {
        "record_n": len(records),
        "unique_candidate_n": len(unique_ids),
        "candidate_ids": unique_ids,
        "score_threshold_n": score_threshold,
        "component_dict_n": components,
        "core_component_n": core,
        "top2_possible_n": top2,
        "outcome_linked_n": outcomes,
    }


def scan_canonical(candidates: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    targets_by_file: dict[tuple[str, Path], dict[str, str]] = defaultdict(dict)
    stage_by_candidate = dict(zip(candidates["candidate_id"], candidates["stage"]))
    for row in candidates.itertuples(index=False):
        parent = (ROOT / str(row.source_file)).parent
        history = parent / ("trades.jsonl" if row.stage == "stage2" else "exit_trades.jsonl")
        targets_by_file[(str(row.stage), history)][str(row.rulebook_hash)] = str(row.candidate_id)

    stats = {
        str(cid): {
            "canonical_trade_rows": 0,
            "canonical_score_threshold_rows": 0,
            "canonical_component_rows": 0,
            "canonical_core_component_rows": 0,
            "canonical_top2_possible_rows": 0,
            "canonical_outcome_rows": 0,
            "canonical_full_ce_snapshot_rows": 0,
            "canonical_periods": set(),
        }
        for cid in candidates["candidate_id"]
    }
    files_summary: dict[str, dict[str, int]] = {
        "stage2": defaultdict(int),
        "stage3": defaultdict(int),
    }

    for file_index, ((stage, path), target_map) in enumerate(targets_by_file.items(), 1):
        marker = "rulebook_hash" if stage == "stage2" else "final_rulebook_hash"
        files_summary[stage]["files"] += 1
        file_has_score = file_has_components = file_has_core = False
        for row in jsonl_rows(path):
            files_summary[stage]["all_file_rows"] += 1
            cid = target_map.get(str(row.get(marker) or ""))
            if cid is None:
                continue
            s = stats[cid]
            s["canonical_trade_rows"] += 1
            files_summary[stage]["matched_rows"] += 1
            s["canonical_periods"].add(str(row.get("period_label") or ""))
            score = row.get("entry_signal_score")
            threshold = row.get("entry_signal_threshold")
            components = row.get("entry_signal_components")
            has_score = numeric(score) and numeric(threshold) and float(threshold) > 0
            has_components = isinstance(components, dict)
            has_core = core_dict_complete(components)
            has_top2 = core_top2_possible(components)
            has_outcome = numeric(row.get("pnl_pct"))
            if has_score:
                s["canonical_score_threshold_rows"] += 1
                files_summary[stage]["score_threshold_rows"] += 1
                file_has_score = True
            if has_components:
                s["canonical_component_rows"] += 1
                files_summary[stage]["component_rows"] += 1
                file_has_components = True
            if has_core:
                s["canonical_core_component_rows"] += 1
                files_summary[stage]["core_component_rows"] += 1
                file_has_core = True
            if has_top2:
                s["canonical_top2_possible_rows"] += 1
                files_summary[stage]["top2_possible_rows"] += 1
            if has_outcome:
                s["canonical_outcome_rows"] += 1
                files_summary[stage]["outcome_rows"] += 1
            if has_score and has_core and has_top2 and has_outcome:
                s["canonical_full_ce_snapshot_rows"] += 1
                files_summary[stage]["full_ce_rows"] += 1
        files_summary[stage]["files_with_score"] += int(file_has_score)
        files_summary[stage]["files_with_components"] += int(file_has_components)
        files_summary[stage]["files_with_core"] += int(file_has_core)
        if file_index % 100 == 0:
            print(f"canonical progress {file_index}/{len(targets_by_file)}", flush=True)

    rows = []
    for cid, value in stats.items():
        rows.append(
            {
                "candidate_id": cid,
                "stage": stage_by_candidate[cid],
                **{k: v for k, v in value.items() if k != "canonical_periods"},
                "canonical_period_n": len(value["canonical_periods"]),
                "canonical_any_history": value["canonical_trade_rows"] > 0,
                "canonical_full_ce_backtestable": value["canonical_full_ce_snapshot_rows"] > 0,
            }
        )
    coverage = pd.DataFrame(rows)
    source_rows = []
    for stage in ("stage2", "stage3"):
        f = files_summary[stage]
        stage_cov = coverage[coverage["stage"].eq(stage)]
        source_rows.append(
            source_row(
                source_name=f"CANONICAL_{stage.upper()}_HISTORY",
                path=(
                    "exp_batch_stage123_2009_20260616_full/tickers/*/stage2*/trades.jsonl"
                    if stage == "stage2"
                    else "exp_batch_stage123_2009_20260616_full/tickers/*/stage3/exit_trades.jsonl"
                ),
                source_kind="HISTORICAL_ENTRY_TRADE" if stage == "stage2" else "HISTORICAL_EXIT_TRADE",
                stage_scope=stage,
                record_n=f["matched_rows"],
                unique_candidate_n=int(stage_cov["canonical_any_history"].sum()),
                origin_candidate_n=int(stage_cov["canonical_any_history"].sum()),
                score_threshold_n=f["score_threshold_rows"],
                component_dict_n=f["component_rows"],
                core_component_n=f["core_component_rows"],
                top2_possible_n=f["top2_possible_rows"],
                outcome_linked_n=f["outcome_rows"],
                historical_entry=stage == "stage2",
                point_in_time=False,
                same_entry_outcome_link=stage == "stage2" and f["full_ce_rows"] > 0,
                durable=True,
                duplicate_or_derived=False,
                suitability="BACKTESTABLE_STAGE2_ONLY" if stage == "stage2" else "NOT_USABLE_NO_ENTRY_SIGNAL_SNAPSHOT",
                note=(
                    f"files={f['files']}; files_with_core={f['files_with_core']}; full_ce_rows={f['full_ce_rows']}"
                    if stage == "stage2"
                    else f"files={f['files']}; exit outcomes exist but entry score/components absent"
                ),
            )
        )
    return coverage, source_rows


def main() -> int:
    candidates = stable_csv(
        OUT / "integrated_gate_candidate_dryrun.csv",
        usecols=["candidate_id", "stage", "ticker", "rulebook_hash", "source_file"],
        low_memory=False,
    )
    if len(candidates) != 17_071:
        raise AssertionError(len(candidates))
    ce7 = stable_csv(OUT / "ce_origin_fail_rejudged.csv", low_memory=False)[
        ["candidate_id", "stage", "ticker"]
    ].drop_duplicates()
    if len(ce7) != 7:
        raise AssertionError(len(ce7))

    candidate_coverage, sources = scan_canonical(candidates)
    origin_ids = set(candidates["candidate_id"])

    live93 = stable_csv(OUT / "live93_three_symptom_scan.csv", low_memory=False)
    live93_ids = set(live93["candidate_id"].astype(str))
    live93_score = live93["final_score"].notna() & live93["threshold"].gt(0)
    live93_core = live93[["core_ma", "core_macd", "core_rsi", "core_bb", "core_volume"]].notna().all(axis=1)
    live93_top2 = pd.to_numeric(live93["top2_share_pct"], errors="coerce").notna()
    sources.append(
        source_row(
            source_name="LIVE93_THREE_SYMPTOM_SCAN",
            path=str((OUT / "live93_three_symptom_scan.csv").relative_to(ROOT)),
            source_kind="POINT_IN_TIME_REEVALUATION",
            stage_scope="stage2+stage3",
            record_n=len(live93), unique_candidate_n=live93["candidate_id"].nunique(),
            origin_candidate_n=len(live93_ids & origin_ids), score_threshold_n=int(live93_score.sum()),
            component_dict_n=int(live93_core.sum()), core_component_n=int(live93_core.sum()),
            top2_possible_n=int(live93_top2.sum()), outcome_linked_n=0,
            historical_entry=False, point_in_time=True, same_entry_outcome_link=False, durable=True,
            duplicate_or_derived=False, suitability="DIAGNOSTIC_POINT_SNAPSHOT_ONLY",
            note="one read-only evaluation per candidate; aggregate exit history is not the same entry outcome",
        )
    )

    dashboard = stable_json(ROOT / "data/_system/real_dashboard_buy_candidates.json")
    dashboard_records = list((dashboard.get("candidates") or {}).values())
    dashboard_stats = summarize_records(
        dashboard_records, score_keys=("final_score",), threshold_keys=("threshold",),
        component_keys=("components",), outcome_keys=("pnl_pct",),
    )
    sources.append(
        source_row(
            source_name="REAL_DASHBOARD_BUY_CANDIDATES", path="data/_system/real_dashboard_buy_candidates.json",
            source_kind="CURRENT_BUY_CANDIDATE_SNAPSHOT", stage_scope="stage2+stage3",
            origin_candidate_n=len(dashboard_stats["candidate_ids"] & origin_ids), historical_entry=False,
            point_in_time=True, same_entry_outcome_link=False, durable=True, duplicate_or_derived=True,
            suitability="DIAGNOSTIC_POINT_SNAPSHOT_ONLY", note="full components but current candidate state only; no later outcome link",
            **{k: dashboard_stats[k] for k in (
                "record_n", "unique_candidate_n", "score_threshold_n", "component_dict_n",
                "core_component_n", "top2_possible_n", "outcome_linked_n"
            )},
        )
    )

    slots = stable_json(ROOT / "data/_system/live_slots_state.json")
    slot_records = list(slots.get("candidate_pool") or [])
    slot_stats = summarize_records(
        slot_records, score_keys=("final_score",), threshold_keys=("threshold",),
        component_keys=("components",), outcome_keys=("pnl_pct",),
    )
    sources.append(
        source_row(
            source_name="LIVE_SLOTS_STATE_CANDIDATE_POOL", path="data/_system/live_slots_state.json#candidate_pool",
            source_kind="CURRENT_SLOT_SNAPSHOT", stage_scope="stage2+stage3",
            origin_candidate_n=len(slot_stats["candidate_ids"] & origin_ids), historical_entry=False,
            point_in_time=True, same_entry_outcome_link=False, durable=True, duplicate_or_derived=True,
            suitability="RATIO_ONLY_NO_COMPONENTS", note="score/threshold retained; realized component dict omitted",
            **{k: slot_stats[k] for k in (
                "record_n", "unique_candidate_n", "score_threshold_n", "component_dict_n",
                "core_component_n", "top2_possible_n", "outcome_linked_n"
            )},
        )
    )

    auxiliary_specs = [
        (
            "ELITE_SHADOW_CLOSED_TRADES", ROOT / "data/_system/elite_shadow_trades.jsonl",
            "CLOSED_SHADOW_ENTRY_OUTCOME", ("entry_score",), ("entry_threshold",), ("components", "entry_signal_components"),
            ("pnl_pct",), "RATIO_OUTCOME_ONLY_NO_COMPONENTS", "entry ratio and outcome retained; component decomposition omitted",
        ),
        (
            "ELITE_STRATEGY_SIM_TRADES", ROOT / "data/_system/elite_strategy_sim_trades.jsonl",
            "CLOSED_SIM_ENTRY_OUTCOME", ("entry_score",), ("entry_threshold",), ("components",),
            ("pnl_pct",), "RATIO_OUTCOME_ONLY_NO_COMPONENTS", "entry ratio and outcome retained; component decomposition omitted",
        ),
    ]
    auxiliary_record_maps: dict[str, list[dict[str, Any]]] = {}
    for name, path, kind, score_keys, threshold_keys, component_keys, outcome_keys, suitability, note in auxiliary_specs:
        records = list(jsonl_rows(path))
        auxiliary_record_maps[name] = records
        s = summarize_records(
            records, score_keys=score_keys, threshold_keys=threshold_keys,
            component_keys=component_keys, outcome_keys=outcome_keys,
        )
        sources.append(
            source_row(
                source_name=name, path=str(path.relative_to(ROOT)), source_kind=kind, stage_scope="stage2+stage3",
                origin_candidate_n=len(s["candidate_ids"] & origin_ids), historical_entry=True, point_in_time=False,
                same_entry_outcome_link=True, durable=True, duplicate_or_derived=False, suitability=suitability, note=note,
                **{k: s[k] for k in (
                    "record_n", "unique_candidate_n", "score_threshold_n", "component_dict_n",
                    "core_component_n", "top2_possible_n", "outcome_linked_n"
                )},
            )
        )

    replay_path = ROOT / "data/_system/research/central_portfolio/daily_signal_replay/daily_signal_replay.jsonl"
    replay = list(jsonl_rows(replay_path))
    replay_stats = summarize_records(
        replay, score_keys=("current_score",), threshold_keys=("current_threshold",),
        component_keys=("components",), outcome_keys=("pnl_pct", "price_path_proxy_baseline"),
    )
    hash_to_origin = defaultdict(set)
    for row in candidates.itertuples(index=False):
        hash_to_origin[str(row.rulebook_hash)].add(str(row.candidate_id))
    replay_origin_ids = set()
    for row in replay:
        replay_origin_ids |= hash_to_origin.get(str(row.get("rulebook_hash") or ""), set())
    sources.append(
        source_row(
            source_name="DAILY_SIGNAL_REPLAY", path=str(replay_path.relative_to(ROOT)),
            source_kind="LIMITED_HISTORICAL_REPLAY", stage_scope="research subset",
            record_n=replay_stats["record_n"], unique_candidate_n=replay_stats["unique_candidate_n"],
            origin_candidate_n=len(replay_origin_ids), score_threshold_n=replay_stats["score_threshold_n"],
            component_dict_n=replay_stats["component_dict_n"], core_component_n=replay_stats["core_component_n"],
            top2_possible_n=replay_stats["top2_possible_n"], outcome_linked_n=replay_stats["outcome_linked_n"],
            historical_entry=True, point_in_time=False, same_entry_outcome_link=False, durable=True,
            duplicate_or_derived=True, suitability="LIMITED_PROBE_NOT_SYSTEM_COVERAGE",
            note="full replay components but narrow research subset; price-path proxy is not full candidate outcome validation",
        )
    )

    queue = stable_json(ROOT / "data/_system/scheduled_open_buy_queue.json")
    queue_records = list(queue.get("items") or [])
    queue_stats = summarize_records(
        queue_records, score_keys=("signal_score",), threshold_keys=("signal_threshold",),
        component_keys=("components", "signal_components"), outcome_keys=("pnl_pct",),
    )
    sources.append(
        source_row(
            source_name="SCHEDULED_OPEN_BUY_QUEUE", path="data/_system/scheduled_open_buy_queue.json#items",
            source_kind="ENTRY_DECISION_QUEUE", stage_scope="mixed legacy/live",
            origin_candidate_n=len(queue_stats["candidate_ids"] & origin_ids), historical_entry=True,
            point_in_time=False, same_entry_outcome_link=False, durable=True, duplicate_or_derived=False,
            suitability="RATIO_ONLY_NO_COMPONENTS", note="entry score/threshold retained; no realized component dict or joined outcome",
            **{k: queue_stats[k] for k in (
                "record_n", "unique_candidate_n", "score_threshold_n", "component_dict_n",
                "core_component_n", "top2_possible_n", "outcome_linked_n"
            )},
        )
    )

    central = stable_json(ROOT / "data/_system/central_buy_candidates.json")
    central_records = list((central.get("candidates") or {}).values())
    central_stats = summarize_records(
        central_records, score_keys=("signal_score", "score"), threshold_keys=("signal_threshold",),
        component_keys=("components", "signal_components"), outcome_keys=("pnl_pct",),
    )
    sources.append(
        source_row(
            source_name="CENTRAL_BUY_CANDIDATES", path="data/_system/central_buy_candidates.json#candidates",
            source_kind="ENTRY_DECISION_SNAPSHOT", stage_scope="legacy candidate-only",
            origin_candidate_n=len(central_stats["candidate_ids"] & origin_ids), historical_entry=True,
            point_in_time=False, same_entry_outcome_link=False, durable=True, duplicate_or_derived=False,
            suitability="RATIO_ONLY_NO_COMPONENTS", note="score/threshold retained; no component dict and candidate IDs are not 17,071 origin IDs",
            **{k: central_stats[k] for k in (
                "record_n", "unique_candidate_n", "score_threshold_n", "component_dict_n",
                "core_component_n", "top2_possible_n", "outcome_linked_n"
            )},
        )
    )

    frozen = stable_csv(ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv", low_memory=False)
    frozen_ids = set(frozen["candidate_id"].dropna().astype(str))
    sources.append(
        source_row(
            source_name="FROZEN_OOS_TRADES_93", path="data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv",
            source_kind="OUTCOME_ONLY_TRADE", stage_scope="live93 frozen OOS",
            record_n=len(frozen), unique_candidate_n=frozen["candidate_id"].nunique(),
            origin_candidate_n=len(frozen_ids & origin_ids), score_threshold_n=0, component_dict_n=0,
            core_component_n=0, top2_possible_n=0, outcome_linked_n=len(frozen), historical_entry=True,
            point_in_time=False, same_entry_outcome_link=False, durable=True, duplicate_or_derived=True,
            suitability="OUTCOME_ONLY_NO_ENTRY_SNAPSHOT", note="candidate outcomes/MAE/MFE retained without entry score or realized components",
        )
    )

    live_list = stable_json(ROOT / "data/_system/live_candidate_list_20260707.json")
    live_list_records = list(live_list.get("candidates") or [])
    live_list_stats = summarize_records(
        live_list_records, score_keys=("final_score",), threshold_keys=("threshold",),
        component_keys=("components",), outcome_keys=("pnl_pct",),
    )
    sources.append(
        source_row(
            source_name="LIVE_CANDIDATE_LIST_93", path="data/_system/live_candidate_list_20260707.json#candidates",
            source_kind="STATIC_CANDIDATE_LIST", stage_scope="stage2+stage3",
            origin_candidate_n=len(live_list_stats["candidate_ids"] & origin_ids), historical_entry=False,
            point_in_time=False, same_entry_outcome_link=False, durable=True, duplicate_or_derived=False,
            suitability="NO_DYNAMIC_SIGNAL_FIELDS", note="candidate IDs and static gate metrics only",
            **{k: live_list_stats[k] for k in (
                "record_n", "unique_candidate_n", "score_threshold_n", "component_dict_n",
                "core_component_n", "top2_possible_n", "outcome_linked_n"
            )},
        )
    )

    # 후보별 보조 소스 카운트
    live93_full = set(live93.loc[live93_score & live93_core, "candidate_id"].astype(str))
    dashboard_full = {
        str(r.get("candidate_id")) for r in dashboard_records
        if r.get("candidate_id") and numeric(r.get("final_score")) and numeric(r.get("threshold"))
        and core_dict_complete(r.get("components"))
    }
    slot_ratio = {
        str(r.get("candidate_id")) for r in slot_records
        if r.get("candidate_id") and numeric(r.get("final_score")) and numeric(r.get("threshold"))
    }
    shadow_counts = defaultdict(int)
    strategy_counts = defaultdict(int)
    for row in auxiliary_record_maps["ELITE_SHADOW_CLOSED_TRADES"]:
        if row.get("candidate_id") and numeric(row.get("entry_score")) and numeric(row.get("entry_threshold")):
            shadow_counts[str(row["candidate_id"])] += 1
    for row in auxiliary_record_maps["ELITE_STRATEGY_SIM_TRADES"]:
        if row.get("candidate_id") and numeric(row.get("entry_score")) and numeric(row.get("entry_threshold")):
            strategy_counts[str(row["candidate_id"])] += 1

    candidate_coverage["live93_full_point_snapshot"] = candidate_coverage["candidate_id"].isin(live93_full)
    candidate_coverage["dashboard_full_point_snapshot"] = candidate_coverage["candidate_id"].isin(dashboard_full)
    candidate_coverage["live_slots_ratio_point_snapshot"] = candidate_coverage["candidate_id"].isin(slot_ratio)
    candidate_coverage["shadow_ratio_outcome_rows"] = candidate_coverage["candidate_id"].map(shadow_counts).fillna(0).astype(int)
    candidate_coverage["strategy_ratio_outcome_rows"] = candidate_coverage["candidate_id"].map(strategy_counts).fillna(0).astype(int)
    candidate_coverage["any_full_decomposed_snapshot"] = (
        candidate_coverage["canonical_full_ce_snapshot_rows"].gt(0)
        | candidate_coverage["live93_full_point_snapshot"]
        | candidate_coverage["dashboard_full_point_snapshot"]
    )
    candidate_coverage["historical_full_ce_backtestable"] = candidate_coverage["canonical_full_ce_snapshot_rows"].gt(0)
    candidate_coverage.to_csv(CANDIDATE_OUT, index=False, compression="gzip")

    ce7_result = ce7.merge(candidate_coverage, on=["candidate_id", "stage"], how="left", validate="one_to_one")
    ce7_result["is_dynamic_only_vs_v3"] = ce7_result["candidate_id"].isin(
        {
            "stage3:ANET:fe220620802b", "stage3:BB:f1bdfe7f8ad9",
            "stage3:CDE:ceb9fe0512dc", "stage3:CE:998b0b638c66",
        }
    )
    ce7_result["ce_threshold_backtestable_for_candidate"] = ce7_result["historical_full_ce_backtestable"]
    ce7_result["snapshot_quality_verdict"] = ce7_result.apply(
        lambda r: (
            "HISTORICAL_FULL_ENTRY_SNAPSHOT"
            if r["historical_full_ce_backtestable"]
            else "POINT_SNAPSHOT_PLUS_RATIO_OUTCOME_NO_COMPONENTS"
            if r["live93_full_point_snapshot"] and (r["shadow_ratio_outcome_rows"] + r["strategy_ratio_outcome_rows"] > 0)
            else "POINT_SNAPSHOT_ONLY"
            if r["live93_full_point_snapshot"]
            else "NO_FULL_SNAPSHOT"
        ), axis=1,
    )
    ce7_result.to_csv(CE7_OUT, index=False)

    source_df = pd.DataFrame(sources)
    source_df.to_csv(SOURCE_OUT, index=False)
    quality = source_df[[
        "source_name", "path", "source_kind", "stage_scope", "historical_entry", "point_in_time",
        "same_entry_outcome_link", "durable", "duplicate_or_derived", "ce_ratio_reconstructable",
        "ce_top2_reconstructable", "ce_threshold_backtest_suitability", "note",
    ]].copy()
    quality["quality_class"] = quality.apply(
        lambda r: (
            "FULL_BACKTEST_INPUT" if r.same_entry_outcome_link and r.ce_ratio_reconstructable and r.ce_top2_reconstructable
            else "FULL_POINT_DIAGNOSTIC" if r.point_in_time and r.ce_ratio_reconstructable and r.ce_top2_reconstructable
            else "RATIO_ONLY" if r.ce_ratio_reconstructable and not r.ce_top2_reconstructable
            else "OUTCOME_ONLY" if "OUTCOME" in str(r.source_kind) or "EXIT_TRADE" in str(r.source_kind)
            else "NO_CE_DYNAMIC_DATA"
        ), axis=1,
    )
    quality.to_csv(QUALITY_OUT, index=False)

    stage_summary = {}
    for stage, group in candidate_coverage.groupby("stage"):
        stage_summary[stage] = {
            "origin_candidate_n": len(group),
            "canonical_history_candidate_n": int(group["canonical_any_history"].sum()),
            "historical_full_ce_candidate_n": int(group["historical_full_ce_backtestable"].sum()),
            "historical_full_ce_entry_n": int(group["canonical_full_ce_snapshot_rows"].sum()),
            "any_full_decomposed_snapshot_candidate_n": int(group["any_full_decomposed_snapshot"].sum()),
        }

    ce7_backtestable = int(ce7_result["ce_threshold_backtestable_for_candidate"].sum())
    dynamic_only = ce7_result[ce7_result["is_dynamic_only_vs_v3"]]
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "PARTIAL",
        "origin_count": 17_071,
        "stage_coverage": stage_summary,
        "source_count": len(source_df),
        "ce7": {
            "total": 7,
            "live93_full_point_snapshot_n": int(ce7_result["live93_full_point_snapshot"].sum()),
            "historical_full_ce_backtestable_n": ce7_backtestable,
            "dynamic_only_total": len(dynamic_only),
            "dynamic_only_historical_full_ce_backtestable_n": int(dynamic_only["historical_full_ce_backtestable"].sum()),
            "dynamic_only_ids": dynamic_only["candidate_id"].tolist(),
        },
        "backtestable_scope": {
            "stage2": "YES: canonical trades contain entry score, threshold, realized components and same-entry outcome",
            "stage3": "NO: canonical exit_trades contain outcomes but omit entry score/components",
            "live93": "diagnostic only: one current evaluation per candidate, not repeated entry snapshots",
            "shadow": "ratio/outcome only: no realized component decomposition, so Top2 cannot be computed",
        },
        "threshold_conclusion": "ratio<1.25 and Top2>=90 can be tested on Stage2 historical entries, but cannot be system-wide or CE7/Stage3 validated from existing durable snapshots",
        "arbitrary_threshold_risk": "HIGH_FOR_STAGE3_AND_CE7",
        "observation_only_logging_feasibility": {
            "verdict": "REALISTIC_LOW_COMPLEXITY",
            "reason": "evaluate_signal already returns score/raw_score/threshold/market_adjustment/components; several callers persist score/threshold but omit components",
            "minimum_fields": [
                "timestamp", "candidate_id", "stage", "ticker", "rulebook_hash", "should_buy",
                "score", "raw_score", "threshold", "market_adjustment", "components",
                "market_score", "sector_score", "vix_level", "news_sentiment", "event_flags",
                "decision_id_or_position_id", "later_outcome_join_key",
            ],
            "mode": "append-only observation; no blocking",
            "implementation_performed": False,
        },
        "no_design_change": True,
        "operational_implementation": False,
        "source_rule_mutation": False,
        "live_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    s2 = stage_summary["stage2"]
    s3 = stage_summary["stage3"]
    lines = [
        "# CE 동적 게이트 백테스트 가능성 점검",
        "",
        "- 판정: **PARTIAL**",
        "- 분석 방식: 기존 로그·스냅샷·원본 history read-only",
        "- 설계·구현 변경: 없음",
        "- 운영 구현: `false`",
        "",
        "## 1. 핵심 결론",
        "",
        "CE의 `ratio = final_score / threshold`와 realized core Top2 집중도를 동일 진입 건의 성과와 함께 검증할 수 있는 durable 데이터는 Stage2에만 존재한다.",
        "",
        f"- Stage2: {s2['historical_full_ce_candidate_n']:,}/{s2['origin_candidate_n']:,}개 후보, {s2['historical_full_ce_entry_n']:,}건의 역사적 진입이 full CE 백테스트 가능",
        f"- Stage3: {s3['historical_full_ce_candidate_n']:,}/{s3['origin_candidate_n']:,}개 후보, {s3['historical_full_ce_entry_n']:,}건 — canonical `exit_trades.jsonl`에 entry score/components가 없음",
        f"- CE FAIL 7개: full 현재 스냅샷은 {int(ce7_result['live93_full_point_snapshot'].sum())}/7, 역사적 full 진입 스냅샷은 {ce7_backtestable}/7",
        "",
        "따라서 CE 임계 `ratio<1.25`, `Top2>=90%`를 Stage2 범위에서 retrospective 검증하는 것은 가능하지만, Stage3와 CE 7개에 일반화해 데이터로 확정하는 것은 불가능하다.",
        "",
        "## 2. 주요 소스 커버리지",
        "",
        "| 소스 | 레코드 | 후보 | score+threshold | core 분해 | Top2 가능 | 동일 진입 성과 | 용도 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name in (
        "CANONICAL_STAGE2_HISTORY", "CANONICAL_STAGE3_HISTORY", "LIVE93_THREE_SYMPTOM_SCAN",
        "REAL_DASHBOARD_BUY_CANDIDATES", "LIVE_SLOTS_STATE_CANDIDATE_POOL",
        "ELITE_SHADOW_CLOSED_TRADES", "ELITE_STRATEGY_SIM_TRADES", "DAILY_SIGNAL_REPLAY",
        "SCHEDULED_OPEN_BUY_QUEUE", "CENTRAL_BUY_CANDIDATES", "FROZEN_OOS_TRADES_93",
    ):
        row = source_df[source_df["source_name"].eq(name)].iloc[0]
        lines.append(
            f"| {name} | {int(row.record_n):,} | {int(row.unique_candidate_n):,} | {int(row.score_threshold_n):,} | "
            f"{int(row.core_component_n):,} | {int(row.top2_possible_n):,} | "
            f"{'YES' if row.same_entry_outcome_link and row.ce_top2_reconstructable else 'NO'} | {row.ce_threshold_backtest_suitability} |"
        )
    lines += [
        "",
        "## 3. 스냅샷 품질",
        "",
        "### Stage2 canonical trades",
        "",
        "`entry_signal_score`, `entry_signal_threshold`, `entry_signal_components`, 시장보정·시장/섹터/VIX 컨텍스트와 `pnl_pct`가 동일 trade row에 저장된다. core MA/MACD/RSI/BB/Volume을 분해해 Top2를 재계산할 수 있다.",
        "",
        "### Stage3 canonical exit trades",
        "",
        "성과·진입일·entry rule hash는 있지만 `entry_signal_score`, `entry_signal_threshold`, `entry_signal_components`가 없다. 결과는 있으나 CE 원인을 붙일 수 없다.",
        "",
        "### live93·real dashboard",
        "",
        "현재 시점 score·threshold·component 분해가 있어 CE 상태 계산은 가능하다. 그러나 후보당 한 시점이고 동일 진입 이후 성과가 연결된 반복 표본이 아니므로 임계 검증용 백테스트 데이터가 아니다.",
        "",
        "### elite shadow·strategy sim",
        "",
        "진입 ratio와 결과가 연결되지만 component dict가 저장되지 않아 realized Top2를 계산할 수 없다. ratio 단독 임계 분석만 가능하다.",
        "",
        "### daily signal replay",
        "",
        "full component를 포함하지만 좁은 연구 subset이며 시스템 전체 후보·CE7 커버리지가 없다. 시스템 임계 도출 근거로는 부족하다.",
        "",
        "## 4. CE 7개",
        "",
        "| 후보 | live93 full 현재값 | 역사적 full CE 진입 | shadow ratio+성과 | 판정 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in ce7_result.sort_values("candidate_id").itertuples(index=False):
        shadow_total = int(row.shadow_ratio_outcome_rows + row.strategy_ratio_outcome_rows)
        lines.append(
            f"| {row.candidate_id} | {'YES' if row.live93_full_point_snapshot else 'NO'} | "
            f"{int(row.canonical_full_ce_snapshot_rows):,} | {shadow_total:,} | {row.snapshot_quality_verdict} |"
        )
    lines += [
        "",
        "v3 동적 전용 ANET·BB·CDE·CE도 역사적 full CE 진입 스냅샷은 0건이다. 일부는 shadow ratio+성과가 있지만 component가 없어 Top2 검증은 불가능하다.",
        "",
        "## 5. 최종 판정",
        "",
        "**PARTIAL**",
        "",
        "- Stage2 historical entry 범위: `BACKTESTABLE`",
        "- Stage3 및 CE7 임계 검증: `INSUFFICIENT_SNAPSHOT`",
        "- 시스템 전체 CE 임계 확정: 불가",
        "",
        "현재 자료만으로 `1.25`와 `90%`를 시스템 전체에 확정하면 Stage3에서는 임의 임계 의존 위험이 크다. Stage2에서 후보 임계 조합을 탐색할 수는 있지만 Stage3 external validation 없이 운영 BLOCK 근거로 일반화하면 안 된다.",
        "",
        "## 6. 관측 로깅 대안 평가",
        "",
        "관측 전용 로깅은 현실적이며 난이도가 낮다. `evaluate_signal`은 이미 score·raw_score·threshold·market_adjustment·components를 반환하고, shadow/queue 계층은 score와 threshold를 이미 저장한다. component와 context, outcome join key를 append-only로 추가하면 된다.",
        "",
        "권장 방식은 차단 없는 observation-only 축적이다. 구현은 이번 작업에서 수행하지 않았다.",
        "",
        "## 7. 산출물",
        "",
        f"- `{SOURCE_OUT.name}`",
        f"- `{CANDIDATE_OUT.name}`",
        f"- `{CE7_OUT.name}`",
        f"- `{QUALITY_OUT.name}`",
        f"- `{SUMMARY_OUT.name}`",
    ]
    READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
