"""
AlphaVantage NEWS_SENTIMENT 캐시 → 일별 집계 CSV 변환
출력: data/_system/sentiment_daily.csv
컬럼: date, news_count, sentiment_avg, sentiment_std, bullish_ratio, bearish_ratio, top_topics
"""
import json
import pandas as pd
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

CACHE_DIR = Path("data/_system/news_cache")
OUTPUT = Path("data/_system/sentiment_daily.csv")

def parse_time(t):
    """20220301T120000 → date"""
    try:
        return datetime.strptime(t[:8], "%Y%m%d").date()
    except:
        return None

def aggregate():
    cache_files = sorted(CACHE_DIR.glob("av_market_*.json"))
    if not cache_files:
        print("❌ 캐시 파일 없음")
        return
    
    print(f"캐시 파일: {len(cache_files)}개")
    
    # 일별 누적
    daily = defaultdict(list)  # date -> [sentiment scores]
    daily_topics = defaultdict(Counter)  # date -> topic counter
    daily_labels = defaultdict(Counter)  # date -> bullish/bearish counter
    
    total_items = 0
    for cf in cache_files:
        data = json.loads(cf.read_text())
        feed = data.get("feed", [])
        for item in feed:
            d = parse_time(item.get("time_published", ""))
            if not d:
                continue
            score = item.get("overall_sentiment_score")
            if score is None:
                continue
            try:
                score = float(score)
            except:
                continue
            
            daily[d].append(score)
            label = item.get("overall_sentiment_label", "")
            daily_labels[d][label] += 1
            
            # 토픽 추출
            for t in item.get("topics", []):
                tname = t.get("topic", "") if isinstance(t, dict) else str(t)
                if tname:
                    daily_topics[d][tname] += 1
            total_items += 1
    
    print(f"총 뉴스: {total_items}건")
    print(f"커버 일수: {len(daily)}일")
    
    # DataFrame 생성
    rows = []
    for d in sorted(daily.keys()):
        scores = daily[d]
        labels = daily_labels[d]
        topics = daily_topics[d].most_common(3)
        
        bullish = labels.get("Bullish", 0) + labels.get("Somewhat-Bullish", 0)
        bearish = labels.get("Bearish", 0) + labels.get("Somewhat-Bearish", 0)
        total_labeled = bullish + bearish + labels.get("Neutral", 0)
        
        s = pd.Series(scores)
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "news_count": len(scores),
            "sentiment_avg": round(s.mean(), 4),
            "sentiment_std": round(s.std() if len(s) > 1 else 0, 4),
            "sentiment_min": round(s.min(), 4),
            "sentiment_max": round(s.max(), 4),
            "bullish_ratio": round(bullish / max(1, total_labeled), 3),
            "bearish_ratio": round(bearish / max(1, total_labeled), 3),
            "top_topics": ",".join([t[0] for t in topics]),
        })
    
    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT, index=False)
    
    print(f"\n✅ 저장: {OUTPUT}")
    print(f"기간: {df['date'].min()} ~ {df['date'].max()}")
    print(f"행수: {len(df)}")
    print(f"\n샘플 (최근 5일):")
    print(df.tail(5).to_string(index=False))
    print(f"\n통계:")
    print(f"  sentiment_avg 범위: {df['sentiment_avg'].min():.3f} ~ {df['sentiment_avg'].max():.3f}")
    print(f"  뉴스/일 평균: {df['news_count'].mean():.1f}건")

if __name__ == "__main__":
    aggregate()
