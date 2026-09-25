import pytest

from engine.live.elite_shadow_mark_to_market import apply_mark_to_market_snapshot


def test_apply_mark_to_market_uses_intraday_high_not_last_price():
    pos = {
        "ticker": "TEST",
        "entry_price": 100.0,
        "shares": 10.0,
        "opened_at": "2026-07-02T14:00:00+00:00",
        "highest_price": 100.0,
        "lowest_price": 100.0,
        "max_profit_pct": 0.0,
        "max_loss_pct": 0.0,
        "last_price": 100.0,
    }
    changed = apply_mark_to_market_snapshot(
        pos,
        {
            "source": "unit_1m",
            "last_price": 101.0,
            "high_price": 105.0,
            "low_price": 99.0,
            "high_time": "2026-07-02T14:15:00+00:00",
            "low_time": "2026-07-02T14:02:00+00:00",
            "bar_count": 16,
        },
        source="unit_test",
    )
    assert changed is True
    assert pos["last_price"] == 101.0
    assert pos["highest_price"] == 105.0
    assert pos["lowest_price"] == 99.0
    assert pos["max_profit_pct"] == pytest.approx(5.0)
    assert pos["max_loss_pct"] == pytest.approx(-1.0)
    assert pos["unrealized_pnl_pct"] == pytest.approx(1.0)
    assert pos["max_profit_observed_at"] == "2026-07-02T14:15:00+00:00"
    assert pos["max_profit_source"] == "unit_test"


def test_apply_mark_to_market_never_reduces_saved_max_profit():
    pos = {
        "ticker": "TEST",
        "entry_price": 100.0,
        "shares": 10.0,
        "opened_at": "2026-07-02T14:00:00+00:00",
        "highest_price": 106.0,
        "lowest_price": 100.0,
        "max_profit_pct": 6.0,
        "max_loss_pct": 0.0,
        "last_price": 104.0,
    }
    apply_mark_to_market_snapshot(
        pos,
        {"source": "unit_1m", "last_price": 101.0, "high_price": 102.0, "low_price": 99.5, "bar_count": 2},
        source="unit_test",
    )
    assert pos["highest_price"] == 106.0
    assert pos["max_profit_pct"] == pytest.approx(6.0)
    assert pos["last_price"] == 101.0
    assert pos["max_loss_pct"] == pytest.approx(-0.5)
