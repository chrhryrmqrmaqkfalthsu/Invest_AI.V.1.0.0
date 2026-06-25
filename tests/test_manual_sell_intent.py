from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.live.broker.base import Balance, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.manual_sell_intent import (
    atomic_write_json,
    create_manual_sell_intent,
    load_manual_sell_state,
)
from engine.live.pending_order_manager import PendingOrderManager
from engine.live.position_manager import PositionEntry, PositionManager
from engine.live.runner import Runner, RunnerStats
from engine.live.safety.layer import SafetyDecision, SafetyLayer


class FakeNotifier:
    def send_order(self, *a, **k): pass
    def send_error(self, *a, **k): pass
    def send_safety_block(self, *a, **k): pass
    def send_trade_exit(self, *a, **k): pass


class FakeSafety:
    def __init__(self):
        self.checked = []
        self.recorded = []
    def check_order(self, side, ticker, shares, price, purpose="entry"):
        self.checked.append((side, ticker, shares, price, purpose))
        return SafetyDecision(True, "ok")
    def record_order(self, order, side, purpose="entry"):
        self.recorded.append((order.order_id, side, purpose))
    def record_fill(self, order, side, purpose="entry"):
        self.recorded.append((order.order_id, side, purpose, "fill"))
    def record_realized_pnl(self, *a, **k): pass


class FakeBroker:
    mode = "alpaca_paper"
    def __init__(self, ticker="AR", shares=10.0, price=100.0, pending=True):
        self.ticker = ticker
        self.shares = shares
        self.price = price
        self.pending = pending
        self.orders = {}
        self.sell_calls = 0
    def get_balance(self):
        return Balance(100000, 100000, self.shares * self.price, self.get_holdings())
    def get_holdings(self):
        if self.shares <= 1e-6:
            return []
        return [Holding(self.ticker, self.shares, self.price, self.price, self.shares * self.price, 0.0, 0.0)]
    def get_current_price(self, ticker):
        return self.price
    def is_market_open(self, ticker=None):
        return True
    def place_buy(self, *a, **k):
        raise AssertionError("BUY should not be called")
    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
        self.sell_calls += 1
        status = OrderStatus.PENDING if self.pending else OrderStatus.FILLED
        filled_shares = 0.0 if self.pending else shares
        order = Order(
            f"S{self.sell_calls}", ticker, OrderSide.SELL, order_type, shares, price,
            status, filled_shares=filled_shares, filled_avg_price=(self.price if filled_shares else 0.0),
            client_order_id=client_order_id, raw_status=status.value,
        )
        self.orders[order.order_id] = order
        if status == OrderStatus.FILLED:
            self.shares = max(0.0, self.shares - shares)
        return order
    def get_order(self, order_id):
        order = self.orders.get(order_id)
        if order is None:
            return None
        if order.status == OrderStatus.PENDING:
            order.status = OrderStatus.FILLED
            order.raw_status = "filled"
            order.filled_shares = order.shares
            order.filled_avg_price = self.price
            self.shares = max(0.0, self.shares - order.shares)
        return order
    def cancel_order(self, order_id):
        return True


def make_position(ticker="AR", shares=10.0, entry=100.0):
    return PositionEntry(
        ticker=ticker,
        entry_date="2026-06-25T00:00:00+09:00",
        entry_price=entry,
        shares=shares,
        atr_at_entry=1.0,
        stop_price=90.0,
        target_price=120.0,
        trailing_distance=5.0,
        trailing_stop=95.0,
        highest_price=entry,
        lowest_price=entry,
        exit_strategy="fixed",
        max_holding_days=10,
        rulebook_direction="long",
        member_hash="abc123",
    )


def make_pm(tmp_path, ticker="AR", shares=10.0):
    pm = PositionManager.__new__(PositionManager)
    pm._positions = {ticker: make_position(ticker, shares)}
    pm._load_error = ""
    return pm


def make_runner(tmp_path, monkeypatch, ticker="AR", shares=10.0, pending=True):
    import engine.live.position_manager as pm_module
    import engine.live.manual_sell_intent as sell_module
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", tmp_path / "positions.json")
    monkeypatch.setattr(pm_module, "TRADE_LOG_PATH", tmp_path / "trade_log.csv")
    monkeypatch.setattr(sell_module, "MANUAL_SELL_INTENT_PATH", tmp_path / "manual_sell_intent.json")
    monkeypatch.setattr(sell_module, "POSITIONS_PATH", tmp_path / "positions.json")
    pm = make_pm(tmp_path, ticker, shares)
    pm._save()
    broker = FakeBroker(ticker, shares, pending=pending)
    runner = Runner.__new__(Runner)
    runner.broker = broker
    runner.safety = FakeSafety()
    runner.notifier = FakeNotifier()
    runner.position_manager = pm
    runner.pending_order_manager = PendingOrderManager(broker, path=tmp_path / "pending_orders.json")
    runner.stats = RunnerStats()
    runner.order_shares = 1.0
    runner.order_notional = 30.0
    runner._tick_locked_tickers = set()
    runner.approval_manager = SimpleNamespace(get_request=lambda _rid: None, _save=lambda: None)
    return runner, broker, sell_module.MANUAL_SELL_INTENT_PATH


def write_positions(path, ticker="AR", shares=10.0):
    atomic_write_json(path, {ticker: make_position(ticker, shares).to_dict()})


