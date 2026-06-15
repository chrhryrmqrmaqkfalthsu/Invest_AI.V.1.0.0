import json

from engine.pipeline.stage3_gate import (
    DEFAULT_STAGE3_PROFILE,
    DEFAULT_STAGE3_QUALIFY,
    stage3_basic_eligibility,
    stage3_profile,
    stage3_qualify_fail_reasons,
)


def _year_metrics(exp=2.0, trades=5, member=10.0):
    return {"trade_count": trades, "member_score": member, "expectancy_pct": exp}


def _period_metrics(exp=1.0, mdd=-20.0, median=7.0, trades=5):
    return {
        "expectancy_pct": exp,
        "max_drawdown_pct": mdd,
        "median_holding_days": median,
        "trade_count": trades,
    }


def _eligible_periods():
    return {
        "train_1": _period_metrics(exp=1.0, mdd=-50.0, median=30.0),
        "train_2": _period_metrics(exp=1.5, mdd=-25.0, median=20.0),
        "recent_1y": _period_metrics(exp=2.0, mdd=-5.0, median=14.0),
        # stress는 profile/eligibility에서 무시되어야 한다.
        "stress_pre_2022h1": _period_metrics(exp=-99.0, mdd=-99.0, median=99.0),
    }


def test_stage3_qualify_passes_when_three_years_meet_boundaries():
    per_year = {
        "2022": _year_metrics(exp=2.0),
        "2023": _year_metrics(exp=2.5),
        "2024": _year_metrics(exp=3.0),
    }

    assert stage3_qualify_fail_reasons(per_year, DEFAULT_STAGE3_QUALIFY) == []


def test_stage3_qualify_fails_when_any_year_expectancy_is_below_two():
    per_year = {
        "2022": _year_metrics(exp=2.0),
        "2023": _year_metrics(exp=1.9),
        "2024": _year_metrics(exp=3.0),
    }

    reasons = stage3_qualify_fail_reasons(per_year)

    assert any(r["metric"] == "expectancy_pct" and r.get("year") == "2023" for r in reasons)
    assert any(r["metric"] == "qualify_pass_count" for r in reasons)


def test_stage3_qualify_fails_when_a_year_is_missing():
    per_year = {
        "2022": _year_metrics(exp=2.0),
        "2023": _year_metrics(exp=2.0),
    }

    reasons = stage3_qualify_fail_reasons(per_year)

    assert any(r["metric"] == "year_count" for r in reasons)
    assert any(r["metric"] == "qualify_pass_count" for r in reasons)


def test_stage3_qualify_fails_trade_or_member_shortfall():
    per_year = {
        "2022": _year_metrics(exp=2.0, trades=4),
        "2023": _year_metrics(exp=2.0, member=9.9),
        "2024": _year_metrics(exp=2.0),
    }

    reasons = stage3_qualify_fail_reasons(per_year)

    assert any(r["metric"] == "trade_count" and r.get("year") == "2022" for r in reasons)
    assert any(r["metric"] == "member_score" and r.get("year") == "2023" for r in reasons)
    assert any(r["metric"] == "qualify_pass_count" for r in reasons)


def test_stage3_basic_eligibility_passes_when_all_oos_expectancy_meets_floor():
    periods = {
        "train_1": _period_metrics(exp=1.0, mdd=-50.0, median=30.0),
        "train_2": _period_metrics(exp=1.1, mdd=-99.0, median=99.0),
        "recent_1y": _period_metrics(exp=2.0, mdd=-30.0, median=30.0),
    }

    assert stage3_basic_eligibility(periods, DEFAULT_STAGE3_PROFILE) == []


def test_stage3_basic_eligibility_fails_when_any_oos_expectancy_is_below_floor():
    periods = _eligible_periods()
    periods["train_2"] = _period_metrics(exp=0.9, mdd=-1.0, median=1.0)

    reasons = stage3_basic_eligibility(periods)

    assert any(r["metric"] == "expectancy_pct" and r.get("period") == "train_2" for r in reasons)


def test_stage3_basic_eligibility_fails_when_required_oos_period_is_missing():
    periods = _eligible_periods()
    del periods["train_2"]

    reasons = stage3_basic_eligibility(periods)

    assert any(
        r["metric"] == "period" and r.get("period") == "train_2" and r.get("reason") == "missing_oos_period"
        for r in reasons
    )


def test_stage3_basic_eligibility_boundary_one_point_zero_passes():
    periods = {
        "train_1": _period_metrics(exp=1.0),
        "train_2": _period_metrics(exp=1.0),
        "recent_1y": _period_metrics(exp=1.0),
    }

    assert stage3_basic_eligibility(periods) == []


