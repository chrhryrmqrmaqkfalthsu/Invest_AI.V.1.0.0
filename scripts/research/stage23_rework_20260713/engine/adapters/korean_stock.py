"""
한국 개별주 어댑터
- 005930 (삼성전자) 등 개별 주식
- pykrx 시세, DART 공시, 어닝 일정 (네이버 금융)
- 뉴스: NewsAPI + 네이버 금융 헤드라인
"""
import os
from datetime import datetime, time, timedelta
from typing import Optional

import requests

from engine.adapters.base import AssetAdapter, AssetMeta, TradingHours
from engine.core.logger import get_logger

log = get_logger("korean_stock")


# ---------- 종목명 ----------
_KR_STOCK_NAME_CACHE: dict[str, str] = {}


def get_korean_stock_name(ticker: str) -> str:
    if ticker in _KR_STOCK_NAME_CACHE:
        return _KR_STOCK_NAME_CACHE[ticker]
    try:
        from pykrx import stock as pykrx_stock
        name = pykrx_stock.get_market_ticker_name(ticker)
        if name:
            _KR_STOCK_NAME_CACHE[ticker] = name
            return name
    except Exception as e:
        log.debug(f"pykrx stock name lookup failed for {ticker}: {e}")
    name = f"한국주식_{ticker}"
    _KR_STOCK_NAME_CACHE[ticker] = name
    return name


# ---------- 매매시간 (한국 주식 = ETF와 동일) ----------
def korean_stock_trading_hours() -> TradingHours:
    return TradingHours(
        timezone="Asia/Seoul",
        open_time=time(9, 0),
        close_time=time(15, 30),
        pre_auction_end=time(9, 5),
        post_auction_start=time(15, 20),
        weekdays_only=True,
    )


# ---------- DART 공시 ----------
DART_API_BASE = "https://opendart.fss.or.kr/api"


def _dart_corp_code(ticker: str) -> Optional[str]:
    """DART는 종목코드가 아닌 자체 corp_code를 씀. 변환 매핑 필요."""
    # TODO: DART corpCode.xml 다운로드 후 매핑 (Step 7에서 구현)
    # 일단 .env에 DART_API_KEY 있는지만 확인하고 skip
    return None


# ---------- 어댑터 ----------
class KoreanStockAdapter(AssetAdapter):

    def build_meta(self) -> AssetMeta:
        base = self.ticker.split(".")[0]
        name = get_korean_stock_name(base)
        return AssetMeta(
            ticker=base,
            name=name,
            asset_type="korean_stock",
            direction="long",          # 개별주는 기본 long (공매도는 미지원)
            currency="KRW",
            market="KRX",
            trading_hours=korean_stock_trading_hours(),
            requires_disclosure=True,
            requires_earnings=True,
            extra={"raw_ticker": self.ticker},
        )

    def fetch_news(self, days: int = 7) -> list[dict]:
        """NewsAPI로 종목명 기반 뉴스 검색"""
        api_key = os.environ.get("NEWSAPI_KEY", "").strip()
        if not api_key:
            return []

        name = self.meta.name
        from_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": name,
                    "from": from_date,
                    "language": "ko",
                    "sortBy": "publishedAt",
                    "pageSize": 10,
                    "apiKey": api_key,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            articles = data.get("articles", [])
            return [
                {
                    "title": a.get("title", ""),
                    "source": (a.get("source") or {}).get("name", ""),
                    "url": a.get("url", ""),
                    "published_at": a.get("publishedAt", ""),
                    "description": a.get("description", ""),
                }
                for a in articles
            ]
        except Exception as e:
            log.warning(f"NewsAPI fetch failed for {self.ticker}: {e}")
            return []

    def fetch_disclosures(self, days: int = 30) -> list[dict]:
        """
        DART 공시 조회 (오늘로부터 days일 이내).
        DART_API_KEY 없으면 빈 리스트.
        """
        api_key = os.environ.get("DART_API_KEY", "").strip()
        if not api_key:
            log.debug(f"{self.ticker}: DART_API_KEY not set")
            return []

        corp_code = _dart_corp_code(self.ticker)
        if not corp_code:
            log.debug(f"{self.ticker}: DART corp_code not mapped yet")
            return []

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        try:
            r = requests.get(
                f"{DART_API_BASE}/list.json",
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp_code,
                    "bgn_de": start,
                    "end_de": end,
                    "page_count": 50,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("list", [])
            return [
                {
                    "title": it.get("report_nm", ""),
                    "date": it.get("rcept_dt", ""),
                    "rcept_no": it.get("rcept_no", ""),
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it.get('rcept_no','')}",
                }
                for it in items
            ]
        except Exception as e:
            log.warning(f"DART fetch failed for {self.ticker}: {e}")
            return []

    def fetch_earnings_calendar(self) -> list[dict]:
        """
        한국 주식 어닝 일정. 일단 빈 리스트 (네이버 금융 크롤링은 Step 7에서).
        """
        return []


if __name__ == "__main__":
    print("=" * 50)
    print("KoreanStockAdapter 테스트")
    print("=" * 50)
    for tk in ["005930", "000660", "035420"]:  # 삼성전자, SK하이닉스, NAVER
        a = KoreanStockAdapter(tk)
        m = a.meta
        print(f"\n{tk}")
        print(f"  이름: {m.name}")
        print(f"  방향: {m.direction}, 자산타입: {m.asset_type}")
        print(f"  공시필요: {m.requires_disclosure}, 어닝필요: {m.requires_earnings}")
        print(f"  매매시간: {m.trading_hours.to_dict()}")
        cur = a.current_price()
        print(f"  현재가: {cur}")
        disc = a.fetch_disclosures(days=7)
        print(f"  최근 공시: {len(disc)}건 (DART 키 없으면 0)")
