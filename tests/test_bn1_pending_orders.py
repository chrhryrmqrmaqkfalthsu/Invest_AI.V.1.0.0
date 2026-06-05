from __future__ import annotations

import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.live.broker.base import Balance, Broker, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.pending_order_manager import PendingOrderManager
from engine.live.position_manager import PositionEntry, PositionManager
from engine.live.runner import Runner
from engine.live.safety.layer import SafetyLayer


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, msg, **kwargs):
        self.messages.append((msg, kwargs))

    def send_order(self, order):
        self.messages.append(("order", order.order_id))

    def send_error(self, msg):
        self.messages.append(("error", msg))

    def send_safety_block(self, code, msg):
        self.messages.append(("block", code, msg))


class FakeBroker(Broker):
    def __init__(self, *, price=100.0, shares=1.0, orders=None, sell_status=OrderStatus.FILLED):
        self.price = price
        self.holdings = {"AAA": shares} if shares > 0 else {}
        self.orders = dict(orders or {})
        self.sell_status = sell_status
        self.sell_calls = 0
        self.buy_calls = 0

    @property
    def mode(self) -> str:
        return "paper"

    def get_balance(self):
        return Balance(cash_krw=100000, total_value_krw=100000, invested_krw=0, holdings=self.get_holdings())

    def get_holdings(self):
        return [Holding(t, s, 90.0, self.price, s * self.price, 0.0, 0.0) for t, s in self.holdings.items() if s > 0]

    def get_current_price(self, ticker):
        return self.price

    def is_market_open(self, ticker=None):
        return True

    def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0):
        self.buy_calls += 1
        order = Order(f"B{self.buy_calls}", ticker, OrderSide.BUY, order_type, shares, price, OrderStatus.FILLED, shares, self.price)
        self.orders[order.order_id] = order
        self.holdings[ticker] = self.holdings.get(ticker, 0.0) + shares
        return order

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0):
        self.sell_calls += 1
        order = Order(f"S{self.sell_calls}", ticker, OrderSide.SELL, order_type, shares, price, self.sell_status)
        if self.sell_status == OrderStatus.FILLED:
            order.filled_shares = shares
            order.filled_avg_price = self.price
            self.holdings[ticker] = max(0.0, self.holdings.get(ticker, 0.0) - shares)
            if self.holdings[ticker] <= 1e-6:
                self.holdings.pop(ticker, None)
        self.orders[order.order_id] = order
        return order

    def cancel_order(self, order_id):
        return True

    def get_order(self, order_id):
        return self.orders.get(order_id)


def make_order(order_id="O1", ticker="AAA", side=OrderSide.SELL, status=OrderStatus.PENDING, shares=1.0, filled=0.0, price=100.0):
    return Order(
        order_id=order_id,
        ticker=ticker,
        side=side,
        order_type=OrderType.MARKET,
        shares=shares,
        price=0.0,
        status=status,
        filled_shares=filled,
        filled_avg_price=price if filled else 0.0,
        raw_status=status.value,
    )


def patch_position_paths(monkeypatch, tmp_path):
    import engine.live.position_manager as pm_mod

    positions = tmp_path / "positions.json"
    trade_log = tmp_path / "trade_log.csv"
    monkeypatch.setattr(pm_mod, "POSITIONS_PATH", positions)
    monkeypatch.setattr(pm_mod, "TRADE_LOG_PATH", trade_log)
    return positions, trade_log


def seed_position(pm: PositionManager, ticker="AAA", shares=1.0):
    pm._positions[ticker] = PositionEntry(
        ticker=ticker,
        entry_date="2026-01-01T09:00:00+09:00",
        entry_price=100.0,
        shares=shares,
        atr_at_entry=1.0,
        stop_price=95.0,
        target_price=110.0,
        trailing_distance=3.0,
        trailing_stop=96.0,
        highest_price=105.0,
        exit_strategy="fixed",
        max_holding_days=99,
        rulebook_direction="long",
        total_invested_krw=100.0 * shares,
    )
    pm._save()


def trade_rows(path: Path):
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_01_immediate_filled_buy_not_tracked_and_no_pending_file(tmp_path):
    mgr = PendingOrderManager(FakeBroker(), path=tmp_path / "pending.json")
    rec = mgr.track_order(make_order(side=OrderSide.BUY, status=OrderStatus.FILLED, filled=1.0), purpose="entry")
    assert rec is None
    assert not (tmp_path / "pending.json").exists()


def test_02_immediate_filled_sell_trade_log_and_unregister(monkeypatch, tmp_path):
    _, trade_log = patch_position_paths(monkeypatch, tmp_path)
    pm = PositionManager()
    seed_position(pm)
    broker = FakeBroker(price=103.0, shares=0.0)
    order = make_order(status=OrderStatus.FILLED, filled=1.0, price=103.0)
    pm.finalize_sell_fill(order, "take_profit", broker, FakeNotifier())
    assert pm.get("AAA") is None
    rows = trade_rows(trade_log)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAA"


def test_03_pending_sell_multiple_ticks_fires_once(monkeypatch, tmp_path):
    patch_position_paths(monkeypatch, tmp_path)
    pm = PositionManager()
    seed_position(pm)
    broker = FakeBroker(price=94.0, shares=1.0, sell_status=OrderStatus.PENDING)
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    pm.check_exits(broker, FakeNotifier(), pending_manager=pending)
    pm.check_exits(broker, FakeNotifier(), pending_manager=pending)
    assert broker.sell_calls == 1
    assert pending.has_pending_exit("AAA")


