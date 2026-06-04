from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.broker.base import BrokerError, OrderType  # noqa: E402
from engine.live.broker.market_aware import CalendarAwarePaperBroker, GuardedKisBroker  # noqa: E402
from engine.live.exit_policy_adapter import count_holding_trading_days  # noqa: E402
from engine.live.market_clock import (  # noqa: E402
    KrxMarketClock,
    UsMarketClock,
    select_market_clock,
    validate_broker_market_compatibility,
)
from engine.live.us_market_calendar import MarketSession, NY, UsMarketCalendar  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: actual={actual!r}, expected={expected!r}")


def static_calendar(tmp: Path) -> UsMarketCalendar:
    return UsMarketCalendar(
        start=date(2026, 1, 1),
        end=date(2027, 12, 31),
        cache_path=tmp / "missing.json",
        allow_api=False,
    )


def test_static_fallback_holidays_and_early_close() -> None:
    with tempfile.TemporaryDirectory() as td:
        cal = static_calendar(Path(td))
        assert_equal(cal.source, "static_fallback", "fallback source")
        for closed in (
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 7, 3),
            date(2026, 11, 26),
            date(2026, 12, 25),
            date(2027, 3, 26),
            date(2027, 11, 25),
        ):
            assert_true(not cal.is_business_day(closed), f"holiday must be closed: {closed}")

        early = date(2026, 11, 27)
        close = cal.session_close(early)
        assert_true(close is not None, "early-close session must exist")
        assert_equal(close.hour, 13, "Thanksgiving Friday close hour")
        assert_true(cal.is_open(datetime(2026, 11, 27, 12, 59, tzinfo=NY)), "must be open before 13:00 ET")
        assert_true(cal.is_open(datetime(2026, 11, 27, 13, 0, tzinfo=NY)), "13:00 ET boundary remains open")
        assert_true(not cal.is_open(datetime(2026, 11, 27, 13, 1, tzinfo=NY)), "must close after 13:00 ET")


def test_next_open_and_exact_session_count() -> None:
    with tempfile.TemporaryDirectory() as td:
        cal = static_calendar(Path(td))
        thanksgiving = datetime(2026, 11, 26, 10, 0, tzinfo=NY)
        nxt = cal.next_open(thanksgiving)
        assert_true(nxt is not None, "next open must exist")
        assert_equal(nxt.date(), date(2026, 11, 27), "next session after Thanksgiving")
        assert_equal(nxt.time().replace(tzinfo=None), time(9, 30), "next session open time")

        exact = cal.session_count(date(2026, 11, 25), date(2026, 11, 30))
        weekday_approx = sum(
            1
            for offset in range(1, 6)
            if (date(2026, 11, 25) + timedelta(days=offset)).weekday() < 5
        )
        assert_equal(exact, 2, "sessions after Nov 25 through Nov 30")
        assert_equal(weekday_approx, 3, "weekday approximation includes Thanksgiving incorrectly")
        assert_equal(weekday_approx - exact, 1, "holiday correction must reduce holding days by one")


def test_cache_first_and_no_secret_material() -> None:
    calls = {"count": 0}
    secret_marker = "DO_NOT_WRITE_SECRET_MARKER"

    def fetcher(start: date, end: date):
        calls["count"] += 1
        return [
            MarketSession(date(2026, 1, 2), datetime(2026, 1, 2, 9, 30, tzinfo=NY), datetime(2026, 1, 2, 16, 0, tzinfo=NY)),
            MarketSession(date(2026, 12, 31), datetime(2026, 12, 31, 9, 30, tzinfo=NY), datetime(2026, 12, 31, 16, 0, tzinfo=NY)),
        ]

    with tempfile.TemporaryDirectory() as td:
        cache = Path(td) / "calendar.json"
        first = UsMarketCalendar(
            start=date(2026, 1, 1), end=date(2026, 12, 31), cache_path=cache,
            api_fetcher=fetcher, allow_api=True,
        )
        assert_equal(first.source, "alpaca", "first load source")
        assert_equal(calls["count"], 1, "API fetcher called once")
        raw_text = cache.read_text(encoding="utf-8")
        assert_true(secret_marker not in raw_text, "secret marker must not be cached")
        rows = json.loads(raw_text)
        assert_true(isinstance(rows, list) and rows, "cache must contain rows")
        assert_equal(set(rows[0].keys()), {"date", "open", "close"}, "cache row keys only")

        def forbidden_fetcher(start: date, end: date):
            raise AssertionError("fresh cache must prevent API call")

        second = UsMarketCalendar(
            start=date(2026, 1, 1), end=date(2026, 12, 31), cache_path=cache,
            api_fetcher=forbidden_fetcher, allow_api=True,
        )
        assert_equal(second.source, "cache", "fresh cache source")


