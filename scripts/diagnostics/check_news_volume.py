import os, time, requests
from dotenv import load_dotenv
load_dotenv()

av = os.getenv('ALPHA_VANTAGE_KEY')

# 2022년 3월 한 달치, limit=1000 최대
print("=== 시장 전체 뉴스 (2022-03, limit=1000) ===")
r = requests.get(
    f'https://www.alphavantage.co/query'
    f'?function=NEWS_SENTIMENT'
    f'&topics=financial_markets'
    f'&time_from=20220301T0000'
    f'&time_to=20220331T2359'
    f'&limit=1000'
    f'&apikey={av}'
)
j = r.json()
items = j.get('feed', [])
print(f"받은 건수: {len(items)}")

if items:
    # 날짜별 분포
    from collections import Counter
    dates = [it.get('time_published', '')[:8] for it in items]
    by_date = Counter(dates)
    print(f"기간: {min(dates)} ~ {max(dates)}")
    print(f"일별 평균: {len(items) / max(1, len(by_date)):.1f}건/일")
    print(f"최다일: {by_date.most_common(1)}")
    
    # 가장 오래된/최신
    print(f"\n첫 기사: {items[-1].get('time_published','')[:8]} - {items[-1].get('title','')[:60]}")
    print(f"끝 기사: {items[0].get('time_published','')[:8]} - {items[0].get('title','')[:60]}")
else:
    print(f"응답: {str(j)[:300]}")

time.sleep(2)

# SPY 한 달치
print("\n=== SPY 뉴스 (2022-03, limit=1000) ===")
r = requests.get(
    f'https://www.alphavantage.co/query'
    f'?function=NEWS_SENTIMENT'
    f'&tickers=SPY'
    f'&time_from=20220301T0000'
    f'&time_to=20220331T2359'
    f'&limit=1000'
    f'&apikey={av}'
)
j = r.json()
items = j.get('feed', [])
print(f"받은 건수: {len(items)}")
if items:
    from collections import Counter
    dates = [it.get('time_published', '')[:8] for it in items]
    by_date = Counter(dates)
    print(f"기간: {min(dates)} ~ {max(dates)}")
    print(f"일별 평균: {len(items) / max(1, len(by_date)):.1f}건/일")

print("\n=== 잔여 호출 횟수 추정 ===")
print(f"오늘 사용: 2~3회 (테스트), 잔여 ~22회")
