from __future__ import annotations

import gzip
import json
from pathlib import Path

from engine.live import news_article_summary as mod


def _write_cache(path: Path, ticker: str, articles: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"feed": articles}, f)


def test_articles_for_ticker_hides_peer_article_without_direct_mention(tmp_path, monkeypatch):
    cache = tmp_path / "ticker_news_cache"
    hold = tmp_path / "holding_news_sentiment_cache.json"
    syms = tmp_path / "symbols"
    (syms / "DELL").mkdir(parents=True)
    (syms / "DELL" / "parameters.json").write_text(json.dumps({"asset_meta": {"name": "Dell Technologies Inc - Class C"}}), encoding="utf-8")
    hold.write_text(json.dumps({"entries": {"DELL": {"latest_article_time_published": "2026-06-26T15:18:35+00:00"}}}), encoding="utf-8")
    _write_cache(
        cache / "DELL" / "av_DELL_202606.json.gz",
        "DELL",
        [
            {
                "title": "Apple stock hits all-time high at 315.02 USD",
                "summary": "Apple shares reached a new high.",
                "url": "https://example.com/apple",
                "time_published": "20260626T150000",
                "source": "Example",
                "ticker_sentiment": [{"ticker": "DELL", "relevance_score": "0.61", "ticker_sentiment_score": "-0.10"}],
            },
            {
                "title": "Dell Technologies shares slip after guidance update",
                "summary": "Dell management discussed softer near-term demand.",
                "url": "https://example.com/dell",
                "time_published": "20260626T140000",
                "source": "Example",
                "ticker_sentiment": [{"ticker": "DELL", "relevance_score": "1.0", "ticker_sentiment_score": "-0.30"}],
            },
        ],
    )
    monkeypatch.setattr(mod, "TICKER_NEWS_CACHE_DIR", cache)
    monkeypatch.setattr(mod, "HOLDING_NEWS_CACHE_PATH", hold)
    monkeypatch.setattr(mod, "SYMBOL_DIR", syms)
    monkeypatch.setenv("NEWS_TRANSLATION_ENABLED", "0")

    rows = mod.articles_for_ticker("DELL", limit=2)

    assert len(rows) == 1
    assert rows[0]["title_en"] == "Dell Technologies shares slip after guidance update"


def test_articles_for_ticker_hides_stale_details_when_score_cache_is_newer(tmp_path, monkeypatch):
    cache = tmp_path / "ticker_news_cache"
    hold = tmp_path / "holding_news_sentiment_cache.json"
    syms = tmp_path / "symbols"
    (syms / "DELL").mkdir(parents=True)
    (syms / "DELL" / "parameters.json").write_text(json.dumps({"asset_meta": {"name": "Dell Technologies Inc - Class C"}}), encoding="utf-8")
    hold.write_text(json.dumps({"entries": {"DELL": {"latest_article_time_published": "2026-06-26T15:18:35+00:00"}}}), encoding="utf-8")
    _write_cache(
        cache / "DELL" / "av_DELL_202606.json.gz",
        "DELL",
        [
            {
                "title": "Dell Technologies shares slip after guidance update",
                "summary": "Dell management discussed softer near-term demand.",
                "url": "https://example.com/dell-old",
                "time_published": "20260602T140000",
                "source": "Example",
                "ticker_sentiment": [{"ticker": "DELL", "relevance_score": "1.0", "ticker_sentiment_score": "-0.30"}],
            }
        ],
    )
    monkeypatch.setattr(mod, "TICKER_NEWS_CACHE_DIR", cache)
    monkeypatch.setattr(mod, "HOLDING_NEWS_CACHE_PATH", hold)
    monkeypatch.setattr(mod, "SYMBOL_DIR", syms)
    monkeypatch.setenv("NEWS_TRANSLATION_ENABLED", "0")

    rows = mod.articles_for_ticker("DELL", limit=2)

    assert rows == []