def test_04_pending_sell_filled_pnl_once(monkeypatch, tmp_path):
    _, trade_log = patch_position_paths(monkeypatch, tmp_path)
    pm = PositionManager()
    seed_position(pm)
    order = make_order("S1", status=OrderStatus.PENDING)
    broker = FakeBroker(price=104.0, shares=0.0, orders={"S1": make_order("S1", status=OrderStatus.FILLED, filled=1.0, price=104.0)})
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    pending.track_order(order, purpose="exit", exit_reason="take_profit")
    for rec, filled in pending.poll_all():
        pm.finalize_sell_fill(filled, rec.exit_reason, broker, FakeNotifier())
        pending.mark_finalized(rec.order_id)
    assert len(trade_rows(trade_log)) == 1
    assert pending.poll_all() == []


def test_05_holdings_disappear_with_pending_does_not_unregister_without_reconcile(monkeypatch, tmp_path):
    patch_position_paths(monkeypatch, tmp_path)
    pm = PositionManager()
    seed_position(pm)
    broker = FakeBroker(price=94.0, shares=0.0, orders={"S1": None})
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    pending.track_order(make_order("S1", status=OrderStatus.PENDING), purpose="exit")
    pm.check_exits(broker, FakeNotifier(), pending_manager=pending)
    assert pm.get("AAA") is not None


def test_06_partial_then_cancel_keeps_residual_position(monkeypatch, tmp_path):
    patch_position_paths(monkeypatch, tmp_path)
    pm = PositionManager()
    seed_position(pm, shares=1.0)
    broker = FakeBroker(price=102.0, shares=0.6)
    order = make_order("S1", status=OrderStatus.CANCELLED, shares=1.0, filled=0.4, price=102.0)
    pm.finalize_sell_fill(order, "cancelled_partial", broker, FakeNotifier())
    assert pm.get("AAA") is not None
    assert pm.get("AAA").shares == pytest.approx(0.6)


def test_07_pending_buy_filled_registers_entry(monkeypatch, tmp_path):
    patch_position_paths(monkeypatch, tmp_path)
    runner = Runner.__new__(Runner)
    runner.safety = SimpleNamespace(record_fill=lambda *a, **k: None)
    runner.notifier = FakeNotifier()
    runner.broker = FakeBroker(price=100.0, shares=1.0)
    runner.approval_manager = SimpleNamespace(get_request=lambda rid: None, _save=lambda: None)
    runner.position_manager = PositionManager()
    rb = SimpleNamespace(
        direction="long", exit_strategy="fixed", max_holding_days=10,
        stop_loss_atr=2.0, take_profit_atr=3.0, trailing_atr=2.0,
        win_rate=0.8, to_dict=lambda: {}, sector_name="",
    )
    runner.rulebook = SimpleNamespace(
        get_last_atr=lambda ticker: 1.0,
        get_rulebook=lambda ticker: rb,
        get_last_market_context=lambda ticker: None,
    )
    rec = SimpleNamespace(side="buy", purpose="entry", approval_request_id="", order_id="B1")
    order = make_order("B1", side=OrderSide.BUY, status=OrderStatus.FILLED, filled=1.0, price=100.0)
    runner._finalize_pending_order(rec, order)
    assert runner.position_manager.get("AAA") is not None


def test_08_get_order_none_keeps_lock(tmp_path):
    broker = FakeBroker(price=100.0, shares=1.0, orders={})
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    pending.track_order(make_order("S1"), purpose="exit")
    assert pending.poll_all() == []
    assert pending.is_ticker_locked("AAA")
    assert pending.get_record("S1").state == "UNKNOWN_OPEN"


def test_09_restart_loads_and_polls_pending_first(tmp_path):
    broker = FakeBroker(price=100.0, shares=1.0, orders={"S1": make_order("S1", status=OrderStatus.FILLED, filled=1.0, price=101.0)})
    path = tmp_path / "pending.json"
    PendingOrderManager(broker, path=path).track_order(make_order("S1"), purpose="exit")
    restarted = PendingOrderManager(broker, path=path)
    events = restarted.poll_all()
    assert len(events) == 1
    assert events[0][1].status == OrderStatus.FILLED


def test_10_auto_sell_then_general_sell_same_tick_blocked(monkeypatch, tmp_path):
    patch_position_paths(monkeypatch, tmp_path)
    pm = PositionManager()
    seed_position(pm)
    broker = FakeBroker(price=94.0, shares=1.0, sell_status=OrderStatus.PENDING)
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    pm.check_exits(broker, FakeNotifier(), pending_manager=pending)
    assert pending.is_ticker_locked("AAA")
    assert broker.sell_calls == 1
    pm.check_exits(broker, FakeNotifier(), pending_manager=pending)
    assert broker.sell_calls == 1


def test_11_paper_like_immediate_filled_regression_no_pending_file(tmp_path):
    broker = FakeBroker(price=100.0, shares=0.0)
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    order = broker.place_buy("AAA", 1.0)
    assert order.status == OrderStatus.FILLED
    assert pending.track_order(order, purpose="entry") is None
    assert not (tmp_path / "pending.json").exists()
