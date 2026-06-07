#!/usr/bin/env python3
"""
EVT-2C4-RUN 보조: 선택된 이벤트 후보 GPT 해석을 병렬로 캐시에 미리 채운다.

- market_history_v2.csv는 절대 쓰지 않는다.
- run_append_market_history_v2.py와 같은 후보 선택 함수를 사용한다.
- 성공 1건마다 llm_news_cache.json에 즉시 저장한다.
- 중단/재개 가능: 이미 캐시에 있는 후보는 건너뛴다.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, date
from pathlib import Path
from statistics import mean

from openai import OpenAI

from engine.market.colab_v32 import CONFIG
from scripts.news_downloader.dry_run_append_market_history_v2 import (
    DEFAULT_CAPPED_LIST,
    available_daily_dates,
    load_feed_for_append_day,
    parse_ymd,
)
from scripts.news_downloader.dry_run_event_candidate_reduction import (
    candidates_for_feed,
    load_cache,
    read_dates,
)
from scripts.news_downloader.run_append_market_history_v2 import (
    DEFAULT_APPEND_FROM,
    DEFAULT_CACHE_FILE,
    DEFAULT_END_DATE,
    DEFAULT_REPORT_DIR,
    cache_key_for_article,
    call_gpt_with_retry,
    reduce_candidates_stable,
    save_cache,
    source_name,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data/_system/research/evt2c4run_20260607/evt2c4_prefill_summary.json"


def collect_targets(
    append_from: date,
    end_date: date,
    capped_dates: set[date],
    cache: dict,
    max_per_event: int,
    max_per_day: int,
) -> list[dict]:
    targets = []
    seen = set()
    append_dates = [d for d in available_daily_dates() if append_from <= d <= end_date]
    for d in append_dates:
        feed, source_mode, used_files = load_feed_for_append_day(d, capped_dates)
        raw_candidates, _neg, _deduped = candidates_for_feed(feed)
        selected = reduce_candidates_stable(raw_candidates, cache, max_per_event=max_per_event, max_per_day=max_per_day)
        for cand in selected:
            article = cand["article"]
            key = cache_key_for_article(article)
            if not key or key in seen:
                continue
            seen.add(key)
            if isinstance(cache.get(key), dict) and cache[key].get("interpretation"):
                continue
            targets.append({
                "date": d.isoformat(),
                "source_mode": source_mode,
                "cache_key": key,
                "article": article,
                "matched_event_types": cand.get("matched_event_types", []),
                "title": article.get("title", "")[:240],
                "source": source_name(article),
            })
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description="EVT-2C4 selected event GPT cache prefill")
    parser.add_argument("--append-from", type=parse_ymd, default=DEFAULT_APPEND_FROM)
    parser.add_argument("--end-date", type=parse_ymd, default=DEFAULT_END_DATE)
    parser.add_argument("--capped-list", type=Path, default=DEFAULT_CAPPED_LIST)
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-new-per-event", type=int, default=3)
    parser.add_argument("--max-new-per-day", type=int, default=40)
    parser.add_argument("--max-calls", type=int, default=5200)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--sleep-sec", type=float, default=0.2)
    args = parser.parse_args()

    cache_lock = threading.Lock()
    cache = load_cache(args.cache_file)
    cache_before = len(cache)
    capped_dates = set(read_dates(args.capped_list))
    targets = collect_targets(
        args.append_from,
        args.end_date,
        capped_dates,
        cache,
        max_per_event=args.max_new_per_event,
        max_per_day=args.max_new_per_day,
    )
    targets = targets[:args.max_calls]
    total_targets = len(targets)
    started = datetime.now()
    counters = Counter()
    usage_rows = []
    failures = []
    progress_path = args.report_dir / "evt2c4_prefill_progress.jsonl"
    args.report_dir.mkdir(parents=True, exist_ok=True)

    thread_local = threading.local()

    def get_client() -> OpenAI:
        client = getattr(thread_local, "client", None)
        if client is None:
            client = OpenAI(api_key=CONFIG["OPENAI_API_KEY"])
            thread_local.client = client
        return client

    def worker(target: dict) -> dict:
        key = target["cache_key"]
        with cache_lock:
            cached = cache.get(key)
            if isinstance(cached, dict) and cached.get("interpretation"):
                return {"status": "cache_hit", "target": target}
        interp, meta = call_gpt_with_retry(
            get_client(),
            target["article"],
            target["matched_event_types"],
            max_retries=args.max_retries,
            sleep_sec=args.sleep_sec,
        )
        if interp is None:
            return {"status": "failed", "target": target, "meta": meta}
        with cache_lock:
            cache[key] = {"cached_at": datetime.now().isoformat(), "interpretation": interp}
            save_cache(args.cache_file, cache)
            done_count = counters["success"] + 1
            progress_path.open("a", encoding="utf-8").write(json.dumps({
                "ts": datetime.now().isoformat(),
                "done_success_including_this": done_count,
                "cache_entries": len(cache),
                "date": target["date"],
                "title": target["title"],
                "meta": meta,
            }, ensure_ascii=False) + "\n")
        return {"status": "success", "target": target, "meta": meta}

    print("=== EVT-2C4 GPT cache prefill ===", flush=True)
    print(f"cache_before={cache_before} targets_missing={total_targets} workers={args.max_workers}", flush=True)
    last_print = time.time()
    with ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = [ex.submit(worker, t) for t in targets]
        for fut in as_completed(futures):
            result = fut.result()
            status = result["status"]
            counters[status] += 1
            if status == "success":
                usage_rows.append(result.get("meta", {}))
            elif status == "failed":
                failures.append({
                    "target": {k: v for k, v in result["target"].items() if k not in ("article",)},
                    "meta": result.get("meta", {}),
                })
            now = time.time()
            if now - last_print >= 10 or sum(counters.values()) == total_targets:
                print(
                    f"progress done={sum(counters.values())}/{total_targets} "
                    f"success={counters['success']} cache_hit={counters['cache_hit']} failed={counters['failed']}",
                    flush=True,
                )
                last_print = now

    cache_after = len(load_cache(args.cache_file))
    elapsed = (datetime.now() - started).total_seconds()
    prompt_tokens = sum(int(u.get("prompt_tokens", 0) or 0) for u in usage_rows)
    completion_tokens = sum(int(u.get("completion_tokens", 0) or 0) for u in usage_rows)
    total_tokens = sum(int(u.get("total_tokens", 0) or 0) for u in usage_rows)
    elapsed_vals = [float(u.get("elapsed_sec", 0) or 0) for u in usage_rows]
    summary = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now().isoformat(),
        "append_from": args.append_from.isoformat(),
        "end_date": args.end_date.isoformat(),
        "cache_before": cache_before,
        "cache_after": cache_after,
        "cache_added": cache_after - cache_before,
        "targets_missing_at_start": total_targets,
        "success": counters["success"],
        "cache_hit_during_run": counters["cache_hit"],
        "failed": counters["failed"],
        "max_workers": args.max_workers,
        "elapsed_sec": elapsed,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "avg_elapsed_sec_per_success": mean(elapsed_vals) if elapsed_vals else None,
        "failures": failures,
        "progress_path": str(progress_path),
    }
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print("=== END PREFILL ===", flush=True)
    return 1 if counters["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
