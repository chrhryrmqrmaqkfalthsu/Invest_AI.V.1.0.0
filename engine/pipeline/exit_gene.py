"""Exit-only gene helpers for Stage 3.

This module is intentionally pure and does not run GA or backtests. It provides
small helpers for the Stage 3 exit-GA wrapper that will keep entry fields fixed
and mutate only exit fields.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


EXIT_NUMERIC: tuple[str, ...] = (
    "stop_loss_atr",
    "stop_loss_atr_bear",
    "take_profit_atr",
    "take_profit_atr_bull",
    "trailing_atr",
    "trailing_atr_volatile",
    "trailing_activation_profit_pct",
    "breakeven_trigger_profit_pct",
    "breakeven_floor_profit_pct",
    "sell_omen_threshold",
    "max_holding_days",
)

EXIT_CATEGORICAL: tuple[str, ...] = (
    "exit_strategy",
    "breakeven_enabled",
    "sell_omen_enabled",
)

EXIT_FIELDS: tuple[str, ...] = EXIT_CATEGORICAL + EXIT_NUMERIC


@dataclass(frozen=True)
class ExitFitnessWeights:
    """Provisional composite-fitness coefficients for the first Stage 3 tests.

    These coefficients are deliberately conservative and must be tuned after the
    first real Stage 3 experiment. The intent is to reward bull-period
    expectancy, reward the weaker of stress/bull expectancies through a downside
    term, penalize failure to clear a bull floor, penalize stress drawdown, and
    penalize median holding days above a soft cap.
    """

    w_downside: float = 2.0
    w_bull_floor_penalty: float = 3.0
    bull_floor: float = 1.0
    w_stress_mdd: float = 0.2
    w_holding: float = 0.1
    holding_soft_cap: float = 7.0


DEFAULT_EXIT_FITNESS_WEIGHTS = ExitFitnessWeights()


def apply_exit(base_rulebook_dict: Mapping[str, Any], exit_gene: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copied rulebook dict with only EXIT_FIELDS overwritten.

    Entry, position-sizing, market-reaction, and metadata fields are copied from
    the base rulebook unchanged. Missing exit-gene keys are ignored so callers
    may layer partial genes during testing, while production callers should pass
    a complete EXIT_FIELDS gene.
    """

    out = copy.deepcopy(dict(base_rulebook_dict or {}))
    gene = exit_gene if isinstance(exit_gene, Mapping) else {}
    for field in EXIT_FIELDS:
        if field in gene:
            out[field] = copy.deepcopy(gene[field])
    return out


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _percentile(sorted_values: Sequence[float], percentile: float) -> float | None:
    if not sorted_values:
        return None
    values = list(sorted_values)
    if len(values) == 1:
        return float(values[0])
    rank = (len(values) - 1) * float(percentile) / 100.0
    lo = int(rank)
    hi = min(lo + 1, len(values) - 1)
    if lo == hi:
        return float(values[lo])
    weight = rank - lo
    return float(values[lo] * (1.0 - weight) + values[hi] * weight)


def holding_days_summary(trades: list[dict[str, Any]] | None) -> dict[str, float | int | None]:
    """Summarize holding_days from trade dictionaries.

    Returns count/mean/median/p75/p90/max. Empty or missing values are safe and
    return count=0 with numeric summaries as None.
    """

    values: list[float] = []
    for trade in trades or []:
        if not isinstance(trade, Mapping):
            continue
        value = _safe_float(trade.get("holding_days"))
        if value is not None:
            values.append(value)

    values.sort()
    count = len(values)
    if count == 0:
        return {"count": 0, "mean": None, "median": None, "p75": None, "p90": None, "max": None}

    return {
        "count": count,
        "mean": float(sum(values) / count),
        "median": _percentile(values, 50.0),
        "p75": _percentile(values, 75.0),
        "p90": _percentile(values, 90.0),
        "max": float(values[-1]),
    }


def _metric_value(metrics: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    if not isinstance(metrics, Mapping):
        return float(default)
    value = _safe_float(metrics.get(key))
    return float(default) if value is None else float(value)


def composite_exit_fitness(
    stress_metrics: Mapping[str, Any] | None,
    bull_metrics: Mapping[str, Any] | None,
    holding_summary: Mapping[str, Any] | None,
    weights: ExitFitnessWeights = DEFAULT_EXIT_FITNESS_WEIGHTS,
) -> float:
    """Compute provisional Stage 3 exit-GA composite fitness.

    Formula intent::

        bull_exp_term
        + w_downside * min(stress_exp, bull_exp)
        - w_bull_floor_penalty * max(0, bull_floor - bull_exp)
        - w_stress_mdd * abs(min(0, stress_mdd))
        - w_holding * max(0, median_holding - holding_soft_cap)

    This is a first-pass scaffold only. Coefficients are exposed in
    ExitFitnessWeights and must be tuned after the first real Stage 3 experiment.
    """

    stress_exp = _metric_value(stress_metrics, "expectancy_pct", 0.0)
    bull_exp = _metric_value(bull_metrics, "expectancy_pct", 0.0)
    stress_mdd = _metric_value(stress_metrics, "max_drawdown_pct", 0.0)

    holding_data = holding_summary if isinstance(holding_summary, Mapping) else {}
    median_holding = _safe_float(holding_data.get("median", holding_data.get("median_holding_days")))
    if median_holding is None:
        median_holding = 0.0

    downside_term = min(stress_exp, bull_exp)
    bull_floor_penalty = max(0.0, float(weights.bull_floor) - bull_exp)
    stress_mdd_abs = abs(min(0.0, stress_mdd))
    holding_excess = max(0.0, median_holding - float(weights.holding_soft_cap))

    fitness = (
        bull_exp
        + float(weights.w_downside) * downside_term
        - float(weights.w_bull_floor_penalty) * bull_floor_penalty
        - float(weights.w_stress_mdd) * stress_mdd_abs
        - float(weights.w_holding) * holding_excess
    )
    return float(fitness)


__all__ = [
    "EXIT_NUMERIC",
    "EXIT_CATEGORICAL",
    "EXIT_FIELDS",
    "ExitFitnessWeights",
    "DEFAULT_EXIT_FITNESS_WEIGHTS",
    "apply_exit",
    "holding_days_summary",
    "composite_exit_fitness",
]
