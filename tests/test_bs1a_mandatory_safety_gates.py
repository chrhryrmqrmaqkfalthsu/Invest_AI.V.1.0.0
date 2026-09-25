from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from engine.live.safety import layer as safety_layer_mod
from engine.live.safety import state as safety_state_mod
from engine.live.safety.layer import SafetyLayer


class GateBroker:
    def __init__(self, *, market_open: bool = True):
        self.market_open = market_open

    def get_holdings(self):
        return []

    def is_market_open(self, ticker=None):
        return self.market_open


def make_policy(tmp_path: Path, *, first_approval: bool = False) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        f"""
small_amount_safety:
  enabled: false
  max_shares_per_order: 1
  max_notional_ratio: 0.25
  max_total_invested_krw: 10
  max_orders_per_day: 1
  require_first_order_approval: {str(first_approval).lower()}
  daily_loss_limit_krw: 100
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
    return path


def setup_paths(monkeypatch, tmp_path: Path, *, whitelisted: bool = True):
    system = tmp_path / "system"
    symbols = tmp_path / "symbols"
    system.mkdir(parents=True, exist_ok=True)
    symbols.mkdir(parents=True, exist_ok=True)
    symbol_path = symbols / "AAA"
    if whitelisted:
        symbol_path.mkdir(exist_ok=True)
    elif symbol_path.exists():
        symbol_path.rmdir()
    monkeypatch.setattr(safety_layer_mod, "KILL_SWITCH_PATH", system / "KILL_SWITCH")
    monkeypatch.setattr(safety_layer_mod, "POSITIONS_PATH", system / "positions.json")
    monkeypatch.setattr(safety_layer_mod, "SYMBOLS_DIR", symbols)
    monkeypatch.setattr(safety_state_mod, "STATE_PATH", system / "safety_state.json")
    return system


def test_enabled_false_cannot_bypass_kill_switch_for_buy_or_sell(monkeypatch, tmp_path):
    system = setup_paths(monkeypatch, tmp_path)
    (system / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    safety = SafetyLayer(broker=GateBroker(), policy_path=make_policy(tmp_path))

    assert safety.check_order("BUY", "AAA", 1, 100).code == "KILL_SWITCH"
    assert safety.check_order("SELL", "AAA", 1, 100).code == "KILL_SWITCH"


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [("kill_until", "DAILY_LOSS"), ("cooldown_until", "COOLDOWN")],
)
def test_enabled_false_cannot_bypass_persistent_lock(monkeypatch, tmp_path, field, expected_code):
    setup_paths(monkeypatch, tmp_path)
    state = safety_state_mod.SafetyState(date=datetime.now().date().isoformat())
    setattr(state, field, (datetime.now() + timedelta(hours=1)).isoformat())
    safety_state_mod.save(state)
    safety = SafetyLayer(broker=GateBroker(), policy_path=make_policy(tmp_path))

    assert safety.check_order("BUY", "AAA", 1, 100).code == expected_code
    assert safety.check_order("SELL", "AAA", 1, 100).code == expected_code


def test_enabled_false_still_enforces_market_and_whitelist(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    closed = SafetyLayer(broker=GateBroker(market_open=False), policy_path=make_policy(tmp_path))
    assert closed.check_order("SELL", "AAA", 1, 100).code == "MARKET_CLOSED"

    setup_paths(monkeypatch, tmp_path, whitelisted=False)
    open_broker = SafetyLayer(broker=GateBroker(market_open=True), policy_path=make_policy(tmp_path))
    assert open_broker.check_order("SELL", "AAA", 1, 100).code == "NOT_WHITELISTED"


def test_enabled_false_still_requires_first_buy_approval(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=GateBroker(), policy_path=make_policy(tmp_path, first_approval=True))
    assert safety.check_order("BUY", "AAA", 1, 100).code == "NEED_APPROVAL"


def test_enabled_false_disables_only_small_amount_limits(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    state = safety_state_mod.SafetyState(
        date=datetime.now().date().isoformat(),
        orders_today=99,
        invested_krw_today=999999,
        first_order_approved=True,
    )
    safety_state_mod.save(state)
    safety = SafetyLayer(broker=GateBroker(), policy_path=make_policy(tmp_path, first_approval=True))

    decision = safety.check_order("BUY", "AAA", 1000, 1000)
    assert decision.allowed is True
    assert "소액 한도 비활성" in decision.reason
