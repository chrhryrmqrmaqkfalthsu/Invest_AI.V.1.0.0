from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.exit_policy import (  # noqa: E402
    ExitExecutionConfig,
    MarketContext,
    PriceSnapshot,
    evaluate_exit,
    initialize_position_state,
    resolve_exit_params,
)


@dataclass
class DummyRulebook:
    direction: str = "long"
    exit_strategy: str = "hybrid"
    stop_loss_atr: float = 2.0
    take_profit_atr: float = 3.0
    trailing_atr: float = 1.5
    max_holding_days: int = 5
    stop_loss_atr_bear: float = 2.8
    take_profit_atr_bull: float = 4.0
    trailing_atr_volatile: float = 2.5


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def make_position(strategy: str = "hybrid", holding_days: int = 3):
    rb = DummyRulebook(exit_strategy=strategy)
    pos = initialize_position_state(
        ticker="NVDA",
        entry_price=100.0,
        shares=1.0,
        rulebook=rb,
        atr_value=2.0,
        market_context=MarketContext(market_score=50, vix_level=18),
        entry_date="2026-01-02",
        member_hash="member-1",
    )
    pos.holding_trading_days = holding_days
    return rb, pos


def conservative_gap_config() -> ExitExecutionConfig:
    return ExitExecutionConfig(
        mode="conservative_gap_fill",
        base_slippage_bps=0.0,
        stress_slippage_bps=0.0,
        use_next_open=True,
    )


def conservative_core_config(**kwargs) -> ExitExecutionConfig:
    return ExitExecutionConfig(
        mode="conservative_core",
        base_slippage_bps=0.0,
        stress_slippage_bps=0.0,
        use_next_open=True,
        **kwargs,
    )


def enable_breakeven(rb: DummyRulebook, trigger_pct: float = 5.0, floor_pct: float = 0.0) -> None:
    rb.breakeven_enabled = True
    rb.breakeven_trigger_profit_pct = trigger_pct
    rb.breakeven_floor_profit_pct = floor_pct


def test_fixed_long_stop_only() -> None:
    rb, pos = make_position("fixed")
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=102.0, low=95.5, close=96.0),
        rb,
    )
    assert_true(decision.should_exit, "fixed stop case must exit")
    assert_true(decision.reason == "stop_loss", "fixed stop case must be stop_loss")
    assert_true(decision.trigger_price == 96.0, "stop trigger must equal entry - ATR*2")


def test_fixed_long_target_only() -> None:
    rb, pos = make_position("fixed")
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=106.5, low=99.0, close=106.0),
        rb,
    )
    assert_true(decision.should_exit, "fixed target case must exit")
    assert_true(decision.reason == "take_profit", "fixed target case must be take_profit")
    assert_true(decision.trigger_price == 106.0, "target trigger must equal entry + ATR*3")


def test_trailing_only() -> None:
    rb, pos = make_position("trailing", holding_days=4)
    first = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=110.0, low=108.0, close=109.0),
        rb,
    )
    updated = first.updated_position
    assert_true(updated is not None, "updated position must be returned")
    assert_true(updated.trailing_stop == 107.0, "trailing stop must move to highest - distance")

    decision = evaluate_exit(
        updated,
        PriceSnapshot(date="2026-01-04", high=109.0, low=106.5, close=107.0),
        rb,
    )
    assert_true(decision.should_exit, "trailing case must exit")
    assert_true(decision.reason == "trailing", "trailing case must be trailing")
    assert_true(decision.trigger_price == 107.0, "trailing trigger must be updated trailing stop")


def test_time_out_trading_days() -> None:
    rb, pos = make_position("fixed", holding_days=5)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-09", high=103.0, low=98.0, close=101.0),
        rb,
        market_context=MarketContext(holding_trading_days=5),
    )
    assert_true(decision.should_exit, "max holding case must exit")
    assert_true(decision.reason == "time_out", "max holding must be time_out")
    assert_true(decision.diagnostics["holding_trading_days"] == 5, "holding days must be trading-day counter")


