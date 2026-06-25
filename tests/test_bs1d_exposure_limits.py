from __future__ import annotations

from datetime import date
from pathlib import Path

from engine.live.broker.base import Balance, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.pending_order_manager import PendingOrderManager
from engine.live.safety import layer as safety_layer_mod
from engine.live.safety import state as safety_state_mod
from engine.live.safety.layer import SafetyLayer


class Broker:
    def __init__(self, *, holdings=None, fail_holdings=False, market_open=True, total_value=100000.0):
        self.holdings = holdings or []
        self.fail_holdings = fail_holdings
        self.market_open = market_open
        self.total_value = float(total_value)

    def get_balance(self):
        return Balance(
            cash_krw=self.total_value,
            total_value_krw=self.total_value,
            invested_krw=sum(h.shares * h.avg_cost for h in self.holdings),
            holdings=self.get_holdings(),
        )

    def get_holdings(self):
        if self.fail_holdings:
            raise RuntimeError("holdings down")
        return list(self.holdings)

    def is_market_open(self, ticker=None):
        return self.market_open


def make_policy(tmp_path: Path, *, daily=1000, exposure=1000) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(
        f"""
small_amount_safety:
  enabled: true
  max_shares_per_order: 100000
  max_notional_ratio: 10.0
  max_bought_notional_per_day: {daily}
  max_total_exposure_notional: {exposure}
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
    return path


def setup_paths(monkeypatch, tmp_path: Path):
    system = tmp_path / "system"
    symbols = tmp_path / "symbols"
    system.mkdir(parents=True, exist_ok=True)
    symbols.mkdir(parents=True, exist_ok=True)
    (symbols / "AAA").mkdir()
    monkeypatch.setattr(safety_layer_mod, "KILL_SWITCH_PATH", system / "KILL_SWITCH")
    monkeypatch.setattr(safety_layer_mod, "POSITIONS_PATH", system / "positions.json")
    monkeypatch.setattr(safety_layer_mod, "SYMBOLS_DIR", symbols)
    monkeypatch.setattr(safety_state_mod, "STATE_PATH", system / "safety_state.json")
    return system


def test_next_day_existing_holding_blocks_by_total_exposure(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    safety_state_mod.save(safety_state_mod.SafetyState(date="1999-01-01", invested_krw_today=0))
    broker = Broker(holdings=[Holding("ZZZ", 9.0, 100.0, 100.0, 900.0, 0.0, 0.0)])
    safety = SafetyLayer(broker=broker, policy_path=make_policy(tmp_path, daily=100000, exposure=950))

    decision = safety.check_order("BUY", "AAA", 1, 100)

    assert decision.allowed is False
    assert decision.code == "LIMIT_TOTAL_EXPOSURE"


def test_sell_reduces_exposure_and_allows_future_buy(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    broker = Broker(holdings=[Holding("ZZZ", 5.0, 100.0, 100.0, 500.0, 0.0, 0.0)])
    safety = SafetyLayer(broker=broker, policy_path=make_policy(tmp_path, daily=100000, exposure=950))
    assert safety.check_order("BUY", "AAA", 1, 100).allowed is True

    broker.holdings = []
    assert safety.check_order("BUY", "AAA", 9, 100).allowed is True


def test_pending_buy_reserved_notional_counts_in_exposure(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    broker = Broker(holdings=[Holding("ZZZ", 5.0, 100.0, 100.0, 500.0, 0.0, 0.0)])
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    pending.track_order(
        Order("B1", "BBB", OrderSide.BUY, OrderType.MARKET, 3.0, 0.0, OrderStatus.PENDING),
        purpose="entry",
    )
    safety = SafetyLayer(broker=broker, policy_path=make_policy(tmp_path, daily=100000, exposure=850))
    safety.pending_order_manager = pending

    decision = safety.check_order("BUY", "AAA", 1, 100)

    assert decision.allowed is False
    assert decision.code == "LIMIT_TOTAL_EXPOSURE"


def test_holdings_query_failure_blocks_buy(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=Broker(fail_holdings=True), policy_path=make_policy(tmp_path, daily=100000, exposure=1000))

    decision = safety.check_order("BUY", "AAA", 1, 100)

    assert decision.allowed is False
    assert decision.code in {"HOLDINGS_CHECK_FAILED", "EXPOSURE_CHECK_FAILED", "LIMIT_NOTIONAL"}


def test_daily_bought_limit_uses_today_counter_and_resets_on_rollover(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    state = safety_state_mod.SafetyState(date=date.today().isoformat(), invested_krw_today=950, first_order_approved=True)
    safety_state_mod.save(state)
    safety = SafetyLayer(broker=Broker(), policy_path=make_policy(tmp_path, daily=1000, exposure=100000))
    assert safety.check_order("BUY", "AAA", 1, 100).code == "LIMIT_DAILY_BUY_NOTIONAL"

    state.date = "1999-01-01"
    safety_state_mod.save(state)
    assert safety.check_order("BUY", "AAA", 1, 100).allowed is True


def test_paper_like_empty_holdings_path_allows_buy_under_limits(monkeypatch, tmp_path):
    setup_paths(monkeypatch, tmp_path)
    safety = SafetyLayer(broker=Broker(holdings=[]), policy_path=make_policy(tmp_path, daily=1000, exposure=1000))
    assert safety.check_order("BUY", "AAA", 1, 100).allowed is True
