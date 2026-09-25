import itertools
import json
from pathlib import Path

import pandas as pd
import pytest

import engine.central.ledger as ledger_mod
from engine.central.allocation_policy import AllocationParams
from engine.central.backtester import run_central_backtest
from engine.central.broker_port import MockBroker
from engine.central.entity_loader import EntityRecord
from engine.central.ledger import EntityPositionLedger
from engine.core.indicators import calc_indicators
from engine.live.broker.base import OrderStatus


class MemoryProvider:
    def __init__(self, frames):
        self.frames = {str(k).upper(): v.copy() for k, v in frames.items()}

    def load_price_df(self, ticker):
        return self.frames[str(ticker).upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker):
        return {}


def test_ledger_memory_mode_skips_writes_until_flush(tmp_path):
    ledger = EntityPositionLedger(base_dir=tmp_path, persist=False)
    broker = MockBroker()
    broker.queue_order(status=OrderStatus.FILLED, filled_shares=10, filled_avg_price=100)

    intent = ledger.open_intent("entity-A", "AAA", "buy", "entry", 10, "buy")
    execution = ledger.dispatch_execution(intent.intent_id, broker, "cid-buy")

    assert execution.state == "filled"
    assert ledger.open_positions()[0].open_shares == 10
    assert not (tmp_path / "ledger_positions.json").exists()
    assert not (tmp_path / "ledger_executions.json").exists()
    assert not (tmp_path / "ledger_intents.json").exists()

    ledger.flush()
    assert (tmp_path / "ledger_positions.json").exists()
    assert (tmp_path / "ledger_executions.json").exists()
    assert (tmp_path / "ledger_intents.json").exists()

    loaded = EntityPositionLedger(base_dir=tmp_path)
    assert loaded.to_record_dicts() == ledger.to_record_dicts()


def test_ledger_memory_mode_does_not_load_existing_disk_state(tmp_path):
    disk = EntityPositionLedger(base_dir=tmp_path)
    broker = MockBroker()
    broker.queue_order(status=OrderStatus.FILLED, filled_shares=5, filled_avg_price=50)
    intent = disk.open_intent("entity-A", "AAA", "buy", "entry", 5, "seed")
    disk.dispatch_execution(intent.intent_id, broker, "cid-seed")
    assert EntityPositionLedger(base_dir=tmp_path).open_positions()

    memory = EntityPositionLedger(base_dir=tmp_path, persist=False)
    assert memory.open_positions() == []
    assert memory.load_error == ""


def test_ledger_persist_and_memory_record_snapshots_are_identical(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger_mod, "_now_iso", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(MockBroker, "_now_iso", staticmethod(lambda: "2026-01-01T00:00:00Z"))

    def run_case(root: Path, persist: bool):
        monkeypatch.setattr(ledger_mod, "_new_id", _id_factory())
        ledger = EntityPositionLedger(base_dir=root, persist=persist)
        broker = MockBroker()
        broker.queue_order(status=OrderStatus.FILLED, filled_shares=10, filled_avg_price=100)
        buy_a = ledger.open_intent("entity-A", "AAA", "buy", "entry", 10, "buy A")
        exec_a = ledger.dispatch_execution(buy_a.intent_id, broker, "cid-buy-a")
        pos_a = ledger.get_position(exec_a.position_id)
        broker.queue_order(status=OrderStatus.FILLED, filled_shares=5, filled_avg_price=120)
        buy_b = ledger.open_intent("entity-B", "AAA", "buy", "entry", 5, "buy B")
        ledger.dispatch_execution(buy_b.intent_id, broker, "cid-buy-b")
        broker.queue_order(status=OrderStatus.FILLED, filled_shares=4, filled_avg_price=130)
        sell_a = ledger.open_intent("entity-A", "AAA", "sell", "exit", 4, "sell A", pos_a.position_id)
        ledger.dispatch_execution(sell_a.intent_id, broker, "cid-sell-a")
        assert ledger.reconcile(broker)["ok"] is True
        if not persist:
            ledger.flush()
        loaded = EntityPositionLedger(base_dir=root)
        return ledger.to_record_dicts(), loaded.to_record_dicts()

    disk_live, disk_loaded = run_case(tmp_path / "disk", True)
    memory_live, memory_loaded = run_case(tmp_path / "memory", False)

    assert disk_live == disk_loaded
    assert memory_live == memory_loaded
    assert disk_live == memory_live


def test_backtester_persist_and_memory_outputs_are_identical(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger_mod, "_now_iso", lambda: "2026-01-01T00:00:00Z")
    frames = {"AAA": _price_df()}
    entities = [_entity("AAA", "hashaaa111111")]
    params = AllocationParams(max_positions=1, min_confidence=0.0, total_capital=10_000.0, per_ticker_exposure_cap=1.0, position_sizing="equal")

    def run_case(root: Path, persist: bool):
        monkeypatch.setattr(ledger_mod, "_new_id", _id_factory())
        result = run_central_backtest(
            entities,
            "2025-03-03",
            "2025-03-21",
            params,
            data_provider=MemoryProvider(frames),
            ledger_dir=root,
            persist_ledger=persist,
            flush_ledger_on_finish=True,
        )
        loaded = EntityPositionLedger(base_dir=root)
        return result.to_dict(), loaded.to_record_dicts()

    disk_result, disk_ledger = run_case(tmp_path / "disk", True)
    memory_result, memory_ledger = run_case(tmp_path / "memory", False)

    assert disk_result == memory_result
    assert disk_ledger == memory_ledger
    assert disk_result["reconcile_failures"] == []


def _id_factory():
    counter = itertools.count(1)

    def _new(prefix: str) -> str:
        return f"{prefix}_{next(counter):016d}"

    return _new


def _price_df(days=80, start="2025-01-01", base=100.0):
    idx = pd.bdate_range(start, periods=days)
    rows = []
    for i, _ in enumerate(idx):
        close = base + i * 0.25
        rows.append({"Open": close - 0.1, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": 1_000_000 + i})
    return calc_indicators(pd.DataFrame(rows, index=idx))


def _entity(ticker: str, rulebook_hash: str) -> EntityRecord:
    return EntityRecord(
        entity_id=f"{ticker}_{rulebook_hash[:12]}",
        ticker=ticker,
        rulebook={
            "ticker": ticker,
            "asset_type": "us_stock",
            "direction": "long",
            "weight_ma_align": 0.0,
            "weight_macd_golden": 0.0,
            "weight_rsi_zone": 0.0,
            "weight_bb_near_lower": 0.0,
            "weight_volume_surge": 0.0,
            "weight_news_sentiment": 0.0,
            "signal_threshold": 0.0,
            "stop_loss_atr": 2.0,
            "take_profit_atr": 20.0,
            "trailing_atr": 50.0,
            "max_holding_days": 3,
            "exit_strategy": "hybrid",
            "position_sizing_strategy": "fixed",
            "base_position_ratio": 1.0,
            "use_news_global": False,
            "use_event_block": False,
            "use_market_entry_adjustment": False,
            "add_buy_enabled": False,
        },
        rulebook_hash=rulebook_hash,
        validation_metrics={"p1": {"expectancy_pct": 2.0, "win_rate": 60.0, "profit_factor": 1.5, "trade_count": 10, "max_drawdown_pct": -5.0}},
        validation_periods=[{"label": "p1", "start": "2025-03-03", "end": "2025-03-21"}],
        tags={},
        confidence=0.5,
    )
