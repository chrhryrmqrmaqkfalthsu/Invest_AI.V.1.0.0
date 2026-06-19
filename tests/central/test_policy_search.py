import pandas as pd
import pytest

from engine.central.entity_loader import EntityRecord
from engine.central.policy_search import (
    EvalPeriod,
    SearchSettings,
    apply_confidence_metric,
    robust_score_from_returns,
    run_policy_search,
)
from engine.central.search_space import SearchSpace
from engine.core.indicators import calc_indicators


class MemoryProvider:
    def __init__(self, frames):
        self.frames = {k.upper(): v.copy() for k, v in frames.items()}

    def load_price_df(self, ticker):
        return self.frames[ticker.upper()]

    def load_market_history(self):
        return None

    def load_ticker_sentiment(self, ticker):
        return {}


def test_search_space_grid_count_and_rows():
    space = SearchSpace(
        max_positions=[1, 2],
        confidence_weight=[0.3],
        signal_strength_weight=[0.7],
        min_confidence=[0.0],
        confidence_metric=["expectancy", "win_rate"],
        position_sizing=["equal"],
    )
    rows = list(space.grid())
    assert space.count() == 4
    assert len(rows) == 4
    assert rows[0]["confidence_metric"] == "expectancy"
    assert rows[-1]["max_positions"] == 2


def test_confidence_metric_changes_entity_confidence():
    entity = _entity("AAA", "hashaaa111111", expectancy=5.0, win_rate=62.0, profit_factor=1.8)
    exp_entity = apply_confidence_metric([entity], "expectancy")[0]
    wr_entity = apply_confidence_metric([entity], "win_rate")[0]
    pf_entity = apply_confidence_metric([entity], "profit_factor")[0]
    assert exp_entity.confidence == pytest.approx(0.5)
    assert wr_entity.confidence == pytest.approx(0.62)
    assert pf_entity.confidence == pytest.approx(0.8)


def test_robust_score_prefers_balanced_over_one_period_jackpot():
    balanced = robust_score_from_returns([1.0, 1.0, 1.0], max_drawdown_pct=0.0, trades=30, min_trades_for_full_score=10)
    jackpot = robust_score_from_returns([10.0, 0.0, 0.0], max_drawdown_pct=0.0, trades=30, min_trades_for_full_score=10)
    assert balanced > jackpot


def test_robust_score_penalizes_low_trade_count():
    full = robust_score_from_returns([2.0, 2.0], max_drawdown_pct=0.0, trades=10, min_trades_for_full_score=10)
    thin = robust_score_from_returns([2.0, 2.0], max_drawdown_pct=0.0, trades=1, min_trades_for_full_score=10)
    assert full > thin


def test_policy_search_grid_end_to_end_and_reconcile(tmp_path):
    entities = [
        _entity("AAA", "hashaaa111111", expectancy=3.0, win_rate=62.0, profit_factor=1.6),
        _entity("BBB", "hashbbb222222", expectancy=1.0, win_rate=53.0, profit_factor=1.2),
    ]
    frames = {"AAA": _price_df(base=100.0), "BBB": _price_df(base=80.0)}
    periods = [
        EvalPeriod("p1", "2025-03-03", "2025-03-14"),
        EvalPeriod("p2", "2025-03-17", "2025-03-28"),
    ]
    space = SearchSpace(
        max_positions=[1, 2],
        confidence_weight=[0.3],
        signal_strength_weight=[0.3],
        min_confidence=[0.0],
        confidence_metric=["expectancy", "win_rate"],
        position_sizing=["equal"],
    )
    result = run_policy_search(
        entities,
        periods,
        space,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1, ledger_root=str(tmp_path / "ledgers")),
        data_provider_factory=lambda: MemoryProvider(frames),
    )
    assert result.evaluated_count == 4
    assert result.period_count == 2
    assert result.best is not None
    assert result.best.rank == 1
    assert result.best.trades > 0
    assert all(c.reconcile_failures == 0 for c in result.candidates)
    scores = [c.robust_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)


def test_policy_search_is_deterministic(tmp_path):
    entities = [_entity("AAA", "hashaaa111111", expectancy=3.0, win_rate=62.0, profit_factor=1.6)]
    frames = {"AAA": _price_df(base=100.0)}
    periods = [EvalPeriod("p1", "2025-03-03", "2025-03-14")]
    space = SearchSpace(
        max_positions=[1],
        confidence_weight=[0.3, 0.7],
        signal_strength_weight=[0.3],
        min_confidence=[0.0],
        confidence_metric=["expectancy"],
        position_sizing=["equal"],
    )
    kwargs = dict(
        entities=entities,
        eval_periods=periods,
        space=space,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1),
        data_provider_factory=lambda: MemoryProvider(frames),
    )
    first = run_policy_search(**kwargs).to_dict()
    second = run_policy_search(**kwargs).to_dict()
    assert first == second


def test_policy_search_random_sample_is_seeded(tmp_path):
    entities = [_entity("AAA", "hashaaa111111", expectancy=3.0, win_rate=62.0, profit_factor=1.6)]
    frames = {"AAA": _price_df(base=100.0)}
    periods = [EvalPeriod("p1", "2025-03-03", "2025-03-14")]
    space = SearchSpace(
        max_positions=[1, 2, 3],
        confidence_weight=[0.3, 0.5],
        signal_strength_weight=[0.3],
        min_confidence=[0.0],
        confidence_metric=["expectancy", "win_rate"],
        position_sizing=["equal"],
    )
    first = run_policy_search(
        entities,
        periods,
        space,
        method="random",
        n_random=3,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1, random_seed=7),
        data_provider_factory=lambda: MemoryProvider(frames),
    )
    second = run_policy_search(
        entities,
        periods,
        space,
        method="random",
        n_random=3,
        settings=SearchSettings(total_capital=10_000.0, min_trades_for_full_score=1, random_seed=7),
        data_provider_factory=lambda: MemoryProvider(frames),
    )
    assert first.evaluated_count == 3
    assert [c.params for c in first.candidates] == [c.params for c in second.candidates]


def _price_df(days=90, start="2025-01-01", base=100.0):
    idx = pd.bdate_range(start, periods=days)
    rows = []
    for i, _ in enumerate(idx):
        close = base + i * 0.20
        rows.append({"Open": close - 0.05, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": 1_000_000 + i})
    return calc_indicators(pd.DataFrame(rows, index=idx))


def _entity(ticker: str, rulebook_hash: str, *, expectancy: float, win_rate: float, profit_factor: float) -> EntityRecord:
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
        validation_metrics={
            "p1": {
                "expectancy_pct": expectancy,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "trade_count": 20,
                "max_drawdown_pct": -5.0,
            }
        },
        validation_periods=[{"label": "p1", "start": "2025-03-03", "end": "2025-03-14"}],
        tags={},
        confidence=expectancy / 10.0,
    )
