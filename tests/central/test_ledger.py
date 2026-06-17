import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.central.broker_port import MockBroker
from engine.central.ledger import EntityPositionLedger, LedgerUnavailableError, ReconcileBlockedError
from engine.live.broker.base import OrderStatus


def ledger(tmp_path):
    return EntityPositionLedger(base_dir=tmp_path)


def buy(l, broker, entity="entity-A", ticker="CW", shares=10, price=100, purpose="entry", target_position_id=None):
    broker.queue_order(status=OrderStatus.FILLED, filled_shares=shares, filled_avg_price=price)
    intent = l.open_intent(entity, ticker, "buy", purpose, shares, "test buy", target_position_id)
    execution = l.dispatch_execution(intent.intent_id, broker, f"cid-{entity}-{ticker}-{purpose}-{shares}-{price}")
    return l.get_position(execution.position_id), execution


def sell(l, broker, position_id, entity="entity-A", ticker="CW", shares=4, price=110):
    broker.queue_order(status=OrderStatus.FILLED, filled_shares=shares, filled_avg_price=price)
    intent = l.open_intent(entity, ticker, "sell", "exit", shares, "test sell", position_id)
    execution = l.dispatch_execution(intent.intent_id, broker, f"cid-sell-{entity}-{ticker}-{shares}-{price}")
    return l.get_position(position_id), execution


