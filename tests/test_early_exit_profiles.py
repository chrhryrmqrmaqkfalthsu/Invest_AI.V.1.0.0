from engine.live.early_exit_profiles import dashboard_early_exit_profile, find_early_exit_profile


def test_find_early_exit_profile_matches_rulebook_prefix():
    row = find_early_exit_profile("FIX", member_hash="cab7d458767dabcdef")
    assert row is not None
    assert row["ticker"] == "FIX"
    assert row["validation_status"] == "pass"
    assert row["match_confidence"] == "rulebook_hash_prefix"


def test_dashboard_early_exit_profile_triggers_manual_review_only():
    pos = {
        "entry_price": 100.0,
        "target_price": 120.0,
        "current_price": 101.0,
        "highest_price": 103.0,
        "member_hash": "cab7d458767dabcdef",
        "entry_date": "2026-06-20T00:00:00+00:00",
        "rulebook_snapshot": {},
    }
    row = dashboard_early_exit_profile("FIX", position=pos, current_price=101.0, pnl_pct=1.0, holding_days=4)
    assert row is not None
    assert row["manual_only"] is True
    assert row["evaluation"]["triggered"] is True
    assert row["evaluation"]["state"] == "review"
    assert row["current"]["metric_value"] == 1.0


def test_dashboard_early_exit_profile_not_due_before_check_day():
    pos = {
        "entry_price": 100.0,
        "target_price": 120.0,
        "current_price": 101.0,
        "member_hash": "57c9bbec4376abcdef",
        "rulebook_snapshot": {},
    }
    row = dashboard_early_exit_profile("DDS", position=pos, current_price=101.0, pnl_pct=1.0, holding_days=3)
    assert row is not None
    assert row["check_day"] == 5
    assert row["evaluation"]["due"] is False
    assert row["evaluation"]["triggered"] is False
