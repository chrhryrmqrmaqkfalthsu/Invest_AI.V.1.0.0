"""Entry-scope effective-event-count fitness penalty for AAP v5 research.

This module intentionally keeps signal generation, entry/exit execution,
strict interval, mutation, legacy scheduling, and fixed notional accounting
unchanged.  It only wraps the entry-scope fitness function and multiplies the
existing trade-count-adjusted primary objective by an EEC concentration factor.
"""
from __future__ import annotations

import math
import os
from collections import Counter
from typing import Any, Mapping

ENTRY_FITNESS_EEC_TARGET = 4.0
ENTRY_FITNESS_EEC_FLOOR = 0.7
ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS = 8
PATCH_TOKEN = "entry_scope_eec_penalty_v5_20260715"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _cluster_params() -> tuple[float, float, int]:
    target = _safe_float(os.environ.get("KINGMAKER_ENTRY_EEC_TARGET"), ENTRY_FITNESS_EEC_TARGET)
    floor = _safe_float(os.environ.get("KINGMAKER_ENTRY_EEC_FLOOR"), ENTRY_FITNESS_EEC_FLOOR)
    gap = _safe_int(
        os.environ.get("KINGMAKER_ENTRY_EEC_CLUSTER_GAP_TRADING_DAYS"),
        ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS,
    )
    target = target if target > 0.0 else ENTRY_FITNESS_EEC_TARGET
    floor = min(1.0, max(0.0, floor))
    gap = max(0, gap)
    return float(target), float(floor), int(gap)


def _trade_event_key(trade: Mapping[str, Any], fallback_index: int) -> tuple[int, str]:
    tape = trade.get("entry_signal_tape") if isinstance(trade.get("entry_signal_tape"), Mapping) else {}
    row_index = _safe_int(tape.get("row_index"), fallback_index)
    date = str(
        tape.get("date")
        or trade.get("entry_signal_date")
        or trade.get("entry_fill_date")
        or trade.get("entry_date")
        or fallback_index
    )
    return row_index, date


def effective_event_count_from_trades(
    trades: list[dict[str, Any]],
    *,
    max_gap: int = ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS,
) -> dict[str, Any]:
    """Return EEC diagnostics using the v4 event-cluster definition.

    One non-duplicated fill event is counted per entry signal date.  Adjacent
    events are grouped when their daily-signal-tape row-index gap is <= max_gap.
    The effective event count is 1 / sum(cluster_trade_share^2).
    """
    event_by_date: dict[str, dict[str, Any]] = {}
    for fallback_index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            continue
        row_index, date = _trade_event_key(trade, fallback_index)
        current = event_by_date.get(date)
        if current is None or row_index < int(current["row_index"]):
            event_by_date[date] = {
                "row_index": int(row_index),
                "date": date,
                "trade_indices": [fallback_index],
            }
        else:
            current.setdefault("trade_indices", []).append(fallback_index)

    events = sorted(event_by_date.values(), key=lambda row: (int(row["row_index"]), str(row["date"])))
    clusters: list[list[dict[str, Any]]] = []
    for event in events:
        if not clusters or int(event["row_index"]) - int(clusters[-1][-1]["row_index"]) > int(max_gap):
            clusters.append([event])
        else:
            clusters[-1].append(event)

    event_count = len(events)
    cluster_rows: list[dict[str, Any]] = []
    shares: list[float] = []
    event_to_cluster: dict[str, tuple[int, float, float]] = {}
    if event_count > 0:
        for cluster_index, cluster in enumerate(clusters, 1):
            share = len(cluster) / event_count
            share_squared = share * share
            shares.append(share)
            for event in cluster:
                event_to_cluster[str(event["date"])] = (cluster_index, share, share_squared)
            cluster_rows.append(
                {
                    "cluster_index": cluster_index,
                    "start": str(cluster[0]["date"]),
                    "end": str(cluster[-1]["date"]),
                    "event_count": len(cluster),
                    "trade_share": float(share),
                    "share_squared": float(share_squared),
                    "monthly_distribution": dict(Counter(str(event["date"])[:7] for event in cluster)),
                }
            )
    denominator = sum(value * value for value in shares)
    effective = 1.0 / denominator if denominator > 0.0 else 0.0
    max_share = max(shares) if shares else 0.0

    for fallback_index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            continue
        _, date = _trade_event_key(trade, fallback_index)
        cluster_index, share, share_squared = event_to_cluster.get(date, (None, 0.0, 0.0))
        trade["entry_fitness_eec_cluster_index"] = cluster_index
        trade["entry_fitness_eec_cluster_trade_share"] = float(share)
        trade["entry_fitness_eec_cluster_share_squared"] = float(share_squared)
        trade["entry_fitness_eec_trade_share"] = float(1.0 / event_count) if event_count else 0.0
        trade["entry_fitness_eec_contribution_note"] = "cluster_share_squared contributes to sum(cluster_share^2) denominator"

    return {
        "event_cluster_gap_trading_days": int(max_gap),
        "nonduplicate_event_count": int(event_count),
        "cluster_count": int(len(clusters)),
        "effective_event_count": float(effective),
        "max_cluster_trade_share": float(max_share),
        "clusters": cluster_rows,
    }


