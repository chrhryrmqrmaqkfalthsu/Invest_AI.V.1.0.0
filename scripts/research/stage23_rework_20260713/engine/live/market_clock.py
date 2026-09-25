"""Market clocks for KRX/US/Crypto live scheduling."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from engine.live.us_market_calendar import UsMarketCalendar

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")


class MarketClock:
    name = "GENERIC"

    def is_open(self, now: Optional[datetime] = None) -> bool:
        raise NotImplementedError

    def is_business_day(self, now: Optional[datetime] = None) -> bool:
        raise NotImplementedError

    def next_open(self, now: Optional[datetime] = None) -> Optional[datetime]:
        return None

    def session_close(self, now: Optional[datetime] = None) -> Optional[datetime]:
        return None


class CryptoMarketClock(MarketClock):
    name = "CRYPTO"

    def is_open(self, now: Optional[datetime] = None) -> bool:
        return True

    def is_business_day(self, now: Optional[datetime] = None) -> bool:
        return True


class KrxMarketClock(MarketClock):
    name = "KRX"

    def is_open(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        local = now.astimezone(KST)
        if local.weekday() >= 5:
            return False
        return time(9, 0) <= local.time() <= time(15, 30)

    def is_business_day(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(KST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        return now.astimezone(KST).weekday() < 5


class UsMarketClock(MarketClock):
    name = "US"

    def __init__(self, calendar: Optional[UsMarketCalendar] = None):
        self.calendar = calendar or UsMarketCalendar()

    @property
    def calendar_source(self) -> str:
        return self.calendar.source

    def is_open(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(NY)
        if now.tzinfo is None:
            now = now.replace(tzinfo=NY)
        return self.calendar.is_open(now.astimezone(NY))

    def is_business_day(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(NY)
        if now.tzinfo is None:
            now = now.replace(tzinfo=NY)
        return self.calendar.is_business_day(now.astimezone(NY).date())

    def next_open(self, now: Optional[datetime] = None) -> Optional[datetime]:
        now = now or datetime.now(NY)
        if now.tzinfo is None:
            now = now.replace(tzinfo=NY)
        return self.calendar.next_open(now.astimezone(NY))

    def session_close(self, now: Optional[datetime] = None) -> Optional[datetime]:
        now = now or datetime.now(NY)
        if now.tzinfo is None:
            now = now.replace(tzinfo=NY)
        return self.calendar.session_close(now.astimezone(NY))

    def session_count(self, start: date, end: date) -> int:
        return self.calendar.session_count(start, end)


def market_region_for_ticker(ticker: str) -> str:
    s = str(ticker or "").strip().upper()
    if s.isdigit() and len(s) == 6:
        return "KRX"
    if s.endswith(".KS") or s.endswith(".KQ"):
        return "KRX"
    return "US"


def market_clock_for_ticker(ticker: str) -> MarketClock:
    return KrxMarketClock() if market_region_for_ticker(ticker) == "KRX" else UsMarketClock()


def select_market_clock(tickers: Iterable[str], us_calendar: Optional[UsMarketCalendar] = None) -> MarketClock:
    regions = {market_region_for_ticker(t) for t in tickers}
    if not regions:
        return KrxMarketClock()
    if len(regions) > 1:
        raise ValueError(f"mixed-market universe is not supported by one Runner: {sorted(regions)}")
    region = next(iter(regions))
    return UsMarketClock(calendar=us_calendar) if region == "US" else KrxMarketClock()


def validate_broker_market_compatibility(broker, clock: MarketClock) -> None:
    """Block unsupported broker/market combinations before Runner starts."""
    class_names = {cls.__name__ for cls in type(broker).mro()}
    is_kis = "KisBroker" in class_names or "GuardedKisBroker" in class_names or bool(getattr(broker, "is_guarded_kis_broker", False))
    if clock.name == "US" and is_kis:
        raise RuntimeError(
            "KisBroker domestic live path cannot run a US-only universe; use paper/Alpaca or implement verified KIS overseas orders"
        )


if __name__ == "__main__":
    assert KrxMarketClock().is_open(datetime(2026, 5, 25, 10, 0, tzinfo=KST))
    assert not UsMarketClock().is_open(datetime(2026, 1, 1, 23, 30, tzinfo=KST))
    print(f"MarketClock OK: US calendar source={UsMarketClock().calendar_source}")
