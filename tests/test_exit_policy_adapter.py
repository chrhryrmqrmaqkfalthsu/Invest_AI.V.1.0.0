from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.exit_policy import ExitDecision  # noqa: E402
from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType  # noqa: E402
from engine.live.exit_policy_adapter import (  # noqa: E402
    classify_shadow_difference,
    compare_legacy_and_exit_policy,
    market_context_to_exit_context,
    position_entry_to_state,
)
from engine.live.position_manager import PositionEntry, PositionManager  # noqa: E402
from engine.strategies.rulebook import Rulebook  # noqa: E402
from scripts.live.replay_exit_shadow import replay_snapshots  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_close(actual: float, expected: float, message: str, tolerance: float = 1e-9) -> None:
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{message}: actual={actual}, expected={expected}")


def make_rulebook(strategy: str = "hybrid") -> Rulebook:
    return Rulebook(
        ticker="TEST",
        asset_type="us_stock",
        direction="long",
        exit_strategy=strategy,
        stop_loss_atr=2.0,
        take_profit_atr=3.0,
        trailing_atr=1.5,
        max_holding_days=20,
        stop_loss_atr_bear=1.0,
        take_profit_atr_bull=5.0,
        trailing_atr_volatile=3.0,
        sector_name="tech",
    )


def make_position(strategy: str = "hybrid", target: float = 106.0) -> PositionEntry:
    return PositionEntry(
        ticker="TEST",
        entry_date=datetime.now().isoformat(),
        entry_price=100.0,
        shares=2.0,
        atr_at_entry=2.0,
        stop_price=96.0,
        target_price=target,
        trailing_distance=3.0,
        trailing_stop=97.0,
        highest_price=100.0,
        exit_strategy=strategy,
        max_holding_days=20,
        rulebook_direction="long",
    )


class FakeBroker:
    def __init__(self, price: float):
        self.price = price
        self.sell_calls: list[tuple] = []

    def get_current_price(self, ticker: str) -> float:
        return self.price

    def get_holdings(self):
        return [
            Holding(
                ticker="TEST",
                shares=2.0,
                avg_cost=100.0,
                current_price=self.price,
                market_value=2.0 * self.price,
                unrealized_pnl=2.0 * (self.price - 100.0),
                unrealized_pnl_pct=(self.price / 100.0 - 1.0) * 100.0,
            )
        ]

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0):
        self.sell_calls.append((ticker, shares, order_type, price))
        return Order(
            order_id="SELL1",
            ticker=ticker,
            side=OrderSide.SELL,
            order_type=order_type,
            shares=shares,
            price=price,
            status=OrderStatus.FILLED,
            filled_shares=shares,
            filled_avg_price=self.price,
        )


def make_manager(pos: PositionEntry) -> PositionManager:
    manager = PositionManager.__new__(PositionManager)
    manager._positions = {pos.ticker: pos}
    manager._save = lambda: None
    manager._append_trade_log = lambda record: None
    manager.unregister = lambda ticker: manager._positions.pop(ticker, None)
    return manager


def test_position_entry_to_state_and_market_context() -> None:
    pos = make_position()
    rb = make_rulebook()
    state = position_entry_to_state(pos, rb, holding_trading_days=7)
    assert_true(state.ticker == "TEST", "ticker must map")
    assert_close(state.avg_cost, 100.0, "avg cost must map from live entry")
    assert_close(state.stop_price, 96.0, "stop must map exactly")
    assert_close(state.target_price, 106.0, "target must map exactly")
    assert_close(state.trailing_stop, 97.0, "trailing must map exactly")
    assert_true(state.holding_trading_days == 7, "trading holding must map")
    assert_true(bool(state.rulebook_snapshot), "current rulebook fallback snapshot must exist")
    assert_true(len(state.member_hash) == 64, "fallback member hash must be computed")

    raw = SimpleNamespace(score=77.0, vix_level=29.0, sector_strength={"tech": 66.0})
    ctx = market_context_to_exit_context(raw, "tech", holding_trading_days=7, current_trade_date="2026-06-04")
    assert_close(ctx.market_score, 77.0, "market score must map")
    assert_close(ctx.vix_level, 29.0, "vix must map")
    assert_close(ctx.sector_score, 66.0, "sector score must map")
    assert_true(ctx.holding_trading_days == 7, "holding days must map")


def test_difference_classification() -> None:
    assert_true(classify_shadow_difference(None, None, {}) == "SAME", "same none")
    assert_true(classify_shadow_difference("stop_loss", "stop_loss", {"dynamic_exit_difference": True}) == "SAME", "same reason wins")
    assert_true(classify_shadow_difference("trailing", None, {"trailing_delay_difference": True}) == "INTENTIONAL_TRAILING_DELAY", "trailing delay")
    assert_true(classify_shadow_difference("time_out", None, {"timeout_boundary": True}) == "INTENTIONAL_TRADING_DAY_TIMEOUT", "timeout")
    assert_true(classify_shadow_difference("trailing", "stop_loss", {"hybrid_priority_difference": True}) == "INTENTIONAL_HYBRID_PRIORITY", "hybrid")
    assert_true(classify_shadow_difference(None, "stop_loss", {"dynamic_exit_difference": True}) == "INTENTIONAL_DYNAMIC_EXIT", "dynamic")
    assert_true(classify_shadow_difference(None, "stop_loss", {}) == "BUG_CANDIDATE", "unexplained mismatch")


