from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine.live.broker.base import Balance, Broker, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.buy_reconciliation import BuyReconciliationService
from engine.live.pending_order_manager import PendingOrderManager
from engine.strategies.rulebook import Rulebook


class BrokerStub(Broker):
    @property
    def mode(self):
        return "paper"

    def get_balance(self):
        return Balance(cash_krw=0, total_value_krw=0, invested_krw=0, holdings=[])

    def get_holdings(self):
        return []

    def get_current_price(self, ticker):
        return 100.0

    def is_market_open(self, ticker=None):
        return True

    def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
        raise NotImplementedError

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
        raise NotImplementedError

    def cancel_order(self, order_id):
        return False

    def get_order(self, order_id):
        return None


class ProviderMustNotFallback:
    def get_last_atr(self, ticker):
        raise AssertionError("central pending reconcile must not call ticker-scoped ATR fallback")

    def get_rulebook(self, ticker):
        raise AssertionError("central pending reconcile must not call ticker-scoped rulebook fallback")

    def get_last_market_context(self, ticker):
        return None


class ProviderAllowsLegacyFallback:
    def get_last_atr(self, ticker):
        return 1.25

    def get_rulebook(self, ticker):
        return Rulebook.from_dict(
            {
                "ticker": ticker,
                "asset_type": "us_stock",
                "direction": "long",
                "version": "v5",
                "signal_threshold": 1.0,
                "stop_loss_atr": 2.0,
                "take_profit_atr": 3.0,
                "trailing_atr": 1.0,
                "max_holding_days": 5,
                "exit_strategy": "fixed",
                "fitness": 1.0,
                "win_rate": 60.0,
                "trade_count": 20,
            }
        )

    def get_last_market_context(self, ticker):
        return {"score": 50.0}


class RecordingPositionManager:
    def __init__(self):
        self.created = None

    def get(self, ticker):
        return self.created

    def register_entry(self, ticker, price, shares, rulebook, atr, entry_market_context=None):
        self.created = SimpleNamespace(
            ticker=ticker,
            price=price,
            shares=shares,
            rulebook=rulebook,
            atr=atr,
            entry_market_context=entry_market_context,
        )
        return self.created

    def add_to_position(self, *args, **kwargs):
        raise AssertionError("not an add_buy test")


def _filled_buy(order_id="B1"):
    return Order(
        order_id=order_id,
        ticker="AAA",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        shares=1.0,
        price=0.0,
        status=OrderStatus.FILLED,
        filled_shares=1.0,
        filled_avg_price=100.0,
        raw_status="filled",
    )


def _pending_manager(tmp_path, metadata):
    broker = BrokerStub()
    manager = PendingOrderManager(broker, path=tmp_path / "pending.json")
    manager.track_order(
        Order("B1", "AAA", OrderSide.BUY, OrderType.MARKET, 1.0, 0.0, OrderStatus.PENDING),
        purpose="entry",
        metadata=metadata,
    )
    return broker, manager


@pytest.mark.parametrize(
    "metadata",
    [
        {"reason": "central_control next_open_queue AAA_abc123def456"},
        {"reason": "central_control next_open_queue AAA_abc123def456", "selected_rulebook": "broken", "selected_rulebook_hash": "abc123def456"},
    ],
)
def test_central_pending_entry_missing_or_broken_rulebook_fails_closed_without_ticker_fallback(tmp_path, metadata):
    broker, pending = _pending_manager(tmp_path, metadata)
    svc = BuyReconciliationService(
        broker=broker,
        rulebook_provider=ProviderMustNotFallback(),
        position_manager=RecordingPositionManager(),
        pending_manager=pending,
    )

    with pytest.raises(RuntimeError, match="fail-closed"):
        svc.reconcile(_filled_buy(), purpose="entry")


@pytest.mark.parametrize(
    "metadata",
    [
        {"entity_id": "AAA_abc123def456"},
        {"selected_rulebook_hash": "abc123def456"},
    ],
)
def test_entity_marked_pending_entry_without_rulebook_fails_closed(tmp_path, metadata):
    broker, pending = _pending_manager(tmp_path, metadata)
    svc = BuyReconciliationService(
        broker=broker,
        rulebook_provider=ProviderMustNotFallback(),
        position_manager=RecordingPositionManager(),
        pending_manager=pending,
    )

    with pytest.raises(RuntimeError, match="ticker-scoped rulebook fallback 금지"):
        svc.reconcile(_filled_buy(), purpose="entry")


def test_legacy_non_central_pending_entry_can_still_use_ticker_fallback(tmp_path):
    broker, pending = _pending_manager(tmp_path, {"reason": "legacy_signal"})
    positions = RecordingPositionManager()
    svc = BuyReconciliationService(
        broker=broker,
        rulebook_provider=ProviderAllowsLegacyFallback(),
        position_manager=positions,
        pending_manager=pending,
    )

    created = svc.reconcile(_filled_buy(), purpose="entry")

    assert created is positions.created
    assert created.atr == 1.25
    assert created.rulebook.ticker == "AAA"
