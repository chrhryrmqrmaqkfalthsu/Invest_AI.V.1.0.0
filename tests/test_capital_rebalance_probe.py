from __future__ import annotations

import pandas as pd

from engine.portfolio.capital_rebalance_probe import (
    RebalanceCandidate,
    RebalanceConfig,
    _weights_from_scores,
    dry_run_plan,
    simulate_rebalance_candidate,
)


def test_weights_respect_concentration_guard():
    contexts = [
        {"trade_id": "A:0", "ticker": "A"},
        {"trade_id": "B:0", "ticker": "B"},
        {"trade_id": "C:0", "ticker": "C"},
    ]
    candidate = RebalanceCandidate("test", "test", True, lambda ctx: 10.0 if ctx["ticker"] == "A" else 1.0)
    weights = _weights_from_scores(contexts, candidate, RebalanceConfig(max_ticker_gross_share_pct=20.0))
    assert max(weights.values()) <= 0.2000000001
    # With only three tickers and 20% concentration cap, 100% exposure is infeasible.
    assert round(sum(weights.values()), 6) == 0.6


def test_simulation_charges_turnover_cost_and_tracks_concentration():
    rows = [
        {"ticker": "AAA", "trade_index": "0", "entry_date": "2024-01-02", "exit_date": "2024-01-08", "entry_price": "100", "entry_signal_score": "2", "entry_signal_threshold": "1", "exit_reason": "trailing"},
        {"ticker": "BBB", "trade_index": "0", "entry_date": "2024-01-02", "exit_date": "2024-01-08", "entry_price": "100", "entry_signal_score": "1", "entry_signal_threshold": "1", "exit_reason": "time_out"},
        {"ticker": "CCC", "trade_index": "0", "entry_date": "2024-01-02", "exit_date": "2024-01-08", "entry_price": "100", "entry_signal_score": "1", "entry_signal_threshold": "1", "exit_reason": "time_out"},
        {"ticker": "DDD", "trade_index": "0", "entry_date": "2024-01-02", "exit_date": "2024-01-08", "entry_price": "100", "entry_signal_score": "1", "entry_signal_threshold": "1", "exit_reason": "time_out"},
        {"ticker": "EEE", "trade_index": "0", "entry_date": "2024-01-02", "exit_date": "2024-01-08", "entry_price": "100", "entry_signal_score": "1", "entry_signal_threshold": "1", "exit_reason": "time_out"},
    ]
    idx = pd.date_range("2024-01-02", "2024-01-08", freq="B")
    histories = {}
    for ticker in ["AAA", "BBB", "CCC", "DDD", "EEE"]:
        close = [100, 101, 102, 103, 104]
        if ticker != "AAA":
            close = [100, 99, 98, 97, 96]
        histories[ticker] = pd.DataFrame({"Close": close}, index=idx)
    candidate = RebalanceCandidate("equal", "equal", False, lambda ctx: 1.0)
    metrics = simulate_rebalance_candidate(rows, histories, candidate, RebalanceConfig(transaction_cost_bps=10.0, min_rebalance_days=5))
    assert metrics["rebalance_count"] >= 1
    assert metrics["transaction_cost_pct_of_initial_capital"] > 0
    assert metrics["max_ticker_gross_share_pct"] <= 20.0000001
    assert metrics["avg_gross_exposure_pct"] > 99.0


def test_dry_run_plan_uses_small_csv(tmp_path):
    p = tmp_path / "trades.csv"
    p.write_text(
        "ticker,trade_index,entry_date,exit_date,entry_price,entry_signal_score,entry_signal_threshold,exit_reason\n"
        "AAA,0,2024-01-02,2024-01-08,100,2,1,trailing\n"
        "BBB,0,2024-01-02,2024-01-08,100,1,1,time_out\n",
        encoding="utf-8",
    )
    summary = dry_run_plan(p, RebalanceConfig())
    assert summary["trade_count"] == 2
    assert summary["ticker_count"] == 2
    assert summary["missing_required_fields"] == []
    assert summary["will_not_execute_heavy_backtest"] is True
    assert "path_winner_scaling" in summary["candidate_names"]
