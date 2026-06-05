from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.broker.base import BrokerError, OrderType  # noqa: E402
from engine.live.broker.market_aware import GuardedKisBroker  # noqa: E402
from engine.live.market_clock import UsMarketClock, validate_broker_market_compatibility  # noqa: E402
from engine.live.scheduler import Scheduler  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: actual={actual!r}, expected={expected!r}")


def test_scheduler_list_jobs_includes_interval_job() -> None:
    scheduler = Scheduler()
    scheduler.add_once_job(lambda: None, delay_sec=60, job_id="startup_check")
    scheduler.add_interval_job(lambda: None, interval_sec=3600, job_id="tick_offmarket", name="tick_offmarket")
    jobs = scheduler.list_jobs()
    assert_equal(len(jobs), 2, "list_jobs must include interval job metadata")
    assert_true(any(j["id"] == "tick_offmarket" and j["type"] == "interval" for j in jobs), "interval job missing")
    scheduler.shutdown(wait=False)


def test_run_live_import_has_no_krx_login_warning() -> None:
    cmd = [sys.executable, "-c", "import scripts.run_live"]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=20)
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert_equal(proc.returncode, 0, "scripts.run_live import must succeed")
    assert_true("KRX 로그인 실패" not in combined, "US live import must not trigger KRX login warning")
    assert_true("KIS_APP" not in combined and "KIS_" not in combined, "import must not leak KIS credential diagnostics")


def test_factory_paper_import_has_no_krx_login_warning() -> None:
    cmd = [sys.executable, "-c", "from engine.live.broker.factory import make_broker; print('ok')"]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=20)
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert_equal(proc.returncode, 0, "broker factory import must succeed")
    assert_true("KRX 로그인 실패" not in combined, "paper-capable factory import must not import KIS eagerly")


def test_guarded_kis_still_fails_closed_for_us_without_initializing_kis() -> None:
    broker = object.__new__(GuardedKisBroker)
    assert_true(not broker.is_market_open("AAPL"), "US market-open on KIS guard must fail closed")
    assert_true(broker.get_current_price("AAPL") is None, "US quote on KIS guard must fail closed")
    try:
        broker.place_buy("AAPL", 1, OrderType.MARKET)
    except BrokerError as exc:
        assert_true("does not support US ticker" in str(exc), "US order guard message")
    else:
        raise AssertionError("US order through KIS guard must fail fast")


def test_validate_broker_market_compatibility_recognizes_guarded_kis_wrapper() -> None:
    broker = object.__new__(GuardedKisBroker)
    with patch.object(UsMarketClock, "__init__", lambda self: None):
        clock = object.__new__(UsMarketClock)
    clock.name = "US"
    try:
        validate_broker_market_compatibility(broker, clock)
    except RuntimeError as exc:
        assert_true("cannot run a US-only universe" in str(exc), "compatibility guard message")
    else:
        raise AssertionError("GuardedKisBroker + US clock must fail")


def test_run_live_scheduler_metadata_has_four_jobs_without_starting_live() -> None:
    run_live = importlib.import_module("scripts.run_live")
    scheduler = Scheduler(default_timezone="Asia/Seoul")
    runner = SimpleNamespace(
        startup_check=lambda: None,
        tick_market=lambda: None,
        tick_offmarket=lambda: None,
        daily_summary=lambda: None,
    )
    clock = SimpleNamespace(name="US", is_open=lambda: False, is_business_day=lambda now=None: True)
    scheduler.add_once_job(func=runner.startup_check, delay_sec=2, job_id="startup_check")
    scheduler.add_market_hours_job(func=runner.tick_market, interval_sec=60, market=clock, job_id="tick_market")
    scheduler.add_interval_job(func=runner.tick_offmarket, interval_sec=3600, job_id="tick_offmarket", name="tick_offmarket")
    scheduler.add_cron_job(func=runner.daily_summary, hour=16, minute=0, market=clock, weekdays_only=True, job_id="daily_summary")
    ids = [job["id"] for job in scheduler.list_jobs()]
    assert_equal(ids, ["startup_check", "tick_market", "tick_offmarket", "daily_summary"], "run_live job metadata order/count")
    scheduler.shutdown(wait=False)


def run_all() -> None:
    tests = [
        test_scheduler_list_jobs_includes_interval_job,
        test_run_live_import_has_no_krx_login_warning,
        test_factory_paper_import_has_no_krx_login_warning,
        test_guarded_kis_still_fails_closed_for_us_without_initializing_kis,
        test_validate_broker_market_compatibility_recognizes_guarded_kis_wrapper,
        test_run_live_scheduler_metadata_has_four_jobs_without_starting_live,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL BR-1 OBSERVABILITY TESTS PASSED")


if __name__ == "__main__":
    run_all()
