from __future__ import annotations

from pathlib import Path

import pandas as pd

from engine.portfolio.capital_lot_rebalance_probe import (
    LotProbeConfig,
    dry_run_plan,
    signal_to_entry_weight,
    simulate_lot_rebalance,
)


def _trade(ticker: str, idx: int, score: float, threshold: float = 1.0, exit_reason: str = "trailing") -> dict:
    return {
        "ticker": ticker,
        "_lot_id": f"{ticker}:{idx}",
        "entry_date": "2024-01-02",
        "exit_date": "2024-01-10",
        "entry_price": 100.0,
        "exit_price": 110.0,
        "entry_signal_score": score,
        "entry_signal_threshold": threshold,
        "exit_reason": exit_reason,
    }


def _histories() -> dict[str, pd.DataFrame]:
    idx = pd.date_range("2024-01-02", "2024-01-10", freq="B")
    return {
        "AAA": pd.DataFrame({"Close": [100, 104, 108, 112, 116, 120, 124]}, index=idx),
        "BBB": pd.DataFrame({"Close": [100, 101, 100, 99, 100, 101, 102]}, index=idx),
        "CCC": pd.DataFrame({"Close": [100, 99, 98, 97, 96, 95, 94]}, index=idx),
        "DDD": pd.DataFrame({"Close": [100, 100, 101, 101, 102, 102, 103]}, index=idx),
    }


def test_signal_to_entry_weight_caps_at_20pct():
    cfg = LotProbeConfig(signal_to_weight_mode="aggressive_linear", max_entry_share_pct=20.0)
    assert signal_to_entry_weight(5.0, cfg) == 0.20
    assert 0.0 < signal_to_entry_weight(1.05, cfg) <= 0.20


def test_lot_probe_enforces_30pct_ticker_cap_with_multiple_lots():
    trades = [_trade("AAA", 0, 2.0), _trade("AAA", 1, 2.0), _trade("BBB", 0, 1.5), _trade("CCC", 0, 1.5), _trade("DDD", 0, 1.5)]
    metrics = simulate_lot_rebalance(trades, _histories(), LotProbeConfig(slippage_bps=0.0, signal_to_weight_mode="aggressive_linear"))
    assert metrics["max_ticker_gross_share_pct"] <= 30.0000001
    assert metrics["buy_count"] >= 4
    assert metrics["avg_gross_exposure_pct"] > 70.0


def test_slippage_reduces_return_vs_zero_slippage():
    trades = [_trade("AAA", 0, 2.0), _trade("BBB", 0, 1.5), _trade("DDD", 0, 1.5)]
    zero = simulate_lot_rebalance(trades, _histories(), LotProbeConfig(slippage_bps=0.0))
    slip = simulate_lot_rebalance(trades, _histories(), LotProbeConfig(slippage_bps=5.0))
    assert slip["slippage_cost_pct_initial_capital"] > 0
    assert slip["total_return_net_pct"] < zero["total_return_net_pct"]


def test_dry_run_plan_detects_stage2_fields(tmp_path: Path):
    p = tmp_path / "trades.jsonl"
    p.write_text(
        '{"ticker":"AAA","entry_date":"2024-01-02","exit_date":"2024-01-10","entry_price":100,"exit_price":110,"entry_signal_score":2,"entry_signal_threshold":1,"exit_reason":"trailing"}\n'
        '{"ticker":"BBB","entry_date":"2024-01-02","exit_date":"2024-01-10","entry_price":100,"exit_price":105,"entry_signal_score":1.5,"entry_signal_threshold":1,"exit_reason":"time_out"}\n',
        encoding="utf-8",
    )
    summary = dry_run_plan(p, tmp_path)
    assert summary["valid_signal_eligible_rows"] == 2
    assert summary["has_entry_signal_score"] is True
    assert summary["has_daily_recomputed_signal"] is False
    assert summary["min_tickers_for_100pct_with_30pct_cap"] == 4