def test_stage3_basic_eligibility_ignores_bad_mdd_and_long_holding():
    periods = {
        "train_1": _period_metrics(exp=1.2, mdd=-50.0, median=30.0),
        "train_2": _period_metrics(exp=1.3, mdd=-80.0, median=99.0),
        "recent_1y": _period_metrics(exp=1.4, mdd=-60.0, median=45.0),
    }

    assert stage3_basic_eligibility(periods) == []


def test_stage3_profile_labels_mid_low_mdd_mid_exp_and_preserves_metrics():
    periods = {
        "train_1": _period_metrics(exp=3.1, mdd=-8.0, median=29.0),
        "train_2": _period_metrics(exp=4.2, mdd=-4.0, median=22.0),
        "recent_1y": _period_metrics(exp=3.0, mdd=-8.0, median=14.0),
    }

    profile = stage3_profile(periods)

    assert profile["holding_class"] == "mid"
    assert profile["risk_class"] == "low_mdd"
    assert profile["return_class"] == "mid_exp"
    assert profile["composite_tag"] == "mid|low_mdd|mid_exp"
    assert profile["period_metrics"]["train_1"]["expectancy_pct"] == 3.1
    assert profile["period_metrics"]["train_2"]["max_drawdown_pct"] == -4.0
    assert profile["period_metrics"]["recent_1y"]["median_holding_days"] == 14.0


def test_stage3_profile_boundary_labels():
    ultra = {
        "train_1": _period_metrics(exp=1.0, mdd=-1.0, median=1.0),
        "train_2": _period_metrics(exp=1.0, mdd=-1.0, median=1.0),
        "recent_1y": _period_metrics(exp=4.0, mdd=-10.0, median=7.0),
    }
    mid_mdd = {
        "train_1": _period_metrics(exp=1.0),
        "train_2": _period_metrics(exp=1.0),
        "recent_1y": _period_metrics(exp=2.0, mdd=-20.0, median=14.0),
    }
    high_mdd = {
        "train_1": _period_metrics(exp=1.0),
        "train_2": _period_metrics(exp=1.0),
        "recent_1y": _period_metrics(exp=1.0, mdd=-20.1, median=14.1),
    }

    assert stage3_profile(ultra)["composite_tag"] == "ultra_short|low_mdd|high_exp"
    assert stage3_profile(mid_mdd)["composite_tag"] == "mid|mid_mdd|mid_exp"
    assert stage3_profile(high_mdd)["composite_tag"] == "long|high_mdd|low_exp"