def test_compare_record_contains_required_fields() -> None:
    pos = make_position()
    rb = make_rulebook()
    state = position_entry_to_state(pos, rb, 3)
    decision = ExitDecision(should_exit=True, reason="stop_loss", trigger_price=96.0, fill_price_base=96.0, fill_price_stress=96.0, updated_position=state, diagnostics={"stop_hit": True, "trailing_hit": True})
    record = compare_legacy_and_exit_policy(
        {"reason": "trailing", "price": 95.0, "hits": {"stop_hit": True, "trailing_hit": True}},
        decision,
        ticker="TEST",
        price=95.0,
        position=pos,
        rulebook=rb,
        market_context=SimpleNamespace(market_score=50.0, vix_level=18.0, sector_score=50.0),
        holding_calendar_days=4,
        holding_trading_days=3,
        static_state=state,
        dynamic_state=state,
        rulebook_source="test",
        timestamp="2026-06-04T00:00:00Z",
    )
    assert_true(record["difference_type"] == "INTENTIONAL_HYBRID_PRIORITY", "hybrid difference must classify")
    for key in ("ticker", "ts", "price", "legacy_reason", "exit_policy_reason", "position", "market", "levels", "diagnostics"):
        assert_true(key in record, f"required shadow field missing: {key}")


def test_shadow_off_keeps_legacy_path_unchanged() -> None:
    pos = make_position()
    manager = make_manager(pos)
    broker = FakeBroker(price=100.0)
    calls = {"shadow": 0}

    def fail_if_called(*args, **kwargs):
        calls["shadow"] += 1
        raise AssertionError("shadow must not run while OFF")

    manager._run_live_exit_shadow = fail_if_called
    with patch.dict(os.environ, {"EXIT_LIVE_SHADOW": "0"}, clear=False):
        result = manager._check_one("TEST", pos, broker, notifier=None)
    assert_true(result is None, "legacy no-exit result must remain None")
    assert_true(calls["shadow"] == 0, "shadow must not execute while OFF")
    assert_true(len(broker.sell_calls) == 0, "OFF path must not place unexpected order")


def test_shadow_exception_isolated_from_legacy_sell() -> None:
    pos = make_position(target=106.0)
    manager = make_manager(pos)
    broker = FakeBroker(price=110.0)
    calls = {"shadow": 0}

    def broken_shadow(*args, **kwargs):
        calls["shadow"] += 1
        raise RuntimeError("shadow failure")

    manager._run_live_exit_shadow = broken_shadow
    with patch.dict(os.environ, {"EXIT_LIVE_SHADOW": "1"}, clear=False):
        result = manager._check_one("TEST", pos, broker, notifier=None)
    assert_true(calls["shadow"] == 1, "shadow must execute while ON")
    assert_true(result is not None and result["exit_reason"] == "take_profit", "legacy sell must survive shadow error")
    assert_true(len(broker.sell_calls) == 1, "legacy order must still be placed")


def test_replay_matrix_has_no_unexplained_bug_for_known_differences() -> None:
    snapshots = [
        {"ticker": "TEST", "entry_date": "2026-05-01", "exit_date": "2026-05-02", "observed_exit_reason": "trailing", "pnl_pct": -5.0, "holding_trading_days": 1, "holding_calendar_days": 1},
        {"ticker": "TEST", "entry_date": "2026-05-01", "exit_date": "2026-05-31", "observed_exit_reason": "time_out", "pnl_pct": 0.0, "holding_trading_days": 18, "holding_calendar_days": 30},
        {"ticker": "TEST", "entry_date": "2026-05-01", "exit_date": "2026-05-10", "observed_exit_reason": "stop_loss", "pnl_pct": -5.0, "holding_trading_days": 5, "holding_calendar_days": 9},
    ]
    result = replay_snapshots(snapshots, example_limit=1)
    assert_true(result["evaluation_count"] == 15, "3 snapshots x 5 scenarios")
    assert_true(result["difference_type_counts"].get("BUG_CANDIDATE", 0) == 0, "known replay differences must be explained")
    assert_true(result["different_count"] > 0, "replay must expose intentional differences")


def run_all() -> None:
    tests = [
        test_position_entry_to_state_and_market_context,
        test_difference_classification,
        test_compare_record_contains_required_fields,
        test_shadow_off_keeps_legacy_path_unchanged,
        test_shadow_exception_isolated_from_legacy_sell,
        test_replay_matrix_has_no_unexplained_bug_for_known_differences,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL EXIT POLICY ADAPTER TESTS PASSED")


if __name__ == "__main__":
    run_all()
