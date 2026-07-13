"""
종목별 실시간 뉴스 수집 + GPT 호재/악재 분석
- NewsAPI에서 최근 7일 뉴스 fetch
- GPT-4o-mini로 호재/악재/중립 + 강도(-10~+10) 분류
- 6시간 캐시 (data/_system/per_ticker_news_cache.json)
- 상위 3건 평균으로 종목 sentiment 점수 계산
"""
import os
import json
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from engine.core.logger import get_logger
from engine.core.config import config as cfg
from openai import OpenAI

log = get_logger("per_ticker_news")

# ============================================================
# 설정
# ============================================================
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CACHE_TTL_HOURS = 6
TOP_N = 3                # 상위 3건 평균
FETCH_LIMIT = 10         # NewsAPI에서 가져올 건수
DAYS_BACK = 7            # 최근 며칠치 뉴스

CACHE_PATH = cfg.system_dir() / "per_ticker_news_cache.json"

# ============================================================
# 종목별 검색 쿼리 매핑
# ============================================================
TICKER_QUERY_MAP = {
    # 미국 지수 추종 ETF
    "379800": ("(S&P 500) OR (\"S&P500\")", "en"),       # KODEX 미국S&P500
    "360750": ("(S&P 500) OR (\"S&P500\")", "en"),       # TIGER 미국S&P500
    "143850": ("(S&P 500) OR (\"S&P500\")", "en"),       # TIGER 미국S&P500선물(H)
    "225030": ("(S&P 500) OR (\"S&P500\")", "en"),       # 인버스도 동일 (부호는 evaluator에서 반전)
    "133690": ("(Nasdaq 100) OR (\"Nasdaq100\") OR QQQ", "en"),  # TIGER 미국나스닥100
    "200030": ("(Nasdaq 100) OR (\"Nasdaq100\") OR QQQ", "en"),  # KODEX 미국나스닥100

    # 한국 지수 추종 ETF (영어 외신 검색이 더 효과적)
    "069500": ("(KOSPI) OR (\"Korea stock\") OR (\"Korean equities\")", "en"),
    "102110": ("(KOSPI) OR (\"Korea stock\") OR (\"Korean equities\")", "en"),
    "152100": ("(KOSPI) OR (\"Korea stock\") OR (\"Korean equities\")", "en"),
    "278530": ("(KOSPI) OR (\"Korea stock\") OR (\"Korean equities\")", "en"),  # 인버스
    "278540": ("(MSCI Korea) OR (\"Korea stock\") OR (\"Korean equities\")", "en"),
    "251340": ("(KOSDAQ) OR (\"Korean tech\") OR (\"Korea small cap\")", "en"),  # 인버스
}

# ============================================================
# 캐시 I/O
# ============================================================
def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"cache load failed: {e}")
        return {}

def _save_cache(cache: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"cache save failed: {e}")

def _cache_valid(entry: dict) -> bool:
    """6시간 이내인지 확인"""
    try:
        cached_at = datetime.fromisoformat(entry["cached_at"])
        return (datetime.now() - cached_at) < timedelta(hours=CACHE_TTL_HOURS)
    except Exception:
        return False

# ============================================================
# 검색 쿼리 결정
# ============================================================
def _get_search_query(ticker: str, meta=None) -> tuple:
    """매핑 우선, 없으면 meta.name 기반 자동 추출"""
    if ticker in TICKER_QUERY_MAP:
        return TICKER_QUERY_MAP[ticker]
    # 미국 주식: 회사명 그대로
    if meta and meta.asset_type == "us_stock":
        name = meta.name or ticker
        # "Apple Inc." → "Apple", "NVIDIA Corporation" → "Nvidia"
        for suffix in [" Inc.", " Corporation", " Corp.", " Ltd.", " Co.", ", Inc."]:
            name = name.replace(suffix, "")
        return (name.strip(), "en")
    # 그 외: ticker 그대로
    return (ticker, "en")

