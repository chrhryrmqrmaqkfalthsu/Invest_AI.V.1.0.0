from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.scoring import (  # noqa: E402
    is_oos_year_pass,
    score_full_training_members,
    score_stock_from_rolling,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_pass(year: int = 2023) -> dict:
    return {
        "year": year,
        "oos": {
            "trade_count": 6,
            "win_rate": 55.0,
            "expectancy_pct": 1.5,
            "profit_factor": 1.4,
            "max_drawdown_pct": -5.0,
        },
    }


def make_fail(year: int = 2023) -> dict:
    return {
        "year": year,
        "oos": {
            "trade_count": 6,
            "win_rate": 49.0,
            "expectancy_pct": 1.5,
            "profit_factor": 1.4,
            "max_drawdown_pct": -5.0,
        },
    }


def test_is_oos_year_pass_spec_criteria() -> None:
    assert_true(is_oos_year_pass(make_pass()), "valid OOS year must pass")
    assert_true(not is_oos_year_pass(make_fail()), "win_rate <= 50 must fail")
    low_trades = make_pass()
    low_trades["oos"]["trade_count"] = 4
    assert_true(not is_oos_year_pass(low_trades), "trade_count < 5 must fail")


def test_stock_score_consistency_three_two_zero_passes() -> None:
    three = score_stock_from_rolling([make_pass(2023), make_pass(2024), make_pass(2025)], 150_000_000)
    assert_true(three["consistency_score"] == 60.0, "3/3 pass must score consistency 60")
    assert_true(not three["excluded"], "3/3 pass with good ADV must not be excluded")

    two = score_stock_from_rolling([make_pass(2023), make_pass(2024), make_fail(2025)], 150_000_000)
    assert_true(two["consistency_score"] == 40.0, "2/3 pass must score consistency 40")

    zero = score_stock_from_rolling([make_fail(2023), make_fail(2024), make_fail(2025)], 150_000_000)
    assert_true(zero["consistency_score"] == 0.0, "0/3 pass must score consistency 0")
    assert_true(zero["stock_score"] == 0.0, "0/3 pass must be excluded with stock_score 0")
    assert_true(zero["excluded"], "0/3 pass must be excluded")
    assert_true(zero["exclude_reason"] == "NO_OOS_PASS", "0/3 pass exclusion reason must be NO_OOS_PASS")


def test_adv_liquidity_filter_and_weight() -> None:
    low_adv = score_stock_from_rolling([make_pass(2023), make_pass(2024), make_pass(2025)], 24_999_999)
    assert_true(low_adv["liquidity_weight"] == 0.0, "ADV below 25M must have zero liquidity weight")
    assert_true(low_adv["stock_score"] == 0.0, "ADV below 25M must force stock_score 0")
    assert_true(low_adv["exclude_reason"] == "ADV_BELOW_MIN", "low ADV exclusion reason must be ADV_BELOW_MIN")

    mid_adv = score_stock_from_rolling([make_pass(2023), make_pass(2024), make_pass(2025)], 50_000_000)
    assert_true(mid_adv["liquidity_weight"] == 0.9, "ADV 50M must apply 0.90 weight")
    expected = round(mid_adv["raw_stock_score"] * 0.9, 6)
    assert_true(mid_adv["stock_score"] == expected, "stock_score must equal raw_score * 0.90")


def test_member_qualification_thresholds() -> None:
    members = [
        {
            "rank": 1,
            "trade_count": 9,
            "expectancy_pct": 2.0,
            "profit_factor": 1.5,
            "win_rate": 60.0,
            "max_drawdown_pct": -4.0,
        },
        {
            "rank": 2,
            "trade_count": 11,
            "expectancy_pct": 0.1,
            "profit_factor": 1.01,
            "win_rate": 51.0,
            "max_drawdown_pct": -8.0,
        },
    ]
    scored = score_full_training_members(members)
    assert_true(scored[0]["qualified"] is False, "trade_count 9 must fail qualification")
    assert_true(scored[1]["qualified"] is True, "11 trades + positive expectancy + pf>1 must pass")


def test_unqualified_members_are_preserved() -> None:
    members = [
        {"rank": 1, "trade_count": 1, "expectancy_pct": -1.0, "profit_factor": 0.5, "win_rate": 10.0},
        {"rank": 2, "trade_count": 20, "expectancy_pct": 1.0, "profit_factor": 1.5, "win_rate": 60.0},
    ]
    scored = score_full_training_members(members)
    assert_true(len(scored) == 2, "all members must be preserved")
    assert_true(scored[0]["qualified"] is False, "unqualified member must remain with qualified=False")
    assert_true(scored[1]["qualified"] is True, "qualified member must remain")


def test_raw_metrics_are_returned() -> None:
    stock = score_stock_from_rolling([make_pass(2023), make_fail(2024), make_pass(2025)], 120_000_000)
    raw = stock["raw_metrics"]
    for key in (
        "period_count",
        "pass_count",
        "avg_trade_count_all",
        "avg_win_rate_all",
        "avg_expectancy_pct_all",
        "avg_profit_factor_all",
        "avg_trade_count_pass",
        "avg_win_rate_pass",
        "avg_expectancy_pct_pass",
        "avg_profit_factor_pass",
        "adv_usd_252d",
    ):
        assert_true(key in raw, f"raw_metrics must include {key}")
    assert_true(stock["quality_provisional"] is True, "stock quality must be marked provisional")

    members = score_full_training_members([
        {"rank": 1, "trade_count": 12, "expectancy_pct": 1.0, "profit_factor": 1.4, "win_rate": 60.0, "max_drawdown_pct": -3.0}
    ])
    assert_true("train_metrics" in members[0], "member result must include train_metrics")
    assert_true("score_components" in members[0], "member result must include score_components")
    assert_true(members[0]["member_score_provisional"] is True, "member score must be marked provisional")


def run_all() -> None:
    tests = [
        test_is_oos_year_pass_spec_criteria,
        test_stock_score_consistency_three_two_zero_passes,
        test_adv_liquidity_filter_and_weight,
        test_member_qualification_thresholds,
        test_unqualified_members_are_preserved,
        test_raw_metrics_are_returned,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL SCORING TESTS PASSED")


if __name__ == "__main__":
    run_all()
