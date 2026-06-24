import pandas as pd
import pytest

from engine.central.allocation_policy import AllocationParams
from engine.central.backtester import _turnover_score_for_entity, run_central_backtest
from engine.central.entity_loader import EntityRecord
from engine.central.signal_collector import SignalCollector, SignalSnapshot


class MemoryProvider:
    def __init__(self, frames):
        self.frames = {k.upper(): v for k, v in frames.items()}

    def load_price_df(self, ticker):
        return self.frames[ticker.upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker):
        return {}


def _price_df():
    idx = pd.bdate_range("2025-01-02", periods=4)
    rows = []
    for i, _ in enumerate(idx):
        close = 100.0 + i
        rows.append({"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1_000_000, "ATR": 2.0})
    return pd.DataFrame(rows, index=idx)


def _rulebook(ticker="AAA"):
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
        "stop_loss_atr": 20.0,
        "take_profit_atr": 20.0,
        "trailing_atr": 50.0,
        "max_holding_days": 99,
        "exit_strategy": "hybrid",
        "position_sizing_strategy": "fixed",
        "base_position_ratio": 1.0,
        "use_news_global": False,
        "use_event_block": False,
        "use_market_entry_adjustment": False,
        "add_buy_enabled": False,
    }


def _entity(entity_id, *, ticker="AAA", confidence=1.0, trade_count=10):
    return EntityRecord(
        entity_id=entity_id,
        ticker=ticker,
        rulebook=_rulebook(ticker),
        rulebook_hash=entity_id[-12:].ljust(12, "0"),
        validation_metrics={},
        validation_periods=[],
        tags={"avg_realized_pnl_pct": 2.0, "avg_holding_days": 4.0, "trade_count": trade_count},
        confidence=confidence,
    )


def _signal(entity, date, *, strength=1.0):
    return SignalSnapshot(
        entity_id=entity.entity_id,
        ticker=entity.ticker,
        date=date,
        should_buy=True,
        score=strength,
        raw_score=strength,
        threshold=1.0,
        strength=strength,
        components={},
        reasons=[],
        market_adjustment=0.0,
        price=100.0,
        confidence=entity.confidence,
    )


def test_turnover_score_respects_min_trades_parameter():
    entity = _entity("AAA_low_sample", trade_count=9)

    assert _turnover_score_for_entity(entity, min_trades=10) is None
    assert _turnover_score_for_entity(entity, min_trades=5) == pytest.approx(0.5)


def test_backtester_signal_cache_and_entity_mode_allow_same_ticker_positions(monkeypatch, tmp_path):
    df = _price_df()
    signal_day = df.index[0].strftime("%Y-%m-%d")
    e1 = _entity("AAA_entity_one", confidence=3.0)
    e2 = _entity("AAA_entity_two", confidence=2.0)
    provider = MemoryProvider({"AAA": df})
    signal_cache = {signal_day: [_signal(e1, signal_day, strength=1.0), _signal(e2, signal_day, strength=1.0)]}

    def fail_collect(self, entities, date):
        raise AssertionError("SignalCollector.collect should not be called when signal_cache is prepopulated")

    monkeypatch.setattr(SignalCollector, "collect", fail_collect)
    params = AllocationParams(
        max_positions=2,
        total_capital=10_000.0,
        per_ticker_exposure_cap=1.0,
        position_sizing="equal",
        min_confidence=0.0,
        allow_same_ticker_entities=True,
    )

    result = run_central_backtest(
        [e1, e2],
        start=signal_day,
        end=signal_day,
        alloc_params=params,
        data_provider=provider,
        ledger_dir=tmp_path / "ledger",
        signal_cache=signal_cache,
    )

    buys = [trade for trade in result.trades if trade.side == "buy"]
    assert [trade.entity_id for trade in buys] == ["AAA_entity_one", "AAA_entity_two"]
    assert result.diagnostics["max_open_entity_positions"] == 2
    assert result.diagnostics["ticker_entity_count_distribution"] == {2: 1}
