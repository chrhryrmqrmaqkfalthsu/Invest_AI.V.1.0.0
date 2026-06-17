import json
from pathlib import Path

import pandas as pd
import pytest

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.central.backtester import common_validation_window, run_central_backtest
from engine.central.entity_loader import ConfidenceParams, load_entities_from_catalog
from engine.central.ledger import EntityPositionLedger
from engine.central.signal_collector import SignalCollector
from engine.central.sim_broker import FillPolicy, SimBroker
from engine.live.broker.base import OrderSide


class MemoryProvider:
    def __init__(self, frames):
        self.frames = {k.upper(): v for k, v in frames.items()}

    def load_price_df(self, ticker):
        return self.frames[ticker.upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker):
        return {}


def _price_df(days=90, start="2025-01-01", base=100.0):
    idx = pd.bdate_range(start, periods=days)
    rows = []
    for i, _ in enumerate(idx):
        close = base + i * 0.25
        rows.append({"Open": close - 0.1, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": 1_000_000 + i})
    return pd.DataFrame(rows, index=idx)


def _rulebook(ticker="AAA", max_holding_days=3):
    return {
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
        "max_holding_days": max_holding_days,
        "exit_strategy": "hybrid",
        "position_sizing_strategy": "fixed",
        "base_position_ratio": 1.0,
        "use_news_global": False,
        "use_event_block": False,
        "use_market_entry_adjustment": False,
        "add_buy_enabled": False,
    }


def _catalog_row(ticker="AAA", rulebook_hash="abcdef1234567890"):
    return {
        "ticker": ticker,
        "rulebook_hash": rulebook_hash,
        "rulebook": _rulebook(ticker),
        "period_results": {
            "train_1": {
                "role": "pure_oos",
                "metrics": {"expectancy_pct": 2.0, "win_rate": 60, "profit_factor": 1.4, "trade_count": 10, "max_drawdown_pct": -4},
            },
            "recent_1y": {
                "role": "pure_oos",
                "metrics": {"expectancy_pct": 4.0, "win_rate": 65, "profit_factor": 1.8, "trade_count": 12, "max_drawdown_pct": -5},
            },
            "stress_pre_2022h1": {
                "role": "exit_check",
                "metrics": {"expectancy_pct": -1.0, "trade_count": 3},
            },
        },
        "pure_oos_validation_periods": [
            {"label": "train_1", "start": "2022-07-01", "end": "2023-06-30"},
            {"label": "recent_1y", "start": "2025-01-01", "end": "2025-03-31"},
        ],
        "holding_class": "short",
        "risk_class": "low",
        "return_class": "steady",
        "composite_tag": "test",
    }


def test_sim_broker_next_open_fill_and_holdings():
    df = pd.DataFrame(
        [
            {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.5},
            {"Open": 12.0, "High": 13.0, "Low": 11.0, "Close": 12.5},
        ],
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )
    broker = SimBroker({"AAA": df}, initial_cash=1_000.0, fill_policy=FillPolicy())
    broker.set_date("2025-01-02")
    order = broker.place_buy("AAA", 10, client_order_id="x")
    assert order.status.value == "filled"
    assert order.filled_avg_price == 12.0
    assert broker.cash == pytest.approx(880.0)
    assert broker.get_holdings()[0].shares == 10

    sell = broker.place_sell("AAA", 4)
    assert sell.filled_avg_price == 12.0
    assert broker.get_holdings()[0].shares == 6


def test_entity_loader_catalog_confidence(tmp_path):
    catalog = tmp_path / "stage3_profile_catalog.jsonl"
    catalog.write_text(json.dumps(_catalog_row()) + "\n", encoding="utf-8")
    entities = load_entities_from_catalog(catalog, params=ConfidenceParams(method="avg_expectancy", min_trade_count=5, confidence_scale=10.0))
    assert len(entities) == 1
    entity = entities[0]
    assert entity.entity_id == "AAA_abcdef123456"
    assert sorted(entity.validation_metrics) == ["recent_1y", "train_1"]
    assert entity.confidence == pytest.approx(0.3)


def test_signal_collector_is_deterministic_and_uses_current_slice_only(tmp_path):
    df = _price_df(days=90)
    entity = load_entities_from_catalog(_write_catalog_tmp(tmp_path, _catalog_row("AAA", "abcdef1234567890")))[0]
    provider = MemoryProvider({"AAA": df})
    collector = SignalCollector(provider, use_llm_events=False)
    date = df.index[75]
    first = collector.signal_for_date(entity, date)
    second = collector.signal_for_date(entity, date)
    assert first == second
    assert first is not None
    assert first.date == date.strftime("%Y-%m-%d")
    assert first.should_buy is True


def test_allocation_policy_limits_and_caps(tmp_path):
    ledger = EntityPositionLedger(base_dir=tmp_path / "ledger")
    candidates = [
        BuyCandidate("e1", "AAA", confidence=1.0, strength=2.0, price=100.0),
        BuyCandidate("e2", "BBB", confidence=-1.0, strength=10.0, price=100.0),
        BuyCandidate("e3", "AAA", confidence=1.0, strength=1.0, price=100.0),
    ]
    params = AllocationParams(max_positions=2, min_confidence=0.0, total_capital=10_000.0, per_ticker_exposure_cap=0.30)
    decisions = decide_buys(candidates, ledger, params)
    assert [d.entity_id for d in decisions] == ["e1"]
    assert sum(d.notional for d in decisions if d.ticker == "AAA") <= 3_000.0 + 1e-6


def test_backtester_end_to_end_reconciles_and_is_deterministic(tmp_path):
    df = _price_df(days=95)
    row_a = _catalog_row("AAA", "aaa1112223334444")
    row_b = _catalog_row("BBB", "bbb1112223334444")
    row_b["rulebook"] = _rulebook("BBB")
    catalog = tmp_path / "stage3_profile_catalog.jsonl"
    catalog.write_text(json.dumps(row_a) + "\n" + json.dumps(row_b) + "\n", encoding="utf-8")
    entities = load_entities_from_catalog(catalog)
    provider = MemoryProvider({"AAA": df, "BBB": df.copy()})
    params = AllocationParams(max_positions=2, min_confidence=0.0, total_capital=10_000.0, per_ticker_exposure_cap=0.5, position_sizing="equal")
    result1 = run_central_backtest(
        entities,
        start=df.index[70].strftime("%Y-%m-%d"),
        end=df.index[80].strftime("%Y-%m-%d"),
        alloc_params=params,
        data_provider=provider,
        ledger_dir=tmp_path / "ledger1",
    )
    result2 = run_central_backtest(
        entities,
        start=df.index[70].strftime("%Y-%m-%d"),
        end=df.index[80].strftime("%Y-%m-%d"),
        alloc_params=params,
        data_provider=provider,
        ledger_dir=tmp_path / "ledger2",
    )
    assert result1.reconcile_failures == []
    assert len(result1.equity_curve) > 0
    assert result1.to_dict()["equity_curve"] == result2.to_dict()["equity_curve"]
    assert result1.final_equity == pytest.approx(result2.final_equity)
    assert sum(result1.per_entity_pnl.values()) == pytest.approx(result1.final_equity - params.total_capital)


def test_common_validation_window_recent_1y(tmp_path):
    catalog = tmp_path / "stage3_profile_catalog.jsonl"
    catalog.write_text(json.dumps(_catalog_row()) + "\n", encoding="utf-8")
    entities = load_entities_from_catalog(catalog)
    assert common_validation_window(entities) == ("2025-01-01", "2025-03-31")


def _write_catalog_tmp(root: Path, row: dict) -> Path:
    path = root / "central_test_stage3_profile_catalog.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path
