from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.exit_policy import (  # noqa: E402
    MarketContext,
    initialize_position_state,
    update_position_for_add_buy,
)
from engine.core.metadata import compute_rulebook_hash  # noqa: E402
from engine.pipeline.rolling_validation import backtest_result_to_oos_period  # noqa: E402
from engine.strategies.exit_simulator import Trade, simulate_exit  # noqa: E402
from engine.strategies.rulebook import default_rulebook  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_trade_to_dict_records_entry_context_fields() -> None:
    trade = Trade(
        entry_date="2024-01-01",
        entry_price=100.0,
        entry_shares=10,
        exit_date="2024-01-05",
        exit_price=110.0,
        exit_reason="take_profit",
        holding_days=4,
        total_shares=10,
        avg_cost=100.0,
        pnl_pct=9.9,
        pnl_krw=990.0,
        commission=10.0,
        trigger_price=111.0,
        fill_price_base=110.0,
        fill_price_stress=109.5,
        stress_pnl_pct=9.4,
        stress_pnl_krw=940.0,
        entry_market_score=66.5,
        entry_vix_level=19.2,
        entry_sector_score=58.7,
        entry_atr=2.0,
        stop_price_at_entry=96.0,
        target_price_at_entry=106.0,
        trailing_stop_at_entry=97.0,
        trailing_distance_at_entry=3.0,
        exit_strategy="hybrid",
        rulebook_hash="a" * 64,
        member_hash="b" * 64,
    )
    data = trade.to_dict()
    for key in [
        "entry_market_score",
        "entry_vix_level",
        "entry_sector_score",
        "entry_atr",
        "stop_price_at_entry",
        "target_price_at_entry",
        "trailing_stop_at_entry",
        "trailing_distance_at_entry",
        "exit_strategy",
        "rulebook_hash",
        "member_hash",
    ]:
        assert_true(key in data, f"{key} must be serialized")
    assert_true(data["entry_market_score"] == 66.5, "entry_market_score value must be preserved")
    assert_true(data["exit_strategy"] == "hybrid", "exit_strategy value must be preserved")
    assert_true(len(data["rulebook_hash"]) == 64, "rulebook_hash must be preserved")


def test_simulate_exit_disables_add_buy_but_preserves_trade_schema_and_entry_context() -> None:
    idx = pd.date_range("2024-01-01", periods=8, freq="D")
    close = [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 111.0, 112.0]
    df = pd.DataFrame(
        {
            "Open": close,
            "High": [c + 0.5 for c in close],
            "Low": [c - 0.5 for c in close],
            "Close": close,
            "Volume": [100000] * len(close),
            "ATR": [1.0] * len(close),
        },
        index=idx,
    )
    rb = default_rulebook("TEST", asset_type="us_stock", direction="long")
    rb.exit_strategy = "fixed"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 100.0
    rb.take_profit_atr_bull = 100.0
    rb.trailing_atr = 10.0
    rb.max_holding_days = 5
    rb.add_buy_enabled = True
    rb.add_buy_max_count = 2
    rb.add_buy_trigger_profit_pct = 0.5
    rb.add_buy_size_ratio = 0.5

    trade = simulate_exit(
        rb,
        df,
        entry_idx=0,
        initial_shares=10,
        initial_budget_krw=10_000.0,
        cur_market_score=72.0,
        cur_vix_level=21.0,
        cur_sector_score=63.0,
    )
    assert_true(trade is not None, "trade must be produced")
    data = trade.to_dict()
    assert_true(data["add_buys"] == [], "add-buy must be runtime-disabled even when rb.add_buy_enabled=True")
    assert_true(data["total_shares"] == 10, "total_shares must remain entry_shares when add-buy is disabled")
    assert_true(data["avg_cost"] == 100.0, "avg_cost must remain entry_price when add-buy is disabled")
    assert_true(data["entry_market_score"] == 72.0, "entry market score must be recorded")
    assert_true(data["entry_vix_level"] == 21.0, "entry VIX must be recorded")
    assert_true(data["entry_sector_score"] == 63.0, "entry sector score must be recorded")
    assert_true(data["entry_atr"] == 1.0, "entry ATR must be recorded")
    assert_true(data["stop_price_at_entry"] == 98.0, "entry stop must preserve original entry context")
    assert_true(data["target_price_at_entry"] == 200.0, "entry target must preserve original entry context")
    assert_true(data["exit_strategy"] == "fixed", "exit strategy must be recorded")
    assert_true(data["rulebook_hash"] == compute_rulebook_hash(rb), "rulebook hash must match metadata hash")
    assert_true(data["member_hash"] == compute_rulebook_hash(rb), "member hash must match rulebook hash in current model")


