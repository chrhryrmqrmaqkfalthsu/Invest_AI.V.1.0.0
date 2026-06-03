from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.screening import (  # noqa: E402
    check_data_gates,
    check_viability,
    run_screening,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def base_context(**overrides):
    ctx = {
        "ticker": "TEST",
        "adv_usd_252d": 100_000_000.0,
        "rows": 1000,
        "data_start": "2020-01-01",
        "data_end": "2026-01-01",
        "data_min": "2020-01-01",
        "data_max": "2026-01-01",
        "valid_close_ratio": 1.0,
        "valid_volume_ratio": 1.0,
        "invalid_price_volume_ratio": 0.0,
        "split_count": 3,
        "splits": [{"year": 2023}, {"year": 2024}, {"year": 2025}],
        "sentiment_days": 100,
    }
    ctx.update(overrides)
    return ctx


def raising_runner(ctx):
    raise AssertionError("viability runner must not be called")


def passing_runner(ctx):
    return {
        "executed": True,
        "method": "injected",
        "trade_count": 5,
        "expectancy_pct": 0.0,
        "profit_factor": 1.0,
        "fitness": 0.0,
    }


def zero_trade_runner(ctx):
    return {
        "executed": True,
        "method": "injected",
        "trade_count": 0,
        "expectancy_pct": 5.0,
        "profit_factor": 0.0,
        "fitness": -1.0,
    }


def test_adv_below_min_returns_immediately_without_viability() -> None:
    result = run_screening("TEST", context=base_context(adv_usd_252d=1_000_000), viability_runner=raising_runner)
    assert_true(result["passed"] is False, "low ADV must fail")
    assert_true(result["reason_code"] == "ADV_BELOW_MIN", "low ADV reason must be ADV_BELOW_MIN")
    assert_true(result["viability"]["executed"] is False, "low ADV must skip viability")


def test_rows_below_min_fails() -> None:
    result = run_screening("TEST", context=base_context(rows=100), viability_runner=raising_runner)
    assert_true(result["reason_code"] == "INSUFFICIENT_ROWS", "low rows must fail")


def test_split_count_below_min_fails() -> None:
    result = run_screening("TEST", context=base_context(split_count=1, splits=[{"year": 2023}]), viability_runner=raising_runner)
    assert_true(result["reason_code"] == "INSUFFICIENT_SPLITS", "split_count 1 must fail")


def test_stale_data_fails() -> None:
    result = run_screening("TEST", context=base_context(data_end="2024-12-31", data_max="2024-12-31"), viability_runner=raising_runner)
    assert_true(result["reason_code"] == "STALE_DATA", "data ending before 2025 must fail")


def test_close_na_ratio_fails() -> None:
    result = run_screening("TEST", context=base_context(valid_close_ratio=0.90), viability_runner=raising_runner)
    assert_true(result["reason_code"] == "CLOSE_NA_TOO_HIGH", "close valid ratio below 0.95 must fail")


def test_volume_na_ratio_fails() -> None:
    result = run_screening("TEST", context=base_context(valid_volume_ratio=0.90), viability_runner=raising_runner)
    assert_true(result["reason_code"] == "VOLUME_NA_TOO_HIGH", "volume valid ratio below 0.95 must fail")


def test_invalid_price_volume_fails() -> None:
    result = run_screening("TEST", context=base_context(invalid_price_volume_ratio=0.10), viability_runner=raising_runner)
    assert_true(result["reason_code"] == "INVALID_PRICE_OR_VOLUME", "invalid price/volume ratio above threshold must fail")


def test_viability_zero_trades_fails_low_viability() -> None:
    result = run_screening("TEST", context=base_context(), viability_runner=zero_trade_runner)
    assert_true(result["passed"] is False, "zero trades must fail viability")
    assert_true(result["reason_code"] == "LOW_VIABILITY", "zero trades reason must be LOW_VIABILITY")
    assert_true(result["viability"]["executed"] is True, "viability must execute after data gates pass")


def test_viability_trade_count_five_passes() -> None:
    result = run_screening("TEST", context=base_context(), viability_runner=passing_runner)
    assert_true(result["passed"] is True, "5 trades must pass loose viability")
    assert_true(result["reason_code"] == "", "passing screen must have empty reason")


def test_sentiment_zero_is_not_failure() -> None:
    result = run_screening("TEST", context=base_context(sentiment_days=0), viability_runner=passing_runner)
    assert_true(result["passed"] is True, "sentiment_days=0 must not fail screening")
    assert_true(result["sentiment"]["has_sentiment"] is False, "sentiment block must record absence")


def test_check_viability_weak_negative_fails() -> None:
    reason = check_viability({"trade_count": 2, "expectancy_pct": -4.0})
    assert_true(reason == "LOW_VIABILITY", "weak low-trade negative expectancy must fail")
    reason2 = check_viability({"trade_count": 2, "expectancy_pct": -2.0})
    assert_true(reason2 == "", "weak low-trade but not severely negative must pass")


def test_check_data_gates_passes_good_context() -> None:
    assert_true(check_data_gates(base_context()) == "", "good context must pass data gates")


def run_all() -> None:
    tests = [
        test_adv_below_min_returns_immediately_without_viability,
        test_rows_below_min_fails,
        test_split_count_below_min_fails,
        test_stale_data_fails,
        test_close_na_ratio_fails,
        test_volume_na_ratio_fails,
        test_invalid_price_volume_fails,
        test_viability_zero_trades_fails_low_viability,
        test_viability_trade_count_five_passes,
        test_sentiment_zero_is_not_failure,
        test_check_viability_weak_negative_fails,
        test_check_data_gates_passes_good_context,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL SCREENING TESTS PASSED")


if __name__ == "__main__":
    run_all()
