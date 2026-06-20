import pytest

from engine.central.backtester import BacktestResult, EquityPoint
from engine.central.policy_search import (
    recompute_multiseg_robust,
    robust_score_from_returns,
    split_equity_curve_to_segment_returns,
)


def _point(day: str, equity: float) -> EquityPoint:
    return EquityPoint(date=day, cash=equity, holdings_value=0.0, equity=equity, open_position_count=0)


def _compound(return_pcts):
    value = 1.0
    for ret in return_pcts:
        value *= 1.0 + ret / 100.0
    return (value - 1.0) * 100.0


def test_split_equity_curve_monthly_return_pct_uses_initial_equity_and_compounding():
    curve = [
        _point("2025-01-31", 11_000.0),
        _point("2025-02-28", 10_450.0),
        _point("2025-03-31", 11_286.0),
    ]

    segments = split_equity_curve_to_segment_returns(curve, granularity="monthly", initial_equity=10_000.0)

    assert [seg.label for seg in segments] == ["2025-01", "2025-02", "2025-03"]
    assert [seg.return_pct for seg in segments] == pytest.approx([10.0, -5.0, 8.0])
    assert segments[0].start_equity == pytest.approx(10_000.0)
    assert segments[0].end_equity == pytest.approx(11_000.0)
    assert segments[1].start_equity == pytest.approx(11_000.0)
    assert _compound([seg.return_pct for seg in segments]) == pytest.approx(12.86)


def test_split_equity_curve_marks_first_and_last_partial_segments():
    curve = [
        _point("2025-01-15", 10_500.0),
        _point("2025-01-31", 11_000.0),
        _point("2025-02-28", 10_450.0),
        _point("2025-03-14", 11_286.0),
    ]

    segments = split_equity_curve_to_segment_returns(curve, granularity="monthly", initial_equity=10_000.0)

    assert [seg.label for seg in segments] == ["2025-01", "2025-02", "2025-03"]
    assert segments[0].is_partial is True
    assert segments[1].is_partial is False
    assert segments[2].is_partial is True
    assert segments[0].trading_days == 2
    assert segments[2].start_date == "2025-03-14"
    assert segments[2].end_date == "2025-03-14"


def test_split_equity_curve_quarterly_count_and_return_pct():
    curve = [
        _point("2025-01-31", 11_000.0),
        _point("2025-02-28", 10_450.0),
        _point("2025-03-31", 11_286.0),
        _point("2025-04-30", 12_414.6),
    ]

    quarterly = split_equity_curve_to_segment_returns(curve, granularity="quarterly", initial_equity=10_000.0)
    monthly = split_equity_curve_to_segment_returns(curve, granularity="monthly", initial_equity=10_000.0)

    assert [seg.label for seg in quarterly] == ["2025-Q1", "2025-Q2"]
    assert quarterly[0].return_pct == pytest.approx(12.86)
    assert quarterly[1].return_pct == pytest.approx(10.0)
    assert len(monthly) == 4


def test_split_equity_curve_fail_closed_for_invalid_inputs():
    with pytest.raises(ValueError, match="at least two"):
        split_equity_curve_to_segment_returns([], granularity="monthly", initial_equity=10_000.0)
    with pytest.raises(ValueError, match="at least two"):
        split_equity_curve_to_segment_returns([_point("2025-01-31", 10_000.0)], granularity="monthly", initial_equity=10_000.0)
    with pytest.raises(ValueError, match="initial_equity"):
        split_equity_curve_to_segment_returns([_point("2025-01-30", 10_000.0), _point("2025-01-31", 10_100.0)], granularity="monthly", initial_equity=0.0)
    with pytest.raises(ValueError, match="unsupported granularity"):
        split_equity_curve_to_segment_returns([_point("2025-01-30", 10_000.0), _point("2025-01-31", 10_100.0)], granularity="weekly", initial_equity=10_000.0)


def test_recompute_multiseg_robust_matches_robust_score_from_returns():
    curve = [
        _point("2025-01-31", 11_000.0),
        _point("2025-02-28", 10_450.0),
        _point("2025-03-31", 11_286.0),
    ]
    result = BacktestResult(
        equity_curve=curve,
        trades=[object()] * 12,
        total_return=12.86,
        max_drawdown_pct=-5.0,
        final_equity=11_286.0,
    )

    out = recompute_multiseg_robust(result, initial_equity=10_000.0, granularities=("monthly", "quarterly"))

    expected = robust_score_from_returns([10.0, -5.0, 8.0], max_drawdown_pct=-5.0, trades=12)
    assert out["monthly"]["robust_score"] == pytest.approx(expected)
    assert out["monthly"]["mean_return"] == pytest.approx((10.0 - 5.0 + 8.0) / 3.0)
    assert out["monthly"]["worst_segment_return"] == pytest.approx(-5.0)
    assert out["monthly"]["return_stdev"] > 0.0
    assert out["monthly"]["segment_compound_return_pct"] == pytest.approx(result.total_return)
    assert out["monthly"]["negative_segment_count"] == 1
    assert out["quarterly"]["segment_count"] == 1
