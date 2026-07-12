"""Strict-AND interval signal evaluator for the redesign workspace.

Entry is a boolean conjunction of five normalized technical features.  Market
context never changes the boolean decision; it only scales a separate position
quality score.  The production evaluator remains untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Optional

import numpy as np
import pandas as pd

from engine.core.logger import get_logger
from engine.strategies.rulebook import (
    ENTRY_FEATURES,
    Rulebook,
    validate_rulebook_intervals,
)

log = get_logger("strict_interval_evaluator")

STRICT_INTERVAL_FEATURE_LAG_DAYS = 5
MIN_HISTORY_ROWS = 60 + STRICT_INTERVAL_FEATURE_LAG_DAYS


@dataclass
class SignalResult:
    should_buy: bool
    score: float
    raw_score: float
    threshold: float
    reasons: list[str]
    market_adjustment: float
    components: dict[str, Any]
    feature_values: dict[str, float] | None = None
    interval_pass: dict[str, bool] | None = None
    interval_margin: dict[str, float] | None = None
    feature_lag_days: int = STRICT_INTERVAL_FEATURE_LAG_DAYS


def _safe_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _clip01(value: float) -> float:
    return float(max(0.0, min(1.0, value)))


def _linear_normalize(value: float, low: float, high: float) -> float:
    if high <= low:
        raise ValueError("normalization domain must be bilateral")
    return _clip01((float(value) - float(low)) / (float(high) - float(low)))


def extract_normalized_entry_features(
    df: pd.DataFrame,
    *,
    direction: str = "long",
    feature_lag_days: int = STRICT_INTERVAL_FEATURE_LAG_DAYS,
) -> tuple[dict[str, float] | None, str | None]:
    """Extract continuous Phase-1 features from the D-5 completed row.

    Fixed transforms keep every feature in [0, 1].  Actual interval reachability
    is then enforced by the GA's thin-sample fitness and Phase-3 coverage audit.
    """
    lag = int(feature_lag_days)
    if df is None or len(df) <= lag:
        return None, "insufficient_data"
    row = df.iloc[-1 - lag]

    close = _safe_float(row.get("Close"))
    ma5 = _safe_float(row.get("MA5"))
    ma20 = _safe_float(row.get("MA20"))
    ma60 = _safe_float(row.get("MA60"))
    macd = _safe_float(row.get("MACD"))
    macd_signal = _safe_float(row.get("MACD_signal"))
    rsi = _safe_float(row.get("RSI"))
    bb_lower = _safe_float(row.get("BB_lower"))
    bb_upper = _safe_float(row.get("BB_upper"))
    volume_ratio = _safe_float(row.get("Volume_ratio"))

    required = [close, ma5, ma20, ma60, macd, macd_signal, rsi, bb_lower, bb_upper, volume_ratio]
    if any(value is None for value in required) or close is None or close <= 0:
        return None, "nonfinite_feature"
    assert ma5 is not None and ma20 is not None and ma60 is not None
    assert macd is not None and macd_signal is not None and rsi is not None
    assert bb_lower is not None and bb_upper is not None and volume_ratio is not None
    if ma20 == 0 or ma60 == 0 or bb_upper <= bb_lower:
        return None, "invalid_feature_domain"

    ma_spread_pct = 0.5 * (((ma5 / ma20) - 1.0) * 100.0 + ((ma20 / ma60) - 1.0) * 100.0)
    macd_hist_pct = ((macd - macd_signal) / close) * 100.0
    bb_position_raw = (close - bb_lower) / (bb_upper - bb_lower)

    values = {
        "ma_trend": _linear_normalize(ma_spread_pct, -10.0, 10.0),
        "macd_hist": _linear_normalize(macd_hist_pct, -5.0, 5.0),
        "rsi": _linear_normalize(rsi, 0.0, 100.0),
        "bb_position": _clip01(bb_position_raw),
        "volume_ratio": _linear_normalize(volume_ratio, 0.0, 5.0),
    }

    if str(direction or "long") == "short":
        for feature in ("ma_trend", "macd_hist", "rsi", "bb_position"):
            values[feature] = 1.0 - values[feature]

    if tuple(values) != ENTRY_FEATURES:
        return None, "feature_schema_mismatch"
    return values, None


def _interval_margin(value: float, low: float, high: float) -> float:
    width = high - low
    if width <= 0:
        return 0.0
    left = (value - low) / width
    right = (high - value) / width
    return _clip01(2.0 * min(left, right))


def _market_sizing_adjustment(
    rb: Rulebook,
    *,
    market_score: float,
    sector_score: float,
    vix_level: float,
) -> float:
    market_norm = (float(market_score) - 50.0) / 50.0
    sector_norm = (float(sector_score) - 50.0) / 50.0
    vix_norm = (18.0 - float(vix_level)) / 10.0
    correlation = (
        market_norm * float(rb.market_score_weight)
        + sector_norm * float(rb.sector_strength_weight)
        + vix_norm * float(rb.vix_sensitivity)
    )
    strength = max(0.0, min(1.0, float(rb.market_adjustment_strength)))
    adjustment = 1.0 + max(min(correlation * strength, strength), -strength)
    if not bool(getattr(rb, "use_market_entry_adjustment", True)):
        return 1.0
    return float(adjustment)


def evaluate_signal(
    rb: Rulebook,
    df: pd.DataFrame,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    news_sentiment: float = 0.0,
    event_flags: Optional[dict] = None,
    topic_features: Optional[dict] = None,
) -> SignalResult:
    """Evaluate the latest signal date using D-5 strict-AND features."""
    if df is None or len(df) < MIN_HISTORY_ROWS:
        return SignalResult(
            False,
            0.0,
            0.0,
            1.0,
            ["insufficient_data"],
            1.0,
            {},
        )

    valid, reason = validate_rulebook_intervals(rb)
    if not valid:
        return SignalResult(
            False,
            0.0,
            0.0,
            1.0,
            [f"invalid_interval:{reason}"],
            1.0,
            {},
        )

    values, feature_error = extract_normalized_entry_features(
        df,
        direction=rb.direction,
        feature_lag_days=STRICT_INTERVAL_FEATURE_LAG_DAYS,
    )
    if values is None:
        return SignalResult(
            False,
            0.0,
            0.0,
            1.0,
            [feature_error or "feature_error"],
            1.0,
            {},
        )

    pass_map: dict[str, bool] = {}
    margin_map: dict[str, float] = {}
    components: dict[str, Any] = {}
    reasons: list[str] = []
    for feature in ENTRY_FEATURES:
        value = float(values[feature])
        pair: Mapping[str, float] = rb.entry_intervals[feature]
        low = float(pair["low"])
        high = float(pair["high"])
        passed = bool(isfinite(value) and low <= value <= high)
        margin = _interval_margin(value, low, high) if passed else 0.0
        pass_map[feature] = passed
        margin_map[feature] = margin
        components[feature] = {
            "value": value,
            "low": low,
            "high": high,
            "passed": passed,
            "margin": margin,
        }
        if passed:
            reasons.append(f"{feature}=PASS")
        else:
            reasons.append(f"{feature}=FAIL({value:.4f} not in [{low:.4f},{high:.4f}])")

    should_buy = all(pass_map.values())
    raw_quality = float(np.mean(list(margin_map.values()))) if should_buy else 0.0
    market_adjustment = _market_sizing_adjustment(
        rb,
        market_score=market_score,
        sector_score=sector_score,
        vix_level=vix_level,
    )
    sizing_quality = _clip01(raw_quality * market_adjustment)
    if market_adjustment != 1.0:
        reasons.append(f"sizing_market_adjustment={market_adjustment:.4f}")

    return SignalResult(
        should_buy=bool(should_buy),
        score=sizing_quality,
        raw_score=raw_quality,
        threshold=1.0,
        reasons=reasons,
        market_adjustment=market_adjustment,
        components=components,
        feature_values=values,
        interval_pass=pass_map,
        interval_margin=margin_map,
        feature_lag_days=STRICT_INTERVAL_FEATURE_LAG_DAYS,
    )


def calc_position_size_krw(
    rb: Rulebook,
    signal_quality: float,
    position_limit_krw: float,
) -> float:
    """Size a passed boolean signal independently from the entry decision."""
    strategy = str(rb.position_sizing_strategy or "fixed")
    base = max(0.0, min(1.0, float(rb.base_position_ratio)))
    quality = _clip01(float(signal_quality))

    if strategy == "fixed":
        ratio = base
    elif strategy == "signal_scaled":
        ratio = base * max(0.10, min(1.0, quality * float(rb.signal_multiplier)))
    elif strategy == "kelly_lite":
        win_rate = max(0.05, min(0.95, float(rb.win_rate) / 100.0))
        average = max(0.001, float(rb.avg_return_pct) / 100.0)
        kelly = win_rate - (1.0 - win_rate) / max(average, 0.01)
        ratio = max(0.20, min(1.0, kelly * base))
    else:
        ratio = base
    return float(position_limit_krw) * max(0.0, min(1.0, ratio))


def mean_daily_return_fitness(trades: list[Mapping[str, Any]] | None) -> float:
    """Method (a): mean of each trade's return divided by holding days."""
    efficiencies: list[float] = []
    for trade in list(trades or []):
        try:
            return_pct = float(trade.get("pnl_pct", 0.0))
            holding_days = max(int(trade.get("holding_days", 0) or 0), 1)
        except (TypeError, ValueError, AttributeError):
            continue
        if isfinite(return_pct):
            efficiencies.append(return_pct / float(holding_days))
    return float(np.mean(efficiencies)) if efficiencies else 0.0


def get_dynamic_exit_params(
    rb: Rulebook,
    market_score: float = 50.0,
    vix_level: float = 18.0,
) -> tuple[float, float, float]:
    """Compatibility shim: only stop-loss ATR remains active."""
    return float(rb.stop_loss_atr), 1_000_000.0, 1_000_000.0