def test_hybrid_stop_and_target_same_bar_stop_first() -> None:
    rb, pos = make_position("hybrid", holding_days=3)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=107.0, low=95.0, close=101.0),
        rb,
    )
    assert_true(decision.should_exit, "hybrid simultaneous stop/target must exit")
    assert_true(decision.reason == "stop_loss", "hybrid must prioritize stop_loss over take_profit")
    assert_true(decision.trigger_price == 96.0, "hybrid stop trigger must be stop price")


def test_hybrid_trailing_and_target_same_bar_trailing_first() -> None:
    rb, pos = make_position("hybrid", holding_days=4)
    first = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=110.0, low=108.0, close=109.0),
        rb,
    )
    updated = first.updated_position
    assert_true(updated is not None, "updated position must be returned")
    assert_true(updated.trailing_stop == 107.0, "trailing stop must move to 107")

    decision = evaluate_exit(
        updated,
        PriceSnapshot(date="2026-01-04", high=106.5, low=106.5, close=106.5),
        rb,
    )
    assert_true(decision.should_exit, "hybrid trailing/target simultaneous case must exit")
    assert_true(decision.reason == "trailing", "hybrid must prioritize trailing over take_profit")
    assert_true(decision.trigger_price == 107.0, "hybrid trailing trigger must be trailing stop")


def test_normal_market_uses_fixed_atr_values() -> None:
    rb = DummyRulebook()
    sl, tp, trail = resolve_exit_params(rb, MarketContext(market_score=50, vix_level=18))
    assert_true(sl == rb.stop_loss_atr, "normal market must use fixed stop ATR")
    assert_true(tp == rb.take_profit_atr, "normal market must use fixed take-profit ATR")
    assert_true(trail == rb.trailing_atr, "normal market must use fixed trailing ATR")


def test_trailing_activation_delay_two_bars() -> None:
    rb, pos = make_position("trailing", holding_days=1)
    first = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=110.0, low=106.5, close=107.0),
        rb,
        execution_config=ExitExecutionConfig(trailing_activation_bars=2),
    )
    assert_true(not first.should_exit, "trailing must not fire before activation bars")
    assert_true(first.diagnostics["trailing_active"] is False, "trailing_active must be false before 2 bars")

    updated = first.updated_position
    assert_true(updated is not None, "updated position must be returned")
    updated.holding_trading_days = 3
    second = evaluate_exit(
        updated,
        PriceSnapshot(date="2026-01-06", high=109.0, low=106.5, close=107.0),
        rb,
        execution_config=ExitExecutionConfig(trailing_activation_bars=2),
    )
    assert_true(second.should_exit, "trailing must fire after activation bars")
    assert_true(second.reason == "trailing", "activated trailing must be trailing")


def test_base_stress_fill_slippage_difference() -> None:
    rb, pos = make_position("fixed", holding_days=3)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", high=106.5, low=99.0, close=106.0, next_open=105.0),
        rb,
        execution_config=ExitExecutionConfig(base_slippage_bps=10.0, stress_slippage_bps=50.0, use_next_open=True),
    )
    assert_true(decision.should_exit, "target case must exit")
    assert_true(decision.reason == "take_profit", "target case must be take_profit")
    assert_true(round(decision.fill_price_base or 0.0, 4) == 104.895, "base fill must apply 10 bps to next open")
    assert_true(round(decision.fill_price_stress or 0.0, 4) == 104.475, "stress fill must apply 50 bps to next open")
    assert_true((decision.fill_price_stress or 0.0) < (decision.fill_price_base or 0.0), "stress fill must be more conservative than base")


def test_conservative_gap_fill_stop_uses_trigger_without_gap() -> None:
    rb, pos = make_position("fixed", holding_days=3)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=100.0, high=101.0, low=95.0, close=95.5, next_open=94.0),
        rb,
        execution_config=conservative_gap_config(),
    )
    assert_true(decision.should_exit, "stop touch must exit")
    assert_true(decision.reason == "stop_loss", "stop touch must be stop_loss")
    assert_true(decision.trigger_price == 96.0, "stop trigger must remain stop price")
    assert_true(decision.fill_price_base == 96.0, "non-gap stop must fill at trigger")


