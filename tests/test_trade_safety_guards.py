from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live import position_manager as pm_mod  # noqa: E402
from engine.live.broker.base import Balance, Holding, Order, OrderSide, OrderStatus, OrderType  # noqa: E402
from engine.live.runner import Runner  # noqa: E402
from engine.live.safety import layer as safety_layer_mod  # noqa: E402
from engine.live.safety import state as safety_state_mod  # noqa: E402
from engine.live.safety.layer import SafetyLayer  # noqa: E402
from engine.strategies.rulebook import default_rulebook  # noqa: E402


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class DummyBroker:
    mode = "paper"

    def __init__(self, *, holdings: dict[str, float] | None = None, price: float = 100.0):
        self.holdings = dict(holdings or {})
        self.price = float(price)
        self.buy_orders: list[tuple[str, float]] = []
        self.sell_orders: list[tuple[str, float]] = []

    def get_balance(self):
        return Balance(cash_krw=1_000_000, total_value_krw=1_000_000, invested_krw=0, holdings=self.get_holdings())

    def get_holdings(self):
        return [
            Holding(
                ticker=ticker,
                shares=shares,
                avg_cost=self.price,
                current_price=self.price,
                market_value=shares * self.price,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
            )
            for ticker, shares in self.holdings.items()
            if shares > 0
        ]

    def get_current_price(self, ticker: str):
        return self.price

    def is_market_open(self, ticker=None):
        return True

    def place_buy(self, ticker: str, shares: float, order_type=OrderType.MARKET, price: float = 0.0):
        self.buy_orders.append((ticker, shares))
        self.holdings[ticker] = self.holdings.get(ticker, 0.0) + shares
        now = datetime.now(timezone.utc).isoformat()
        return Order(
            order_id=f"BUY-{len(self.buy_orders)}",
            ticker=ticker,
            side=OrderSide.BUY,
            order_type=order_type,
            shares=shares,
            price=price,
            status=OrderStatus.FILLED,
            filled_shares=shares,
            filled_avg_price=self.price,
            submitted_at=now,
            filled_at=now,
        )

    def place_sell(self, ticker: str, shares: float, order_type=OrderType.MARKET, price: float = 0.0):
        self.sell_orders.append((ticker, shares))
        now = datetime.now(timezone.utc).isoformat()
        return Order(
            order_id=f"SELL-{len(self.sell_orders)}",
            ticker=ticker,
            side=OrderSide.SELL,
            order_type=order_type,
            shares=shares,
            price=price,
            status=OrderStatus.FILLED,
            filled_shares=shares,
            filled_avg_price=self.price,
            submitted_at=now,
            filled_at=now,
        )

    def cancel_order(self, order_id: str):
        return True

    def get_order(self, order_id: str):
        return None


class DummyNotifier:
    def __init__(self):
        self.blocks = []
        self.messages = []
        self.orders = []
        self.errors = []

    def send_safety_block(self, code, message):
        self.blocks.append((code, message))

    def send(self, message, **kwargs):
        self.messages.append((message, kwargs))

    def send_order(self, order):
        self.orders.append(order)

    def send_error(self, message):
        self.errors.append(message)

    def send_approval_request(self, req):
        self.messages.append(("approval", req.request_id))


class DummyRulebookProvider:
    def __init__(self, ticker="AAPL"):
        self.rb = default_rulebook(ticker, asset_type="us_stock", direction="long")

    def name(self):
        return "dummy"

    def get_last_atr(self, ticker):
        return 2.0

    def get_rulebook(self, ticker):
        return self.rb

    def get_last_market_context(self, ticker):
        return {"score": 50.0, "vix_level": 18.0, "sector_score": 50.0}


class DummyPositionManager:
    def __init__(self):
        self.register_calls = 0
        self.add_calls = 0
        self.last_add = None

    def register_entry(self, *args, **kwargs):
        self.register_calls += 1
        raise AssertionError("register_entry must not be called in this guarded path")

    def add_to_position(self, *args, **kwargs):
        self.add_calls += 1
        self.last_add = (args, kwargs)
        return SimpleNamespace(ticker=args[0])

    def get(self, ticker):
        return None


