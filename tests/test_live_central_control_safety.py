from __future__ import annotations

import json
from types import SimpleNamespace

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.buy_reconciliation import BuyReconciliationService
from engine.live.central_control import LiveCentralController
from engine.live.pending_order_manager import PendingOrderManager
from engine.strategies.demo_rulebook import Signal, SignalResult


class Stats:
    market_ticks = 0
    signals_buy = 0
    signals_sell = 0
    signals_hold = 0


class Pending:
    load_error = ""

    def __init__(self, records=None):
        self._records = list(records or [])

    def all(self):
        return list(self._records)

    def is_ticker_locked(self, ticker):
        return False


class Broker:
    mode = "paper"

    def __init__(self, *, holdings=None, price=100.0):
        self.price = price
        self._holdings = list(holdings or [])

    def get_current_price(self, ticker):
        return self.price

    def get_holdings(self):
        return list(self._holdings)

    def get_balance(self):
        return SimpleNamespace(total_value_usd=100_000.0)


class SequenceBroker(Broker):
    def __init__(self, sequences, *, price=100.0):
        super().__init__(holdings=[], price=price)
        self._sequences = list(sequences)

    def get_holdings(self):
        if not self._sequences:
            return []
        value = self._sequences.pop(0)
        if isinstance(value, Exception):
            raise value
        return list(value)


class PositionManager:
    load_error = ""

    def __init__(self, positions=None):
        self._positions = list(positions or [])

    def all(self):
        return list(self._positions)


class SellRulebook:
    def evaluate(self, ticker, price):
        return SignalResult(ticker=ticker, signal=Signal.SELL, price=price, reason="test sell")


class Runner:
    def __init__(self, *, rulebook=None, positions=None, pending=None, holdings=None):
        self.symbols = ["AAA"]
        self.broker = Broker(holdings=holdings or [])
        self.rulebook = rulebook or SellRulebook()
        self.position_manager = PositionManager(positions or [])
        self.pending_order_manager = Pending(pending or [])
        self.stats = Stats()
        self.orders = []

    def _maybe_reconfirm_existing(self, ticker, price):
        pass

    def _try_order(self, side, ticker, price, reason, signal_result=None):
        self.orders.append((side, ticker, price, reason))


def make_controller(runner):
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.runner = runner
    ctl.config = SimpleNamespace(
        max_positions=8,
        confidence_weight=0.5,
        signal_strength_weight=0.5,
        min_confidence=0.0,
        per_ticker_exposure_cap=0.25,
        cash_buffer_ratio=0.98,
    )
    ctl.selection_metric = "confidence"
    ctl.position_sizing = "score_weighted"
    ctl.confidence_mode = "adjusted"
    ctl.selection_scores = {}
    ctl.entity_by_ticker = {
        "AAA": [SimpleNamespace(entity_id="AAA_entity", ticker="AAA", confidence=1.0, rulebook={})]
    }
    return ctl


def position_payload(ticker="AAA"):
    return {
        "ticker": ticker,
        "entry_date": "2026-01-01T00:00:00+09:00",
        "entry_price": 100.0,
        "shares": 1.0,
        "atr_at_entry": 2.0,
        "stop_price": 95.0,
        "target_price": 110.0,
        "trailing_distance": 3.0,
        "trailing_stop": 97.0,
        "highest_price": 101.0,
        "lowest_price": 99.0,
        "exit_strategy": "fixed",
        "max_holding_days": 30,
        "rulebook_direction": "long",
        "rulebook_snapshot": {},
        "member_hash": "member123456",
    }


def filled_buy_order(ticker="AAA", order_id="B1"):
    return Order(
        order_id=order_id,
        ticker=ticker,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        shares=1.0,
        price=0.0,
        status=OrderStatus.FILLED,
        filled_shares=1.0,
        filled_avg_price=100.0,
    )


class RulebookProvider:
    def get_last_atr(self, ticker):
        return None

    def get_rulebook(self, ticker):
        return object()


class PM:
    pass


def make_reconcile_service(tmp_path, broker, *, max_retries=1, confirm_seconds=0):
    pending = PendingOrderManager(broker, path=tmp_path / "pending.json")
    svc = BuyReconciliationService(
        broker=broker,
        rulebook_provider=RulebookProvider(),
        position_manager=PM(),
        pending_manager=pending,
        max_reconcile_retries=max_retries,
        empty_holding_confirm_seconds=confirm_seconds,
    )
    return svc, pending


def test_central_control_never_emits_sell_order():
    runner = Runner(rulebook=SellRulebook())
    ctl = make_controller(runner)

    ctl._process_central_buy_selection()

    assert runner.orders == []
    assert runner.stats.signals_sell == 1


def test_position_manager_load_error_blocks_central_new_buy(tmp_path, monkeypatch):
    from engine.live import position_manager as pm_module

    broken = tmp_path / "positions.json"
    broken.write_text("{not-valid-json", encoding="utf-8")
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", broken)
    real_pm = pm_module.PositionManager()
    assert real_pm.load_error
    assert real_pm.all() == []

    runner = Runner()
    runner.position_manager = real_pm
    ctl = make_controller(runner)

    ctl._process_central_buy_selection()

    assert runner.orders == []


