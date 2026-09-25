#!/usr/bin/env python3
"""
EVT-2B2 파일럿: capped 일자를 intraday window로 분할 수집한다.

- 기본 출력: data/_system/news_cache/daily_pilot/av_market_YYYYMMDD_HHMM_HHMM.json
- 기존 data/_system/news_cache/daily/ 원본은 절대 수정하지 않는다.
- AlphaVantage NEWS_SENTIMENT time_from/time_to를 window 단위로 호출한다.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "data/_system/news_cache/daily_pilot"
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
class Segment:
    day: date
    start_hour: int
    end_hour: int

    @property
    def start_dt(self) -> datetime:
        return datetime.combine(self.day, dtime(self.start_hour, 0))

    @property
    def end_dt(self) -> datetime:
        if self.end_hour >= 24:
            return datetime.combine(self.day + timedelta(days=1), dtime(0, 0))
        return datetime.combine(self.day, dtime(self.end_hour, 0))

    @property
    def label_start(self) -> str:
        return f"{self.start_hour:02d}00"

    @property
    def label_end(self) -> str:
        return "2400" if self.end_hour >= 24 else f"{self.end_hour:02d}00"

    @property
    def filename(self) -> str:
        return f"av_market_{self.day:%Y%m%d}_{self.label_start}_{self.label_end}.json"

    @property
    def time_from(self) -> str:
        return self.start_dt.strftime("%Y%m%dT%H%M")

    @property
    def time_to(self) -> str:
        return self.end_dt.strftime("%Y%m%dT%H%M")


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date '{value}', expected YYYY-MM-DD") from exc


def parse_dates_arg(values: list[str]) -> list[date]:
    dates: list[date] = []
    seen: set[date] = set()
    for raw in values:
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            d = parse_date(part)
            if d not in seen:
                dates.append(d)
                seen.add(d)
    return dates


def build_segments(days: list[date], window_hours: int) -> list[Segment]:
    if 24 % window_hours != 0:
        raise ValueError("window-hours must divide 24")
    segments: list[Segment] = []
    for day in days:
        for start in range(0, 24, window_hours):
            segments.append(Segment(day=day, start_hour=start, end_hour=start + window_hours))
    return segments


def segment_path(output_dir: Path, seg: Segment) -> Path:
    return output_dir / seg.filename


def fetch_segment(seg: Segment) -> dict | None:
    url = (
        f"{BASE_URL}?function=NEWS_SENTIMENT"
        f"&time_from={seg.time_from}&time_to={seg.time_to}"
        f"&limit=1000&apikey={API_KEY}"
    )
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            info = str(data.get("Information", ""))
            if info and ("rate limit" in info.lower() or "premium" in info.lower()):
                print(f"[RATE] {seg.day} {seg.label_start}-{seg.label_end}: {info[:120]}", flush=True)
                time.sleep(30)
                continue
            if "Error Message" in data:
                print(f"[ERROR] {seg.day} {seg.label_start}-{seg.label_end}: {data['Error Message'][:120]}", flush=True)
                return None
            return data
        except Exception as exc:
            print(f"[RETRY {attempt}/3] {seg.day} {seg.label_start}-{seg.label_end}: {type(exc).__name__}", flush=True)
            time.sleep(5 * attempt)
    return None


def feed_count(data: dict) -> int:
    feed = data.get("feed", [])
    return len(feed) if isinstance(feed, list) else 0


def items_count(data: dict) -> int:
    raw = data.get("items", feed_count(data))
    try:
        return int(raw)
    except Exception:
        return feed_count(data)


def item_key(item: dict) -> str:
    url = str(item.get("url", "") or "").strip()
    if url:
        return "url:" + url
    title = str(item.get("title", "") or "").strip().lower()
    ts = str(item.get("time_published", "") or "")
    return "title:" + title + "|" + ts


def analyze_day(output_dir: Path, day: date, window_hours: int) -> dict:
    rows = []
    unique_keys: set[str] = set()
    total_feed = 0
    capped = []
    empty = []
    for seg in build_segments([day], window_hours):
        p = segment_path(output_dir, seg)
        if not p.exists():
            rows.append({"segment": seg, "exists": False, "items": None, "feed": None, "size": 0})
            empty.append(seg)
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            rows.append({"segment": seg, "exists": True, "items": None, "feed": None, "size": p.stat().st_size})
            empty.append(seg)
            continue
        items = items_count(data)
        feed = data.get("feed", []) if isinstance(data.get("feed", []), list) else []
        total_feed += len(feed)
        for item in feed:
            unique_keys.add(item_key(item))
        if items >= 1000 or len(feed) >= 1000:
            capped.append(seg)
        if len(feed) == 0 or any(k in data for k in ("Information", "Note", "Error Message")):
            empty.append(seg)
        rows.append({"segment": seg, "exists": True, "items": items, "feed": len(feed), "size": p.stat().st_size})
    return {
        "day": day,
        "rows": rows,
        "total_feed": total_feed,
        "unique_count": len(unique_keys),
        "capped_segments": capped,
        "empty_or_failed_segments": empty,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EVT-2B2 intraday pilot downloader")
    parser.add_argument("--dates", nargs="+", required=True, help="YYYY-MM-DD 목록. 쉼표 구분 또는 공백 구분 가능")
    parser.add_argument("--window-hours", type=int, default=2, help="분할 시간 단위. 기본 2")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--rate-per-min", type=float, default=DEFAULT_RATE_PER_MIN)
    parser.add_argument("--max-calls", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="기존 pilot segment 파일 덮어쓰기")
    args = parser.parse_args()
    if args.window_hours <= 0 or 24 % args.window_hours != 0:
        parser.error("--window-hours must be a positive divisor of 24")
    if args.rate_per_min <= 0:
        parser.error("--rate-per-min must be > 0")
    if args.max_calls is not None and args.max_calls < 0:
        parser.error("--max-calls must be >= 0")
    args.parsed_dates = parse_dates_arg(args.dates)
    if not args.parsed_dates:
        parser.error("--dates is empty")
    return args


def main() -> int:
    args = parse_args()
    segments = build_segments(args.parsed_dates, args.window_hours)
    interval = 60.0 / args.rate_per_min
    planned = []
    skipped_existing = []
    for seg in segments:
        p = segment_path(args.output_dir, seg)
        if p.exists() and not args.force:
            skipped_existing.append(seg)
        else:
            planned.append(seg)
    if args.max_calls is not None:
        planned = planned[: args.max_calls]

    print("=== EVT-2B2 intraday pilot ===", flush=True)
    print(f"dates={','.join(d.isoformat() for d in args.parsed_dates)}", flush=True)
    print(f"window_hours={args.window_hours}", flush=True)
    print(f"output_dir={args.output_dir}", flush=True)
    print(f"segments_total={len(segments)}", flush=True)
    print(f"segments_existing_skip={len(skipped_existing)}", flush=True)
    print(f"planned_calls={len(planned)}", flush=True)
    print(f"rate_per_min={args.rate_per_min:g}", flush=True)
    print(f"request_interval_sec={interval:.3f}", flush=True)

    if args.dry_run:
        for seg in planned:
            print(f"DRY {seg.day} {seg.label_start}-{seg.label_end} {seg.time_from}->{seg.time_to} {segment_path(args.output_dir, seg)}")
        print("=== END DRY RUN ===", flush=True)
        return 0

    if not API_KEY:
        raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    calls = success = failed = capped = 0
    started = time.time()
    for seg in planned:
        if STOP:
            print("[STOP] signal received", flush=True)
            break
        t0 = time.time()
        data = fetch_segment(seg)
        calls += 1
        p = segment_path(args.output_dir, seg)
        if data is None:
            failed += 1
            print(f"FAIL {seg.day} {seg.label_start}-{seg.label_end}", flush=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            items = items_count(data)
            feed = feed_count(data)
            if items >= 1000 or feed >= 1000:
                capped += 1
                mark = "CAP"
            else:
                mark = "OK"
            success += 1
            print(f"{mark} {seg.day} {seg.label_start}-{seg.label_end} items={items} feed={feed} size={p.stat().st_size//1024}KB", flush=True)
        elapsed = time.time() - t0
        if elapsed < interval:
            time.sleep(interval - elapsed)

    print(f"=== FETCH DONE calls={calls} success={success} failed={failed} capped={capped} elapsed_min={(time.time()-started)/60:.2f} ===", flush=True)
    print("=== DAY SUMMARY ===", flush=True)
    for day in args.parsed_dates:
        info = analyze_day(args.output_dir, day, args.window_hours)
        print(
            f"DAY {day} total_feed={info['total_feed']} unique={info['unique_count']} "
            f"capped_segments={len(info['capped_segments'])} empty_or_failed={len(info['empty_or_failed_segments'])}",
            flush=True,
        )
        for row in info["rows"]:
            seg = row["segment"]
            print(
                f"  SEG {seg.label_start}-{seg.label_end} exists={row['exists']} "
                f"items={row['items']} feed={row['feed']} size={row['size']}",
                flush=True,
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
