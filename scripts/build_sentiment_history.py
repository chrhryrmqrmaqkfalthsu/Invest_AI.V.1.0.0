"""
AlphaVantage NEWS_SENTIMENT로 3년치 시장 sentiment 시계열 빌드
- 월 단위 호출 (1회 = 한 달치)
- 영구 캐시 (한 번 받은 월은 재호출 안 함)
- 25 req/day 한도 자동 추적 → 한도 초과 시 다음 날 이어서
"""
import os, json, time, requests
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

AV_KEY = os.getenv("ALPHA_VANTAGE_KEY")
CACHE_DIR = Path("data/_system/news_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
USAGE_FILE = CACHE_DIR / "_usage.json"

# 수집 대상: 2022-03 ~ 2025-05 (39개월)
START_YM = (2020, 6)
END_YM = (2025, 5)

def get_today_usage():
    today = datetime.now().strftime("%Y-%m-%d")
    if USAGE_FILE.exists():
        u = json.loads(USAGE_FILE.read_text())
        if u.get("date") == today:
            return u.get("count", 0)
    return 0

def increment_usage():
    today = datetime.now().strftime("%Y-%m-%d")
    count = get_today_usage() + 1
    USAGE_FILE.write_text(json.dumps({"date": today, "count": count}))
    return count

def month_iter(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            m = 1; y += 1

def fetch_month(year, month):
    """한 달치 financial_markets 뉴스"""
    cache_file = CACHE_DIR / f"av_market_{year}{month:02d}.json"
    if cache_file.exists():
        return "cached", json.loads(cache_file.read_text())
    
    # 호출 한도 체크
    used = get_today_usage()
    if used >= 24:  # 25개 한도, 안전 마진 1개
        return "quota_exceeded", None
    
    # 월 시작/끝
    next_y, next_m = (year+1, 1) if month == 12 else (year, month+1)
    time_from = f"{year}{month:02d}01T0000"
    time_to = f"{next_y}{next_m:02d}01T0000"
    
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "NEWS_SENTIMENT",
        "topics": "financial_markets",
        "time_from": time_from,
        "time_to": time_to,
        "limit": 1000,
        "sort": "EARLIEST",
        "apikey": AV_KEY,
    }
    
    r = requests.get(url, params=params, timeout=30)
    j = r.json()
    
    # rate limit 또는 에러 체크
    if "Information" in j or "Note" in j:
        return "rate_limit", j
    if "Error Message" in j:
        return "error", j
    
    items = j.get("feed", [])
    if not items:
        # 빈 응답도 캐시 (재시도 방지)
        cache_file.write_text(json.dumps({"feed": [], "fetched_at": datetime.now().isoformat()}))
        increment_usage()
        return "empty", []
    
    payload = {"feed": items, "fetched_at": datetime.now().isoformat()}
    cache_file.write_text(json.dumps(payload, ensure_ascii=False))
    increment_usage()
    return "fetched", items

def main():
    print("=" * 60)
    print("AlphaVantage Sentiment History Builder")
    print(f"기간: {START_YM[0]}-{START_YM[1]:02d} ~ {END_YM[0]}-{END_YM[1]:02d}")
    print(f"오늘 사용: {get_today_usage()}/25")
    print("=" * 60)
    
    stats = defaultdict(int)
    months_to_fetch = list(month_iter(START_YM, END_YM))
    print(f"총 {len(months_to_fetch)}개월 처리 예정\n")
    
    for y, m in months_to_fetch:
        ym = f"{y}-{m:02d}"
        status, data = fetch_month(y, m)
        stats[status] += 1
        
        if status == "cached":
            print(f"  [{ym}] ✓ 캐시 ({len(data.get('feed', []))}건)")
        elif status == "fetched":
            print(f"  [{ym}] ✅ 신규 ({len(data)}건) — 사용 {get_today_usage()}/25")
            time.sleep(13)  # 분당 5회 = 12초 간격, 안전 마진 1초
        elif status == "empty":
            print(f"  [{ym}] ⚠ 빈 응답")
            time.sleep(13)
        elif status == "quota_exceeded":
            print(f"\n⛔ 일일 한도 도달. 내일 다시 실행하세요.")
            break
        elif status == "rate_limit":
            print(f"  [{ym}] ⚠ Rate limit: {str(data)[:150]}")
            print(f"\n오늘은 여기까지. 내일 다시 실행하세요.")
            break
        else:
            print(f"  [{ym}] ❌ 에러: {str(data)[:150]}")
            break
    
    print("\n" + "=" * 60)
    print(f"통계: {dict(stats)}")
    
    # 완료 여부
    cached_count = len(list(CACHE_DIR.glob("av_market_*.json")))
    total_needed = len(months_to_fetch)
    print(f"진행률: {cached_count}/{total_needed} ({cached_count*100//total_needed}%)")
    
    if cached_count >= total_needed:
        print("\n✅ 모든 월 수집 완료! 다음 단계: aggregate_sentiment.py")
    else:
        remaining = total_needed - cached_count
        days_needed = (remaining + 23) // 24
        print(f"\n⏳ 남은 월: {remaining}개월 → 약 {days_needed}일 더 필요")
    print("=" * 60)

if __name__ == "__main__":
    main()