def test_conservative_gap_fill_stop_uses_open_on_gap_down() -> None:
    rb, pos = make_position("fixed", holding_days=3)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=95.0, high=98.0, low=94.0, close=97.0, next_open=99.0),
        rb,
        execution_config=conservative_gap_config(),
    )
    assert_true(decision.should_exit, "gap-down stop must exit")
    assert_true(decision.reason == "stop_loss", "gap-down stop must be stop_loss")
    assert_true(decision.trigger_price == 96.0, "stop trigger must remain stop price")
    assert_true(decision.fill_price_base == 95.0, "gap-down stop must fill at open")


def test_conservative_gap_fill_target_uses_trigger_without_gap() -> None:
    rb, pos = make_position("fixed", holding_days=3)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=104.0, high=107.0, low=103.0, close=106.5, next_open=105.0),
        rb,
        execution_config=conservative_gap_config(),
    )
    assert_true(decision.should_exit, "target touch must exit")
    assert_true(decision.reason == "take_profit", "target touch must be take_profit")
    assert_true(decision.trigger_price == 106.0, "target trigger must remain target price")
    assert_true(decision.fill_price_base == 106.0, "non-gap target must fill at trigger")


def test_conservative_gap_fill_target_uses_open_on_gap_up() -> None:
    rb, pos = make_position("fixed", holding_days=3)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=107.0, high=108.0, low=105.5, close=106.5, next_open=105.0),
        rb,
        execution_config=conservative_gap_config(),
    )
    assert_true(decision.should_exit, "gap-up target must exit")
    assert_true(decision.reason == "take_profit", "gap-up target must be take_profit")
    assert_true(decision.trigger_price == 106.0, "target trigger must remain target price")
    assert_true(decision.fill_price_base == 107.0, "gap-up target must fill at open")


def test_conservative_gap_fill_keeps_decision_exit_next_open() -> None:
    rb, pos = make_position("fixed", holding_days=5)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-09", open=101.5, high=103.0, low=98.0, close=101.0, next_open=100.0),
        rb,
        market_context=MarketContext(holding_trading_days=5),
        execution_config=conservative_gap_config(),
    )
    assert_true(decision.should_exit, "timeout must exit")
    assert_true(decision.reason == "time_out", "decision exit must be time_out")
    assert_true(decision.trigger_price == 101.0, "timeout trigger must use reference close")
    assert_true(decision.fill_price_base == 100.0, "decision exit must keep next-open fill")


def test_conservative_core_blocks_same_bar_trailing_activation_exit() -> None:
    rb, pos = make_position("trailing", holding_days=4)
    legacy = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=100.0, high=110.0, low=106.5, close=107.0),
        rb,
        execution_config=conservative_gap_config(),
    )
    conservative = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=100.0, high=110.0, low=106.5, close=107.0),
        rb,
        execution_config=conservative_core_config(trailing_activation_profit_pct=5.0),
    )
    assert_true(legacy.should_exit and legacy.reason == "trailing", "legacy activation may same-bar exit")
    assert_true(not conservative.should_exit, "conservative_core must block same-bar trailing activation exit")
    assert_true(conservative.diagnostics["activation_peak"] == 100.0, "activation peak must be T-1 peak")
    assert_true(conservative.diagnostics["propagation_peak"] == 110.0, "propagation peak must include today high")
    assert_true(conservative.updated_position is not None, "state must still propagate")
    assert_true(conservative.updated_position.trailing_stop == 107.0, "today high updates trailing for next bar")


