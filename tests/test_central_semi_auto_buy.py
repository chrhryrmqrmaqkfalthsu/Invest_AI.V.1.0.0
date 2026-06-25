from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from engine.central.allocation_policy import BuyDecision
from engine.live.central_control import LiveCentralControlConfig, LiveCentralController
from engine.live.manual_buy_intent import (
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
    def __init__(self, *, block: bool = False):
        self.stats = Stats()
        self.order_notional = 30.0
        self.orders = []
        self.block = block

    def _try_order(self, side, ticker, price, reason, signal_result=None):
        self.stats.orders_attempted += 1
        if self.block:
            self.stats.orders_blocked += 1
            return None
        self.orders.append(
            {
                "side": side,
                "ticker": ticker,
                "price": price,
                "reason": reason,
                "order_notional": self.order_notional,
                "signal_result": signal_result,
            }
        )
        return None


def make_controller(tmp_path: Path, *, block: bool = False, now_et: datetime | None = None):
    runner = Runner(block=block)
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
    ctl._now_et = lambda: now_et or datetime(2026, 6, 25, 10, 0, tzinfo=ET)
    return ctl, runner, cfg


def decision(ticker="AAA", entity_id="AAA_entity", notional=1234.5):
    return BuyDecision(
        entity_id=entity_id,
        ticker=ticker,
        shares=12.345,
        notional=notional,
        score=2.5,
        confidence=1.5,
        strength=1.0,
    )


def signal(score=3.0, threshold=2.0):
    return SimpleNamespace(score=score, threshold=threshold)


def test_semi_auto_publishes_candidates_without_immediate_order(tmp_path):
    ctl, runner, cfg = make_controller(tmp_path, now_et=datetime(2026, 6, 25, 10, 0, tzinfo=ET))
    d = decision()

    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})

    assert runner.orders == []
    state = load_candidate_state(cfg.candidate_state_path)
    cid = candidate_id_for("2026-06-25", d.entity_id)
    assert cid in state["candidates"]
    assert state["candidates"][cid]["status"] == "pending"
    assert state["candidates"][cid]["notional"] == 1234.5


def test_manual_intent_executes_through_runner_with_central_reason_and_fixed_notional(tmp_path):
    ctl, runner, cfg = make_controller(tmp_path, now_et=datetime(2026, 6, 25, 10, 0, tzinfo=ET))
    d = decision(notional=2222.0)
    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 101.0})
    cid = candidate_id_for("2026-06-25", d.entity_id)
    intent = create_manual_buy_intent(candidate_id=cid, candidate_path=cfg.candidate_state_path, intent_path=cfg.manual_intent_path)

    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 101.0})

    assert len(runner.orders) == 1
    order = runner.orders[0]
    assert order["side"] == "BUY"
    assert order["ticker"] == "AAA"
    assert order["order_notional"] == 2222.0
    assert "central_control manual_timing" in order["reason"]
    assert intent["intent_id"] in order["reason"]
    assert runner.order_notional == 30.0
    intent_state = read_json(cfg.manual_intent_path, {})
    assert intent_state["intents"][intent["intent_id"]]["status"] == "consumed"
    state = load_candidate_state(cfg.candidate_state_path)
    assert state["candidates"][cid]["status"] == "manual_executed"


def test_auto_fallback_after_1530_executes_pending_candidate(tmp_path):
    ctl, runner, cfg = make_controller(tmp_path, now_et=datetime(2026, 6, 25, 15, 31, tzinfo=ET))
    d = decision(ticker="BBB", entity_id="BBB_entity", notional=3333.0)

    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 88.0})

    assert len(runner.orders) == 1
    assert runner.orders[0]["ticker"] == "BBB"
    assert runner.orders[0]["order_notional"] == 3333.0
    assert "central_control auto_fallback" in runner.orders[0]["reason"]
    cid = candidate_id_for("2026-06-25", d.entity_id)
    state = load_candidate_state(cfg.candidate_state_path)
    assert state["candidates"][cid]["status"] == "auto_executed"


def test_manual_executed_candidate_is_not_auto_fallback_duplicate(tmp_path):
    ctl, runner, cfg = make_controller(tmp_path, now_et=datetime(2026, 6, 25, 10, 0, tzinfo=ET))
    d = decision()
    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})
    cid = candidate_id_for("2026-06-25", d.entity_id)
    create_manual_buy_intent(candidate_id=cid, candidate_path=cfg.candidate_state_path, intent_path=cfg.manual_intent_path)
    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})
    assert len(runner.orders) == 1

    ctl._now_et = lambda: datetime(2026, 6, 25, 15, 31, tzinfo=ET)
    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})

    assert len(runner.orders) == 1
    state = load_candidate_state(cfg.candidate_state_path)
    assert state["candidates"][cid]["status"] == "manual_executed"


def test_runner_block_marks_manual_intent_blocked_and_no_executed_status(tmp_path):
    ctl, runner, cfg = make_controller(tmp_path, block=True, now_et=datetime(2026, 6, 25, 10, 0, tzinfo=ET))
    d = decision()
    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})
    cid = candidate_id_for("2026-06-25", d.entity_id)
    intent = create_manual_buy_intent(candidate_id=cid, candidate_path=cfg.candidate_state_path, intent_path=cfg.manual_intent_path)

    ctl._process_semi_auto_decisions([d], {d.entity_id: signal()}, {d.entity_id: 100.0})

    assert runner.orders == []
    intent_state = read_json(cfg.manual_intent_path, {})
    assert intent_state["intents"][intent["intent_id"]]["status"] == "blocked"
    state = load_candidate_state(cfg.candidate_state_path)
    assert state["candidates"][cid]["status"] == "blocked"
