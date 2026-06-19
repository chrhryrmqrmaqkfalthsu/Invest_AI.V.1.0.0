import json
from pathlib import Path

import pandas as pd
import pytest

from engine.central.allocation_policy import AllocationParams
from engine.central.backtester import (
    BacktestResult,
    _initialize_record_from_entry,
    _process_exits,
)
from engine.central.entity_loader import EntityRecord, load_entities_from_stage3_dirs
from engine.central.ledger import EntityPositionLedger
from engine.central.parameters_adapter import (
    build_parameters_from_stage3_row,
    join_stage3_rows_by_rulebook_hash,
    load_asset_meta_for_ticker,
    load_final_rulebooks,
    load_stage3_catalog,
)
from engine.central.policy_search import EvalPeriod, SearchSettings, run_policy_search
from engine.central.search_space import SearchSpace
from engine.central.signal_collector import CacheOnlyDataProvider
from engine.central.sim_broker import SimBroker
from engine.core.indicators import calc_indicators
from engine.live.broker.base import OrderSide
from engine.strategies.rulebook import Rulebook


class MemoryProvider:
    def __init__(self, frames):
        self.frames = {k.upper(): v.copy() for k, v in frames.items()}

    def load_price_df(self, ticker):
        return self.frames[str(ticker).upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker):
        return {}


class RejectAAASellBroker(SimBroker):
    def place_sell(self, ticker, shares, order_type=None, price=0.0, client_order_id=""):
        ticker_u = str(ticker or "").upper()
        if ticker_u == "AAA":
            fill_price, fill_date = self.execution_price(ticker_u, OrderSide.SELL)
            return self._rejected_order(
                ticker_u,
                OrderSide.SELL,
                shares,
                order_type,
                price or fill_price,
                client_order_id,
                fill_date,
                "forced test sell reject",
            )
        return super().place_sell(ticker, shares, order_type, price, client_order_id)


def test_policy_search_uses_fresh_run_subdir_when_ledger_root_reused(tmp_path):
    entities = [_entity("AAA", "hashaaa111111")]
    frames = {"AAA": _price_df()}
    periods = [EvalPeriod("p1", "2025-03-03", "2025-03-14")]
    space = SearchSpace(
        max_positions=[1],
        confidence_weight=[0.3],
        signal_strength_weight=[0.3],
        min_confidence=[0.0],
        confidence_metric=["expectancy"],
        position_sizing=["equal"],
    )
    ledger_root = tmp_path / "policy_ledgers"
    kwargs = dict(
        entities=entities,
        eval_periods=periods,
        space=space,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1, ledger_root=str(ledger_root)),
        data_provider_factory=lambda: MemoryProvider(frames),
    )

    first = run_policy_search(**kwargs).to_dict()
    second = run_policy_search(**kwargs).to_dict()

    assert first == second
    run_dirs = sorted(path for path in ledger_root.iterdir() if path.is_dir())
    assert len(run_dirs) == 2
    assert all(path.name.startswith("run_") for path in run_dirs)
    assert list(ledger_root.glob("combo_*")) == []
    assert all(list(path.glob("combo_00001_p1")) for path in run_dirs)


def test_exit_reject_continues_other_positions_and_reconcile_stays_ok(tmp_path):
    frames = {"AAA": _exit_df(), "BBB": _exit_df()}
    provider = MemoryProvider(frames)
    entities = [_entity("AAA", "hashaaa111111", sell_omen=True), _entity("BBB", "hashbbb222222", sell_omen=True)]
    rb_by_entity = {entity.entity_id: Rulebook.from_dict(entity.rulebook) for entity in entities}
    entity_by_id = {entity.entity_id: entity for entity in entities}
    ledger = EntityPositionLedger(base_dir=tmp_path / "ledger")
    broker = RejectAAASellBroker(frames, initial_cash=100_000.0)
    signal_day = frames["AAA"].index[70]
    exit_day = frames["AAA"].index[71]

    broker.set_date(signal_day)
    for entity in entities:
        intent = ledger.open_intent(entity.entity_id, entity.ticker, "buy", "entry", 10.0, "seed position")
        execution = ledger.dispatch_execution(intent.intent_id, broker, f"seed-{entity.ticker}")
        order = broker.get_order(execution.order_id)
        assert order is not None and order.status.value == "filled"
        pos = ledger.get_position(execution.position_id)
        assert pos is not None
        _initialize_record_from_entry(pos, entity, rb_by_entity[entity.entity_id], provider, order, signal_day)

    broker.set_date(exit_day)
    result = BacktestResult()
    _process_exits(exit_day, ledger, broker, provider, entity_by_id, rb_by_entity, result)

    sells = [trade for trade in result.trades if trade.side == "sell"]
    assert len(sells) == 1
    assert sells[0].ticker == "BBB"
    assert result.rejected_order_count == 1
    assert result.rejected_orders[0].ticker == "AAA"
    assert result.rejected_orders[0].side == "sell"
    assert "forced test sell reject" in result.rejected_orders[0].reason
    rec = ledger.reconcile(broker)
    assert rec["ok"] is True
    open_positions = ledger.open_positions()
    assert [(pos.ticker, round(pos.open_shares, 6)) for pos in open_positions] == [("AAA", 10.0)]
    holdings = broker.get_holdings()
    assert [(holding.ticker, round(holding.shares, 6)) for holding in holdings] == [("AAA", 10.0)]


