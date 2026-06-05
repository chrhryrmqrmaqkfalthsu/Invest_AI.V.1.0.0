from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import os
import sys
import tempfile
import time
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.telegram.locked_bot import TelegramBot, TelegramPollingLock  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_raises(expected, fn, contains: str = "") -> Exception:
    try:
        fn()
    except expected as exc:
        if contains and contains not in str(exc):
            raise AssertionError(f"exception missing '{contains}': {exc}")
        return exc
    except Exception as exc:
        raise AssertionError(f"wrong exception: {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {expected.__name__}")


def load_run_live_module():
    path = ROOT / "scripts" / "run_live.py"
    spec = importlib.util.spec_from_file_location("run_live_bq2b_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load run_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def child_hold_lock(path: str, ready, release) -> None:
    lock = TelegramPollingLock(token="child-secret-token", owner="child-run_live", path=Path(path))
    lock.acquire()
    ready.put(True)
    release.wait(timeout=10)
    lock.release()


def test_first_poller_acquires_lock_without_token_leak() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "telegram_polling.lock"
        token = "SUPER-SECRET-TELEGRAM-TOKEN"
        lock = TelegramPollingLock(token=token, owner="run_live", path=path)
        lock.acquire()
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert_true(lock.owned, "first poller must own lock")
        assert_true(payload["pid"] == os.getpid(), "lock PID")
        assert_true(payload["owner"] == "run_live", "lock owner")
        assert_true(bool(payload.get("process_identity")), "process identity required")
        assert_true(bool(payload.get("process_start_ticks")), "process start ticks required")
        assert_true(bool(payload.get("process_cmd_fingerprint")), "cmd fingerprint required")
        assert_true(token not in raw, "raw Telegram token must never be stored")
        lock.release()
        assert_true(not path.exists(), "release must remove lock")


def test_second_poller_fails_fast() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "telegram_polling.lock"
        first = TelegramPollingLock(token="token", owner="run_bot", path=path)
        second = TelegramPollingLock(token="token", owner="run_live", path=path)
        first.acquire()
        assert_raises(RuntimeError, second.acquire, "already owned")
        first.release()


def test_two_processes_cannot_poll_simultaneously() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = str(Path(td) / "telegram_polling.lock")
        ctx = mp.get_context("fork")
        ready = ctx.Queue()
        release = ctx.Event()
        process = ctx.Process(target=child_hold_lock, args=(path, ready, release))
        process.start()
        assert_true(ready.get(timeout=5) is True, "child must acquire lock")
        contender = TelegramPollingLock(token="parent-secret-token", owner="parent-run_bot", path=Path(path))
        assert_raises(RuntimeError, contender.acquire, "already owned")
        release.set()
        process.join(timeout=5)
        assert_true(process.exitcode == 0, f"child exitcode={process.exitcode}")
        assert_true(not Path(path).exists(), "child release must remove lock")


def test_dead_stale_lock_is_replaced() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "telegram_polling.lock"
        path.write_text(
            json.dumps(
                {
                    "pid": 999_999_999,
                    "owner": "dead-owner",
                    "started_at": "2000-01-01T00:00:00+00:00",
                    "token_fingerprint": "dead",
                    "process_identity": "dead",
                    "process_start_ticks": "1",
                    "process_cmd_fingerprint": "dead",
                    "boot_id_fingerprint": "dead",
                }
            ),
            encoding="utf-8",
        )
        lock = TelegramPollingLock(token="new-token", owner="run_live", path=path)
        lock.acquire()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert_true(payload["owner"] == "run_live", "dead stale lock must be replaced")
        lock.release()


def test_pid_reuse_guard_fails_closed_without_stealing() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "telegram_polling.lock"
        original = {
            "pid": os.getpid(),
            "owner": "old-owner",
            "started_at": "2000-01-01T00:00:00+00:00",
            "token_fingerprint": "old",
            "process_identity": "definitely-not-current-process",
            "process_start_ticks": "1",
            "process_cmd_fingerprint": "old",
            "boot_id_fingerprint": "old",
        }
        path.write_text(json.dumps(original, sort_keys=True), encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        contender = TelegramPollingLock(token="new-token", owner="run_live", path=path)
        assert_raises(RuntimeError, contender.acquire, "PID reuse suspected")
        assert_true(path.read_text(encoding="utf-8") == before, "PID reuse suspicion must not steal/delete lock")


def test_bot_stop_releases_lock() -> None:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "telegram_polling.lock"
        bot = TelegramBot.__new__(TelegramBot)
        bot.poll_interval = 0.01
        bot._running = False
        bot._thread = None
        bot._polling_lock = TelegramPollingLock(token="token", owner="run_live", path=path)

        def fake_loop(self):
            while self._running:
                time.sleep(0.005)

        bot._poll_loop = MethodType(fake_loop, bot)
        bot.start_polling(blocking=False)
        deadline = time.time() + 2
        while not path.exists() and time.time() < deadline:
            time.sleep(0.01)
        assert_true(path.exists(), "nonblocking bot must acquire lock")
        bot.stop()
        assert_true(not path.exists(), "stop() must release lock after polling thread exits")


def test_run_live_telegram_failure_exits_before_scheduler() -> None:
    run_live = load_run_live_module()
    scheduler_created = {"count": 0}
    universe = SimpleNamespace(symbols=["AAPL"], config=SimpleNamespace(), summary=lambda: "test universe")
    clock = SimpleNamespace(name="US", calendar_source="test")
    broker = SimpleNamespace(mode="paper")
    fake_rulebook = SimpleNamespace(name=lambda: "fake")

    class FakeRunner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeScheduler:
        def __init__(self, *args, **kwargs):
            scheduler_created["count"] += 1

    argv = ["run_live.py", "--mode", "paper"]
    with patch.object(sys, "argv", argv), \
         patch.object(run_live, "load_live_universe", return_value=universe), \
         patch.object(run_live, "select_market_clock", return_value=clock), \
         patch.object(run_live, "make_broker", return_value=broker), \
         patch.object(run_live, "validate_broker_market_compatibility", return_value=None), \
         patch.object(run_live, "TelegramNotifier", return_value=SimpleNamespace()), \
         patch.object(run_live, "SafetyLayer", return_value=SimpleNamespace()), \
         patch.object(run_live, "LearnedRuleBook", return_value=fake_rulebook), \
         patch.object(run_live, "Runner", FakeRunner), \
         patch.object(run_live, "start_telegram_control", side_effect=RuntimeError("polling owner conflict")), \
         patch.object(run_live, "Scheduler", FakeScheduler):
        exc = assert_raises(SystemExit, run_live.main)
    assert_true(getattr(exc, "code", None) == 3, "Telegram failure must exit with code 3")
    assert_true(scheduler_created["count"] == 0, "Scheduler must not be created after Telegram failure")


def test_headless_mode_requires_explicit_flag() -> None:
    run_live = load_run_live_module()
    runner = SimpleNamespace(attach_bot=lambda bot: None)

    class MustNotInstantiate:
        def __init__(self, **kwargs):
            raise AssertionError("bot factory must not run in explicit headless mode")

    result = run_live.start_telegram_control(
        no_telegram_bot=True,
        broker=SimpleNamespace(),
        safety=SimpleNamespace(),
        notifier=SimpleNamespace(),
        runner=runner,
        bot_factory=MustNotInstantiate,
        legacy_run_bot_pid_path=Path("/nonexistent/bq2b-run-bot.pid"),
    )
    assert_true(result is None, "explicit --no-telegram-bot must allow headless mode")

    class BrokenBot:
        def __init__(self, **kwargs):
            pass
        def start_polling(self, blocking=False):
            raise RuntimeError("cannot poll")
        def stop(self):
            pass

    assert_raises(
        RuntimeError,
        lambda: run_live.start_telegram_control(
            no_telegram_bot=False,
            broker=SimpleNamespace(),
            safety=SimpleNamespace(),
            notifier=SimpleNamespace(),
            runner=runner,
            bot_factory=BrokenBot,
            legacy_run_bot_pid_path=Path("/nonexistent/bq2b-run-bot.pid"),
        ),
        "cannot poll",
    )


def test_run_live_bot_receives_runner_managers_and_rulebook() -> None:
    run_live = load_run_live_module()
    position_manager = object()
    approval_manager = object()
    rulebook = object()

    class FakeRunner:
        def __init__(self):
            self.position_manager = position_manager
            self.approval_manager = approval_manager
            self.rulebook = rulebook
            self.attached = None
        def attach_bot(self, bot):
            bot.position_manager = self.position_manager
            bot.approval_manager = self.approval_manager
            bot.rulebook = self.rulebook
            self.attached = bot

    class FakeBot:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False
            self.position_manager = None
            self.approval_manager = None
            self.rulebook = None
        def start_polling(self, blocking=False):
            self.started = True
        def stop(self):
            self.started = False

    runner = FakeRunner()
    bot = run_live.start_telegram_control(
        no_telegram_bot=False,
        broker=SimpleNamespace(),
        safety=SimpleNamespace(),
        notifier=SimpleNamespace(),
        runner=runner,
        bot_factory=FakeBot,
        legacy_run_bot_pid_path=Path("/nonexistent/bq2b-run-bot.pid"),
    )
    assert_true(bot is runner.attached, "run_live must attach the exact polling bot")
    assert_true(bot.position_manager is position_manager, "position manager must be attached")
    assert_true(bot.approval_manager is approval_manager, "approval manager must be attached")
    assert_true(bot.rulebook is rulebook, "rulebook must be attached")
    assert_true(bot.kwargs.get("polling_owner") == "run_live", "run_live owner label required")
    assert_true(bot.started, "polling must start after manager attachment")


def test_legacy_run_bot_pid_guard_blocks_transition_conflict() -> None:
    run_live = load_run_live_module()
    with tempfile.TemporaryDirectory() as td:
        pid_path = Path(td) / "run_bot.pid"
        pid_path.write_text("424242", encoding="utf-8")
        with patch.object(run_live, "is_process_alive", return_value=True):
            assert_raises(RuntimeError, lambda: run_live.assert_no_legacy_run_bot(pid_path), "still polling")


def run_all() -> None:
    tests = [
        test_first_poller_acquires_lock_without_token_leak,
        test_second_poller_fails_fast,
        test_two_processes_cannot_poll_simultaneously,
        test_dead_stale_lock_is_replaced,
        test_pid_reuse_guard_fails_closed_without_stealing,
        test_bot_stop_releases_lock,
        test_run_live_telegram_failure_exits_before_scheduler,
        test_headless_mode_requires_explicit_flag,
        test_run_live_bot_receives_runner_managers_and_rulebook,
        test_legacy_run_bot_pid_guard_blocks_transition_conflict,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL TELEGRAM POLLING OWNERSHIP TESTS PASSED")


if __name__ == "__main__":
    run_all()
