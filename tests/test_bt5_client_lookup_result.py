from __future__ import annotations

from engine.live.broker.base import Order, OrderSide, OrderStatus, OrderType
from engine.live.broker.alpaca import CLIENT_LOOKUP_FOUND, CLIENT_LOOKUP_NOT_FOUND, CLIENT_LOOKUP_UNKNOWN, AlpacaBroker
from engine.live.pending_order_manager import PendingOrderManager, STATE_UNKNOWN_OPEN


class LookupBroker:
    mode = "alpaca_paper"

    def __init__(self, status, order=None):
        self.status = status
        self.order = order

    def get_order_by_client_order_id_result(self, cid):
        return self.status, self.order


def test_lookup_found_recovers_order(tmp_path):
    cid = PendingOrderManager.make_client_order_id(ticker="AAA", side="buy", purpose="entry", seed="found")
    order = Order("B1", "AAA", OrderSide.BUY, OrderType.MARKET, 1, 0, OrderStatus.PENDING, client_order_id=cid)
    pending = PendingOrderManager(LookupBroker(CLIENT_LOOKUP_FOUND, order), path=tmp_path / "p.json")
    pending.create_submitting_intent(client_order_id=cid, ticker="AAA", side="buy", purpose="entry", requested_shares=1)

    assert pending.resolve_submit_exception(cid).order_id == "B1"
    assert pending.get_record("B1") is not None


def test_lookup_not_found_discards_intent(tmp_path):
    cid = PendingOrderManager.make_client_order_id(ticker="AAA", side="buy", purpose="entry", seed="notfound")
    pending = PendingOrderManager(LookupBroker(CLIENT_LOOKUP_NOT_FOUND), path=tmp_path / "p.json")
    pending.create_submitting_intent(client_order_id=cid, ticker="AAA", side="buy", purpose="entry", requested_shares=1)

    assert pending.resolve_submit_exception(cid) is None
    assert pending.get_record_by_client_order_id(cid) is None
    assert not pending.is_ticker_locked("AAA")


def test_lookup_unknown_keeps_fail_closed_lock(tmp_path):
    cid = PendingOrderManager.make_client_order_id(ticker="AAA", side="buy", purpose="entry", seed="unknown")
    pending = PendingOrderManager(LookupBroker(CLIENT_LOOKUP_UNKNOWN), path=tmp_path / "p.json")
    pending.create_submitting_intent(client_order_id=cid, ticker="AAA", side="buy", purpose="entry", requested_shares=1)

    assert pending.resolve_submit_exception(cid) is None
    rec = pending.get_record_by_client_order_id(cid)
    assert rec.state == STATE_UNKNOWN_OPEN
    assert pending.is_ticker_locked("AAA")


def test_alpaca_exception_classification_without_network():
    class E404(Exception):
        status_code = 404

    class E500(Exception):
        status_code = 500

    assert AlpacaBroker._is_not_found_exception(E404("missing")) is True
    assert AlpacaBroker._is_not_found_exception(Exception("404 not found")) is True
    assert AlpacaBroker._is_not_found_exception(E500("server down")) is False
