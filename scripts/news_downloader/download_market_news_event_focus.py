#!/usr/bin/env python3
"""
EVT-2C1: event-focused 시장 뉴스 다운로더

목적:
- capped day에 대해 AlphaVantage NEWS_SENTIMENT를 event-focused variants로 재수집한다.
- 원본 daily 캐시(data/_system/news_cache/daily)는 절대 수정하지 않는다.
- 기본 출력은 data/_system/news_cache/daily_event_focus 이다.
- dry-run에서는 API 호출/디렉터리 생성/파일 쓰기를 하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "data/_system/news_cache/daily_event_focus"
BASE_URL = "https://www.alphavantage.co/query"
API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
DEFAULT_RATE_PER_MIN = 70.0

STOP = False


def handle_sig(_signum, _frame):
    global STOP
    STOP = True


signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)


@dataclass(frozen=True)
class Variant:
    name: str
    sort: str | None = None
    topics: str | None = None


EVENT_FOCUS_VARIANTS = [
    Variant("relevance", sort="RELEVANCE"),
    Variant("topic_earnings", sort="RELEVANCE", topics="earnings"),
    Variant("topic_financial_markets", sort="RELEVANCE", topics="financial_markets"),
    Variant("topic_economy_monetary", sort="RELEVANCE", topics="economy_monetary"),
    Variant("topic_economy_macro", sort="RELEVANCE", topics="economy_macro"),
    Variant("topic_economy_fiscal", sort="RELEVANCE", topics="economy_fiscal"),
]


def parse_ymd(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date '{value}', expected YYYY-MM-DD") from exc


def read_capped_dates(path: Path) -> list[date]:
    if not path.exists():
        raise FileNotFoundError(f"capped list not found: {path}")
    dates: list[date] = []
    seen: set[date] = set()
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = parse_ymd(line)
        except argparse.ArgumentTypeError as exc:
            raise ValueError(f"invalid date at {path}:{lineno}: {line}") from exc
        if d not in seen:
            dates.append(d)
            seen.add(d)
    return sorted(dates)


def filter_dates(dates: list[date], start_date: date | None, end_date: date | None) -> list[date]:
    out = []
    for d in dates:
        if start_date is not None and d < start_date:
            continue
        if end_date is not None and d > end_date:
            continue
        out.append(d)
    return out


def time_range(day: date) -> tuple[str, str]:
    nd = day + timedelta(days=1)
    return day.strftime("%Y%m%dT0000"), nd.strftime("%Y%m%dT0000")


def output_path(output_dir: Path, day: date, variant: Variant) -> Path:
    return output_dir / f"av_market_{day:%Y%m%d}_{variant.name}.json"


def build_url(day: date, variant: Variant) -> str:
    t_from, t_to = time_range(day)
    params = {
        "function": "NEWS_SENTIMENT",
        "time_from": t_from,
        "time_to": t_to,
        "limit": "1000",
        "apikey": API_KEY,
    }
    if variant.sort:
        params["sort"] = variant.sort
    if variant.topics:
        params["topics"] = variant.topics
    return BASE_URL + "?" + urllib.parse.urlencode(params)


def fetch(day: date, variant: Variant) -> dict | None:
    url = build_url(day, variant)
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            info = str(data.get("Information", ""))
            if info and ("rate limit" in info.lower() or "premium" in info.lower()):
                print(f"[RATE] {day} {variant.name}: {info[:120]}", flush=True)
                time.sleep(30)
                continue
            if "Error Message" in data:
                print(f"[ERROR] {day} {variant.name}: {data['Error Message'][:120]}", flush=True)
                return None
            return data
        except Exception as exc:
            print(f"[RETRY {attempt}/3] {day} {variant.name}: {type(exc).__name__}", flush=True)
            time.sleep(5 * attempt)
    return None


def count_items(data: dict) -> tuple[int, int]:
    feed = data.get("feed", [])
    feed_n = len(feed) if isinstance(feed, list) else 0
    try:
        items = int(data.get("items", feed_n))
    except Exception:
        items = feed_n
    return items, feed_n


def classify(output_dir: Path, day: date, variant: Variant, no_overwrite_existing: bool) -> dict:
    p = output_path(output_dir, day, variant)
    exists = p.exists()
    size = p.stat().st_size if exists else 0
    if exists and no_overwrite_existing:
        action = "skip_existing_protected"
    elif exists:
        action = "fetch_overwrite_existing"
    else:
        action = "fetch_missing"
    return {"day": day, "variant": variant, "path": p, "exists": exists, "size": size, "action": action}


def build_plan(
    dates: list[date],
    output_dir: Path,
    no_overwrite_existing: bool,
    max_calls: int | None,
) -> tuple[list[dict], list[dict], list[dict]]:
    entries = [
        classify(output_dir, day, variant, no_overwrite_existing)
        for day in dates
        for variant in EVENT_FOCUS_VARIANTS
    ]
    fetch_entries = [e for e in entries if e["action"].startswith("fetch")]
    planned = fetch_entries[:max_calls] if max_calls is not None else fetch_entries
    return entries, fetch_entries, planned


def print_dry_run(args: argparse.Namespace, dates_all: list[date], dates: list[date]) -> int:
    entries, fetch_entries, planned = build_plan(
        dates,
        args.output_dir,
        args.no_overwrite_existing,
        args.max_calls,
    )
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["action"]] = counts.get(e["action"], 0) + 1
    before_cutoff = [e for e in planned if e["day"] <= date(2025, 5, 31)]
    interval = 60.0 / args.rate_per_min
    expected_total = len(dates) * len(EVENT_FOCUS_VARIANTS)

    print("=== DRY RUN: EVT-2C1 event-focused downloader ===")
    print(f"capped_list={args.capped_list}")
    print(f"output_dir={args.output_dir}")
    print(f"variants={','.join(v.name for v in EVENT_FOCUS_VARIANTS)}")
    print(f"capped_dates_in_file={len(dates_all)}")
    print(f"target_dates_after_filter={len(dates)}")
    print(f"target_first={dates[0].isoformat() if dates else 'NONE'}")
    print(f"target_last={dates[-1].isoformat() if dates else 'NONE'}")
    print(f"variants_per_day={len(EVENT_FOCUS_VARIANTS)}")
    print(f"expected_entries={expected_total}")
    print(f"estimated_api_calls_total={len(fetch_entries)}")
    print(f"planned_api_calls_this_run={len(planned)}")
    print(f"max_calls={args.max_calls if args.max_calls is not None else 'NONE'}")
    print(f"rate_per_min={args.rate_per_min:g}")
    print(f"request_interval_sec={interval:.3f}")
    print(f"estimated_minutes_at_rate={len(planned) / args.rate_per_min:.2f}")
    print(f"no_overwrite_existing={args.no_overwrite_existing}")
    print(f"touches_2025_05_31_or_before={len(before_cutoff)}")
    print("action_counts:")
    for action in sorted(counts):
        print(f"  {action}: {counts[action]}")
    if planned:
        first, last = planned[0], planned[-1]
        print(f"planned_first={first['day'].isoformat()} {first['variant'].name} path={first['path'].relative_to(ROOT)}")
        print(f"planned_last={last['day'].isoformat()} {last['variant'].name} path={last['path'].relative_to(ROOT)}")
    else:
        print("planned_first=NONE")
        print("planned_last=NONE")
    print("sample_planned_first_12:")
    for e in planned[:12]:
        print(
            f"  {e['day'].isoformat()} {e['variant'].name} "
            f"sort={e['variant'].sort} topics={e['variant'].topics} "
            f"action={e['action']} path={e['path'].relative_to(ROOT)} size={e['size']}"
        )
    print("sample_planned_last_12:")
    for e in planned[-12:]:
        print(
            f"  {e['day'].isoformat()} {e['variant'].name} "
            f"sort={e['variant'].sort} topics={e['variant'].topics} "
            f"action={e['action']} path={e['path'].relative_to(ROOT)} size={e['size']}"
        )
    print("=== END DRY RUN ===")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EVT-2C1 event-focused AlphaVantage NEWS_SENTIMENT downloader")
    parser.add_argument("--capped-list", type=Path, required=True, help="capped 날짜 목록 파일")
    parser.add_argument("--start-date", type=parse_ymd, default=None, help="대상 시작일 YYYY-MM-DD")
    parser.add_argument("--end-date", type=parse_ymd, default=None, help="대상 종료일 YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="저장 경로")
    parser.add_argument("--max-calls", type=int, default=None, help="이번 실행 최대 API 호출 수")
    parser.add_argument("--rate-per-min", type=float, default=DEFAULT_RATE_PER_MIN, help="분당 호출 상한. 기본 70")
    parser.add_argument("--dry-run", action="store_true", help="API 호출/파일 쓰기 없이 계획만 출력")
    parser.add_argument("--no-overwrite-existing", action="store_true", help="기존 event-focused 파일 보호")
    args = parser.parse_args()
    if args.start_date and args.end_date and args.end_date < args.start_date:
        parser.error("--end-date must be >= --start-date")
    if args.max_calls is not None and args.max_calls < 0:
        parser.error("--max-calls must be >= 0")
    if args.rate_per_min <= 0:
        parser.error("--rate-per-min must be > 0")
    return args


def main() -> int:
    args = parse_args()
    dates_all = read_capped_dates(args.capped_list)
    dates = filter_dates(dates_all, args.start_date, args.end_date)
    if not dates:
        raise SystemExit("❌ 대상 날짜가 없습니다")

    if args.dry_run:
        return print_dry_run(args, dates_all, dates)

    if not API_KEY:
        raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    entries, fetch_entries, planned = build_plan(
        dates,
        args.output_dir,
        args.no_overwrite_existing,
        args.max_calls,
    )
    interval = 60.0 / args.rate_per_min
    print("=== EVT-2C1 event-focused download ===", flush=True)
    print(f"target_dates={len(dates)} variants={len(EVENT_FOCUS_VARIANTS)} total_entries={len(entries)}", flush=True)
    print(f"pending_calls={len(fetch_entries)} planned_calls={len(planned)} max_calls={args.max_calls if args.max_calls is not None else 'NONE'}", flush=True)
    print(f"output_dir={args.output_dir}", flush=True)
    print(f"rate_per_min={args.rate_per_min:g} interval={interval:.3f}", flush=True)

    calls = success = failed = capped = skipped = 0
    started = time.time()
    planned_ids = {(e["day"], e["variant"].name) for e in planned}
    for e in entries:
        if STOP:
            print("[STOP] signal received", flush=True)
            break
        day = e["day"]
        variant = e["variant"]
        if not e["action"].startswith("fetch"):
            skipped += 1
            continue
        if (day, variant.name) not in planned_ids:
            continue
        t0 = time.time()
        data = fetch(day, variant)
        calls += 1
        p = e["path"]
        if data is None:
            failed += 1
            print(f"FAIL {day} {variant.name}", flush=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            items, feed_n = count_items(data)
            mark = "CAP" if items >= 1000 or feed_n >= 1000 else "OK"
            if mark == "CAP":
                capped += 1
            success += 1
            print(f"{mark} {day} {variant.name} items={items} feed={feed_n} size={p.stat().st_size//1024}KB", flush=True)
        elapsed = time.time() - t0
        if elapsed < interval:
            time.sleep(interval - elapsed)

    print(
        f"=== DONE calls={calls} success={success} failed={failed} capped={capped} "
        f"skipped_existing={skipped} elapsed_min={(time.time()-started)/60:.2f} ===",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
