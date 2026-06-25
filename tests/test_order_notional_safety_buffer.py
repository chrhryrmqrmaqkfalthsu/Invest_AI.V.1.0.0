from __future__ import annotations

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.live.broker.base import Balance
from engine.live.central_control import order_notional_safety_buffer_from_policy
from engine.live.safety.layer import SafetyLayer


class EmptyLedger:
    def open_positions(self):
        return []


class SafetyBroker:
    mode = "paper"

    def __init__(self, total_value: float):
        self.total_value = float(total_value)

    def get_balance(self):
        return Balance(
            cash_krw=self.total_value,
            total_value_krw=self.total_value,
            invested_krw=0.0,
            holdings=[],
        )

    def get_holdings(self):
        return []

    def is_market_open(self, ticker=None):
        return True


def test_cap_bound_high_price_order_uses_buffer_for_0_3pct_quote_move():
    cap_notional = 24_960.0
    price = 2_000.0
    params = AllocationParams(
        max_positions=1,
        total_capital=cap_notional / 0.25,
        per_ticker_exposure_cap=0.25,
        position_sizing="equal",
        cash_buffer_ratio=1.0,
        order_notional_safety_buffer=0.003,
        allocation_stats={},
    )

    decisions = decide_buys(
        [BuyCandidate("fix_entity", "FIX", confidence=10.0, strength=1.0, price=price)],
        EmptyLedger(),
        params,
    )

    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.notional <= cap_notional * 0.997 + 0.01
    assert decision.shares * (price * 1.003) <= cap_notional + 0.01
    assert params.allocation_stats["ticker_cap_hit_events"] == 1
    assert params.allocation_stats["order_notional_safety_buffer_applied"] == 1


def test_buffer_not_applied_when_desired_notional_is_below_cap():
    params = AllocationParams(
        max_positions=1,
        total_capital=100_000.0,
        per_ticker_exposure_cap=0.25,
        position_sizing="equal",
        cash_buffer_ratio=0.10,
        order_notional_safety_buffer=0.003,
        allocation_stats={},
    )

    decisions = decide_buys(
        [BuyCandidate("ar_entity", "AR", confidence=1.0, strength=1.0, price=34.0)],
        EmptyLedger(),
        params,
    )

    assert len(decisions) == 1
    assert abs(decisions[0].notional - 10_000.0) < 0.01
    assert "order_notional_safety_buffer_applied" not in params.allocation_stats
    assert "ticker_cap_hit_events" not in params.allocation_stats


def test_buffered_cap_bound_order_still_passes_safety_layer_dynamic_cap(tmp_path, monkeypatch):
    cap_notional = 24_960.0
    price = 2_000.0
    params = AllocationParams(
        max_positions=1,
        total_capital=cap_notional / 0.25,
        per_ticker_exposure_cap=0.25,
        position_sizing="equal",
        cash_buffer_ratio=1.0,
        order_notional_safety_buffer=0.003,
    )
    decision = decide_buys(
        [BuyCandidate("fix_entity", "FIX", confidence=10.0, strength=1.0, price=price)],
        EmptyLedger(),
        params,
    )[0]
    raised_price = price * 1.003

    import engine.live.safety.layer as safety_layer

    symbols = tmp_path / "symbols"
    (symbols / "FIX").mkdir(parents=True)
    positions = tmp_path / "positions.json"
    positions.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(safety_layer, "SYMBOLS_DIR", symbols)
    monkeypatch.setattr(safety_layer, "POSITIONS_PATH", positions)
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "small_amount_safety:\n"
        "  enabled: true\n"
        "  max_shares_per_order: 0\n"
        "  max_notional_ratio: 0.25\n"
        "  max_orders_per_day: 1000000\n"
        "  require_first_order_approval: false\n"
        "entry:\n"
        "  cooldown_after_buy_hours: 0\n",
        encoding="utf-8",
    )

    safety = SafetyLayer(broker=SafetyBroker(cap_notional / 0.25), policy_path=policy)
    result = safety.check_order("BUY", "FIX", decision.shares, raised_price, purpose="entry")

    assert result.allowed, result


def test_policy_order_notional_safety_buffer_defaults_and_clamps():
    assert order_notional_safety_buffer_from_policy({}) == 0.003
    assert order_notional_safety_buffer_from_policy({"small_amount_safety": {"order_notional_safety_buffer": 0.004}}) == 0.004
    assert order_notional_safety_buffer_from_policy({"small_amount_safety": {"order_notional_safety_buffer": 0.02}}) == 0.005
    assert order_notional_safety_buffer_from_policy({"small_amount_safety": {"order_notional_safety_buffer": -1}}) == 0.0