# ============================================================
# NewsAPI fetch
# ============================================================
def fetch_ticker_news(ticker: str, meta=None, days: int = DAYS_BACK, limit: int = FETCH_LIMIT) -> list:
    """종목 관련 최근 뉴스 fetch"""
    if not NEWSAPI_KEY:
        log.warning("NEWSAPI_KEY 없음")
        return []

    query, lang = _get_search_query(ticker, meta)
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    trusted_en = ("bloomberg.com,reuters.com,cnbc.com,wsj.com,ft.com,"
                  "marketwatch.com,finance.yahoo.com,barrons.com,investors.com")

    params = {
        "q": query,
        "language": lang,
        "sortBy": "relevancy",
        "from": from_date,
        "pageSize": limit,
        "apiKey": NEWSAPI_KEY,
    }
    if lang == "en":
        params["domains"] = trusted_en

    try:
        r = requests.get("https://newsapi.org/v2/everything", params=params, timeout=15)
        j = r.json()
        if j.get("status") != "ok":
            log.warning(f"NewsAPI 에러 [{ticker}]: {j.get('message', '')}")
            return []
        articles = j.get("articles", [])
        # 신뢰 도메인에서 부족하면 한 번 더 (도메인 제한 풀어서)
        if len(articles) < 3 and "domains" in params:
            params.pop("domains")
            r2 = requests.get("https://newsapi.org/v2/everything", params=params, timeout=15)
            j2 = r2.json()
            if j2.get("status") == "ok":
                articles = j2.get("articles", [])
        log.info(f"[{ticker}] {query} ({lang}) → {len(articles)}건")
        return articles
    except Exception as e:
        log.warning(f"fetch failed [{ticker}]: {e}")
        return []

# ============================================================
# GPT 호재/악재 분석
# ============================================================
GPT_PROMPT_TEMPLATE = """다음은 {ticker}({company}) 관련 최근 뉴스 {n}건입니다.
각 뉴스가 해당 종목 주가에 미칠 영향을 평가하세요.

뉴스 목록:
{news_block}

각 뉴스에 대해 JSON 객체로 답변하세요 (배열 아님, 반드시 객체):
{{"items": [
  {{"idx": 1, "sentiment": "강한_호재|호재|중립|악재|강한_악재", "score": -10~+10, "reason": "10자 이내"}},
  {{"idx": 2, ...}}
]}}

점수 기준:
- 강한 호재 (+7~+10): 큰 계약, 어닝 서프라이즈, 정부 지원, M&A 호재
- 호재 (+3~+6): 신제품, 파트너십, 긍정적 전망
- 중립 (-2~+2): 일반 보도, 단순 정보
- 악재 (-3~-6): 매출 둔화, 경쟁 심화, 부정적 전망
- 강한 악재 (-7~-10): 소송, 회계부정, CEO 사임, 큰 손실

JSON만 출력하세요."""

