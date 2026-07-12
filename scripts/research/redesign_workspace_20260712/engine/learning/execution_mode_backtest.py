"""Daily strict-AND execution loop for the isolated redesign workspace.

Differences from production:
- evaluates the signal on every eligible trading day, including while holding;
- enters at D+1 open after a strict-AND signal;
- exits on interval break, ATR stop, or a fixed seven-day maximum;
- removes take-profit and trailing exits;
- uses mean trade return per holding day as fitness.
"""
from __future__ import annotations

from dataclasses import asdict
from math import isfinite
from typing import Any, Optional

import numpy as np
import pandas as pd

from engine.learning.backtest import (
    BacktestResult,
    _apply_complexity_penalty,
    _lookup_signal_context,
    _news_zscore_window,
    _precompute_topic_feature_map,
    _signal_snapshot,
    _summarize,
)
from engine.strategies.evaluator import (
    STRICT_INTERVAL_FEATURE_LAG_DAYS,
    calc_position_size_krw,
    evaluate_signal,
    mean_daily_return_fitness,
)
from engine.strategies.rulebook import FIXED_MAX_HOLDING_DAYS, Rulebook


def _date_series_for_df(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        values = pd.to_datetime(df["date"], errors="coerce")
        return pd.Series(values.to_numpy(), index=range(len(df)))
    values = pd.to_datetime(pd.Index(df.index), errors="coerce")
    return pd.Series(values.to_numpy(), index=range(len(df)))


def _date_at(date_series: pd.Series, index: int) -> pd.Timestamp | None:
    try:
        value = pd.Timestamp(date_series.iloc[int(index)])
    except Exception:
        return None
    return value if pd.notna(value) else None


def _price(row: pd.Series, key: str, fallback: str = "Close") -> float:
    value = row.get(key, row.get(fallback))
    result = float(value)
    if not isfinite(result) or result <= 0:
        raise ValueError(f"invalid price {key}={value}")
    return result


def _context_at(
    *,
    rb: Rulebook,
    df: pd.DataFrame,
    index: int,
    market_score: float,
    sector_score: float,
    vix_level: float,
    market_history_df: Optional[pd.DataFrame],
    sector_name: str,
    ticker_sentiment: Optional[dict],
    topic_feature_map: dict,
    use_llm_events: bool,
) -> tuple[Any, tuple[float, float, float, float, dict, dict]]:
    context = _lookup_signal_context(
        df=df,
        idx=index,
        market_score=market_score,
        sector_score=sector_score,
        vix_level=vix_level,
        market_history_df=market_history_df,
        sector_name=sector_name,
        ticker_sentiment=ticker_sentiment,
        topic_feature_map=topic_feature_map,
        use_llm_events=use_llm_events,
    )
    cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = context
    signal = evaluate_signal(
        rb,
        df.iloc[: index + 1],
        market_score=cur_market,
        sector_score=cur_sector,
        vix_level=cur_vix,
        news_sentiment=cur_sentiment,
        event_flags=cur_event_flags,
        topic_features=cur_topic_features,
    )
    return signal, context


def _daily_path_diagnostics(
    df: pd.DataFrame,
    *,
    entry_index: int,
    exit_index: int,
    entry_price: float,
    exit_price: float,
) -> dict[str, Any]:
    daily_returns: list[float] = []
    cumulative: list[float] = []
    previous = float(entry_price)
    equity = 0.0
    for index in range(int(entry_index), int(exit_index) + 1):
        if index == int(exit_index):
            current = float(exit_price)
        else:
            current = float(df.iloc[index].get("Close", previous))
        daily = ((current / previous) - 1.0) * 100.0 if previous > 0 else 0.0
        daily_returns.append(float(daily))
        equity += float(daily)
        cumulative.append(float(equity))
        previous = current

    negative_abs = [abs(value) for value in daily_returns if value < 0]
    worst_daily = min(daily_returns) if daily_returns else 0.0
    negative_sum = sum(negative_abs)
    concentration = abs(worst_daily) / negative_sum if negative_sum > 0 else 0.0
    running_max = np.maximum.accumulate(np.asarray(cumulative, dtype=float)) if cumulative else np.asarray([])
    drawdowns = np.asarray(cumulative, dtype=float) - running_max if cumulative else np.asarray([])
    path_mdd = float(drawdowns.min()) if len(drawdowns) else 0.0

    return {
        "holding_daily_returns_pct": daily_returns,
        "holding_cumulative_return_path_pct": cumulative,
        "worst_single_day_return_pct": float(worst_daily),
        "negative_return_abs_sum_pct": float(negative_sum),
        "daily_loss_concentration": float(concentration),
        "holding_path_mdd_pct": path_mdd,
        "mdd_type": "pending_real_measurement_threshold",
    }


def _build_trade(
    *,
    rb: Rulebook,
    df: pd.DataFrame,
    date_series: pd.Series,
    position: dict[str, Any],
    exit_index: int,
    exit_price: float,
    exit_reason: str,
    commission_rate: float,
    exit_signal: Any | None,
    exit_context: tuple[float, float, float, float, dict, dict] | None,
) -> dict[str, Any]:
    entry_index = int(position["entry_index"])
    entry_price = float(position["entry_price"])
    shares = int(position["shares"])
    holding_days = max(1, int(exit_index) - entry_index + 1)
    gross_return_pct = ((float(exit_price) / entry_price) - 1.0) * 100.0
    commission_pct = float(commission_rate) * 2.0 * 100.0
    pnl_pct = gross_return_pct - commission_pct
    gross_value = entry_price * shares
    pnl_krw = gross_value * pnl_pct / 100.0

    path = _daily_path_diagnostics(
        df,
        entry_index=entry_index,
        exit_index=exit_index,
        entry_price=entry_price,
        exit_price=float(exit_price),
    )
    closes = pd.to_numeric(df.iloc[entry_index : exit_index + 1]["Close"], errors="coerce")
    high_return = ((float(closes.max()) / entry_price) - 1.0) * 100.0 if len(closes) else pnl_pct
    low_return = ((float(closes.min()) / entry_price) - 1.0) * 100.0 if len(closes) else pnl_pct

    trade: dict[str, Any] = {
        "entry_date": str((_date_at(date_series, entry_index) or pd.Timestamp.min).date()),
        "entry_signal_date": position["entry_signal_date"],
        "entry_fill_date": str((_date_at(date_series, entry_index) or pd.Timestamp.min).date()),
        "entry_price": entry_price,
        "entry_shares": shares,
        "total_shares": shares,
        "avg_cost": entry_price,
        "exit_date": str((_date_at(date_series, exit_index) or pd.Timestamp.min).date()),
        "exit_price": float(exit_price),
        "exit_reason": str(exit_reason),
        "holding_days": holding_days,
        "pnl_pct": float(pnl_pct),
        "pnl_krw": float(pnl_krw),
        "commission": float(gross_value * commission_rate * 2.0),
        "max_profit_during_hold": float(high_return),
        "max_loss_during_hold": float(low_return),
        "entry_atr": float(position["entry_atr"]),
        "stop_price_at_entry": float(position["stop_price"]),
        "target_price_at_entry": None,
        "trailing_stop_at_entry": None,
        "exit_strategy": "strict_interval",
        "entry_execution_mode": "t_plus_1_open",
        "exit_execution_mode": "strict_interval_daily",
        "feature_lag_days": STRICT_INTERVAL_FEATURE_LAG_DAYS,
        "max_holding_days_fixed": FIXED_MAX_HOLDING_DAYS,
        "interval_exit_signal_seen": bool(position.get("interval_exit_signal_seen", False)),
        "interval_exit_signal_date": position.get("interval_exit_signal_date"),
        "holding_signal_evaluations": int(position.get("holding_signal_evaluations", 0)),
        "holding_signal_passes": int(position.get("holding_signal_passes", 0)),
        **path,
    }
    trade.update(position["entry_snapshot"])

    if exit_signal is not None and exit_context is not None:
        cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = exit_context
        trade.update(
            _signal_snapshot(
                "exit",
                exit_signal,
                sentiment=cur_sentiment,
                market=cur_market,
                sector=cur_sector,
                vix=cur_vix,
                event_flags=cur_event_flags,
                topic_features=cur_topic_features,
            )
        )
    else:
        trade["exit_signal_reason"] = exit_reason
        trade["exit_signal_reasons"] = [exit_reason]
    trade["daily_efficiency_pct"] = float(pnl_pct) / float(holding_days)
    return trade


def _apply_daily_efficiency_fitness(
    rb: Rulebook,
    result: BacktestResult,
    *,
    complexity_penalty_per_mask: float,
) -> BacktestResult:
    raw_fitness = mean_daily_return_fitness(result.trades)
    result.fitness = _apply_complexity_penalty(rb, raw_fitness, complexity_penalty_per_mask)
    rb.fitness = float(result.fitness)
    rb.fitness_daily_efficiency = float(raw_fitness)
    return result


def run_backtest_execution_mode(
    rb: Rulebook,
    df: pd.DataFrame,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    position_limit_krw: float = 120000.0,
    commission_rate: float = 0.0005,
    cooldown_days: int = 1,
    warmup: int = 200,
    market_history_df: Optional[pd.DataFrame] = None,
    sector_name: str = "tech",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    ticker_sentiment: Optional[dict] = None,
    fitness_mode: str = "swing",
    complexity_penalty_per_mask: float = 0.0,
    use_llm_events: bool = False,
    entry_execution_mode: str = "t_plus_1_open",
    exit_execution_mode: str = "strict_interval_daily",
    fold_exit_policy: str = "fold_end_mark_to_market",
    live_hard_stop_guard: bool = False,
) -> BacktestResult:
    """Run the daily state-machine backtest for one Rulebook."""
    if str(entry_execution_mode) not in {"t_plus_1_open", "next_open"}:
        raise ValueError("strict interval redesign requires t_plus_1_open entry")

    rb.max_holding_days = FIXED_MAX_HOLDING_DAYS
    trades: list[dict[str, Any]] = []
    date_series = _date_series_for_df(df)
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    topic_window = _news_zscore_window(rb)
    topic_feature_map = _precompute_topic_feature_map(ticker_sentiment, topic_window)
    n = len(df)
    index = max(int(warmup), 60 + STRICT_INTERVAL_FEATURE_LAG_DAYS)
    position: dict[str, Any] | None = None

    flat_signal_evaluations = 0
    flat_signal_passes = 0
    holding_signal_evaluations = 0
    holding_signal_passes = 0
    total_daily_evaluations = 0
    total_daily_passes = 0

    while index < n:
        current_ts = _date_at(date_series, index)
        if current_ts is None:
            index += 1
            continue
        if start_ts is not None and current_ts < start_ts:
            index += 1
            continue
        if end_ts is not None and current_ts > end_ts:
            break

        if position is None:
            signal, context = _context_at(
                rb=rb,
                df=df,
                index=index,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                market_history_df=market_history_df,
                sector_name=sector_name,
                ticker_sentiment=ticker_sentiment,
                topic_feature_map=topic_feature_map,
                use_llm_events=use_llm_events,
            )
            flat_signal_evaluations += 1
            total_daily_evaluations += 1
            if signal.should_buy:
                flat_signal_passes += 1
                total_daily_passes += 1
            else:
                index += 1
                continue

            entry_index = index + 1
            if entry_index >= n:
                break
            entry_ts = _date_at(date_series, entry_index)
            if entry_ts is None or (end_ts is not None and entry_ts > end_ts):
                break
            entry_row = df.iloc[entry_index]
            entry_price = _price(entry_row, "Open")
            signal_row = df.iloc[index]
            try:
                entry_atr = float(signal_row.get("ATR", entry_price * 0.02))
            except Exception:
                entry_atr = entry_price * 0.02
            if not isfinite(entry_atr) or entry_atr <= 0:
                entry_atr = entry_price * 0.02

            amount = calc_position_size_krw(rb, signal.score, position_limit_krw)
            shares = int(amount / entry_price) if entry_price > 0 else 0
            if shares <= 0:
                index += 1
                continue

            cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = context
            entry_snapshot = _signal_snapshot(
                "entry",
                signal,
                sentiment=cur_sentiment,
                market=cur_market,
                sector=cur_sector,
                vix=cur_vix,
                event_flags=cur_event_flags,
                topic_features=cur_topic_features,
            )
            position = {
                "entry_index": entry_index,
                "entry_signal_index": index,
                "entry_signal_date": str(current_ts.date()),
                "entry_price": entry_price,
                "entry_atr": entry_atr,
                "stop_price": entry_price - float(rb.stop_loss_atr) * entry_atr,
                "shares": shares,
                "entry_snapshot": entry_snapshot,
                "holding_signal_evaluations": 0,
                "holding_signal_passes": 0,
                "interval_exit_signal_seen": False,
                "interval_exit_signal_date": None,
            }
            index = entry_index
            continue

        entry_index = int(position["entry_index"])
        row = df.iloc[index]
        row_open = _price(row, "Open")
        row_low = _price(row, "Low")
        row_close = _price(row, "Close")
        stop_price = float(position["stop_price"])

        # Intraday stop is evaluated before close-based interval information.
        if row_open <= stop_price or row_low <= stop_price:
            exit_price = row_open if row_open <= stop_price else stop_price
            reason = "stop_gap" if row_open <= stop_price else "stop_loss_atr"
            trade = _build_trade(
                rb=rb,
                df=df,
                date_series=date_series,
                position=position,
                exit_index=index,
                exit_price=exit_price,
                exit_reason=reason,
                commission_rate=commission_rate,
                exit_signal=None,
                exit_context=None,
            )
            trades.append(trade)
            position = None
            index += 1 + max(0, int(cooldown_days))
            continue

        signal, context = _context_at(
            rb=rb,
            df=df,
            index=index,
            market_score=market_score,
            sector_score=sector_score,
            vix_level=vix_level,
            market_history_df=market_history_df,
            sector_name=sector_name,
            ticker_sentiment=ticker_sentiment,
            topic_feature_map=topic_feature_map,
            use_llm_events=use_llm_events,
        )
        position["holding_signal_evaluations"] += 1
        holding_signal_evaluations += 1
        total_daily_evaluations += 1
        if signal.should_buy:
            position["holding_signal_passes"] += 1
            holding_signal_passes += 1
            total_daily_passes += 1

        holding_days = index - entry_index + 1
        at_fold_end = end_ts is not None and current_ts >= end_ts
        if not signal.should_buy:
            position["interval_exit_signal_seen"] = True
            position["interval_exit_signal_date"] = str(current_ts.date())
            next_index = index + 1
            if next_index < n:
                next_ts = _date_at(date_series, next_index)
            else:
                next_ts = None
            if next_index < n and next_ts is not None and (end_ts is None or next_ts <= end_ts):
                exit_index = next_index
                exit_price = _price(df.iloc[next_index], "Open")
            else:
                exit_index = index
                exit_price = row_close
            trade = _build_trade(
                rb=rb,
                df=df,
                date_series=date_series,
                position=position,
                exit_index=exit_index,
                exit_price=exit_price,
                exit_reason="interval_break",
                commission_rate=commission_rate,
                exit_signal=signal,
                exit_context=context,
            )
            trades.append(trade)
            position = None
            index = exit_index + 1 + max(0, int(cooldown_days))
            continue

        if holding_days >= FIXED_MAX_HOLDING_DAYS:
            trade = _build_trade(
                rb=rb,
                df=df,
                date_series=date_series,
                position=position,
                exit_index=index,
                exit_price=row_close,
                exit_reason="max_holding_7d",
                commission_rate=commission_rate,
                exit_signal=signal,
                exit_context=context,
            )
            trades.append(trade)
            position = None
            index += 1 + max(0, int(cooldown_days))
            continue

        if at_fold_end:
            trade = _build_trade(
                rb=rb,
                df=df,
                date_series=date_series,
                position=position,
                exit_index=index,
                exit_price=row_close,
                exit_reason="fold_end_mark_to_market",
                commission_rate=commission_rate,
                exit_signal=signal,
                exit_context=context,
            )
            trades.append(trade)
            position = None
            break

        index += 1

    # Mark any still-open position at the last admissible close.
    if position is not None:
        final_index = min(n - 1, max(int(position["entry_index"]), index - 1))
        if end_ts is not None:
            eligible = [
                idx
                for idx in range(int(position["entry_index"]), n)
                if (_date_at(date_series, idx) is not None and _date_at(date_series, idx) <= end_ts)
            ]
            if eligible:
                final_index = max(eligible)
        final_close = _price(df.iloc[final_index], "Close")
        trades.append(
            _build_trade(
                rb=rb,
                df=df,
                date_series=date_series,
                position=position,
                exit_index=final_index,
                exit_price=final_close,
                exit_reason="fold_end_mark_to_market",
                commission_rate=commission_rate,
                exit_signal=None,
                exit_context=None,
            )
        )

    result = _summarize(rb, trades)
    result = _apply_daily_efficiency_fitness(
        rb,
        result,
        complexity_penalty_per_mask=complexity_penalty_per_mask,
    )
    result.flat_signal_evaluations = flat_signal_evaluations
    result.flat_signal_passes = flat_signal_passes
    result.flat_signal_coverage = (
        float(flat_signal_passes) / float(flat_signal_evaluations)
        if flat_signal_evaluations
        else 0.0
    )
    result.holding_signal_evaluations = holding_signal_evaluations
    result.holding_signal_passes = holding_signal_passes
    result.holding_signal_coverage = (
        float(holding_signal_passes) / float(holding_signal_evaluations)
        if holding_signal_evaluations
        else 0.0
    )
    result.total_daily_signal_evaluations = total_daily_evaluations
    result.total_daily_signal_passes = total_daily_passes
    result.total_daily_signal_coverage = (
        float(total_daily_passes) / float(total_daily_evaluations)
        if total_daily_evaluations
        else 0.0
    )
    result.feature_lag_days = STRICT_INTERVAL_FEATURE_LAG_DAYS
    result.max_holding_days_fixed = FIXED_MAX_HOLDING_DAYS
    result.mdd_classification_threshold = "pending_real_measurement"
    return result
