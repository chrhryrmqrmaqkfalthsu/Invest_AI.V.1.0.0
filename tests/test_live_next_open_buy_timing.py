from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from engine.central.allocation_policy import AllocationParams
from engine.live import scheduled_open_buy_queue as queue_mod
from engine.live.broker.base import Balance, Broker, BrokerError, Holding, Order, OrderSide, OrderStatus, OrderType
from engine.live.scheduled_open_buy_queue import NextOpenBuyCoordinator, load_queue

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
        return pd.DataFrame(
            {
                "Open": [99.0] * len(idx),
                "High": [101.0] * len(idx),
                "Low": [98.0] * len(idx),
                "Close": [100.0] * len(idx),
                "Volume": [1000000.0] * len(idx),
                "ATR": [1.0] * len(idx),
            },
            index=idx,
        )

    def _load_ticker_sentiment(self, ticker):
        return {"2026-06-25": {"sentiment_avg": 0.25}}


class Controller:
    def __init__(self, tmp_path: Path, last_date: date):
        rb = {
            "ticker": "AAA",
            "asset_type": "us_stock",
            "direction": "long",
            "sector_name": "tech",
            "signal_threshold": 1.0,
            "stop_loss_atr": 1.0,
            "take_profit_atr": 3.0,
            "trailing_atr": 1.0,
            "max_holding_days": 10,
            "fitness": 20.0,
            "win_rate": 60.0,
            "trade_count": 20,
        }
        self.entity = SimpleNamespace(entity_id="AAA_abc123def456", ticker="AAA", confidence=2.0, rulebook=rb, tags={"stage": "stage2"})
        self.entity_by_ticker = {"AAA": [self.entity]}
        self.selection_metric = "confidence"
        self.selection_scores = {}
        self.runner = SimpleNamespace(
            rulebook=RulebookProvider(last_date),
            position_manager=SimpleNamespace(all=lambda: [], get=lambda ticker: None),
            pending_order_manager=SimpleNamespace(all=lambda: [], has_pending_buy=lambda ticker: False),
            broker=SimpleNamespace(get_holdings=lambda: [], get_open_orders=lambda: [], get_current_price=lambda ticker: 101.0),
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
        raise AssertionError("next_open must not call live/fresh _evaluate_entity_signal")

    def _allocation_params(self):
        return AllocationParams(max_positions=8, total_capital=8000.0, position_sizing="equal", cash_buffer_ratio=1.0)

    def _execute_decision(self, decision, signal_result, price, *, execution_reason="auto", manual_intent_id=""):
        self.executed.append((decision, price, execution_reason))
        return True


class BrokerWithoutOpenOrders(Broker):
    @property
    def mode(self):
        return "paper"

    def get_balance(self):
        return Balance(cash_krw=0, total_value_krw=0, invested_krw=0, holdings=[])

    def get_holdings(self):
        return []

    def get_current_price(self, ticker):
        return 101.0

    def is_market_open(self, ticker=None):
        return True

    def place_buy(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
        return Order("B1", ticker, OrderSide.BUY, order_type, shares, price, OrderStatus.PENDING)

    def place_sell(self, ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=""):
        return Order("S1", ticker, OrderSide.SELL, order_type, shares, price, OrderStatus.PENDING)

    def cancel_order(self, order_id):
        return False

    def get_order(self, order_id):
        return None


def _market_history():
    idx = pd.to_datetime(["2026-06-24", "2026-06-25", "2026-06-26"])
    return pd.DataFrame(
        {
            "score": [51.0, 66.0, 99.0],
            "vix": [18.5, 19.25, 99.0],
            "sector_tech": [55.0, 72.0, 99.0],
            "has_war": [0, 1, 1],
        },
        index=idx,
    )


def _patch_point_in_time_eval(monkeypatch):
    captured = {}

    def fake_get_market_history(*args, **kwargs):
        captured["get_market_history_called"] = captured.get("get_market_history_called", 0) + 1
        return _market_history()

    def fake_evaluate_signal(*, rb, df, market_score, sector_score, vix_level, news_sentiment, event_flags, topic_features):
        captured["ticker"] = rb.ticker
        captured["market_score"] = market_score
        captured["sector_score"] = sector_score
        captured["vix_level"] = vix_level
        captured["news_sentiment"] = news_sentiment
        captured["event_flags"] = dict(event_flags or {})
        captured["topic_features"] = dict(topic_features or {})
        return SimpleNamespace(
            should_buy=True,
            score=4.0,
            raw_score=4.0,
            threshold=2.0,
            market_adjustment=1.0,
            reasons=["test"],
            components={},
        )

    monkeypatch.setattr(queue_mod, "get_market_history", fake_get_market_history)
    monkeypatch.setattr(queue_mod, "evaluate_signal", fake_evaluate_signal)
    return captured


def test_prepare_queue_uses_previous_session_close_and_preserves_rulebook(tmp_path: Path, monkeypatch):
    captured = _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")

    result = coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    assert result["decision_count"] == 1
    assert captured["get_market_history_called"] == 1
    payload = load_queue(tmp_path / "queue.json")
    item = payload["items"][0]
    assert item["signal_session"] == "2026-06-26"
    assert item["execution_session"] == "2026-06-29"
    assert item["entity_id"] == "AAA_abc123def456"
    assert item["rulebook"]["take_profit_atr"] == 3.0
    assert item["reference_price"] == 100.0


def test_prepare_queue_uses_lagged_market_history_and_not_fresh_context(tmp_path: Path, monkeypatch):
    captured = _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")

    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    assert captured["market_score"] == 66.0
    assert captured["sector_score"] == 72.0
    assert captured["vix_level"] == 19.25
    assert captured["news_sentiment"] == 0.25
    # Stage2/Stage3 training passed use_llm_events=False, so event flags are zeroed
    # even if market_history has v2 event columns.
    assert captured["event_flags"].get("has_war") == 0


def test_prepare_queue_skips_stale_bar(tmp_path: Path, monkeypatch):
    _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 25))
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")

    result = coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    assert result["decision_count"] == 0
    payload = load_queue(tmp_path / "queue.json")
    assert payload["items"] == []
    assert payload["diagnostics"]["skipped"][0]["reason"] == "stale_bar"


