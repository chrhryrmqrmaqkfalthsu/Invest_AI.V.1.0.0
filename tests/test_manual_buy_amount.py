from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from engine.central.allocation_policy import BuyDecision
from engine.live.central_control import LiveCentralControlConfig, LiveCentralController
from engine.live.manual_buy_intent import (
    atomic_write_json,
    candidate_id_for,
    create_manual_buy_intent,
    load_candidate_state,
    read_json,
)

ET = ZoneInfo("America/New_York")


class Stats:
    def __init__(self):
        self.orders_attempted = 0
        self.orders_blocked = 0
        self.orders_filled = 0
        self.signals_buy = 0
        self.signals_sell = 0
        self.signals_hold = 0
        self.market_ticks = 0


class Runner:
    def __init__(self):
        self.stats = Stats()
        self.order_notional = 30.0
        self.orders = []

    def _try_order(self, side, ticker, price, reason, signal_result=None, rulebook_override=None):
        self.stats.orders_attempted += 1
        self.orders.append(
            {
                "side": side,
                "ticker": ticker,
                "price": price,
                "reason": reason,
                "order_notional": self.order_notional,
                "signal_result": signal_result,
                "rulebook_override": rulebook_override,
            }
        )


def make_controller(tmp_path: Path):
    runner = Runner()
    cfg = LiveCentralControlConfig(
        buy_mode="semi_auto",
        candidate_state_path=tmp_path / "central_buy_candidates.json",
        manual_intent_path=tmp_path / "manual_buy_intent.json",
        auto_fallback_hour_et=15,
        auto_fallback_minute_et=30,
    )
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.runner = runner
    ctl.config = cfg
    ctl.selection_metric = "confidence"
    ctl.position_sizing = "score_weighted"
    ctl.confidence_mode = "adjusted"
    ctl.buy_mode = "semi_auto"
    ctl._now_et = lambda: datetime(2026, 6, 25, 10, 0, tzinfo=ET)
    return ctl, runner, cfg


def decision(notional=2222.0):
    return BuyDecision(
        entity_id="AAA_entity",
        ticker="AAA",
        shares=22.0,
        notional=notional,
        score=2.5,
        confidence=1.5,
        strength=1.0,
        rulebook={"ticker": "AAA"},
    )


def signal(score=3.0, threshold=2.0):
    return SimpleNamespace(score=score, threshold=threshold)


def test_create_manual_buy_intent_stores_dashboard_amount(tmp_path):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    trade_date = "2026-06-25"
    cid = candidate_id_for(trade_date, "AAA_entity")
    atomic_write_json(
        candidate_path,
        {
            "schema_version": 1,
            "trade_date": trade_date,
            "buy_mode": "semi_auto",
            "candidates": {
                cid: {
                    "candidate_id": cid,
                    "trade_date": trade_date,
                    "status": "pending",
                    "ticker": "AAA",
                    "entity_id": "AAA_entity",
                    "notional": 2222.0,
                    "price": 100.0,
                }
            },
        },
    )

    row = create_manual_buy_intent(
        candidate_id=cid,
        source="dashboard_amount",
        manual_notional=750.0,
        candidate_path=candidate_path,
        intent_path=intent_path,
    )

    assert row["notional"] == 750.0
    assert row["manual_notional"] == 750.0
    assert row["candidate_notional"] == 2222.0
    assert row["notional_source"] == "dashboard_amount"
    state = load_candidate_state(candidate_path)
    assert state["candidates"][cid]["manual_requested_notional"] == 750.0


def test_existing_pending_manual_buy_intent_amount_can_be_updated(tmp_path):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    trade_date = "2026-06-25"
    cid = candidate_id_for(trade_date, "AAA_entity")
    atomic_write_json(
        candidate_path,
        {
            "schema_version": 1,
            "trade_date": trade_date,
            "buy_mode": "semi_auto",
            "candidates": {
                cid: {
                    "candidate_id": cid,
                    "trade_date": trade_date,
                    "status": "pending",
                    "ticker": "AAA",
                    "entity_id": "AAA_entity",
                    "notional": 2222.0,
                    "price": 100.0,
                }
            },
        },
    )
    create_manual_buy_intent(candidate_id=cid, manual_notional=750.0, candidate_path=candidate_path, intent_path=intent_path)

    row = create_manual_buy_intent(candidate_id=cid, manual_notional=900.0, candidate_path=candidate_path, intent_path=intent_path)

    assert row["intent_id"] == f"manual:{cid}"
    assert row["notional"] == 900.0
    intents = read_json(intent_path, {})["intents"]
    assert intents[f"manual:{cid}"]["notional"] == 900.0


def test_semi_auto_manual_intent_executes_with_dashboard_amount(tmp_path):
    ctl, runner, cfg = make_controller(tmp_path)
    d = decision(notional=2222.0)
    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})
    cid = candidate_id_for("2026-06-25", d.entity_id)
    create_manual_buy_intent(
        candidate_id=cid,
        source="dashboard_amount",
        manual_notional=750.0,
        candidate_path=cfg.candidate_state_path,
        intent_path=cfg.manual_intent_path,
    )

    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})

    assert len(runner.orders) == 1
    assert runner.orders[0]["ticker"] == "AAA"
    assert runner.orders[0]["order_notional"] == 750.0
    assert runner.order_notional == 30.0
