from __future__ import annotations

import json
from pathlib import Path

from data._system.ops import lightweight_ticker_news_updater as updater


def test_current_candidate_source_has_18_unique_tickers() -> None:
    rows = updater.collect_candidate_targets()
    tickers = [row["ticker"] for row in rows]
    assert len(tickers) == 18
    assert len(set(tickers)) == 18
    assert "BTBT" in tickers
    assert all(row["source"] == "candidate_pool" for row in rows)


def test_priority_is_holdings_then_new_candidates_then_deferred(monkeypatch) -> None:
    monkeypatch.setattr(
        updater,
        "collect_candidate_targets",
        lambda: [
            {"ticker": "AAA", "source": "candidate_pool"},
            {"ticker": "BBB", "source": "candidate_pool"},
            {"ticker": "CCC", "source": "candidate_pool"},
        ],
    )
    monkeypatch.setattr(
        updater,
        "collect_holding_targets",
        lambda: [
            {"ticker": "HLD", "source": "broker_holdings"},
            {"ticker": "AAA", "source": "broker_holdings"},
        ],
    )
    monkeypatch.setattr(updater, "last_csv_date", lambda ticker: "")
    state = {
        "known_candidates": ["AAA"],
        "known_holdings": [],
        "deferred": ["OLD"],
    }
    plan = updater.plan_targets("daily", state)
    assert plan["ordered_targets"] == ["HLD", "AAA", "BBB", "CCC", "OLD"]
    assert plan["new_candidates"] == ["BBB", "CCC"]
    assert plan["new_holdings"] == ["HLD", "AAA"]


def test_on_demand_ignores_old_non_deferred_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        updater,
        "collect_candidate_targets",
        lambda: [
            {"ticker": "OLD", "source": "candidate_pool"},
            {"ticker": "NEW", "source": "candidate_pool"},
        ],
    )
    monkeypatch.setattr(updater, "collect_holding_targets", lambda: [])
    monkeypatch.setattr(updater, "last_csv_date", lambda ticker: "")
    plan = updater.plan_targets(
        "on-demand",
        {"known_candidates": ["OLD"], "known_holdings": [], "deferred": []},
    )
    assert plan["ordered_targets"] == ["NEW"]


def test_quota_budget_matches_updater_market_reserve_logic(tmp_path, monkeypatch) -> None:
    market_usage = tmp_path / "market_usage.json"
    ticker_usage = tmp_path / "ticker_usage.json"
    today = updater.date.today().isoformat()
    market_usage.write_text(json.dumps({"date": today, "count": 0}), encoding="utf-8")
    ticker_usage.write_text(json.dumps({"date": today, "count": 21}), encoding="utf-8")
    monkeypatch.setattr(updater, "MARKET_USAGE_PATH", market_usage)
    monkeypatch.setattr(updater, "TICKER_USAGE_PATH", ticker_usage)
    assert updater.current_usage_count(ticker_usage) == 21
    assert updater.remaining_ticker_budget(25, 2) == 2

    market_usage.write_text(json.dumps({"date": today, "count": 2}), encoding="utf-8")
    ticker_usage.write_text(json.dumps({"date": today, "count": 18}), encoding="utf-8")
    snapshot = updater.usage_snapshot(25, 2)
    assert snapshot == {
        "market_used": 2,
        "ticker_used": 18,
        "reserve_remaining": 0,
        "available": 5,
    }


def test_dry_run_defers_over_budget_without_invoking_updater(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    run_log = tmp_path / "runs.jsonl"
    market_usage = tmp_path / "market_usage.json"
    ticker_usage = tmp_path / "ticker_usage.json"
    updater_log = tmp_path / "updater.jsonl"
    today = updater.date.today().isoformat()
    market_usage.write_text(json.dumps({"date": today, "count": 0}), encoding="utf-8")
    ticker_usage.write_text(json.dumps({"date": today, "count": 21}), encoding="utf-8")
    monkeypatch.setattr(updater, "STATE_PATH", state_path)
    monkeypatch.setattr(updater, "RUN_LOG_PATH", run_log)
    monkeypatch.setattr(updater, "MARKET_USAGE_PATH", market_usage)
    monkeypatch.setattr(updater, "TICKER_USAGE_PATH", ticker_usage)
    monkeypatch.setattr(updater, "UPDATER_RUN_LOG_PATH", updater_log)
    monkeypatch.setattr(updater, "snapshot_csvs", lambda tickers: {})
    monkeypatch.setattr(
        updater,
        "plan_targets",
        lambda mode, state: {
            "candidate_tickers": ["A", "B", "C"],
            "holding_tickers": [],
            "new_candidates": ["A", "B", "C"],
            "new_holdings": [],
            "stale_or_missing": ["A", "B", "C"],
            "ordered_targets": ["A", "B", "C"],
        },
    )
    result = updater.run_once(mode="daily", dry_run=True)
    assert result["selected"] == ["A", "B"]
    assert result["deferred"] == ["C"]
    assert result["next_deferred"] == ["C"]
    assert result["usage_delta"] == 0
    assert result["fail_open_unchanged"] is True
    assert result["updater_rows"] == []


def test_updater_rows_are_read_from_appended_log_segment(tmp_path, monkeypatch) -> None:
    log = tmp_path / "updater.jsonl"
    log.write_text(json.dumps({"ticker": "OLD", "status": "OK"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(updater, "UPDATER_RUN_LOG_PATH", log)
    offset = updater.updater_log_offset()
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ticker": "NEW", "status": "FAILED"}) + "\n")
    assert updater.read_updater_rows_since(offset) == [{"ticker": "NEW", "status": "FAILED"}]


def test_wrapper_delegates_to_existing_updater_and_never_uses_positions_json() -> None:
    source = Path(updater.__file__).read_text(encoding="utf-8")
    assert "update_ticker_sentiment_recent.py" in source
    assert "collect_real_holding_targets" in source
    assert "positions.json" not in source
    assert "News=0" in source