def test_execute_queue_waits_until_positions_holdings_and_pending_are_flat(tmp_path: Path, monkeypatch):
    _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    controller.runner.position_manager = SimpleNamespace(all=lambda: [object()])
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))

    assert result["status"] == "waiting_for_clear"
    assert controller.executed == []


def test_execute_queue_submitted_item_keeps_queue_submitted_not_executed(tmp_path: Path, monkeypatch):
    _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    pending = SimpleNamespace(active=False)
    controller.runner.pending_order_manager = SimpleNamespace(
        all=lambda: [],
        has_pending_buy=lambda ticker: pending.active,
    )

    def submit_only(decision, signal_result, price, *, execution_reason="auto", manual_intent_id=""):
        pending.active = True
        controller.executed.append((decision, price, execution_reason))
        return True

    controller._execute_decision = submit_only
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))
    payload = load_queue(tmp_path / "queue.json")

    assert result["status"] == "submitted"
    assert result["executed"] == 0
    assert result["submitted"] == 1
    assert result["blocked"] == 0
    assert payload["status"] == "submitted"
    assert payload["items"][0]["status"] == "submitted"
    assert payload["items"][0]["note"] == "pending_buy_waiting_for_fill_reconcile"


def test_execute_queue_marks_executed_only_when_position_registered(tmp_path: Path, monkeypatch):
    _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    position = SimpleNamespace(member_hash="abc123def456")
    controller.runner.position_manager = SimpleNamespace(all=lambda: [], get=lambda ticker: position)
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))
    payload = load_queue(tmp_path / "queue.json")

    assert result["status"] == "executed"
    assert result["executed"] == 1
    assert result["submitted"] == 0
    assert payload["status"] == "executed"
    assert payload["items"][0]["status"] == "executed"
    assert payload["items"][0]["fills"]["member_hash"] == "abc123def456"


def test_execute_queue_waits_when_broker_open_orders_exist(tmp_path: Path, monkeypatch):
    _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    controller.runner.broker = SimpleNamespace(
        get_holdings=lambda: [],
        get_open_orders=lambda: [SimpleNamespace(status="pending")],
        get_current_price=lambda ticker: 101.0,
    )
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))

    assert result["status"] == "waiting_for_clear"
    assert result["reason"] == "broker_open_orders_not_empty:1"
    assert controller.executed == []


def test_execute_queue_waits_when_broker_open_orders_api_is_not_implemented(tmp_path: Path, monkeypatch):
    _patch_point_in_time_eval(monkeypatch)
    controller = Controller(tmp_path, last_date=date(2026, 6, 26))
    controller.runner.broker = BrokerWithoutOpenOrders()
    coordinator = NextOpenBuyCoordinator(controller=controller, market_clock=Clock(), queue_path=tmp_path / "queue.json")
    coordinator.prepare_queue(execution_session=date(2026, 6, 29))

    result = coordinator.execute_queue(execution_session=date(2026, 6, 29))

    assert result["status"] == "waiting_for_clear"
    assert result["reason"].startswith("broker_open_orders_unavailable:")
    assert controller.executed == []