def analyze_news_sentiment(articles: list, ticker: str, company: str = "") -> dict:
    """GPT로 뉴스 sentiment 분석"""
    if not articles:
        return {
            "sentiment_score": 0.0,
            "normalized_score": 0.0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "total_analyzed": 0,
            "samples": [],
        }
    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY 없음, 중립 반환")
        return {
            "sentiment_score": 0.0,
            "normalized_score": 0.0,
            "normalized_score": 0.0,
            "bullish_count": 0, "bearish_count": 0, "neutral_count": len(articles),
            "total_analyzed": 0, "samples": [],
        }

    # 뉴스 블록 구성
    news_lines = []
    for i, a in enumerate(articles[:FETCH_LIMIT], 1):
        title = (a.get("title") or "")[:200]
        desc = (a.get("description") or "")[:300]
        src = (a.get("source") or {}).get("name", "")
        news_lines.append(f"[{i}] ({src}) {title}\n    {desc}")
    news_block = "\n\n".join(news_lines)

    prompt = GPT_PROMPT_TEMPLATE.format(
        ticker=ticker, company=company or ticker,
        n=len(articles[:FETCH_LIMIT]), news_block=news_block
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        txt = resp.choices[0].message.content.strip()
        parsed = json.loads(txt)
        # {"items": [...]} 또는 배열 자체 둘 다 허용
        if isinstance(parsed, dict):
            items = parsed.get("items") or parsed.get("results") or parsed.get("data") or []
            if not items and len(parsed) == 1:
                # {"news": [...]} 같이 키 1개인 경우
                items = list(parsed.values())[0] if isinstance(list(parsed.values())[0], list) else []
        elif isinstance(parsed, list):
            items = parsed
        else:
            items = []
    except Exception as e:
        log.warning(f"GPT 분석 실패 [{ticker}]: {e}")
        return {
            "sentiment_score": 0.0,
            "normalized_score": 0.0,
            "normalized_score": 0.0,
            "bullish_count": 0, "bearish_count": 0, "neutral_count": len(articles),
            "total_analyzed": 0, "samples": [],
        }

    # 집계
    bullish = bearish = neutral = 0
    scores = []
    samples = []
    for it in items:
        score = float(it.get("score", 0))
        sentiment = it.get("sentiment", "중립")
        idx = int(it.get("idx", 0)) - 1
        scores.append(score)
        if score >= 3:
            bullish += 1
        elif score <= -3:
            bearish += 1
        else:
            neutral += 1
        # 샘플 (원본 제목 매칭)
        if 0 <= idx < len(articles):
            art = articles[idx]
            samples.append({
                "title": (art.get("title") or "")[:120],
                "source": (art.get("source") or {}).get("name", ""),
                "sentiment": sentiment,
                "score": score,
                "reason": it.get("reason", ""),
                "url": art.get("url", ""),
            })

    # 상위 3건 평균 (절댓값 기준)
    top_scores = sorted(scores, key=abs, reverse=True)[:TOP_N]
    top_avg = sum(top_scores) / len(top_scores) if top_scores else 0.0

    log.info(
        f"[{ticker}] sentiment={top_avg:+.2f} "
        f"(호재 {bullish}/악재 {bearish}/중립 {neutral})"
    )

    # -10 ~ +10 → -1 ~ +1 정규화 (evaluator 입력용)
    normalized = max(-1.0, min(1.0, top_avg / 5.0))  # ±5 이상이면 클리핑

    return {
        "sentiment_score": round(top_avg, 2),          # -10 ~ +10 (raw)
        "normalized_score": round(normalized, 3),      # -1.0 ~ +1.0 (evaluator용)
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "total_analyzed": len(items),
        "samples": samples[:5],
    }

# ============================================================
# 원스톱: get_news_score
# ============================================================
def get_news_score(ticker: str, meta=None, force_refresh: bool = False) -> dict:
    """캐시 → fetch → analyze 통합. 6h 캐시 활용."""
    cache = _load_cache()
    
    if not force_refresh and ticker in cache and _cache_valid(cache[ticker]):
        log.info(f"[{ticker}] 캐시 히트 (6h TTL)")
        return cache[ticker]["data"]

    # 신규 fetch + 분석
    articles = fetch_ticker_news(ticker, meta=meta)
    company = meta.name if meta else ticker
    data = analyze_news_sentiment(articles, ticker, company)

    # 캐시 저장
    cache[ticker] = {
        "cached_at": datetime.now().isoformat(),
        "data": data,
    }
    _save_cache(cache)

    return data


# ============================================================
# 자가 검증
# ============================================================
if __name__ == "__main__":
    from engine.adapters.factory import get_adapter

    print("=" * 60)
    print("per_ticker_news 자가 검증")
    print("=" * 60)

    for ticker in ["NVDA", "379800", "278530"]:
        print(f"\n--- {ticker} ---")
        try:
            adapter = get_adapter(ticker)
            data = get_news_score(ticker, meta=adapter.meta, force_refresh=True)
            print(f"  sentiment_score: {data['sentiment_score']:+.2f}")
            print(f"  호재: {data['bullish_count']}, 악재: {data['bearish_count']}, 중립: {data['neutral_count']}")
            print(f"  주요 뉴스:")
            for s in data["samples"][:3]:
                print(f"    • [{s['source']}] {s['title'][:80]}")
                print(f"      → {s['sentiment']} ({s['score']:+.0f}): {s['reason']}")
        except Exception as e:
            print(f"  ❌ {e}")

    print("\n=== 캐시 히트 테스트 ===")
    data = get_news_score("NVDA", meta=get_adapter("NVDA").meta)
    print(f"NVDA 재호출 (캐시 히트): {data['sentiment_score']:+.2f}")
