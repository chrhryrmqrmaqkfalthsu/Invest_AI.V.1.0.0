"""
어댑터 베이스 클래스
- 모든 자산 어댑터가 상속받는 공통 인터페이스
- 시세 로딩, 매매시간 판별, 자산 메타 제공
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from engine.core.data_loader import load_ohlcv, get_current_price
from engine.core.indicators import calc_indicators
from engine.core.logger import get_logger

log = get_logger("adapter")


@dataclass
class TradingHours:
    timezone: str
    open_time: time
    close_time: time
    pre_auction_end: Optional[time] = None
    post_auction_start: Optional[time] = None
    weekdays_only: bool = True

    def is_open(self, now: Optional[datetime] = None) -> bool:
        tz = ZoneInfo(self.timezone)
        now_local = (now or datetime.now(tz)).astimezone(tz)
        if self.weekdays_only and now_local.weekday() >= 5:
            return False
        t = now_local.time()
        if not (self.open_time <= t <= self.close_time):
            return False
        if self.pre_auction_end and t < self.pre_auction_end:
            return False
        if self.post_auction_start and t >= self.post_auction_start:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "timezone": self.timezone,
            "open": self.open_time.strftime("%H:%M"),
            "close": self.close_time.strftime("%H:%M"),
            "pre_auction_end": self.pre_auction_end.strftime("%H:%M") if self.pre_auction_end else None,
            "post_auction_start": self.post_auction_start.strftime("%H:%M") if self.post_auction_start else None,
        }


@dataclass
class AssetMeta:
    ticker: str
    name: str
    asset_type: str
    direction: str
    currency: str
    market: str
    trading_hours: TradingHours
    requires_disclosure: bool = False
    requires_earnings: bool = False
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "asset_type": self.asset_type,
            "direction": self.direction,
            "currency": self.currency,
            "market": self.market,
            "trading_hours": self.trading_hours.to_dict(),
            "requires_disclosure": self.requires_disclosure,
            "requires_earnings": self.requires_earnings,
            "extra": self.extra,
        }


class AssetAdapter(ABC):
    def __init__(self, ticker: str):
        self.ticker = ticker
        self._meta: Optional[AssetMeta] = None

    @abstractmethod
    def build_meta(self) -> AssetMeta:
        ...

    @abstractmethod
    def fetch_news(self, days: int = 7) -> list[dict]:
        ...

    @property
    def meta(self) -> AssetMeta:
        if self._meta is None:
            self._meta = self.build_meta()
        return self._meta

    def is_market_open(self, now: Optional[datetime] = None) -> bool:
        return self.meta.trading_hours.is_open(now)

    def load_history(self, years: int = 5) -> pd.DataFrame:
        df = load_ohlcv(self.ticker, years=years)
        df = calc_indicators(df)
        log.info(f"{self.ticker}: history loaded ({len(df)} rows, {len(df.columns)} cols)")
        return df

    def current_price(self) -> Optional[float]:
        return get_current_price(self.ticker)

    def fetch_disclosures(self, days: int = 30) -> list[dict]:
        return []

    def fetch_earnings_calendar(self) -> list[dict]:
        return []

    def fetch_analyst_opinions(self) -> list[dict]:
        return []

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.ticker}>"


def detect_asset_type(ticker: str) -> str:
    base = ticker.split(".")[0].upper()
    if base.isdigit() and len(base) == 6:
        return "korean"
    return "us"


_KR_ETF_CACHE: Optional[set[str]] = None


def get_korean_etf_codes() -> set[str]:
    global _KR_ETF_CACHE
    if _KR_ETF_CACHE is not None:
        return _KR_ETF_CACHE
    try:
        from pykrx import stock as pykrx_stock
        from datetime import datetime as _dt, timedelta as _td

        codes: set[str] = set()
        for back in range(7):
            day = (_dt.now() - _td(days=back)).strftime("%Y%m%d")
            try:
                lst = pykrx_stock.get_etf_ticker_list(day)
                if lst:
                    codes = set(lst)
                    break
            except Exception:
                continue

        if not codes:
            codes = {
                "379800", "360750", "225030",
                "069500", "102110", "152100",
                "133690", "143850", "200030",
                "278530", "278540", "251340",
            }
            log.warning(f"pykrx ETF list unavailable, using fallback ({len(codes)} codes)")

        _KR_ETF_CACHE = codes
        log.info(f"loaded {len(codes)} Korean ETF codes")
        return codes
    except Exception as e:
        log.warning(f"failed to load Korean ETF codes: {e}")
        _KR_ETF_CACHE = set()
        return _KR_ETF_CACHE


def is_korean_etf(ticker: str) -> bool:
    base = ticker.split(".")[0]
    return base in get_korean_etf_codes()


if __name__ == "__main__":
    for tk in ["379800", "360750", "225030", "005930", "AAPL", "SPY"]:
        atype = detect_asset_type(tk)
        is_etf = is_korean_etf(tk) if atype == "korean" else False
        print(f"  {tk:10} → type={atype}, korean_etf={is_etf}")
