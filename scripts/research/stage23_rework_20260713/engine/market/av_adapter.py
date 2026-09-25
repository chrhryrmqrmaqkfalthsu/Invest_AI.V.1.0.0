"""
AlphaVantage NEWS_SENTIMENT 응답 → 콜랩 article 형식 변환
- AlphaVantage feed item을 콜랩 keyword_filter()가 받을 수 있는 dict로 변환
- 일별 그룹화 지원
"""
import json
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

CACHE_DIR = Path("data/_system/news_cache")


def av_item_to_article(item):
    """
    AlphaVantage feed item을 콜랩 article 형식으로 변환
    
    AlphaVantage 구조:
      {
        "title": "...", "url": "...", "time_published": "20220301T120000",
        "summary": "...", "source": "Reuters", "source_domain": "reuters.com",
        "overall_sentiment_score": 0.15, "overall_sentiment_label": "Bullish",
        "topics": [{"topic": "...", "relevance_score": "..."}],
        "ticker_sentiment": [...]
      }
    
    콜랩 기대 형식:
      {
        "title": "...", "description": "...", "url": "...",
        "source": {"name": "Reuters"}
      }
    """
    return {
        "title": item.get("title", ""),
        "description": item.get("summary", ""),
        "url": item.get("url", ""),
        "source": {"name": item.get("source", "")},
        # 추가 메타 (콜랩은 안 쓰지만 검증/디버깅용)
        "_av_sentiment_score": item.get("overall_sentiment_score"),
        "_av_sentiment_label": item.get("overall_sentiment_label"),
        "_av_topics": [t.get("topic", "") for t in item.get("topics", [])],
        "_time_published": item.get("time_published", ""),
    }


def parse_av_date(time_published):
    """20220301T120000 → date(2022, 3, 1)"""
    try:
        return datetime.strptime(time_published[:8], "%Y%m%d").date()
    except:
        return None


def load_all_cached_articles():
    """
    모든 캐시 파일을 읽어 콜랩 형식 article 리스트 반환
    - 우선순위: daily/ 폴더 (일별, 잘림 없음)
    - fallback: 월별 av_market_YYYYMM.json (limit 1000에 잘림 가능)
    - 같은 날짜에 둘 다 있으면 일별 사용 (월별 무시)
    """
    daily_dir = CACHE_DIR / "daily"
    daily_files = sorted(daily_dir.glob("av_market_*.json")) if daily_dir.exists() else []
    
    all_articles = []
    covered_dates = set()
    
    # 1) 일별 파일 (우선)
    for cf in daily_files:
        try:
            data = json.loads(cf.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"  ⚠️ {cf.name} 로드 실패: {e}")
            continue
        feed = data.get("feed", [])
        # 파일명에서 날짜 추출: av_market_20200601.json → 2020-06-01
        try:
            stem = cf.stem.replace("av_market_", "")
            file_date = datetime.strptime(stem, "%Y%m%d").date()
            covered_dates.add(file_date)
        except:
            file_date = None
        for item in feed:
            article = av_item_to_article(item)
            all_articles.append(article)
    
    # 2) 월별 파일 (일별로 커버 안 된 날짜만)
    monthly_files = sorted(CACHE_DIR.glob("av_market_*.json"))
    for cf in monthly_files:
        # 월별 파일명은 av_market_YYYYMM (6자리), 일별은 YYYYMMDD (8자리)
        stem = cf.stem.replace("av_market_", "")
        if len(stem) != 6:  # 일별 파일이 아닌, 월별만 처리
            continue
        try:
            data = json.loads(cf.read_text(encoding='utf-8'))
        except:
            continue
        feed = data.get("feed", [])
        added = 0
        for item in feed:
            d = parse_av_date(item.get("time_published", ""))
            if d and d in covered_dates:
                continue  # 일별로 이미 커버됨, skip
            article = av_item_to_article(item)
            all_articles.append(article)
            added += 1
    
    return all_articles


def group_articles_by_date(articles):
    """
    article 리스트를 날짜별로 그룹화
    반환: {date: [article, ...]}
    """
    by_date = defaultdict(list)
    for art in articles:
        d = parse_av_date(art.get("_time_published", ""))
        if d:
            by_date[d].append(art)
    return dict(by_date)


def load_articles_by_date():
    """편의 함수: 캐시 로드 + 날짜별 그룹화 한 번에"""
    articles = load_all_cached_articles()
    return group_articles_by_date(articles)


