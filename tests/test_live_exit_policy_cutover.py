from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType  # noqa: E402
from engine.live.exit_policy_adapter import evaluate_live_policy, resolve_position_rulebook, shadow_record_from_live_policy  # noqa: E402
from engine.live.position_manager import PositionEntry, PositionManager  # noqa: E402
from engine.live.runner import Runner, RunnerStats  # noqa: E402
from engine.strategies.learned_rulebook import LearnedRuleBook  # noqa: E402
from engine.strategies.rulebook import Rulebook  # noqa: E402

NEUTRAL_CTX = SimpleNamespace(score=50.0, vix_level=18.0, sector_strength={"tech": 50.0})


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_close(actual: float, expected: float, message: str, tolerance: float = 1e-9) -> None:
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{message}: actual={actual}, expected={expected}")


def make_rulebook(strategy: str = "hybrid") -> Rulebook:
    return Rulebook(
        ticker="TEST", asset_type="us_stock", direction="long", exit_strategy=strategy,
        stop_loss_atr=2.0, take_profit_atr=3.0, trailing_atr=1.5, max_holding_days=20,
        stop_loss_atr_bear=1.0, take_profit_atr_bull=5.0, trailing_atr_volatile=3.0,
        sector_name="tech",
    )


def make_manager(pos: PositionEntry | None = None) -> PositionManager:
    manager = PositionManager.__new__(PositionManager)
    manager._positions = {pos.ticker: pos} if pos is not None else {}
    manager._save = lambda: None
    manager._append_trade_log = lambda record: None
    manager.unregister = lambda ticker: manager._positions.pop(ticker, None)
    return manager


def run_check(manager: PositionManager, pos: PositionEntry, broker, *, policy: str = "1", shadow: str = "0"):
    with patch.dict(os.environ, {"EXIT_LIVE_POLICY": policy, "EXIT_LIVE_SHADOW": shadow}, clear=False), \
         patch("engine.market.context.get_market_context", return_value=NEUTRAL_CTX):
        return manager._check_one("TEST", pos, broker)


class FakeBroker:
    def __init__(self, price: float, status: OrderStatus = OrderStatus.FILLED, fill_price: float | None = None):
        self.price = price
        self.status = status
        self.fill_price = price if fill_price is None else fill_price
        self.sell_calls: list[tuple] = []

    def get_current_price(self, ticker: str) -> float:
        return self.price

    def get_holdings(self):
        return [Holding(ticker="TEST", shares=2.0, avg_cost=100.0, current_price=self.price,
                        market_value=2.0 * self.price, unrealized_pnl=2.0 * (self.price - 100.0),
                        unrealized_pnl_pct=(self.price / 100.0 - 1.0) * 100.0)]

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0):
        self.sell_calls.append((ticker, shares, order_type, price))
        return Order(
            order_id="SELL1", ticker=ticker, side=OrderSide.SELL, order_type=order_type,
            shares=shares, price=price, status=self.status,
            filled_shares=shares if self.status == OrderStatus.FILLED else 0.0,
            filled_avg_price=self.fill_price if self.status == OrderStatus.FILLED else 0.0,
        )


def snapshot_position(target: float = 106.0, strategy: str = "hybrid") -> PositionEntry:
    rb = make_rulebook(strategy)
    return PositionEntry(
        ticker="TEST", entry_date=datetime.now().isoformat(), entry_price=100.0, shares=2.0,
        atr_at_entry=2.0, stop_price=96.0, target_price=target, trailing_distance=3.0,
        trailing_stop=97.0, highest_price=100.0, lowest_price=100.0,
        exit_strategy=strategy, max_holding_days=20,
        rulebook_direction="long", rulebook_snapshot=rb.to_dict(), member_hash="a" * 64,
        entry_market_score=50.0, entry_vix_level=18.0, entry_sector_score=50.0,
    )


def test_register_entry_uses_entry_context_and_snapshot() -> None:
    manager = make_manager()
    pos = manager.register_entry(
        "TEST", 100.0, 2.0, make_rulebook(), 2.0,
        entry_market_context={"score": 75.0, "vix_level": 30.0, "sector_strength": {"tech": 66.0}},
    )
    assert_close(pos.stop_price, 96.0, "bull keeps base stop")
    assert_close(pos.target_price, 110.0, "bull target uses entry-time dynamic parameter")
    assert_close(pos.trailing_distance, 6.0, "volatile trailing uses entry-time dynamic parameter")
    assert_close(pos.trailing_stop, 94.0, "entry trailing stop")
    assert_close(pos.lowest_price, 100.0, "entry lowest price starts at entry")
    assert_true(bool(pos.rulebook_snapshot), "snapshot must be stored")
    assert_true(len(pos.member_hash) == 64, "member hash must be stored")
    assert_close(pos.entry_market_score, 75.0, "entry market score")
    assert_close(pos.entry_vix_level, 30.0, "entry VIX")
    assert_close(pos.entry_sector_score, 66.0, "entry sector score")


