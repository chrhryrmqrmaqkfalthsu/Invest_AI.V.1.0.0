#!/usr/bin/env python3
"""관심 종목 Alpha Vantage ticker sentiment 최신화.

목표
----
- 무료 플랜(기본 25 calls/day)을 전제로, 운용 후보 종목만 하루 1회 증분 갱신한다.
- 기존 monthly raw gz를 절대 단순 덮어쓰지 않고 feed 병합 + URL 기반 dedupe를 수행한다.
- raw 갱신 후 engine.market.ticker_sentiment.aggregate_ticker/save_csv로 해당 daily CSV를 강제 재집계한다.
- dry-run은 API 호출/파일 쓰기를 하지 않고 계획만 출력한다.

주의
----
아직 live news_sentiment 경로는 변경하지 않는다. 이 스크립트는 AV CSV 최신성 확보용이다.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.market.ticker_sentiment import CACHE_DIR, aggregate_ticker, save_csv

load_dotenv(ROOT / ".env")

BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "JPM", "KO", "XOM"]
DEFAULT_DAILY_LIMIT = 25
DEFAULT_MARKET_RESERVE = 2
DEFAULT_REQUEST_INTERVAL = 0.86
MAX_RETRY = 3
RETRY_WAIT_SEC = 30

OUTPUT_DIR = ROOT / "data" / "_system" / "ticker_sentiment"
MARKET_USAGE_FILE = ROOT / "data" / "_system" / "news_cache" / "_usage.json"
TICKER_USAGE_FILE = ROOT / "data" / "_system" / "ticker_sentiment_update_usage.json"
FAILURE_FILE = ROOT / "data" / "_system" / "ticker_sentiment_update_failures.json"
RUN_LOG_FILE = ROOT / "data" / "_system" / "ticker_sentiment_update.log.jsonl"


class QuotaExceeded(RuntimeError):
    pass


class FetchError(RuntimeError):
    pass


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _today_count(path: Path) -> int:
    data = _load_json(path, {})
    if data.get("date") != _today():
        return 0
    return int(data.get("count", 0) or 0)


def _load_ticker_usage() -> dict:
    data = _load_json(TICKER_USAGE_FILE, {})
    if data.get("date") != _today():
        return {"date": _today(), "count": 0, "items": []}
    data.setdefault("count", 0)
    data.setdefault("items", [])
    return data


def _record_ticker_usage(ticker: str, kind: str, status: str, meta: dict | None = None) -> int:
    data = _load_ticker_usage()
    data["count"] = int(data.get("count", 0) or 0) + 1
    item = {
        "ts": _now_iso(),
        "script": "update_ticker_sentiment_recent",
        "kind": kind,
        "ticker": ticker,
        "status": status,
    }
    if meta:
        item.update(meta)
    data.setdefault("items", []).append(item)
    _write_json_atomic(TICKER_USAGE_FILE, data)
    return int(data["count"])


def _available_calls(daily_limit: int, market_reserve: int) -> tuple[int, dict]:
    market_used = _today_count(MARKET_USAGE_FILE)
    ticker_used = _today_count(TICKER_USAGE_FILE)
    reserve_remaining = max(0, market_reserve - market_used)
    available = daily_limit - market_used - ticker_used - reserve_remaining
    return max(0, available), {
        "daily_limit": daily_limit,
        "market_used": market_used,
        "ticker_used": ticker_used,
        "market_reserve": market_reserve,
        "reserve_remaining": reserve_remaining,
        "available": max(0, available),
    }


def _last_csv_date(ticker: str) -> str | None:
    path = OUTPUT_DIR / f"{ticker}_daily.csv"
    if not path.exists():
        return None
    last = None
    try:
        with open(path, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
            if "date" not in header:
                return None
            idx = header.index("date")
            for line in f:
                parts = line.strip().split(",")
                if len(parts) > idx and parts[idx]:
                    last = parts[idx]
    except Exception:
        return None
    return last


def _build_time_range(last_date: str | None, lookback_days: int, overlap_days: int) -> tuple[str, str, str, str]:
    now = datetime.now()
    lookback_start = now - timedelta(days=lookback_days)
    if last_date:
        try:
            last_dt = datetime.strptime(last_date, "%Y-%m-%d")
            start_dt = max(last_dt - timedelta(days=overlap_days), lookback_start)
        except Exception:
            start_dt = lookback_start
    else:
        start_dt = lookback_start
    time_from = start_dt.strftime("%Y%m%dT0000")
    time_to = now.strftime("%Y%m%dT%H%M")
    return time_from, time_to, start_dt.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")


def _dedupe_key(item: dict) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return "url:" + url
    title = (item.get("title") or "").strip()
    published = (item.get("time_published") or "").strip()
    return f"fallback:{published}|{title}"


def _month_from_item(item: dict) -> str | None:
    ts = (item.get("time_published") or "").strip()
    if len(ts) < 6 or not ts[:6].isdigit():
        return None
    return ts[:6]


def _read_month_payload(ticker: str, yyyymm: str) -> dict:
    path = CACHE_DIR / ticker / f"av_{ticker}_{yyyymm}.json.gz"
    if not path.exists():
        return {"feed": []}
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("feed", [])
            return data
    except Exception:
        pass
    return {"feed": []}


def _write_month_payload(ticker: str, yyyymm: str, payload: dict) -> Path:
    ddir = CACHE_DIR / ticker
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / f"av_{ticker}_{yyyymm}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def _merge_feed_to_cache(ticker: str, new_feed: list[dict]) -> dict:
    by_month: dict[str, list[dict]] = {}
    for item in new_feed:
        month = _month_from_item(item)
        if month:
            by_month.setdefault(month, []).append(item)

    summary = {"months": {}, "new_items": 0, "deduped_items": 0}
    for yyyymm, items in sorted(by_month.items()):
        payload = _read_month_payload(ticker, yyyymm)
        existing = payload.get("feed", []) if isinstance(payload.get("feed"), list) else []
        merged: dict[str, dict] = {}
        for item in existing:
            if isinstance(item, dict):
                merged[_dedupe_key(item)] = item
        before = len(merged)
        for item in items:
            if isinstance(item, dict):
                merged[_dedupe_key(item)] = item
        after = len(merged)
        feed = sorted(merged.values(), key=lambda x: x.get("time_published", ""))
        payload["feed"] = feed
        payload["updated_at"] = _now_iso()
        payload["update_source"] = "update_ticker_sentiment_recent"
        _write_month_payload(ticker, yyyymm, payload)
        summary["months"][yyyymm] = {
            "existing": len(existing),
            "incoming": len(items),
            "merged": len(feed),
            "added": after - before,
        }
        summary["new_items"] += max(0, after - before)
        summary["deduped_items"] += max(0, len(existing) + len(items) - len(feed))
    return summary


def _fetch_range(ticker: str, time_from: str, time_to: str, api_key: str, daily_limit: int, market_reserve: int, interval: float) -> dict:
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": ticker,
        "time_from": time_from,
        "time_to": time_to,
        "limit": "1000",
        "apikey": api_key,
    }
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(1, MAX_RETRY + 1):
        available, budget = _available_calls(daily_limit, market_reserve)
        if available <= 0:
            raise QuotaExceeded(f"AV daily budget exhausted: {budget}")
        _record_ticker_usage(ticker, "ticker_news", "attempt", {"attempt": attempt, "time_from": time_from, "time_to": time_to})
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            data = json.loads(raw)
            if "Information" in data or "Note" in data:
                msg = str(data.get("Information") or data.get("Note"))[:300]
                raise FetchError(f"rate_limit_or_notice: {msg}")
            if "Error Message" in data:
                raise FetchError(f"api_error: {str(data.get('Error Message'))[:300]}")
            if "feed" not in data or not isinstance(data.get("feed"), list):
                raise FetchError(f"unexpected_payload_keys: {list(data.keys())[:20]}")
            if interval > 0:
                time.sleep(interval)
            return data
        except FetchError:
            raise
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            if attempt < MAX_RETRY:
                time.sleep(min(RETRY_WAIT_SEC, 5 * attempt))
                continue
            raise FetchError(last_error)
    raise FetchError(last_error or "unknown fetch failure")


def _append_run_log(record: dict) -> None:
    try:
        RUN_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RUN_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _load_failures() -> dict:
    data = _load_json(FAILURE_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_failures(data: dict) -> None:
    _write_json_atomic(FAILURE_FILE, data)


def _ordered_tickers(tickers: list[str]) -> list[str]:
    failures = _load_failures()
    failed = [t for t in tickers if t in failures]
    rest = [t for t in tickers if t not in failures]
    return failed + rest


def _mark_success(ticker: str) -> None:
    failures = _load_failures()
    if ticker in failures:
        failures.pop(ticker, None)
        _save_failures(failures)


def _mark_failure(ticker: str, reason: str) -> None:
    failures = _load_failures()
    failures[ticker] = {"last_failed_at": _now_iso(), "reason": reason}
    _save_failures(failures)


def _reaggregate_ticker(ticker: str) -> dict:
    rows = aggregate_ticker(ticker, verbose=False)
    if rows is None:
        raise RuntimeError("aggregate_ticker returned None (raw cache missing?)")
    if not rows:
        raise RuntimeError("aggregate_ticker returned empty rows; existing CSV preserved")
    path = save_csv(ticker, rows)
    return {"csv_path": str(path), "rows": len(rows), "first_date": rows[0]["date"], "last_date": rows[-1]["date"]}


def _process_ticker(ticker: str, args, api_key: str | None) -> dict:
    last_date = _last_csv_date(ticker)
    time_from, time_to, start_date, end_date = _build_time_range(last_date, args.lookback_days, args.overlap_days)
    plan = {
        "ticker": ticker,
        "last_csv_date": last_date,
        "time_from": time_from,
        "time_to": time_to,
        "start_date": start_date,
        "end_date": end_date,
    }

    today = _today()
    if last_date == today and not args.force:
        return {**plan, "status": "SKIP_UP_TO_DATE"}

    if args.dry_run:
        return {**plan, "status": "DRY_RUN", "would_call_api": True}

    if not api_key:
        raise RuntimeError("ALPHA_VANTAGE_KEY is not set")

    payload = _fetch_range(
        ticker,
        time_from,
        time_to,
        api_key,
        args.daily_limit,
        args.market_reserve,
        args.request_interval,
    )
    feed = payload.get("feed", [])
    merge_summary = _merge_feed_to_cache(ticker, feed) if feed else {"months": {}, "new_items": 0, "deduped_items": 0}
    aggregate_summary = _reaggregate_ticker(ticker)
    return {
        **plan,
        "status": "OK",
        "fetched_items": len(feed),
        "merge": merge_summary,
        "aggregate": aggregate_summary,
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="관심 종목 AV ticker sentiment 증분 갱신")
    p.add_argument("tickers", nargs="*", help="갱신할 티커 목록. 기본: 핵심 6종목")
    p.add_argument("--daily-limit", type=int, default=int(os.getenv("AV_DAILY_LIMIT", DEFAULT_DAILY_LIMIT)))
    p.add_argument("--market-reserve", type=int, default=int(os.getenv("AV_MARKET_RESERVE", DEFAULT_MARKET_RESERVE)), help="시장 sentiment용으로 남겨둘 호출 수")
    p.add_argument("--lookback-days", type=int, default=7, help="마지막 CSV가 오래됐을 때 최대 재조회 범위")
    p.add_argument("--overlap-days", type=int, default=1, help="날짜 경계 누락 방지용 중복 조회 일수")
    p.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    p.add_argument("--dry-run", action="store_true", help="API 호출/파일 쓰기 없이 계획만 출력")
    p.add_argument("--force", action="store_true", help="CSV 최신 날짜가 오늘이어도 호출")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    tickers = [t.upper() for t in (args.tickers or DEFAULT_TICKERS)]
    tickers = _ordered_tickers(list(dict.fromkeys(tickers)))
    api_key = os.getenv("ALPHA_VANTAGE_KEY")
    available, budget = _available_calls(args.daily_limit, args.market_reserve)

    print("=== AV ticker sentiment recent updater ===")
    print(f"tickers={tickers}")
    print(f"dry_run={args.dry_run} daily_limit={args.daily_limit} budget={budget}")
    print(f"usage_files: market={MARKET_USAGE_FILE}, ticker={TICKER_USAGE_FILE}")

    if not args.dry_run and not api_key:
        print("ERROR: ALPHA_VANTAGE_KEY is not set", file=sys.stderr)
        return 2

    ok = skip = fail = 0
    for idx, ticker in enumerate(tickers, 1):
        if not args.dry_run:
            available, budget = _available_calls(args.daily_limit, args.market_reserve)
            if available <= 0:
                print(f"[{idx}/{len(tickers)}] {ticker}: QUOTA_STOP budget={budget}")
                break
        try:
            result = _process_ticker(ticker, args, api_key)
            status = result.get("status")
            if status == "OK":
                ok += 1
                _mark_success(ticker)
                agg = result.get("aggregate", {})
                print(
                    f"[{idx}/{len(tickers)}] {ticker}: OK "
                    f"feed={result.get('fetched_items')} new={result.get('merge',{}).get('new_items')} "
                    f"csv={agg.get('rows')}일 {agg.get('first_date')}~{agg.get('last_date')}"
                )
            elif status and status.startswith("SKIP"):
                skip += 1
                print(f"[{idx}/{len(tickers)}] {ticker}: {status} last={result.get('last_csv_date')}")
            elif status == "DRY_RUN":
                skip += 1
                print(
                    f"[{idx}/{len(tickers)}] {ticker}: DRY_RUN "
                    f"last={result.get('last_csv_date')} from={result.get('time_from')} to={result.get('time_to')}"
                )
            else:
                skip += 1
                print(f"[{idx}/{len(tickers)}] {ticker}: {status}")
            _append_run_log({"ts": _now_iso(), **result})
        except QuotaExceeded as e:
            fail += 1
            reason = str(e)
            _mark_failure(ticker, reason)
            rec = {"ts": _now_iso(), "ticker": ticker, "status": "QUOTA_EXCEEDED", "error": reason}
            _append_run_log(rec)
            print(f"[{idx}/{len(tickers)}] {ticker}: QUOTA_EXCEEDED {reason}")
            break
        except Exception as e:
            fail += 1
            reason = f"{type(e).__name__}: {str(e)[:300]}"
            _mark_failure(ticker, reason)
            rec = {"ts": _now_iso(), "ticker": ticker, "status": "FAIL", "error": reason}
            _append_run_log(rec)
            print(f"[{idx}/{len(tickers)}] {ticker}: FAIL {reason}")

    print(f"=== summary: ok={ok} skip={skip} fail={fail} ===")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
