from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.live.broker.base import Balance, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.pending_order_manager import PendingOrderManager, STATE_RECONCILING
from engine.live.runner import Runner, RunnerStats
from engine.live.safety.layer import SafetyDecision
from engine.strategies.rulebook import default_rulebook


class Notifier:
    def __init__(self):
        self.errors = []
        self.orders = []
        self.blocks = []
        self.messages = []

    def send_error(self, msg):
        self.errors.append(msg)

    def send_order(self, order):
        self.orders.append(order)

    def send_safety_block(self, code, msg):
        self.blocks.append((code, msg))

    def send(self, msg, **kwargs):
        self.messages.append((msg, kwargs))


class Broker:
    mode = "paper"

    def __init__(self, *, holdings=None, price=100.0):
        self.price = float(price)
        self.holdings = dict(holdings or {})
        self.buy_orders = []
        self.orders = {}

    def health_check(self):
        return True

    def get_balance(self):
        return Balance(100000, 100000, 0, self.get_holdings())

    def get_holdings(self):
        return [
            Holding(t, s, self.price, self.price, s * self.price, 0.0, 0.0)
            for t, s in self.holdings.items()
            if s > 0
        ]

    def get_current_price(self, ticker):
        return self.price

    def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0):
        self.buy_orders.append((ticker, shares))
        self.holdings[ticker] = self.holdings.get(ticker, 0.0) + shares
        order = Order(
            order_id=f"B{len(self.buy_orders)}",
            ticker=ticker,
            side=OrderSide.BUY,
            order_type=order_type,
            shares=shares,
            price=price,
            status=OrderStatus.FILLED,
            filled_shares=shares,
            filled_avg_price=self.price,
        )
        self.orders[order.order_id] = order
        return order

    def get_order(self, order_id):
        return self.orders.get(order_id)


class Safety:
    def check_order(self, *args, **kwargs):
        return SafetyDecision(True, "ok", "")

    def record_order(self, *args, **kwargs):
        pass

    def record_fill(self, *args, **kwargs):
        pass


class RulebookProvider:
    def __init__(self, *, atr=1.0, rb=None):
        self.atr = atr
        self.rb = rb if rb is not None else default_rulebook("AAA", asset_type="us_stock", direction="long")

    def name(self):
        return "rulebook-provider"

    def get_last_atr(self, ticker):
        return self.atr

    def get_rulebook(self, ticker):
        return self.rb

    def get_last_market_context(self, ticker):
        return {"score": 50.0, "vix_level": 18.0, "sector_score": 50.0}


class PositionManager:
    def __init__(self, *, fail_register=False, existing=None):
        self.fail_register = fail_register
        self.positions = dict(existing or {})
        self.register_calls = 0
        self.add_calls = 0

    def register_entry(self, ticker, entry_price, shares, rulebook, atr_value, entry_market_context=None):
        self.register_calls += 1
        if self.fail_register:
            raise RuntimeError("register boom")
        entry = SimpleNamespace(ticker=ticker, entry_price=entry_price, shares=shares)
        self.positions[ticker] = entry
        return entry

    def add_to_position(self, *args, **kwargs):
        self.add_calls += 1
        return SimpleNamespace(ticker=args[0])

    def get(self, ticker):
        return self.positions.get(ticker)


class ApprovalManager:
    def get_request(self, rid):
        return None

    def _save(self):
        pass


def make_runner(tmp_path: Path, *, rulebook=None, position_manager=None, broker=None):
    runner = Runner.__new__(Runner)
    runner.broker = broker or Broker()
    runner.safety = Safety()
    runner.notifier = Notifier()
    runner.rulebook = rulebook or RulebookProvider()
    runner.position_manager = position_manager or PositionManager()
    runner.approval_manager = ApprovalManager()
    runner.pending_order_manager = PendingOrderManager(runner.broker, path=tmp_path / "pending_orders.json")
    runner.buy_reconciler = runner._make_buy_reconciler()
    runner.order_shares = 1.0
    runner.order_notional = None
    runner.stats = RunnerStats()
    runner.symbols = ["AAA"]
    return runner


def test_buy_preflight_missing_atr_blocks_before_order(tmp_path):
    runner = make_runner(tmp_path, rulebook=RulebookProvider(atr=None))
    runner._try_order("BUY", "AAA", 100.0, "signal")

    assert runner.broker.buy_orders == []
    assert runner.stats.orders_blocked == 1
    assert runner.pending_order_manager.all() == []
    assert runner.notifier.errors


def test_immediate_filled_register_exception_creates_durable_reconciling_lock(tmp_path):
    pm = PositionManager(fail_register=True)
    runner = make_runner(tmp_path, position_manager=pm)
    runner._try_order("BUY", "AAA", 100.0, "signal")

    assert runner.broker.buy_orders == [("AAA", 1.0)]
    records = runner.pending_order_manager.all()
    assert len(records) == 1
    assert records[0].state == STATE_RECONCILING
    assert records[0].metadata["local_reconciliation"] is True
    assert runner.pending_order_manager.is_ticker_locked("AAA")
    assert runner.notifier.errors and "ORPHAN-BUY" in runner.notifier.errors[-1]


def test_pending_buy_reconcile_failure_stays_reconciling_and_is_not_finalized(tmp_path):
    pm = PositionManager(fail_register=True)
    runner = make_runner(tmp_path, position_manager=pm)
    order = Order(
        order_id="B9", ticker="AAA", side=OrderSide.BUY, order_type=OrderType.MARKET,
        shares=1.0, price=0.0, status=OrderStatus.FILLED,
        filled_shares=1.0, filled_avg_price=100.0,
    )
    runner.pending_order_manager.track_reconciliation(order, purpose="entry", error="seed failure")

    runner._poll_pending_orders(context="test")

    rec = runner.pending_order_manager.get_record("B9")
    assert rec is not None
    assert rec.state == STATE_RECONCILING
    assert rec.finalization_state == "pending"
    assert runner.pending_order_manager.is_ticker_locked("AAA")


def test_startup_orphan_holding_detected_and_locked(tmp_path):
    broker = Broker(holdings={"AAA": 2.0})
    runner = make_runner(tmp_path, broker=broker, position_manager=PositionManager())
    runner.startup_check()

    records = runner.pending_order_manager.all()
    assert len(records) == 1
    assert records[0].order_id == "ORPHAN-AAA"
    assert records[0].state == STATE_RECONCILING
    assert runner.pending_order_manager.is_ticker_locked("AAA")


def test_normal_immediate_filled_buy_registers_and_does_not_create_pending(tmp_path):
    pm = PositionManager()
    runner = make_runner(tmp_path, position_manager=pm)
    runner._try_order("BUY", "AAA", 100.0, "signal")

    assert pm.get("AAA") is not None
    assert runner.pending_order_manager.all() == []
    assert not (tmp_path / "pending_orders.json").exists()
