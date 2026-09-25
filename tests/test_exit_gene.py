import pytest

from engine.pipeline.exit_gene import (
    EXIT_CATEGORICAL,
    EXIT_FIELDS,
    EXIT_NUMERIC,
    ExitFitnessWeights,
    apply_exit,
    composite_exit_fitness,
    holding_days_summary,
)


EXPECTED_EXIT_FIELDS = (
    "exit_strategy",
    "breakeven_enabled",
    "sell_omen_enabled",
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


SAMPLE_EXIT_GENE = {
    "exit_strategy": "trailing",
    "breakeven_enabled": True,
    "sell_omen_enabled": True,
    "stop_loss_atr": 1.3,
    "stop_loss_atr_bear": 1.8,
    "take_profit_atr": 4.2,
    "take_profit_atr_bull": 5.4,
    "trailing_atr": 1.1,
    "trailing_atr_volatile": 2.4,
    "trailing_activation_profit_pct": 2.2,
    "breakeven_trigger_profit_pct": 5.5,
    "breakeven_floor_profit_pct": 1.2,
    "sell_omen_threshold": 0.45,
    "max_holding_days": 9,
}


def _base_fitness(weights=None, **kwargs):
    return composite_exit_fitness(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 2.0, "max_drawdown_pct": -5.0},
        {"median": 5.0},
        weights or ExitFitnessWeights(),
        **kwargs,
    )


def test_exit_fields_are_the_audited_fourteen_fields():
    assert len(EXIT_FIELDS) == 14
    assert EXIT_FIELDS == EXPECTED_EXIT_FIELDS
    assert EXIT_CATEGORICAL == ("exit_strategy", "breakeven_enabled", "sell_omen_enabled")
    assert EXIT_NUMERIC == EXPECTED_EXIT_FIELDS[3:]


def test_apply_exit_overwrites_only_exit_fields_and_keeps_entry_fields_fixed():
    base = {
        "ticker": "CRWD",
        "weight_ma_align": 1.7,
        "signal_threshold": 3.4,
        "base_position_ratio": 0.6,
        "market_score_weight": 0.4,
        "exit_strategy": "fixed",
        "breakeven_enabled": False,
        "sell_omen_enabled": False,
        "stop_loss_atr": 3.0,
        "max_holding_days": 30,
    }

    out = apply_exit(base, SAMPLE_EXIT_GENE)

    assert out is not base
    for field in EXIT_FIELDS:
        assert out[field] == SAMPLE_EXIT_GENE[field]

    assert out["ticker"] == "CRWD"
    assert out["weight_ma_align"] == 1.7
    assert out["signal_threshold"] == 3.4
    assert out["base_position_ratio"] == 0.6
    assert out["market_score_weight"] == 0.4

    # 원본 dict는 변하지 않아야 한다.
    assert base["exit_strategy"] == "fixed"
    assert base["max_holding_days"] == 30


def test_holding_days_summary_known_percentiles():
    trades = [
        {"holding_days": 1},
        {"holding_days": 2},
        {"holding_days": 3},
        {"holding_days": 4},
    ]

    summary = holding_days_summary(trades)

    assert summary["count"] == 4
    assert summary["mean"] == pytest.approx(2.5)
    assert summary["median"] == pytest.approx(2.5)
    assert summary["p75"] == pytest.approx(3.25)
    assert summary["p90"] == pytest.approx(3.7)
    assert summary["max"] == pytest.approx(4.0)


def test_holding_days_summary_empty_input_is_safe():
    summary = holding_days_summary([])

    assert summary == {"count": 0, "mean": None, "median": None, "p75": None, "p90": None, "max": None}


def test_composite_exit_fitness_penalizes_weaker_stress_expectancy():
    weights = ExitFitnessWeights()
    base = composite_exit_fitness(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 2.0, "max_drawdown_pct": -5.0},
        {"median": 5.0},
        weights,
    )
    weak_stress = composite_exit_fitness(
        {"expectancy_pct": -1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 2.0, "max_drawdown_pct": -5.0},
        {"median": 5.0},
        weights,
    )

    assert weak_stress < base


