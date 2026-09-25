from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.exit_policy_guard import is_strict_live_broker, validate_startup_exit_policy
from engine.live.pending_order_manager import PendingOrderManager
from engine.live.position_manager import PositionEntry, PositionManager
from engine.strategies.rulebook import Rulebook


class Broker:
    def __init__(self, *, mode="paper", kis_mode="", price=95.0):
        self.mode = mode
        self.kis_mode = kis_mode
        self.price = price
        self.sell_calls = 0

    def get_current_price(self, ticker):
        return self.price

    def get_holdings(self):
        return [Holding("AAA", 1.0, 100.0, self.price, self.price, 0.0, 0.0)]

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0):
        self.sell_calls += 1
        return Order(
            order_id="S1", ticker=ticker, side=OrderSide.SELL, order_type=order_type,
            shares=shares, price=price, status=OrderStatus.FILLED,
            filled_shares=shares, filled_avg_price=self.price,
        )


class Notifier:
    def __init__(self):
        self.errors = []

    def send_error(self, msg):
        self.errors.append(msg)


def make_manager(pos):
    manager = PositionManager.__new__(PositionManager)
    manager._positions = {pos.ticker: pos}
    manager._save = lambda: None
    manager._append_trade_log = lambda record: None
    manager.unregister = lambda ticker: manager._positions.pop(ticker, None)
    return manager


def make_pos(*, snapshot=True):
    rb = Rulebook(
        ticker="AAA", asset_type="us_stock", direction="long", exit_strategy="hybrid",
        stop_loss_atr=2.0, take_profit_atr=3.0, trailing_atr=1.5, max_holding_days=20,
    )
    return PositionEntry(
        ticker="AAA", entry_date=datetime.now().isoformat(), entry_price=100.0, shares=1.0,
        atr_at_entry=2.0, stop_price=96.0, target_price=106.0, trailing_distance=3.0,
        trailing_stop=97.0, highest_price=100.0, lowest_price=100.0,
        exit_strategy="hybrid", max_holding_days=20,
        rulebook_direction="long", rulebook_snapshot=rb.to_dict() if snapshot else {}, member_hash="a" * 64,
    )


def test_strict_live_broker_detection():
    assert is_strict_live_broker(SimpleNamespace(mode="live", kis_mode="real")) is True
    assert is_strict_live_broker(SimpleNamespace(mode="live", kis_mode="live")) is True
    assert is_strict_live_broker(SimpleNamespace(mode="live", kis_mode="vts")) is False
    assert is_strict_live_broker(SimpleNamespace(mode="alpaca_live")) is True
    assert is_strict_live_broker(SimpleNamespace(mode="alpaca_paper")) is False
    assert is_strict_live_broker(SimpleNamespace(mode="paper")) is False


def test_startup_fail_fast_only_for_strict_live(monkeypatch):
    monkeypatch.delenv("EXIT_LIVE_POLICY", raising=False)
    monkeypatch.delenv("ALLOW_LEGACY_EXIT_LIVE", raising=False)
    validate_startup_exit_policy(SimpleNamespace(mode="paper"))
    validate_startup_exit_policy(SimpleNamespace(mode="alpaca_paper"))
    validate_startup_exit_policy(SimpleNamespace(mode="live", kis_mode="vts"))

    try:
        validate_startup_exit_policy(SimpleNamespace(mode="live", kis_mode="real"))
    except RuntimeError as exc:
        assert "EXIT_LIVE_POLICY=1" in str(exc)
    else:
        raise AssertionError("KIS real must fail fast without EXIT_LIVE_POLICY")

    try:
        validate_startup_exit_policy(SimpleNamespace(mode="alpaca_live"))
    except RuntimeError:
        pass
    else:
        raise AssertionError("Alpaca live must fail fast without EXIT_LIVE_POLICY")

    monkeypatch.setenv("EXIT_LIVE_POLICY", "1")
    validate_startup_exit_policy(SimpleNamespace(mode="live", kis_mode="real"))
    validate_startup_exit_policy(SimpleNamespace(mode="alpaca_live"))


def test_strict_live_snapshotless_position_blocks_silent_legacy_and_locks(monkeypatch, tmp_path):
    monkeypatch.setenv("EXIT_LIVE_POLICY", "1")
    monkeypatch.delenv("ALLOW_LEGACY_EXIT_LIVE", raising=False)
    pos = make_pos(snapshot=False)
    manager = make_manager(pos)
    broker = Broker(mode="live", kis_mode="real", price=94.0)
    notifier = Notifier()
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")

    result = manager._check_one("AAA", pos, broker, notifier=notifier, pending_manager=pending)

    assert result is None
    assert broker.sell_calls == 0
    assert notifier.errors and "EXIT-POLICY-GUARD" in notifier.errors[-1]
    assert pending.is_ticker_locked("AAA")


def test_strict_live_policy_exception_blocks_silent_legacy_and_locks(monkeypatch, tmp_path):
    monkeypatch.setenv("EXIT_LIVE_POLICY", "1")
    monkeypatch.delenv("ALLOW_LEGACY_EXIT_LIVE", raising=False)
    pos = make_pos(snapshot=True)
    manager = make_manager(pos)
    manager._evaluate_policy = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("policy boom"))
    broker = Broker(mode="alpaca_live", price=94.0)
    notifier = Notifier()
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")

    result = manager._check_one("AAA", pos, broker, notifier=notifier, pending_manager=pending)

    assert result is None
    assert broker.sell_calls == 0
    assert notifier.errors and "policy boom" in notifier.errors[-1]
    assert pending.is_ticker_locked("AAA")


def test_explicit_override_allows_legacy_for_strict_live(monkeypatch, tmp_path):
    monkeypatch.setenv("EXIT_LIVE_POLICY", "1")
    monkeypatch.setenv("ALLOW_LEGACY_EXIT_LIVE", "1")
    pos = make_pos(snapshot=False)
    manager = make_manager(pos)
    broker = Broker(mode="live", kis_mode="real", price=94.0)
    notifier = Notifier()
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")

    result = manager._check_one("AAA", pos, broker, notifier=notifier, pending_manager=pending)

    assert result is not None
    assert broker.sell_calls == 1
    assert not notifier.errors