def test_position_manager_missing_file_policy_first_run_allowed_then_marker_blocks(tmp_path, monkeypatch):
    from engine.live import position_manager as pm_module

    positions = tmp_path / "positions.json"
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", positions)

    first = pm_module.PositionManager()
    assert first.load_error == ""
    assert positions.exists()
    assert (tmp_path / "positions.json.initialized").exists()

    positions.unlink()
    second = pm_module.PositionManager()
    assert second.load_error
    assert second.all() == []


def test_position_manager_marker_write_failure_sets_load_error_and_blocks_buys(tmp_path, monkeypatch):
    from engine.live import position_manager as pm_module

    positions = tmp_path / "positions.json"
    positions.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", positions)
    original_write_text = pm_module.Path.write_text

    def fake_write_text(self, *args, **kwargs):
        if str(self).endswith("positions.json.initialized"):
            raise OSError("marker disk error")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pm_module.Path, "write_text", fake_write_text)
    real_pm = pm_module.PositionManager()

    assert "init marker write failed" in real_pm.load_error
    assert real_pm.all() == []
    runner = Runner()
    runner.position_manager = real_pm
    ctl = make_controller(runner)

    ctl._process_central_buy_selection()

    assert runner.orders == []


def test_position_manager_marker_write_failure_preserves_loaded_positions_and_blocks_buys(tmp_path, monkeypatch):
    from engine.live import position_manager as pm_module

    positions = tmp_path / "positions.json"
    positions.write_text(json.dumps({"AAA": position_payload("AAA")}), encoding="utf-8")
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", positions)
    original_write_text = pm_module.Path.write_text

    def fake_write_text(self, *args, **kwargs):
        if str(self).endswith("positions.json.initialized"):
            raise OSError("marker disk error")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pm_module.Path, "write_text", fake_write_text)
    real_pm = pm_module.PositionManager()

    assert "init marker write failed" in real_pm.load_error
    assert len(real_pm.all()) == 1
    assert real_pm.get("AAA") is not None

    runner = Runner()
    runner.position_manager = real_pm
    ctl = make_controller(runner)
    ctl._process_central_buy_selection()

    assert runner.orders == []


def test_position_manager_marker_normal_creation_keeps_load_error_empty(tmp_path, monkeypatch):
    from engine.live import position_manager as pm_module

    positions = tmp_path / "positions.json"
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", positions)

    manager = pm_module.PositionManager()

    assert manager.load_error == ""
    assert positions.exists()
    assert (tmp_path / "positions.json.initialized").exists()


def test_position_manager_load_error_clears_after_normal_reload(tmp_path, monkeypatch):
    from engine.live import position_manager as pm_module

    positions = tmp_path / "positions.json"
    positions.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(pm_module, "POSITIONS_PATH", positions)
    manager = pm_module.PositionManager()
    assert manager.load_error
    assert manager.all() == []

    positions.write_text("{}", encoding="utf-8")
    manager._load()

    assert manager.load_error == ""
    assert manager.all() == []


def test_pending_buy_and_orphan_holding_reduce_live_slots():
    pending_buy = SimpleNamespace(
        ticker="BBB",
        side="buy",
        state="RECONCILING",
        requested_shares=1.0,
        filled_shares=0.0,
        filled_avg_price=0.0,
        order_id="B1",
    )
    orphan = Holding("CCC", 1.0, 100.0, 100.0, 100.0, 0.0, 0.0)
    runner = Runner(pending=[pending_buy], holdings=[orphan])
    ctl = make_controller(runner)

    ledger = ctl._build_live_ledger_view()
    tickers = {p.ticker for p in ledger.open_positions()}

    assert tickers == {"BBB", "CCC"}
    candidates = [BuyCandidate("ddd", "DDD", confidence=1.0, strength=1.0, price=100.0)]
    params = AllocationParams(max_positions=2, total_capital=10_000.0, per_ticker_exposure_cap=1.0)
    assert decide_buys(candidates, ledger, params) == []


def test_allocation_applies_cash_buffer_and_current_price_exposure_cap():
    class Ledger:
        def __init__(self, positions=None):
            self.positions = list(positions or [])

        def open_positions(self):
            return list(self.positions)

    one = [BuyCandidate("aaa", "AAA", confidence=1.0, strength=1.0, price=100.0)]
    buffered = decide_buys(
        one,
        Ledger(),
        AllocationParams(
            max_positions=1,
            total_capital=10_000.0,
            per_ticker_exposure_cap=1.0,
            position_sizing="equal",
            cash_buffer_ratio=0.80,
        ),
    )
    assert len(buffered) == 1
    assert buffered[0].notional <= 8_000.0 + 1e-6

    held = SimpleNamespace(
        entity_id="aaa",
        ticker="AAA",
        open_shares=10.0,
        avg_entry_price=100.0,
        current_price=200.0,
        add_buy_count=0,
        position_id="pos-aaa",
    )
    add_buy = [
        BuyCandidate(
            "aaa",
            "AAA",
            confidence=1.0,
            strength=1.0,
            price=200.0,
            rulebook={"add_buy_enabled": True, "add_buy_max_count": 1},
        )
    ]
    capped = decide_buys(
        add_buy,
        Ledger([held]),
        AllocationParams(
            max_positions=1,
            total_capital=10_000.0,
            per_ticker_exposure_cap=0.25,
            position_sizing="equal",
            cash_buffer_ratio=1.0,
        ),
    )
    assert len(capped) == 1
    assert capped[0].notional <= 500.0 + 1e-6