def test_real_stage3_entity_policy_search_and_parameters_adapter_e2e(tmp_path):
    stage3_dir = Path("exp_cw_stage3_20260613_0001")
    catalog_path = stage3_dir / "stage3_profile_catalog.jsonl"
    final_path = stage3_dir / "final_rulebooks.jsonl"
    if not catalog_path.exists() or not final_path.exists():
        pytest.skip("standalone CW Stage3 artifacts are not available in this environment")

    entities = load_entities_from_stage3_dirs([stage3_dir])[:2]
    assert entities
    provider = CacheOnlyDataProvider(recompute_indicators=True, sell_omen_score_path=None)
    try:
        provider.load_price_df("CW")
    except FileNotFoundError as exc:
        pytest.skip(f"CW cache unavailable: {exc}")

    space = SearchSpace(
        max_positions=[1],
        confidence_weight=[0.3],
        signal_strength_weight=[0.7],
        min_confidence=[-999.0],
        confidence_metric=["expectancy"],
        position_sizing=["equal"],
    )
    search = run_policy_search(
        entities,
        [EvalPeriod("smoke", "2025-07-01", "2025-07-10")],
        space,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1, ledger_root=str(tmp_path / "ledgers")),
        data_provider_factory=lambda: CacheOnlyDataProvider(recompute_indicators=True, sell_omen_score_path=None),
    )
    assert search.best is not None
    assert search.best.reconcile_failures == 0

    rows = join_stage3_rows_by_rulebook_hash(load_stage3_catalog(catalog_path), load_final_rulebooks(final_path))
    row = next(row for row in rows if row["ticker"] == "CW")
    payload = build_parameters_from_stage3_row(
        row,
        asset_meta=load_asset_meta_for_ticker("CW"),
        promotion_id="stage3_e2e_test",
        source_run_dir=str(stage3_dir),
        source_run_id=stage3_dir.name,
        version="stage3_parameters_adapter_v1",
        created_at="2026-06-19T00:00:00Z",
    )
    assert sorted(payload.keys()) == ["asset_meta", "promotion", "rulebook", "saved_at", "version"]
    assert payload["asset_meta"]["ticker"] == "CW"
    assert payload["rulebook"]["ticker"] == "CW"
    assert payload["promotion"]["promotion_id"] == "stage3_e2e_test"
    assert len(payload["rulebook"]) == 88
    assert json.dumps(payload, sort_keys=True)


def _price_df(days=90, start="2025-01-01", base=100.0):
    idx = pd.bdate_range(start, periods=days)
    rows = []
    for i, _ in enumerate(idx):
        close = base + i * 0.20
        rows.append({"Open": close - 0.05, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": 1_000_000 + i})
    return calc_indicators(pd.DataFrame(rows, index=idx))


def _exit_df() -> pd.DataFrame:
    df = _price_df()
    df["sell_omen_score"] = pd.NA
    df["sell_omen_model_train_end"] = pd.NA
    df["sell_omen_score_year"] = pd.NA
    df.loc[df.index[71], "sell_omen_score"] = 0.95
    df.loc[df.index[71], "sell_omen_model_train_end"] = "2024-12-31"
    df.loc[df.index[71], "sell_omen_score_year"] = 2025
    return df


def _entity(ticker: str, rulebook_hash: str, *, sell_omen: bool = False) -> EntityRecord:
    rulebook = {
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
        "stop_loss_atr": 1000.0 if sell_omen else 2.0,
        "take_profit_atr": 1000.0 if sell_omen else 20.0,
        "trailing_atr": 1000.0 if sell_omen else 50.0,
        "max_holding_days": 99999 if sell_omen else 3,
        "exit_strategy": "hybrid",
        "position_sizing_strategy": "fixed",
        "base_position_ratio": 1.0,
        "use_news_global": False,
        "use_event_block": False,
        "use_market_entry_adjustment": False,
        "add_buy_enabled": False,
        "sell_omen_enabled": bool(sell_omen),
        "sell_omen_threshold": 0.5,
    }
    return EntityRecord(
        entity_id=f"{ticker}_{rulebook_hash[:12]}",
        ticker=ticker,
        rulebook=rulebook,
        rulebook_hash=rulebook_hash,
        validation_metrics={
            "p1": {
                "expectancy_pct": 3.0,
                "win_rate": 62.0,
                "profit_factor": 1.6,
                "trade_count": 20,
                "max_drawdown_pct": -5.0,
            }
        },
        validation_periods=[{"label": "p1", "start": "2025-03-03", "end": "2025-03-14"}],
        tags={},
        confidence=0.3,
    )
