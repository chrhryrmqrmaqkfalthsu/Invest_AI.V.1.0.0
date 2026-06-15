"""Stage 3 qualification, eligibility, and profile helpers.

This module contains only pure gate/profile logic. It does not run GA or
backtests. Stage 3 단계4는 최종 개체를 죽이는 hard gate가 아니라, 최소
적격선(OOS expectancy)만 확인한 뒤 보유·리스크·수익 성격을 라벨링하는
카탈로그 생성 로직으로 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


STAGE3_FINAL_OOS_PERIODS: tuple[str, ...] = ("train_1", "train_2", "recent_1y")


@dataclass(frozen=True)
class Stage3QualifyConfig:
    """Ticker qualification thresholds for the Stage 3 pre-check.

    A ticker qualifies only when the required number of independent year rows
    pass all absolute criteria. The default is three years, all with expectancy
    at or above 2%.
    """

    min_trades: int = 5
    min_member_score: float = 10.0
    qualify_min_expectancy_pct: float = 2.0
    qualify_years: int = 3


@dataclass(frozen=True)
class Stage3ProfileConfig:
    """Stage 3 단계4 최소 적격선과 프로파일 경계값.

    임시 1차 기준이다. 단계4는 모든 순수 OOS 구간의 expectancy_pct가
    eligibility_min_expectancy_pct 이상인지로 적격 여부를 판단하고,
    진짜 forward OOS인 recent_1y에만 최소 거래 수를 요구한다.
    MDD와 보유일은 탈락 사유가 아니라 프로파일 라벨로만 사용한다.
    """

    eligibility_min_expectancy_pct: float = 1.0
    eligibility_min_trades: int = 5
    eligibility_min_trades_periods: tuple[str, ...] = ("recent_1y",)
    holding_ultra_short_max_days: float = 7.0
    holding_mid_max_days: float = 14.0
    low_mdd_floor_pct: float = -10.0
    mid_mdd_floor_pct: float = -20.0
    mid_exp_floor_pct: float = 2.0
    high_exp_floor_pct: float = 4.0

    @property
    def max_median_holding_days(self) -> float:
        """Backward-compatible alias for older display code."""
        return self.holding_ultra_short_max_days

    @property
    def oos_min_expectancy_pct(self) -> float:
        """Backward-compatible alias for older display code."""
        return self.eligibility_min_expectancy_pct

    @property
    def min_expectancy_pct(self) -> float:
        """Backward-compatible alias for older callers."""
        return self.eligibility_min_expectancy_pct


# Backward-compatible name. The semantics are now profile-oriented, not hard final gating.
Stage3FinalConfig = Stage3ProfileConfig


DEFAULT_STAGE3_QUALIFY = Stage3QualifyConfig()
DEFAULT_STAGE3_PROFILE = Stage3ProfileConfig()
DEFAULT_STAGE3_FINAL = DEFAULT_STAGE3_PROFILE


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


def _safe_int(value: Any) -> int | None:
    number = _safe_float(value)
    return None if number is None else int(number)


def _append_reason(
    reasons: list[dict[str, Any]],
    *,
    metric: str,
    value: Any,
    threshold: Any,
    rule: str = ">=",
    year: str | None = None,
    period: str | None = None,
    reason: str | None = None,
) -> None:
    row: dict[str, Any] = {
        "metric": metric,
        "value": value,
        "threshold": threshold,
        "rule": rule,
    }
    if year is not None:
        row["year"] = year
    if period is not None:
        row["period"] = period
    if reason is not None:
        row["reason"] = reason
    reasons.append(row)


def _year_items(per_year_metrics: Mapping[str, Any] | None) -> list[tuple[str, Mapping[str, Any]]]:
    if not isinstance(per_year_metrics, Mapping):
        return []
    out: list[tuple[str, Mapping[str, Any]]] = []
    for year, metrics in per_year_metrics.items():
        if isinstance(metrics, Mapping):
            out.append((str(year), metrics))
        else:
            out.append((str(year), {}))
    return out


def stage3_qualify_fail_reasons(
    per_year_metrics: Mapping[str, Any] | None,
    config: Stage3QualifyConfig = DEFAULT_STAGE3_QUALIFY,
) -> list[dict[str, Any]]:
    """Return qualification failures for independent per-year metrics.

    Expected input shape::

        {
            "2022": {"trade_count": 5, "member_score": 10.0, "expectancy_pct": 2.0},
            "2023": {...},
            "2024": {...},
        }

    Boundary rule: equality passes for all minimum thresholds.
    """

    reasons: list[dict[str, Any]] = []
    rows = _year_items(per_year_metrics)
    required_years = max(0, int(config.qualify_years))

    if len(rows) < required_years:
        _append_reason(
            reasons,
            metric="year_count",
            value=len(rows),
            threshold=required_years,
            rule=">=",
            reason="missing_required_years",
        )

    passed_years = 0
    for year, metrics in rows:
        year_failed = False

        trade_count = _safe_int(metrics.get("trade_count"))
        if trade_count is None or trade_count < config.min_trades:
            year_failed = True
            _append_reason(
                reasons,
                metric="trade_count",
                value=trade_count,
                threshold=config.min_trades,
                year=year,
            )

        member_score = _safe_float(metrics.get("member_score"))
        if member_score is None or member_score < config.min_member_score:
            year_failed = True
            _append_reason(
                reasons,
                metric="member_score",
                value=member_score,
                threshold=config.min_member_score,
                year=year,
            )

        expectancy_pct = _safe_float(metrics.get("expectancy_pct"))
        if expectancy_pct is None or expectancy_pct < config.qualify_min_expectancy_pct:
            year_failed = True
            _append_reason(
                reasons,
                metric="expectancy_pct",
                value=expectancy_pct,
                threshold=config.qualify_min_expectancy_pct,
                year=year,
            )

        if not year_failed:
            passed_years += 1

    if passed_years < required_years:
        _append_reason(
            reasons,
            metric="qualify_pass_count",
            value=passed_years,
            threshold=required_years,
            rule=">=",
        )

    return reasons


def _period_metrics(per_period_metrics: Mapping[str, Any] | None, period: str) -> Mapping[str, Any] | None:
    if not isinstance(per_period_metrics, Mapping):
        return None
    value = per_period_metrics.get(period)
    return value if isinstance(value, Mapping) else None


def _metric_snapshot(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    metrics = metrics or {}
    return {
        "expectancy_pct": _safe_float(metrics.get("expectancy_pct")),
        "max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
        "median_holding_days": _safe_float(metrics.get("median_holding_days", metrics.get("median"))),
    }


def stage3_basic_eligibility(
    per_period_metrics: Mapping[str, Any] | None,
    config: Stage3ProfileConfig = DEFAULT_STAGE3_PROFILE,
) -> list[dict[str, Any]]:
    """Return minimum eligibility failures for Stage 3 profile cataloging.

    Minimum eligibility line:
    train_1, train_2, and recent_1y must each have expectancy_pct >=
    eligibility_min_expectancy_pct. Missing periods also fail. Only periods in
    eligibility_min_trades_periods require trade_count >= eligibility_min_trades,
    so mixed/down-market checks can still treat no-trade behavior as normal.
    MDD and holding days are intentionally ignored here and used only by
    stage3_profile.

    Boundary rule: exact equality passes.
    """

    reasons: list[dict[str, Any]] = []
    min_trade_periods = tuple(str(p) for p in (config.eligibility_min_trades_periods or ()))
    for period in STAGE3_FINAL_OOS_PERIODS:
        metrics = _period_metrics(per_period_metrics, period)
        if metrics is None:
            _append_reason(
                reasons,
                metric="period",
                value=None,
                threshold="present",
                rule="exists",
                period=period,
                reason="missing_oos_period",
            )
            continue
        expectancy_pct = _safe_float(metrics.get("expectancy_pct"))
        if expectancy_pct is None or expectancy_pct < config.eligibility_min_expectancy_pct:
            _append_reason(
                reasons,
                metric="expectancy_pct",
                value=expectancy_pct,
                threshold=config.eligibility_min_expectancy_pct,
                period=period,
            )
        if period in min_trade_periods:
            trade_count = _safe_int(metrics.get("trade_count", 0))
            if trade_count is None or trade_count < config.eligibility_min_trades:
                _append_reason(
                    reasons,
                    metric="trade_count",
                    value=trade_count,
                    threshold=config.eligibility_min_trades,
                    period=period,
                    reason="below_min_trades_for_period",
                )
    return reasons


def _holding_class(median_holding_days: float | None, config: Stage3ProfileConfig) -> str:
    if median_holding_days is None:
        return "unknown_holding"
    if median_holding_days <= config.holding_ultra_short_max_days:
        return "ultra_short"
    if median_holding_days <= config.holding_mid_max_days:
        return "mid"
    return "long"


def _risk_class(max_drawdown_pct: float | None, config: Stage3ProfileConfig) -> str:
    if max_drawdown_pct is None:
        return "unknown_mdd"
    if max_drawdown_pct >= config.low_mdd_floor_pct:
        return "low_mdd"
    if max_drawdown_pct >= config.mid_mdd_floor_pct:
        return "mid_mdd"
    return "high_mdd"


def _return_class(expectancy_pct: float | None, config: Stage3ProfileConfig) -> str:
    if expectancy_pct is None:
        return "unknown_exp"
    if expectancy_pct >= config.high_exp_floor_pct:
        return "high_exp"
    if expectancy_pct >= config.mid_exp_floor_pct:
        return "mid_exp"
    return "low_exp"


def stage3_profile(
    per_period_metrics: Mapping[str, Any] | None,
    config: Stage3ProfileConfig = DEFAULT_STAGE3_PROFILE,
) -> dict[str, Any]:
    """Return Stage 3 profile labels and raw OOS metric snapshots.

    recent_1y is the representative period for labels. train_1/train_2 values
    are preserved in the returned period_metrics for later filtering and manual
    selection.
    """

    period_snapshots = {
        period: _metric_snapshot(_period_metrics(per_period_metrics, period))
        for period in STAGE3_FINAL_OOS_PERIODS
    }
    recent = period_snapshots["recent_1y"]
    holding = _holding_class(recent.get("median_holding_days"), config)
    risk = _risk_class(recent.get("max_drawdown_pct"), config)
    ret = _return_class(recent.get("expectancy_pct"), config)
    composite_tag = f"{holding}|{risk}|{ret}"
    return {
        "holding_class": holding,
        "risk_class": risk,
        "return_class": ret,
        "composite_tag": composite_tag,
        "period_metrics": period_snapshots,
        "config": {
            "eligibility_min_expectancy_pct": config.eligibility_min_expectancy_pct,
            "eligibility_min_trades": config.eligibility_min_trades,
            "eligibility_min_trades_periods": config.eligibility_min_trades_periods,
            "holding_ultra_short_max_days": config.holding_ultra_short_max_days,
            "holding_mid_max_days": config.holding_mid_max_days,
            "low_mdd_floor_pct": config.low_mdd_floor_pct,
            "mid_mdd_floor_pct": config.mid_mdd_floor_pct,
            "mid_exp_floor_pct": config.mid_exp_floor_pct,
            "high_exp_floor_pct": config.high_exp_floor_pct,
        },
    }


def stage3_final_fail_reasons(
    per_period_metrics: Mapping[str, Any] | None,
    config: Stage3ProfileConfig = DEFAULT_STAGE3_PROFILE,
) -> list[dict[str, Any]]:
    """Backward-compatible alias for the new minimum eligibility check.

    Stage 3 단계4는 더 이상 MDD/보유일 hard gate가 아니다. 기존 caller가 이
    함수를 호출해도 최소 적격선(expectancy)만 검사하도록 유지한다.
    """

    return stage3_basic_eligibility(per_period_metrics, config)


__all__ = [
    "STAGE3_FINAL_OOS_PERIODS",
    "Stage3QualifyConfig",
    "Stage3ProfileConfig",
    "Stage3FinalConfig",
    "DEFAULT_STAGE3_QUALIFY",
    "DEFAULT_STAGE3_PROFILE",
    "DEFAULT_STAGE3_FINAL",
    "stage3_qualify_fail_reasons",
    "stage3_basic_eligibility",
    "stage3_profile",
    "stage3_final_fail_reasons",
]