def test_old_position_json_is_backward_compatible() -> None:
    raw = {
        "ticker": "TEST", "entry_date": "2026-01-01T00:00:00", "entry_price": 100.0,
        "shares": 2, "atr_at_entry": 2.0, "stop_price": 96.0, "target_price": 106.0,
        "trailing_distance": 3.0, "trailing_stop": 97.0, "highest_price": 100.0,
        "exit_strategy": "hybrid", "max_holding_days": 20, "rulebook_direction": "long",
        "future_unknown_field": "ignored",
    }
    pos = PositionEntry.from_dict(raw)
    assert_true(pos.rulebook_snapshot == {}, "old position defaults to no snapshot")
    assert_true(pos.member_hash == "", "old position hash default")
    assert_true(pos.add_buy_count == 0, "old position add count default")
    assert_close(pos.lowest_price, 100.0, "old position lowest defaults to entry")


def test_policy_on_uses_snapshot_and_actual_fill() -> None:
    pos = snapshot_position()
    manager = make_manager(pos)
    result = run_check(manager, pos, FakeBroker(price=95.0, fill_price=94.5))
    assert_true(result is not None and result["exit_reason"] == "stop_loss", "policy stop must place sell")
    assert_close(result["exit_price"], 94.5, "trade must use actual filled price")
    assert_true("lowest_price" in result and "mae_pct" in result, "trade must include MAE fields")
    assert_true("TEST" not in manager._positions, "FILLED policy exit must unregister")


def test_nonfilled_guard_keeps_position() -> None:
    pos = snapshot_position()
    manager = make_manager(pos)
    broker = FakeBroker(price=95.0, status=OrderStatus.PENDING)
    result = run_check(manager, pos, broker)
    assert_true(result is None, "pending order must not finalize trade")
    assert_true("TEST" in manager._positions, "pending order must keep position")
    assert_true(len(broker.sell_calls) == 1, "trigger submits one order")


def test_snapshotless_position_stays_legacy_when_policy_on() -> None:
    pos = snapshot_position(target=106.0)
    pos.rulebook_snapshot = {}
    pos.member_hash = ""
    manager = make_manager(pos)
    manager._evaluate_policy = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call policy"))
    result = run_check(manager, pos, FakeBroker(price=110.0))
    assert_true(result is not None and result["exit_reason"] == "take_profit", "old position must use legacy")


def test_policy_exception_falls_back_to_legacy() -> None:
    pos = snapshot_position(target=106.0)
    manager = make_manager(pos)
    manager._evaluate_policy = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("policy broken"))
    result = run_check(manager, pos, FakeBroker(price=110.0))
    assert_true(result is not None and result["exit_reason"] == "take_profit", "policy failure retains legacy authority")


def test_policy_state_update_is_single_authority() -> None:
    pos = snapshot_position(target=120.0)
    manager = make_manager(pos)
    broker = FakeBroker(price=105.0)
    result = run_check(manager, pos, broker)
    assert_true(result is None, "no exit expected")
    assert_close(pos.highest_price, 105.0, "policy updates highest")
    assert_close(pos.lowest_price, 100.0, "policy preserves lowest when no new low")
    assert_close(pos.trailing_stop, 102.0, "policy ratchets trailing once")
    assert_true(len(broker.sell_calls) == 0, "no order expected")


def test_policy_off_keeps_legacy_authority() -> None:
    pos = snapshot_position(target=106.0)
    manager = make_manager(pos)
    manager._evaluate_policy = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("policy must stay OFF"))
    result = run_check(manager, pos, FakeBroker(price=110.0), policy="0")
    assert_true(result is not None and result["exit_reason"] == "take_profit", "OFF must preserve legacy")


