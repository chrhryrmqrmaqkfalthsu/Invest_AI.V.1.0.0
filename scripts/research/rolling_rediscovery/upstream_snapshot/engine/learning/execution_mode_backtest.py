"""Rolling target-date and fixed-two-session comparison backtests.

Research-only working copy.  Entry remains the daily strict-AND probability
score.  Exit no longer reuses a failed entry score as an immediate liquidation
trigger.  Instead, every active score projects a target date two trading
sessions forward; inactive days keep the last valid target unchanged.
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
    """Binary strict-AND gate converted to its train precision probability."""
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
            "early_take_profit_count": 0,
            "target_date_exit_count": 0,
            "mean_target_extension_count": 0.0,
            "max_target_extension_count": 0,
        }
    returns = np.array([float(row["return_pct"]) / 100.0 for row in trades], dtype=float)
    holds = np.array([int(row["holding_sessions"]) for row in trades], dtype=float)
    extensions = np.array([int(row.get("target_extension_count", 0)) for row in trades], dtype=float)
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
        "early_take_profit_count": int(sum(row.get("exit_reason") == "EARLY_TAKE_PROFIT" for row in trades)),
        "target_date_exit_count": int(sum(row.get("exit_reason") == "TARGET_DATE_REACHED" for row in trades)),
        "mean_target_extension_count": float(np.mean(extensions)),
        "max_target_extension_count": int(np.max(extensions)),
    }


def _valid_price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def rolling_target_backtest(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    *,
    target_horizon_sessions: int = 2,
    early_take_profit: bool = False,
    take_profit_pct: float = 3.0,
    round_trip_cost_bps: float = 10.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Backtest the rolling target-date exit contract.

    Contract:
    - Enter at D0 open when score >= threshold.
    - Initial target index is entry index + target_horizon_sessions.
    - On every later holding day with score >= threshold, move the target to
      that day + target_horizon_sessions.
    - A score below threshold never liquidates immediately; it only stops the
      extension.  Sell at target-day close when the non-extended target arrives.
    - With early_take_profit=True, an intraday high reaching entry +3% exits at
      the exact target price on that day.
    - A position whose target lies beyond the supplied frame is marked to the
      final D0 close and explicitly labelled as a period-end exit.
    """
    if frame.empty:
        return _performance([]), []
    if target_horizon_sessions < 1:
        raise ValueError("target_horizon_sessions must be >= 1")
    required = {"date", "entry_open_d0", "entry_close_d0"}
    if early_take_profit:
        required.add("entry_high_d0")
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"rolling target backtest missing columns: {missing}")

    score_arr = np.asarray(scores, dtype=float)
    if len(score_arr) != len(frame):
        raise ValueError(f"score length mismatch: scores={len(score_arr)} frame={len(frame)}")
    active = score_arr >= float(threshold)
    trades: list[dict[str, Any]] = []

    entry_idx: int | None = None
    entry_price = 0.0
    target_idx: int | None = None
    initial_target_idx: int | None = None
    extension_count = 0

    def close_trade(exit_i: int, exit_price: float, reason: str, *, period_end: bool) -> None:
        nonlocal entry_idx, entry_price, target_idx, initial_target_idx, extension_count
        assert entry_idx is not None
        assert target_idx is not None
        assert initial_target_idx is not None
        ret = _trade_return(entry_price, exit_price, round_trip_cost_bps)
        final_target_date = str(frame.iloc[min(target_idx, len(frame) - 1)]["date"])
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
                "initial_target_date": str(frame.iloc[min(initial_target_idx, len(frame) - 1)]["date"]),
                "final_target_date": final_target_date,
                "target_index_beyond_period": bool(target_idx >= len(frame)),
                "target_extension_count": int(extension_count),
                "exit_reason": reason,
                "early_take_profit": bool(reason == "EARLY_TAKE_PROFIT"),
                "period_end_exit": bool(period_end),
            }
        )
        entry_idx = None
        entry_price = 0.0
        target_idx = None
        initial_target_idx = None
        extension_count = 0

    for i in range(len(frame)):
        if entry_idx is None:
            if not active[i]:
                continue
            price = _valid_price(frame.iloc[i]["entry_open_d0"])
            if price is None:
                continue
            entry_idx = i
            entry_price = price
            target_idx = i + int(target_horizon_sessions)
            initial_target_idx = target_idx
            extension_count = 0
            if early_take_profit:
                high = _valid_price(frame.iloc[i]["entry_high_d0"])
                take_price = entry_price * (1.0 + take_profit_pct / 100.0)
                if high is not None and high >= take_price:
                    close_trade(i, take_price, "EARLY_TAKE_PROFIT", period_end=False)
            continue

        assert target_idx is not None

        if early_take_profit:
            high = _valid_price(frame.iloc[i]["entry_high_d0"])
            take_price = entry_price * (1.0 + take_profit_pct / 100.0)
            if high is not None and high >= take_price:
                close_trade(i, take_price, "EARLY_TAKE_PROFIT", period_end=False)
                continue

        # Extension is evaluated before target expiry.  Therefore an active
        # score on the current target day rolls the target two sessions forward.
        if active[i]:
            proposed_target = i + int(target_horizon_sessions)
            if proposed_target > target_idx:
                target_idx = proposed_target
                extension_count += 1

        if i >= target_idx:
            exit_price = _valid_price(frame.iloc[i]["entry_close_d0"])
            if exit_price is None:
                exit_price = _valid_price(frame.iloc[i]["entry_open_d0"])
            if exit_price is None:
                exit_price = entry_price
            close_trade(i, exit_price, "TARGET_DATE_REACHED", period_end=False)

    if entry_idx is not None:
        exit_i = len(frame) - 1
        exit_price = _valid_price(frame.iloc[exit_i]["entry_close_d0"])
        if exit_price is None:
            exit_price = _valid_price(frame.iloc[exit_i]["entry_open_d0"])
        if exit_price is None:
            exit_price = entry_price
        close_trade(exit_i, exit_price, "FORCED_PERIOD_END_MARK_TO_MARKET", period_end=True)

    return _performance(trades), trades


