import json
from pathlib import Path

import pandas as pd
import pytest

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.central.backtester import common_validation_window, run_central_backtest
from engine.central.entity_loader import ConfidenceParams, load_entities_from_catalog
from engine.central.ledger import EntityPositionLedger
from engine.central.signal_collector import CacheOnlyDataProvider, SignalCollector
from engine.central.sim_broker import FillPolicy, SimBroker
from engine.core.indicators import calc_indicators


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


def _catalog_row(ticker="AAA", rulebook_hash="abcdef1234567890", max_holding_days=3):
    return {
        "ticker": ticker,
        "rulebook_hash": rulebook_hash,
        "rulebook": _rulebook(ticker, max_holding_days=max_holding_days),
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


def test_sim_broker_rejects_insufficient_cash_without_mutating_holdings():
    df = pd.DataFrame(
        [
            {"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": 10.0},
            {"Open": 200.0, "High": 205.0, "Low": 195.0, "Close": 200.0},
        ],
        index=pd.to_datetime(["2025-01-02", "2025-01-03"]),
    )
    broker = SimBroker({"AAA": df}, initial_cash=1_000.0)
    broker.set_date("2025-01-02")
    order = broker.place_buy("AAA", 10)
    assert order.status.value == "rejected"
    assert order.filled_shares == 0
    assert broker.cash == pytest.approx(1_000.0)
    assert broker.get_holdings() == []


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


def test_cache_only_provider_recomputes_indicators_from_raw_ohlcv(tmp_path):
    cache_dir = tmp_path / "stage0" / "ohlcv_cache"
    cache_dir.mkdir(parents=True)
    df = _price_df(days=40)
    polluted = calc_indicators(df)
    polluted["MA5"] = 999999.0
    polluted.to_pickle(cache_dir / "AAA.pkl")
    provider = CacheOnlyDataProvider(cache_roots=[tmp_path], recompute_indicators=True)
    loaded = provider.load_price_df("AAA")
    expected = calc_indicators(df)
    assert loaded["MA5"].iloc[-1] == pytest.approx(expected["MA5"].iloc[-1])
    assert loaded["MA5"].iloc[-1] != 999999.0


def test_cache_only_provider_attaches_sell_omen_scores_and_guards(tmp_path):
    cache_dir = tmp_path / "stage0" / "ohlcv_cache"
    cache_dir.mkdir(parents=True)
    df = _price_df(days=20)
    df.to_pickle(cache_dir / "AAA.pkl")
    good_date = pd.Timestamp(df.index[10]).strftime("%Y-%m-%d")
    bad_date = pd.Timestamp(df.index[11]).strftime("%Y-%m-%d")
    score_path = tmp_path / "sell_omen_scores.csv"
    pd.DataFrame(
        [
            {"ticker": "AAA", "Date": good_date, "sell_omen_score": 0.77, "model_train_end": "2024-12-31", "score_year": 2025},
            {"ticker": "AAA", "Date": bad_date, "sell_omen_score": 0.99, "model_train_end": bad_date, "score_year": 2025},
        ]
    ).to_csv(score_path, index=False)
    provider = CacheOnlyDataProvider(cache_roots=[tmp_path], sell_omen_score_path=score_path)
    loaded = provider.load_price_df("AAA")
    assert loaded.loc[pd.Timestamp(good_date), "sell_omen_score"] == pytest.approx(0.77)
    assert pd.isna(loaded.loc[pd.Timestamp(bad_date), "sell_omen_score"])
    assert loaded.loc[pd.Timestamp(good_date), "sell_omen_model_train_end"] == "2024-12-31"
    assert loaded.loc[pd.Timestamp(good_date), "sell_omen_score_year"] == 2025
    assert provider.sell_omen_guard_violations == 1
    assert provider.sell_omen_loaded_rows == 2
    assert provider.sell_omen_missing_tickers == set()


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
    result1 = run_central_backtest(entities, start=df.index[70].strftime("%Y-%m-%d"), end=df.index[80].strftime("%Y-%m-%d"), alloc_params=params, data_provider=provider, ledger_dir=tmp_path / "ledger1")
    result2 = run_central_backtest(entities, start=df.index[70].strftime("%Y-%m-%d"), end=df.index[80].strftime("%Y-%m-%d"), alloc_params=params, data_provider=provider, ledger_dir=tmp_path / "ledger2")
    assert result1.reconcile_failures == []
    assert len(result1.equity_curve) > 0
    assert result1.to_dict()["equity_curve"] == result2.to_dict()["equity_curve"]
    assert result1.final_equity == pytest.approx(result2.final_equity)
    assert sum(result1.per_entity_pnl.values()) == pytest.approx(result1.final_equity - params.total_capital)


def test_backtester_gap_change_does_not_change_signal_day_shares_or_entry_risk(tmp_path):
    base = _gap_regression_df(next_open=101.0, next_high=102.0, next_low=100.0, next_close=101.0)
    spiky = _gap_regression_df(next_open=101.0, next_high=300.0, next_low=1.0, next_close=101.0)
    result_a, pos_a = _run_single_day_gap_case(tmp_path / "a", base, total_capital=20_000.0)
    result_b, pos_b = _run_single_day_gap_case(tmp_path / "b", spiky, total_capital=20_000.0)
    assert result_a.rejected_order_count == 0
    assert result_b.rejected_order_count == 0
    assert result_a.trades[0].shares == pytest.approx(result_b.trades[0].shares)
    assert pos_a.atr_at_entry == pytest.approx(pos_b.atr_at_entry)
    assert pos_a.stop_price == pytest.approx(pos_b.stop_price)
    assert pos_a.target_price == pytest.approx(pos_b.target_price)
    assert pos_a.trailing_distance == pytest.approx(pos_b.trailing_distance)


def test_backtester_rejects_buy_when_next_open_gap_exceeds_cash(tmp_path):
    df = _gap_regression_df(next_open=200.0, next_high=205.0, next_low=195.0, next_close=200.0)
    result, ledger = _run_single_day_gap_case(tmp_path, df, total_capital=10_000.0, return_ledger=True)
    assert result.trades == []
    assert result.rejected_order_count == 1
    assert "insufficient simulated cash" in result.rejected_orders[0].reason
    assert ledger.open_positions() == []
    assert result.reconcile_failures == []


def test_backtester_cash_buffer_caps_d_close_sized_shares(tmp_path):
    df = _gap_regression_df(next_open=100.0, next_high=101.0, next_low=99.0, next_close=100.0)
    result, pos = _run_single_day_gap_case(tmp_path, df, total_capital=10_000.0, cash_buffer_ratio=0.98)
    assert result.rejected_order_count == 0
    assert result.trades[0].shares == pytest.approx(98.0)
    assert pos.open_shares == pytest.approx(98.0)


def test_sell_omen_disabled_ignores_high_score(tmp_path):
    df = _sell_omen_df({1: 0.95})
    result = _run_sell_omen_case(tmp_path, df, sell_omen_enabled=False)
    assert _sell_trades(result) == []
    assert result.reconcile_failures == []


def test_sell_omen_enabled_exits_when_score_crosses_threshold(tmp_path):
    df = _sell_omen_df({1: 0.95})
    result = _run_sell_omen_case(tmp_path, df, sell_omen_enabled=True, threshold=0.5)
    sells = _sell_trades(result)
    assert len(sells) == 1
    assert sells[0].reason == "sell_omen"
    assert result.reconcile_failures == []


def test_sell_omen_missing_score_does_not_exit(tmp_path):
    df = _sell_omen_df({})
    result = _run_sell_omen_case(tmp_path, df, sell_omen_enabled=True, threshold=0.5)
    assert _sell_trades(result) == []
    assert result.reconcile_failures == []


def test_sell_omen_guarded_score_row_is_ignored(tmp_path):
    df = _sell_omen_df({})
    root = tmp_path / "guarded"
    cache_dir = root / "stage0" / "ohlcv_cache"
    cache_dir.mkdir(parents=True)
    raw = df[["Open", "High", "Low", "Close", "Volume"]]
    raw.to_pickle(cache_dir / "AAA.pkl")
    exit_date = pd.Timestamp(df.index[71]).strftime("%Y-%m-%d")
    score_path = root / "sell_omen_scores.csv"
    pd.DataFrame(
        [{"ticker": "AAA", "Date": exit_date, "sell_omen_score": 0.95, "model_train_end": exit_date, "score_year": 2025}]
    ).to_csv(score_path, index=False)
    provider = CacheOnlyDataProvider(cache_roots=[root], sell_omen_score_path=score_path)
    result = _run_sell_omen_case(tmp_path, None, sell_omen_enabled=True, threshold=0.5, provider=provider)
    assert _sell_trades(result) == []
    assert provider.sell_omen_guard_violations == 1
    assert result.reconcile_failures == []


def test_sell_omen_d_plus_one_score_does_not_affect_d_day_exit(tmp_path):
    df = _sell_omen_df({1: 0.1, 2: 0.99})
    result = _run_sell_omen_case(tmp_path, df, sell_omen_enabled=True, threshold=0.5)
    assert _sell_trades(result) == []
    assert result.reconcile_failures == []


def test_common_validation_window_recent_1y(tmp_path):
    catalog = tmp_path / "stage3_profile_catalog.jsonl"
    catalog.write_text(json.dumps(_catalog_row()) + "\n", encoding="utf-8")
    entities = load_entities_from_catalog(catalog)
    assert common_validation_window(entities) == ("2025-01-01", "2025-03-31")


def _write_catalog_tmp(root: Path, row: dict) -> Path:
    path = root / "central_test_stage3_profile_catalog.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return path


def _gap_regression_df(next_open: float, next_high: float, next_low: float, next_close: float) -> pd.DataFrame:
    df = _price_df(days=90, base=100.0)
    signal_idx = df.index[70]
    next_idx = df.index[71]
    df.loc[signal_idx, ["Open", "High", "Low", "Close"]] = [100.0, 101.0, 99.0, 100.0]
    df.loc[next_idx, ["Open", "High", "Low", "Close"]] = [next_open, next_high, next_low, next_close]
    return calc_indicators(df)


def _run_single_day_gap_case(root: Path, df: pd.DataFrame, *, total_capital: float, cash_buffer_ratio: float = 0.98, return_ledger: bool = False):
    catalog = root / "stage3_profile_catalog.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps(_catalog_row("AAA", "gap1112223334444", max_holding_days=99)) + "\n", encoding="utf-8")
    entities = load_entities_from_catalog(catalog)
    provider = MemoryProvider({"AAA": df})
    signal_day = df.index[70].strftime("%Y-%m-%d")
    ledger_dir = root / "ledger"
    params = AllocationParams(max_positions=1, min_confidence=0.0, total_capital=total_capital, per_ticker_exposure_cap=1.0, position_sizing="equal", cash_buffer_ratio=cash_buffer_ratio)
    result = run_central_backtest(entities, start=signal_day, end=signal_day, alloc_params=params, data_provider=provider, ledger_dir=ledger_dir)
    ledger = EntityPositionLedger(base_dir=ledger_dir)
    if return_ledger:
        return result, ledger
    positions = ledger.open_positions()
    assert len(positions) == 1
    return result, positions[0]


def _sell_omen_rulebook(ticker="AAA", *, enabled=True, threshold=0.5):
    rb = _rulebook(ticker, max_holding_days=99999)
    rb.update(
        {
            "stop_loss_atr": 1000.0,
            "take_profit_atr": 1000.0,
            "trailing_atr": 1000.0,
            "sell_omen_enabled": bool(enabled),
            "sell_omen_threshold": float(threshold),
        }
    )
    return rb


def _sell_omen_catalog_row(ticker="AAA", rulebook_hash="omen111222333", *, enabled=True, threshold=0.5):
    row = _catalog_row(ticker, rulebook_hash, max_holding_days=99999)
    row["rulebook"] = _sell_omen_rulebook(ticker, enabled=enabled, threshold=threshold)
    return row


def _sell_omen_df(score_by_offset: dict[int, float]) -> pd.DataFrame:
    raw = _price_df(days=90, base=100.0)
    out = calc_indicators(raw)
    out["sell_omen_score"] = pd.NA
    out["sell_omen_model_train_end"] = pd.NA
    out["sell_omen_score_year"] = pd.NA
    signal_idx = 70
    for offset, score in score_by_offset.items():
        idx = signal_idx + int(offset)
        out.loc[out.index[idx], "sell_omen_score"] = float(score)
        out.loc[out.index[idx], "sell_omen_model_train_end"] = "2024-12-31"
        out.loc[out.index[idx], "sell_omen_score_year"] = 2025
    return out


def _run_sell_omen_case(tmp_path: Path, df: pd.DataFrame | None, *, sell_omen_enabled: bool, threshold: float = 0.5, provider=None):
    root = tmp_path / f"sell_omen_{len(list(tmp_path.iterdir())) if tmp_path.exists() else 0}"
    root.mkdir(parents=True, exist_ok=True)
    catalog = root / "stage3_profile_catalog.jsonl"
    catalog.write_text(json.dumps(_sell_omen_catalog_row(enabled=sell_omen_enabled, threshold=threshold)) + "\n", encoding="utf-8")
    entities = load_entities_from_catalog(catalog)
    provider = provider or MemoryProvider({"AAA": df})
    price_df = df if df is not None else provider.load_price_df("AAA")
    start = price_df.index[70].strftime("%Y-%m-%d")
    end = price_df.index[71].strftime("%Y-%m-%d")
    params = AllocationParams(max_positions=1, min_confidence=0.0, total_capital=10_000.0, per_ticker_exposure_cap=1.0, position_sizing="equal")
    return run_central_backtest(
        entities,
        start=start,
        end=end,
        alloc_params=params,
        data_provider=provider,
        ledger_dir=root / "ledger",
    )


def _sell_trades(result):
    return [t for t in result.trades if t.side == "sell"]
