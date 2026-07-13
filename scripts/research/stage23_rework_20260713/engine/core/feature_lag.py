"""Feature lag helpers for backtest-safe news/event lookups.

Policy
------
D-day trading signals may only use news/event features available through
D-1 or earlier. Raw aggregation files keep their original event/news dates;
this module applies the lag at consumption time.

Metadata connection point
-------------------------
When build_metadata(...) is attached to outputs, pass FEATURE_LAG_METADATA so
artifacts record the actual applied values:
    {"ticker_sentiment_days": 1, "market_events_days": 1, "max_age_days": 7}
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, timedelta
from typing import Any

DEFAULT_LAG_DAYS = 1
DEFAULT_MAX_AGE_DAYS = 7
FEATURE_LAG_METADATA = {
    "ticker_sentiment_days": DEFAULT_LAG_DAYS,
    "market_events_days": DEFAULT_LAG_DAYS,
    "max_age_days": DEFAULT_MAX_AGE_DAYS,
}

# id(dict) -> (len(dict), tuple(sorted_date_objects), tuple(sorted_keys))
_DAILY_DICT_KEY_CACHE: dict[int, tuple[int, tuple[date, ...], tuple[str, ...]]] = {}


def _to_date(value: Any) -> date | None:
    """Best-effort conversion to a calendar date. Invalid input returns None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        # pandas/numpy Timestamp-like objects usually support to_pydatetime().
        if hasattr(value, "to_pydatetime"):
            return value.to_pydatetime().date()
    except Exception:
        pass
    try:
        s = str(value)
        if not s or s.lower() in {"nat", "nan", "none"}:
            return None
        # Keep date part for strings such as '2026-06-04 15:30:00'.
        return datetime.fromisoformat(s[:10]).date()
    except Exception:
        return None


def lagged_date_key(trade_date: Any, lag_days: int = DEFAULT_LAG_DAYS) -> str:
    """Return YYYY-MM-DD key for trade_date minus lag_days calendar days.

    Invalid input returns an empty string instead of raising.
    """
    d = _to_date(trade_date)
    if d is None:
        return ""
    try:
        lag = max(0, int(lag_days or 0))
    except Exception:
        lag = DEFAULT_LAG_DAYS
    return (d - timedelta(days=lag)).strftime("%Y-%m-%d")


def _daily_dict_index(daily_dict: dict) -> tuple[tuple[date, ...], tuple[str, ...]]:
    if not isinstance(daily_dict, dict) or not daily_dict:
        return (), ()
    cache_key = id(daily_dict)
    cached = _DAILY_DICT_KEY_CACHE.get(cache_key)
    if cached is not None and cached[0] == len(daily_dict):
        return cached[1], cached[2]

    pairs: list[tuple[date, str]] = []
    for key in daily_dict.keys():
        d = _to_date(key)
        if d is not None:
            pairs.append((d, str(key)[:10]))
    pairs.sort(key=lambda x: x[0])
    dates = tuple(d for d, _ in pairs)
    keys = tuple(k for _, k in pairs)
    _DAILY_DICT_KEY_CACHE[cache_key] = (len(daily_dict), dates, keys)
    return dates, keys


def lookup_lagged_daily_dict(
    daily_dict: dict | None,
    trade_date: Any,
    lag_days: int = DEFAULT_LAG_DAYS,
    max_age_days: int | None = DEFAULT_MAX_AGE_DAYS,
) -> dict:
    """Lookup the newest daily feature row at or before trade_date - lag_days.

    Args:
        daily_dict: {"YYYY-MM-DD": {...}} style feature map.
        trade_date: signal/trade date D.
        lag_days: D-day signal can only see cutoff = D - lag_days.
        max_age_days: if the selected row is older than this, return {}.
            None disables staleness filtering.

    Returns:
        The selected row dict, or {} for missing/stale/invalid inputs.
    """
    if not isinstance(daily_dict, dict) or not daily_dict:
        return {}

    cutoff = _to_date(lagged_date_key(trade_date, lag_days))
    if cutoff is None:
        return {}

    dates, keys = _daily_dict_index(daily_dict)
    if not dates:
        return {}

    pos = bisect_right(dates, cutoff) - 1
    if pos < 0:
        return {}

    selected_date = dates[pos]
    if max_age_days is not None:
        try:
            max_age = int(max_age_days)
            if max_age >= 0 and (cutoff - selected_date).days > max_age:
                return {}
        except Exception:
            if (cutoff - selected_date).days > DEFAULT_MAX_AGE_DAYS:
                return {}

    row = daily_dict.get(keys[pos], {})
    return row if isinstance(row, dict) else {}


def lookup_market_at_lagged(
    history_df: Any,
    trade_date: Any,
    lag_days: int = DEFAULT_LAG_DAYS,
) -> dict:
    """Lagged wrapper for engine.market.context.lookup_market_at.

    Market price/trend columns and v2 event columns are both read at cutoff
    date. This keeps backtest signal context strictly D-1 or earlier.
    """
    cutoff_key = lagged_date_key(trade_date, lag_days)
    if not cutoff_key:
        return {}
    try:
        from engine.market.context import lookup_market_at

        row = lookup_market_at(history_df, cutoff_key)
        return row if isinstance(row, dict) else {}
    except Exception:
        return {}
