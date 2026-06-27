from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from engine.central.allocation_policy import AllocationParams
from engine.live.scheduled_open_buy_queue import NextOpenBuyCoordinator, load_queue
from engine.strategies.demo_rulebook import Signal, SignalResult

ET = ZoneInfo("America/New_York")


class Calendar:
    def session_dates(self, start, end):
        out = []
        cur = start
        while cur <= end:
            if cur.weekday() < 5:
                out.append(cur)
            cur += timedelta(days=1)
        return out

    def session_open(self, session):
        return datetime.combine(session, time(9, 30), tzinfo=ET)


class Clock:
    name = "US"
    calendar = Calendar()

    def is_business_day(self, now):
        day = now.date() if isinstance(now, datetime) else now
        return day.weekday() < 5

    def next_open(self, now):
        day = now.date()
        while not self.is_business_day(day):
            day += timedelta(days=1)
        return datetime.combine(day, time(9, 30), tzinfo=ET)


class RulebookProvider:
    def __init__(self, last_date: date):
        self.last_date = last_date

    def _get_ohlcv(self, ticker):
        idx = pd.date_range(end=pd.Timestamp(self.last_date), periods=70, freq="B")
        return pd.DataFrame({"Close": [100.0] * len(idx), "ATR": [1.0] * len(idx)}, index=idx)


class Controller:
    def __init__(self, tmp_path: Path, last_date: date):
        rb = {"ticker": "AAA", "stop_loss_atr": 1.0, "take_profit_atr": 3.0}
        self.entity = SimpleNamespace(entity_id="AAA_abc123def456", ticker="AAA", confidence=2.0, rulebook=rb, tags={"stage": "stage2"})
        self.entity_by_ticker = {"AAA": [self.entity]}
        self.selection_metric = "confidence"
        self.selection_scores = {}
        self.runner = SimpleNamespace(
            rulebook=RulebookProvider(last_date),
            position_manager=SimpleNamespace(all=lambda: []),
            pending_order_manager=SimpleNamespace(all=lambda: []),
            broker=SimpleNamespace(get_holdings=lambda: [], get_current_price=lambda ticker: 101.0),
        )
        self.executed = []
        self.tmp_path = tmp_path

    def _now_et(self):
        return datetime(2026, 6, 29, 9, 20, tzinfo=ET)

    def _is_live_whitelisted_ticker(self, ticker):
        return True

    def _candidate_strength_for_entity(self, entity, sig, confidence):
        return 2.0, 2.0, ""

    def _evaluate_entity_signal(self, entity, price):
        return SignalResult(
            ticker=entity.ticker,
            signal=Signal.BUY,
            price=price,
            reason="test",
            score=4.0,
            raw_score=4.0,
            threshold=2.0,
            market_adjustment=1.0,
            reasons=["test"],
        )

    def _allocation_params(self):
        return AllocationParams(max_positions=8, total_capital=8000.0, position_sizing="equal", cash_buffer_ratio=1.0)

    def _execute_decision(self, decision, signal_result, price, *, execution_reason="auto", manual_intent_id=""):
        self.executed.append((decision, price, execution_reason))
        return True


def test_prepare_queue_uses_previous_session_close_and_preserves_rulebook(tmp_path: Path):
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")

    result = coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    assert result["decision_count"] == 1
    payload = load_queue(tmp_path / "queue.json")
    item = payload["items"][0]
    assert item["signal_session"] == "2026-06-26"
    assert item["execution_session"] == "2026-06-29"
    assert item["entity_id"] == "AAA_abc123def456"
    assert item["rulebook"]["take_profit_atr"] == 3.0
    assert item["reference_price"] == 100.0


def test_prepare_queue_skips_stale_bar(tmp_path: Path):
    controller = Controller(tmp_path, last_date=date(2026, 6, 25))
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")

    result = coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    assert result["decision_count"] == 0
    payload = load_queue(tmp_path / "queue.json")
    assert payload["items"] == []
    assert payload["diagnostics"]["skipped"][0]["reason"] == "stale_bar"


def test_execute_queue_waits_until_positions_holdings_and_pending_are_flat(tmp_path: Path):
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    controller.runner.position_manager = SimpleNamespace(all=lambda: [object()])
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))

    assert result["status"] == "waiting_for_clear"
    assert controller.executed == []


def test_execute_queue_executes_pending_items_when_flat(tmp_path: Path):
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))

    assert result["executed"] == 1
    assert controller.executed[0][0].entity_id == "AAA_abc123def456"
    assert controller.executed[0][0].rulebook["stop_loss_atr"] == 1.0
    assert controller.executed[0][2] == "next_open_queue"