def test_allocation_blocks_add_buy_when_existing_exposure_price_unknown():
    class Ledger:
        def open_positions(self):
            return [
                SimpleNamespace(
                    entity_id="aaa",
                    ticker="AAA",
                    open_shares=10.0,
                    current_price=0.0,
                    market_price=0.0,
                    avg_entry_price=0.0,
                    add_buy_count=0,
                    position_id="pos-aaa",
                )
            ]

    add_buy = [
        BuyCandidate(
            "aaa",
            "AAA",
            confidence=1.0,
            strength=1.0,
            price=200.0,
            rulebook={"add_buy_enabled": True, "add_buy_max_count": 1},
        )
    ]

    assert decide_buys(add_buy, Ledger(), AllocationParams(max_positions=1, total_capital=10_000.0)) == []


def test_unknown_exposure_ticker_does_not_block_unrelated_new_ticker():
    class Ledger:
        def open_positions(self):
            return [
                SimpleNamespace(
                    entity_id="aaa",
                    ticker="AAA",
                    open_shares=10.0,
                    current_price=0.0,
                    market_price=0.0,
                    avg_entry_price=0.0,
                    add_buy_count=0,
                    position_id="pos-aaa",
                )
            ]

    decision = decide_buys(
        [BuyCandidate("ddd", "DDD", confidence=1.0, strength=1.0, price=100.0)],
        Ledger(),
        AllocationParams(max_positions=2, total_capital=10_000.0, per_ticker_exposure_cap=1.0),
    )

    assert len(decision) == 1
    assert decision[0].ticker == "DDD"


def test_buy_reconciliation_get_holdings_exception_keeps_pending(tmp_path):
    broker = SequenceBroker([RuntimeError("temporary outage")])
    svc, pending = make_reconcile_service(tmp_path, broker, max_retries=1)

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr")

    rows = pending.all()
    assert len(rows) == 1
    assert rows[0].ticker == "AAA"


def test_buy_reconciliation_transient_empty_then_holding_keeps_pending(tmp_path):
    holding = Holding("AAA", 1.0, 100.0, 100.0, 100.0, 0.0, 0.0)
    broker = SequenceBroker([[], [holding]])
    svc, pending = make_reconcile_service(tmp_path, broker, max_retries=1, confirm_seconds=0)

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr")
    rows = pending.all()
    assert len(rows) == 1
    assert rows[0].metadata["zero_holding_seen_count"] == 1

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr again")

    rows = pending.all()
    assert len(rows) == 1
    assert "zero_holding_seen_count" not in rows[0].metadata


def test_buy_reconciliation_zero_probe_resets_on_lookup_exception(tmp_path):
    broker = SequenceBroker([[], RuntimeError("temporary outage"), []])
    svc, pending = make_reconcile_service(tmp_path, broker, max_retries=1, confirm_seconds=0)

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr")
    assert pending.all()[0].metadata["zero_holding_seen_count"] == 1

    svc.track_failure(filled_buy_order(), purpose="entry", error="lookup outage")
    rows = pending.all()
    assert len(rows) == 1
    assert "zero_holding_seen_count" not in rows[0].metadata

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr again")
    rows = pending.all()
    assert len(rows) == 1
    assert rows[0].metadata["zero_holding_seen_count"] == 1


def test_buy_reconciliation_drops_only_after_two_zero_holding_confirmations(tmp_path):
    broker = SequenceBroker([[], []])
    svc, pending = make_reconcile_service(tmp_path, broker, max_retries=1, confirm_seconds=0)

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr")
    assert len(pending.all()) == 1

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr again")
    assert pending.all() == []


def test_buy_reconciliation_zero_confirmation_too_soon_keeps_pending(tmp_path):
    broker = SequenceBroker([[], []])
    svc, pending = make_reconcile_service(tmp_path, broker, max_retries=1, confirm_seconds=60)

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr")
    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr again")

    assert len(pending.all()) == 1


def test_buy_reconciliation_keeps_pending_after_retry_limit_when_broker_holding_exists(tmp_path):
    holding = Holding("AAA", 1.0, 100.0, 100.0, 100.0, 0.0, 0.0)
    broker = Broker(holdings=[holding])
    svc, pending = make_reconcile_service(tmp_path, broker, max_retries=1)

    svc.track_failure(filled_buy_order(), purpose="entry", error="no atr")

    rows = pending.all()
    assert len(rows) == 1
    assert rows[0].ticker == "AAA"
