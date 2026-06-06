#!/usr/bin/env python3
"""
EVT-2CAP2 strategy pilot: NEWS_SENTIMENT sort/topics variants.

- Stores only under data/_system/news_cache/daily_pilot2
- Does not modify data/_system/news_cache/daily or market_history_v2.csv
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
OUTPUT_DIR = ROOT / "data/_system/news_cache/daily_pilot2"
API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
BASE_URL = "https://www.alphavantage.co/query"
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


DEFAULT_VARIANTS = [
    Variant("relevance", sort="RELEVANCE"),
    Variant("topic_earnings", sort="RELEVANCE", topics="earnings"),
    Variant("topic_financial_markets", sort="RELEVANCE", topics="financial_markets"),
    Variant("topic_economy_monetary", sort="RELEVANCE", topics="economy_monetary"),
    Variant("topic_economy_macro", sort="RELEVANCE", topics="economy_macro"),
    Variant("topic_economy_fiscal", sort="RELEVANCE", topics="economy_fiscal"),
]


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date '{value}', expected YYYY-MM-DD") from exc


def parse_dates(values: list[str]) -> list[date]:
    out: list[date] = []
    seen: set[date] = set()
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            d = parse_date(part)
            if d not in seen:
                out.append(d)
                seen.add(d)
    return out


def time_range(day: date) -> tuple[str, str]:
    nd = day + timedelta(days=1)
    return day.strftime("%Y%m%dT0000"), nd.strftime("%Y%m%dT0000")


def out_path(day: date, variant: Variant) -> Path:
    return OUTPUT_DIR / f"av_market_{day:%Y%m%d}_{variant.name}.json"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EVT-2CAP2 NEWS_SENTIMENT strategy pilot")
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--rate-per-min", type=float, default=DEFAULT_RATE_PER_MIN)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.parsed_dates = parse_dates(args.dates)
    if not args.parsed_dates:
        parser.error("--dates is empty")
    if args.rate_per_min <= 0:
        parser.error("--rate-per-min must be > 0")
    return args


def main() -> int:
    args = parse_args()
    interval = 60.0 / args.rate_per_min
    planned = []
    skipped = []
    for day in args.parsed_dates:
        for variant in DEFAULT_VARIANTS:
            p = out_path(day, variant)
            if p.exists() and not args.force:
                skipped.append((day, variant, p))
            else:
                planned.append((day, variant, p))
    print("=== EVT-2CAP2 strategy pilot ===", flush=True)
    print(f"dates={','.join(d.isoformat() for d in args.parsed_dates)}", flush=True)
    print(f"variants={','.join(v.name for v in DEFAULT_VARIANTS)}", flush=True)
    print(f"output_dir={OUTPUT_DIR}", flush=True)
    print(f"planned_calls={len(planned)} skipped_existing={len(skipped)}", flush=True)
    print(f"rate_per_min={args.rate_per_min:g} interval={interval:.3f}", flush=True)
    if args.dry_run:
        for day, variant, p in planned:
            print(f"DRY {day} {variant.name} sort={variant.sort} topics={variant.topics} path={p}", flush=True)
        print("=== END DRY RUN ===", flush=True)
        return 0
    if not API_KEY:
        raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    calls = success = failed = capped = 0
    started = time.time()
    for day, variant, p in planned:
        if STOP:
            print("[STOP] signal received", flush=True)
            break
        t0 = time.time()
        data = fetch(day, variant)
        calls += 1
        if data is None:
            failed += 1
            print(f"FAIL {day} {variant.name}", flush=True)
        else:
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
    print(f"=== DONE calls={calls} success={success} failed={failed} capped={capped} elapsed_min={(time.time()-started)/60:.2f} ===", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