def fixed_two_day_backtest(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    *,
    round_trip_cost_bps: float = 10.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Flat-only baseline: enter on an active day and exit after two sessions."""
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
        entry_price = _valid_price(frame.iloc[i]["entry_open_d0"])
        exit_i = i + 2
        exit_price = _valid_price(frame.iloc[exit_i]["entry_close_d0"])
        if entry_price is not None and exit_price is not None:
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
                    "initial_target_date": str(frame.iloc[exit_i]["date"]),
                    "final_target_date": str(frame.iloc[exit_i]["date"]),
                    "target_index_beyond_period": False,
                    "target_extension_count": 0,
                    "exit_reason": "FIXED_TWO_SESSION_EXIT",
                    "early_take_profit": False,
                    "period_end_exit": False,
                }
            )
        i = exit_i + 1
    return _performance(trades), trades


def whipsaw_statistics(
    frame: pd.DataFrame,
    scores: np.ndarray,
    threshold: float,
    trades: list[dict[str, Any]],
    *,
    method: str,
) -> dict[str, Any]:
    score_arr = np.asarray(scores, dtype=float)
    active = score_arr >= float(threshold)
    transitions = int(np.sum(active[1:] != active[:-1])) if len(active) > 1 else 0
    one_session_all = int(sum(int(row.get("holding_sessions", 0)) <= 1 for row in trades))
    one_session_signal_whipsaw = int(
        sum(
            int(row.get("holding_sessions", 0)) <= 1
            and row.get("exit_reason") not in {"EARLY_TAKE_PROFIT", "FORCED_PERIOD_END_MARK_TO_MARKET"}
            for row in trades
        )
    )
    short_cycle = int(sum(int(row.get("holding_sessions", 0)) <= 2 for row in trades))
    margin = np.abs(score_arr - float(threshold))
    near = margin <= 0.03
    near_transitions = int(np.sum((active[1:] != active[:-1]) & (near[1:] | near[:-1]))) if len(active) > 1 else 0
    max_hold = max([int(row.get("holding_sessions", 0)) for row in trades] or [0])
    return {
        "method": method,
        "evaluated_days": int(len(frame)),
        "active_days": int(active.sum()),
        "threshold_crossing_count": transitions,
        "near_threshold_crossing_count": near_transitions,
        "trade_count": int(len(trades)),
        "one_session_trade_count_all": one_session_all,
        "one_session_whipsaw_count": one_session_signal_whipsaw,
        "two_or_less_session_cycle_count": short_cycle,
        "whipsaw_rate": one_session_all / len(trades) if trades else 0.0,
        "signal_whipsaw_rate": one_session_signal_whipsaw / len(trades) if trades else 0.0,
        "max_holding_sessions": max_hold,
        "holding_over_20_sessions_count": int(sum(int(row.get("holding_sessions", 0)) > 20 for row in trades)),
        "holding_over_60_sessions_count": int(sum(int(row.get("holding_sessions", 0)) > 60 for row in trades)),
        "period_end_open_position_count": int(sum(bool(row.get("period_end_exit")) for row in trades)),
        "early_take_profit_count": int(sum(row.get("exit_reason") == "EARLY_TAKE_PROFIT" for row in trades)),
        "target_date_exit_count": int(sum(row.get("exit_reason") == "TARGET_DATE_REACHED" for row in trades)),
        "infinite_holding_risk_flag": bool(max_hold > 252),
    }
