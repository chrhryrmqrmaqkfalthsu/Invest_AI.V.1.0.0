#!/usr/bin/env python3
"""
시장 전체 뉴스 일별 다운로드
- 저장: data/_system/news_cache/daily/av_market_YYYYMMDD.json
- 일별 호출로 1000 limit 회피
- dry-run / 날짜 범위 / 호출 상한 / 기존 파일 보호 옵션 지원
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv as _load_dotenv

_load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data/_system/news_cache/daily"
LOG_FILE = Path("/tmp/market_news_daily.log")

API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_RATE_PER_MIN = 70.0
DEFAULT_START_DATE = date(2020, 6, 1)
DEFAULT_END_DATE = date.today() - timedelta(days=1)  # 어제까지 자동

STOP = False


def handle_sig(s, f):
    global STOP
    STOP = True


signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)


def parse_ymd(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date '{value}', expected YYYY-MM-DD") from exc


def date_iter(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def daily_path(d: date) -> Path:
    return CACHE_DIR / f"av_market_{d.year:04d}{d.month:02d}{d.day:02d}.json"


def log(msg: str, *, write_file: bool = True):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    if write_file:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def fetch_day(d: date):
    nd = d + timedelta(days=1)
    t_from = f"{d.year:04d}{d.month:02d}{d.day:02d}T0000"
    t_to = f"{nd.year:04d}{nd.month:02d}{nd.day:02d}T0000"
    url = (
        f"{BASE_URL}?function=NEWS_SENTIMENT"
        f"&time_from={t_from}&time_to={t_to}&limit=1000&apikey={API_KEY}"
    )
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            if "Information" in data and (
                "rate limit" in data["Information"].lower()
                or "premium" in data["Information"].lower()
            ):
                log(f"  [RATE] {d}: {data['Information'][:80]}")
                time.sleep(30)
                continue
            if "Error Message" in data:
                log(f"  [ERROR] {d}: {data['Error Message'][:80]}")
                return None
            return data
        except Exception as e:
            log(f"  [RETRY {attempt}/3] {d}: {type(e).__name__}")
            time.sleep(5 * attempt)
    return None


def classify_date(d: date, *, no_overwrite_existing: bool) -> dict:
    path = daily_path(d)
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    if exists and no_overwrite_existing:
        return {"date": d, "path": path, "action": "skip_existing_protected", "size": size}
    if exists and size > 100:
        return {"date": d, "path": path, "action": "skip_existing_valid", "size": size}
    if exists:
        return {"date": d, "path": path, "action": "fetch_overwrite_small", "size": size}
    return {"date": d, "path": path, "action": "fetch_missing", "size": 0}


def build_plan(start_date: date, end_date: date, *, no_overwrite_existing: bool, max_calls: int | None):
    entries = [
        classify_date(d, no_overwrite_existing=no_overwrite_existing)
        for d in date_iter(start_date, end_date)
    ]
    fetch_entries = [e for e in entries if e["action"].startswith("fetch")]
    planned_fetch_entries = fetch_entries[:max_calls] if max_calls is not None else fetch_entries
    return entries, fetch_entries, planned_fetch_entries


def print_dry_run(args: argparse.Namespace) -> int:
    entries, fetch_entries, planned_fetch_entries = build_plan(
        args.start_date,
        args.end_date,
        no_overwrite_existing=args.no_overwrite_existing,
        max_calls=args.max_calls,
    )
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["action"]] = counts.get(e["action"], 0) + 1
    before_start_touches = [e for e in planned_fetch_entries if e["date"] < args.start_date]
    interval = 60.0 / args.rate_per_min

    print("=== DRY RUN: market news daily backfill plan ===")
    print(f"range={args.start_date.isoformat()}..{args.end_date.isoformat()}")
    print(f"total_days={(args.end_date - args.start_date).days + 1}")
    print(f"no_overwrite_existing={args.no_overwrite_existing}")
    print(f"rate_per_min={args.rate_per_min:g}")
    print(f"request_interval_sec={interval:.3f}")
    print(f"max_calls={args.max_calls if args.max_calls is not None else 'NONE'}")
    print(f"estimated_api_calls_total={len(fetch_entries)}")
    print(f"planned_api_calls_this_run={len(planned_fetch_entries)}")
    print(f"touches_before_start={len(before_start_touches)}")
    print("action_counts:")
    for action in sorted(counts):
        print(f"  {action}: {counts[action]}")
    if fetch_entries:
        print(f"fetch_total_first={fetch_entries[0]['date'].isoformat()}")
        print(f"fetch_total_last={fetch_entries[-1]['date'].isoformat()}")
    else:
        print("fetch_total_first=NONE")
        print("fetch_total_last=NONE")
    if planned_fetch_entries:
        print(f"planned_first={planned_fetch_entries[0]['date'].isoformat()}")
        print(f"planned_last={planned_fetch_entries[-1]['date'].isoformat()}")
    else:
        print("planned_first=NONE")
        print("planned_last=NONE")

    print("FETCH_DATES_THIS_RUN:")
    if not planned_fetch_entries:
        print("  NONE")
    for e in planned_fetch_entries:
        rel = e["path"].relative_to(ROOT)
        print(f"  {e['date'].isoformat()} action={e['action']} path={rel} size={e['size']}")

    protected_small = [
        e for e in entries
        if e["action"] == "skip_existing_protected" and 0 < e["size"] <= 100
    ]
    print("PROTECTED_SMALL_EXISTING:")
    if not protected_small:
        print("  NONE")
    for e in protected_small:
        rel = e["path"].relative_to(ROOT)
        print(f"  {e['date'].isoformat()} path={rel} size={e['size']}")
    print("=== END DRY RUN ===")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시장 전체 AlphaVantage NEWS_SENTIMENT 일별 다운로드")
    parser.add_argument("--start-date", type=parse_ymd, default=DEFAULT_START_DATE, help="수집 시작일 YYYY-MM-DD")
    parser.add_argument("--end-date", type=parse_ymd, default=DEFAULT_END_DATE, help="수집 종료일 YYYY-MM-DD")
    parser.add_argument("--max-calls", type=int, default=None, help="이번 실행에서 허용할 최대 API 호출 수")
    parser.add_argument("--rate-per-min", type=float, default=DEFAULT_RATE_PER_MIN, help="분당 호출 상한. 기본 70")
    parser.add_argument("--dry-run", action="store_true", help="API 호출/파일 쓰기 없이 계획만 출력")
    parser.add_argument(
        "--no-overwrite-existing",
        action="store_true",
        help="기존 daily 파일은 크기와 무관하게 절대 덮어쓰지 않음",
    )
    args = parser.parse_args(argv)
    if args.end_date < args.start_date:
        parser.error("--end-date must be >= --start-date")
    if args.max_calls is not None and args.max_calls < 0:
        parser.error("--max-calls must be >= 0")
    if args.rate_per_min <= 0:
        parser.error("--rate-per-min must be > 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        return print_dry_run(args)
    if not API_KEY:
        raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entries, fetch_entries, _ = build_plan(
        args.start_date,
        args.end_date,
        no_overwrite_existing=args.no_overwrite_existing,
        max_calls=None,
    )
    total_days = len(entries)
    request_interval = 60.0 / args.rate_per_min
    log(
        "=== 시장 뉴스 일별 다운로드: "
        f"range={args.start_date}..{args.end_date}, days={total_days}, "
        f"pending_calls={len(fetch_entries)}, max_calls={args.max_calls if args.max_calls is not None else 'NONE'}, "
        f"rate_per_min={args.rate_per_min:g}, no_overwrite_existing={args.no_overwrite_existing} ==="
    )

    done, skipped, failed, hit1000, call_count = 0, 0, 0, 0, 0
    start_ts = time.time()

    for e in entries:
        if STOP:
            log("[STOP] 신호 수신 → 종료")
            break

        cur = e["date"]
        path = e["path"]
        action = e["action"]
        if not action.startswith("fetch"):
            skipped += 1
            continue

        if args.max_calls is not None and call_count >= args.max_calls:
            log(f"[MAX_CALLS] max_calls={args.max_calls} 도달 → 정상 중단")
            break

        t0 = time.time()
        data = fetch_day(cur)
        call_count += 1
        if data is not None:
            items = data.get("items", 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            if items == 1000:
                hit1000 += 1
                log(f"⚠️ {cur}: items=1000 (HIT, 잘림 가능) {path.stat().st_size//1024}KB")
            else:
                log(f"✅ {cur}: items={items} ({path.stat().st_size//1024}KB)")
            done += 1
        else:
            log(f"❌ {cur}: 실패")
            failed += 1

        elapsed = time.time() - t0
        if elapsed < request_interval:
            time.sleep(request_interval - elapsed)

        if done > 0 and done % 100 == 0:
            rate = (done + skipped) / max(time.time() - start_ts, 1) * 60
            log(f"  [진행] {done + skipped}/{total_days}일, 속도 {rate:.1f}/min, 1000hit {hit1000}건")

    log(
        f"=== 종료: 신규 {done}, skip {skipped}, 실패 {failed}, "
        f"calls {call_count}, 1000hit {hit1000} | 소요 {(time.time()-start_ts)/60:.1f}분 ==="
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
