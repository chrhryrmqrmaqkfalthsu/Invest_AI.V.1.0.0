"""Rolling score entry/exit and fixed-two-day comparison backtests.

The working-copy backtest removes artificial holding-day limits.  A position is
opened or maintained when the daily probability score is at least the single
decision threshold, and is absent or closed when it is below the same threshold.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def classification_metrics(y_true: np.ndarray, active: np.ndarray) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=int)
    mask = np.asarray(active, dtype=bool)
    n = int(len(y))
    positives = int(y.sum()) if n else 0
    passed = int(mask.sum()) if n else 0
    passed_positive = int(y[mask].sum()) if passed else 0
    base_rate = positives / n if n else 0.0
    precision = passed_positive / passed if passed else 0.0
    return {
        "signal_count": n,
        "positive_count": positives,
        "base_rate": base_rate,
        "passed_count": passed,
        "passed_positive_count": passed_positive,
        "precision": precision,
        "recall": passed_positive / positives if positives else 0.0,
        "coverage": passed / n if n else 0.0,
        "lift_pp": 100.0 * (precision - base_rate),
    }


def probability_scores(active: np.ndarray, pass_probability: float) -> np.ndarray:
    """Binary AND gate converted to a calibrated train precision probability."""
    return np.where(np.asarray(active, dtype=bool), float(pass_probability), 0.0)


def _trade_return(entry_price: float, exit_price: float, round_trip_cost_bps: float) -> float:
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    gross = exit_price / entry_price - 1.0
    return float(gross - round_trip_cost_bps / 10_000.0)


def _performance(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "avg_return_pct": 0.0,
            "median_return_pct": 0.0,
            "compounded_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_holding_sessions": 0.0,
            "median_holding_sessions": 0.0,
            "p95_holding_sessions": 0.0,
            "max_holding_sessions": 0,
            "open_at_period_end_count": 0,
        }
    returns = np.array([float(row["return_pct"]) / 100.0 for row in trades], dtype=float)
    holds = np.array([int(row["holding_sessions"]) for row in trades], dtype=float)
    equity = np.cumprod(1.0 + returns)
    running = np.maximum.accumulate(equity)
    drawdowns = equity / running - 1.0
    return {
        "trade_count": int(len(trades)),
        "win_rate": float(np.mean(returns > 0.0)),
        "avg_return_pct": float(np.mean(returns) * 100.0),
        "median_return_pct": float(np.median(returns) * 100.0),
        "compounded_return_pct": float((equity[-1] - 1.0) * 100.0),
        "max_drawdown_pct": float(np.min(drawdowns) * 100.0),
        "avg_holding_sessions": float(np.mean(holds)),
        "median_holding_sessions": float(np.median(holds)),
        "p95_holding_sessions": float(np.quantile(holds, 0.95)),
        "max_holding_sessions": int(np.max(holds)),
        "open_at_period_end_count": int(sum(bool(row.get("period_end_exit")) for row in trades)),
    }


def rolling_score_backtest(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    *,
    round_trip_cost_bps: float = 10.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Same-threshold daily rolling entry/maintain/exit; no holding cap."""
    if frame.empty:
        return _performance([]), []
    score_arr = np.asarray(scores, dtype=float)
    active = score_arr >= float(threshold)
    trades: list[dict[str, Any]] = []
    entry_idx: int | None = None
    entry_price = 0.0

    for i in range(len(frame)):
        if active[i] and entry_idx is None:
            price = float(frame.iloc[i]["entry_open_d0"])
            if math.isfinite(price) and price > 0:
                entry_idx = i
                entry_price = price
        elif not active[i] and entry_idx is not None:
            exit_price = float(frame.iloc[i]["entry_open_d0"])
            if not math.isfinite(exit_price) or exit_price <= 0:
                exit_price = float(frame.iloc[i]["entry_close_d0"])
            ret = _trade_return(entry_price, exit_price, round_trip_cost_bps)
            trades.append(
                {
                    "entry_date": str(frame.iloc[entry_idx]["date"]),
                    "exit_date": str(frame.iloc[i]["date"]),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "return_pct": ret * 100.0,
                    "holding_sessions": i - entry_idx,
                    "entry_score": float(score_arr[entry_idx]),
                    "exit_score": float(score_arr[i]),
                    "period_end_exit": False,
                }
            )
            entry_idx = None
            entry_price = 0.0

    if entry_idx is not None:
        exit_i = len(frame) - 1
        exit_price = float(frame.iloc[exit_i]["entry_close_d0"])
        ret = _trade_return(entry_price, exit_price, round_trip_cost_bps)
        trades.append(
            {
                "entry_date": str(frame.iloc[entry_idx]["date"]),
                "exit_date": str(frame.iloc[exit_i]["date"]),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "return_pct": ret * 100.0,
                "holding_sessions": exit_i - entry_idx,
                "entry_score": float(score_arr[entry_idx]),
                "exit_score": float(score_arr[exit_i]),
                "period_end_exit": True,
            }
        )
    return _performance(trades), trades


