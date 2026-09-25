#!/usr/bin/env python3
"""
EVT-2C4-RUN: market_history_v2 append-only 실제 집계 runner

안전 원칙:
- 원본 CSV에 직접 쓰지 않는다. --out 임시 파일에 먼저 쓴다.
- append_from 이전 라인은 기존 CSV 원문 그대로 복사한다.
- GPT 성공 1건마다 llm_news_cache.json에 즉시 저장한다.
- 중단/재개 시 cache hit는 재호출하지 않는다.
- prefix 검증이 실패하면 non-zero exit.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
from collections import Counter
from datetime import datetime, date
from pathlib import Path
from statistics import mean

import pandas as pd
from openai import OpenAI

from engine.market.av_adapter import av_item_to_article
from engine.market.colab_v32 import CONFIG, aggregate_events
from scripts.build_market_history_v2 import daily_av_aggregate
from scripts.news_downloader.dry_run_append_market_history_v2 import (
    DEFAULT_CAPPED_LIST,
    DEFAULT_EXISTING_CSV,
    EVENT_TO_FLAG,
    FLAG_COLS,
    available_daily_dates,
    exact_prefix_lines,
    hash_lines,
    load_feed_for_append_day,
    parse_ymd,
)
from scripts.news_downloader.dry_run_event_candidate_reduction import (
    VARIANTS,
    article_key,
    article_hour_bucket,
    candidates_for_feed,
    event_counts,
    event_set,
    is_trusted,
    load_cache,
    normalize_title,
    read_dates,
    source_name,
)
from scripts.news_downloader.sample_gpt_event_cost import build_messages

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = Path("/tmp/market_history_v2_run.csv")
DEFAULT_APPEND_FROM = date(2025, 6, 1)
DEFAULT_END_DATE = date(2026, 6, 5)
DEFAULT_CACHE_FILE = ROOT / CONFIG["LLM_CACHE_FILE"]
DEFAULT_REPORT_DIR = ROOT / "data/_system/research/evt2c4run_20260607"


def cache_key_for_article(article: dict) -> str:
    url = article.get("url", "")
    return url if url else article.get("title", "")[:100]


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def cache_has_interpretation(cache: dict, article: dict) -> bool:
    item = cache.get(cache_key_for_article(article))
    return isinstance(item, dict) and bool(item.get("interpretation"))


def candidate_score(cand: dict, cache: dict, source_counts: Counter, bucket_counts: Counter, title_cluster_counts: Counter) -> float:
    art = cand["article"]
    score = 0.0
    if cache_has_interpretation(cache, art):
        score += 3.0
    if is_trusted(art):
        score += 3.0
    score += min(len(cand.get("matched_event_types", [])), 3) * 1.0
    score += min(len(str(art.get("description", "") or "")) / 350.0, 1.5)
    src = source_name(art).lower()
    bucket = article_hour_bucket(art)
    cluster = normalize_title(str(art.get("title", "") or ""))[:80]
    if source_counts[src] == 0:
        score += 1.0
    elif source_counts[src] == 1:
        score += 0.3
    else:
        score -= 1.0 * (source_counts[src] - 1)
    if bucket_counts[bucket] == 0:
        score += 0.8
    if title_cluster_counts[cluster] > 0:
        score -= 2.0 * title_cluster_counts[cluster]
    return score


def reduce_candidates_stable(candidates: list[dict], cache: dict, max_per_event: int, max_per_day: int) -> list[dict]:
    """캐시 여부와 무관하게 event_type별 총 대표 후보 수를 고정한다.

    재실행 시 cache hit가 늘어나도 새 후보가 계속 추가 선택되는 일을 막기 위한 C4-RUN 전용 축약.
    """
    selected: list[dict] = []
    selected_keys: set[str] = set()
    per_event = Counter()
    source_counts = Counter()
    bucket_counts = Counter()
    title_cluster_counts = Counter()
    raw_events = sorted(event_set(candidates))

    def add(cand: dict) -> bool:
        key = article_key(cand["article"])
        if key in selected_keys:
            return False
        selected.append(cand)
        selected_keys.add(key)
        src = source_name(cand["article"]).lower()
        source_counts[src] += 1
        bucket_counts[article_hour_bucket(cand["article"])] += 1
        title_cluster_counts[normalize_title(str(cand["article"].get("title", "") or ""))[:80]] += 1
        for event in cand.get("matched_event_types", []):
            per_event[event] += 1
        return True

    for event in raw_events:
        while per_event[event] < max_per_event and len(selected) < max_per_day:
            pool = [
                c for c in candidates
                if event in c.get("matched_event_types", []) and article_key(c["article"]) not in selected_keys
            ]
            if not pool:
                break
            best = max(pool, key=lambda c: candidate_score(c, cache, source_counts, bucket_counts, title_cluster_counts))
            add(best)

    # max_per_day가 꽉 차지 않았고 혹시 빠진 event가 있으면 event당 1개 강제 보강.
    reduced_events = event_set(selected)
    missing_events = sorted(set(raw_events) - reduced_events)
    for event in missing_events:
        if len(selected) >= max_per_day:
            break
        pool = [
            c for c in candidates
            if event in c.get("matched_event_types", []) and article_key(c["article"]) not in selected_keys
        ]
        if not pool:
            continue
        best = max(pool, key=lambda c: candidate_score(c, cache, source_counts, bucket_counts, title_cluster_counts))
        add(best)
    return selected


def call_gpt_with_retry(client: OpenAI, article: dict, matched_events: list[str], max_retries: int, sleep_sec: float) -> tuple[dict | None, dict]:
    messages = build_messages(article, matched_events)
    for attempt in range(1, max_retries + 2):
        t0 = time.time()
        try:
            response = client.chat.completions.create(
                model=CONFIG["LLM_MODEL"],
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=500,
                temperature=0.2,
                timeout=CONFIG["LLM_TIMEOUT"],
            )
            elapsed = time.time() - t0
            content = response.choices[0].message.content.strip()
            interp = json.loads(content)
            usage = response.usage
            meta = {
                "attempts": attempt,
                "elapsed_sec": elapsed,
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
                "error": None,
            }
            return interp, meta
        except Exception as exc:  # noqa: BLE001
            meta = {
                "attempts": attempt,
                "elapsed_sec": time.time() - t0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:220]}",
            }
            if attempt > max_retries:
                return None, meta
            time.sleep(max(1.0, sleep_sec) * attempt)
    return None, meta


def predicted_flags_from_events(events: set[str]) -> dict[str, int]:
    flags = {col: 0 for col in FLAG_COLS}
    for event, flag in EVENT_TO_FLAG.items():
        if event in events:
            flags[flag] = 1
    flags["has_rate_event"] = int(flags["has_rate_hike"] or flags["has_rate_cut"])
    return flags


def build_row_with_gpt(
    day: date,
    feed: list[dict],
    cache: dict,
    cache_file: Path,
    client: OpenAI,
    max_per_event: int,
    max_per_day: int,
    max_gpt_calls_remaining: int,
    max_retries: int,
    sleep_sec: float,
    rate_per_min: float,
) -> tuple[dict, dict, int]:
    articles = [av_item_to_article(item) for item in feed]
    av_stats = daily_av_aggregate(articles)
    raw_candidates, neg_filtered, deduped_count = candidates_for_feed(feed)
    reduced = reduce_candidates_stable(raw_candidates, cache, max_per_event=max_per_event, max_per_day=max_per_day)
    interpreted = []
    cache_hits = 0
    new_calls = 0
    failures = []
    usage_rows = []
    interval = 60.0 / rate_per_min if rate_per_min > 0 else 0.0

    for cand in reduced:
        article = cand["article"]
        key = cache_key_for_article(article)
        cached = cache.get(key)
        interp = cached.get("interpretation") if isinstance(cached, dict) else None
        if interp:
            cache_hits += 1
            interpreted.append({"article": article, "matched_event_types": cand.get("matched_event_types", []), "interpretation": interp})
            continue
        if max_gpt_calls_remaining <= 0:
            failures.append({
                "title": article.get("title", "")[:240],
                "source": source_name(article),
                "matched_event_types": cand.get("matched_event_types", []),
                "error": "max_gpt_calls_exhausted",
            })
            continue
        t_call = time.time()
        interp, meta = call_gpt_with_retry(client, article, cand.get("matched_event_types", []), max_retries=max_retries, sleep_sec=sleep_sec)
        if interp is None:
            failures.append({
                "title": article.get("title", "")[:240],
                "source": source_name(article),
                "matched_event_types": cand.get("matched_event_types", []),
                "error": meta.get("error"),
            })
        else:
            cache[key] = {"cached_at": datetime.now().isoformat(), "interpretation": interp}
            save_cache(cache_file, cache)
            interpreted.append({"article": article, "matched_event_types": cand.get("matched_event_types", []), "interpretation": interp})
            usage_rows.append(meta)
            new_calls += 1
            max_gpt_calls_remaining -= 1
        elapsed = time.time() - t_call
        if interval > 0 and elapsed < interval:
            time.sleep(interval - elapsed)

    active_events, event_adj, rejected, conflicts, conflict_penalty = aggregate_events(interpreted, verbose=False)
    row = {
        "date": day.strftime("%Y-%m-%d"),
        **av_stats,
        "candidates_count": len(raw_candidates),
        "negation_filtered": len(neg_filtered),
        "gpt_interpreted": len(interpreted),
        "gpt_calls_new": new_calls,
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
    reduced_events = event_set(reduced)
    actual_events = set(active_events.keys())
    diag = {
        "date": day.strftime("%Y-%m-%d"),
        "feed_unique": len(feed),
        "deduped_articles": deduped_count,
        "raw_candidates": len(raw_candidates),
        "reduced_candidates": len(reduced),
        "cache_hits": cache_hits,
        "gpt_calls_new": new_calls,
        "gpt_failures": len(failures),
        "failures": failures,
        "raw_events": sorted(raw_events),
        "reduced_events": sorted(reduced_events),
        "actual_events": sorted(actual_events),
        "raw_event_counts": dict(event_counts(raw_candidates)),
        "reduced_event_counts": dict(event_counts(reduced)),
        "predicted_flags": predicted_flags_from_events(reduced_events),
        "actual_flags": {col: int(row.get(col, 0) or 0) for col in FLAG_COLS},
        "active_events_count": len(active_events),
        "event_adjustment": event_adj,
        "usage": usage_rows,
    }
    return row, diag, new_calls


def csv_lines_for_rows(rows: list[dict], header_cols: list[str]) -> list[str]:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header_cols, lineterminator="\n", extrasaction="ignore")
    for row in rows:
        writer.writerow({col: row.get(col, "") for col in header_cols})
    return buf.getvalue().splitlines(keepends=True)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="EVT-2C4 actual GPT append market_history_v2 runner")
    parser.add_argument("--existing-csv", type=Path, default=DEFAULT_EXISTING_CSV)
    parser.add_argument("--append-from", type=parse_ymd, default=DEFAULT_APPEND_FROM)
    parser.add_argument("--end-date", type=parse_ymd, default=DEFAULT_END_DATE)
    parser.add_argument("--capped-list", type=Path, default=DEFAULT_CAPPED_LIST)
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--max-new-per-event", type=int, default=3)
    parser.add_argument("--max-new-per-day", type=int, default=40)
    parser.add_argument("--max-gpt-calls", type=int, required=True)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--sleep-sec", type=float, default=0.2)
    parser.add_argument("--rate-per-min", type=float, default=120.0)
    args = parser.parse_args()

    if args.out.resolve() == args.existing_csv.resolve():
        raise SystemExit("❌ --out은 원본 market_history_v2.csv와 같을 수 없습니다")
    if args.end_date < args.append_from:
        raise SystemExit("❌ --end-date must be >= --append-from")
    if args.max_gpt_calls <= 0:
        raise SystemExit("❌ --max-gpt-calls must be > 0")

    existing_df = pd.read_csv(args.existing_csv)
    header_cols = list(existing_df.columns)
    _, kept_lines, skipped_existing_lines = exact_prefix_lines(args.existing_csv, args.append_from)
    capped_dates = set(read_dates(args.capped_list))
    cache = load_cache(args.cache_file)
    cache_before = len(cache)
    all_daily_dates = available_daily_dates()
    append_dates = [d for d in all_daily_dates if args.append_from <= d <= args.end_date]
    client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])

    rows = []
    diagnostics = []
    source_modes = Counter()
    total_new_calls = 0
    max_remaining = args.max_gpt_calls
    started = datetime.now()

    for idx, d in enumerate(append_dates, start=1):
        feed, source_mode, used_files = load_feed_for_append_day(d, capped_dates)
        row, diag, new_calls = build_row_with_gpt(
            d,
            feed,
            cache,
            args.cache_file,
            client,
            max_per_event=args.max_new_per_event,
            max_per_day=args.max_new_per_day,
            max_gpt_calls_remaining=max_remaining,
            max_retries=args.max_retries,
            sleep_sec=args.sleep_sec,
            rate_per_min=args.rate_per_min,
        )
        max_remaining -= new_calls
        total_new_calls += new_calls
        row = {col: row.get(col, "") for col in header_cols}
        rows.append(row)
        diag["source_mode"] = source_mode
        diag["used_files"] = used_files
        diagnostics.append(diag)
        source_modes[source_mode] += 1
        print(
            f"DAY {idx}/{len(append_dates)} {d.isoformat()} mode={source_mode} "
            f"raw={diag['raw_candidates']} reduced={diag['reduced_candidates']} "
            f"cache={diag['cache_hits']} new={diag['gpt_calls_new']} fail={diag['gpt_failures']} "
            f"active={diag['active_events_count']} remaining={max_remaining}",
            flush=True,
        )
        if max_remaining <= 0 and idx < len(append_dates):
            print("[STOP] max_gpt_calls exhausted before all dates completed", flush=True)

    new_lines = csv_lines_for_rows(rows, header_cols)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(kept_lines + new_lines), encoding="utf-8")

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

    total_failures = sum(d["gpt_failures"] for d in diagnostics)
    actual_any_event_days = int((new_df[[c for c in FLAG_COLS if c != "has_rate_event"]].sum(axis=1) > 0).sum())
    predicted_any_event_days = sum(1 for d in diagnostics if any(v for k, v in d["predicted_flags"].items() if k != "has_rate_event"))
    actual_flag_counts = {col: int(new_df[col].sum()) for col in FLAG_COLS}
    predicted_flag_counts = Counter()
    for d in diagnostics:
        for k, v in d["predicted_flags"].items():
            predicted_flag_counts[k] += int(v)

    usage = [u for d in diagnostics for u in d.get("usage", [])]
    prompt_tokens = sum(int(u.get("prompt_tokens", 0) or 0) for u in usage)
    completion_tokens = sum(int(u.get("completion_tokens", 0) or 0) for u in usage)
    total_tokens = sum(int(u.get("total_tokens", 0) or 0) for u in usage)
    elapsed_vals = [float(u.get("elapsed_sec", 0) or 0) for u in usage]

    args.report_dir.mkdir(parents=True, exist_ok=True)
    diff_path = args.report_dir / "evt2c4run_prefix_check.txt"
    flags_path = args.report_dir / "evt2c4run_new_flags.txt"
    diag_path = args.report_dir / "evt2c4run_diagnostics.json"
    summary_path = args.report_dir / "evt2c4run_summary.json"
    write_text(diff_path, "".join(diff_lines))
    write_text(
        flags_path,
        "actual_flag_counts\n"
        + "\n".join(f"{k}\t{v}" for k, v in actual_flag_counts.items())
        + "\n\npredicted_flag_counts\n"
        + "\n".join(f"{k}\t{v}" for k, v in dict(predicted_flag_counts).items())
        + "\n",
    )
    diag_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")

    cache_after = len(load_cache(args.cache_file))
    summary = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "existing_csv": str(args.existing_csv),
        "out": str(args.out),
        "append_from": args.append_from.isoformat(),
        "end_date": args.end_date.isoformat(),
        "existing_rows": len(existing_df),
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
        "cache_before": cache_before,
        "cache_after": cache_after,
        "cache_added": cache_after - cache_before,
        "gpt_calls_new": total_new_calls,
        "gpt_failures": total_failures,
        "actual_any_event_days": actual_any_event_days,
        "predicted_any_event_days": predicted_any_event_days,
        "actual_flag_counts": actual_flag_counts,
        "predicted_flag_counts": dict(predicted_flag_counts),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "avg_elapsed_sec": mean(elapsed_vals) if elapsed_vals else None,
        "max_new_per_event": args.max_new_per_event,
        "max_new_per_day": args.max_new_per_day,
        "max_gpt_calls": args.max_gpt_calls,
        "skipped_existing_lines_at_or_after_append_from": len(skipped_existing_lines),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== EVT-2C4-RUN summary ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("=== END RUN ===")

    if not prefix_same:
        return 2
    if total_failures:
        return 3
    if len(new_df) != len(append_dates):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
