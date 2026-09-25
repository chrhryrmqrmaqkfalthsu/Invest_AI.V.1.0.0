from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import engine.live.central_control as central_control
from engine.central.allocation_policy import AllocationParams, BuyCandidate, BuyDecision, decide_buys
from engine.live.central_control import LiveCentralControlConfig, LiveCentralController, _LiveLedgerView, _LiveOpenPosition
from engine.live.manual_buy_intent import atomic_write_json, candidate_id_for, load_candidate_state

ET = ZoneInfo("America/New_York")


class Stats:
    def __init__(self):
        self.orders_attempted = 0
        self.orders_blocked = 0
        self.signals_buy = 0
        self.signals_sell = 0
        self.signals_hold = 0
        self.market_ticks = 0


class Runner:
    def __init__(self):
        self.stats = Stats()
        self.order_notional = 30.0
        self.orders = []

    def _try_order(self, side, ticker, price, reason, signal_result=None):
        self.stats.orders_attempted += 1
        self.orders.append({"side": side, "ticker": ticker, "price": price, "reason": reason})


def make_controller(tmp_path: Path, *, now_et: datetime | None = None) -> LiveCentralController:
    cfg = LiveCentralControlConfig(
        buy_mode="semi_auto",
        candidate_state_path=tmp_path / "central_buy_candidates.json",
        manual_intent_path=tmp_path / "manual_buy_intent.json",
        auto_fallback_hour_et=15,
        auto_fallback_minute_et=30,
    )
    ctl = LiveCentralController.__new__(LiveCentralController)
    ctl.runner = Runner()
    ctl.config = cfg
    ctl.selection_metric = "confidence"
    ctl.position_sizing = "score_weighted"
    ctl.confidence_mode = "adjusted"
    ctl.buy_mode = "semi_auto"
    ctl._now_et = lambda: now_et or datetime(2026, 6, 25, 10, 0, tzinfo=ET)
    return ctl


def candidate(ticker: str, confidence: float) -> BuyCandidate:
    return BuyCandidate(
        entity_id=f"{ticker}_entity",
        ticker=ticker,
        confidence=confidence,
        strength=1.0,
        price=100.0,
        signal_score=3.0,
        threshold=2.0,
        rulebook={},
    )


def decision(ticker: str, notional: float = 1000.0) -> BuyDecision:
    return BuyDecision(
        entity_id=f"{ticker}_entity",
        ticker=ticker,
        shares=10.0,
        notional=notional,
        score=2.0,
        confidence=1.0,
        strength=1.0,
    )


def write_candidate_state(path: Path, trade_date: str, rows: list[dict]) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "trade_date": trade_date,
            "buy_mode": "semi_auto",
            "updated_at": "2026-06-25T00:00:00+00:00",
            "candidates": {row["candidate_id"]: row for row in rows},
        },
    )