def _write_stage3_final_rulebook(path):
    row = {
        "ticker": "TST",
        "entry_rank": 1,
        "entry_rulebook_hash": "entry-hash-1",
        "exit_rank": 2,
        "composite_fitness": 9.9,
        "rulebook": {
            "ticker": "TST",
            "stop_loss_atr": 2.1,
            "stop_loss_atr_bear": 1.4,
            "max_holding_days": 17,
        },
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def _patch_stage3_validate_dependencies(monkeypatch, runner, trades_by_label):
    class DummyRulebook:
        def __init__(self, data):
            self._data = dict(data)

        def to_dict(self):
            return dict(self._data)

    class DummyRulebookFactory:
        @staticmethod
        def from_dict(data):
            return DummyRulebook(data)

    def fake_validate_one_period(*, rulebook, ctx, period, end_override=None):
        label = str(period.get("label"))
        trades = list(trades_by_label.get(label, []))
        return {
            "label": label,
            "period": {"start": period.get("start"), "end": end_override if end_override is not None else period.get("end")},
            "metrics": {
                "trade_count": len(trades),
                "win_count": len([t for t in trades if float(t.get("pnl_pct", 0.0)) > 0.0]),
                "loss_count": len([t for t in trades if float(t.get("pnl_pct", 0.0)) < 0.0]),
                "win_rate": 100.0,
                "expectancy_pct": 1.2,
                "avg_return_pct": 1.2,
                "profit_factor": 2.0,
                "max_drawdown_pct": -3.0,
                "fitness": 10.0,
                "median_holding_days": 3.0,
            },
            "holding_summary": {"count": len(trades), "mean": 3.0 if trades else None, "median": 3.0 if trades else None, "p75": 3.0 if trades else None, "p90": 3.0 if trades else None, "max": 3.0 if trades else None},
            "exit_reason_distribution": {"time_out": len(trades)} if trades else {},
            "trades": trades,
        }

    monkeypatch.setattr(runner, "prepare_ticker_context", lambda ticker: {"data_end": "2026-06-12"})
    monkeypatch.setattr(runner, "Rulebook", DummyRulebookFactory)
    monkeypatch.setattr(runner, "compute_rulebook_hash", lambda rb: "final-hash-1")
    monkeypatch.setattr(runner, "_validate_one_period", fake_validate_one_period)
    monkeypatch.setattr(runner, "stage3_basic_eligibility", lambda per_period_metrics, config: [])
    monkeypatch.setattr(
        runner,
        "stage3_profile",
        lambda per_period_metrics, config: {
            "holding_class": "mid",
            "risk_class": "low_mdd",
            "return_class": "low_exp",
            "composite_tag": "mid|low_mdd|low_exp",
            "period_metrics": dict(per_period_metrics),
            "config": {"fake": True},
        },
    )


def _sample_exit_trade(label="train_1"):
    return {
        "entry_date": "2025-01-02",
        "exit_date": "2025-01-06",
        "holding_days": 3,
        "pnl_pct": -2.5,
        "exit_reason": "time_out",
        "max_profit_during_hold": 1.1,
        "max_loss_during_hold": -3.2,
        "entry_price": 100.0,
        "exit_price": 97.5,
        "stop_price_at_entry": 95.0,
        "target_price_at_entry": 110.0,
        "trailing_stop_at_entry": 96.0,
        "breakeven_enabled": False,
        "breakeven_trigger_profit_pct": 5.0,
        "sell_omen_enabled": True,
        "rulebook_full": {
            "stop_loss_atr": 2.2,
            "stop_loss_atr_bear": 1.5,
            "max_holding_days": 18,
        },
        "debug_label": label,
    }


def test_run_validate_writes_exit_trades_jsonl_and_preserves_existing_outputs(tmp_path, monkeypatch):
    from scripts.research import run_stage3_aggressive as runner

    _write_stage3_final_rulebook(tmp_path / "final_rulebooks.jsonl")
    trades_by_label = {
        "train_1": [_sample_exit_trade("train_1")],
        "train_2": [_sample_exit_trade("train_2")],
        "recent_1y": [_sample_exit_trade("recent_1y")],
        "stress_pre_2022h1": [_sample_exit_trade("stress_pre_2022h1")],
    }
    _patch_stage3_validate_dependencies(monkeypatch, runner, trades_by_label)

    result = runner.run_validate("TST", tmp_path, seed_base=123)

    assert result["candidate_count"] == 1
    assert (tmp_path / "exit_trades.jsonl").exists()
    assert (tmp_path / "validation_results.jsonl").exists()
    assert (tmp_path / "validate_result.json").exists()
    assert (tmp_path / "stage3_profile_catalog.jsonl").exists()
    assert (tmp_path / "stage3_ineligible.jsonl").exists()

    rows = [json.loads(line) for line in (tmp_path / "exit_trades.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 4
    assert {row["period_label"] for row in rows} == {"train_1", "train_2", "recent_1y", "stress_pre_2022h1"}
    assert all(row["final_rulebook_hash"] == "final-hash-1" for row in rows)
    assert all(row["entry_rulebook_hash"] == "entry-hash-1" for row in rows)
    assert all(row["exit_rank"] == 2 for row in rows)
    assert rows[0]["holding_days"] == 3
    assert rows[0]["pnl_pct"] == -2.5
    assert rows[0]["exit_reason"] == "time_out"
    assert rows[0]["stop_loss_atr"] == 2.2
    assert rows[0]["stop_loss_atr_bear"] == 1.5
    assert rows[0]["max_holding_days"] == 18

    validation_rows = [json.loads(line) for line in (tmp_path / "validation_results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "trades" not in validation_rows[0]["period_results"]["train_1"]
    assert "trades" not in validation_rows[0]["period_results"]["stress_pre_2022h1"]


def test_run_validate_writes_empty_exit_trades_when_backtests_have_no_trades(tmp_path, monkeypatch):
    from scripts.research import run_stage3_aggressive as runner

    _write_stage3_final_rulebook(tmp_path / "final_rulebooks.jsonl")
    _patch_stage3_validate_dependencies(monkeypatch, runner, {})

    runner.run_validate("TST", tmp_path, seed_base=123)

    assert (tmp_path / "exit_trades.jsonl").exists()
    assert (tmp_path / "exit_trades.jsonl").read_text(encoding="utf-8") == ""
    assert (tmp_path / "validation_results.jsonl").exists()
    assert (tmp_path / "validate_result.json").exists()
    assert (tmp_path / "stage3_profile_catalog.jsonl").exists()

