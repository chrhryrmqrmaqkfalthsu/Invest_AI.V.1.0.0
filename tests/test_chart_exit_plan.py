import json
from pathlib import Path

from engine.live.chart_exit_plan import evaluate_chart_exit_plans, load_chart_exit_state, upsert_chart_exit_plan
from engine.live.manual_sell_intent import load_manual_sell_state


def _positions(path: Path):
    path.write_text(
        json.dumps(
            {
                "ABC": {
                    "ticker": "ABC",
                    "shares": 10,
                    "entry_price": 100,
                    "entry_date": "2026-07-02T14:00:00+00:00",
                }
            }
        ),
        encoding="utf-8",
    )


def _regular(monkeypatch, allowed=True):
    monkeypatch.setattr(
        "engine.live.chart_exit_plan.regular_hours_snapshot",
        lambda: {"allow_decision": allowed, "reason": "regular" if allowed else "closed"},
    )


def test_upsert_chart_exit_plan_requires_held_position(tmp_path):
    pos = tmp_path / "positions.json"
    plan = tmp_path / "plans.json"
    _positions(pos)
    row = upsert_chart_exit_plan(
        ticker="ABC",
        take_profit_price=110,
        stop_loss_price=95,
        positions_path=pos,
        plan_path=plan,
    )
    assert row["ticker"] == "ABC"
    assert row["status"] == "active"
    assert row["take_profit_price"] == 110
    assert row["stop_loss_price"] == 95


def test_chart_exit_plan_skips_outside_regular_hours(tmp_path, monkeypatch):
    pos = tmp_path / "positions.json"
    plan = tmp_path / "plans.json"
    intent = tmp_path / "intent.json"
    _positions(pos)
    _regular(monkeypatch, allowed=False)
    upsert_chart_exit_plan(ticker="ABC", take_profit_price=101, positions_path=pos, plan_path=plan)
    result = evaluate_chart_exit_plans(
        price_lookup=lambda ticker: 120,
        plan_path=plan,
        positions_path=pos,
        intent_path=intent,
    )
    assert result["skipped"] is True
    assert result["triggered"] == []
    state = load_manual_sell_state(intent)
    assert state["intents"] == {}


def test_chart_exit_plan_triggers_take_profit_intent_regular_hours(tmp_path, monkeypatch):
    pos = tmp_path / "positions.json"
    plan = tmp_path / "plans.json"
    intent = tmp_path / "intent.json"
    _positions(pos)
    _regular(monkeypatch, allowed=True)
    upsert_chart_exit_plan(ticker="ABC", take_profit_price=110, stop_loss_price=90, positions_path=pos, plan_path=plan)
    result = evaluate_chart_exit_plans(
        price_lookup=lambda ticker: 111,
        plan_path=plan,
        positions_path=pos,
        intent_path=intent,
    )
    assert result["skipped"] is False
    assert len(result["triggered"]) == 1
    assert result["triggered"][0]["trigger_kind"] == "take_profit"
    state = load_manual_sell_state(intent)
    rows = list(state["intents"].values())
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABC"
    assert rows[0]["source"] == "chart_exit_plan"
    assert rows[0]["reason"] == "chart_take_profit"
    assert rows[0]["metadata"]["trigger_kind"] == "take_profit"


def test_chart_exit_plan_triggers_stop_loss_intent_regular_hours(tmp_path, monkeypatch):
    pos = tmp_path / "positions.json"
    plan = tmp_path / "plans.json"
    intent = tmp_path / "intent.json"
    _positions(pos)
    _regular(monkeypatch, allowed=True)
    upsert_chart_exit_plan(ticker="ABC", take_profit_price=110, stop_loss_price=95, positions_path=pos, plan_path=plan)
    result = evaluate_chart_exit_plans(
        price_lookup=lambda ticker: 94.5,
        plan_path=plan,
        positions_path=pos,
        intent_path=intent,
    )
    assert len(result["triggered"]) == 1
    row = list(load_manual_sell_state(intent)["intents"].values())[0]
    assert row["reason"] == "chart_stop_loss"
