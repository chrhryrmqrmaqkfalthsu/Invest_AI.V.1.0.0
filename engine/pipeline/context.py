"""Pipeline context helpers.

These helpers prepare ticker-level inputs for the new staged pipeline without
calling the legacy learn/true-WF/diagnostic orchestrators.
"""
from __future__ import annotations

from pathlib import Path
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
DEFAULT_SELL_OMEN_SCORE_TABLE = Path("data/_system/ml_sell_omen/sell_omen_scores.csv")
_SELL_OMEN_SCORE_CACHE: dict[str, pd.DataFrame] = {}


def _date_series(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        return pd.to_datetime(df["date"])
    if "Date" in df.columns:
        return pd.to_datetime(df["Date"])
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, index=df.index)
    return pd.to_datetime(pd.Series(df.index, index=df.index), errors="coerce")


def _valid_ratio(series: pd.Series | None, *, positive: bool = False, non_negative: bool = False) -> float:
    if series is None or len(series) == 0:
        return 0.0
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.notna()
    if positive:
        valid = valid & (numeric > 0)
    if non_negative:
        valid = valid & (numeric >= 0)
    return float(valid.sum() / len(numeric)) if len(numeric) else 0.0


def _invalid_price_volume_ratio(df: pd.DataFrame) -> float:
    if df is None or len(df) == 0 or "Close" not in df.columns or "Volume" not in df.columns:
        return 1.0
    close = pd.to_numeric(df["Close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")
    invalid = close.isna() | volume.isna() | (close <= 0) | (volume <= 0)
    return float(invalid.sum() / len(df)) if len(df) else 1.0


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


def _load_sell_omen_score_table(path: str | Path = DEFAULT_SELL_OMEN_SCORE_TABLE) -> pd.DataFrame:
    """Load the walk-forward sell-omen score table once per process.

    The score table is intentionally optional. If it does not exist, the
    pipeline behaves exactly as before and sell_omen rules simply do not fire.
    """
    p = Path(path)
    key = str(p.resolve()) if p.exists() else str(p)
    cached = _SELL_OMEN_SCORE_CACHE.get(key)
    if cached is not None:
        return cached
    required = {"ticker", "Date", "sell_omen_score"}
    if not p.exists():
        empty = pd.DataFrame(columns=["ticker", "Date", "sell_omen_score", "model_train_end", "score_year"])
        _SELL_OMEN_SCORE_CACHE[key] = empty
        return empty
    try:
        df = pd.read_csv(p)
    except Exception:
        empty = pd.DataFrame(columns=["ticker", "Date", "sell_omen_score", "model_train_end", "score_year"])
        _SELL_OMEN_SCORE_CACHE[key] = empty
        return empty
    if not required.issubset(df.columns):
        empty = pd.DataFrame(columns=["ticker", "Date", "sell_omen_score", "model_train_end", "score_year"])
        _SELL_OMEN_SCORE_CACHE[key] = empty
        return empty
    out = df.copy()
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["sell_omen_score"] = pd.to_numeric(out["sell_omen_score"], errors="coerce")
    out = out.dropna(subset=["ticker", "Date", "sell_omen_score"])
    out = out[(out["sell_omen_score"] >= 0.0) & (out["sell_omen_score"] <= 1.0)]
    keep = [c for c in ["ticker", "Date", "sell_omen_score", "model_train_end", "score_year"] if c in out.columns]
    out = out[keep].drop_duplicates(["ticker", "Date"], keep="last").reset_index(drop=True)
    _SELL_OMEN_SCORE_CACHE[key] = out
    return out


def attach_sell_omen_scores(
    df: pd.DataFrame,
    ticker: str,
    score_table_path: str | Path = DEFAULT_SELL_OMEN_SCORE_TABLE,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Left-join walk-forward sell_omen_score into one ticker OHLCV frame.

    Merge key:
        ticker + normalized date.

    Rows without score, typically 2020-2023 training years or missing tickers,
    are left as NaN. ``simulate_exit`` treats NaN as no score, so
    sell_omen_enabled rules do not fire before OOS score coverage starts.
    """
    if df is None or len(df) == 0:
        return df, {"available": False, "matched_rows": 0, "coverage": 0.0, "reason": "empty_df"}

    scores = _load_sell_omen_score_table(score_table_path)
    if scores.empty:
        return df, {"available": False, "matched_rows": 0, "coverage": 0.0, "reason": "score_table_missing_or_empty"}

    ticker_norm = str(ticker or "").upper().strip()
    ticker_scores = scores[scores["ticker"] == ticker_norm].copy()
    if ticker_scores.empty:
        return df, {"available": True, "matched_rows": 0, "coverage": 0.0, "reason": "ticker_not_in_score_table"}

    out = df.copy()
    original_index = out.index
    original_columns = set(out.columns)
    for col in ("sell_omen_score", "sell_omen_model_train_end", "sell_omen_score_year"):
        if col in out.columns:
            out = out.drop(columns=[col])

    out["_sell_omen_merge_date"] = _date_series(out).dt.strftime("%Y-%m-%d")
    right = ticker_scores.rename(
        columns={
            "Date": "_sell_omen_merge_date",
            "model_train_end": "sell_omen_model_train_end",
            "score_year": "sell_omen_score_year",
        }
    )[[c for c in ["_sell_omen_merge_date", "sell_omen_score", "sell_omen_model_train_end", "sell_omen_score_year"] if c in ticker_scores.rename(columns={"Date": "_sell_omen_merge_date", "model_train_end": "sell_omen_model_train_end", "score_year": "sell_omen_score_year"}).columns]]
    merged = out.merge(right, on="_sell_omen_merge_date", how="left", sort=False)
    merged.index = original_index
    merged = merged.drop(columns=["_sell_omen_merge_date"])

    matched = int(pd.to_numeric(merged.get("sell_omen_score"), errors="coerce").notna().sum())
    score_series = pd.to_numeric(merged.get("sell_omen_score"), errors="coerce")
    info = {
        "available": True,
        "matched_rows": matched,
        "coverage": float(matched / len(merged)) if len(merged) else 0.0,
        "score_min": float(score_series.min()) if matched else None,
        "score_max": float(score_series.max()) if matched else None,
        "score_table_path": str(score_table_path),
        "added_columns": sorted([c for c in set(merged.columns) - original_columns if c.startswith("sell_omen")]),
    }
    return merged, info


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
    """Prepare all shared inputs needed by screening/rolling validation for one ticker."""
    adapter = get_adapter(ticker)
    meta = adapter.meta
    # adapter.load_history already calls calc_indicators; do not call it again.
    df = adapter.load_history(years=DEFAULT_HISTORY_YEARS)
    df, sell_omen_info = attach_sell_omen_scores(df, ticker)
    dates = _date_series(df).dropna()
    data_min = pd.Timestamp(dates.min()).normalize() if len(dates) else None
    data_max = pd.Timestamp(dates.max()).normalize() if len(dates) else None
    data_min_str = data_min.strftime("%Y-%m-%d") if data_min is not None else None
    data_max_str = data_max.strftime("%Y-%m-%d") if data_max is not None else None
    splits = make_year_splits(DEFAULT_ROLLING_YEARS, data_min_str, data_max_str)

    market_history_df = get_market_history(years=DEFAULT_MARKET_HISTORY_YEARS)
    ticker_sentiment = load_ticker_sentiment(ticker)

    # TODO: move this private learner helper into pipeline context once sector
    # mapping is standardized for the new pipeline.
    from engine.learning.learner import _detect_sector_name

    sector_name = _detect_sector_name(meta.name)
    base_rulebook = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rulebook.sector_name = sector_name

    adv_usd_252d = calculate_adv_usd_252d(df)
    close_series = df["Close"] if df is not None and "Close" in df.columns else None
    volume_series = df["Volume"] if df is not None and "Volume" in df.columns else None

    return {
        "ticker": ticker,
        "adapter": adapter,
        "meta": meta,
        "df": df,
        "rows": int(len(df) if df is not None else 0),
        "data_min": data_min_str,
        "data_max": data_max_str,
        "data_start": data_min_str,
        "data_end": data_max_str,
        "valid_close_ratio": _valid_ratio(close_series, positive=True),
        "valid_volume_ratio": _valid_ratio(volume_series, non_negative=True),
        "invalid_price_volume_ratio": _invalid_price_volume_ratio(df),
        "splits": splits,
        "split_count": len(splits),
        "market_history_df": market_history_df,
        "ticker_sentiment": ticker_sentiment,
        "sentiment_days": len(ticker_sentiment or {}),
        "sector_name": sector_name,
        "base_rulebook": base_rulebook,
        "adv_usd_252d": adv_usd_252d,
        "sell_omen_score": sell_omen_info,
    }
