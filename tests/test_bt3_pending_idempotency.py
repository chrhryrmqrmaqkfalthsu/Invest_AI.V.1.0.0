from __future__ import annotations

from engine.live.broker.base import Order, OrderSide, OrderStatus, OrderType
from engine.live.pending_order_manager import PendingOrderManager
from tests.test_bn2_client_order_id_recovery import B


def test_track_order_same_bn2_order_preserves_created_at_and_metadata(tmp_path):
    broker = B()
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    cid = pending.make_client_order_id(ticker="AAA", side="buy", purpose="entry", seed="dup")
    pending.create_submitting_intent(
        client_order_id=cid,
        ticker="AAA",
        side="buy",
        purpose="entry",
        requested_shares=1,
        metadata={"first": "kept"},
    )
    order = Order(
        "B1", "AAA", OrderSide.BUY, OrderType.MARKET, 1, 0,
        OrderStatus.PENDING, client_order_id=cid,
    )
    rec1 = pending.mark_submitted(cid, order, purpose="entry", metadata={"second": "kept"})
    created = rec1.created_at

    rec2 = pending.track_order(order, purpose="entry", metadata={"third": "ignored"})

    assert rec2 is rec1
    assert pending.get_record("B1").created_at == created
    assert pending.get_record("B1").metadata == {"first": "kept", "second": "kept"}
