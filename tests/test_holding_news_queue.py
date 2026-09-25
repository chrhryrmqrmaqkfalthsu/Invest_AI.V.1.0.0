from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from engine.live import holding_news_queue as hnq
from engine.live import news_alerts as na
from engine.live.holding_news_queue import (
    HoldingNewsSignal,
    lookup_holding_news_cache_score,
    rank_holding_news_queue,
    refresh_holding_news_for_positions,
    save_holding_news_cache_entry,
)


def test_rank_queue_zeroes_stale_s4():
    asof = datetime(2026, 6, 10, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    ranked = rank_holding_news_queue([
        HoldingNewsSignal("FRESH", s4_sell_omen_score=0.9, s4_score_date="2026-06-09"),
        HoldingNewsSignal("STALE", s4_sell_omen_score=1.0, s4_score_date="2026-05-26"),
    ], asof=asof, limit=2, s4_max_age_days=5)
    by_ticker = {r.ticker: r for r in ranked}
    assert by_ticker["FRESH"].norm_s4 == 1.0
    assert by_ticker["FRESH"].s4_stale is False
    assert by_ticker["STALE"].norm_s4 == 0.0
    assert by_ticker["STALE"].s4_stale is True
    assert ranked[0].ticker == "FRESH"


def test_rank_queue_uses_s1_for_starvation_tiebreak():
    asof = datetime(2026, 6, 10, 15, 0, tzinfo=ZoneInfo("America/New_York"))
    ranked = rank_holding_news_queue([
        HoldingNewsSignal("RECENT", s1_age_days=0, s2_price_risk=1, s3_sentiment_risk=1),
        HoldingNewsSignal("OLD", s1_age_days=10, s2_price_risk=1, s3_sentiment_risk=1),
    ], asof=asof, limit=2)
    assert [r.ticker for r in ranked] == ["OLD", "RECENT"]
    assert ranked[0].norm_s1 == 1.0


def test_rank_queue_handles_missing_signals():
    ranked = rank_holding_news_queue([
        {"ticker": "A", "s1_age_days": None, "s2_price_risk": None, "s3_sentiment_risk": 0.2},
        {"ticker": "B", "s1_age_days": 3, "s2_price_risk": None, "s3_sentiment_risk": None},
    ], asof="2026-06-10T15:00:00-04:00", limit=2)
    assert len(ranked) == 2
    assert {r.ticker for r in ranked} == {"A", "B"}
    assert all(0.0 <= r.queue_score <= 1.0 for r in ranked)


def test_rank_queue_limit_larger_than_universe_returns_all():
    ranked = rank_holding_news_queue([
        HoldingNewsSignal("A", s2_price_risk=0.1),
        HoldingNewsSignal("B", s2_price_risk=0.2),
    ], limit=10)
    assert [r.ticker for r in ranked] == ["B", "A"]


def test_holding_news_cache_save_and_lookup(tmp_path: Path):
    cache_path = tmp_path / "holding_news_cache.json"
    row = save_holding_news_cache_entry(
        "mpc",
        score=0.77,
        fetched_at="2026-06-10T20:00:00+00:00",
        score_date="2026-06-10",
        article_count=3,
        path=cache_path,
    )
    assert row["ticker"] == "MPC"
    looked = lookup_holding_news_cache_score("MPC", path=cache_path)
    assert looked is not None
    assert looked["ticker"] == "MPC"
    assert looked["date"] == "2026-06-10"
    assert looked["score"] == 0.77
    assert looked["article_count"] == 3


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "Date", "sell_omen_score", "model_train_end", "score_year"])
        writer.writeheader()
        writer.writerows(rows)


