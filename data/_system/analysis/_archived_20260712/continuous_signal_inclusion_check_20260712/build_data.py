#!/usr/bin/env python3
from __future__ import annotations

import bisect
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from common import (BACKUP_TAG, LOG_DATASET_PATH, OUT, PRE_HEAD, ROOT, UNIVERSE_PATH,
                    live_hashes, load_current_candidates, load_session_maps,
                    parse_date, process_snapshot, rel, write_csv)


def build_clusters(universe: pd.DataFrame, session_maps: dict[str, dict[pd.Timestamp, int]],
                   log_keys: set[tuple[str, str]]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    clusters: list[dict[str, Any]] = []
    date_to_cluster: dict[tuple[str, str], dict[str, Any]] = {}
    serial = 0
    for (candidate_id, ticker), group in universe.groupby(["candidate_id", "ticker"], sort=True):
        stage = str(group.iloc[0]["stage"])
        positions = session_maps[str(ticker).upper()]
        dates = sorted({pd.Timestamp(v).normalize() for v in pd.to_datetime(group["signal_date"], errors="coerce").dropna()})
        current: list[pd.Timestamp] = []
        last_pos: int | None = None

        def flush() -> None:
            nonlocal serial, current
            if not current:
                return
            serial += 1
            texts = [day.strftime("%Y-%m-%d") for day in current]
            row = {
                "cluster_id": f"C{serial:06d}",
                "candidate_id": str(candidate_id),
                "stage": stage,
                "ticker": str(ticker),
                "cluster_start": texts[0],
                "cluster_end": texts[-1],
                "cluster_length": len(texts),
                "length_bucket": "1일" if len(texts) == 1 else "2일" if len(texts) == 2 else "3일+",
                "log_key_overlap_count": sum((str(candidate_id), day) in log_keys for day in texts),
                "dates": texts,
            }
            clusters.append(row)
            for day in texts:
                date_to_cluster[(str(candidate_id), day)] = row
            current = []

        for day in dates:
            pos = positions.get(day)
            if pos is None:
                flush()
                current = [day]
                last_pos = None
            elif current and last_pos is not None and pos == last_pos + 1:
                current.append(day)
                last_pos = pos
            else:
                flush()
                current = [day]
                last_pos = pos
        flush()
    return clusters, date_to_cluster


def distribution_rows(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scopes: list[tuple[str, str, list[dict[str, Any]]]] = [("ALL", "ALL", clusters)]
    for stage in sorted({str(row["stage"]) for row in clusters}):
        scopes.append(("STAGE", stage, [row for row in clusters if row["stage"] == stage]))
    for candidate_id in sorted({str(row["candidate_id"]) for row in clusters}):
        scopes.append(("ENTITY", candidate_id, [row for row in clusters if row["candidate_id"] == candidate_id]))
    out: list[dict[str, Any]] = []
    for scope_type, scope_value, part in scopes:
        total_clusters = len(part)
        total_signals = sum(int(row["cluster_length"]) for row in part)
        ticker = str(part[0]["ticker"]) if scope_type == "ENTITY" and part else ""
        stage = str(part[0]["stage"]) if scope_type == "ENTITY" and part else (scope_value if scope_type == "STAGE" else "ALL")
        for bucket in ["1일", "2일", "3일+"]:
            selected = [row for row in part if row["length_bucket"] == bucket]
            cluster_count = len(selected)
            signal_count = sum(int(row["cluster_length"]) for row in selected)
            out.append({
                "scope_type": scope_type,
                "scope_value": scope_value,
                "candidate_id": scope_value if scope_type == "ENTITY" else "",
                "stage": stage,
                "ticker": ticker,
                "length_bucket": bucket,
                "cluster_count": cluster_count,
                "signal_count": signal_count,
                "cluster_share": cluster_count / total_clusters if total_clusters else 0.0,
                "signal_share": signal_count / total_signals if total_signals else 0.0,
                "scope_total_clusters": total_clusters,
                "scope_total_signals": total_signals,
                "scope_max_cluster_length": max((int(row["cluster_length"]) for row in part), default=0),
            })
    return out


def matching_intervals(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = Path(str(candidate.get("source_file") or ""))
    if not source.is_absolute():
        source = ROOT / source
    path = source.parent / "rl_replay_trades.jsonl"
    target = str(candidate.get("rulebook_hash") or "")
    rows: list[dict[str, Any]] = []
    matched = 0
    invalid = 0
    if path.exists():
        with path.open("r", encoding="utf-8", errors="ignore") as fp:
            for line in fp:
                if target not in line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    invalid += 1
                    continue
                hashes = {str(raw.get("rulebook_hash") or ""), str(raw.get("final_rulebook_hash") or "")}
                if target not in hashes:
                    continue
                matched += 1
                entry = parse_date(raw.get("entry_signal_date") or raw.get("entry_date"))
                if entry is None:
                    continue
                rows.append({
                    "entry_signal_date": entry,
                    "entry_fill_date": parse_date(raw.get("entry_fill_date") or raw.get("entry_date")),
                    "exit_date": parse_date(raw.get("exit_date")),
                    "period_label": str(raw.get("period_label") or ""),
                    "period_role": str(raw.get("period_role") or ""),
                })
    dedup: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row["entry_signal_date"], row["entry_fill_date"], row["exit_date"], row["period_label"], row["period_role"])
        dedup[key] = row
    rows = sorted(dedup.values(), key=lambda row: (row["entry_signal_date"], row["exit_date"] or pd.Timestamp.max))
    return rows, {"path": rel(path), "exists": path.exists(), "matched_trade_rows": matched,
                  "dedup_trade_intervals": len(rows), "invalid_json_rows": invalid}


def holding_interval(day: pd.Timestamp, intervals: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in reversed(intervals):
        if row["entry_signal_date"] >= day:
            continue
        if row["exit_date"] is not None and day <= row["exit_date"]:
            return row
    return None


def gap_rows(universe: pd.DataFrame, log_frame: pd.DataFrame,
             intervals_by_candidate: dict[str, list[dict[str, Any]]],
             date_to_cluster: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    log_dates: dict[str, list[pd.Timestamp]] = {}
    log_texts: dict[str, set[str]] = {}
    for candidate_id, group in log_frame.groupby("candidate_id"):
        dates = sorted({pd.Timestamp(v).normalize() for v in pd.to_datetime(group["signal_date"], errors="coerce").dropna()})
        log_dates[str(candidate_id)] = dates
        log_texts[str(candidate_id)] = {day.strftime("%Y-%m-%d") for day in dates}
    out: list[dict[str, Any]] = []
    for _, raw in universe.sort_values(["candidate_id", "signal_date"]).iterrows():
        cid = str(raw["candidate_id"])
        date_text = str(raw["signal_date"])[:10]
        if date_text in log_texts.get(cid, set()):
            continue
        day = pd.Timestamp(date_text)
        dates = log_dates.get(cid, [])
        pos = bisect.bisect_left(dates, day)
        previous = dates[pos - 1] if pos > 0 else None
        following = dates[pos] if pos < len(dates) else None
        between = previous is not None and following is not None and previous < day < following
        hold = holding_interval(day, intervals_by_candidate.get(cid, []))
        cluster = date_to_cluster[(cid, date_text)]
        cdates = list(cluster["dates"])
        same_cluster_logged = set(cdates) & log_texts.get(cid, set())
        continuation = any(d < date_text for d in same_cluster_logged)
        if hold:
            kind = "DURING_LOGGED_HOLD"
        elif continuation:
            kind = "CONTINUATION_AFTER_LOGGED_ENTRY_SAME_CLUSTER"
        elif between:
            kind = "BETWEEN_LOGGED_ENTRIES"
        else:
            kind = "REPLAY_ONLY_OTHER"
        out.append({
            "candidate_id": cid, "stage": str(raw["stage"]), "ticker": str(raw["ticker"]),
            "rulebook_hash": str(raw["rulebook_hash"]), "signal_date": date_text,
            "label_status": str(raw.get("label_status") or ""), "in_log_based_3430": False,
            "previous_logged_entry_signal_date": previous.strftime("%Y-%m-%d") if previous is not None else "",
            "next_logged_entry_signal_date": following.strftime("%Y-%m-%d") if following is not None else "",
            "between_logged_entries": bool(between),
            "calendar_days_from_previous_entry": int((day - previous).days) if previous is not None else "",
            "calendar_days_to_next_entry": int((following - day).days) if following is not None else "",
            "during_logged_holding_interval": bool(hold),
            "holding_entry_signal_date": hold["entry_signal_date"].strftime("%Y-%m-%d") if hold else "",
            "holding_entry_fill_date": hold["entry_fill_date"].strftime("%Y-%m-%d") if hold and hold["entry_fill_date"] is not None else "",
            "holding_exit_date": hold["exit_date"].strftime("%Y-%m-%d") if hold and hold["exit_date"] is not None else "",
            "holding_period_label": hold["period_label"] if hold else "",
            "cluster_id": cluster["cluster_id"], "cluster_start": cluster["cluster_start"],
            "cluster_end": cluster["cluster_end"], "cluster_length": cluster["cluster_length"],
            "cluster_contains_logged_entry_key": bool(same_cluster_logged),
            "continuation_after_logged_entry_same_cluster": bool(continuation), "inclusion_class": kind,
        })
    return out


def entity_rows(candidates: list[dict[str, Any]], meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        cid = str(candidate.get("candidate_id") or "")
        stage = str(candidate.get("stage") or "")
        source = str(candidate.get("source_file") or "")
        if stage == "stage2":
            phase = "Stage2 3개 train split GA + 5기간 gate"
            runner = "scripts/research/run_stage2.py"
            evidence = "train_one_split evaluate_fn 458-470; run_ga 505-516"
            role = "survivors.jsonl: GA rulebook에서 파생된 gate 생존 개체"
        else:
            phase = "Stage3 qualify GA + entry GA(train_3) + exit-gene GA(stress+bull)"
            runner = "scripts/research/run_stage3_aggressive.py 및 원본 backup 모듈"
            evidence = "run_backtest_period 324-336; entry 597-615; exit 752-777"
            role = "final_rulebooks.jsonl: entry GA 개체에 exit-gene GA를 적용한 최종 개체"
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = ROOT / source_path
        m = meta[cid]
        out.append({
            "candidate_id": cid, "stage": stage, "ticker": str(candidate.get("ticker") or ""),
            "rulebook_hash": str(candidate.get("rulebook_hash") or ""), "source_file": source,
            "source_file_exists": source_path.exists(), "source_artifact_role": role,
            "origin_runner": runner, "training_phase": phase,
            "fitness_input": "run_backtest_execution_mode의 executed trades 기반 fitness/expectancy/PF/MDD",
            "sample_definition": "flat 상태에서 날짜별 should_buy 평가; 진입 후 청산+cooldown 다음 인덱스로 점프",
            "all_should_buy_signal_days_independently_scored": False,
            "flat_state_should_buy_handling": "should_buy=True이면 거래 생성 후 결과를 fitness에 반영",
            "holding_state_should_buy_handling": "SKIPPED_NOT_EVALUATED",
            "cooldown_handling": "청산일 뒤 cooldown_days=1 추가 스킵",
            "persisted_training_sample": "체결 거래만 trades/rl_replay_trades 저장; 보유중·cooldown should_buy 원 표본 미저장",
            "raw_all_signal_sample_stored": False, "code_verifiable": True,
            "common_backtest_evidence": "engine/learning/execution_mode_backtest.py 234-267, 291-340; 특히 337-340 index jump",
            "runner_evidence": evidence, "matched_rl_replay_path": m["path"],
            "matched_rl_replay_trade_rows": m["matched_trade_rows"],
            "rl_replay_invalid_json_rows": m["invalid_json_rows"], "verdict": "HAS_GAP",
            "verdict_reason": "보유중 연속 should_buy를 독립 fitness 표본으로 보지 않음",
        })
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    baseline = {"live_hashes": live_hashes(), "daemon": process_snapshot()}
    universe = pd.read_csv(UNIVERSE_PATH, dtype=str, keep_default_na=False)
    log_frame = pd.read_csv(LOG_DATASET_PATH, dtype=str, keep_default_na=False)
    candidates = load_current_candidates()
    candidate_ids = {str(row.get("candidate_id") or "") for row in candidates}
    missing = sorted(set(universe["candidate_id"]) - candidate_ids)
    if missing:
        raise RuntimeError(f"candidate mapping missing: {missing}")
    universe_keys = set(map(tuple, universe[["candidate_id", "signal_date"]].itertuples(index=False, name=None)))
    log_keys = set(map(tuple, log_frame[["candidate_id", "signal_date"]].itertuples(index=False, name=None)))
    clusters, date_to_cluster = build_clusters(universe, load_session_maps(candidates), log_keys)
    write_csv(OUT / "cluster_distribution.csv", distribution_rows(clusters))
    intervals: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        cid = str(candidate.get("candidate_id") or "")
        intervals[cid], meta[cid] = matching_intervals(candidate)
    gaps = gap_rows(universe, log_frame, intervals, date_to_cluster)
    gap_fields = ["candidate_id", "stage", "ticker", "rulebook_hash", "signal_date", "label_status",
                  "in_log_based_3430", "previous_logged_entry_signal_date", "next_logged_entry_signal_date",
                  "between_logged_entries", "calendar_days_from_previous_entry", "calendar_days_to_next_entry",
                  "during_logged_holding_interval", "holding_entry_signal_date", "holding_entry_fill_date",
                  "holding_exit_date", "holding_period_label", "cluster_id", "cluster_start", "cluster_end",
                  "cluster_length", "cluster_contains_logged_entry_key", "continuation_after_logged_entry_same_cluster",
                  "inclusion_class"]
    write_csv(OUT / "gap_day_signal_inclusion.csv", gaps, gap_fields)
    entities = entity_rows(candidates, meta)
    write_csv(OUT / "entity_training_sample_definition.csv", entities)
    bucket_clusters = Counter(str(row["length_bucket"]) for row in clusters)
    bucket_signals: Counter[str] = Counter()
    for row in clusters:
        bucket_signals[str(row["length_bucket"])] += int(row["cluster_length"])
    stage_overlap: dict[str, dict[str, int]] = {}
    for stage in sorted(set(universe["stage"]) | set(log_frame["stage"])):
        uk = set(map(tuple, universe.loc[universe["stage"] == stage, ["candidate_id", "signal_date"]].itertuples(index=False, name=None)))
        lk = set(map(tuple, log_frame.loc[log_frame["stage"] == stage, ["candidate_id", "signal_date"]].itertuples(index=False, name=None)))
        stage_overlap[str(stage)] = {"replay": len(uk), "log": len(lk), "intersection": len(uk & lk),
                                     "replay_only": len(uk - lk), "log_only": len(lk - uk)}
    state = {
        "baseline": baseline, "backup": {"pre_head": PRE_HEAD, "tag": BACKUP_TAG},
        "input": {"replay_rows": len(universe), "log_rows": len(log_frame), "candidate_count": len(candidates),
                  "stage2_candidates": sum(row["stage"] == "stage2" for row in entities),
                  "stage3_candidates": sum(row["stage"] == "stage3" for row in entities)},
        "set_comparison": {"intersection": len(universe_keys & log_keys), "replay_only": len(universe_keys - log_keys),
                           "log_only": len(log_keys - universe_keys), "naive_row_delta": len(universe)-len(log_frame),
                           "strict_superset": len(log_keys-universe_keys) == 0, "by_stage": stage_overlap},
        "clusters": {"cluster_count": len(clusters), "bucket_cluster_count": dict(bucket_clusters),
                     "bucket_signal_count": dict(bucket_signals),
                     "multi_day_cluster_count": sum(int(row["cluster_length"]) >= 2 for row in clusters),
                     "multi_day_signal_count": sum(int(row["cluster_length"]) for row in clusters if int(row["cluster_length"]) >= 2),
                     "max_cluster_length": max(int(row["cluster_length"]) for row in clusters),
                     "longest_examples": [{k: row[k] for k in ["candidate_id", "ticker", "stage", "cluster_start", "cluster_end", "cluster_length"]}
                                          for row in sorted(clusters, key=lambda x: int(x["cluster_length"]), reverse=True)[:10]]},
        "gap_inclusion": {"replay_only_rows": len(gaps),
                          "during_logged_holding_interval": sum(bool(row["during_logged_holding_interval"]) for row in gaps),
                          "between_logged_entries": sum(bool(row["between_logged_entries"]) for row in gaps),
                          "continuation_after_logged_entry_same_cluster": sum(bool(row["continuation_after_logged_entry_same_cluster"]) for row in gaps),
                          "union_clear_gap_evidence": sum(bool(row["during_logged_holding_interval"]) or bool(row["between_logged_entries"]) or bool(row["continuation_after_logged_entry_same_cluster"]) for row in gaps),
                          "class_counts": dict(Counter(str(row["inclusion_class"]) for row in gaps))},
        "trade_meta": meta,
    }
    (OUT / "analysis_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"rows": len(universe), "clusters": len(clusters), "gaps": len(gaps),
                      "during_hold": state["gap_inclusion"]["during_logged_holding_interval"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
