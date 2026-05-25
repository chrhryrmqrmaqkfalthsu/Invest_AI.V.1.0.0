"""
미국 ETF 어댑터
- SPY, QQQ, VOO 등
- yfinance 시세 + Finnhub 뉴스 (선택)
"""
import os
from datetime import datetime, time, timedelta
from typing import Optional

import requests

from engine.adapters.base import AssetAdapter, AssetMeta, TradingHours
from engine.core.logger import get_logger

log = get_logger("us_etf")


# 알려진 미국 ETF (인버스 판별용)
INVERSE_US_ETF = {
    "SH", "SDS", "SPXU", "SQQQ", "PSQ", "QID", "DOG", "DXD",
    "TZA", "TWM", "RWM", "SOXS", "TECS",
}

ETF_NAME_FALLBACK = {
    "SPY": "SPDR S&P 500 ETF",
    "QQQ": "Invesco QQQ Trust",
    "VOO": "Vanguard S&P 500 ETF",
    "IVV": "iShares Core S&P 500",
    "VTI": "Vanguard Total Stock Market",
    "DIA": "SPDR Dow Jones",
    "IWM": "iShares Russell 2000",
    "SH": "ProShares Short S&P500 (Inverse)",
    "SQQQ": "ProShares UltraPro Short QQQ (3x Inverse)",
}


def us_market_trading_hours() -> TradingHours:
    """미국 정규장: 09:30~16:00 ET (서머타임은 OS tz가 처리)"""
    return TradingHours(
        timezone="America/New_York",
        open_time=time(9, 30),
        close_time=time(16, 0),
        pre_auction_end=None,
        post_auction_start=None,
        weekdays_only=True,
    )


def _yf_get_name(ticker: str) -> str:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        return info.get("longName") or info.get("shortName") or ETF_NAME_FALLBACK.get(ticker, ticker)
    except Exception:
        return ETF_NAME_FALLBACK.get(ticker, ticker)


class USETFAdapter(AssetAdapter):

    def build_meta(self) -> AssetMeta:
        tk = self.ticker.upper()
        name = _yf_get_name(tk)
        direction = "short" if tk in INVERSE_US_ETF else "long"
        return AssetMeta(
            ticker=tk,
            name=name,
            asset_type="us_etf",
            direction=direction,
            currency="USD",
            market="NYSE/NASDAQ",
            trading_hours=us_market_trading_hours(),
            requires_disclosure=False,
            requires_earnings=False,
            extra={},
        )

    def fetch_news(self, days: int = 7) -> list[dict]:
        """Finnhub 뉴스 (무료 60 req/min)"""
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
            log.warning(f"Finnhub fetch failed for {self.ticker}: {e}")
            return []


if __name__ == "__main__":
    print("=" * 50)
    print("USETFAdapter 테스트")
    print("=" * 50)
    for tk in ["SPY", "QQQ", "SH"]:
        a = USETFAdapter(tk)
        m = a.meta
        print(f"\n{tk}")
        print(f"  이름: {m.name}")
        print(f"  방향: {m.direction}, 통화: {m.currency}")
        print(f"  매매시간: {m.trading_hours.to_dict()}")
        print(f"  장 열림? {a.is_market_open()}")