def test_conservative_core_allows_previously_active_trailing_exit() -> None:
    rb, pos = make_position("trailing", holding_days=4)
    pos.highest_price = 110.0
    pos.trailing_stop = 107.0
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-04", open=108.0, high=109.0, low=106.5, close=107.0),
        rb,
        execution_config=conservative_core_config(trailing_activation_profit_pct=5.0),
    )
    assert_true(decision.should_exit, "T-1 active trailing must exit")
    assert_true(decision.reason == "trailing", "T-1 active trailing exit reason must be trailing")
    assert_true(decision.trigger_price == 107.0, "conservative trailing trigger must be prior trailing stop")


def test_conservative_core_blocks_same_bar_breakeven_activation_exit() -> None:
    rb, pos = make_position("fixed", holding_days=4)
    enable_breakeven(rb, trigger_pct=5.0, floor_pct=0.0)
    legacy = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=100.0, high=105.5, low=99.5, close=101.0),
        rb,
        execution_config=conservative_gap_config(),
    )
    conservative = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=100.0, high=105.5, low=99.5, close=101.0),
        rb,
        execution_config=conservative_core_config(breakeven_enabled=True, breakeven_trigger_profit_pct=5.0, breakeven_floor_profit_pct=0.0),
    )
    assert_true(legacy.should_exit and legacy.reason == "breakeven_stop", "legacy breakeven may same-bar exit")
    assert_true(not conservative.should_exit, "conservative_core must block same-bar breakeven activation exit")
    assert_true(conservative.diagnostics["breakeven_active"] is False, "same-bar high must not activate breakeven")


def test_conservative_core_allows_previously_active_breakeven_exit() -> None:
    rb, pos = make_position("fixed", holding_days=4)
    enable_breakeven(rb, trigger_pct=5.0, floor_pct=0.0)
    pos.highest_price = 106.0
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-04", open=102.0, high=104.0, low=99.5, close=101.0),
        rb,
        execution_config=conservative_core_config(breakeven_enabled=True, breakeven_trigger_profit_pct=5.0, breakeven_floor_profit_pct=0.0),
    )
    assert_true(decision.should_exit, "T-1 active breakeven must exit")
    assert_true(decision.reason == "breakeven_stop", "T-1 active breakeven reason must be breakeven_stop")
    assert_true(decision.trigger_price == 100.0, "breakeven trigger must be floor stop")


def test_conservative_core_keeps_path_independent_stop_loss() -> None:
    rb, pos = make_position("fixed", holding_days=4)
    decision = evaluate_exit(
        pos,
        PriceSnapshot(date="2026-01-03", open=100.0, high=105.0, low=95.5, close=96.0),
        rb,
        execution_config=conservative_core_config(),
    )
    assert_true(decision.should_exit, "stop_loss must still exit in conservative_core")
    assert_true(decision.reason == "stop_loss", "path-independent stop_loss reason must remain stop_loss")


def run_all() -> None:
    tests = [
        test_fixed_long_stop_only,
        test_fixed_long_target_only,
        test_trailing_only,
        test_time_out_trading_days,
        test_hybrid_stop_and_target_same_bar_stop_first,
        test_hybrid_trailing_and_target_same_bar_trailing_first,
        test_normal_market_uses_fixed_atr_values,
        test_trailing_activation_delay_two_bars,
        test_base_stress_fill_slippage_difference,
        test_conservative_gap_fill_stop_uses_trigger_without_gap,
        test_conservative_gap_fill_stop_uses_open_on_gap_down,
        test_conservative_gap_fill_target_uses_trigger_without_gap,
        test_conservative_gap_fill_target_uses_open_on_gap_up,
        test_conservative_gap_fill_keeps_decision_exit_next_open,
        test_conservative_core_blocks_same_bar_trailing_activation_exit,
        test_conservative_core_allows_previously_active_trailing_exit,
        test_conservative_core_blocks_same_bar_breakeven_activation_exit,
        test_conservative_core_allows_previously_active_breakeven_exit,
        test_conservative_core_keeps_path_independent_stop_loss,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL EXIT POLICY TESTS PASSED")


if __name__ == "__main__":
    run_all()
