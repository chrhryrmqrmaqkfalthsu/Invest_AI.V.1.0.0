from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.feature_lag import (  # noqa: E402
    FEATURE_LAG_METADATA,
    lagged_date_key,
    lookup_lagged_daily_dict,
    lookup_market_at_lagged,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_lagged_date_key_defaults_to_d_minus_one() -> None:
    assert_true(lagged_date_key("2026-06-04") == "2026-06-03", "default lag must be D-1")
    assert_true(lagged_date_key(pd.Timestamp("2026-06-04"), lag_days=2) == "2026-06-02", "custom lag must work")
    assert_true(lagged_date_key(None) == "", "invalid date must be safe")


def test_d_day_news_is_excluded() -> None:
    daily = {
        "2026-06-02": {"sentiment_avg": 0.2, "label": "D-2"},
        "2026-06-03": {"sentiment_avg": 0.3, "label": "D-1"},
        "2026-06-04": {"sentiment_avg": 0.9, "label": "D"},
    }
    row = lookup_lagged_daily_dict(daily, "2026-06-04", lag_days=1, max_age_days=7)
    assert_true(row["label"] == "D-1", "D-day news must be excluded from D-day signal")


def test_forward_fill_over_weekend_or_missing_news_day() -> None:
    daily = {
        "2026-06-05": {"sentiment_avg": 0.5, "label": "friday"},
        "2026-06-08": {"sentiment_avg": 0.8, "label": "monday"},
    }
    row = lookup_lagged_daily_dict(daily, "2026-06-08", lag_days=1, max_age_days=7)
    assert_true(row["label"] == "friday", "Monday signal must use Friday if Sunday/Saturday have no news row")


def test_max_age_blocks_stale_news() -> None:
    daily = {
        "2026-05-20": {"sentiment_avg": -0.4, "label": "stale"},
    }
    row = lookup_lagged_daily_dict(daily, "2026-06-04", lag_days=1, max_age_days=7)
    assert_true(row == {}, "news older than max_age_days must be ignored")


def test_empty_and_invalid_inputs_are_safe() -> None:
    assert_true(lookup_lagged_daily_dict({}, "2026-06-04") == {}, "empty dict must return empty dict")
    assert_true(lookup_lagged_daily_dict(None, "2026-06-04") == {}, "None dict must return empty dict")
    assert_true(lookup_lagged_daily_dict({"bad": {"x": 1}}, "2026-06-04") == {}, "bad date keys must be ignored")
    assert_true(lookup_lagged_daily_dict({"2026-06-01": []}, "2026-06-04") == {}, "non-dict row must return empty dict")


def test_market_lookup_uses_lagged_cutoff() -> None:
    df = pd.DataFrame(
        [
            {"date": "2026-06-02", "score": 10.0, "has_war": 0},
            {"date": "2026-06-03", "score": 20.0, "has_war": 1},
            {"date": "2026-06-04", "score": 99.0, "has_war": 1},
        ]
    )
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    row = lookup_market_at_lagged(df, "2026-06-04", lag_days=1)
    assert_true(float(row.get("score")) == 20.0, "market lookup must use D-1 row, not D row")


def test_feature_lag_metadata_values() -> None:
    assert_true(FEATURE_LAG_METADATA["ticker_sentiment_days"] == 1, "metadata ticker lag must be 1")
    assert_true(FEATURE_LAG_METADATA["market_events_days"] == 1, "metadata event lag must be 1")
    assert_true(FEATURE_LAG_METADATA["max_age_days"] == 7, "metadata max age must be 7")


def run_all() -> None:
    tests = [
        test_lagged_date_key_defaults_to_d_minus_one,
        test_d_day_news_is_excluded,
        test_forward_fill_over_weekend_or_missing_news_day,
        test_max_age_blocks_stale_news,
        test_empty_and_invalid_inputs_are_safe,
        test_market_lookup_uses_lagged_cutoff,
        test_feature_lag_metadata_values,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL FEATURE LAG TESTS PASSED")


if __name__ == "__main__":
    run_all()
