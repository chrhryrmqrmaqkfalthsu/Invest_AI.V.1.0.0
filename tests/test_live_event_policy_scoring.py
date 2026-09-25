import json
from types import SimpleNamespace

import pandas as pd

from engine.live import event_policy
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import default_rulebook


def _frame():
    rows = []
    for i in range(60):
        rows.append({
            "Close": 100.0, "MA5": 101.0, "MA20": 100.0, "MA60": 99.0,
            "Aligned_bull": 1, "MACD": 1.0 if i == 59 else 0.0,
            "MACD_signal": 0.5, "MACD_golden": 1 if i == 59 else 0,
            "RSI": 50.0, "BB_lower": 99.5, "BB_upper": 105.0,
            "Volume_ratio": 2.0,
        })
    return pd.DataFrame(rows)


def _rb():
    rb = default_rulebook("TEST", "us_equity", "long")
    rb.signal_threshold = 1.0
    rb.use_event_block = True
    rb.event_response_war = 0.75
    rb.event_response_rate_hike = 1.25
    rb.event_response_inflation = 0.6
    rb.event_strength_multiplier = 1.7
    rb.use_market_entry_adjustment = True
    rb.market_score_weight = 0.4
    rb.sector_strength_weight = 0.2
    rb.vix_sensitivity = 0.1
    rb.market_adjustment_strength = 0.5
    return rb


def _legacy_flags(ctx):
    active = ctx.active_events
    names = (
        ("has_war", "전쟁"), ("has_rate_hike", "금리정책_인상"),
        ("has_rate_cut", "금리정책_인하"), ("has_geopolitical", "지정학_긴장"),
        ("has_tariff", "관세"), ("has_export_ban", "수출규제"),
        ("has_earnings_shock", "실적쇼크"), ("has_oil_surge", "유가급등"),
        ("has_banking_crisis", "은행위기"), ("has_inflation", "인플레이션"),
        ("has_fed_statement", "연준발언"),
    )
    return {key: int(name in active) for key, name in names}


def _kwargs():
    return {
        "rb": _rb(), "df": _frame(), "market_score": 71.5,
        "sector_score": 63.0, "vix_level": 19.25,
        "news_sentiment": 0.2, "topic_features": {"technology": 0.5},
    }


def test_on_score_is_bitwise_identical_to_legacy():
    ctx = SimpleNamespace(active_events={"전쟁": {}, "금리정책_인상": {}, "인플레이션": {}})
    legacy = evaluate_signal(event_flags=_legacy_flags(ctx), **_kwargs())
    current = evaluate_signal(event_flags=event_policy.live_event_flags(ctx, True), **_kwargs())
    assert current == legacy
    assert current.score.hex() == legacy.score.hex()
    assert current.raw_score.hex() == legacy.raw_score.hex()


def test_shadow_log_invariants(tmp_path, monkeypatch):
    monkeypatch.setattr(event_policy, "SHADOW_LOG_DIR", tmp_path)
    ctx = SimpleNamespace(active_events={"전쟁": {}, "금리정책_인상": {}, "인플레이션": {}})
    on = evaluate_signal(event_flags=event_policy.live_event_flags(ctx, True), **_kwargs())
    off = evaluate_signal(event_flags=event_policy.live_event_flags(ctx, False), **_kwargs())
    path = event_policy.append_shadow_direct_event_log(
        candidate_id="stage3:TEST:abc", mode="test", path="test",
        market_score_on=71.5, market_score_off=71.5,
        result_on=on, result_off=off,
    )
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["market_score_on"] == row["market_score_off"]
    assert row["event_component"] == on.components["events"]
    assert row["score_on"] == on.score and row["score_off"] == off.score
    assert row["invariant_ok"] is True
