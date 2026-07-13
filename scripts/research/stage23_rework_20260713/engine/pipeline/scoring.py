"""Scoring utilities for the new staged training pipeline.

All constants marked PROVISIONAL must be revisited after the first full rolling
result distribution is available. The module keeps raw metrics in every return
payload so scores can be recalculated without rerunning GA/backtests.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from statistics import mean
from typing import Any

# ---------------------------------------------------------------------------
# PROVISIONAL — 분포 확인 후 확정할 값. 한 곳에서만 바꾸도록 상단에 모은다.
# ---------------------------------------------------------------------------
OOS_MIN_TRADES = 5
OOS_MIN_WIN_RATE = 50.0
OOS_MIN_EXPECTANCY_PCT = 1.0
OOS_MIN_PROFIT_FACTOR = 1.2

MIN_ADV_USD = 25_000_000.0
FULL_LIQUIDITY_ADV_USD = 100_000_000.0
PARTIAL_LIQUIDITY_WEIGHT = 0.90
FULL_LIQUIDITY_WEIGHT = 1.00
EXCLUDED_LIQUIDITY_WEIGHT = 0.0

CONSISTENCY_SCORE_BY_PASS_COUNT = {
    0: 0.0,
    1: 20.0,
    2: 40.0,
    3: 60.0,
}

# PROVISIONAL quality scaling. quality_score is capped to 40.
QUALITY_EXPECTANCY_REF_PCT = 3.0
QUALITY_PROFIT_FACTOR_REF = 2.5
QUALITY_EXPECTANCY_WEIGHT = 20.0
QUALITY_PROFIT_FACTOR_WEIGHT = 20.0

# PROVISIONAL member qualification.
MEMBER_MIN_TRADE_COUNT = 10
MEMBER_MIN_EXPECTANCY_PCT = 0.0
MEMBER_MIN_PROFIT_FACTOR = 1.0

# PROVISIONAL member score weights. Sum should be 1.0.
MEMBER_SCORE_EXPECTANCY_WEIGHT = 0.70
MEMBER_SCORE_PROFIT_FACTOR_WEIGHT = 0.20
MEMBER_SCORE_WIN_RATE_WEIGHT = 0.00
MEMBER_SCORE_DRAWDOWN_WEIGHT = 0.10

METRIC_ALIASES = {
    "trade_count": ("trade_count", "trades", "oos_trades", "train_trades"),
    "win_rate": ("win_rate", "oos_win_rate", "train_win_rate"),
    "expectancy_pct": (
        "expectancy_pct",
        "avg_return_pct",
        "oos_expectancy_pct",
        "train_expectancy_pct",
    ),
    "profit_factor": ("profit_factor", "oos_profit_factor", "train_profit_factor"),
    "max_drawdown_pct": (
        "max_drawdown_pct",
        "mdd_pct",
        "oos_max_drawdown_pct",
        "train_max_drawdown_pct",
    ),
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if not isfinite(v):
            return default
        return v
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _first_present(mapping: dict, aliases: tuple[str, ...], default: Any = None) -> Any:
    for key in aliases:
        if key in mapping:
            return mapping.get(key)
    return default


def _extract_oos_dict(period_or_oos: Any) -> dict[str, Any]:
    if period_or_oos is None:
        return {}
    if hasattr(period_or_oos, "to_dict") and callable(period_or_oos.to_dict):
        try:
            period_or_oos = period_or_oos.to_dict()
        except Exception:
            pass
    if hasattr(period_or_oos, "__dict__") and not isinstance(period_or_oos, dict):
        period_or_oos = {
            k: v for k, v in vars(period_or_oos).items() if not str(k).startswith("_")
        }
    if not isinstance(period_or_oos, dict):
        return {}
    inner = period_or_oos.get("oos")
    if isinstance(inner, dict):
        merged = dict(period_or_oos)
        merged.update(inner)
        return merged
    return dict(period_or_oos)


def _extract_metrics(item: Any) -> dict[str, float | int]:
    data = _extract_oos_dict(item)
    return {
        "trade_count": _safe_int(_first_present(data, METRIC_ALIASES["trade_count"], 0)),
        "win_rate": _safe_float(_first_present(data, METRIC_ALIASES["win_rate"], 0.0)),
        "expectancy_pct": _safe_float(_first_present(data, METRIC_ALIASES["expectancy_pct"], 0.0)),
        "profit_factor": _safe_float(_first_present(data, METRIC_ALIASES["profit_factor"], 0.0)),
        "max_drawdown_pct": _safe_float(_first_present(data, METRIC_ALIASES["max_drawdown_pct"], 0.0)),
    }


def _liquidity_weight(adv_usd_252d: Any) -> float:
    adv = _safe_float(adv_usd_252d, 0.0)
    if adv < MIN_ADV_USD:
        return EXCLUDED_LIQUIDITY_WEIGHT
    if adv < FULL_LIQUIDITY_ADV_USD:
        return PARTIAL_LIQUIDITY_WEIGHT
    return FULL_LIQUIDITY_WEIGHT


def _provisional_quality_score(pass_metrics: list[dict[str, float | int]], quality_scale: dict | None) -> float:
    if not pass_metrics:
        return 0.0

    avg_expectancy = mean(float(m["expectancy_pct"]) for m in pass_metrics)
    avg_pf = mean(float(m["profit_factor"]) for m in pass_metrics)

    scale = quality_scale or {}
    exp_ref = _safe_float(scale.get("expectancy_ref_pct"), QUALITY_EXPECTANCY_REF_PCT)
    pf_ref = _safe_float(scale.get("profit_factor_ref"), QUALITY_PROFIT_FACTOR_REF)
    exp_weight = _safe_float(scale.get("expectancy_weight"), QUALITY_EXPECTANCY_WEIGHT)
    pf_weight = _safe_float(scale.get("profit_factor_weight"), QUALITY_PROFIT_FACTOR_WEIGHT)

    exp_part = _clamp(max(0.0, avg_expectancy) / max(exp_ref, 1e-9), 0.0, 1.0) * exp_weight
    pf_part = _clamp(max(0.0, avg_pf - 1.0) / max(pf_ref - 1.0, 1e-9), 0.0, 1.0) * pf_weight
    return _clamp(exp_part + pf_part, 0.0, 40.0)


def _percentile_ranks(values: list[float], higher_is_better: bool = True) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    indexed = list(enumerate(values))
    indexed.sort(key=lambda x: x[1], reverse=not higher_is_better)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (n - 1)
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = pct
        i = j + 1
    return ranks


def is_oos_year_pass(oos: Any) -> bool:
    """Return True if an OOS year passes the SPEC criteria."""
    metrics = _extract_metrics(oos)
    return (
        int(metrics["trade_count"]) >= OOS_MIN_TRADES
        and float(metrics["win_rate"]) > OOS_MIN_WIN_RATE
        and float(metrics["expectancy_pct"]) > OOS_MIN_EXPECTANCY_PCT
        and float(metrics["profit_factor"]) > OOS_MIN_PROFIT_FACTOR
    )


def score_stock_from_rolling(
    periods: list[Any] | tuple[Any, ...] | None,
    adv_usd_252d: Any,
    quality_scale: dict | None = None,
) -> dict[str, Any]:
    """Score a ticker from rolling OOS periods.

    The quality formula is PROVISIONAL. Raw metrics are returned so the score can
    be recalculated after the full rolling distribution is reviewed.
    """
    period_items = list(periods or [])
    period_results: list[dict[str, Any]] = []
    pass_metrics: list[dict[str, float | int]] = []

    for idx, period in enumerate(period_items):
        data = _extract_oos_dict(period)
        metrics = _extract_metrics(data)
        passed = is_oos_year_pass(metrics)
        if passed:
            pass_metrics.append(metrics)
        period_results.append(
            {
                "year": data.get("year", idx + 1) if isinstance(data, dict) else idx + 1,
                "pass": passed,
                "metrics": metrics,
            }
        )

    pass_count = sum(1 for p in period_results if p["pass"])
    consistency = CONSISTENCY_SCORE_BY_PASS_COUNT.get(pass_count, 60.0 if pass_count >= 3 else 0.0)
    quality = _provisional_quality_score(pass_metrics, quality_scale)
    liquidity = _liquidity_weight(adv_usd_252d)
    raw_score = consistency + quality
    stock_score = raw_score * liquidity
    excluded = liquidity <= 0.0 or pass_count <= 0
    if excluded:
        stock_score = 0.0

    all_metrics = [p["metrics"] for p in period_results]

    def avg(key: str, source: list[dict[str, float | int]]) -> float:
        return float(mean(float(m[key]) for m in source)) if source else 0.0

    raw_metrics = {
        "period_count": len(period_results),
        "pass_count": pass_count,
        "avg_trade_count_all": avg("trade_count", all_metrics),
        "avg_win_rate_all": avg("win_rate", all_metrics),
        "avg_expectancy_pct_all": avg("expectancy_pct", all_metrics),
        "avg_profit_factor_all": avg("profit_factor", all_metrics),
        "avg_max_drawdown_pct_all": avg("max_drawdown_pct", all_metrics),
        "avg_trade_count_pass": avg("trade_count", pass_metrics),
        "avg_win_rate_pass": avg("win_rate", pass_metrics),
        "avg_expectancy_pct_pass": avg("expectancy_pct", pass_metrics),
        "avg_profit_factor_pass": avg("profit_factor", pass_metrics),
        "avg_max_drawdown_pct_pass": avg("max_drawdown_pct", pass_metrics),
        "adv_usd_252d": _safe_float(adv_usd_252d, 0.0),
    }

    return {
        "stock_score": round(float(stock_score), 6),
        "raw_stock_score": round(float(raw_score), 6),
        "consistency_score": round(float(consistency), 6),
        "quality_score": round(float(quality), 6),
        "quality_provisional": True,
        "liquidity_weight": round(float(liquidity), 6),
        "excluded": bool(excluded),
        "exclude_reason": "ADV_BELOW_MIN" if liquidity <= 0.0 else ("NO_OOS_PASS" if pass_count <= 0 else ""),
        "raw_metrics": raw_metrics,
        "periods": period_results,
        "criteria": {
            "oos_min_trades": OOS_MIN_TRADES,
            "oos_min_win_rate": OOS_MIN_WIN_RATE,
            "oos_min_expectancy_pct": OOS_MIN_EXPECTANCY_PCT,
            "oos_min_profit_factor": OOS_MIN_PROFIT_FACTOR,
            "min_adv_usd": MIN_ADV_USD,
            "full_liquidity_adv_usd": FULL_LIQUIDITY_ADV_USD,
        },
    }


def _member_payload(member: Any) -> dict[str, Any]:
    if member is None:
        return {}
    if hasattr(member, "to_dict") and callable(member.to_dict):
        try:
            d = member.to_dict()
            if isinstance(d, dict):
                return deepcopy(d)
        except Exception:
            pass
    if hasattr(member, "__dict__") and not isinstance(member, dict):
        return {k: deepcopy(v) for k, v in vars(member).items() if not str(k).startswith("_")}
    return deepcopy(member) if isinstance(member, dict) else {"value": str(member)}


def _is_member_qualified(metrics: dict[str, float | int]) -> bool:
    return (
        int(metrics["trade_count"]) >= MEMBER_MIN_TRADE_COUNT
        and float(metrics["expectancy_pct"]) > MEMBER_MIN_EXPECTANCY_PCT
        and float(metrics["profit_factor"]) > MEMBER_MIN_PROFIT_FACTOR
    )


def score_full_training_members(
    members: list[Any] | tuple[Any, ...] | None,
    score_scale: dict | None = None,
) -> list[dict[str, Any]]:
    """Score full-training members and preserve every member.

    Qualification thresholds and member_score formula are PROVISIONAL. Members
    that fail qualification are returned with qualified=False for distribution
    analysis instead of being discarded.
    """
    items = list(members or [])
    payloads = [_member_payload(m) for m in items]
    metrics_list = [_extract_metrics(p) for p in payloads]
    n = len(payloads)
    if n == 0:
        return []

    expectancies = [float(m["expectancy_pct"]) for m in metrics_list]
    pfs = [float(m["profit_factor"]) for m in metrics_list]
    win_rates = [float(m["win_rate"]) for m in metrics_list]
    drawdown_quality = [-abs(float(m["max_drawdown_pct"])) for m in metrics_list]

    exp_rank = _percentile_ranks(expectancies, higher_is_better=True)
    pf_rank = _percentile_ranks(pfs, higher_is_better=True)
    wr_rank = _percentile_ranks(win_rates, higher_is_better=True)
    dd_rank = _percentile_ranks(drawdown_quality, higher_is_better=True)

    scale = score_scale or {}
    w_exp = _safe_float(scale.get("expectancy_weight"), MEMBER_SCORE_EXPECTANCY_WEIGHT)
    w_pf = _safe_float(scale.get("profit_factor_weight"), MEMBER_SCORE_PROFIT_FACTOR_WEIGHT)
    w_wr = _safe_float(scale.get("win_rate_weight"), MEMBER_SCORE_WIN_RATE_WEIGHT)
    w_dd = _safe_float(scale.get("drawdown_weight"), MEMBER_SCORE_DRAWDOWN_WEIGHT)
    total_w = max(w_exp + w_pf + w_wr + w_dd, 1e-9)

    out: list[dict[str, Any]] = []
    for idx, payload in enumerate(payloads):
        metrics = metrics_list[idx]
        score = (
            exp_rank[idx] * w_exp
            + pf_rank[idx] * w_pf
            + wr_rank[idx] * w_wr
            + dd_rank[idx] * w_dd
        ) / total_w
        score = _clamp(float(score), 0.0, 1.0)
        qualified = _is_member_qualified(metrics)
        row = dict(payload)
        row.update(
            {
                "rank": payload.get("rank", idx + 1),
                "qualified": bool(qualified),
                "member_score": round(score, 6),
                "member_score_provisional": True,
                "train_metrics": metrics,
                "qualification": {
                    "min_trade_count": MEMBER_MIN_TRADE_COUNT,
                    "min_expectancy_pct": MEMBER_MIN_EXPECTANCY_PCT,
                    "min_profit_factor": MEMBER_MIN_PROFIT_FACTOR,
                    "passed": bool(qualified),
                },
                "score_components": {
                    "expectancy_percentile": round(exp_rank[idx], 6),
                    "profit_factor_percentile": round(pf_rank[idx], 6),
                    "win_rate_percentile": round(wr_rank[idx], 6),
                    "drawdown_percentile": round(dd_rank[idx], 6),
                },
            }
        )
        out.append(row)
    return out
