from __future__ import annotations

from types import SimpleNamespace

from scripts.run_live import LEGACY_BUY_DISABLED_CODE, install_legacy_buy_guard


class Stats:
    def __init__(self):
        self.orders_attempted = 0
        self.orders_blocked = 0


class Notifier:
    def __init__(self):
        self.blocks = []

    def send_safety_block(self, code, message):
        self.blocks.append((code, message))


class DummyRunner:
    def __init__(self):
        self.stats = Stats()
        self.notifier = Notifier()
        self.calls = []

    def _try_order(self, side, ticker, price, reason, signal_result=None):
        self.calls.append((side, ticker, price, reason, signal_result))
        self.stats.orders_attempted += 1


def test_legacy_signal_buy_is_blocked_before_original_try_order():
    runner = DummyRunner()
    install_legacy_buy_guard(runner)

    runner._try_order("BUY", "AAA", 100.0, "legacy signal", signal_result=SimpleNamespace())

    assert runner.calls == []
    assert runner.stats.orders_attempted == 1
    assert runner.stats.orders_blocked == 1
    assert runner.notifier.blocks
    assert runner.notifier.blocks[-1][0] == LEGACY_BUY_DISABLED_CODE


def test_legacy_buy_guard_allows_sell_path():
    runner = DummyRunner()
    install_legacy_buy_guard(runner)

    runner._try_order("SELL", "AAA", 100.0, "legacy sell", signal_result=None)

    assert len(runner.calls) == 1
    assert runner.calls[0][0] == "SELL"
    assert runner.stats.orders_attempted == 1
    assert runner.stats.orders_blocked == 0
    assert runner.notifier.blocks == []


def test_legacy_buy_guard_allows_central_control_buy_reason():
    runner = DummyRunner()
    install_legacy_buy_guard(runner)

    runner._try_order("BUY", "AAA", 100.0, "central_control metric=confidence entity=AAA", signal_result=None)

    assert len(runner.calls) == 1
    assert runner.calls[0][0] == "BUY"
    assert runner.stats.orders_attempted == 1
    assert runner.stats.orders_blocked == 0
    assert runner.notifier.blocks == []


def test_legacy_buy_guard_allows_semi_auto_manual_and_fallback_reasons():
    runner = DummyRunner()
    install_legacy_buy_guard(runner)

    runner._try_order("BUY", "AAA", 100.0, "central_control manual_timing metric=confidence entity=AAA", signal_result=None)
    runner._try_order("BUY", "BBB", 101.0, "central_control auto_fallback metric=confidence entity=BBB", signal_result=None)

    assert [call[1] for call in runner.calls] == ["AAA", "BBB"]
    assert runner.stats.orders_attempted == 2
    assert runner.stats.orders_blocked == 0
    assert runner.notifier.blocks == []


def test_legacy_buy_guard_install_is_idempotent():
    runner = DummyRunner()
    install_legacy_buy_guard(runner)
    first = runner._try_order
    install_legacy_buy_guard(runner)

    assert runner._try_order is first
