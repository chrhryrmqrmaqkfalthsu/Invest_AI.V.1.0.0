from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.analyze_sell_structure_preview import (  # noqa: E402
    analyze_sell_structure,
    enrich_trades,
    future_path_metrics,
    holding_bucket,
    lookup_market_score_before,
    market_regime,
    pnl_bucket,
    summarize_counterfactual,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_close(actual: float | None, expected: float, message: str, tolerance: float = 1e-9) -> None:
    if actual is None or abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{message}: actual={actual}, expected={expected}")


def test_future_path_metrics_uses_subsequent_trading_closes() -> None:
    close = pd.Series(
        [100.0, 90.0, 80.0, 110.0, 120.0],
        index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]),
    )
    result = future_path_metrics(close, "2025-01-02", 100.0, horizons=(2, 4))
    assert_true(set(result) == {2, 4}, "both horizons must be available")
    assert_close(result[2]["additional_return_pct"], -20.0, "+2 return must use second subsequent trading close")
    assert_close(result[2]["min_return_pct"], -20.0, "+2 min path must match")
    assert_close(result[2]["max_return_pct"], -10.0, "+2 max path must match")
    assert_true(result[2]["direction"] == "lower", "+2 must classify as lower")
    assert_close(result[4]["additional_return_pct"], 20.0, "+4 return must match")
    assert_true(result[4]["direction"] == "higher", "+4 must classify as higher")


def test_lookup_market_score_before_is_strict_d_minus_one_approximation() -> None:
    history = pd.DataFrame(
        {"score_with_events": [35.0, 72.0, 80.0]},
        index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"]),
    )
    score = lookup_market_score_before(history, "2025-01-06", "score_with_events")
    assert_close(score, 72.0, "entry date row itself must be excluded")
    assert_true(market_regime(score) == "bull", "score >= 70 must be bull")
    assert_true(lookup_market_score_before(history, "2025-01-02", "score_with_events") is None, "no prior row must return None")


def test_counterfactual_summary_counts_defense_and_opportunity() -> None:
    rows = [
        {"future_paths": {5: {"direction": "lower", "additional_return_pct": -5.0, "min_return_pct": -8.0, "max_return_pct": 1.0}}},
        {"future_paths": {5: {"direction": "higher", "additional_return_pct": 7.0, "min_return_pct": -2.0, "max_return_pct": 10.0}}},
        {"future_paths": {5: {"direction": "higher", "additional_return_pct": 3.0, "min_return_pct": 0.0, "max_return_pct": 5.0}}},
    ]
    summary = summarize_counterfactual(rows, horizons=(5,))["5"]
    assert_true(summary["observed_count"] == 3, "observed count must match")
    assert_close(summary["lower_rate_pct"], 100.0 / 3.0, "defense rate must match")
    assert_close(summary["higher_rate_pct"], 200.0 / 3.0, "opportunity rate must match")
    assert_close(summary["additional_return_pct"]["avg"], 5.0 / 3.0, "average additional return must match")


def test_analyze_structure_with_dummy_trades_and_market_history() -> None:
    prices = {
        "AAA": pd.Series(
            [100.0, 95.0, 90.0, 105.0, 110.0],
            index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]),
        ),
        "BBB": pd.Series(
            [100.0, 101.0, 103.0, 104.0, 106.0],
            index=pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08"]),
        ),
    }
    market = pd.DataFrame(
        {"score_with_events": [75.0, 50.0]},
        index=pd.to_datetime(["2025-01-01", "2025-01-02"]),
    )
    trades = [
        {
            "ticker": "AAA",
            "entry_date": "2025-01-02",
            "exit_date": "2025-01-02",
            "exit_price": 100.0,
            "exit_reason": "trailing",
            "pnl_pct": -1.0,
            "holding_days": 2,
        },
        {
            "ticker": "BBB",
            "entry_date": "2025-01-02",
            "exit_date": "2025-01-02",
            "exit_price": 100.0,
            "exit_reason": "time_out",
            "pnl_pct": 2.0,
            "holding_days": 10,
        },
        {
            "ticker": "AAA",
            "entry_date": "2025-01-02",
            "exit_date": "2025-01-02",
            "exit_price": 100.0,
            "exit_reason": "stop_loss",
            "pnl_pct": -3.0,
            "holding_days": 1,
        },
    ]
    enriched, missing = enrich_trades(trades, prices, market, "score_with_events", horizons=(2,))
    analysis = analyze_sell_structure(enriched, horizons=(2,), meta=missing)
    trailing = analysis["trailing_counterfactual"]["horizons"]["2"]
    timeout = analysis["post_timeout"]["horizons"]["2"]
    stop = analysis["entry_market_regime_approx"]["stop_loss_focus"]
    assert_close(trailing["lower_rate_pct"], 100.0, "trailing dummy must show defense")
    assert_close(timeout["higher_rate_pct"], 100.0, "timeout dummy must show opportunity")
    assert_true(stop["regime_counts"]["bull"] == 1, "stop-loss entry must reconstruct bull regime")
    assert_true("근사/예비" in analysis["analysis_label"], "analysis must carry preliminary label")


def test_buckets() -> None:
    assert_true(holding_bucket(2) == "0-2", "holding bucket boundary")
    assert_true(holding_bucket(6) == "6-10", "holding bucket middle")
    assert_true(pnl_bucket(-6.0) == "<-5", "negative pnl bucket")
    assert_true(pnl_bucket(5.0) == "5+", "positive pnl bucket")


def run_all() -> None:
    tests = [
        test_future_path_metrics_uses_subsequent_trading_closes,
        test_lookup_market_score_before_is_strict_d_minus_one_approximation,
        test_counterfactual_summary_counts_defense_and_opportunity,
        test_analyze_structure_with_dummy_trades_and_market_history,
        test_buckets,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL SELL STRUCTURE PREVIEW TESTS PASSED")


if __name__ == "__main__":
    run_all()