def test_api_failure_uses_static_fallback() -> None:
    def failed_fetcher(start: date, end: date):
        raise RuntimeError("simulated API failure without credentials")

    with tempfile.TemporaryDirectory() as td:
        cal = UsMarketCalendar(
            start=date(2026, 1, 1), end=date(2027, 12, 31), cache_path=Path(td) / "none.json",
            api_fetcher=failed_fetcher, allow_api=True,
        )
        assert_equal(cal.source, "static_fallback", "API failure fallback source")
        assert_true(not cal.is_business_day(date(2026, 11, 26)), "fallback Thanksgiving closed")
        assert_equal(cal.session_close(date(2026, 11, 27)).hour, 13, "fallback early close")


def test_us_only_selection_and_mixed_fail_fast() -> None:
    with tempfile.TemporaryDirectory() as td:
        cal = static_calendar(Path(td))
        us_clock = select_market_clock(["AAPL", "QQQ"], us_calendar=cal)
        assert_true(isinstance(us_clock, UsMarketClock), "US-only universe selects UsMarketClock")
        kr_clock = select_market_clock(["005930", "379800"])
        assert_true(isinstance(kr_clock, KrxMarketClock), "KR-only universe selects KrxMarketClock")
        try:
            select_market_clock(["AAPL", "005930"], us_calendar=cal)
        except ValueError as exc:
            assert_true("mixed-market" in str(exc), "mixed universe error must be explicit")
        else:
            raise AssertionError("mixed universe must fail fast")


def test_exit_adapter_uses_exact_session_count() -> None:
    with tempfile.TemporaryDirectory() as td:
        clock = UsMarketClock(calendar=static_calendar(Path(td)))
        entry = datetime(2026, 11, 25, 10, 0, tzinfo=NY)
        current = datetime(2026, 11, 30, 10, 0, tzinfo=NY)
        days = count_holding_trading_days(entry.isoformat(), current, ticker="AAPL", market_clock=clock)
        assert_equal(days, 2, "adapter must exclude Thanksgiving from holding days")


def test_paper_broker_uses_shared_calendar() -> None:
    broker = object.__new__(CalendarAwarePaperBroker)
    fake_clock = SimpleNamespace(is_open=lambda: False)
    with patch("engine.live.broker.market_aware.market_clock_for_ticker", return_value=fake_clock) as mocked:
        assert_true(not broker.is_market_open("AAPL"), "PaperBroker must honor shared closed calendar")
        mocked.assert_called_once_with("AAPL")


def test_kis_us_guard_and_startup_compatibility() -> None:
    broker = object.__new__(GuardedKisBroker)
    assert_true(not broker.is_market_open("AAPL"), "KIS US market-open must fail closed")
    assert_true(broker.get_current_price("AAPL") is None, "KIS US quote must fail closed before network")
    try:
        broker.place_buy("AAPL", 1, OrderType.MARKET)
    except BrokerError as exc:
        assert_true("does not support US ticker" in str(exc), "KIS US order guard message")
    else:
        raise AssertionError("KIS US order must fail fast")

    with tempfile.TemporaryDirectory() as td:
        us_clock = UsMarketClock(calendar=static_calendar(Path(td)))
        try:
            validate_broker_market_compatibility(broker, us_clock)
        except RuntimeError as exc:
            assert_true("cannot run a US-only universe" in str(exc), "startup compatibility guard")
        else:
            raise AssertionError("KIS + US clock must fail before Runner starts")


def run_all() -> None:
    tests = [
        test_static_fallback_holidays_and_early_close,
        test_next_open_and_exact_session_count,
        test_cache_first_and_no_secret_material,
        test_api_failure_uses_static_fallback,
        test_us_only_selection_and_mixed_fail_fast,
        test_exit_adapter_uses_exact_session_count,
        test_paper_broker_uses_shared_calendar,
        test_kis_us_guard_and_startup_compatibility,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL US MARKET CALENDAR TESTS PASSED")


if __name__ == "__main__":
    run_all()
