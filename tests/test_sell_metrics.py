from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.analyze_sell_metrics import analyze_trades, distribution, percentile, summarize_reason  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_percentile_distribution() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert_true(percentile(values, 0.0) == 1.0, "min percentile must match")
    assert_true(percentile(values, 0.5) == 2.5, "median percentile must interpolate")
    d = distribution(values)
    assert_true(d["count"] == 4, "distribution count must match")
    assert_true(d["avg"] == 2.5, "distribution avg must match")
    assert_true(d["p75"] == 3.25, "distribution p75 must interpolate")


def test_summarize_reason_winrate_and_slippage() -> None:
    trades = [
        {"exit_reason": "trailing", "pnl_pct": 2.0, "stress_pnl_pct": 1.5, "slippage_cost_pct": 0.5, "holding_days": 5},
        {"exit_reason": "trailing", "pnl_pct": -1.0, "stress_pnl_pct": -1.4, "slippage_cost_pct": 0.4, "holding_days": 7},
        {"exit_reason": "trailing", "pnl_pct": 3.0, "stress_pnl_pct": 2.6, "slippage_cost_pct": 0.4, "holding_days": 9},
    ]
    row = summarize_reason("trailing", trades, total_count=6)
    assert_true(row["count"] == 3, "count must match")
    assert_true(abs(row["ratio"] - 50.0) < 1e-9, "ratio must be count / total")
    assert_true(abs(row["avg_pnl_pct"] - (4.0 / 3.0)) < 1e-9, "avg pnl must match")
    assert_true(row["median_pnl_pct"] == 2.0, "median pnl must match")
    assert_true(abs(row["win_rate"] - (2 / 3 * 100.0)) < 1e-9, "win rate must match")
    assert_true(row["avg_holding_days"] == 7.0, "avg holding days must match")
    assert_true(abs(row["slippage_cost_pct_avg"] - (1.3 / 3.0)) < 1e-9, "slippage avg must match")


def test_analyze_trades_exit_reason_slippage_time_out() -> None:
    trades = [
        {"exit_reason": "take_profit", "pnl_pct": 5.0, "stress_pnl_pct": 4.7, "slippage_cost_pct": 0.3, "holding_days": 6},
        {"exit_reason": "stop_loss", "pnl_pct": -3.0, "stress_pnl_pct": -3.3, "slippage_cost_pct": 0.3, "holding_days": 4},
        {"exit_reason": "time_out", "pnl_pct": 1.0, "stress_pnl_pct": 0.8, "slippage_cost_pct": 0.2, "holding_days": 20},
        {"exit_reason": "time_out", "pnl_pct": -2.0, "stress_pnl_pct": -2.2, "slippage_cost_pct": 0.2, "holding_days": 20},
    ]
    analysis = analyze_trades(trades, {"run_id": "dummy", "skip_count": 0})
    assert_true(analysis["trade_count"] == 4, "trade_count must match")
    assert_true(analysis["exit_reason_summary"]["take_profit"]["count"] == 1, "take_profit count must match")
    assert_true(analysis["exit_reason_summary"]["stop_loss"]["win_rate"] == 0.0, "stop_loss win rate must match")
    assert_true(abs(analysis["slippage"]["avg_base_minus_stress_pct"] - 0.25) < 1e-9, "overall slippage must match")
    assert_true(analysis["holding_days_distribution"]["p50"] == 13.0, "holding p50 must interpolate")
    assert_true(analysis["time_out"]["count"] == 2, "time_out count must match")
    assert_true(analysis["time_out"]["win_rate"] == 50.0, "time_out win rate must match")
    assert_true(analysis["time_out"]["median_pnl_pct"] == -0.5, "time_out median pnl must match")


def run_all() -> None:
    tests = [
        test_percentile_distribution,
        test_summarize_reason_winrate_and_slippage,
        test_analyze_trades_exit_reason_slippage_time_out,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL SELL METRICS TESTS PASSED")


if __name__ == "__main__":
    run_all()
