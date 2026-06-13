from __future__ import annotations

from engine.pipeline.stage2_gate import Stage2GateConfig, stage2_fail_reasons
from scripts.research.recheck_stage2_gate import TARGET_COUNTS, recheck_all


def test_stress_gate_passes_mdd_boundary_when_ratio_above_one() -> None:
    metrics = {
        "trade_count": 11,
        "expectancy_pct": 2.0,
        "max_drawdown_pct": -20.0,
    }
    assert stage2_fail_reasons(metrics, "stress") == []


def test_stress_gate_fails_ratio_exactly_one() -> None:
    ratio_exactly_one = {
        "trade_count": 10,
        "expectancy_pct": 2.0,
        "max_drawdown_pct": -20.0,
    }
    reasons = stage2_fail_reasons(ratio_exactly_one, "stress")
    assert any(r["metric"] == "stress_return_mdd_ratio" and r["rule"] == ">" for r in reasons)


def test_stress_gate_fails_mdd_deeper_than_minus_20() -> None:
    metrics = {
        "trade_count": 30,
        "expectancy_pct": 2.0,
        "max_drawdown_pct": -20.001,
    }
    reasons = stage2_fail_reasons(metrics, "stress")
    assert any(r["metric"] == "max_drawdown_pct" for r in reasons)


def test_stress_gate_fails_missing_or_zero_mdd_for_ratio() -> None:
    zero_mdd = {
        "trade_count": 10,
        "expectancy_pct": 2.0,
        "max_drawdown_pct": 0.0,
    }
    reasons = stage2_fail_reasons(zero_mdd, "stress")
    assert any(r["metric"] == "stress_return_mdd_ratio" and r.get("reason") == "ratio_unavailable" for r in reasons)

    missing_mdd = {
        "trade_count": 10,
        "expectancy_pct": 2.0,
    }
    reasons = stage2_fail_reasons(missing_mdd, "stress")
    assert any(r["metric"] == "max_drawdown_pct" for r in reasons)
    assert any(r["metric"] == "stress_return_mdd_ratio" for r in reasons)


def test_train_gate_uses_trade_member_and_expectancy() -> None:
    passing = {"trade_count": 5, "member_score": 10.0, "expectancy_pct": 1.0}
    assert stage2_fail_reasons(passing, "train") == []

    failing = {"trade_count": 4, "member_score": 9.99, "expectancy_pct": 0.99}
    reasons = stage2_fail_reasons(failing, "general")
    assert {r["metric"] for r in reasons} == {"trade_count", "member_score", "expectancy_pct"}


def test_oos_gate_boundary_and_trade_member_on() -> None:
    passing = {
        "trade_count": 5,
        "member_score": 10.0,
        "expectancy_pct": 1.0,
        "max_drawdown_pct": -15.0,
    }
    assert stage2_fail_reasons(passing, "oos") == []

    failing = {
        "trade_count": 4,
        "member_score": 9.0,
        "expectancy_pct": 0.5,
        "max_drawdown_pct": -15.001,
    }
    reasons = stage2_fail_reasons(failing, "oos")
    assert {r["metric"] for r in reasons} == {
        "trade_count",
        "member_score",
        "expectancy_pct",
        "max_drawdown_pct",
    }


def test_oos_trade_member_gate_can_be_disabled() -> None:
    config = Stage2GateConfig(oos_keep_trade_member_gate=False)
    metrics = {
        "trade_count": 0,
        "member_score": 0.0,
        "expectancy_pct": 1.0,
        "max_drawdown_pct": -15.0,
    }
    assert stage2_fail_reasons(metrics, "oos", config) == []


def test_stage2_gate_recheck_matches_10stock_targets() -> None:
    results = recheck_all()
    got = {result.ticker: result.final_count for result in results}
    assert got == TARGET_COUNTS
    assert sum(got.values()) == 201
    assert all(result.passed for result in results)
