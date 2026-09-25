from datetime import datetime, timezone

from engine.live.elite_exit_policy_lab import _clean_entry_allowed


def test_lab_rejects_source_entry_before_regular_open():
    state = {"created_at": "2026-07-01T00:00:00+00:00"}
    source_pos = {
        "opened_at": "2026-07-02T12:45:00+00:00",  # 08:45 ET, premarket
        "ticker": "TEST",
        "position_id": "shadow-test",
        "entry_price": 100,
        "shares": 1,
        "notional": 100,
    }
    allowed, reason = _clean_entry_allowed(source_pos, state)
    assert allowed is False
    assert reason.startswith("source_opened_outside_regular_hours")


def test_lab_accepts_source_entry_during_regular_hours():
    state = {"created_at": "2026-07-01T00:00:00+00:00"}
    source_pos = {
        "opened_at": "2026-07-02T14:00:00+00:00",  # 10:00 ET, regular market
        "ticker": "TEST",
        "position_id": "shadow-test",
        "entry_price": 100,
        "shares": 1,
        "notional": 100,
    }
    allowed, reason = _clean_entry_allowed(source_pos, state)
    assert allowed is True
    assert reason == "clean_entry_regular_hours"


def test_lab_rejects_source_entry_after_regular_close():
    state = {"created_at": "2026-07-01T00:00:00+00:00"}
    source_pos = {
        "opened_at": "2026-07-02T20:30:00+00:00",  # 16:30 ET, after-hours
        "ticker": "TEST",
        "position_id": "shadow-test",
        "entry_price": 100,
        "shares": 1,
        "notional": 100,
    }
    allowed, reason = _clean_entry_allowed(source_pos, state)
    assert allowed is False
    assert reason.startswith("source_opened_outside_regular_hours")
