"""Stage 2 absolute-evaluation gate helpers.

This module centralizes the finalized Stage 2 gate so future research scripts do
not have to rediscover or re-encode the thresholds.

MDD sign convention: drawdowns are stored as percentages where deeper drawdown
is more negative.  For example, an MDD cap of -20.0 means -20.0% passes and
-20.01% fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Stage2GateConfig:
    min_trades: int = 5
    min_member_score: float = 10.0
    train_min_expectancy_pct: float = 1.0
    stress_min_expectancy_pct: float = 1.0
    stress_min_mdd_pct: float = -20.0
    stress_min_return_mdd_ratio: float = 1.0
    oos_min_expectancy_pct: float = 1.0
    oos_min_mdd_pct: float = -15.0
    oos_keep_trade_member_gate: bool = True


DEFAULT_STAGE2_GATE = Stage2GateConfig()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return None if number is None else int(number)


def _append_min_reason(
    reasons: list[dict[str, Any]],
    *,
    metric: str,
    value: float | int | None,
    threshold: float | int,
    rule: str = ">=",
) -> None:
    reasons.append(
        {
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "rule": rule,
        }
    )


def _check_trade_member_gate(
    reasons: list[dict[str, Any]],
    metrics: dict[str, Any],
    config: Stage2GateConfig,
) -> None:
    trade_count = _safe_int(metrics.get("trade_count"))
    if trade_count is None or trade_count < config.min_trades:
        _append_min_reason(
            reasons,
            metric="trade_count",
            value=trade_count,
            threshold=config.min_trades,
        )

    member_score = _safe_float(metrics.get("member_score"))
    if member_score is None or member_score < config.min_member_score:
        _append_min_reason(
            reasons,
            metric="member_score",
            value=member_score,
            threshold=config.min_member_score,
        )


def _period_family(period_kind: str) -> str:
    kind = str(period_kind or "").strip().lower()
    if kind == "stress" or "stress" in kind:
        return "stress"
    if kind == "oos" or kind.startswith("oos_") or "oos" in kind:
        return "oos"
    return "train"


def stage2_fail_reasons(
    metrics: dict[str, Any],
    period_kind: str,
    config: Stage2GateConfig = DEFAULT_STAGE2_GATE,
) -> list[dict[str, Any]]:
    """Return Stage 2 gate failures for one period metric row.

    An empty list means the row passes.  Missing fields are treated as explicit
    failures instead of raising KeyError, so CSV rechecks can identify bad or
    incomplete rows safely.

    Boundary rules:
    * stress MDD passes at exactly -20.0; ratio must be strictly greater than 1.0.
    * OOS MDD passes at exactly -15.0.
    * Train/general gates pass at exact threshold equality.
    """
    reasons: list[dict[str, Any]] = []
    family = _period_family(period_kind)

    expectancy_pct = _safe_float(metrics.get("expectancy_pct"))
    max_drawdown_pct = _safe_float(metrics.get("max_drawdown_pct"))
    trade_count = _safe_int(metrics.get("trade_count"))

    if family == "stress":
        if expectancy_pct is None or expectancy_pct < config.stress_min_expectancy_pct:
            _append_min_reason(
                reasons,
                metric="expectancy_pct",
                value=expectancy_pct,
                threshold=config.stress_min_expectancy_pct,
            )

        if max_drawdown_pct is None or max_drawdown_pct < config.stress_min_mdd_pct:
            _append_min_reason(
                reasons,
                metric="max_drawdown_pct",
                value=max_drawdown_pct,
                threshold=config.stress_min_mdd_pct,
            )

        if expectancy_pct is None or trade_count is None or max_drawdown_pct in (None, 0.0):
            reasons.append(
                {
                    "metric": "stress_return_mdd_ratio",
                    "value": None,
                    "threshold": config.stress_min_return_mdd_ratio,
                    "rule": ">",
                    "reason": "ratio_unavailable",
                }
            )
        else:
            cumulative_return_pct = expectancy_pct * trade_count
            ratio = cumulative_return_pct / abs(max_drawdown_pct)
            if ratio <= config.stress_min_return_mdd_ratio:
                reasons.append(
                    {
                        "metric": "stress_return_mdd_ratio",
                        "value": ratio,
                        "threshold": config.stress_min_return_mdd_ratio,
                        "rule": ">",
                    }
                )
        return reasons

    if family == "oos":
        if config.oos_keep_trade_member_gate:
            _check_trade_member_gate(reasons, metrics, config)

        if expectancy_pct is None or expectancy_pct < config.oos_min_expectancy_pct:
            _append_min_reason(
                reasons,
                metric="expectancy_pct",
                value=expectancy_pct,
                threshold=config.oos_min_expectancy_pct,
            )

        if max_drawdown_pct is None or max_drawdown_pct < config.oos_min_mdd_pct:
            _append_min_reason(
                reasons,
                metric="max_drawdown_pct",
                value=max_drawdown_pct,
                threshold=config.oos_min_mdd_pct,
            )
        return reasons

    _check_trade_member_gate(reasons, metrics, config)
    if expectancy_pct is None or expectancy_pct < config.train_min_expectancy_pct:
        _append_min_reason(
            reasons,
            metric="expectancy_pct",
            value=expectancy_pct,
            threshold=config.train_min_expectancy_pct,
        )
    return reasons


__all__ = ["DEFAULT_STAGE2_GATE", "Stage2GateConfig", "stage2_fail_reasons"]