def eec_multiplier(effective_event_count: float, *, target: float, floor: float) -> float:
    if target <= 0.0:
        return 1.0
    return float(max(floor, min(1.0, _safe_float(effective_event_count) / target)))


def apply_eec_to_entry_scope_result(
    execution_bt: Any,
    rb: Any,
    result: Any,
    *,
    complexity_penalty_per_mask: float,
) -> Any:
    diagnostics = dict(getattr(result, "entry_fitness_diagnostics", {}) or {})
    trades = [trade for trade in list(getattr(result, "trades", []) or []) if isinstance(trade, dict)]
    target, floor, gap = _cluster_params()
    metrics = effective_event_count_from_trades(trades, max_gap=gap)
    multiplier = eec_multiplier(metrics["effective_event_count"], target=target, floor=floor)

    primary_after_trade_count = _safe_float(diagnostics.get("primary_after_trade_count_factor"))
    eec_adjusted_primary = primary_after_trade_count * multiplier
    mae_penalty = _safe_float(diagnostics.get("mae_penalty"))
    realized_loss_penalty = _safe_float(diagnostics.get("realized_loss_penalty"))
    pre_complexity = eec_adjusted_primary - mae_penalty - realized_loss_penalty
    post_complexity = execution_bt._apply_complexity_penalty(
        rb,
        pre_complexity,
        complexity_penalty_per_mask,
    )
    disqualified = bool(diagnostics.get("disqualified"))
    disqualified_value = _safe_float(
        diagnostics.get("disqualified_fitness"),
        getattr(execution_bt, "ENTRY_FITNESS_DISQUALIFIED", -1_000_000_000.0),
    )
    final = disqualified_value if disqualified else post_complexity

    diagnostics.update(
        {
            "entry_fitness_eec_penalty_enabled": True,
            "entry_fitness_eec_patch_token": PATCH_TOKEN,
            "entry_fitness_eec_target": float(target),
            "entry_fitness_eec_floor": float(floor),
            "entry_fitness_eec_multiplier_formula": "clamp(effective_event_count / target, floor, 1.0)",
            "entry_fitness_eec_multiplier": float(multiplier),
            "effective_event_count": float(metrics["effective_event_count"]),
            "eec_nonduplicate_event_count": int(metrics["nonduplicate_event_count"]),
            "eec_event_cluster_count": int(metrics["cluster_count"]),
            "eec_event_cluster_gap_trading_days": int(metrics["event_cluster_gap_trading_days"]),
            "eec_max_cluster_trade_share": float(metrics["max_cluster_trade_share"]),
            "eec_event_clusters": metrics["clusters"],
            "primary_after_trade_count_factor_before_eec": float(primary_after_trade_count),
            "primary_after_eec_penalty": float(eec_adjusted_primary),
            "fitness_before_complexity_before_eec": _safe_float(diagnostics.get("fitness_before_complexity")),
            "fitness_before_entry_gate_before_eec": _safe_float(diagnostics.get("fitness_before_entry_gate")),
            "fitness_before_win_gate_before_eec": _safe_float(diagnostics.get("fitness_before_win_gate")),
            "fitness_before_complexity": float(pre_complexity),
            "fitness_before_entry_gate": float(post_complexity),
            "fitness_before_win_gate": float(post_complexity),
            "final_fitness_before_eec": _safe_float(diagnostics.get("final_fitness")),
            "final_fitness": float(final),
        }
    )

    for trade in trades:
        trade["entry_fitness_effective_event_count"] = float(metrics["effective_event_count"])
        trade["entry_fitness_eec_multiplier"] = float(multiplier)
        trade["entry_fitness_eec_target"] = float(target)
        trade["entry_fitness_eec_floor"] = float(floor)

    result.fitness = float(final)
    result.entry_fitness_diagnostics = diagnostics
    result.trades = trades
    rb.fitness = float(final)
    setattr(rb, getattr(execution_bt, "ENTRY_FITNESS_DIAGNOSTICS_ATTR", "_entry_fitness_diagnostics"), diagnostics)
    return result


def install(execution_bt: Any) -> None:
    if getattr(execution_bt, "_ENTRY_EEC_V5_INSTALLED", False):
        return
    original = execution_bt._apply_entry_scope_fitness

    def _wrapped_apply_entry_scope_fitness(rb: Any, result: Any, *, complexity_penalty_per_mask: float) -> Any:
        scoped = original(rb, result, complexity_penalty_per_mask=complexity_penalty_per_mask)
        return apply_eec_to_entry_scope_result(
            execution_bt,
            rb,
            scoped,
            complexity_penalty_per_mask=complexity_penalty_per_mask,
        )

    execution_bt._apply_entry_scope_fitness = _wrapped_apply_entry_scope_fitness
    execution_bt.ENTRY_FITNESS_EEC_TARGET = ENTRY_FITNESS_EEC_TARGET
    execution_bt.ENTRY_FITNESS_EEC_FLOOR = ENTRY_FITNESS_EEC_FLOOR
    execution_bt.ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS = ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS
    execution_bt._entry_fitness_effective_event_count = effective_event_count_from_trades
    execution_bt._entry_fitness_eec_multiplier = eec_multiplier
    execution_bt._ENTRY_EEC_V5_INSTALLED = True
    execution_bt._ENTRY_EEC_V5_ORIGINAL_APPLY = original