def test_composite_exit_fitness_penalizes_bull_floor_shortfall():
    weights = ExitFitnessWeights(bull_floor=1.0, w_bull_floor_penalty=3.0)
    ok_bull = composite_exit_fitness(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 1.5, "max_drawdown_pct": -5.0},
        {"median": 5.0},
        weights,
    )
    weak_bull = composite_exit_fitness(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 0.5, "max_drawdown_pct": -5.0},
        {"median": 5.0},
        weights,
    )

    assert weak_bull < ok_bull


def test_composite_exit_fitness_penalizes_holding_above_soft_cap():
    weights = ExitFitnessWeights(holding_soft_cap=7.0, w_holding=0.1)
    short_holding = composite_exit_fitness(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 2.0, "max_drawdown_pct": -5.0},
        {"median": 7.0},
        weights,
    )
    long_holding = composite_exit_fitness(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -5.0},
        {"expectancy_pct": 2.0, "max_drawdown_pct": -5.0},
        {"median": 12.0},
        weights,
    )

    assert long_holding < short_holding


def test_trade_penalty_weights_zero_preserve_previous_fitness_exactly():
    weights = ExitFitnessWeights(w_timeout_loss=0.0, w_deep_stop=0.0)
    no_trades = _base_fitness(weights)
    with_bad_trades = _base_fitness(
        weights,
        stress_trades=[
            {"exit_reason": "time_out", "pnl_pct": -7.0},
            {"exit_reason": "stop_loss", "pnl_pct": -18.0},
        ],
        bull_trades=[
            {"exit_reason": "time_out", "pnl_pct": -5.0},
            {"exit_reason": "stop_loss", "pnl_pct": -14.0},
        ],
    )

    assert with_bad_trades == pytest.approx(no_trades)


def test_timeout_loss_penalty_decreases_fitness_monotonically_with_more_timeout_losses():
    weights = ExitFitnessWeights(w_timeout_loss=0.5)
    no_timeout_loss = _base_fitness(
        weights,
        stress_trades=[{"exit_reason": "time_out", "pnl_pct": 3.0}],
    )
    one_timeout_loss = _base_fitness(
        weights,
        stress_trades=[{"exit_reason": "time_out", "pnl_pct": -3.0}],
    )
    two_timeout_losses = _base_fitness(
        weights,
        stress_trades=[
            {"exit_reason": "time_out", "pnl_pct": -3.0},
            {"exit_reason": "time_out", "pnl_pct": -4.0},
        ],
    )

    assert one_timeout_loss < no_timeout_loss
    assert two_timeout_losses < one_timeout_loss


def test_deep_stop_penalty_decreases_fitness_only_above_threshold():
    weights = ExitFitnessWeights(w_deep_stop=1.0, deep_stop_threshold_pct=10.0)
    shallow_stop = _base_fitness(
        weights,
        stress_trades=[{"exit_reason": "stop_loss", "pnl_pct": -9.9}],
    )
    threshold_stop = _base_fitness(
        weights,
        stress_trades=[{"exit_reason": "stop_loss", "pnl_pct": -10.0}],
    )
    deep_stop = _base_fitness(
        weights,
        stress_trades=[{"exit_reason": "stop_loss", "pnl_pct": -12.5}],
    )
    deeper_stops = _base_fitness(
        weights,
        stress_trades=[
            {"exit_reason": "stop_loss", "pnl_pct": -12.5},
            {"exit_reason": "stop_loss", "pnl_pct": -15.0},
        ],
    )

    assert shallow_stop == pytest.approx(threshold_stop)
    assert deep_stop < threshold_stop
    assert deeper_stops < deep_stop


def test_profitable_timeout_and_shallow_non_stop_losses_are_not_trade_penalized():
    weights = ExitFitnessWeights(w_timeout_loss=1.0, w_deep_stop=1.0, deep_stop_threshold_pct=10.0)
    base = _base_fitness(weights)
    harmless = _base_fitness(
        weights,
        stress_trades=[
            {"exit_reason": "time_out", "pnl_pct": 2.0},
            {"exit_reason": "trailing", "pnl_pct": -20.0},
            {"exit_reason": "stop_loss", "pnl_pct": -8.0},
        ],
        bull_trades=[{"exit_reason": "take_profit", "pnl_pct": 7.0}],
    )

    assert harmless == pytest.approx(base)
