from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from engine.live.manual_buy_intent import atomic_write_json, candidate_id_for


def test_pytest_guard_blocks_live_manual_buy_files(monkeypatch):
    import engine.live.manual_buy_intent as buy_module
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_guard")

    with pytest.raises(RuntimeError, match="test attempted to write live manual_buy_intent.json"):
        buy_module.atomic_write_json(buy_module.MANUAL_BUY_INTENT_PATH, {"schema_version": 1})
    with pytest.raises(RuntimeError, match="test attempted to write live central_buy_candidates.json"):
        buy_module.atomic_write_json(buy_module.CENTRAL_BUY_CANDIDATES_PATH, {"schema_version": 1})


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



def test_dashboard_manual_sell_intent_endpoint_writes_file_without_broker_import(tmp_path, monkeypatch):
    from engine.live.manual_sell_intent import atomic_write_json as sell_write

    positions_path = tmp_path / "positions.json"
    intent_path = tmp_path / "manual_sell_intent.json"
    sell_write(
        positions_path,
        {
            "AR": {
                "ticker": "AR",
                "entry_date": "2026-06-25T00:00:00+09:00",
                "entry_price": 34.53,
                "shares": 301.169956,
                "atr_at_entry": 1.0,
                "stop_price": 32.0,
                "target_price": 36.0,
                "trailing_distance": 1.0,
                "trailing_stop": 33.0,
                "highest_price": 34.53,
                "lowest_price": 34.53,
                "exit_strategy": "fixed",
                "max_holding_days": 10,
                "rulebook_direction": "long",
            }
        },
    )
    monkeypatch.setattr(api_server, "MANUAL_SELL_POSITIONS_PATH", positions_path)
    monkeypatch.setattr(api_server, "MANUAL_SELL_INTENT_PATH", intent_path)
    client = TestClient(api_server.app)

    res = client.post("/api/live/manual_sell_intent", json={"ticker": "AR", "source": "test-dashboard"})

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["intent"]["ticker"] == "AR"
    assert body["intent"]["status"] == "pending"
    assert body["intent"]["shares_requested"] == 301.169956
    assert intent_path.exists()

    get_res = client.get("/api/live/manual_sell_intents")
    assert get_res.status_code == 200
    assert next(iter(get_res.json()["intents"].values()))["ticker"] == "AR"

    source = Path(api_server.__file__).read_text(encoding="utf-8")
    forbidden = ["make_broker", "AlpacaBroker", "place_buy", "place_sell"]
    assert all(token not in source for token in forbidden)
