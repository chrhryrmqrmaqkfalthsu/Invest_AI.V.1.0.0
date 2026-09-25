from __future__ import annotations

from pathlib import Path

from engine.central.allocation_policy import BuyDecision
from engine.live.scheduled_open_buy_queue import (
    decision_to_queue_item,
    load_queue,
    mark_item_status,
    pending_items,
    save_queue,
)


def test_queue_roundtrip_and_status_update(tmp_path: Path):
    path = tmp_path / "queue.json"
    decision = BuyDecision(
        entity_id="AAA_abc123def456",
        ticker="AAA",
        shares=10.0,
        notional=1000.0,
        score=3.0,
        confidence=2.0,
        strength=1.0,
        rulebook={"ticker": "AAA", "stop_loss_atr": 1.2},
    )
    item = decision_to_queue_item(
        decision,
        signal_session="2026-06-26",
        execution_session="2026-06-29",
        reference_price=100.0,
        signal_score=2.5,
        signal_threshold=1.5,
        stage="stage2",
    )
    save_queue({"schema_version": 1, "items": [item]}, path)

    loaded = load_queue(path)
    rows = pending_items(loaded, "2026-06-29")
    assert len(rows) == 1
    assert rows[0]["entity_id"] == "AAA_abc123def456"
    assert rows[0]["rulebook"]["stop_loss_atr"] == 1.2

    mark_item_status(loaded, rows[0]["candidate_id"], "executed", fills={"reference_price": 101.0})
    assert pending_items(loaded, "2026-06-29") == []
    assert loaded["items"][0]["status"] == "executed"
    assert loaded["items"][0]["fills"]["reference_price"] == 101.0


def test_load_missing_queue_returns_empty_payload(tmp_path: Path):
    payload = load_queue(tmp_path / "missing.json")
    assert payload["schema_version"] == 1
    assert payload["items"] == []
