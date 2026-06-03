from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.context import calculate_adv_usd_252d, make_year_splits  # noqa: E402
from engine.pipeline.rolling_validation import backtest_result_to_oos_period  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_backtest_result_to_oos_period_maps_keys_and_units() -> None:
    result = SimpleNamespace(
        trade_count=7,
        win_rate=57.14,
        expectancy_pct=1.23,
        profit_factor=1.45,
        max_drawdown_pct=-4.56,
        fitness=12.34,
        trades=[{"entry_date": "2023-01-03", "pnl_pct": 1.0}],
    )
    ga_result = SimpleNamespace(
        generations_run=5,
        best=SimpleNamespace(fitness=99.9),
        final_population=[1, 2, 3],
    )
    period = backtest_result_to_oos_period(
        year=2023,
        train_start="2020-01-01",
        train_end="2022-12-31",
        test_start="2023-01-01",
        test_end="2023-12-31",
        result=result,
        ga_result=ga_result,
    )
    assert_true(period["year"] == 2023, "year must be preserved")
    assert_true(period["train_period"] == ["2020-01-01", "2022-12-31"], "train period must be preserved")
    assert_true(period["test_period"] == ["2023-01-01", "2023-12-31"], "test period must be preserved")
    assert_true(period["oos"]["trade_count"] == 7, "trade_count key must map directly")
    assert_true(period["oos"]["win_rate"] == 57.14, "win_rate must remain percent units")
    assert_true(period["oos"]["expectancy_pct"] == 1.23, "expectancy_pct key must be explicit")
    assert_true(period["oos"]["profit_factor"] == 1.45, "profit_factor must map directly")
    assert_true(period["oos"]["max_drawdown_pct"] == -4.56, "max_drawdown_pct must map directly")
    assert_true(period["ga"]["generations_run"] == 5, "GA generations must be summarized")
    assert_true(period["ga"]["population_size"] == 3, "GA population size must be summarized")


def test_make_year_splits_clamps_to_data_bounds() -> None:
    splits = make_year_splits(
        years=(2023, 2024, 2025),
        data_min="2020-06-15",
        data_max="2025-06-20",
    )
    assert_true(len(splits) == 3, "all 3 years should be available")
    assert_true(splits[0]["train_start"] == "2020-06-15", "train_start must clamp to data_min")
    assert_true(splits[0]["train_end"] == "2022-12-31", "2023 train_end must be previous day")
    assert_true(splits[0]["test_start"] == "2023-01-01", "2023 test_start must be Jan 1")
    assert_true(splits[0]["test_end"] == "2023-12-31", "2023 test_end must be Dec 31")
    assert_true(splits[2]["test_end"] == "2025-06-20", "2025 test_end must clamp to data_max")


def test_make_year_splits_skips_unavailable_years() -> None:
    splits = make_year_splits(years=(2023, 2024), data_min="2024-02-01", data_max="2024-12-31")
    assert_true(len(splits) == 0, "years without prior train period should be skipped")


def test_calculate_adv_usd_252d() -> None:
    df = pd.DataFrame(
        {
            "Close": [10.0, 20.0, 30.0, None, 50.0],
            "Volume": [100.0, 200.0, 300.0, 400.0, None],
        }
    )
    adv = calculate_adv_usd_252d(df, lookback_days=2)
    # Valid rows are 10*100, 20*200, 30*300. Last 2 valid rows average = 6500.
    assert_true(adv == 6500.0, "ADV must average Close*Volume over latest valid rows")


def test_calculate_adv_empty_or_missing_columns_safe() -> None:
    assert_true(calculate_adv_usd_252d(pd.DataFrame()) == 0.0, "empty df must return 0")
    assert_true(calculate_adv_usd_252d(pd.DataFrame({"Close": [1, 2]})) == 0.0, "missing Volume must return 0")


def run_all() -> None:
    tests = [
        test_backtest_result_to_oos_period_maps_keys_and_units,
        test_make_year_splits_clamps_to_data_bounds,
        test_make_year_splits_skips_unavailable_years,
        test_calculate_adv_usd_252d,
        test_calculate_adv_empty_or_missing_columns_safe,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL ROLLING VALIDATION TESTS PASSED")


if __name__ == "__main__":
    run_all()
