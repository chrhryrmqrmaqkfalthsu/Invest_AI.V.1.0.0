"""Pipeline context helpers.

These helpers prepare ticker-level inputs for the new staged pipeline without
calling the legacy learn/true-WF/diagnostic orchestrators.
"""
from __future__ import annotations

from typing import Any, Iterable

import pandas as pd

from engine.adapters.factory import get_adapter
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.rulebook import default_rulebook


DEFAULT_HISTORY_YEARS = 6
DEFAULT_MARKET_HISTORY_YEARS = 7
DEFAULT_ADV_LOOKBACK_DAYS = 252
DEFAULT_ROLLING_YEARS = (2023, 2024, 2025)


def _date_series(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        return pd.to_datetime(df["date"])
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, index=df.index)
    return pd.to_datetime(pd.Series(df.index, index=df.index), errors="coerce")


def calculate_adv_usd_252d(df: pd.DataFrame, lookback_days: int = DEFAULT_ADV_LOOKBACK_DAYS) -> float:
    """Calculate recent average daily dollar volume.

    Formula: mean(Close * Volume) over the latest lookback_days valid rows.

    TODO: US tickers are already USD. Korean assets need an FX conversion layer
    before this value can be compared to the USD liquidity threshold.
    """
    if df is None or len(df) == 0 or "Close" not in df.columns or "Volume" not in df.columns:
        return 0.0
    close = pd.to_numeric(df["Close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")
    adv = pd.DataFrame({"Close": close, "Volume": volume}).dropna()
    adv = adv[(adv["Close"] > 0) & (adv["Volume"] > 0)]
    if adv.empty:
        return 0.0
    recent = adv.tail(max(1, int(lookback_days or DEFAULT_ADV_LOOKBACK_DAYS)))
    return float((recent["Close"] * recent["Volume"]).mean())


def make_year_splits(
    years: Iterable[int] = DEFAULT_ROLLING_YEARS,
    data_min: Any = None,
    data_max: Any = None,
) -> list[dict[str, Any]]:
    """Build true-WF style expanding train / annual OOS test splits.

    train = data_min through the day before the test year starts.
    test = Jan 1 through Dec 31 of the year, clamped to data boundaries.
    """
    if data_min is None or data_max is None:
        return []
    data_min_ts = pd.Timestamp(data_min).normalize()
    data_max_ts = pd.Timestamp(data_max).normalize()
    if pd.isna(data_min_ts) or pd.isna(data_max_ts) or data_max_ts < data_min_ts:
        return []

    splits: list[dict[str, Any]] = []
    for year in years:
        y = int(year)
        test_start_ts = max(pd.Timestamp(f"{y}-01-01"), data_min_ts)
        test_end_ts = min(pd.Timestamp(f"{y}-12-31"), data_max_ts)
        train_start_ts = data_min_ts
        train_end_ts = test_start_ts - pd.Timedelta(days=1)
        if train_end_ts < train_start_ts or test_end_ts < test_start_ts:
            continue
        splits.append(
            {
                "year": y,
                "train_start": train_start_ts.strftime("%Y-%m-%d"),
                "train_end": train_end_ts.strftime("%Y-%m-%d"),
                "test_start": test_start_ts.strftime("%Y-%m-%d"),
                "test_end": test_end_ts.strftime("%Y-%m-%d"),
                "train_period": [train_start_ts.strftime("%Y-%m-%d"), train_end_ts.strftime("%Y-%m-%d")],
                "test_period": [test_start_ts.strftime("%Y-%m-%d"), test_end_ts.strftime("%Y-%m-%d")],
            }
        )
    return splits


def prepare_ticker_context(ticker: str) -> dict[str, Any]:
    """Prepare all shared inputs needed by rolling validation for one ticker."""
    adapter = get_adapter(ticker)
    meta = adapter.meta
    # adapter.load_history already calls calc_indicators; do not call it again.
    df = adapter.load_history(years=DEFAULT_HISTORY_YEARS)
    dates = _date_series(df).dropna()
    data_min = pd.Timestamp(dates.min()).normalize() if len(dates) else None
    data_max = pd.Timestamp(dates.max()).normalize() if len(dates) else None

    market_history_df = get_market_history(years=DEFAULT_MARKET_HISTORY_YEARS)
    ticker_sentiment = load_ticker_sentiment(ticker)

    # TODO: move this private learner helper into pipeline context once sector
    # mapping is standardized for the new pipeline.
    from engine.learning.learner import _detect_sector_name

    sector_name = _detect_sector_name(meta.name)
    base_rulebook = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rulebook.sector_name = sector_name

    adv_usd_252d = calculate_adv_usd_252d(df)

    return {
        "ticker": ticker,
        "adapter": adapter,
        "meta": meta,
        "df": df,
        "data_min": data_min.strftime("%Y-%m-%d") if data_min is not None else None,
        "data_max": data_max.strftime("%Y-%m-%d") if data_max is not None else None,
        "market_history_df": market_history_df,
        "ticker_sentiment": ticker_sentiment,
        "sentiment_days": len(ticker_sentiment or {}),
        "sector_name": sector_name,
        "base_rulebook": base_rulebook,
        "adv_usd_252d": adv_usd_252d,
    }
