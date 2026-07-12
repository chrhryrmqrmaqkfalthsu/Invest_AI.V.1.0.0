#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.central.entity_loader import EntityRecord
from engine.central.signal_collector import CacheOnlyDataProvider, SignalCollector
from engine.live.elite_shadow_report import build_elite_shadow_report

OUT = Path(__file__).resolve().parent
BATCH = ROOT / "exp_batch_stage123_2009_20260616_full"
START = pd.Timestamp("2021-01-01")
END = pd.Timestamp("2026-07-02")


def resolve_source(source_file: str) -> Path:
    p = Path(source_file)
    if p.is_absolute():
        return p
    direct = ROOT / p
    if direct.exists():
        return direct
    return BATCH / p


def load_logged(candidate: dict) -> set[str]:
    source = resolve_source(str(candidate.get("source_file") or ""))
    path = source.parent / "rl_replay_trades.jsonl"
    out: set[str] = set()
    if not path.exists():
        return out
    target_hash = str(candidate.get("rulebook_hash") or "")
    with path.open("r", encoding="utf-8", errors="ignore") as fp:
        for line in fp:
            try:
                row = json.loads(line)
            except Exception:
                continue
            hashes = {
                str(row.get("rulebook_hash") or ""),
                str(row.get("final_rulebook_hash") or ""),
            }
            if target_hash not in hashes:
                continue
            date_text = str(row.get("entry_signal_date") or "")[:10]
            if START.strftime("%Y-%m-%d") <= date_text <= END.strftime("%Y-%m-%d"):
                out.add(date_text)
    return out


def main() -> int:
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    candidates = list(report.get("candidates") or [])
    entities = [
        EntityRecord(
            entity_id=str(c["candidate_id"]),
            ticker=str(c["ticker"]),
            rulebook=dict(c["rulebook"]),
            rulebook_hash=str(c["rulebook_hash"]),
            validation_metrics={},
            validation_periods=[],
            tags={},
            confidence=float(c.get("fitness") or 0.0),
            source_path=str(c.get("source_file") or ""),
        )
        for c in candidates
    ]
    provider = CacheOnlyDataProvider(
        cache_roots=[
            ROOT / "data/_system/research",
            ROOT / "exp_batch_stage123_2009_20260616_full",
        ]
    )
    collector = SignalCollector(provider, use_llm_events=False)

    replay: dict[str, set[str]] = defaultdict(set)
    entity_days: dict[str, int] = {}
    for entity in entities:
        df = provider.load_price_df(entity.ticker)
        if df is None or df.empty:
            entity_days[entity.entity_id] = 0
            continue
        days = [pd.Timestamp(x).normalize() for x in df.index if START <= pd.Timestamp(x).normalize() <= END]
        entity_days[entity.entity_id] = len(days)
        for day in days:
            snapshot = collector.signal_for_date(entity, day)
            if snapshot is not None and bool(snapshot.should_buy):
                replay[entity.entity_id].add(snapshot.date)

    rows: list[dict] = []
    for candidate, entity in zip(candidates, entities):
        replay_dates = replay[entity.entity_id]
        logged_dates = load_logged(candidate)
        overlap = replay_dates & logged_dates
        rows.append(
            {
                "candidate_id": entity.entity_id,
                "stage": candidate.get("stage"),
                "ticker": entity.ticker,
                "evaluated_sessions": entity_days.get(entity.entity_id, 0),
                "replay_should_buy_count": len(replay_dates),
                "logged_entry_signal_count": len(logged_dates),
                "overlap_count": len(overlap),
                "additional_replay_only_count": len(replay_dates - logged_dates),
                "logged_not_replayed_count": len(logged_dates - replay_dates),
                "replay_to_logged_ratio": (len(replay_dates) / len(logged_dates)) if logged_dates else None,
                "overlap_rate_vs_logged": (len(overlap) / len(logged_dates)) if logged_dates else None,
                "overlap_rate_vs_replay": (len(overlap) / len(replay_dates)) if replay_dates else None,
            }
        )

    fields = list(rows[0].keys())
    with (OUT / "sample_delta_estimate.csv").open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    totals = {
        "entity_count": len(rows),
        "evaluated_sessions": sum(int(r["evaluated_sessions"]) for r in rows),
        "replay_should_buy_count": sum(int(r["replay_should_buy_count"]) for r in rows),
        "logged_entry_signal_count": sum(int(r["logged_entry_signal_count"]) for r in rows),
        "overlap_count": sum(int(r["overlap_count"]) for r in rows),
        "additional_replay_only_count": sum(int(r["additional_replay_only_count"]) for r in rows),
        "logged_not_replayed_count": sum(int(r["logged_not_replayed_count"]) for r in rows),
    }
    totals["replay_to_logged_ratio"] = (
        totals["replay_should_buy_count"] / totals["logged_entry_signal_count"]
        if totals["logged_entry_signal_count"]
        else None
    )
    totals["overlap_rate_vs_logged"] = (
        totals["overlap_count"] / totals["logged_entry_signal_count"]
        if totals["logged_entry_signal_count"]
        else None
    )
    totals["overlap_rate_vs_replay"] = (
        totals["overlap_count"] / totals["replay_should_buy_count"]
        if totals["replay_should_buy_count"]
        else None
    )
    (OUT / "sample_delta_summary.json").write_text(
        json.dumps(totals, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(totals, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
