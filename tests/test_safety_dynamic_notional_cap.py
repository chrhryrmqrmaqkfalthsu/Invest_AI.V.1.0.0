from __future__ import annotations

import json
from pathlib import Path

from engine.live.broker.base import Balance
from engine.live.safety import layer as safety_layer_mod
from engine.live.safety import state as safety_state_mod
from engine.live.safety.layer import SafetyLayer


class RatioBroker:
    def __init__(self, *, total_value=1000.0, fail_balance=False, market_open=True):
        self.total_value = total_value
        self.fail_balance = fail_balance
        self.market_open = market_open

    def get_balance(self):
        if self.fail_balance:
            raise RuntimeError("balance down")
        return Balance(cash_krw=float(self.total_value or 0.0), total_value_krw=float(self.total_value or 0.0), invested_krw=0.0, holdings=[])

    def get_holdings(self):
        return []

    def is_market_open(self, ticker=None):
        return self.market_open


def write_policy(tmp_path: Path, *, ratio=0.25) -> Path:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        f"""
small_amount_safety:
  enabled: true
  max_shares_per_order: 1000000
  max_notional_ratio: {ratio}
  max_bought_notional_per_day: 0
  max_total_exposure_notional: 0
  max_orders_per_day: 100
  require_first_order_approval: false
risk:
  daily_loss_limit_pct: 10
  consecutive_loss_limit: 3
  cooldown_after_consecutive_loss_hours: 24
entry:
  one_position_per_symbol: true
  cooldown_after_buy_hours: 0
add_buy:
  enabled: true
  min_cooldown_minutes: 0
""".strip(),
        encoding="utf-8",
    )
    return policy


def patch_paths(monkeypatch, tmp_path: Path):
    system = tmp_path / "system"
    symbols = tmp_path / "symbols"
    system.mkdir(parents=True, exist_ok=True)
    symbols.mkdir(parents=True, exist_ok=True)
    (symbols / "AAA").mkdir(parents=True, exist_ok=True)
    positions = system / "positions.json"
    positions.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(safety_layer_mod, "KILL_SWITCH_PATH", system / "KILL_SWITCH")
    monkeypatch.setattr(safety_layer_mod, "POSITIONS_PATH", positions)
    monkeypatch.setattr(safety_layer_mod, "SYMBOLS_DIR", symbols)
    monkeypatch.setattr(safety_state_mod, "STATE_PATH", system / "safety_state.json")


def test_dynamic_notional_cap_allows_exact_25_percent_boundary(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=RatioBroker(total_value=1000.0), policy_path=write_policy(tmp_path, ratio=0.25))

    decision = safety.check_order("BUY", "AAA", 2.5, 100.0)

    assert decision.allowed is True


def test_dynamic_notional_cap_blocks_above_25_percent_boundary(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=RatioBroker(total_value=1000.0), policy_path=write_policy(tmp_path, ratio=0.25))

    decision = safety.check_order("BUY", "AAA", 2.51, 100.0)

    assert decision.allowed is False
    assert decision.code == "LIMIT_NOTIONAL"
    assert "251.00" in decision.reason
    assert "250.00" in decision.reason


def test_dynamic_notional_cap_fail_closed_without_broker(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=None, policy_path=write_policy(tmp_path, ratio=0.25))

    decision = safety.check_order("BUY", "AAA", 1.0, 100.0)

    assert decision.allowed is False
    assert decision.code == "LIMIT_NOTIONAL"
    assert "broker 없음" in decision.reason


def test_dynamic_notional_cap_fail_closed_on_balance_exception(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=RatioBroker(fail_balance=True), policy_path=write_policy(tmp_path, ratio=0.25))

    decision = safety.check_order("BUY", "AAA", 1.0, 100.0)

    assert decision.allowed is False
    assert decision.code == "LIMIT_NOTIONAL"
    assert "잔고 조회 실패" in decision.reason


def test_dynamic_notional_cap_fail_closed_on_zero_total_value(monkeypatch, tmp_path):
    patch_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=RatioBroker(total_value=0.0), policy_path=write_policy(tmp_path, ratio=0.25))

    decision = safety.check_order("BUY", "AAA", 1.0, 100.0)

    assert decision.allowed is False
    assert decision.code == "LIMIT_NOTIONAL"
    assert "계좌 총액" in decision.reason