def write_trade_log(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "exited_at", "ticker", "direction", "entry_date", "entry_price", "exit_price",
        "shares", "exit_reason", "holding_days", "highest_price", "lowest_price",
        "mfe_pct", "mae_pct", "pnl_pct", "pnl_krw", "exit_strategy",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(",".join(fields) + "\n")
        for row in rows:
            f.write(",".join(str(row.get(k, "")) for k in fields) + "\n")


def test_same_day_terminal_and_sold_tickers_are_skipped_before_decision(tmp_path, monkeypatch):
    ctl = make_controller(tmp_path)
    trade_date = "2026-06-25"
    write_candidate_state(
        ctl.config.candidate_state_path,
        trade_date,
        [
            {
                "candidate_id": candidate_id_for(trade_date, "AAA_entity"),
                "trade_date": trade_date,
                "status": "manual_executed",
                "ticker": "AAA",
                "entity_id": "AAA_entity",
            }
        ],
    )
    trade_log = tmp_path / "trade_log.csv"
    # 2026-06-26 04:30 KST is still 2026-06-25 in US/Eastern trading-date terms.
    write_trade_log(trade_log, [{"exited_at": "2026-06-26T04:30:00+09:00", "ticker": "CCC"}])
    monkeypatch.setattr(central_control, "TRADE_LOG_PATH", trade_log)

    blocked = ctl._same_day_reentry_blocked_tickers(trade_date)
    filtered = [c for c in [candidate("AAA", 9.0), candidate("CCC", 8.0), candidate("BBB", 7.0)] if c.ticker not in blocked]
    decisions = decide_buys(
        filtered,
        _LiveLedgerView([]),
        AllocationParams(max_positions=1, total_capital=10_000, position_sizing="equal"),
    )

    assert blocked == {"AAA", "CCC"}
    assert len(decisions) == 1
    assert decisions[0].ticker == "BBB"


def test_trade_log_exit_on_different_et_date_is_not_blocked(tmp_path, monkeypatch):
    ctl = make_controller(tmp_path)
    write_candidate_state(ctl.config.candidate_state_path, "2026-06-25", [])
    trade_log = tmp_path / "trade_log.csv"
    # 13:00 KST is 00:00 ET on 2026-06-26, so it must not block 2026-06-25.
    write_trade_log(trade_log, [{"exited_at": "2026-06-26T13:00:00+09:00", "ticker": "CCC"}])
    monkeypatch.setattr(central_control, "TRADE_LOG_PATH", trade_log)

    assert ctl._same_day_reentry_blocked_tickers("2026-06-25") == set()


def test_existing_held_ticker_is_still_excluded_by_allocation_policy():
    open_pos = _LiveOpenPosition(
        entity_id="DDD_live",
        ticker="DDD",
        open_shares=10.0,
        avg_entry_price=100.0,
        current_price=100.0,
    )

    decisions = decide_buys(
        [candidate("DDD", 9.0), candidate("EEE", 8.0)],
        _LiveLedgerView([open_pos]),
        AllocationParams(max_positions=2, total_capital=10_000, position_sizing="equal"),
    )

    assert len(decisions) == 1
    assert decisions[0].ticker == "EEE"


def test_terminal_preservation_remains_and_new_next_rank_candidate_is_pending(tmp_path):
    ctl = make_controller(tmp_path)
    trade_date = "2026-06-25"
    old_cid = candidate_id_for(trade_date, "AAA_entity")
    write_candidate_state(
        ctl.config.candidate_state_path,
        trade_date,
        [
            {
                "candidate_id": old_cid,
                "trade_date": trade_date,
                "status": "manual_executed",
                "ticker": "AAA",
                "entity_id": "AAA_entity",
                "manual_intent_id": "manual:old",
            }
        ],
    )

    d = decision("BBB")
    ctl._process_semi_auto_decisions([d], {d.entity_id: SimpleNamespace(score=3.0, threshold=2.0)}, {d.entity_id: 100.0})

    state = load_candidate_state(ctl.config.candidate_state_path)
    new_cid = candidate_id_for(trade_date, "BBB_entity")
    assert state["candidates"][old_cid]["status"] == "manual_executed"
    assert state["candidates"][new_cid]["status"] == "pending"
    assert ctl.runner.orders == []


def test_auto_fallback_executes_new_candidate_after_terminal_ticker_was_filtered(tmp_path, monkeypatch):
    ctl = make_controller(tmp_path, now_et=datetime(2026, 6, 25, 15, 31, tzinfo=ET))
    trade_date = "2026-06-25"
    write_candidate_state(
        ctl.config.candidate_state_path,
        trade_date,
        [
            {
                "candidate_id": candidate_id_for(trade_date, "AAA_entity"),
                "trade_date": trade_date,
                "status": "manual_executed",
                "ticker": "AAA",
                "entity_id": "AAA_entity",
            }
        ],
    )
    monkeypatch.setattr(central_control, "TRADE_LOG_PATH", tmp_path / "missing_trade_log.csv")
    blocked = ctl._same_day_reentry_blocked_tickers(trade_date)
    filtered = [c for c in [candidate("AAA", 9.0), candidate("BBB", 8.0)] if c.ticker not in blocked]
    decisions = decide_buys(filtered, _LiveLedgerView([]), AllocationParams(max_positions=1, total_capital=10_000))

    ctl._process_semi_auto_decisions(decisions, {"BBB_entity": SimpleNamespace(score=3.0, threshold=2.0)}, {"BBB_entity": 100.0})

    assert len(ctl.runner.orders) == 1
    assert ctl.runner.orders[0]["ticker"] == "BBB"
    state = load_candidate_state(ctl.config.candidate_state_path)
    assert state["candidates"][candidate_id_for(trade_date, "AAA_entity")]["status"] == "manual_executed"
    assert state["candidates"][candidate_id_for(trade_date, "BBB_entity")]["status"] == "auto_executed"