def test_shadow_record_uses_same_cutover_decision() -> None:
    pos = snapshot_position()
    rb, source = resolve_position_rulebook(pos)
    assert_true(rb is not None, "snapshot rulebook must resolve")
    evaluation = evaluate_live_policy(
        ticker="TEST", pos=pos, price=95.0, rulebook=rb, raw_market_context=None,
        holding_trading_days=3, timestamp="2026-06-04T00:00:00Z", rulebook_source=source,
    )
    record = shadow_record_from_live_policy(
        evaluation, ticker="TEST", pos=pos, price=95.0, holding_calendar_days=3,
        actual_legacy_reason="trailing", timestamp="2026-06-04T00:00:00Z",
    )
    assert_true(record["exit_policy_reason"] == evaluation.decision.reason, "shadow and cutover decision match")
    assert_true(record["diagnostics"].get("cutover_authority") is True, "cutover shadow marker")


def test_learned_rulebook_caches_exact_signal_context() -> None:
    learned = LearnedRuleBook()
    learned._rulebook_cache["TEST"] = make_rulebook()
    df = pd.DataFrame({"Close": [100.0] * 60, "ATR": [2.0] * 60})
    ctx = SimpleNamespace(score=73.0, vix_level=27.0, sector_strength={"tech": 64.0}, timestamp="ctx-ts", active_events={})
    result = SimpleNamespace(should_buy=True, score=3.0, raw_score=3.0, threshold=2.0, market_adjustment=1.0, reasons=[])
    with patch("engine.strategies.learned_rulebook.get_market_context", return_value=ctx), \
         patch.object(learned, "_lookup_lagged_news_context", return_value=(0.0, {}, "test")), \
         patch("engine.strategies.learned_rulebook.evaluate_signal", return_value=result):
        learned.evaluate("TEST", 100.0, df=df)
    cached = learned.get_last_market_context("TEST")
    assert_close(cached["score"], 73.0, "cached market score")
    assert_close(cached["vix_level"], 27.0, "cached vix")
    assert_close(cached["sector_score"], 64.0, "cached sector score")
    assert_true(cached["timestamp"] == "ctx-ts", "cached timestamp")


def test_runner_passes_cached_context_to_register_entry() -> None:
    captured = {}

    class FakeRulebookProvider:
        def get_last_atr(self, ticker): return 2.0
        def get_rulebook(self, ticker): return make_rulebook()
        def get_last_market_context(self, ticker): return {"score": 77.0, "vix_level": 29.0, "sector_strength": {"tech": 68.0}}

    class FakeSafety:
        def check_order(self, *args, **kwargs): return SimpleNamespace(allowed=True, code="", reason="")
        def record_order(self, *args, **kwargs): pass

    class FakePM:
        def register_entry(self, *args, **kwargs): captured.update(kwargs)

    class FakeBuyBroker:
        mode = "paper"
        def place_buy(self, ticker, shares, order_type):
            return Order(order_id="B1", ticker=ticker, side=OrderSide.BUY, order_type=order_type,
                         shares=shares, price=0.0, status=OrderStatus.FILLED,
                         filled_shares=shares, filled_avg_price=100.0)

    runner = Runner.__new__(Runner)
    runner.stats = RunnerStats()
    runner.broker = FakeBuyBroker()
    runner.safety = FakeSafety()
    runner.rulebook = FakeRulebookProvider()
    runner.position_manager = FakePM()
    runner.notifier = SimpleNamespace(send_order=lambda order: None, send_error=lambda msg: None, send_safety_block=lambda *args: None)
    runner.order_notional = None
    runner.order_shares = 1.0
    runner._maybe_request_approval = lambda *args, **kwargs: None
    runner._try_order("BUY", "TEST", 100.0, "test")
    assert_close(captured["entry_market_context"]["score"], 77.0, "runner passes exact cached context")


def run_all() -> None:
    tests = [
        test_register_entry_uses_entry_context_and_snapshot,
        test_old_position_json_is_backward_compatible,
        test_policy_on_uses_snapshot_and_actual_fill,
        test_nonfilled_guard_keeps_position,
        test_snapshotless_position_stays_legacy_when_policy_on,
        test_policy_exception_falls_back_to_legacy,
        test_policy_state_update_is_single_authority,
        test_policy_off_keeps_legacy_authority,
        test_shadow_record_uses_same_cutover_decision,
        test_learned_rulebook_caches_exact_signal_context,
        test_runner_passes_cached_context_to_register_entry,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL LIVE EXIT POLICY CUTOVER TESTS PASSED")


if __name__ == "__main__":
    run_all()
