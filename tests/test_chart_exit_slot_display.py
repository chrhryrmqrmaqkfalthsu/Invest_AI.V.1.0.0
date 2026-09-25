from engine.live.chart_exit_slot_display import apply_chart_exit_display_override


def test_chart_exit_display_override_uses_manual_prices_and_percents():
    row = {
        "ticker": "ABC",
        "entry_price": 100.0,
        "target_price": 120.0,
        "stop_price": 90.0,
        "target_return_pct": 20.0,
        "stop_return_pct": -10.0,
    }
    plans = {
        "ABC": {
            "ticker": "ABC",
            "enabled": True,
            "status": "active",
            "take_profit_price": 111.0,
            "stop_loss_price": 97.0,
            "take_profit_pct": 11.0,
            "stop_loss_pct": 3.0,
            "take_profit_basis": "pct",
            "stop_loss_basis": "pct",
        }
    }

    out = apply_chart_exit_display_override(row, plans)

    assert out["target_price"] == 111.0
    assert out["stop_price"] == 97.0
    assert out["target_return_pct"] == 11.0
    assert out["stop_return_pct"] == -3.0
    assert out["display_target_return_pct"] == 11.0
    assert out["display_stop_return_pct"] == -3.0
    assert out["manual_take_profit_pct"] == 11.0
    assert out["manual_stop_loss_pct"] == -3.0
    assert out["rulebook_target_price"] == 120.0
    assert out["rulebook_stop_price"] == 90.0
    assert out["rulebook_target_return_pct"] == 20.0
    assert out["rulebook_stop_return_pct"] == -10.0
    assert out["manual_exit_plan_active"] is True


def test_chart_exit_display_override_ignores_inactive_plan():
    row = {"ticker": "ABC", "entry_price": 100.0, "target_price": 120.0, "stop_price": 90.0}
    plans = {"ABC": {"ticker": "ABC", "enabled": True, "status": "disabled", "take_profit_price": 111.0}}

    assert apply_chart_exit_display_override(row, plans) is row
