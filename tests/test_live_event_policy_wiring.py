from pathlib import Path


def test_three_live_callers_use_common_event_policy():
    root = Path(__file__).resolve().parents[1]
    callers = [
        root / "engine/live/central_control.py",
        root / "engine/strategies/learned_rulebook.py",
        root / "engine/live/elite_shadow_trader.py",
    ]
    for path in callers:
        text = path.read_text(encoding="utf-8")
        assert "live_event_flags(" in text
        assert '"has_war": int(' not in text
        assert '"has_rate_hike": int(' not in text
    policy = (root / "config/policy.yaml").read_text(encoding="utf-8")
    assert "direct_event_enabled: true" in policy
