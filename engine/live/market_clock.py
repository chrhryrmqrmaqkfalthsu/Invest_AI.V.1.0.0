"""Market clocks and single-market live-universe selection.

Scheduler delegates all market-time decisions to this interface. US sessions
are backed by the cache/API/fallback provider in ``us_market_calendar``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Iterable, Optional, Set
from zoneinfo import ZoneInfo


class MarketClock(ABC):
    """Common market-hours and trading-session interface."""

    name: str = "abstract"
    timezone: ZoneInfo = ZoneInfo("UTC")

    @abstractmethod
    def is_open(self, dt: Optional[datetime] = None) -> bool:
        ...

    @abstractmethod
    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        ...

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    def next_open(self, dt: Optional[datetime] = None) -> Optional[datetime]:
        return None

    def session_close(self, value: date | datetime | str) -> Optional[datetime]:
        return None

    def session_count(self, start: date | datetime | str, end: date | datetime | str) -> int:
        """Generic session count: strictly after start and through end."""
        start_date = self._to_local_date(start)
        end_date = self._to_local_date(end)
        if end_date <= start_date:
            return 0
        count = 0
        cursor = start_date
        while cursor < end_date:
            cursor += timedelta(days=1)
            probe = datetime.combine(cursor, time(12, 0), tzinfo=self.timezone)
            if self.is_business_day(probe):
                count += 1
        return count

    def _to_local_date(self, value: date | datetime | str) -> date:
        if isinstance(value, datetime):
            dt = value.replace(tzinfo=self.timezone) if value.tzinfo is None else value.astimezone(self.timezone)
            return dt.date()
        if isinstance(value, date):
            return value
        raw = str(value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=self.timezone) if dt.tzinfo is None else dt.astimezone(self.timezone)
            return dt.date()
        except Exception:
            return date.fromisoformat(raw[:10])


class KrxMarketClock(MarketClock):
    """KRX regular market. Holiday list remains manually extensible."""

    name = "KRX"
    timezone = ZoneInfo("Asia/Seoul")
    OPEN_TIME = time(9, 0)
    CLOSE_TIME = time(15, 30)
    holidays: Set[str] = set()

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        local = self._to_local(dt)
        return self.is_business_day(local) and self.OPEN_TIME <= local.time() <= self.CLOSE_TIME

    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        local = self._to_local(dt)
        return local.weekday() < 5 and local.strftime("%Y-%m-%d") not in self.holidays

    def next_open(self, dt: Optional[datetime] = None) -> Optional[datetime]:
        local = self._to_local(dt)
        candidate = local.replace(hour=9, minute=0, second=0, microsecond=0)
        if local.time() >= self.OPEN_TIME:
            candidate += timedelta(days=1)
        for _ in range(15):
            if self.is_business_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        return None

    def session_close(self, value: date | datetime | str) -> Optional[datetime]:
        session_date = self._to_local_date(value)
        probe = datetime.combine(session_date, time(12, 0), tzinfo=self.timezone)
        if not self.is_business_day(probe):
            return None
        return datetime.combine(session_date, self.CLOSE_TIME, tzinfo=self.timezone)

    def _to_local(self, dt: Optional[datetime]) -> datetime:
        if dt is None:
            return self.now()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self.timezone)
        return dt.astimezone(self.timezone)


class CryptoMarketClock(MarketClock):
    name = "Crypto"
    timezone = ZoneInfo("UTC")

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        return True

    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        return True


class UsMarketClock(MarketClock):
    """US equity regular sessions backed by an exact session calendar."""

    name = "US"
    timezone = ZoneInfo("America/New_York")

    def __init__(self, calendar=None):
        self.calendar = calendar if calendar is not None else get_us_market_calendar()

    @property
    def calendar_source(self) -> str:
        return str(getattr(self.calendar, "source", "unknown"))

    def is_open(self, dt: Optional[datetime] = None) -> bool:
        return bool(self.calendar.is_open(dt))

    def is_business_day(self, dt: Optional[datetime] = None) -> bool:
        return bool(self.calendar.is_business_day(dt or self.now()))

    def next_open(self, dt: Optional[datetime] = None) -> Optional[datetime]:
        return self.calendar.next_open(dt)

    def session_close(self, value: date | datetime | str) -> Optional[datetime]:
        return self.calendar.session_close(value)

    def session_count(self, start: date | datetime | str, end: date | datetime | str) -> int:
        return int(self.calendar.session_count(start, end))


@lru_cache(maxsize=1)
def get_us_market_calendar():
    from engine.live.us_market_calendar import UsMarketCalendar

    return UsMarketCalendar()


def market_region_for_ticker(ticker: str) -> str:
    """Classify without importing adapters or triggering any market-data side effect."""
    base = str(ticker).strip().split(".")[0].upper()
    return "KRX" if base.isdigit() and len(base) == 6 else "US"


@lru_cache(maxsize=2)
def _clock_for_region(region: str) -> MarketClock:
    if region == "US":
        return UsMarketClock()
    if region == "KRX":
        return KrxMarketClock()
    raise ValueError(f"unsupported market region: {region}")


def market_clock_for_ticker(ticker: str) -> MarketClock:
    return _clock_for_region(market_region_for_ticker(ticker))


def select_market_clock(symbols: Iterable[str], *, us_calendar=None) -> MarketClock:
    """Select one clock for a single-market universe; mixed markets fail fast."""
    normalized = [str(symbol).strip() for symbol in symbols if str(symbol).strip()]
    if not normalized:
        raise ValueError("cannot select market clock for empty symbols")
    regions = {market_region_for_ticker(symbol) for symbol in normalized}
    if len(regions) != 1:
        raise ValueError(f"mixed-market live universe is not supported yet: {sorted(regions)}")
    region = next(iter(regions))
    if region == "US":
        return UsMarketClock(calendar=us_calendar) if us_calendar is not None else UsMarketClock()
    return KrxMarketClock()


def validate_broker_market_compatibility(broker, clock: MarketClock) -> None:
    """Block unsupported broker/market combinations before Runner starts."""
    class_names = {cls.__name__ for cls in type(broker).mro()}
    if clock.name == "US" and "KisBroker" in class_names:
        raise RuntimeError(
            "KisBroker domestic live path cannot run a US-only universe; use paper/Alpaca or implement verified KIS overseas orders"
        )


if __name__ == "__main__":
    from datetime import datetime as dt

    krx = KrxMarketClock()
    us = UsMarketClock()
    tz_seoul = ZoneInfo("Asia/Seoul")
    assert krx.is_open(dt(2026, 5, 25, 10, 0, tzinfo=tz_seoul))
    assert not us.is_open(dt(2026, 1, 1, 23, 30, tzinfo=tz_seoul))
    print(f"MarketClock OK: US calendar source={us.calendar_source}")
