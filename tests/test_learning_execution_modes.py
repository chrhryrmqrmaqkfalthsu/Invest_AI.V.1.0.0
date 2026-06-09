import pandas as pd

from engine.learning.execution_mode_backtest import run_backtest_execution_mode
from engine.strategies.rulebook import Rulebook


def _df() -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=80, freq="B")
    close = [100.0 + i * 0.1 for i in range(80)]
    return pd.DataFrame(
        {
            "Open": [c + 1.0 for c in close],
            "High": [c + 2.0 for c in close],
            "Low": [c - 2.0 for c in close],
            "Close": close,
            "Volume": [1_000_000] * 80,
            "ATR": [1.0] * 80,
            "Aligned_bull": [1] * 80,
            "MACD_golden": [0] * 80,
            "RSI": [50] * 80,
            "BB_lower": [c - 5.0 for c in close],
            "Volume_ratio": [1.0] * 80,
        },
        index=idx,
    )


def _rb() -> Rulebook:
    return Rulebook(
        ticker="TEST",
        signal_threshold=0.5,
        weight_ma_align=1.0,
        weight_macd_golden=0.0,
        weight_rsi_zone=0.0,
        weight_bb_near_lower=0.0,
        weight_volume_surge=0.0,
        weight_news_sentiment=0.0,
        market_score_weight=0.0,
        sector_strength_weight=0.0,
        vix_sensitivity=0.0,
        stop_loss_atr=100.0,
        take_profit_atr=100.0,
        trailing_atr=100.0,
        trailing_activation_profit_pct=999.0,
        max_holding_days=5,
        base_position_ratio=1.0,
    )


def test_tplus1_entry_uses_next_open_and_records_signal_fill_dates():
    df = _df()
    res = run_backtest_execution_mode(
        _rb(),
        df,
        start_date=str(df.index[60].date()),
        end_date=str(df.index[62].date()),
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
    )

    assert res.trade_count == 1
    trade = res.trades[0]
    assert trade["entry_signal_date"] == str(df.index[60].date())
    assert trade["entry_fill_date"] == str(df.index[61].date())
    assert trade["entry_price"] == float(df.iloc[61]["Open"])
    assert trade["entry_execution_mode"] == "t_plus_1_open"
    assert trade["exit_execution_mode"] == "conservative_core"


def test_fold_end_mark_to_market_does_not_use_future_prices():
    df = _df()
    fold_end = str(df.index[62].date())
    res = run_backtest_execution_mode(
        _rb(),
        df,
        start_date=str(df.index[60].date()),
        end_date=fold_end,
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
    )

    assert res.trade_count == 1
    trade = res.trades[0]
    assert trade["exit_date"] == fold_end
    assert trade["exit_reason"] == "fold_end_mark_to_market"
    assert trade["fold_end_mark_to_market"] is True


def test_tplus1_fill_after_fold_end_is_skipped():
    df = _df()
    same_day = str(df.index[60].date())
    res = run_backtest_execution_mode(
        _rb(),
        df,
        start_date=same_day,
        end_date=same_day,
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
    )

    assert res.trade_count == 0


def test_close_mode_keeps_signal_close_entry_price():
    df = _df()
    res = run_backtest_execution_mode(
        _rb(),
        df,
        start_date=str(df.index[60].date()),
        end_date=str(df.index[62].date()),
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="close",
        exit_execution_mode="base",
        fold_exit_policy="unbounded",
    )

    assert res.trade_count == 1
    trade = res.trades[0]
    assert trade["entry_price"] == float(df.iloc[60]["Close"])
    assert trade["entry_signal_date"] == str(df.index[60].date())
    assert trade["entry_fill_date"] == str(df.index[60].date())