# ============================================================
# 자가 검증
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("AlphaVantage Adapter 검증")
    print("=" * 60)
    
    # 1) 캐시 파일 확인
    cache_files = sorted(CACHE_DIR.glob("av_market_*.json"))
    print(f"\n캐시 파일: {len(cache_files)}개")
    if cache_files:
        print(f"  최초: {cache_files[0].name}")
        print(f"  최종: {cache_files[-1].name}")
    
    # 2) 전체 로드
    print("\n전체 article 로드 중...")
    articles = load_all_cached_articles()
    print(f"  총 article: {len(articles)}건")
    
    # 3) 샘플 변환 검증
    if articles:
        sample = articles[0]
        print(f"\n샘플 article (변환 후):")
        print(f"  title: {sample['title'][:70]}")
        print(f"  source: {sample['source']['name']}")
        print(f"  description: {sample['description'][:100]}")
        print(f"  AV sentiment: {sample.get('_av_sentiment_score')} ({sample.get('_av_sentiment_label')})")
        print(f"  topics: {sample.get('_av_topics')[:3]}")
    
    # 4) 일별 그룹화
    print("\n일별 그룹화...")
    by_date = group_articles_by_date(articles)
    print(f"  커버 일수: {len(by_date)}")
    sorted_dates = sorted(by_date.keys())
    print(f"  기간: {sorted_dates[0]} ~ {sorted_dates[-1]}")
    
    # 5) 일별 분포
    counts = sorted([(d, len(arts)) for d, arts in by_date.items()], key=lambda x: x[1], reverse=True)
    print(f"\n일별 뉴스 수 분포:")
    print(f"  최다: {counts[0][0]} - {counts[0][1]}건")
    print(f"  최소: {counts[-1][0]} - {counts[-1][1]}건")
    print(f"  평균: {sum(c for _, c in counts) / len(counts):.1f}건")
    
    # 6) 콜랩 함수와 호환 테스트
    print("\n콜랩 keyword_filter() 호환 테스트...")
    from colab_v32 import keyword_filter, deduplicate_articles, filter_trusted
    
    # 테스트 일자: 처음 10일치 합쳐서
    test_date = sorted_dates[0]
    test_articles = by_date[test_date][:20]
    
    deduped = deduplicate_articles(test_articles)
    trusted, others = filter_trusted(deduped)
    candidates, neg_filtered = keyword_filter(deduped)
    
    print(f"  테스트 일자: {test_date}")
    print(f"  원본 {len(test_articles)} → 중복제거 {len(deduped)}")
    print(f"  신뢰매체 {len(trusted)}, 기타 {len(others)}")
    print(f"  키워드 매칭 {len(candidates)}, 부정어 차단 {len(neg_filtered)}")
    
    if candidates:
        print(f"\n  매칭된 후보 샘플:")
        for c in candidates[:3]:
            print(f"    - [{c['matched_event_types']}] {c['article']['title'][:60]}")
    
    print("\n" + "=" * 60)
    print("✅ 어댑터 검증 완료")


def list_available_dates():
    """
    캐시된 모든 날짜 리스트 반환 (정렬됨)
    - daily/ 파일명에서 직접 추출 (메모리 효율)
    - 월별 파일은 무시 (일별이 우선)
    """
    daily_dir = CACHE_DIR / "daily"
    if not daily_dir.exists():
        return []
    
    dates = set()
    for cf in daily_dir.glob("av_market_*.json"):
        stem = cf.stem.replace("av_market_", "")
        if len(stem) == 8:  # YYYYMMDD
            try:
                d = datetime.strptime(stem, "%Y%m%d").date()
                dates.add(d)
            except:
                pass
    return sorted(dates)


def load_articles_for_date(d):
    """
    특정 날짜 하나의 article만 로드 (스트리밍)
    - 일별 파일 우선
    - 없으면 월별 파일에서 해당 날짜만 필터링
    """
    daily_path = CACHE_DIR / "daily" / f"av_market_{d.year:04d}{d.month:02d}{d.day:02d}.json"
    if daily_path.exists():
        try:
            data = json.loads(daily_path.read_text(encoding='utf-8'))
            return [av_item_to_article(item) for item in data.get("feed", [])]
        except Exception as e:
            print(f"  ⚠️ {daily_path.name} 로드 실패: {e}")
            return []
    
    # fallback: 월별 파일에서 해당 날짜 추출
    monthly_path = CACHE_DIR / f"av_market_{d.year:04d}{d.month:02d}.json"
    if monthly_path.exists():
        try:
            data = json.loads(monthly_path.read_text(encoding='utf-8'))
            articles = []
            for item in data.get("feed", []):
                art_date = parse_av_date(item.get("time_published", ""))
                if art_date == d:
                    articles.append(av_item_to_article(item))
            return articles
        except:
            return []
    
    return []

