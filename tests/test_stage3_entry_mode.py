from scripts.research import run_stage3_aggressive as runner


class _DummyRulebook:
    pass


def test_run_backtest_period_uses_honest_stage2_execution_modes(monkeypatch):
    calls = {}

    def fake_run_backtest_execution_mode(rulebook, df, **kwargs):
        calls["rulebook"] = rulebook
        calls["df"] = df
        calls["kwargs"] = dict(kwargs)
        return {"ok": True}

    monkeypatch.setattr(runner, "run_backtest_execution_mode", fake_run_backtest_execution_mode)
    rb = _DummyRulebook()
    df = object()
    ctx = {
        "df": df,
        "market_history_df": object(),
        "sector_name": "semis",
        "ticker_sentiment": {"headline": 1.0},
    }

    result = runner.run_backtest_period(rb, ctx, start="2024-07-01", end="2025-06-30")

    assert result == {"ok": True}
    assert calls["rulebook"] is rb
    assert calls["df"] is df
    assert calls["kwargs"]["start_date"] == "2024-07-01"
    assert calls["kwargs"]["end_date"] == "2025-06-30"
    assert calls["kwargs"]["position_limit_krw"] == runner.POSITION_LIMIT_KRW
    assert calls["kwargs"]["market_history_df"] is ctx["market_history_df"]
    assert calls["kwargs"]["sector_name"] == "semis"
    assert calls["kwargs"]["ticker_sentiment"] == {"headline": 1.0}
    assert calls["kwargs"]["fitness_mode"] == "swing"
    assert calls["kwargs"]["use_llm_events"] is False
    assert calls["kwargs"]["entry_execution_mode"] == "t_plus_1_open"
    assert calls["kwargs"]["entry_execution_mode"] == runner.ENTRY_EXECUTION_MODE
    assert calls["kwargs"]["exit_execution_mode"] == "conservative_core"
    assert calls["kwargs"]["exit_execution_mode"] == runner.EXIT_EXECUTION_MODE
    assert calls["kwargs"]["fold_exit_policy"] == "fold_end_mark_to_market"
    assert calls["kwargs"]["fold_exit_policy"] == runner.FOLD_EXIT_POLICY
    assert calls["kwargs"]["live_hard_stop_guard"] is True
    assert calls["kwargs"]["live_hard_stop_guard"] == runner.LIVE_HARD_STOP_GUARD
