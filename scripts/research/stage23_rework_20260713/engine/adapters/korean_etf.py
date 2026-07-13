"""
한국 ETF 어댑터
- 379800 (KODEX 미국S&P500), 360750 (TIGER 미국S&P500), 225030 (TIGER 인버스) 등
- pykrx 우선, yfinance fallback
- 뉴스: NewsAPI / 네이버 금융 (헤드라인)
- 공시 없음, 어닝 없음
"""
import os
from datetime import datetime, time, timedelta
from typing import Optional

import requests

from engine.adapters.base import AssetAdapter, AssetMeta, TradingHours
from engine.core.logger import get_logger

log = get_logger("korean_etf")


# ---------- 종목명 캐시 ----------
_KR_NAME_CACHE: dict[str, str] = {}


def get_korean_etf_name(ticker: str) -> str:
    if ticker in _KR_NAME_CACHE:
        return _KR_NAME_CACHE[ticker]
    try:
        from pykrx import stock as pykrx_stock
        name = pykrx_stock.get_etf_ticker_name(ticker)
        if name:
            _KR_NAME_CACHE[ticker] = name
            return name
    except Exception as e:
        log.debug(f"pykrx name lookup failed for {ticker}: {e}")

    # 폴백: 알려진 종목명
    fallback = {
        "379800": "KODEX 미국S&P500",
        "360750": "TIGER 미국S&P500",
        "225030": "TIGER 미국S&P500선물인버스(H)",
        "069500": "KODEX 200",
        "102110": "TIGER 200",
        "152100": "ARIRANG 200",
        "133690": "TIGER 미국나스닥100",
        "143850": "TIGER 미국S&P500선물(H)",
        "200030": "KODEX 미국나스닥100",
        "278530": "KODEX 200선물인버스",
        "278540": "KODEX MSCI Korea TR",
        "251340": "KODEX 코스닥150선물인버스",
    }
    name = fallback.get(ticker, f"한국ETF_{ticker}")
    _KR_NAME_CACHE[ticker] = name
    return name


# ---------- 인버스 종목 판별 ----------
INVERSE_KEYWORDS = ["인버스", "선물인버스", "INVERSE"]


def is_inverse_etf(name: str) -> bool:
    return any(k in name.upper() or k in name for k in INVERSE_KEYWORDS)


# ---------- 한국 ETF 매매시간 ----------
def korean_etf_trading_hours() -> TradingHours:
    return TradingHours(
        timezone="Asia/Seoul",
        open_time=time(9, 0),
        close_time=time(15, 30),
        pre_auction_end=time(9, 5),       # 09:00~09:05 동시호가 제외
        post_auction_start=time(15, 20),  # 15:20~15:30 동시호가 제외
        weekdays_only=True,
    )


# ---------- 어댑터 ----------
class KoreanETFAdapter(AssetAdapter):
    """한국 상장 ETF 어댑터"""

    def build_meta(self) -> AssetMeta:
        base = self.ticker.split(".")[0]
        name = get_korean_etf_name(base)
        direction = "short" if is_inverse_etf(name) else "long"
        return AssetMeta(
            ticker=base,
            name=name,
            asset_type="korean_etf",
            direction=direction,
            currency="KRW",
            market="KRX",
            trading_hours=korean_etf_trading_hours(),
            requires_disclosure=False,
            requires_earnings=False,
            extra={"raw_ticker": self.ticker},
        )

    def fetch_news(self, days: int = 7) -> list[dict]:
        """
        NewsAPI로 ETF 관련 뉴스 헤드라인 수집.
        키 없으면 빈 리스트 반환.
        """
        api_key = os.environ.get("NEWSAPI_KEY", "").strip()
        if not api_key:
            log.debug("NEWSAPI_KEY not set, skipping news fetch")
            return []

        # ETF의 추종 지수 기반 검색어 생성
        name = self.meta.name
        if "S&P500" in name or "S&P 500" in name:
            query = "S&P 500 OR S&P500"
        elif "나스닥" in name or "NASDAQ" in name:
            query = "NASDAQ 100 OR Nasdaq"
        elif "200" in name:
            query = "KOSPI 200"
        elif "코스닥" in name:
            query = "KOSDAQ"
        else:
            query = name

        from_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "from": from_date,
                    "language": "en",
                    "sortBy": "relevancy",
                    "pageSize": 10,
                    "apiKey": api_key,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            articles = data.get("articles", [])
            results = [
                {
                    "title": a.get("title", ""),
                    "source": (a.get("source") or {}).get("name", ""),
                    "url": a.get("url", ""),
                    "published_at": a.get("publishedAt", ""),
                    "description": a.get("description", ""),
                }
                for a in articles
            ]
            log.info(f"{self.ticker}: fetched {len(results)} news items")
            return results
        except Exception as e:
            log.warning(f"NewsAPI fetch failed for {self.ticker}: {e}")
            return []


if __name__ == "__main__":
    print("=" * 50)
    print("KoreanETFAdapter 테스트")
    print("=" * 50)
    for tk in ["379800", "360750", "225030"]:
        a = KoreanETFAdapter(tk)
        m = a.meta
        print(f"\n{tk}")
        print(f"  이름: {m.name}")
        print(f"  방향: {m.direction}")
        print(f"  통화: {m.currency}, 시장: {m.market}")
        print(f"  매매시간: {m.trading_hours.to_dict()}")
        print(f"  장 열림? {a.is_market_open()}")
        cur = a.current_price()
        print(f"  현재가: {cur}")
