from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.adapters.base import TradingHours  # noqa: E402
from engine.live.broker.paper import PaperBroker  # noqa: E402
from engine.live.us_market_calendar import NY, UsMarketCalendar  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_direct_us_trading_hours_uses_exact_calendar() -> None:
    hours = TradingHours(timezone="America/New_York", open_time=time(9, 30), close_time=time(16, 0))
    assert_true(not hours.is_open(datetime(2026, 11, 26, 10, 0, tzinfo=NY)), "Thanksgiving must be closed")
    assert_true(hours.is_open(datetime(2026, 11, 27, 12, 59, tzinfo=NY)), "early-close session open before 13:00")
    assert_true(not hours.is_open(datetime(2026, 11, 27, 13, 1, tzinfo=NY)), "early-close session closed after 13:00")


def test_direct_paper_broker_market_check_delegates_to_adapter() -> None:
    broker = object.__new__(PaperBroker)
    hours = TradingHours(timezone="America/New_York", open_time=time(9, 30), close_time=time(16, 0))
    fake_adapter = SimpleNamespace(
        is_market_open=lambda: hours.is_open(datetime(2026, 11, 26, 10, 0, tzinfo=NY))
    )
    with patch("engine.live.broker.paper.get_adapter", return_value=fake_adapter) as mocked:
        assert_true(not broker.is_market_open("AAPL"), "direct PaperBroker must honor exact US holiday")
        mocked.assert_called_once_with("AAPL")


def test_static_fallback_matches_early_close_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        cal = UsMarketCalendar(
            start=date(2026, 1, 1), end=date(2027, 12, 31),
            cache_path=Path(td) / "none.json", allow_api=False,
        )
        assert_true(cal.session_close(date(2026, 11, 27)).hour == 13, "2026 Thanksgiving Friday early close")
        assert_true(cal.session_close(date(2026, 12, 24)).hour == 13, "2026 Christmas Eve early close")
        assert_true(cal.session_close(date(2027, 11, 26)).hour == 13, "2027 Thanksgiving Friday early close")
        assert_true(cal.session_close(date(2026, 7, 2)).hour == 16, "2026 July 2 is regular close per Alpaca calendar")
        assert_true(cal.session_close(date(2027, 7, 2)).hour == 16, "2027 July 2 is regular close per Alpaca calendar")


def run_all() -> None:
    tests = [
        test_direct_us_trading_hours_uses_exact_calendar,
        test_direct_paper_broker_market_check_delegates_to_adapter,
        test_static_fallback_matches_early_close_contract,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL MARKET CLOCK INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    run_all()