def fixed_two_day_backtest(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    *,
    round_trip_cost_bps: float = 10.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Flat-only comparison: enter on an active day and exit after two sessions."""
    if frame.empty:
        return _performance([]), []
    score_arr = np.asarray(scores, dtype=float)
    active = score_arr >= float(threshold)
    trades: list[dict[str, Any]] = []
    i = 0
    while i < len(frame) - 2:
        if not active[i]:
            i += 1
            continue
        entry_price = float(frame.iloc[i]["entry_open_d0"])
        exit_i = i + 2
        exit_price = float(frame.iloc[exit_i]["entry_close_d0"])
        if entry_price > 0 and exit_price > 0 and math.isfinite(entry_price) and math.isfinite(exit_price):
            ret = _trade_return(entry_price, exit_price, round_trip_cost_bps)
            trades.append(
                {
                    "entry_date": str(frame.iloc[i]["date"]),
                    "exit_date": str(frame.iloc[exit_i]["date"]),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "return_pct": ret * 100.0,
                    "holding_sessions": 2,
                    "entry_score": float(score_arr[i]),
                    "exit_score": float(score_arr[exit_i]),
                    "period_end_exit": False,
                }
            )
        i = exit_i + 1
    return _performance(trades), trades


def whipsaw_statistics(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    rolling_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    score_arr = np.asarray(scores, dtype=float)
    active = score_arr >= float(threshold)
    transitions = int(np.sum(active[1:] != active[:-1])) if len(active) > 1 else 0
    one_day = int(sum(int(row.get("holding_sessions", 0)) <= 1 for row in rolling_trades))
    short_cycle = int(sum(int(row.get("holding_sessions", 0)) <= 2 for row in rolling_trades))
    margin = np.abs(score_arr - float(threshold))
    near = margin <= 0.03
    near_transitions = int(np.sum((active[1:] != active[:-1]) & (near[1:] | near[:-1]))) if len(active) > 1 else 0
    max_hold = max([int(row.get("holding_sessions", 0)) for row in rolling_trades] or [0])
    return {
        "evaluated_days": int(len(frame)),
        "active_days": int(active.sum()),
        "threshold_crossing_count": transitions,
        "near_threshold_crossing_count": near_transitions,
        "rolling_trade_count": int(len(rolling_trades)),
        "one_session_whipsaw_count": one_day,
        "two_or_less_session_cycle_count": short_cycle,
        "whipsaw_rate": one_day / len(rolling_trades) if rolling_trades else 0.0,
        "max_holding_sessions": max_hold,
        "holding_over_20_sessions_count": int(sum(int(row.get("holding_sessions", 0)) > 20 for row in rolling_trades)),
        "holding_over_60_sessions_count": int(sum(int(row.get("holding_sessions", 0)) > 60 for row in rolling_trades)),
        "period_end_open_position_count": int(sum(bool(row.get("period_end_exit")) for row in rolling_trades)),
        "infinite_holding_risk_flag": bool(max_hold > 252),
    }
