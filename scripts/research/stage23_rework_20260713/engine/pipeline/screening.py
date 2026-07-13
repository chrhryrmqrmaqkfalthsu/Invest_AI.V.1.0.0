"""Screening stage for the new staged pipeline.

The screening stage is intentionally loose. It removes clearly untradeable or
bad-data tickers, while leaving real quality judgement to rolling validation.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable
from uuid import uuid4

import pandas as pd

from engine.core.feature_lag import FEATURE_LAG_METADATA
from engine.core.metadata import build_metadata
from engine.learning.backtest import run_backtest
from engine.pipeline.context import DEFAULT_ROLLING_YEARS, prepare_ticker_context

# ---------------------------------------------------------------------------
# PROVISIONAL — distribution check 후 확정할 screening 임계값.
# ---------------------------------------------------------------------------
MIN_ADV_USD = 25_000_000.0
MIN_ROWS = 756
MIN_SPLIT_COUNT = 2
MAX_CLOSE_NA_RATIO = 0.05
MAX_VOLUME_NA_RATIO = 0.05
MAX_INVALID_PRICE_VOLUME_RATIO = 0.05
STALE_DATA_CUTOFF = "2025-01-01"

# PROVISIONAL viability thresholds. Very loose to minimize false negatives.
WEAK_TRADE_COUNT = 3
WEAK_EXPECTANCY_FLOOR_PCT = -3.0
VIABILITY_START_DATE = "2020-01-01"
VIABILITY_END_DATE = "2025-12-31"
DEFAULT_POSITION_LIMIT_KRW = 120_000.0

ViabilityRunner = Callable[[dict[str, Any]], dict[str, Any]]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if pd.isna(v):
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


def _liquidity_weight(adv_usd: Any) -> float:
    adv = _safe_float(adv_usd, 0.0)
    if adv < MIN_ADV_USD:
        return 0.0
    if adv < 100_000_000.0:
        return 0.90
    return 1.0


def _data_block(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "rows": _safe_int(ctx.get("rows", 0)),
        "data_start": ctx.get("data_start") or ctx.get("data_min") or "",
        "data_end": ctx.get("data_end") or ctx.get("data_max") or "",
        "valid_close_ratio": _safe_float(ctx.get("valid_close_ratio"), 0.0),
        "valid_volume_ratio": _safe_float(ctx.get("valid_volume_ratio"), 0.0),
        "invalid_price_volume_ratio": _safe_float(ctx.get("invalid_price_volume_ratio"), 1.0),
        "split_count": _safe_int(ctx.get("split_count", len(ctx.get("splits") or []))),
    }


def _sentiment_block(ctx: dict[str, Any]) -> dict[str, Any]:
    days = _safe_int(ctx.get("sentiment_days", 0))
    return {
        "sentiment_days": days,
        "has_sentiment": days > 0,
    }


def _base_result(
    *,
    ticker: str,
    ctx: dict[str, Any],
    run_id: str,
    passed: bool,
    status: str,
    reason_code: str,
    viability: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _data_block(ctx)
    result = {
        "ticker": ticker,
        "stage": "screening",
        "run_id": run_id,
        "passed": bool(passed),
        "status": status,
        "reason_code": reason_code,
        "adv_usd_252d": _safe_float(ctx.get("adv_usd_252d"), 0.0),
        "liquidity_weight": _liquidity_weight(ctx.get("adv_usd_252d")),
        "data": data,
        "sentiment": _sentiment_block(ctx),
        "viability": viability or {"executed": False},
        "error": error or {},
    }
    result["_meta"] = build_metadata(
        source="pipeline_v1.screening",
        ticker=ticker,
        fitness_mode="swing",
        data_start=data.get("data_start"),
        data_end=data.get("data_end"),
        validation={
            "stage": "screening",
            "passed": bool(passed),
            "status": status,
            "reason_code": reason_code,
            "adv_usd_252d": result["adv_usd_252d"],
            "liquidity_weight": result["liquidity_weight"],
            "data": data,
            "sentiment": result["sentiment"],
            "viability": result["viability"],
        },
        feature_lag=FEATURE_LAG_METADATA,
        run_id=run_id,
    )
    return result


def check_data_gates(ctx: dict[str, Any]) -> str:
    """Return empty string if data/liquidity gates pass, else reason code."""
    adv = _safe_float(ctx.get("adv_usd_252d"), 0.0)
    if adv < MIN_ADV_USD:
        return "ADV_BELOW_MIN"

    data = _data_block(ctx)
    if data["rows"] < MIN_ROWS:
        return "INSUFFICIENT_ROWS"

    try:
        data_end = pd.Timestamp(data["data_end"])
        if pd.isna(data_end) or data_end < pd.Timestamp(STALE_DATA_CUTOFF):
            return "STALE_DATA"
    except Exception:
        return "STALE_DATA"

    if data["split_count"] < MIN_SPLIT_COUNT:
        return "INSUFFICIENT_SPLITS"

    if data["valid_close_ratio"] < 1.0 - MAX_CLOSE_NA_RATIO:
        return "CLOSE_NA_TOO_HIGH"

    if data["valid_volume_ratio"] < 1.0 - MAX_VOLUME_NA_RATIO:
        return "VOLUME_NA_TOO_HIGH"

    if data["invalid_price_volume_ratio"] > MAX_INVALID_PRICE_VOLUME_RATIO:
        return "INVALID_PRICE_OR_VOLUME"

    return ""


def run_default_viability_backtest(ctx: dict[str, Any]) -> dict[str, Any]:
    """Run one cheap default-rulebook backtest over 2020-2025."""
    result = run_backtest(
        ctx["base_rulebook"],
        ctx["df"],
        position_limit_krw=DEFAULT_POSITION_LIMIT_KRW,
        market_history_df=ctx.get("market_history_df"),
        sector_name=ctx.get("sector_name", "tech"),
        start_date=VIABILITY_START_DATE,
        end_date=VIABILITY_END_DATE,
        ticker_sentiment=ctx.get("ticker_sentiment"),
        fitness_mode="swing",
    )
    return {
        "executed": True,
        "method": "default_rulebook_backtest",
        "period": [VIABILITY_START_DATE, VIABILITY_END_DATE],
        "trade_count": int(result.trade_count or 0),
        "win_rate": _safe_float(result.win_rate, 0.0),
        "expectancy_pct": _safe_float(result.expectancy_pct, 0.0),
        "profit_factor": _safe_float(result.profit_factor, 0.0),
        "max_drawdown_pct": _safe_float(result.max_drawdown_pct, 0.0),
        "fitness": _safe_float(result.fitness, 0.0),
    }


def check_viability(viability: dict[str, Any]) -> str:
    """Return empty string if viability is acceptable, else LOW_VIABILITY."""
    trades = _safe_int(viability.get("trade_count", 0))
    expectancy = _safe_float(viability.get("expectancy_pct"), 0.0)
    if trades <= 0:
        return "LOW_VIABILITY"
    if trades < WEAK_TRADE_COUNT and expectancy < WEAK_EXPECTANCY_FLOOR_PCT:
        return "LOW_VIABILITY"
    return ""


def run_screening(
    ticker: str,
    context: dict[str, Any] | None = None,
    run_viability: bool = True,
    viability_runner: ViabilityRunner | None = None,
    include_context: bool = False,
) -> dict[str, Any]:
    """Run the loose screening stage for one ticker.

    Data/liquidity gates run before the viability backtest. If a ticker fails a
    cheap gate, the viability backtest is skipped.
    """
    run_id = str(uuid4())
    ticker = str(ticker or "").upper().strip()
    ctx: dict[str, Any] = {}
    try:
        ctx = context or prepare_ticker_context(ticker)
        reason = check_data_gates(ctx)
        if reason:
            result = _base_result(
                ticker=ticker,
                ctx=ctx,
                run_id=run_id,
                passed=False,
                status="FAIL",
                reason_code=reason,
                viability={"executed": False, "skipped_reason": reason},
            )
        else:
            viability = {"executed": False, "skipped_reason": "run_viability_disabled"}
            if run_viability:
                runner = viability_runner or run_default_viability_backtest
                viability = runner(ctx)
                viability.setdefault("executed", True)
            viability_reason = check_viability(viability) if run_viability else ""
            result = _base_result(
                ticker=ticker,
                ctx=ctx,
                run_id=run_id,
                passed=not bool(viability_reason),
                status="PASS" if not viability_reason else "FAIL",
                reason_code=viability_reason,
                viability=viability,
            )
        if include_context:
            result["_context"] = ctx
        return result
    except Exception as exc:
        error = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        result = _base_result(
            ticker=ticker,
            ctx=ctx or {"ticker": ticker},
            run_id=run_id,
            passed=False,
            status="ERROR",
            reason_code="ERROR",
            viability={"executed": False, "skipped_reason": "ERROR"},
            error=error,
        )
        if include_context and ctx:
            result["_context"] = ctx
        return result


__all__ = [
    "MIN_ADV_USD",
    "MIN_ROWS",
    "MIN_SPLIT_COUNT",
    "MAX_CLOSE_NA_RATIO",
    "MAX_VOLUME_NA_RATIO",
    "MAX_INVALID_PRICE_VOLUME_RATIO",
    "check_data_gates",
    "check_viability",
    "run_default_viability_backtest",
    "run_screening",
]
