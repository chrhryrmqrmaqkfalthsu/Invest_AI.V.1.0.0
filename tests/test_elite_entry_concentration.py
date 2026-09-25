from engine.live.elite_entry_concentration import score_entry_concentration


def _candidate(**overrides):
    base = {
        "ticker": "TEST",
        "stage": "stage3",
        "bucket": "A_core",
        "metrics": {
            "oos_expectancy_pct": 9.0,
            "oos_win_rate": 82.0,
            "oos_fitness": 120.0,
            "oos_trade_count": 15,
            "worst_drawdown_pct": -6.0,
        },
        "trade_summary": {
            "trade_count": 15,
            "avg_mfe_pct": 8.0,
        },
    }
    base.update(overrides)
    return base


def _quality(score=86, label="STRONG_FOLLOW_THROUGH", size_factor=1.0, **metric_overrides):
    metrics = {
        "ret_5d_pct": 4.0,
        "ret_3d_pct": 2.0,
        "dist_ma5_pct": 2.0,
        "dist_high5_pct": -2.0,
        "volume_ratio20": 1.1,
        "atr_pct": 3.0,
        "close_position": 0.7,
        "event_heavy": False,
        "high_vol": False,
        "low_price": False,
    }
    metrics.update(metric_overrides)
    return {"score": score, "label": label, "size_factor": size_factor, "metrics": metrics}


def test_strong_stage3_q80_plus_can_be_top_concentration_candidate():
    result = score_entry_concentration(_candidate(), _quality(score=86))
    assert result["score"] >= 85
    assert result["action"] == "TOP 진입 몰빵"
    assert result["allowed"] is True
    assert result["blocks"] == []


def test_stage3_q_below_80_is_blocked_even_if_score_is_high():
    result = score_entry_concentration(_candidate(), _quality(score=75))
    assert result["score"] > 70
    assert result["allowed"] is False
    assert "stage3 Q<80" in result["blocks"]


def test_weak_follow_through_is_never_concentration_candidate():
    result = score_entry_concentration(_candidate(stage="stage2"), _quality(score=90, label="WEAK_FOLLOW_THROUGH"))
    assert result["allowed"] is False
    assert "WEAK" in result["blocks"]


def test_high_vol_low_price_needs_q90_for_concentration():
    result = score_entry_concentration(
        _candidate(),
        _quality(score=86, high_vol=True, low_price=True, atr_pct=9.5),
    )
    assert result["allowed"] is False
    assert "고위험 Q<90" in result["blocks"]
