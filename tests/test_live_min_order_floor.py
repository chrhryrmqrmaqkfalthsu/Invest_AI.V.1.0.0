from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.broker.base import Balance, Holding  # noqa: E402
from engine.live.runner import Runner  # noqa: E402
from engine.live.safety import layer as safety_layer_mod  # noqa: E402
from engine.live.safety import state as safety_state_mod  # noqa: E402
from engine.live.safety.layer import SafetyLayer  # noqa: E402


class FloorBroker:
    mode = "paper"

    def __init__(self, *, price: float = 100.0) -> None:
        self.price = float(price)
        self.buy_calls = 0

    def get_balance(self):
        return Balance(cash_krw=100_000, total_value_krw=100_000, invested_krw=0, holdings=[])

    def get_holdings(self):
        return []

    def get_current_price(self, ticker: str):
        return self.price

    def is_market_open(self, ticker=None):
        return True

    def place_buy(self, *args, **kwargs):
        self.buy_calls += 1
        raise AssertionError("minimum order floor must block before broker.place_buy")


class FloorNotifier:
    def __init__(self) -> None:
        self.rejections: list[dict] = []
        self.blocks: list[tuple[str, str]] = []
        self.errors: list[str] = []

    def send_order_rejected(self, **kwargs):
        self.rejections.append(dict(kwargs))
        return True

    def send_safety_block(self, code, reason):
        self.blocks.append((code, reason))
        return self.send_order_rejected(code=code, reason=reason)

    def send_error(self, message):
        self.errors.append(str(message))
        return True


class BuySignalRulebook:
    def name(self):
        return "min-order-floor-test"

    def evaluate(self, ticker, price):
        raise AssertionError("not used")


class EmptyPositionManager:
    def get(self, ticker):
        return None


def _write_policy(tmp_path: Path) -> Path:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "\n".join(
            [
                "small_amount_safety:",
                "  enabled: true",
                "  min_notional_per_order: 1.0",
                "  min_fractional_shares_per_order: 0.001",
                "  max_shares_per_order: 1000000",
                "  max_notional_ratio: 10.0",
                "  max_bought_notional_per_day: 1000000",
                "  max_total_exposure_notional: 1000000",
                "  max_orders_per_day: 100",
                "  require_first_order_approval: false",
                "risk:",
                "  daily_loss_limit_pct: 99",
                "  consecutive_loss_limit: 99",
                "  cooldown_after_consecutive_loss_hours: 0",
                "entry:",
                "  one_position_per_symbol: true",
                "  cooldown_after_buy_hours: 0",
                "add_buy:",
                "  enabled: false",
                "  min_cooldown_minutes: 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return policy


def _patch_paths(tmp_path: Path) -> None:
    system = tmp_path / "system"
    symbols = tmp_path / "symbols"
    system.mkdir(parents=True, exist_ok=True)
    symbols.mkdir(parents=True, exist_ok=True)
    (symbols / "AAPL").mkdir(parents=True, exist_ok=True)
    positions = system / "positions.json"
    positions.write_text(json.dumps({}), encoding="utf-8")
    safety_layer_mod.POSITIONS_PATH = positions
    safety_layer_mod.SYMBOLS_DIR = symbols
    safety_layer_mod.KILL_SWITCH_PATH = system / "KILL_SWITCH"
    safety_state_mod.STATE_PATH = system / "safety_state.json"


def _make_runner(tmp_path: Path, *, price: float, order_notional: float) -> tuple[Runner, FloorBroker, FloorNotifier]:
    _patch_paths(tmp_path)
    broker = FloorBroker(price=price)
    notifier = FloorNotifier()
    runner = Runner.__new__(Runner)
    runner.broker = broker
    runner.safety = SafetyLayer(broker=broker, policy_path=_write_policy(tmp_path))
    runner.notifier = notifier
    runner.rulebook = BuySignalRulebook()
    runner.position_manager = EmptyPositionManager()
    runner.pending_order_manager = SimpleNamespace(is_ticker_locked=lambda ticker: False)
    runner.order_shares = 1.0
    runner.order_notional = float(order_notional)
    runner.stats = SimpleNamespace(orders_attempted=0, orders_filled=0, orders_blocked=0, market_ticks=1)
    runner._tick_locked_tickers = set()
    runner._get_buy_reconciler = lambda: SimpleNamespace(preflight=lambda ticker: SimpleNamespace(rulebook=None))
    return runner, broker, notifier


def test_safety_blocks_buy_below_min_notional(tmp_path):
    _patch_paths(tmp_path)
    broker = FloorBroker(price=100.0)
    safety = SafetyLayer(broker=broker, policy_path=_write_policy(tmp_path))

    decision = safety.check_order("BUY", "AAPL", 0.005, 100.0, purpose="entry")

    assert decision.allowed is False
    assert decision.code == "MIN_NOTIONAL"
    assert "< 최소" in decision.reason


def test_safety_blocks_too_small_fractional_buy_even_when_notional_is_ok(tmp_path):
    _patch_paths(tmp_path)
    broker = FloorBroker(price=600_000.0)
    safety = SafetyLayer(broker=broker, policy_path=_write_policy(tmp_path))

    decision = safety.check_order("BUY", "AAPL", 0.00005, 600_000.0, purpose="entry")

    assert decision.allowed is False
    assert decision.code == "MIN_FRACTIONAL_SHARES"
    assert "fractional 수량" in decision.reason


def test_safety_does_not_apply_buy_floor_to_sell_residuals(tmp_path):
    _patch_paths(tmp_path)
    broker = FloorBroker(price=600_000.0)
    safety = SafetyLayer(broker=broker, policy_path=_write_policy(tmp_path))

    decision = safety.check_order("SELL", "AAPL", 0.00005, 600_000.0, purpose="exit")

    assert decision.allowed is True


def test_runner_blocks_high_price_fractional_buy_before_broker_submission(tmp_path):
    runner, broker, notifier = _make_runner(tmp_path, price=600_000.0, order_notional=30.0)

    runner._try_order("BUY", "AAPL", 600_000.0, "test high-price fractional")

    assert broker.buy_calls == 0
    assert runner.stats.orders_attempted == 1
    assert runner.stats.orders_blocked == 1
    assert notifier.rejections
    assert notifier.rejections[-1]["code"] == "MIN_FRACTIONAL_SHARES"