class DummyApprovalManager:
    def __init__(self):
        self.saved = 0

    def _save(self):
        self.saved += 1


def policy_text(*, first_approval: bool = False, entry_cooldown_hours: int = 24, add_cooldown_minutes: int = 30) -> str:
    return f"""
small_amount_safety:
  enabled: true
  max_shares_per_order: 100000
  max_notional_per_order: 100000000
  max_total_notional: 100000000
  max_orders_per_day: 100
  require_first_order_approval: {str(first_approval).lower()}
risk:
  daily_loss_limit_pct: 10
  consecutive_loss_limit: 3
  cooldown_after_consecutive_loss_hours: 24
entry:
  one_position_per_symbol: true
  cooldown_after_buy_hours: {entry_cooldown_hours}
add_buy:
  enabled: true
  min_cooldown_minutes: {add_cooldown_minutes}
"""


def patch_paths(tmp: Path, *, first_approval: bool = False, entry_cooldown_hours: int = 24, add_cooldown_minutes: int = 30):
    system = tmp / "system"
    symbols = tmp / "symbols"
    system.mkdir(parents=True, exist_ok=True)
    symbols.mkdir(parents=True, exist_ok=True)
    policy = tmp / "policy.yaml"
    policy.write_text(
        policy_text(first_approval=first_approval, entry_cooldown_hours=entry_cooldown_hours, add_cooldown_minutes=add_cooldown_minutes),
        encoding="utf-8",
    )
    positions = system / "positions.json"
    safety_state = system / "safety_state.json"
    kill = system / "KILL_SWITCH"
    safety_layer_mod.POSITIONS_PATH = positions
    safety_layer_mod.SYMBOLS_DIR = symbols
    safety_layer_mod.KILL_SWITCH_PATH = kill
    safety_state_mod.STATE_PATH = safety_state
    pm_mod.POSITIONS_PATH = positions
    (symbols / "AAPL").mkdir(parents=True, exist_ok=True)
    return policy, positions, safety_state


def write_position(positions: Path, ticker: str = "AAPL") -> None:
    positions.parent.mkdir(parents=True, exist_ok=True)
    positions.write_text(json.dumps({ticker: {"dummy": True}}), encoding="utf-8")


def make_runner(tmp: Path, *, holdings: dict[str, float] | None = None, positions: bool = False, first_approval: bool = False):
    policy, positions_path, _ = patch_paths(tmp, first_approval=first_approval)
    if positions:
        write_position(positions_path)
    broker = DummyBroker(holdings=holdings or {})
    safety = SafetyLayer(broker=broker, policy_path=policy)
    runner = Runner.__new__(Runner)
    runner.broker = broker
    runner.safety = safety
    runner.notifier = DummyNotifier()
    runner.rulebook = DummyRulebookProvider("AAPL")
    runner.order_shares = 1.0
    runner.order_notional = None
    runner.position_manager = DummyPositionManager()
    runner.approval_manager = DummyApprovalManager()
    runner.stats = SimpleNamespace(orders_attempted=0, orders_filled=0, orders_blocked=0)
    return runner, broker, positions_path


def test_general_buy_blocked_by_broker_holding_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        runner, broker, _ = make_runner(Path(td), holdings={"AAPL": 1.0})
        runner._try_order("BUY", "AAPL", 100.0, "test")
        assert_true(len(broker.buy_orders) == 0, "broker holding must block general BUY")
        assert_true(runner.position_manager.register_calls == 0, "blocked BUY must not register entry")
        assert_true(runner.stats.orders_blocked == 1, "blocked BUY must increment blocked count")


def test_general_buy_blocked_by_position_manager_file_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        runner, broker, _ = make_runner(Path(td), holdings={}, positions=True)
        runner._try_order("BUY", "AAPL", 100.0, "test")
        assert_true(len(broker.buy_orders) == 0, "tracked position must block general BUY")
        assert_true(runner.position_manager.register_calls == 0, "tracked position block must protect snapshot")


