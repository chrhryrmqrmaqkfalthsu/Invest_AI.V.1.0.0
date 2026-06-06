from __future__ import annotations

from types import SimpleNamespace

from engine.live.broker.base import Order, OrderSide, OrderStatus, OrderType
from engine.live.broker.market_aware import GuardedKisBroker


def test_guarded_kis_forwards_client_order_id_without_typeerror(monkeypatch):
    broker = object.__new__(GuardedKisBroker)
    calls = []

    class Inner:
        def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
            calls.append(("buy", ticker, shares, client_order_id))
            return Order("K1", ticker, OrderSide.BUY, order_type, shares, price, OrderStatus.PENDING)

        def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
            calls.append(("sell", ticker, shares, client_order_id))
            return Order("K2", ticker, OrderSide.SELL, order_type, shares, price, OrderStatus.PENDING)

    broker._inner = Inner()
    monkeypatch.setattr("engine.live.broker.market_aware.market_region_for_ticker", lambda ticker: "KRX")

    broker.place_buy("005930", 1, client_order_id="ignored-buy")
    broker.place_sell("005930", 1, client_order_id="ignored-sell")

    assert calls == [("buy", "005930", 1, "ignored-buy"), ("sell", "005930", 1, "ignored-sell")]


def test_kis_and_paper_signatures_accept_client_order_id():
    from engine.live.broker.kis import KisBroker
    from engine.live.broker.paper import PaperBroker

    assert "client_order_id" in PaperBroker.place_buy.__code__.co_varnames
    assert "client_order_id" in PaperBroker.place_sell.__code__.co_varnames
    assert "client_order_id" in KisBroker.place_buy.__code__.co_varnames
    assert "client_order_id" in KisBroker.place_sell.__code__.co_varnames
