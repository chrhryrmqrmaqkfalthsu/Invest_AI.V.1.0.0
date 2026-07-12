"""
미국 개별주 어댑터
- AAPL, NVDA, TSLA 등
- yfinance 시세 + 어닝 캘린더
- SEC EDGAR 공시 (무료, 키 불필요)
- Finnhub 뉴스 + 애널리스트 의견
"""
import os
from datetime import datetime, time, timedelta
from typing import Optional

import requests

from engine.adapters.base import AssetAdapter, AssetMeta, TradingHours
from engine.adapters.us_etf import us_market_trading_hours, _yf_get_name
from engine.core.logger import get_logger

log = get_logger("us_stock")


SEC_USER_AGENT = "KingMaker Trading Bot contact@example.com"


class USStockAdapter(AssetAdapter):

    def build_meta(self) -> AssetMeta:
        tk = self.ticker.upper()
        name = _yf_get_name(tk)
        return AssetMeta(
            ticker=tk,
            name=name,
            asset_type="us_stock",
            direction="long",
            currency="USD",
            market="NYSE/NASDAQ",
            trading_hours=us_market_trading_hours(),
            requires_disclosure=True,
            requires_earnings=True,
            extra={},
        )

    def fetch_news(self, days: int = 7) -> list[dict]:
        api_key = os.environ.get("FINNHUB_API_KEY", "").strip()
        if not api_key:
            return []
        end = datetime.utcnow().strftime("%Y-%m-%d")
        start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": self.ticker.upper(),
                    "from": start,
                    "to": end,
                    "token": api_key,
                },
                timeout=10,
            )
            r.raise_for_status()
            items = r.json() or []
            return [
                {
                    "title": it.get("headline", ""),
                    "source": it.get("source", ""),
                    "url": it.get("url", ""),
                    "published_at": datetime.fromtimestamp(it.get("datetime", 0)).isoformat() if it.get("datetime") else "",
                    "description": it.get("summary", ""),
                }
                for it in items[:10]
            ]
        except Exception as e:
            log.warning(f"Finnhub news fetch failed for {self.ticker}: {e}")
            return []

    def fetch_disclosures(self, days: int = 30) -> list[dict]:
        """SEC EDGAR — 키 불필요, User-Agent 필수"""
        tk = self.ticker.upper()
        try:
            # CIK 조회
            r = requests.get(
                "https://www.sec.gov/cgi-bin/browse-edgar",
                params={"action": "getcompany", "CIK": tk, "type": "8-K", "dateb": "", "owner": "include", "count": "20", "output": "atom"},
                headers={"User-Agent": SEC_USER_AGENT},
                timeout=10,
            )
            r.raise_for_status()
            # 간단한 파싱 (자세한 파싱은 Step 7 disclosure_fetcher.py에서)
            text = r.text
            entries = []
            # Atom feed의 <entry> 태그 개수만 추출
            count = text.count("<entry>")
            log.info(f"{tk}: SEC EDGAR 8-K filings found ~{count} entries")
            # 단순 응답 (상세 파싱은 다음 모듈에서)
            return [{"source": "SEC_EDGAR", "type": "8-K", "raw_count": count}]
        except Exception as e:
            log.warning(f"SEC EDGAR fetch failed for {tk}: {e}")
            return []

    def fetch_earnings_calendar(self) -> list[dict]:
        """yfinance 어닝 일정"""
        try:
            import yfinance as yf
            t = yf.Ticker(self.ticker.upper())
            cal = t.calendar
            if cal is None:
                return []
            if isinstance(cal, dict):
                return [{"event": "earnings", "data": {k: str(v) for k, v in cal.items()}}]
            return []
        except Exception as e:
            log.debug(f"earnings calendar failed for {self.ticker}: {e}")
            return []

    def fetch_analyst_opinions(self) -> list[dict]:
        """yfinance 애널리스트 추천"""
        try:
            import yfinance as yf
            t = yf.Ticker(self.ticker.upper())
            rec = t.recommendations
            if rec is None or len(rec) == 0:
                return []
            # 최근 5개
            recent = rec.tail(5) if hasattr(rec, "tail") else []
            return [{"recent": str(recent)}]
        except Exception as e:
            log.debug(f"analyst opinions failed for {self.ticker}: {e}")
            return []


if __name__ == "__main__":
    print("=" * 50)
    print("USStockAdapter 테스트")
    print("=" * 50)
    for tk in ["AAPL", "NVDA"]:
        a = USStockAdapter(tk)
        m = a.meta
        print(f"\n{tk}")
        print(f"  이름: {m.name}")
        print(f"  자산타입: {m.asset_type}, 공시필요: {m.requires_disclosure}")
        print(f"  매매시간: {m.trading_hours.to_dict()}")
        disc = a.fetch_disclosures(days=30)
        print(f"  SEC 공시: {disc}")
