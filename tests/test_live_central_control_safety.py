from __future__ import annotations

from types import SimpleNamespace

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.buy_reconciliation import BuyReconciliationService
from engine.live.central_control import LiveCentralController
from engine.live.pending_order_manager import PendingOrderManager
from engine.strategies.demo_rulebook import Signal, SignalResult


class Stats:
    market_ticks = 0
    signals_buy = 0
    signals_sell = 0
    signals_hold = 0


class Pending:
    load_error = ""

    def __init__(self, records=None):
        self._records = list(records or [])

    def all(self):
        return list(self._records)

    def is_ticker_locked(self, ticker):
        return False


class Broker:
    mode = "paper"

    def __init__(self, *, holdings=None, price=100.0):
        self.price = price
        self._holdings = list(holdings or [])

    def get_current_price(self, ticker):
        return self.price

    def get_holdings(self):
        return list(self._holdings)

    def get_balance(self):
        return SimpleNamespace(total_value_usd=100_000.0)


class PositionManager:
    load_error = ""

    def __init__(self, positions=None):
        self._positions = list(positions or [])

    def all(self):
        return list(self._positions)


class SellRulebook:
    def evaluate(self, ticker, price):
        return SignalResult(ticker=ticker, signal=Signal.SELL, price=price, reason="test sell")


class Runner:
    def __init__(self, *, rulebook=None, positions=None, pending=None, holdings=None):
        self.symbols = ["AAA"]
        self.broker = Broker(holdings=holdings or [])
        self.rulebook = rulebook or SellRulebook()
        self.position_manager = PositionManager(positions or [])
        self.pending_order_manager = Pending(pending or [])
        self.stats = Stats()
        self.orders = []

    def _maybe_reconfirm_existing(self, ticker, price):
        pass

    def _try_order(self, side, ticker, price, reason, signal_result=None):
        self.orders.append((side, ticker, price, reason))


def make_controller(runner):
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.runner = runner
    ctl.config = SimpleNamespace(
        max_positions=8,
        confidence_weight=0.5,
        signal_strength_weight=0.5,
        min_confidence=0.0,
        per_ticker_exposure_cap=0.25,
        cash_buffer_ratio=0.98,
    )
    ctl.selection_metric = "confidence"
    ctl.position_sizing = "score_weighted"
    ctl.confidence_mode = "adjusted"
    ctl.selection_scores = {}
    ctl.entity_by_ticker = {
        "AAA": [SimpleNamespace(entity_id="AAA_entity", ticker="AAA", confidence=1.0, rulebook={})]
    }
    return ctl


def test_central_control_never_emits_sell_order():
    runner = Runner(rulebook=SellRulebook())
    ctl = make_controller(runner)

    ctl._process_central_buy_selection()

    assert runner.orders == []
    assert runner.stats.signals_sell == 1


def test_pending_buy_and_orphan_holding_reduce_live_slots():
    pending_buy = SimpleNamespace(
        ticker="BBB",
        side="buy",
        state="RECONCILING",
        requested_shares=1.0,
        filled_shares=0.0,
        filled_avg_price=0.0,
        order_id="B1",
    )
    orphan = Holding("CCC", 1.0, 100.0, 100.0, 100.0, 0.0, 0.0)
    runner = Runner(pending=[pending_buy], holdings=[orphan])
    ctl = make_controller(runner)

    ledger = ctl._build_live_ledger_view()
    tickers = {p.ticker for p in ledger.open_positions()}

    assert tickers == {"BBB", "CCC"}
    candidates = [BuyCandidate("ddd", "DDD", confidence=1.0, strength=1.0, price=100.0)]
    params = AllocationParams(max_positions=2, total_capital=10_000.0, per_ticker_exposure_cap=1.0)
    assert decide_buys(candidates, ledger, params) == []


def test_allocation_applies_cash_buffer_and_current_price_exposure_cap():
    class Ledger:
        def __init__(self, positions=None):
            self.positions = list(positions or [])

        def open_positions(self):
            return list(self.positions)

    one = [BuyCandidate("aaa", "AAA", confidence=1.0, strength=1.0, price=100.0)]
    buffered = decide_buys(
        one,
        Ledger(),
        AllocationParams(
            max_positions=1,
            total_capital=10_000.0,
            per_ticker_exposure_cap=1.0,
            position_sizing="equal",
            cash_buffer_ratio=0.80,
        ),
    )
    assert len(buffered) == 1
    assert buffered[0].notional <= 8_000.0 + 1e-6

    held = SimpleNamespace(
        entity_id="aaa",
        ticker="AAA",
        open_shares=10.0,
        avg_entry_price=100.0,
        current_price=200.0,
        add_buy_count=0,
        position_id="pos-aaa",
    )
    add_buy = [
        BuyCandidate(
            "aaa",
            "AAA",
            confidence=1.0,
            strength=1.0,
            price=200.0,
            rulebook={"add_buy_enabled": True, "add_buy_max_count": 1},
        )
    ]
    capped = decide_buys(
        add_buy,
        Ledger([held]),
        AllocationParams(
            max_positions=1,
            total_capital=10_000.0,
            per_ticker_exposure_cap=0.25,
            position_sizing="equal",
            cash_buffer_ratio=1.0,
        ),
    )
    assert len(capped) == 1
    assert capped[0].notional <= 500.0 + 1e-6


def test_buy_reconciliation_drops_pending_after_retry_limit(tmp_path):
    class RulebookProvider:
        def get_last_atr(self, ticker):
            return None

        def get_rulebook(self, ticker):
            return object()

    class PM:
        pass

    pending = PendingOrderManager(Broker(), path=tmp_path / "pending.json")
    svc = BuyReconciliationService(
        broker=Broker(),
        rulebook_provider=RulebookProvider(),
        position_manager=PM(),
        pending_manager=pending,
        max_reconcile_retries=1,
    )
    order = Order(
        order_id="B1",
        ticker="AAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        shares=1.0,
        price=0.0,
        status=OrderStatus.FILLED,
        filled_shares=1.0,
        filled_avg_price=100.0,
    )

    svc.track_failure(order, purpose="entry", error="no atr")

    assert pending.all() == []
