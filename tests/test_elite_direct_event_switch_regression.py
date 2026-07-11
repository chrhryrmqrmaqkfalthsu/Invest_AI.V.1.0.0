from types import SimpleNamespace

import pandas as pd

from engine.live import elite_shadow_trader as elite
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
            "Volume_ratio": 2.0, "ATR": 1.0,
        })
    return pd.DataFrame(rows)


def _legacy_flags(active):
    pairs = (
        ("has_war", "전쟁"), ("has_rate_hike", "금리정책_인상"),
        ("has_rate_cut", "금리정책_인하"), ("has_geopolitical", "지정학_긴장"),
        ("has_tariff", "관세"), ("has_export_ban", "수출규제"),
        ("has_earnings_shock", "실적쇼크"), ("has_oil_surge", "유가급등"),
        ("has_banking_crisis", "은행위기"), ("has_inflation", "인플레이션"),
        ("has_fed_statement", "연준발언"),
    )
    return {key: int(name in active) for key, name in pairs}


def test_elite_evaluate_candidate_on_is_legacy_identical(monkeypatch):
    rb = default_rulebook("CE", "us_equity", "long")
    rb.signal_threshold = 1.0
    rb.use_event_block = True
    rb.event_response_rate_hike = 1.25
    rb.event_response_inflation = 0.6
    rb.event_strength_multiplier = 1.7
    rb.use_market_entry_adjustment = False
    df = _frame()
    active = {"금리정책_인상": {}, "인플레이션": {}}
    ctx = SimpleNamespace(score=87.2, sector_strength={}, vix_level=18.0, active_events=active)
    candidate = {"candidate_id": "stage3:CE:test", "ticker": "CE", "stage": "stage3"}

    monkeypatch.setattr(elite, "_load_rulebook_for_candidate", lambda candidate: dict(vars(rb)))
    monkeypatch.setattr(elite, "_load_ohlcv", lambda ticker: df)
    monkeypatch.setattr(elite, "_latest_price", lambda ticker, frame: 100.0)
    monkeypatch.setattr(elite, "_news_context", lambda ticker, rulebook, signal_date: (0.0, {}))
    monkeypatch.setattr(elite, "assess_shadow_entry_quality", lambda **kwargs: {"allow": True})
    captured = {}
    monkeypatch.setattr(elite, "append_shadow_direct_event_log", lambda **kwargs: captured.update(kwargs))

    actual = elite.evaluate_candidate(candidate, ctx=ctx)
    expected = evaluate_signal(
        rb=rb, df=df, market_score=87.2, sector_score=50.0, vix_level=18.0,
        news_sentiment=0.0, event_flags=_legacy_flags(active), topic_features={},
    )

    assert actual["score"].hex() == expected.score.hex()
    assert actual["raw_score"].hex() == expected.raw_score.hex()
    assert actual["should_buy"] == expected.should_buy
    assert actual["components"] == expected.components
    assert captured["result_on"] == expected
    assert captured["market_score_on"] == captured["market_score_off"] == 87.2
