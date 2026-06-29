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
    monkeypatch.setattr(api_server, "SCHEDULED_OPEN_BUY_QUEUE_PATH", str(tmp_path / "missing_scheduled_open_buy_queue.json"))
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


def test_dashboard_live_news_filters_non_holdings_and_reports_risk_thresholds(tmp_path, monkeypatch):
    positions_path = tmp_path / "positions.json"
    news_cache_path = tmp_path / "holding_news_sentiment_cache.json"
    alerts_path = tmp_path / "news_alert_state.json"
    positions_path.write_text(
        '{"AAA":{"shares":2,"entry_price":10},"BBB":{"shares":0,"entry_price":20},"CCC":{"shares":3,"entry_price":30}}',
        encoding="utf-8",
    )
    news_cache_path.write_text(
        """
        {
          "updated_at": "2026-06-26T19:32:50+00:00",
          "entries": {
            "AAA": {"ticker":"AAA", "score":0.72, "fetched_at":"2026-06-26T19:30:00+00:00", "article_count":5, "source":"test", "score_logic_version":"direct_asset_v3_recent3d", "max_score_article_age_days":3},
            "BBB": {"ticker":"BBB", "score":0.50, "fetched_at":"2026-06-26T19:30:00+00:00", "article_count":4, "source":"test", "score_logic_version":"direct_asset_v3_recent3d", "max_score_article_age_days":3},
            "OLD": {"ticker":"OLD", "score":0.90, "fetched_at":"2026-06-26T19:30:00+00:00", "article_count":9, "source":"test", "score_logic_version":"direct_asset_v3_recent3d", "max_score_article_age_days":3}
          }
        }
        """,
        encoding="utf-8",
    )
    alerts_path.write_text('{"sell_omen_prealerts": {}}', encoding="utf-8")
    monkeypatch.setattr(api_server, "POSITIONS_PATH", str(positions_path))
    monkeypatch.setattr(api_server, "HOLDING_NEWS_CACHE_PATH", str(news_cache_path))
    monkeypatch.setattr(api_server, "NEWS_ALERT_STATE_PATH", str(alerts_path))
    client = TestClient(api_server.app)

    res = client.get("/api/live/news")

    assert res.status_code == 200
    body = res.json()
    entries = body["sentiment"]["entries"]
    meta = body["sentiment"]["meta"]
    assert sorted(entries) == ["AAA", "CCC"]
    assert entries["AAA"]["risk_label"] == "high"
    assert entries["CCC"]["missing"] is True
    assert meta["held_count"] == 2
    assert meta["hidden_non_holding_count"] == 2
    assert meta["risk_thresholds"] == {"low_lt": 0.3, "medium_gte": 0.3, "high_gte": 0.6}


def test_dashboard_live_news_rejects_old_score_logic_cache(tmp_path, monkeypatch):
    positions_path = tmp_path / "positions.json"
    news_cache_path = tmp_path / "holding_news_sentiment_cache.json"
    alerts_path = tmp_path / "news_alert_state.json"
    positions_path.write_text('{"DDD":{"shares":1,"entry_price":10}}', encoding="utf-8")
    news_cache_path.write_text(
        '{"updated_at":"2026-06-26T19:32:50+00:00","entries":{"DDD":{"ticker":"DDD","score":0.95,"fetched_at":"2026-06-26T19:30:00+00:00","article_count":3,"source":"old_logic"}}}',
        encoding="utf-8",
    )
    alerts_path.write_text('{"sell_omen_prealerts": {}}', encoding="utf-8")
    monkeypatch.setattr(api_server, "POSITIONS_PATH", str(positions_path))
    monkeypatch.setattr(api_server, "HOLDING_NEWS_CACHE_PATH", str(news_cache_path))
    monkeypatch.setattr(api_server, "NEWS_ALERT_STATE_PATH", str(alerts_path))
    client = TestClient(api_server.app)

    res = client.get("/api/live/news")

    assert res.status_code == 200
    entry = res.json()["sentiment"]["entries"]["DDD"]
    assert entry["score"] is None
    assert entry["risk_label"] == "missing"
    assert entry["score_logic_usable"] is False
    assert entry["score_unusable_reason"] == "score_logic_version_invalid"
    assert entry["stale"] is True


def test_dashboard_html_is_served_from_single_8001_origin():
    client = TestClient(api_server.app)

    res = client.get("/dashboard")

    assert res.status_code == 200
    html = res.text
    assert "const API=window.location.origin;" in html
    assert "function newsRiskView" in html
    assert "http://localhost:8001" not in html


def test_news_translation_falls_back_without_key(tmp_path, monkeypatch):
    from engine.live.news_translation import translate_article_to_ko

    monkeypatch.setenv("NEWS_TRANSLATION_ENABLED", "0")
    article = {"ticker": "AAA", "title": "AAA shares fall after guidance cut", "summary": "AAA shares declined after management lowered its outlook."}

    out = translate_article_to_ko(article, cache_path=tmp_path / "translation_cache.json")

    assert out["translated"] is False
    assert out["translation_source"] == "disabled_by_env"
    assert out["title"] == article["title"]
    assert out["title_ko"] == article["title"]
    assert out["summary_en"] == article["summary"]


def test_news_translation_uses_gpt_once_then_cache(tmp_path, monkeypatch):
    from engine.live import news_translation

    cache_path = tmp_path / "translation_cache.json"
    monkeypatch.setenv("NEWS_TRANSLATION_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    calls = {"n": 0}

    def fake_call(article, *, api_key, model):
        calls["n"] += 1
        assert api_key == "test-key"
        return "AAA 가이던스 하향에 주가 약세", "경영진이 전망을 낮추면서 AAA 주가가 하락했다."

    monkeypatch.setattr(news_translation, "_call_openai_translation", fake_call)
    article = {
        "ticker": "AAA",
        "title": "AAA shares fall after guidance cut",
        "summary": "AAA shares declined after management lowered its outlook.",
        "published_at": "2026-06-26T13:00:00+00:00",
        "url": "https://example.com/a",
    }

    first = news_translation.translate_article_to_ko(article, cache_path=cache_path, force=True)
    second = news_translation.translate_article_to_ko(article, cache_path=cache_path, force=True)

    assert calls["n"] == 1
    assert first["translated"] is True
    assert second["translated"] is True
    assert first["title"] == "AAA 가이던스 하향에 주가 약세"
    assert second["translation_source"].startswith("openai:")
    assert first["title_en"] == article["title"]
    assert cache_path.exists()
