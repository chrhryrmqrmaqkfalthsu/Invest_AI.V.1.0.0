#!/usr/bin/env python3
"""
EVT-2C4-DRY: market_history_v2 append-only 집계 dry-run

- 실제 data/_system/market_history_v2.csv는 쓰지 않는다.
- 기존 CSV의 append_from 이전 라인은 원문 그대로 복사한다.
- append_from 이후 row만 /tmp 등 임시 출력에 append한다.
- GPT 호출은 절대 하지 않는다. llm_news_cache hit만 반영하고, 신규 GPT 필요량만 집계한다.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from datetime import datetime, date
from pathlib import Path

import pandas as pd

sys.path.insert(0, ".")

from engine.market.av_adapter import av_item_to_article
from engine.market.colab_v32 import aggregate_events
from scripts.build_market_history_v2 import daily_av_aggregate
from scripts.news_downloader.dry_run_event_candidate_reduction import (
    VARIANTS,
    EVENT_FOCUS_DIR,
    DAILY_DIR,
    candidates_for_feed,
    cache_has_interpretation,
    event_counts,
    event_set,
    load_cache,
    load_feed_for_day,
    read_dates,
    reduce_candidates,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXISTING_CSV = ROOT / "data/_system/market_history_v2.csv"
DEFAULT_OUT = Path("/tmp/market_history_v2_dryrun.csv")
DEFAULT_APPEND_FROM = date(2025, 6, 1)
DEFAULT_END_DATE = date(2026, 6, 5)
DEFAULT_CAPPED_LIST = ROOT / "data/_system/research/evt2b_20260607/evt2b_capped_1000_dates.txt"
DEFAULT_CACHE_FILE = ROOT / "data/_system/llm_news_cache.json"
DEFAULT_REPORT_DIR = ROOT / "data/_system/research/evt2c4dry_20260607"

EVENT_TO_FLAG = {
    "전쟁": "has_war",
    "금리정책_인상": "has_rate_hike",
    "금리정책_인하": "has_rate_cut",
    "지정학_긴장": "has_geopolitical",
    "관세": "has_tariff",
    "수출규제": "has_export_ban",
    "실적쇼크": "has_earnings_shock",
    "유가급등": "has_oil_surge",
    "은행위기": "has_banking_crisis",
    "인플레이션": "has_inflation",
    "연준발언": "has_fed_statement",
}
FLAG_COLS = [
    "has_war",
    "has_rate_hike",
    "has_rate_cut",
    "has_rate_event",
    "has_geopolitical",
    "has_tariff",
    "has_export_ban",
    "has_earnings_shock",
    "has_oil_surge",
    "has_banking_crisis",
    "has_inflation",
    "has_fed_statement",
]


def parse_ymd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def available_daily_dates() -> list[date]:
    dates = []
    for p in sorted(DAILY_DIR.glob("av_market_*.json")):
        stem = p.stem.replace("av_market_", "")
        if len(stem) != 8:
            continue
        try:
            dates.append(datetime.strptime(stem, "%Y%m%d").date())
        except ValueError:
            pass
    return sorted(set(dates))


def exact_prefix_lines(existing_csv: Path, append_from: date) -> tuple[list[str], list[str], list[str]]:
    """기존 CSV에서 append_from 이전 라인을 원문 그대로 분리한다."""
    lines = existing_csv.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines:
        raise ValueError("existing CSV is empty")
    header = lines[0]
    kept = [header]
    skipped = []
    for line in lines[1:]:
        if not line.strip():
            continue
        first = line.split(",", 1)[0].strip().strip('"')
        try:
            d = parse_ymd(first)
        except Exception:
            skipped.append(line)
            continue
        if d < append_from:
            kept.append(line)
        else:
            skipped.append(line)
    return lines, kept, skipped


def load_feed_for_append_day(day: date, capped_dates: set[date]) -> tuple[list[dict], str, int]:
    if day in capped_dates:
        feed, used_files = load_feed_for_day(day, VARIANTS)
        return feed, "daily_plus_event_focus", len(used_files)
    feed, used_files = load_feed_for_day(day, [])
    return feed, "daily_only", len(used_files)


def make_interpreted_from_cache(selected_candidates: list[dict], cache: dict) -> tuple[list[dict], int, int]:
    interpreted = []
    cache_hits = 0
    cache_misses = 0
    for cand in selected_candidates:
        article = cand["article"]
        url = article.get("url", "")
        cache_key = url if url else article.get("title", "")[:100]
        cached = cache.get(cache_key)
        interp = cached.get("interpretation") if isinstance(cached, dict) else None
        if interp:
            cache_hits += 1
            interpreted.append({
                "article": article,
                "matched_event_types": cand.get("matched_event_types", []),
                "interpretation": interp,
            })
        else:
            cache_misses += 1
    return interpreted, cache_hits, cache_misses


def predicted_flags_from_events(events: set[str]) -> dict[str, int]:
    flags = {col: 0 for col in FLAG_COLS}
    for event, flag in EVENT_TO_FLAG.items():
        if event in events:
            flags[flag] = 1
    flags["has_rate_event"] = int(flags["has_rate_hike"] or flags["has_rate_cut"])
    return flags


def build_cache_only_row(
    day: date,
    feed: list[dict],
    cache: dict,
    max_new_per_event: int,
    max_new_per_day: int,
    max_cached_per_event: int,
) -> tuple[dict, dict]:
    articles = [av_item_to_article(item) for item in feed]
    av_stats = daily_av_aggregate(articles)
    raw_candidates, neg_filtered, deduped_count = candidates_for_feed(feed)
    reduced = reduce_candidates(
        raw_candidates,
        cache,
        max_new_per_event=max_new_per_event,
        max_new_per_day=max_new_per_day,
        max_cached_per_event=max_cached_per_event,
    )
    interpreted, cache_hits, cache_misses = make_interpreted_from_cache(reduced.selected, cache)
    active_events, event_adj, rejected, conflicts, conflict_penalty = aggregate_events(interpreted, verbose=False)

    row = {
        "date": day.strftime("%Y-%m-%d"),
        **av_stats,
        "candidates_count": len(raw_candidates),
        "negation_filtered": len(neg_filtered),
        "gpt_interpreted": len(interpreted),
        "gpt_calls_new": 0,
        "active_events_count": len(active_events),
        "event_adjustment": event_adj,
        "conflicts_count": len(conflicts),
        "conflict_penalty": conflict_penalty,
        "sanity_corrections": len(rejected),
        "active_events": ",".join(active_events.keys()),
        "has_war": int("전쟁" in active_events),
        "has_rate_hike": int("금리정책_인상" in active_events),
        "has_rate_cut": int("금리정책_인하" in active_events),
        "has_rate_event": int(("금리정책_인상" in active_events) or ("금리정책_인하" in active_events)),
        "has_geopolitical": int("지정학_긴장" in active_events),
        "has_tariff": int("관세" in active_events),
        "has_export_ban": int("수출규제" in active_events),
        "has_earnings_shock": int("실적쇼크" in active_events),
        "has_oil_surge": int("유가급등" in active_events),
        "has_banking_crisis": int("은행위기" in active_events),
        "has_inflation": int("인플레이션" in active_events),
        "has_fed_statement": int("연준발언" in active_events),
    }
    raw_events = event_set(raw_candidates)
    reduced_events = event_set(reduced.selected)
    diag = {
        "date": day.strftime("%Y-%m-%d"),
        "feed_unique": len(feed),
        "deduped_articles": deduped_count,
        "raw_candidates": len(raw_candidates),
        "reduced_candidates": len(reduced.selected),
        "raw_events": sorted(raw_events),
        "reduced_events": sorted(reduced_events),
        "raw_event_counts": dict(event_counts(raw_candidates)),
        "reduced_event_counts": dict(event_counts(reduced.selected)),
        "predicted_flags": predicted_flags_from_events(reduced_events),
        "cache_hits_selected": cache_hits,
        "cache_misses_selected": cache_misses,
        "estimated_new_gpt_calls": reduced.new_selected,
        "cache_only_active_events": sorted(active_events.keys()),
        "cache_only_has_any": int(any(row.get(c, 0) for c in FLAG_COLS if c != "has_rate_event")),
        "cache_only_event_adjustment": event_adj,
    }
    return row, diag


def csv_lines_for_rows(rows: list[dict], header_cols: list[str]) -> list[str]:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header_cols, lineterminator="\n", extrasaction="ignore")
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in header_cols})
    return buf.getvalue().splitlines(keepends=True)


def hash_lines(lines: list[str]) -> str:
    import hashlib
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="EVT-2C4 append-only market_history_v2 dry-run")
    parser.add_argument("--existing-csv", type=Path, default=DEFAULT_EXISTING_CSV)
    parser.add_argument("--append-from", type=parse_ymd, default=DEFAULT_APPEND_FROM)
    parser.add_argument("--end-date", type=parse_ymd, default=DEFAULT_END_DATE)
    parser.add_argument("--capped-list", type=Path, default=DEFAULT_CAPPED_LIST)
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--max-new-per-event", type=int, default=3)
    parser.add_argument("--max-new-per-day", type=int, default=40)
    parser.add_argument("--max-cached-per-event", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="필수 안전 플래그")
    args = parser.parse_args()

    if not args.dry_run:
        raise SystemExit("❌ EVT-2C4-DRY는 --dry-run 필수입니다. 실제 CSV 쓰기 금지.")
    if args.out.resolve() == args.existing_csv.resolve():
        raise SystemExit("❌ --out은 existing market_history_v2.csv와 같을 수 없습니다")
    if args.end_date < args.append_from:
        raise SystemExit("❌ --end-date must be >= --append-from")

    existing_df = pd.read_csv(args.existing_csv)
    header_cols = list(existing_df.columns)
    existing_lines, kept_lines, skipped_existing_lines = exact_prefix_lines(args.existing_csv, args.append_from)
    capped_dates = set(read_dates(args.capped_list))
    cache = load_cache(args.cache_file)
    all_daily_dates = available_daily_dates()
    append_dates = [d for d in all_daily_dates if args.append_from <= d <= args.end_date]

    if not append_dates:
        raise SystemExit("❌ append 대상 날짜가 없습니다")

    rows = []
    diagnostics = []
    source_modes = Counter()
    total_estimated_gpt = 0
    started = datetime.now()
    for d in append_dates:
        feed, source_mode, used_files = load_feed_for_append_day(d, capped_dates)
        row, diag = build_cache_only_row(
            d,
            feed,
            cache,
            args.max_new_per_event,
            args.max_new_per_day,
            args.max_cached_per_event,
        )
        row = {col: row.get(col, "") for col in header_cols}
        rows.append(row)
        diag["source_mode"] = source_mode
        diag["used_files"] = used_files
        diagnostics.append(diag)
        source_modes[source_mode] += 1
        total_estimated_gpt += diag["estimated_new_gpt_calls"]

    new_lines = csv_lines_for_rows(rows, header_cols)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(kept_lines + new_lines), encoding="utf-8")

    # prefix exact verification: output의 기존 구간 lines가 kept_lines와 같은지 확인
    out_lines = args.out.read_text(encoding="utf-8").splitlines(keepends=True)
    prefix_same = out_lines[:len(kept_lines)] == kept_lines
    diff_lines = []
    if not prefix_same:
        for i, (a, b) in enumerate(zip(kept_lines, out_lines[:len(kept_lines)]), start=1):
            if a != b:
                diff_lines.append(f"line {i}\nOLD:{a}NEW:{b}")
        if len(out_lines[:len(kept_lines)]) != len(kept_lines):
            diff_lines.append("prefix length mismatch")

    out_df = pd.read_csv(args.out)
    new_df = out_df[pd.to_datetime(out_df["date"]) >= pd.Timestamp(args.append_from)]
    old_df = out_df[pd.to_datetime(out_df["date"]) < pd.Timestamp(args.append_from)]

    predicted_any_event_days = sum(1 for d in diagnostics if any(v for k, v in d["predicted_flags"].items() if k != "has_rate_event"))
    cache_only_any_event_days = sum(1 for d in diagnostics if d["cache_only_has_any"])
    predicted_flag_counts = Counter()
    for d in diagnostics:
        for k, v in d["predicted_flags"].items():
            predicted_flag_counts[k] += int(v)

    args.report_dir.mkdir(parents=True, exist_ok=True)
    diff_path = args.report_dir / "evt2c4_pre0531_diff.txt"
    preview_path = args.report_dir / "evt2c4_new_rows_preview.txt"
    diag_path = args.report_dir / "evt2c4_diagnostics.json"
    summary_path = args.report_dir / "evt2c4_summary.json"
    diff_path.write_text("".join(diff_lines), encoding="utf-8")

    preview_cols = [
        "date", "news_count", "candidates_count", "gpt_interpreted", "gpt_calls_new",
        "active_events_count", "event_adjustment", *FLAG_COLS,
    ]
    preview_text = []
    preview_text.append("# dry-run output rows preview (cache-only same-schema CSV values)\n")
    preview_text.append(new_df[preview_cols].head(12).to_string(index=False))
    preview_text.append("\n\n# sample diagnostics with predicted keyword-category flags\n")
    for sample_date in ["2025-10-14", "2026-02-27", "2026-06-05"]:
        match = next((d for d in diagnostics if d["date"] == sample_date), None)
        if match:
            preview_text.append(f"\n[{sample_date}] source_mode={match['source_mode']} raw={match['raw_candidates']} reduced={match['reduced_candidates']} est_new_gpt={match['estimated_new_gpt_calls']}\n")
            preview_text.append(f"raw_events={match['raw_event_counts']}\n")
            preview_text.append(f"reduced_events={match['reduced_event_counts']}\n")
            preview_text.append(f"predicted_flags={match['predicted_flags']}\n")
            preview_text.append(f"cache_only_active_events={match['cache_only_active_events']} cache_only_event_adjustment={match['cache_only_event_adjustment']}\n")
    preview_path.write_text("".join(preview_text), encoding="utf-8")

    diag_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "started_at": started.isoformat(),
        "existing_csv": str(args.existing_csv),
        "out": str(args.out),
        "append_from": args.append_from.isoformat(),
        "end_date": args.end_date.isoformat(),
        "existing_rows": len(existing_df),
        "kept_existing_lines_including_header": len(kept_lines),
        "skipped_existing_lines_at_or_after_append_from": len(skipped_existing_lines),
        "append_rows": len(rows),
        "output_rows": len(out_df),
        "old_rows_output": len(old_df),
        "new_rows_output": len(new_df),
        "new_first_date": str(new_df["date"].min()) if len(new_df) else None,
        "new_last_date": str(new_df["date"].max()) if len(new_df) else None,
        "prefix_same_as_existing_pre_append": prefix_same,
        "pre_append_hash_existing_prefix": hash_lines(kept_lines),
        "pre_append_hash_output_prefix": hash_lines(out_lines[:len(kept_lines)]),
        "diff_lines": len(diff_lines),
        "source_modes": dict(source_modes),
        "cache_entries": len(cache),
        "estimated_new_gpt_calls": total_estimated_gpt,
        "cache_only_any_event_days": cache_only_any_event_days,
        "predicted_any_event_days": predicted_any_event_days,
        "predicted_flag_counts": dict(predicted_flag_counts),
        "max_new_per_event": args.max_new_per_event,
        "max_new_per_day": args.max_new_per_day,
        "max_cached_per_event": args.max_cached_per_event,
        "dry_run_note": "CSV new rows are cache-only same-schema rows. predicted_flags diagnostics show keyword-category coverage without GPT calls.",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== EVT-2C4 append-only dry-run ===")
    print(f"existing_csv={args.existing_csv}")
    print(f"out={args.out}")
    print(f"append_from={args.append_from} end_date={args.end_date}")
    print(f"existing_rows={len(existing_df)}")
    print(f"append_rows={len(rows)}")
    print(f"output_rows={len(out_df)}")
    print(f"old_rows_output={len(old_df)}")
    print(f"new_rows_output={len(new_df)}")
    print(f"new_first_date={new_df['date'].min() if len(new_df) else 'NONE'}")
    print(f"new_last_date={new_df['date'].max() if len(new_df) else 'NONE'}")
    print(f"prefix_same_as_existing_pre_append={prefix_same}")
    print(f"pre_append_hash_existing_prefix={summary['pre_append_hash_existing_prefix']}")
    print(f"pre_append_hash_output_prefix={summary['pre_append_hash_output_prefix']}")
    print(f"diff_lines={len(diff_lines)}")
    print(f"source_modes={dict(source_modes)}")
    print(f"cache_entries={len(cache)}")
    print(f"estimated_new_gpt_calls={total_estimated_gpt}")
    print(f"cache_only_any_event_days={cache_only_any_event_days}")
    print(f"predicted_any_event_days={predicted_any_event_days}")
    print("predicted_flag_counts:")
    for k, v in predicted_flag_counts.items():
        print(f"  {k}: {v}")
    print(f"diff_path={diff_path}")
    print(f"preview_path={preview_path}")
    print(f"diagnostics_path={diag_path}")
    print("=== END DRY RUN ===")
    if not prefix_same:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
