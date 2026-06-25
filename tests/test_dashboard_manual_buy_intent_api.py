from __future__ import annotations

import inspect
from pathlib import Path

from fastapi.testclient import TestClient

import api_server
from engine.live.manual_buy_intent import atomic_write_json, candidate_id_for


def test_dashboard_manual_buy_intent_endpoint_writes_file_without_broker_import(tmp_path, monkeypatch):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    trade_date = "2026-06-25"
    cid = candidate_id_for(trade_date, "AAA_entity")
    atomic_write_json(
        candidate_path,
        {
            "schema_version": 1,
            "trade_date": trade_date,
            "buy_mode": "semi_auto",
            "updated_at": "2026-06-25T00:00:00Z",
            "candidates": {
                cid: {
                    "candidate_id": cid,
                    "trade_date": trade_date,
                    "status": "pending",
                    "ticker": "AAA",
                    "entity_id": "AAA_entity",
                    "notional": 1234.5,
                    "price": 100.0,
                }
            },
        },
    )
    monkeypatch.setattr(api_server, "CENTRAL_BUY_CANDIDATES_PATH", candidate_path)
    monkeypatch.setattr(api_server, "MANUAL_BUY_INTENT_PATH", intent_path)
    client = TestClient(api_server.app)

    res = client.post("/api/live/manual_buy_intent", json={"candidate_id": cid, "source": "test-dashboard"})

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["intent"]["candidate_id"] == cid
    assert body["intent"]["ticker"] == "AAA"
    assert body["intent"]["notional"] == 1234.5
    assert intent_path.exists()
    text = intent_path.read_text(encoding="utf-8")
    assert "AAA_entity" in text

    source = Path(api_server.__file__).read_text(encoding="utf-8")
    forbidden = ["make_broker", "AlpacaBroker", "place_buy", "place_sell"]
    assert all(token not in source for token in forbidden)


def test_dashboard_central_candidates_endpoint_reads_file_only(tmp_path, monkeypatch):
    candidate_path = tmp_path / "central_buy_candidates.json"
    atomic_write_json(candidate_path, {"schema_version": 1, "trade_date": "2026-06-25", "candidates": {"x": {"ticker": "AAA"}}})
    monkeypatch.setattr(api_server, "CENTRAL_BUY_CANDIDATES_PATH", candidate_path)
    client = TestClient(api_server.app)

    res = client.get("/api/live/central_candidates")

    assert res.status_code == 200
    assert res.json()["candidates"]["x"]["ticker"] == "AAA"
    endpoint_source = inspect.getsource(api_server.central_candidates)
    assert "make_broker" not in endpoint_source
    assert "place_buy" not in endpoint_source