def test_existing_position_general_buy_never_overwrites_snapshot() -> None:
    with tempfile.TemporaryDirectory() as td:
        runner, broker, _ = make_runner(Path(td), holdings={"AAPL": 1.0}, positions=True)
        runner._try_order("BUY", "AAPL", 100.0, "test")
        assert_true(len(broker.buy_orders) == 0, "existing position must not send repeated BUY")
        assert_true(runner.position_manager.register_calls == 0, "register_entry must not overwrite snapshot")


def test_approved_add_buy_uses_add_to_position_only_when_legacy_approval_enabled() -> None:
    with tempfile.TemporaryDirectory() as td:
        runner, broker, _ = make_runner(Path(td), holdings={"AAPL": 1.0}, positions=True)
        runner.safety.policy.setdefault("add_buy", {})["approval_enabled"] = True
        req = SimpleNamespace(ticker="AAPL", approved_krw=1000.0, request_id="req123456", status="approved", options_krw=[1000.0])
        runner._execute_approved(req)
        assert_true(len(broker.buy_orders) == 1, "valid add-buy approval must send one BUY")
        assert_true(runner.position_manager.add_calls == 1, "approved add-buy must call add_to_position")
        assert_true(runner.position_manager.register_calls == 0, "approved add-buy must not call register_entry")
        assert_true(req.status == "executed", "valid add-buy request must be executed")


def test_stale_add_buy_approval_does_not_create_new_position() -> None:
    with tempfile.TemporaryDirectory() as td:
        runner, broker, _ = make_runner(Path(td), holdings={}, positions=False)
        runner.safety.policy.setdefault("add_buy", {})["approval_enabled"] = True
        req = SimpleNamespace(ticker="AAPL", approved_krw=1000.0, request_id="req123456", status="approved", options_krw=[1000.0])
        runner._execute_approved(req)
        assert_true(len(broker.buy_orders) == 0, "stale add-buy approval must not place order")
        assert_true(runner.position_manager.add_calls == 0, "stale approval must not call add_to_position")
        assert_true(runner.position_manager.register_calls == 0, "stale approval must not create new entry")
        assert_true(req.status == "rejected", "stale add-buy request must be rejected")


def test_entry_cooldown_persists_across_restart() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        policy, _, _ = patch_paths(tmp, entry_cooldown_hours=24)
        now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        state = safety_state_mod.SafetyState(date="2026-06-05")
        state.last_buy_at_by_ticker["AAPL"] = (now - timedelta(hours=1)).isoformat()
        safety_state_mod.save(state)
        first = SafetyLayer(broker=DummyBroker(), policy_path=policy)
        second = SafetyLayer(broker=DummyBroker(), policy_path=policy)
        assert_true(not first.check_entry_guard("AAPL", now=now).allowed, "first layer must block cooldown")
        assert_true(not second.check_entry_guard("AAPL", now=now).allowed, "restarted layer must preserve cooldown")


def test_date_rollover_preserves_ticker_cooldown() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        policy, _, _ = patch_paths(tmp, entry_cooldown_hours=24)
        now = datetime.now(timezone.utc)
        state = safety_state_mod.SafetyState(date="1999-01-01", orders_today=99, invested_krw_today=12345)
        state.last_buy_at_by_ticker["AAPL"] = (now - timedelta(hours=1)).isoformat()
        safety_state_mod.save(state)
        loaded = safety_state_mod.load()
        assert_true(loaded.orders_today == 0, "date rollover must reset daily order count")
        assert_true("AAPL" in loaded.last_buy_at_by_ticker, "date rollover must preserve ticker buy map")
        safety = SafetyLayer(broker=DummyBroker(), policy_path=policy)
        assert_true(not safety.check_entry_guard("AAPL", now=now).allowed, "preserved buy map must still enforce cooldown")


def run_all() -> None:
    tests = [
        test_general_buy_blocked_by_broker_holding_only,
        test_general_buy_blocked_by_position_manager_file_only,
        test_existing_position_general_buy_never_overwrites_snapshot,
        test_approved_add_buy_uses_add_to_position_only_when_legacy_approval_enabled,
        test_stale_add_buy_approval_does_not_create_new_position,
        test_entry_cooldown_persists_across_restart,
        test_date_rollover_preserves_ticker_cooldown,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("ALL TRADE SAFETY GUARD TESTS PASSED")


if __name__ == "__main__":
    run_all()