def test_immediate_full_fill_creates_position_with_average_price(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()

    pos, execution = buy(l, broker, shares=10, price=123.45)

    assert execution.already_applied_filled_shares == 10
    assert pos.opened_shares == 10
    assert pos.open_shares == 10
    assert pos.closed_shares == 0
    assert pos.avg_entry_price == pytest.approx(123.45)
    assert pos.entity_id == "entity-A"
    assert pos.ticker == "CW"


def test_partial_fills_apply_only_delta_and_weighted_average(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    cumulative_avg_7 = (4 * 100 + 3 * 110) / 7
    cumulative_avg_10 = (4 * 100 + 3 * 110 + 3 * 120) / 10
    broker.queue_order(
        status=OrderStatus.PARTIAL,
        filled_shares=4,
        filled_avg_price=100,
        poll_sequence=[
            {"status": OrderStatus.PARTIAL, "filled_shares": 7, "filled_avg_price": cumulative_avg_7},
            {"status": OrderStatus.FILLED, "filled_shares": 10, "filled_avg_price": cumulative_avg_10},
        ],
    )

    intent = l.open_intent("entity-A", "CW", "buy", "entry", 10, "partial")
    execution = l.dispatch_execution(intent.intent_id, broker, "cid-partial")
    pos = l.get_position(execution.position_id)
    assert pos.open_shares == 4
    assert pos.avg_entry_price == pytest.approx(100)

    order = broker.get_order(execution.order_id)
    l.apply_fill(execution.execution_id, order)
    pos = l.get_position(execution.position_id)
    assert pos.open_shares == 7
    assert pos.opened_shares == 7
    assert pos.avg_entry_price == pytest.approx(cumulative_avg_7)

    # Re-applying the same cumulative order must not double count.
    l.apply_fill(execution.execution_id, order)
    pos = l.get_position(execution.position_id)
    assert pos.open_shares == 7
    assert pos.opened_shares == 7

    order = broker.get_order(execution.order_id)
    l.apply_fill(execution.execution_id, order)
    pos = l.get_position(execution.position_id)
    assert pos.open_shares == 10
    assert pos.opened_shares == 10
    assert pos.avg_entry_price == pytest.approx(cumulative_avg_10)


def test_add_buy_updates_same_position_average_and_count(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    pos, _ = buy(l, broker, shares=10, price=100)

    pos2, _ = buy(l, broker, shares=5, price=120, purpose="add_buy", target_position_id=pos.position_id)

    assert pos2.position_id == pos.position_id
    assert pos2.opened_shares == 15
    assert pos2.open_shares == 15
    assert pos2.avg_entry_price == pytest.approx((10 * 100 + 5 * 120) / 15)
    assert pos2.add_buy_count == 1


def test_same_ticker_entities_are_independent_positions(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    a, _ = buy(l, broker, entity="entity-A", ticker="CW", shares=10, price=100)
    b, _ = buy(l, broker, entity="entity-B", ticker="CW", shares=5, price=200)

    assert a.position_id != b.position_id
    assert a.entity_id == "entity-A"
    assert b.entity_id == "entity-B"
    assert a.open_shares == 10
    assert b.open_shares == 5
    assert len(l.open_positions(ticker="CW")) == 2
    assert sum(p.open_shares for p in l.open_positions(ticker="CW")) == 15


def test_entity_specific_sell_changes_only_target_position(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    a, _ = buy(l, broker, entity="entity-A", ticker="CW", shares=10, price=100)
    b, _ = buy(l, broker, entity="entity-B", ticker="CW", shares=5, price=200)

    a_after, _ = sell(l, broker, a.position_id, entity="entity-A", ticker="CW", shares=4, price=110)
    b_after = l.get_position(b.position_id)

    assert a_after.open_shares == 6
    assert a_after.closed_shares == 4
    assert a_after.realized_pnl == pytest.approx(40)
    assert b_after.open_shares == 5
    assert b_after.closed_shares == 0
    assert b_after.realized_pnl == 0


def test_reconcile_success_when_ledger_sum_equals_broker_holding(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    buy(l, broker, entity="entity-A", ticker="CW", shares=10, price=100)
    buy(l, broker, entity="entity-B", ticker="CW", shares=5, price=200)

    report = l.reconcile(broker)

    assert report["ok"] is True
    assert report["discrepancies"] == []
    assert report["ledger_shares_by_ticker"]["CW"] == 15
    assert report["broker_shares_by_ticker"]["CW"] == 15


def test_reconcile_failure_blocks_future_intents_for_ticker(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    buy(l, broker, ticker="CW", shares=10, price=100)
    broker.set_holding("CW", shares=9, avg_cost=100)

    report = l.reconcile(broker)

    assert report["ok"] is False
    assert report["discrepancies"][0]["kind"] == "mismatch"
    assert "CW" in report["blocked_tickers"]
    with pytest.raises(ReconcileBlockedError):
        l.open_intent("entity-C", "CW", "buy", "entry", 1, "must be blocked")


def test_reconcile_detects_broker_only_and_ledger_only_orphans(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    buy(l, broker, ticker="LEDGERONLY", shares=3, price=50)
    broker.set_holding("LEDGERONLY", shares=0)
    broker.set_holding("BROKERONLY", shares=2, avg_cost=25)

    report = l.reconcile(broker)
    by_ticker = {d["ticker"]: d for d in report["discrepancies"]}

    assert by_ticker["LEDGERONLY"]["kind"] == "ledger-only"
    assert by_ticker["BROKERONLY"]["kind"] == "broker-only"
    assert set(report["blocked_tickers"]) == {"BROKERONLY", "LEDGERONLY"}


def test_persistence_roundtrip_preserves_records(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    pos, execution = buy(l, broker, shares=10, price=101)
    intent = l.get_intent(execution.intent_id)

    loaded = ledger(tmp_path)

    assert loaded.load_error == ""
    assert loaded.get_position(pos.position_id).to_dict() == pos.to_dict()
    assert loaded.get_execution(execution.execution_id).to_dict() == execution.to_dict()
    assert loaded.get_intent(intent.intent_id).to_dict() == intent.to_dict()


def test_corrupt_load_is_fail_closed_for_new_intents(tmp_path):
    positions_path = tmp_path / "ledger_positions.json"
    positions_path.write_text("{not-json", encoding="utf-8")

    l = ledger(tmp_path)

    assert l.load_error
    with pytest.raises(LedgerUnavailableError):
        l.open_intent("entity-A", "CW", "buy", "entry", 1, "blocked")


def test_pending_and_rejected_orders_do_not_create_positions(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()

    broker.queue_order(status=OrderStatus.PENDING, filled_shares=0, filled_avg_price=0)
    pending_intent = l.open_intent("entity-A", "CW", "buy", "entry", 10, "pending")
    pending_execution = l.dispatch_execution(pending_intent.intent_id, broker, "cid-pending")
    assert pending_execution.state == "pending"
    assert l.open_positions() == []

    broker.queue_order(status=OrderStatus.REJECTED, filled_shares=0, filled_avg_price=0, message="reject")
    rejected_intent = l.open_intent("entity-B", "WELL", "buy", "entry", 10, "rejected")
    rejected_execution = l.dispatch_execution(rejected_intent.intent_id, broker, "cid-rejected")
    assert rejected_execution.state == "rejected"
    assert l.open_positions(ticker="WELL") == []


def test_saved_files_use_schema_version_and_records_envelope(tmp_path):
    l = ledger(tmp_path)
    broker = MockBroker()
    buy(l, broker, shares=1, price=100)

    payload = json.loads((tmp_path / "ledger_positions.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert "updated_at" in payload
    assert "records" in payload
