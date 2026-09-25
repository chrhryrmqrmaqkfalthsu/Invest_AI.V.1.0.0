from types import SimpleNamespace

from scripts.research import run_stage2 as runner


class _DummyRulebook:
    fitness = 1.23

    def to_dict(self):
        return {"dummy": True}


class _DummyLogger:
    def info(self, *args, **kwargs):
        pass


def _fake_backtest_result() -> SimpleNamespace:
    return SimpleNamespace(
        fitness=7.5,
        trades=[],
        trade_count=6,
        win_count=4,
        loss_count=2,
        win_rate=0.667,
        expectancy_pct=2.5,
        avg_return_pct=1.0,
        profit_factor=1.8,
        max_drawdown_pct=-5.0,
    )


def test_train_one_split_uses_honest_stage2_execution_modes(monkeypatch):
    calls = []
    rb = _DummyRulebook()
    df = object()

    def fake_prepare_ticker_context(ticker):
        assert ticker == "MPLX"
        return {
            "df": df,
            "base_rulebook": rb,
            "market_history_df": object(),
            "sector_name": "energy",
            "ticker_sentiment": {"x": 1.0},
        }

    def fake_run_backtest_execution_mode(rulebook, data_frame, **kwargs):
        calls.append((rulebook, data_frame, dict(kwargs)))
        return _fake_backtest_result()

    def fake_run_ga(*, base_rulebook, evaluate_fn, ga_config, on_generation):
        assert base_rulebook is rb
        assert evaluate_fn(rb) == 7.5
        return SimpleNamespace(final_population=[], generations_run=1)

    monkeypatch.setattr(runner, "prepare_ticker_context", fake_prepare_ticker_context)
    monkeypatch.setattr(runner, "run_backtest_execution_mode", fake_run_backtest_execution_mode)
    monkeypatch.setattr(runner, "run_ga", fake_run_ga)

    result = runner.train_one_split(
        ticker="MPLX",
        split_idx=1,
        split={"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
        seed_base=123,
    )

    assert result["generations_run"] == 1
    assert len(calls) == 1
    rulebook, data_frame, kwargs = calls[0]
    assert rulebook is rb
    assert data_frame is df
    assert kwargs["start_date"] == "2022-07-01"
    assert kwargs["end_date"] == "2023-06-30"
    assert kwargs["position_limit_krw"] == runner.POSITION_LIMIT_KRW
    assert kwargs["sector_name"] == "energy"
    assert kwargs["fitness_mode"] == "swing"
    assert kwargs["use_llm_events"] is False
    assert kwargs["entry_execution_mode"] == "t_plus_1_open"
    assert kwargs["entry_execution_mode"] == runner.ENTRY_EXECUTION_MODE
    assert kwargs["exit_execution_mode"] == "conservative_core"
    assert kwargs["exit_execution_mode"] == runner.EXIT_EXECUTION_MODE
    assert kwargs["fold_exit_policy"] == "fold_end_mark_to_market"
    assert kwargs["fold_exit_policy"] == runner.FOLD_EXIT_POLICY
    assert kwargs["live_hard_stop_guard"] is True
    assert kwargs["live_hard_stop_guard"] == runner.LIVE_HARD_STOP_GUARD


def test_evaluate_periods_uses_honest_stage2_execution_modes(monkeypatch):
    calls = []
    rb = _DummyRulebook()
    df = object()

    def fake_run_backtest_execution_mode(rulebook, data_frame, **kwargs):
        calls.append((rulebook, data_frame, dict(kwargs)))
        return _fake_backtest_result()

    def fake_score_period_candidates(raw_candidates):
        scored = []
        for row in raw_candidates:
            scored.append(
                {
                    **row,
                    "oos_metrics": {
                        "trade_count": row["trade_count"],
                        "expectancy_pct": row["expectancy_pct"],
                        "profit_factor": row["profit_factor"],
                        "win_rate": row["win_rate"],
                        "max_drawdown_pct": row["max_drawdown_pct"],
                    },
                    "oos_member_score": 100.0,
                    "fitness": row["fitness"],
                }
            )
        return scored

    monkeypatch.setattr(runner, "run_backtest_execution_mode", fake_run_backtest_execution_mode)
    monkeypatch.setattr(runner, "_score_period_candidates", fake_score_period_candidates)
    monkeypatch.setattr(runner, "stage2_fail_reasons", lambda metrics, period_kind, gate: [])

    result = runner.evaluate_periods(
        ticker="MPLX",
        ctx={
            "df": df,
            "market_history_df": object(),
            "sector_name": "energy",
            "ticker_sentiment": {"x": 1.0},
        },
        periods=[{"label": "stress_pre_2022h1", "kind": "stress", "start": None, "end": "2022-06-30", "order": 1}],
        representative_by_hash={"abc": rb},
        origin_rows_by_hash={"abc": [{"train_label": "train_1", "origin_rank": 1, "train_fitness": 1.0}]},
        logger=_DummyLogger(),
    )

    assert result["survivors"] == ["abc"]
    assert len(calls) == 1
    rulebook, data_frame, kwargs = calls[0]
    assert rulebook is rb
    assert data_frame is df
    assert kwargs["start_date"] is None
    assert kwargs["end_date"] == "2022-06-30"
    assert kwargs["position_limit_krw"] == runner.POSITION_LIMIT_KRW
    assert kwargs["sector_name"] == "energy"
    assert kwargs["fitness_mode"] == "swing"
    assert kwargs["use_llm_events"] is False
    assert kwargs["entry_execution_mode"] == "t_plus_1_open"
    assert kwargs["entry_execution_mode"] == runner.ENTRY_EXECUTION_MODE
    assert kwargs["exit_execution_mode"] == "conservative_core"
    assert kwargs["exit_execution_mode"] == runner.EXIT_EXECUTION_MODE
    assert kwargs["fold_exit_policy"] == "fold_end_mark_to_market"
    assert kwargs["fold_exit_policy"] == runner.FOLD_EXIT_POLICY
    assert kwargs["live_hard_stop_guard"] is True
    assert kwargs["live_hard_stop_guard"] == runner.LIVE_HARD_STOP_GUARD