def test_update_position_for_add_buy_logic_is_preserved() -> None:
    rb = default_rulebook("TEST", asset_type="us_stock", direction="long")
    rb.exit_strategy = "fixed"
    rb.stop_loss_atr = 2.0
    rb.take_profit_atr = 3.0
    rb.trailing_atr = 1.5
    mctx = MarketContext(market_score=72.0, vix_level=21.0, sector_score=63.0)
    position = initialize_position_state(
        ticker="TEST",
        entry_price=100.0,
        shares=10,
        rulebook=rb,
        atr_value=2.0,
        market_context=mctx,
        entry_date="2024-01-01",
    )

    updated = update_position_for_add_buy(
        position,
        add_price=110.0,
        add_shares=5,
        rulebook=rb,
        atr_value=2.0,
        market_context=mctx,
    )

    expected_avg_cost = ((100.0 * 10) + (110.0 * 5)) / 15
    assert_true(abs(updated.avg_cost - expected_avg_cost) < 1e-9, "avg_cost must update by weighted average")
    assert_true(updated.shares == 15, "shares must include add-buy shares")
    assert_true(updated.add_buy_count == 1, "add_buy_count must increment")
    assert_true(abs(updated.stop_price - (expected_avg_cost - 4.0)) < 1e-9, "stop must update from avg_cost and ATR")
    assert_true(
        abs(updated.target_price - (expected_avg_cost + (2.0 * rb.take_profit_atr_bull))) < 1e-9,
        "target must update from avg_cost and dynamic ATR target",
    )


def test_rolling_period_records_best_rulebook() -> None:
    rb = default_rulebook("TEST", asset_type="us_stock", direction="long")
    rb.signal_threshold = 2.75
    rb.fitness = 123.456
    result = SimpleNamespace(
        trade_count=7,
        win_rate=57.1,
        expectancy_pct=1.4,
        profit_factor=1.6,
        max_drawdown_pct=-5.0,
        fitness=88.8,
        trades=[{"exit_reason": "take_profit", "pnl_pct": 3.0}],
    )
    ga_result = SimpleNamespace(best=rb, generations_run=3, final_population=[rb])
    period = backtest_result_to_oos_period(
        year=2025,
        train_start="2020-01-01",
        train_end="2024-12-31",
        test_start="2025-01-01",
        test_end="2025-12-31",
        result=result,
        ga_result=ga_result,
    )
    assert_true(period["best_rulebook"]["signal_threshold"] == 2.75, "best_rulebook must be serialized")
    assert_true(period["best_rulebook_hash"] == compute_rulebook_hash(rb), "best_rulebook_hash must match")
    assert_true(period["ga"]["best_fitness"] == 123.456, "GA best_fitness summary must remain intact")


def run_all() -> None:
    tests = [
        test_trade_to_dict_records_entry_context_fields,
        test_simulate_exit_disables_add_buy_but_preserves_trade_schema_and_entry_context,
        test_update_position_for_add_buy_logic_is_preserved,
        test_rolling_period_records_best_rulebook,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL EXIT TRADE METADATA TESTS PASSED")


if __name__ == "__main__":
    run_all()
