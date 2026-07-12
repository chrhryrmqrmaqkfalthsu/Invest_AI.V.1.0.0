#!/usr/bin/env python3
"""build_data.py의 Stage2 배치 상대경로를 보정한 재현 실행 진입점."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import build_data as base
from common import ROOT, parse_date, rel

BATCH_ROOT = ROOT / "exp_batch_stage123_2009_20260616_full"


def resolve_source(candidate: dict[str, Any]) -> Path:
    source = Path(str(candidate.get("source_file") or ""))
    if source.is_absolute():
        return source
    direct = ROOT / source
    batched = BATCH_ROOT / source
    if direct.exists():
        return direct
    if batched.exists():
        return batched
    return direct


def matching_intervals(candidate: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = resolve_source(candidate)
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
    return rows, {
        "path": rel(path),
        "exists": path.exists(),
        "matched_trade_rows": matched,
        "dedup_trade_intervals": len(rows),
        "invalid_json_rows": invalid,
    }


def entity_rows(candidates: list[dict[str, Any]], meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = base.entity_rows_original(candidates, meta)
    by_id = {str(candidate.get("candidate_id") or ""): candidate for candidate in candidates}
    for row in rows:
        row["source_file_exists"] = resolve_source(by_id[row["candidate_id"]]).exists()
    return rows


base.matching_intervals = matching_intervals
base.entity_rows_original = base.entity_rows
base.entity_rows = entity_rows

if __name__ == "__main__":
    raise SystemExit(base.main())