def test_create_manual_sell_intent_for_held_position_is_idempotent(tmp_path):
    positions = tmp_path / "positions.json"
    intents = tmp_path / "manual_sell_intent.json"
    write_positions(positions, "AR", 10.0)

    first = create_manual_sell_intent(ticker="ar", positions_path=positions, intent_path=intents)
    second = create_manual_sell_intent(ticker="AR", positions_path=positions, intent_path=intents)

    assert first["intent_id"] == second["intent_id"]
    assert first["status"] == "pending"
    assert first["shares_requested"] == 10.0


def test_create_manual_sell_intent_rejects_not_held_and_partial(tmp_path):
    positions = tmp_path / "positions.json"
    intents = tmp_path / "manual_sell_intent.json"
    write_positions(positions, "AR", 10.0)

    with pytest.raises(ValueError, match="not held"):
        create_manual_sell_intent(ticker="NOPE", positions_path=positions, intent_path=intents)
    with pytest.raises(ValueError, match="partial sell not supported"):
        create_manual_sell_intent(ticker="AR", shares_requested=5.0, positions_path=positions, intent_path=intents)


def test_pending_sell_intent_consumes_existing_exit_path_and_finalizes(tmp_path, monkeypatch):
    runner, broker, intent_path = make_runner(tmp_path, monkeypatch, "AR", 10.0, pending=True)
    create_manual_sell_intent(ticker="AR", positions_path=tmp_path / "positions.json", intent_path=intent_path)

    runner._process_manual_sell_intents()
    state = load_manual_sell_state(intent_path)
    intent = next(iter(state["intents"].values()))
    assert intent["status"] == "submitted"
    assert broker.sell_calls == 1
    assert runner.pending_order_manager.has_pending_exit("AR")

    runner._poll_pending_orders(context="test")
    runner._process_manual_sell_intents()

    state = load_manual_sell_state(intent_path)
    intent = next(iter(state["intents"].values()))
    assert intent["status"] == "consumed"
    assert runner.position_manager.get("AR") is None
    assert broker.get_holdings() == []
    assert runner.pending_order_manager.all() == []


def test_already_exited_manual_sell_intent_is_rejected_without_order(tmp_path, monkeypatch):
    import engine.live.manual_sell_intent as sell_module
    monkeypatch.setattr(sell_module, "MANUAL_SELL_INTENT_PATH", tmp_path / "manual_sell_intent.json")
    runner, broker, intent_path = make_runner(tmp_path, monkeypatch, "AR", 10.0, pending=True)
    # Simulate stale pending intent after the bot already exited and local state was cleaned.
    runner.position_manager.unregister("AR")
    broker.shares = 0.0
    atomic_write_json(intent_path, {"schema_version": 1, "trade_date": "2026-06-25", "intents": {"manual_sell:AR:x": {"intent_id": "manual_sell:AR:x", "ticker": "AR", "status": "pending"}}})

    runner._process_manual_sell_intents()

    state = load_manual_sell_state(intent_path)
    row = state["intents"]["manual_sell:AR:x"]
    assert row["status"] == "rejected"
    assert row["note"] == "already exited"
    assert broker.sell_calls == 0


def test_sell_pending_lock_rejects_duplicate_manual_intent_and_skips_order(tmp_path, monkeypatch):
    runner, broker, intent_path = make_runner(tmp_path, monkeypatch, "AR", 10.0, pending=True)
    # Pre-existing SELL pending lock, e.g. auto-exit already submitted.
    order = Order("SLOCK", "AR", OrderSide.SELL, OrderType.MARKET, 10.0, 0.0, OrderStatus.PENDING, client_order_id="x")
    runner.pending_order_manager.track_order(order, purpose="exit", exit_reason="stop_loss")
    create_manual_sell_intent(ticker="AR", positions_path=tmp_path / "positions.json", intent_path=intent_path)

    runner._process_manual_sell_intents()

    row = next(iter(load_manual_sell_state(intent_path)["intents"].values()))
    assert row["status"] == "rejected"
    assert row["note"] == "already exiting"
    assert broker.sell_calls == 0


def test_safety_layer_does_not_limit_notional_for_sell(tmp_path, monkeypatch):
    import engine.live.safety.layer as safety_layer
    symbols = tmp_path / "symbols"
    (symbols / "FIX").mkdir(parents=True)
    positions = tmp_path / "positions.json"
    positions.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(safety_layer, "SYMBOLS_DIR", symbols)
    monkeypatch.setattr(safety_layer, "POSITIONS_PATH", positions)
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "small_amount_safety:\n"
        "  enabled: true\n"
        "  max_shares_per_order: 0\n"
        "  max_notional_ratio: 0.25\n"
        "  max_orders_per_day: 1000000\n"
        "  require_first_order_approval: false\n"
        "entry:\n"
        "  cooldown_after_buy_hours: 0\n",
        encoding="utf-8",
    )
    sell_broker = FakeBroker("FIX", shares=12.0, price=2000.0, pending=False)
    sell_broker.get_balance = lambda: Balance(100000, 50000, 24000, sell_broker.get_holdings())
    sell_safety = SafetyLayer(broker=sell_broker, policy_path=policy)

    buy_broker = FakeBroker("FIX", shares=0.0, price=2000.0, pending=False)
    buy_broker.get_balance = lambda: Balance(100000, 50000, 0.0, [])
    buy_safety = SafetyLayer(broker=buy_broker, policy_path=policy)

    sell = sell_safety.check_order("SELL", "FIX", 12.0, 2000.0, purpose="exit")
    buy = buy_safety.check_order("BUY", "FIX", 12.0, 2000.0, purpose="entry")

    assert sell.allowed, sell
    assert not buy.allowed
    assert buy.code == "LIMIT_NOTIONAL"
