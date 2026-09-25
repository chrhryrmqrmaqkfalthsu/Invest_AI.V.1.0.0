from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from engine.live.candidate_news_guard import candidate_news_required, check_candidate_news_guard
from engine.live.holding_news_queue import save_holding_news_cache_entry


def test_candidate_news_required_uses_learned_sell_omen_switch():
    assert candidate_news_required({"sell_omen_enabled": True}) is True
    assert candidate_news_required({"sell_omen_enabled": "true"}) is True
    assert candidate_news_required({"sell_omen_enabled": False}) is False
    assert candidate_news_required({"use_news_global": True}) is False


def test_candidate_news_guard_blocks_fresh_score_above_learned_threshold(tmp_path: Path):
    cache_path = tmp_path / "holding_news_sentiment_cache.json"
    save_holding_news_cache_entry(
        "AAA",
        score=0.82,
        fetched_at=datetime.now(timezone.utc),
        article_count=2,
        path=cache_path,
    )

    row = check_candidate_news_guard(
        "AAA",
        {"sell_omen_enabled": True, "sell_omen_threshold": 0.7},
        allow_fetch=False,
        cache_path=cache_path,
    )

    assert row["enabled"] is True
    assert row["fresh"] is True
    assert row["blocked"] is True
    assert row["score"] == 0.82
    assert row["threshold"] == 0.7


def test_candidate_news_guard_does_not_block_when_rulebook_did_not_learn_sell_omen(tmp_path: Path):
    cache_path = tmp_path / "holding_news_sentiment_cache.json"
    save_holding_news_cache_entry(
        "AAA",
        score=0.99,
        fetched_at=datetime.now(timezone.utc),
        article_count=1,
        path=cache_path,
    )

    row = check_candidate_news_guard(
        "AAA",
        {"sell_omen_enabled": False, "sell_omen_threshold": 0.1},
        allow_fetch=False,
        cache_path=cache_path,
    )

    assert row["enabled"] is False
    assert row["blocked"] is False
