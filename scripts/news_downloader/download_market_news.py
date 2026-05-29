#!/usr/bin/env python3
"""
AlphaVantage 시장 전체 뉴스 다운로드 (tickers 파라미터 없음)
- 기간: 2020-06 ~ 2025-05 (60개월)
- 기존 21개월 (2022-03 ~ 2023-11) skip
- 저장: data/_system/news_cache/av_market_YYYYMM.json (gzip 없음, 기존 포맷 유지)
"""
import os, sys, json, time
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data/_system/news_cache"
LOG_FILE = Path("/tmp/market_news_download.log")

API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
if not API_KEY:
    raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")
BASE_URL = "https://www.alphavantage.co/query"
REQ_INTERVAL = 0.86  # 70 req/min

MONTHS = []
for y in range(2020, 2026):
    for m in range(1, 13):
        if (y == 2020 and m < 6) or (y == 2025 and m > 5) or y > 2025:
            continue
        MONTHS.append((y, m))

def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def fetch_market_month(year, month):
    t_from = f"{year:04d}{month:02d}01T0000"
    if month == 12:
        ny, nm = year + 1, 1
    else:
        ny, nm = year, month + 1
    t_to = f"{ny:04d}{nm:02d}01T0000"
    # tickers 파라미터 없음 → 시장 전체
    url = (f"{BASE_URL}?function=NEWS_SENTIMENT"
           f"&time_from={t_from}&time_to={t_to}&limit=1000&apikey={API_KEY}")
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                d = json.loads(resp.read())
            if "Information" in d and ("rate limit" in d["Information"].lower() or
                                       "premium" in d["Information"].lower()):
                log(f"  [RATE] {year}-{month:02d}: {d['Information'][:80]}")
                time.sleep(30)
                continue
            if "Error Message" in d:
                log(f"  [ERROR] {year}-{month:02d}: {d['Error Message'][:80]}")
                return None
            return d
        except Exception as e:
            log(f"  [RETRY {attempt}/3] {year}-{month:02d}: {type(e).__name__}")
            time.sleep(5 * attempt)
    return None

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"=== 시장 뉴스 다운로드: {len(MONTHS)}개월 ===")

    done, skipped, failed = 0, 0, 0
    start_ts = time.time()

    for (y, m) in MONTHS:
        path = CACHE_DIR / f"av_market_{y:04d}{m:02d}.json"
        if path.exists() and path.stat().st_size > 1000:
            skipped += 1
            continue

        t0 = time.time()
        d = fetch_market_month(y, m)
        if d is not None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
            items = d.get("items", 0)
            log(f"✅ {y}-{m:02d}: items={items}, size={path.stat().st_size//1024}KB ({time.time()-t0:.1f}s)")
            done += 1
        else:
            log(f"❌ {y}-{m:02d}: 실패")
            failed += 1

        elapsed = time.time() - t0
        if elapsed < REQ_INTERVAL:
            time.sleep(REQ_INTERVAL - elapsed)

    log(f"=== 종료: 신규 {done}, skip {skipped}, 실패 {failed} | 소요 {(time.time()-start_ts)/60:.1f}분 ===")

if __name__ == "__main__":
    main()
