from types import SimpleNamespace

from engine.live import event_policy


def _legacy_flags(ctx):
    active = getattr(ctx, "active_events", {}) or {} if ctx is not None else {}
    return {
        "has_war": int("전쟁" in active),
        "has_rate_hike": int("금리정책_인상" in active),
        "has_rate_cut": int("금리정책_인하" in active),
        "has_geopolitical": int("지정학_긴장" in active),
        "has_tariff": int("관세" in active),
        "has_export_ban": int("수출규제" in active),
        "has_earnings_shock": int("실적쇼크" in active),
        "has_oil_surge": int("유가급등" in active),
        "has_banking_crisis": int("은행위기" in active),
        "has_inflation": int("인플레이션" in active),
        "has_fed_statement": int("연준발언" in active),
    }


def test_policy_default_is_on():
    assert event_policy.live_direct_event_enabled() is True


def test_live_event_flags_on_matches_legacy_exactly():
    ctx = SimpleNamespace(active_events={
        "전쟁": {}, "금리정책_인상": {}, "지정학_긴장": {},
        "실적쇼크": {}, "인플레이션": {},
    })
    expected = _legacy_flags(ctx)
    actual = event_policy.live_event_flags(ctx, enabled_override=True)
    assert actual == expected
    assert list(actual or {}) == list(expected)
    assert event_policy.live_event_flags(None, enabled_override=True) == _legacy_flags(None)
    assert event_policy.live_event_flags(ctx, enabled_override=False) is None