def test_lookup_live_sell_omen_uses_fresh_cache_before_csv(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    csv_path = tmp_path / "scores.csv"
    _write_csv(csv_path, [{"ticker": "MPC", "Date": "2026-06-10", "sell_omen_score": "0.11", "model_train_end": "x", "score_year": "2026"}])
    save_holding_news_cache_entry("MPC", score=0.88, fetched_at="2026-06-10T20:00:00+00:00", score_date="2026-06-10", path=cache_path)
    monkeypatch.setattr(hnq, "HOLDING_NEWS_CACHE_PATH", cache_path)
    monkeypatch.setattr(na, "SELL_OMEN_SCORE_TABLE", csv_path)
    row = na.lookup_live_sell_omen_score("MPC", asof="2026-06-10T15:00:00-04:00", path=csv_path)
    assert row is not None
    assert row["source"] == "alphavantage_holding_news"
    assert row["score"] == 0.88


def test_lookup_live_sell_omen_stale_cache_falls_back_to_csv(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    csv_path = tmp_path / "scores.csv"
    _write_csv(csv_path, [{"ticker": "MPC", "Date": "2026-06-10", "sell_omen_score": "0.42", "model_train_end": "x", "score_year": "2026"}])
    save_holding_news_cache_entry("MPC", score=0.99, fetched_at="2026-05-26T20:00:00+00:00", score_date="2026-05-26", path=cache_path)
    monkeypatch.setattr(hnq, "HOLDING_NEWS_CACHE_PATH", cache_path)
    monkeypatch.setattr(na, "SELL_OMEN_SCORE_TABLE", csv_path)
    row = na.lookup_live_sell_omen_score("MPC", asof="2026-06-10T15:00:00-04:00", path=csv_path)
    assert row is not None
    assert row["score"] == 0.42
    assert row["model_train_end"] == "x"


def test_lookup_live_sell_omen_stale_cache_and_stale_csv_return_none(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    csv_path = tmp_path / "scores.csv"
    _write_csv(csv_path, [{"ticker": "MPC", "Date": "2026-05-26", "sell_omen_score": "0.42", "model_train_end": "x", "score_year": "2026"}])
    save_holding_news_cache_entry("MPC", score=0.99, fetched_at="2026-05-26T20:00:00+00:00", score_date="2026-05-26", path=cache_path)
    monkeypatch.setattr(hnq, "HOLDING_NEWS_CACHE_PATH", cache_path)
    monkeypatch.setattr(na, "SELL_OMEN_SCORE_TABLE", csv_path)
    row = na.lookup_live_sell_omen_score("MPC", asof="2026-06-10T15:00:00-04:00", path=csv_path)
    assert row is None


def test_refresh_holding_news_dry_run_caps_at_18_and_excludes_covered():
    positions = [SimpleNamespace(ticker=f"T{i:02d}", entry_price=100.0, highest_price=110.0 + i, lowest_price=95.0) for i in range(30)]
    result = refresh_holding_news_for_positions(
        positions,
        asof="2026-06-10T15:00:00-04:00",
        budget=99,
        dry_run=True,
        exclude_tickers={"T00", "T01", "T02"},
    )
    assert result["held_count"] == 30
    assert result["budget"] == 18
    assert result["selected_count"] == 18
    assert not ({"T00", "T01", "T02"} & set(result["selected_tickers"]))


def test_refresh_holding_news_updates_cache_without_real_api(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    positions = [SimpleNamespace(ticker="AAA", entry_price=100.0, highest_price=110.0, lowest_price=90.0)]

    def fake_fetch(ticker, *, api_key, **kwargs):
        return {
            "ticker": ticker,
            "score": 0.66,
            "article_count": 2,
            "latest_article_time_published": "2026-06-10T19:00:00+00:00",
        }

    monkeypatch.setattr(hnq, "fetch_alpha_vantage_ticker_news_score", fake_fetch)
    result = refresh_holding_news_for_positions(
        positions,
        asof="2026-06-10T15:00:00-04:00",
        budget=18,
        dry_run=False,
        api_key="dummy",
        cache_path=cache_path,
    )
    assert result["selected_tickers"] == ["AAA"]
    assert len(result["cache_updates"]) == 1
    row = lookup_holding_news_cache_score("AAA", path=cache_path)
    assert row is not None
    assert row["score"] == 0.66
    assert row["article_count"] == 2
