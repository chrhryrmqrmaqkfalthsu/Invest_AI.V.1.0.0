#!/usr/bin/env python3
"""
시장 전체 뉴스 일별 다운로드
- 기간: 2020-06-01 ~ 2025-05-31 (약 1825일)
- 저장: data/_system/news_cache/daily/av_market_YYYYMMDD.json
- 일별 호출로 1000 limit 회피
"""
import os, sys, json, time, signal
from pathlib import Path
from datetime import datetime, date, timedelta
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data/_system/news_cache/daily"
LOG_FILE = Path("/tmp/market_news_daily.log")

API_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
if not API_KEY:
    raise SystemExit("❌ ALPHA_VANTAGE_KEY 환경변수가 필요합니다 (.env 파일 확인)")
BASE_URL = "https://www.alphavantage.co/query"
REQ_INTERVAL = 0.86

START_DATE = date(2020, 6, 1)
END_DATE = date(2025, 5, 31)

STOP = False
def handle_sig(s, f):
    global STOP
    STOP = True
signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

def fetch_day(d):
    nd = d + timedelta(days=1)
    t_from = f"{d.year:04d}{d.month:02d}{d.day:02d}T0000"
    t_to = f"{nd.year:04d}{nd.month:02d}{nd.day:02d}T0000"
    url = (f"{BASE_URL}?function=NEWS_SENTIMENT"
           f"&time_from={t_from}&time_to={t_to}&limit=1000&apikey={API_KEY}")
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
            if "Information" in data and ("rate limit" in data["Information"].lower() or
                                          "premium" in data["Information"].lower()):
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

def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    total_days = (END_DATE - START_DATE).days + 1
    log(f"=== 시장 뉴스 일별 다운로드: {total_days}일 ===")

    done, skipped, failed, hit1000 = 0, 0, 0, 0
    start_ts = time.time()
    cur = START_DATE

    while cur <= END_DATE:
        if STOP:
            log("[STOP] 신호 수신 → 종료")
            break

        path = CACHE_DIR / f"av_market_{cur.year:04d}{cur.month:02d}{cur.day:02d}.json"
        if path.exists() and path.stat().st_size > 100:
            skipped += 1
            cur += timedelta(days=1)
            continue

        t0 = time.time()
        d = fetch_day(cur)
        if d is not None:
            items = d.get("items", 0)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False)
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
        if elapsed < REQ_INTERVAL:
            time.sleep(REQ_INTERVAL - elapsed)

        cur += timedelta(days=1)

        # 매 100일마다 진행 요약
        if done > 0 and done % 100 == 0:
            rate = (done + skipped) / max(time.time() - start_ts, 1) * 60
            log(f"  [진행] {done + skipped}/{total_days}일, 속도 {rate:.1f}/min, 1000hit {hit1000}건")

    log(f"=== 종료: 신규 {done}, skip {skipped}, 실패 {failed}, 1000hit {hit1000} | 소요 {(time.time()-start_ts)/60:.1f}분 ===")

if __name__ == "__main__":
    main()
