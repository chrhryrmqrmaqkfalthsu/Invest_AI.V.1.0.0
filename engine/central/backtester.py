"""Daily central-policy backtester skeleton.

This module orchestrates entity-level signals, score-based allocation, simulated
broker fills, EntityPositionLedger accounting, and daily reconcile checks.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from engine.central.allocation_policy import AllocationParams, BuyCandidate, BuyDecision, MIN_ORDER_SHARES, decide_buys
from engine.central.entity_loader import EntityRecord
from engine.central.ledger import EntityPositionLedger
from engine.central.models import normalize_shares, normalize_ticker
from engine.central.signal_collector import CacheOnlyDataProvider, SignalCollector
from engine.central.sim_broker import FillPolicy, SimBroker
from engine.core.exit_policy import (
    ExitExecutionConfig,
    MarketContext,
    PositionState,
    PriceSnapshot,
    evaluate_exit,
    initialize_position_state,
)
from engine.core.metadata import compute_rulebook_hash
from engine.live.broker.base import OrderSide, OrderStatus
from engine.strategies.rulebook import Rulebook


@dataclass(frozen=True)
class EquityPoint:
    date: str
    cash: float
    holdings_value: float
    equity: float
    open_position_count: int


@dataclass(frozen=True)
class TradeRecord:
    date: str
    entity_id: str
    ticker: str
    side: str
    shares: float
    price: float
    notional: float
    reason: str
    position_id: str
    realized_pnl: float = 0.0


@dataclass(frozen=True)
class RejectedOrderRecord:
    date: str
    entity_id: str
    ticker: str
    side: str
    requested_shares: float
    reason: str
    order_id: str = ""
    client_order_id: str = ""


@dataclass
class BacktestResult:
    equity_curve: list[EquityPoint] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    rejected_orders: list[RejectedOrderRecord] = field(default_factory=list)
    per_entity_pnl: dict[str, float] = field(default_factory=dict)
    total_return: float = 0.0
    max_drawdown_pct: float = 0.0
    final_equity: float = 0.0
    reconcile_failures: list[dict] = field(default_factory=list)

    @property
    def rejected_order_count(self) -> int:
        return len(self.rejected_orders)

    def to_dict(self) -> dict:
        return {
            "equity_curve": [asdict(x) for x in self.equity_curve],
            "trades": [asdict(x) for x in self.trades],
            "rejected_orders": [asdict(x) for x in self.rejected_orders],
            "rejected_order_count": self.rejected_order_count,
            "per_entity_pnl": dict(self.per_entity_pnl),
            "total_return": self.total_return,
            "max_drawdown_pct": self.max_drawdown_pct,
            "final_equity": self.final_equity,
            "reconcile_failures": list(self.reconcile_failures),
        }


def common_validation_window(entities: Iterable[EntityRecord], *, preferred_label: str = "recent_1y") -> tuple[str, str]:
    starts: list[pd.Timestamp] = []
    ends: list[pd.Timestamp] = []
    for entity in entities:
        matched = None
        for period in entity.validation_periods or []:
            if str(period.get("label") or "") == preferred_label:
                matched = period
                break
        if matched is None and entity.validation_periods:
            matched = entity.validation_periods[-1]
        if not matched:
            continue
        starts.append(pd.Timestamp(matched.get("start")))
        ends.append(pd.Timestamp(matched.get("end")))
    if not starts or not ends:
        raise ValueError("no validation periods available")
    start = max(starts)
    end = min(ends)
    if start > end:
        raise ValueError(f"empty common validation window: {start.date()} > {end.date()}")
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def run_central_backtest(
    entities: Iterable[EntityRecord],
    start: str,
    end: str,
    alloc_params: AllocationParams,
    *,
    data_provider: Optional[CacheOnlyDataProvider] = None,
    ledger_dir: Optional[str | Path] = None,
    fill_policy: Optional[FillPolicy] = None,
    exit_via: str = "rulebook",
    use_llm_events: bool = False,
    persist_ledger: bool = False,
    flush_ledger_on_finish: Optional[bool] = None,
    candidate_log_path: Optional[str | Path] = None,
    candidate_log_append: bool = False,
    swap_enabled: bool = False,
    swap_score_gap_threshold: float = 0.0,
    swap_score_metric: str = "confidence",
    swap_guard_queue_enabled: bool = False,
    exit_imminence_threshold: float = 0.75,
    turnover_guard: float = 0.0,
    queue_signal_ttl: int = 5,
) -> BacktestResult:
    entity_list = list(entities)
    if not entity_list:
        raise ValueError("entities required")
    provider = data_provider or CacheOnlyDataProvider()
    collector = SignalCollector(provider, use_llm_events=use_llm_events)
    price_data = {ticker: provider.load_price_df(ticker) for ticker in sorted({e.ticker for e in entity_list})}
    broker = SimBroker(price_data, initial_cash=alloc_params.total_capital, fill_policy=fill_policy or FillPolicy())
    ledger_dir_was_provided = ledger_dir is not None
    if ledger_dir is None:
        ledger_dir = tempfile.mkdtemp(prefix="central_bt_ledger_")
    ledger = EntityPositionLedger(base_dir=Path(ledger_dir), persist=bool(persist_ledger))
    entity_by_id = {e.entity_id: e for e in entity_list}
    rb_by_entity = {e.entity_id: Rulebook.from_dict(e.rulebook) for e in entity_list}
    candidate_log_rows: list[dict] = []
    guard_queue: dict[str, dict] = {}
    guard_stats = _new_swap_guard_queue_stats() if bool(swap_guard_queue_enabled) else None

    result = BacktestResult()
    days = _trading_days(price_data.values(), start, end)
    for day in days:
        broker.set_date(day)
        if exit_via != "rulebook":
            raise ValueError(f"unsupported exit_via: {exit_via}")
        _process_exits(day, ledger, broker, provider, entity_by_id, rb_by_entity, result)
        signals = collector.collect(entity_list, day)
        candidates = [
            BuyCandidate(
                entity_id=s.entity_id,
                ticker=s.ticker,
                confidence=s.confidence,
                strength=s.strength,
                price=s.price,
                signal_score=s.score,
                threshold=s.threshold,
                rulebook=entity_by_id[s.entity_id].rulebook,
            )
            for s in signals
        ]
        if guard_stats is not None:
            _refresh_swap_guard_queue(day, guard_queue, candidates, int(queue_signal_ttl or 0), guard_stats)
        decisions = decide_buys(candidates, ledger, alloc_params)
        if candidate_log_path is not None:
            _append_candidate_log_rows(candidate_log_rows, day, candidates, decisions, ledger, provider, alloc_params)
        swapped_position_ids = _execute_score_swaps(
            day,
            candidates,
            decisions,
            ledger,
            broker,
            provider,
            entity_by_id,
            rb_by_entity,
            alloc_params,
            result,
            swap_enabled=bool(swap_enabled),
            swap_score_gap_threshold=float(swap_score_gap_threshold or 0.0),
            swap_score_metric=str(swap_score_metric or "confidence"),
        )
        if guard_stats is not None:
            guard_swapped = _execute_swap_guard_queue(
                day,
                candidates,
                decisions,
                guard_queue,
                guard_stats,
                ledger,
                broker,
                provider,
                entity_by_id,
                rb_by_entity,
                alloc_params,
                result,
                exit_imminence_threshold=float(exit_imminence_threshold if exit_imminence_threshold is not None else 0.75),
                turnover_guard=float(turnover_guard or 0.0),
                queue_signal_ttl=int(queue_signal_ttl or 0),
            )
            swapped_position_ids.update(guard_swapped)
        for decision in decisions:
            if decision.target_position_id and decision.target_position_id in swapped_position_ids:
                continue
            if guard_stats is not None and not _decision_still_allowed(decision, ledger, alloc_params):
                guard_stats["normal_decisions_skipped_after_queue"] += 1
                continue
            _execute_buy(day, decision, ledger, broker, provider, entity_by_id[decision.entity_id], rb_by_entity[decision.entity_id], alloc_params, result)
        rec = ledger.reconcile(broker)
        if not rec.get("ok"):
            result.reconcile_failures.append({"date": pd.Timestamp(day).strftime("%Y-%m-%d"), **rec})
        result.equity_curve.append(_equity_point(day, broker, ledger))
    _finalize_result(result, alloc_params.total_capital, ledger, broker)
    if guard_stats is not None:
        guard_stats["queue_active_end"] = len(guard_queue)
        guard_stats["queue_conversion_rate"] = _safe_ratio(guard_stats["queue_converted"], guard_stats["queue_registered"])
        result.swap_guard_queue_stats = guard_stats
    if candidate_log_path is not None:
        _flush_candidate_log(candidate_log_path, candidate_log_rows, append=bool(candidate_log_append))
    should_flush = (ledger_dir_was_provided and not bool(persist_ledger)) if flush_ledger_on_finish is None else bool(flush_ledger_on_finish)
    if should_flush:
        ledger.flush()
    return result


def _process_exits(day, ledger, broker: SimBroker, provider: CacheOnlyDataProvider, entity_by_id: dict, rb_by_entity: dict, result: BacktestResult) -> None:
    for pos in list(ledger.open_positions()):
        entity = entity_by_id.get(pos.entity_id)
        rb = rb_by_entity.get(pos.entity_id)
        if entity is None or rb is None:
            continue
        df = provider.load_price_df(pos.ticker)
        idx = _index_for_date(df, day)
        if idx is None:
            continue
        state = _position_state_from_record(pos)
        row = df.iloc[idx]
        next_open = None
        if idx + 1 < len(df):
            next_open = _float(df.iloc[idx + 1].get("Open", df.iloc[idx + 1].get("Close", 0.0)))
        snap = PriceSnapshot(
            date=pd.Timestamp(df.index[idx]).strftime("%Y-%m-%d"),
            open=_float(row.get("Open", row.get("Close", 0.0))),
            high=_float(row.get("High", row.get("Close", 0.0))),
            low=_float(row.get("Low", row.get("Close", 0.0))),
            close=_float(row.get("Close", 0.0)),
            next_open=next_open,
        )
        sell_omen_score = _optional_float(row.get("sell_omen_score"))
        decision = evaluate_exit(
            state,
            snap,
            rb,
            MarketContext(holding_trading_days=_holding_days(df, pos.entry_date, idx), current_trade_date=snap.date),
            ExitExecutionConfig(
                mode="base",
                use_next_open=True,
                fallback_to_trigger_price=True,
                sell_omen_enabled=bool(getattr(rb, "sell_omen_enabled", False)),
                sell_omen_score=sell_omen_score,
                sell_omen_threshold=_optional_float(getattr(rb, "sell_omen_threshold", None)),
            ),
        )
        if decision.updated_position is not None:
            _copy_state_to_record(pos, decision.updated_position)
        if not decision.should_exit:
            continue
        intent = ledger.open_intent(
            pos.entity_id,
            pos.ticker,
            OrderSide.SELL.value,
            "exit",
            pos.open_shares,
            str(decision.reason or "rulebook_exit"),
            target_position_id=pos.position_id,
        )
        client_order_id = _client_order_id("sell", pos.entity_id, pos.ticker, snap.date)
        execution = ledger.dispatch_execution(intent.intent_id, broker, client_order_id)
        order = broker.get_order(execution.order_id)
        if order is None:
            _record_reject(result, snap.date, pos.entity_id, pos.ticker, "sell", pos.open_shares, order)
            continue
        if order.status != OrderStatus.FILLED:
            _record_reject(result, snap.date, pos.entity_id, pos.ticker, "sell", pos.open_shares, order)
            continue
        ledger.apply_fill(execution.execution_id, order)
        result.trades.append(
            TradeRecord(
                date=str(order.filled_at or snap.date),
                entity_id=pos.entity_id,
                ticker=pos.ticker,
                side="sell",
                shares=float(order.filled_shares or 0.0),
                price=float(order.filled_avg_price or 0.0),
                notional=float(order.filled_shares or 0.0) * float(order.filled_avg_price or 0.0),
                reason=str(decision.reason or "rulebook_exit"),
                position_id=pos.position_id,
                realized_pnl=float(getattr(pos, "realized_pnl", 0.0) or 0.0),
            )
        )


def _execute_buy(day, decision, ledger, broker: SimBroker, provider: CacheOnlyDataProvider, entity: EntityRecord, rb: Rulebook, alloc_params: AllocationParams, result: BacktestResult, *, reason: str = "central_policy_buy") -> None:
    d_close = _close_price_on_day(provider, decision.ticker, day)
    shares = _cash_capped_shares(broker.cash, decision.shares, d_close, alloc_params.cash_buffer_ratio)
    if shares <= MIN_ORDER_SHARES:
        result.rejected_orders.append(
            RejectedOrderRecord(
                date=pd.Timestamp(day).strftime("%Y-%m-%d"),
                entity_id=decision.entity_id,
                ticker=decision.ticker,
                side="buy",
                requested_shares=float(decision.shares or 0.0),
                reason="cash_buffer_cap_zero",
            )
        )
        return
    intent = ledger.open_intent(
        decision.entity_id,
        decision.ticker,
        OrderSide.BUY.value,
        decision.purpose,
        shares,
        str(reason or "central_policy_buy"),
        target_position_id=decision.target_position_id,
    )
    client_order_id = _client_order_id("buy", decision.entity_id, decision.ticker, pd.Timestamp(day).strftime("%Y-%m-%d"))
    execution = ledger.dispatch_execution(intent.intent_id, broker, client_order_id)
    order = broker.get_order(execution.order_id)
    if order is None:
        return
    if order.status != OrderStatus.FILLED:
        _record_reject(result, pd.Timestamp(day).strftime("%Y-%m-%d"), decision.entity_id, decision.ticker, "buy", shares, order)
        return
    ledger.apply_fill(execution.execution_id, order)
    pos = ledger.get_position(execution.position_id)
    if pos is not None and decision.purpose == "entry":
        _initialize_record_from_entry(pos, entity, rb, provider, order, day)
    result.trades.append(
        TradeRecord(
            date=str(order.filled_at or pd.Timestamp(day).strftime("%Y-%m-%d")),
            entity_id=decision.entity_id,
            ticker=decision.ticker,
            side="buy",
            shares=float(order.filled_shares or 0.0),
            price=float(order.filled_avg_price or 0.0),
            notional=float(order.filled_shares or 0.0) * float(order.filled_avg_price or 0.0),
            reason=str(reason or "central_policy_buy"),
            position_id=execution.position_id,
        )
    )


def _cash_capped_shares(cash: float, requested_shares: float, d_close_price: float, cash_buffer_ratio: float = 0.98) -> float:
    requested = normalize_shares(requested_shares)
    price = float(d_close_price or 0.0)
    if requested <= 0.0 or price <= 0.0:
        return 0.0
    ratio = max(0.0, min(float(cash_buffer_ratio or 0.0), 1.0))
    max_affordable = (float(cash or 0.0) * ratio) / price
    capped = normalize_shares(min(requested, max_affordable))
    return capped if capped > MIN_ORDER_SHARES else 0.0


def _execute_score_swaps(
    day,
    candidates: list[BuyCandidate],
    decisions: list[BuyDecision],
    ledger: EntityPositionLedger,
    broker: SimBroker,
    provider: CacheOnlyDataProvider,
    entity_by_id: dict[str, EntityRecord],
    rb_by_entity: dict[str, Rulebook],
    alloc_params: AllocationParams,
    result: BacktestResult,
    *,
    swap_enabled: bool,
    swap_score_gap_threshold: float,
    swap_score_metric: str,
) -> set[str]:
    if not swap_enabled:
        return set()
    metric = str(swap_score_metric or "confidence").strip().lower()
    if metric not in {"confidence", "allocation_score"}:
        raise ValueError(f"unsupported swap_score_metric: {swap_score_metric}")
    max_positions = max(int(alloc_params.max_positions or 0), 0)
    if max_positions <= 0:
        return set()
    open_positions = [p for p in list(ledger.open_positions()) if normalize_shares(getattr(p, "open_shares", 0.0)) > 0.0]
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in open_positions if normalize_ticker(getattr(p, "ticker", ""))}
    if len(open_tickers) < max_positions:
        return set()

    candidate_by_entity = {str(c.entity_id or ""): c for c in candidates}
    selected_entities = {str(d.entity_id or "") for d in decisions or []}
    selected_tickers = {normalize_ticker(getattr(d, "ticker", "")) for d in decisions or [] if normalize_ticker(getattr(d, "ticker", ""))}

    a_pool = []
    for pos in open_positions:
        ticker = normalize_ticker(getattr(pos, "ticker", ""))
        current_price = _close_price_on_day(provider, ticker, day)
        entry_price = float(getattr(pos, "avg_entry_price", 0.0) or 0.0)
        if current_price <= 0.0 or entry_price <= 0.0:
            continue
        unrealized_pct = (current_price - entry_price) / entry_price * 100.0
        if unrealized_pct <= 0.0:
            continue
        score = _score_for_position(pos, metric, candidate_by_entity, entity_by_id, alloc_params)
        if score is None:
            continue
        a_pool.append((float(score), str(getattr(pos, "position_id", "") or ""), pos, float(unrealized_pct)))
    if not a_pool:
        return set()

    b_pool = []
    used_b_tickers: set[str] = set()
    open_entities = {str(getattr(p, "entity_id", "") or "") for p in open_positions}
    for cand in sorted(candidates, key=lambda c: (_score_for_candidate(c, metric, alloc_params) or -1e100, str(c.entity_id or "")), reverse=True):
        ticker = normalize_ticker(cand.ticker)
        entity_id = str(cand.entity_id or "")
        if not ticker or ticker in open_tickers or ticker in selected_tickers or ticker in used_b_tickers:
            continue
        if entity_id in open_entities or entity_id in selected_entities:
            continue
        price = float(cand.price or 0.0)
        if price <= 0.0:
            continue
        confidence = float(cand.confidence or 0.0)
        if confidence < float(alloc_params.min_confidence or 0.0):
            continue
        allocation_score = _allocation_score_for_candidate(cand, alloc_params)
        if allocation_score <= 0.0:
            continue
        score = _score_for_candidate(cand, metric, alloc_params)
        if score is None:
            continue
        b_pool.append((float(score), entity_id, ticker, cand))
        used_b_tickers.add(ticker)
    if not b_pool:
        return set()

    a_pool.sort(key=lambda row: (row[0], row[1]))
    b_pool.sort(key=lambda row: (row[0], row[1]), reverse=True)
    swapped_position_ids: set[str] = set()
    threshold = float(swap_score_gap_threshold or 0.0)
    for a_row, b_row in zip(a_pool, b_pool):
        a_score, _, pos, _ = a_row
        b_score, _, _, cand = b_row
        if b_score - a_score < threshold:
            break
        ok, proceeds = _execute_swap_sell(day, pos, ledger, broker, result)
        if not ok or proceeds <= 0.0:
            continue
        swapped_position_ids.add(str(pos.position_id or ""))
        _execute_swap_buy(day, cand, proceeds, ledger, broker, provider, entity_by_id, rb_by_entity, alloc_params, result, b_score)
    return swapped_position_ids


def _new_swap_guard_queue_stats() -> dict:
    return {
        "schema_version": 1,
        "queue_registered": 0,
        "queue_refreshed": 0,
        "queue_expired": 0,
        "queue_signal_lost": 0,
        "queue_converted": 0,
        "queue_active_end": 0,
        "queue_conversion_rate": 0.0,
        "swap_attempt_days": 0,
        "swap_executed": 0,
        "swap_blocked_no_safe_a": 0,
        "swap_blocked_turnover": 0,
        "swap_blocked_churn": 0,
        "swap_blocked_score_gap": 0,
        "swap_blocked_no_b": 0,
        "normal_decisions_skipped_after_queue": 0,
        "queued_entry_trades": 0,
        "proxy_uses_future_data": False,
        "guard_samples": [],
        "swap_events": [],
        "queue_events": [],
    }


def _refresh_swap_guard_queue(day, queue: dict[str, dict], candidates: list[BuyCandidate], ttl: int, stats: dict) -> None:
    date_str = pd.Timestamp(day).strftime("%Y-%m-%d")
    current_entities = {str(c.entity_id or "") for c in candidates}
    ttl_days = max(int(ttl or 0), 1)
    for entity_id, row in list(queue.items()):
        queued_at = str(row.get("queued_at") or date_str)
        age = _calendar_days_between(queued_at, date_str)
        if age > ttl_days:
            stats["queue_expired"] += 1
            stats["queue_events"].append({"date": date_str, "event": "expired", "entity_id": entity_id, "ticker": row.get("ticker", ""), "age_days": age})
            queue.pop(entity_id, None)
            continue
        if entity_id not in current_entities:
            stats["queue_signal_lost"] += 1
            stats["queue_events"].append({"date": date_str, "event": "signal_lost", "entity_id": entity_id, "ticker": row.get("ticker", ""), "age_days": age})
            queue.pop(entity_id, None)
            continue


def _execute_swap_guard_queue(
    day,
    candidates: list[BuyCandidate],
    decisions: list[BuyDecision],
    queue: dict[str, dict],
    stats: dict,
    ledger: EntityPositionLedger,
    broker: SimBroker,
    provider: CacheOnlyDataProvider,
    entity_by_id: dict[str, EntityRecord],
    rb_by_entity: dict[str, Rulebook],
    alloc_params: AllocationParams,
    result: BacktestResult,
    *,
    exit_imminence_threshold: float,
    turnover_guard: float,
    queue_signal_ttl: int,
) -> set[str]:
    swapped_position_ids: set[str] = set()
    _execute_queued_entries(day, candidates, queue, stats, ledger, broker, provider, entity_by_id, rb_by_entity, alloc_params, result)

    max_positions = max(int(alloc_params.max_positions or 0), 0)
    if max_positions <= 0:
        return swapped_position_ids
    open_positions = [p for p in list(ledger.open_positions()) if normalize_shares(getattr(p, "open_shares", 0.0)) > 0.0]
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in open_positions if normalize_ticker(getattr(p, "ticker", ""))}
    if len(open_tickers) < max_positions:
        return swapped_position_ids

    selected_entities = {str(d.entity_id or "") for d in decisions or []}
    selected_tickers = {normalize_ticker(getattr(d, "ticker", "")) for d in decisions or [] if normalize_ticker(getattr(d, "ticker", ""))}
    open_entities = {str(getattr(p, "entity_id", "") or "") for p in open_positions}
    b_pool = []
    used_b_tickers: set[str] = set()
    for cand in sorted(candidates, key=lambda c: (_allocation_score_for_candidate(c, alloc_params), str(c.entity_id or "")), reverse=True):
        ticker = normalize_ticker(cand.ticker)
        entity_id = str(cand.entity_id or "")
        if not ticker or ticker in open_tickers or ticker in selected_tickers or ticker in used_b_tickers:
            continue
        if entity_id in open_entities or entity_id in selected_entities:
            continue
        if float(cand.price or 0.0) <= 0.0:
            continue
        if float(cand.confidence or 0.0) < float(alloc_params.min_confidence or 0.0):
            continue
        score = _allocation_score_for_candidate(cand, alloc_params)
        if score <= 0.0:
            continue
        b_pool.append((score, entity_id, ticker, cand))
        used_b_tickers.add(ticker)
    if not b_pool:
        stats["swap_blocked_no_b"] += 1
        return swapped_position_ids

    stats["swap_attempt_days"] += 1
    b_score, b_entity_id, b_ticker, b_cand = b_pool[0]
    date_str = pd.Timestamp(day).strftime("%Y-%m-%d")
    turnover_ratio = _recent_turnover_ratio(day, result, broker, lookback_days=20)
    if float(turnover_guard or 0.0) > 0.0 and turnover_ratio > float(turnover_guard or 0.0):
        stats["swap_blocked_turnover"] += 1
        _queue_candidate(day, b_cand, queue, stats, reason="turnover_guard", ttl=queue_signal_ttl)
        return swapped_position_ids
    if _recent_trade_match(b_cand, result.trades, day, lookback_days=20):
        stats["swap_blocked_churn"] += 1
        _queue_candidate(day, b_cand, queue, stats, reason="churn_guard", ttl=queue_signal_ttl)
        return swapped_position_ids

    candidate_by_entity = {str(c.entity_id or ""): c for c in candidates}
    a_pool = []
    for pos in open_positions:
        ticker = normalize_ticker(getattr(pos, "ticker", ""))
        current_price = _close_price_on_day(provider, ticker, day)
        entry_price = float(getattr(pos, "avg_entry_price", 0.0) or 0.0)
        if current_price <= 0.0 or entry_price <= 0.0:
            continue
        unrealized_pct = (current_price - entry_price) / entry_price * 100.0
        if unrealized_pct <= 0.0:
            continue
        score = _score_for_position(pos, "allocation_score", candidate_by_entity, entity_by_id, alloc_params)
        if score is None:
            continue
        imminence = _exit_imminence_proxy(day, pos, provider)
        sample = {
            "date": date_str,
            "position_id": str(getattr(pos, "position_id", "") or ""),
            "entity_id": str(getattr(pos, "entity_id", "") or ""),
            "ticker": ticker,
            "entry_date": str(getattr(pos, "entry_date", "") or ""),
            "unrealized_pnl_pct": float(unrealized_pct),
            "allocation_score": float(score),
            **imminence,
        }
        stats["guard_samples"].append(sample)
        if float(imminence["exit_imminence_score"]) >= float(exit_imminence_threshold):
            continue
        a_pool.append((float(score), str(getattr(pos, "position_id", "") or ""), pos, float(unrealized_pct), imminence))
    if not a_pool:
        stats["swap_blocked_no_safe_a"] += 1
        _queue_candidate(day, b_cand, queue, stats, reason="no_safe_a", ttl=queue_signal_ttl)
        return swapped_position_ids

    a_pool.sort(key=lambda row: (row[0], row[1]))
    a_score, _, pos, unrealized_pct, imminence = a_pool[0]
    if b_score <= a_score:
        stats["swap_blocked_score_gap"] += 1
        _queue_candidate(day, b_cand, queue, stats, reason="non_positive_score_gap", ttl=queue_signal_ttl)
        return swapped_position_ids
    ok, proceeds = _execute_swap_sell(day, pos, ledger, broker, result, reason="swap_guard_queue_exit")
    if not ok or proceeds <= 0.0:
        _queue_candidate(day, b_cand, queue, stats, reason="swap_sell_failed", ttl=queue_signal_ttl)
        return swapped_position_ids
    swapped_position_ids.add(str(pos.position_id or ""))
    _execute_swap_buy(day, b_cand, proceeds, ledger, broker, provider, entity_by_id, rb_by_entity, alloc_params, result, b_score, reason="swap_guard_queue_entry")
    queue.pop(b_entity_id, None)
    stats["swap_executed"] += 1
    stats["swap_events"].append(
        {
            "date": date_str,
            "a_position_id": str(getattr(pos, "position_id", "") or ""),
            "a_entity_id": str(getattr(pos, "entity_id", "") or ""),
            "a_ticker": normalize_ticker(getattr(pos, "ticker", "")),
            "a_entry_date": str(getattr(pos, "entry_date", "") or ""),
            "a_score": float(a_score),
            "a_unrealized_pnl_pct": float(unrealized_pct),
            "b_entity_id": b_entity_id,
            "b_ticker": b_ticker,
            "b_score": float(b_score),
            "score_gap": float(b_score - a_score),
            "turnover_ratio_20d": float(turnover_ratio),
            **imminence,
        }
    )
    return swapped_position_ids


def _execute_queued_entries(
    day,
    candidates: list[BuyCandidate],
    queue: dict[str, dict],
    stats: dict,
    ledger: EntityPositionLedger,
    broker: SimBroker,
    provider: CacheOnlyDataProvider,
    entity_by_id: dict[str, EntityRecord],
    rb_by_entity: dict[str, Rulebook],
    alloc_params: AllocationParams,
    result: BacktestResult,
) -> None:
    if not queue:
        return
    max_positions = max(int(alloc_params.max_positions or 0), 0)
    if max_positions <= 0:
        return
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in ledger.open_positions() if normalize_ticker(getattr(p, "ticker", ""))}
    if len(open_tickers) >= max_positions:
        return
    cand_by_entity = {str(c.entity_id or ""): c for c in candidates}
    queued_candidates = []
    for entity_id, row in sorted(queue.items(), key=lambda item: (str(item[1].get("queued_at") or ""), str(item[0]))):
        cand = cand_by_entity.get(entity_id)
        if cand is None:
            continue
        queued_candidates.append(cand)
    if not queued_candidates:
        return
    queue_decisions = decide_buys(queued_candidates, ledger, alloc_params)
    for decision in queue_decisions:
        if not _decision_still_allowed(decision, ledger, alloc_params):
            continue
        entity = entity_by_id.get(decision.entity_id)
        rb = rb_by_entity.get(decision.entity_id)
        if entity is None or rb is None:
            continue
        _execute_buy(day, decision, ledger, broker, provider, entity, rb, alloc_params, result, reason="swap_guard_queue_entry")
        stats["queue_converted"] += 1
        stats["queued_entry_trades"] += 1
        row = queue.pop(str(decision.entity_id or ""), {})
        stats["queue_events"].append(
            {
                "date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                "event": "converted",
                "entity_id": str(decision.entity_id or ""),
                "ticker": normalize_ticker(decision.ticker),
                "queued_at": row.get("queued_at", ""),
            }
        )


def _queue_candidate(day, cand: BuyCandidate, queue: dict[str, dict], stats: dict, *, reason: str, ttl: int) -> None:
    entity_id = str(cand.entity_id or "")
    ticker = normalize_ticker(cand.ticker)
    if not entity_id or not ticker:
        return
    date_str = pd.Timestamp(day).strftime("%Y-%m-%d")
    row = queue.get(entity_id)
    if row is None:
        queue[entity_id] = {
            "entity_id": entity_id,
            "ticker": ticker,
            "queued_at": date_str,
            "last_seen": date_str,
            "reason": str(reason or ""),
            "ttl": int(ttl or 0),
            "score": float(_allocation_score_for_candidate(cand, AllocationParams()) or 0.0),
        }
        stats["queue_registered"] += 1
        stats["queue_events"].append({"date": date_str, "event": "registered", "entity_id": entity_id, "ticker": ticker, "reason": str(reason or "")})
        return
    row["last_seen"] = date_str
    row["reason"] = str(reason or row.get("reason", ""))
    stats["queue_refreshed"] += 1


def _decision_still_allowed(decision: BuyDecision, ledger: EntityPositionLedger, alloc_params: AllocationParams) -> bool:
    active_open_positions = [p for p in list(ledger.open_positions()) if normalize_shares(getattr(p, "open_shares", 0.0)) > 0.0]
    open_by_entity = {str(getattr(p, "entity_id", "") or ""): p for p in active_open_positions}
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in active_open_positions if normalize_ticker(getattr(p, "ticker", ""))}
    if decision.purpose == "add_buy":
        return str(decision.target_position_id or "") in {str(getattr(p, "position_id", "") or "") for p in active_open_positions}
    if str(decision.entity_id or "") in open_by_entity:
        return False
    ticker = normalize_ticker(decision.ticker)
    if not ticker or ticker in open_tickers:
        return False
    max_positions = max(int(alloc_params.max_positions or 0), 0)
    return len(open_tickers) < max_positions


def _exit_imminence_proxy(day, pos, provider: CacheOnlyDataProvider) -> dict:
    """Current-bar-only exit-imminence proxy.

    This intentionally uses only position state saved before/at ``day`` and the
    OHLCV row at ``day``. It never scans future rows or actual future exits.
    """
    ticker = normalize_ticker(getattr(pos, "ticker", ""))
    df = provider.load_price_df(ticker)
    idx = _index_for_date(df, day)
    if idx is None:
        return {
            "exit_imminence_score": 1.0,
            "time_imminence": 1.0,
            "take_profit_imminence": 1.0,
            "stop_imminence": 1.0,
            "trailing_imminence": 1.0,
            "holding_days": 0,
            "max_holding_days": int(getattr(pos, "max_holding_days", 0) or 0),
            "uses_future_data": False,
        }
    row = df.iloc[idx]
    close = _float(row.get("Close", row.get("close", 0.0)))
    atr = _float(row.get("ATR", 0.0))
    if atr <= 0.0:
        atr = max(close * 0.02, 1e-9)
    holding_days = _holding_days(df, str(getattr(pos, "entry_date", "") or ""), idx)
    max_holding_days = max(int(getattr(pos, "max_holding_days", 0) or 0), 0)
    if max_holding_days > 0:
        remaining = max(max_holding_days - holding_days, 0)
        time_imminence = 1.0 - min(remaining / max(float(max_holding_days), 1.0), 1.0)
    else:
        remaining = 9999
        time_imminence = 0.0
    target = float(getattr(pos, "target_price", 0.0) or 0.0)
    stop = float(getattr(pos, "stop_price", 0.0) or 0.0)
    trailing = float(getattr(pos, "trailing_stop", 0.0) or 0.0)
    take_profit_imminence = _upper_boundary_imminence(close, target, atr)
    stop_imminence = _lower_boundary_imminence(close, stop, atr)
    trailing_imminence = _lower_boundary_imminence(close, trailing, atr)
    score = max(time_imminence, take_profit_imminence, stop_imminence, trailing_imminence)
    return {
        "exit_imminence_score": float(max(0.0, min(score, 1.0))),
        "time_imminence": float(max(0.0, min(time_imminence, 1.0))),
        "take_profit_imminence": float(max(0.0, min(take_profit_imminence, 1.0))),
        "stop_imminence": float(max(0.0, min(stop_imminence, 1.0))),
        "trailing_imminence": float(max(0.0, min(trailing_imminence, 1.0))),
        "holding_days": int(holding_days),
        "max_holding_days": int(max_holding_days),
        "time_stop_remaining_days": int(remaining),
        "current_close": float(close),
        "current_atr": float(atr),
        "target_price": float(target),
        "stop_price": float(stop),
        "trailing_stop": float(trailing),
        "uses_future_data": False,
    }


def _upper_boundary_imminence(close: float, boundary: float, atr: float) -> float:
    if close <= 0.0 or boundary <= 0.0 or atr <= 0.0:
        return 0.0
    gap_atr = (boundary - close) / atr
    if gap_atr <= 0.0:
        return 1.0
    return max(0.0, 1.0 - min(gap_atr / 3.0, 1.0))


def _lower_boundary_imminence(close: float, boundary: float, atr: float) -> float:
    if close <= 0.0 or boundary <= 0.0 or atr <= 0.0:
        return 0.0
    gap_atr = (close - boundary) / atr
    if gap_atr <= 0.0:
        return 1.0
    return max(0.0, 1.0 - min(gap_atr / 3.0, 1.0))


def _recent_turnover_ratio(day, result: BacktestResult, broker: SimBroker, *, lookback_days: int) -> float:
    date = pd.Timestamp(day).normalize()
    start = date - pd.Timedelta(days=max(int(lookback_days or 0), 1) * 2)
    notional = 0.0
    for trade in result.trades:
        tdate = pd.Timestamp(trade.date).normalize()
        if start <= tdate < date:
            notional += abs(float(trade.notional or 0.0))
    equity = float(broker.cash or 0.0) + sum(float(h.market_value or 0.0) for h in broker.get_holdings())
    return notional / equity if equity > 0.0 else 0.0


def _recent_trade_match(cand: BuyCandidate, trades: list[TradeRecord], day, *, lookback_days: int) -> bool:
    date = pd.Timestamp(day).normalize()
    start = date - pd.Timedelta(days=max(int(lookback_days or 0), 1))
    ticker = normalize_ticker(cand.ticker)
    entity_id = str(cand.entity_id or "")
    for trade in trades:
        tdate = pd.Timestamp(trade.date).normalize()
        if not (start <= tdate < date):
            continue
        if normalize_ticker(trade.ticker) == ticker or str(trade.entity_id or "") == entity_id:
            return True
    return False


def _calendar_days_between(start: str, end: str) -> int:
    try:
        return max(0, int((pd.Timestamp(end).normalize() - pd.Timestamp(start).normalize()).days))
    except Exception:
        return 0


def _safe_ratio(numerator: float, denominator: float) -> float:
    try:
        den = float(denominator or 0.0)
        return float(numerator or 0.0) / den if den else 0.0
    except Exception:
        return 0.0


def _score_for_candidate(cand: BuyCandidate, metric: str, alloc_params: AllocationParams) -> Optional[float]:
    if metric == "confidence":
        return float(cand.confidence or 0.0)
    if metric == "allocation_score":
        return _allocation_score_for_candidate(cand, alloc_params)
    return None


def _score_for_position(pos, metric: str, candidate_by_entity: dict[str, BuyCandidate], entity_by_id: dict[str, EntityRecord], alloc_params: AllocationParams) -> Optional[float]:
    entity_id = str(getattr(pos, "entity_id", "") or "")
    cand = candidate_by_entity.get(entity_id)
    if cand is not None:
        return _score_for_candidate(cand, metric, alloc_params)
    entity = entity_by_id.get(entity_id)
    if entity is None:
        return None
    confidence = float(getattr(entity, "confidence", 0.0) or 0.0)
    if metric == "confidence":
        return confidence
    if metric == "allocation_score":
        return float(alloc_params.confidence_weight) * confidence
    return None


def _execute_swap_sell(day, pos, ledger: EntityPositionLedger, broker: SimBroker, result: BacktestResult, *, reason: str = "swap_exit") -> tuple[bool, float]:
    date_str = pd.Timestamp(day).strftime("%Y-%m-%d")
    shares = normalize_shares(getattr(pos, "open_shares", 0.0))
    if shares <= MIN_ORDER_SHARES:
        return False, 0.0
    intent = ledger.open_intent(
        pos.entity_id,
        pos.ticker,
        OrderSide.SELL.value,
        "exit",
        shares,
        str(reason or "swap_exit"),
        target_position_id=pos.position_id,
    )
    client_order_id = _client_order_id("swap-sell", pos.entity_id, pos.ticker, date_str)
    execution = ledger.dispatch_execution(intent.intent_id, broker, client_order_id)
    order = broker.get_order(execution.order_id)
    if order is None:
        _record_reject(result, date_str, pos.entity_id, pos.ticker, "sell", shares, order)
        return False, 0.0
    if order.status != OrderStatus.FILLED:
        _record_reject(result, date_str, pos.entity_id, pos.ticker, "sell", shares, order)
        return False, 0.0
    ledger.apply_fill(execution.execution_id, order)
    notional = float(order.filled_shares or 0.0) * float(order.filled_avg_price or 0.0)
    result.trades.append(
        TradeRecord(
            date=str(order.filled_at or date_str),
            entity_id=pos.entity_id,
            ticker=pos.ticker,
            side="sell",
            shares=float(order.filled_shares or 0.0),
            price=float(order.filled_avg_price or 0.0),
            notional=notional,
            reason=str(reason or "swap_exit"),
            position_id=pos.position_id,
            realized_pnl=float(getattr(pos, "realized_pnl", 0.0) or 0.0),
        )
    )
    return True, notional


def _execute_swap_buy(day, cand: BuyCandidate, proceeds: float, ledger: EntityPositionLedger, broker: SimBroker, provider: CacheOnlyDataProvider, entity_by_id: dict[str, EntityRecord], rb_by_entity: dict[str, Rulebook], alloc_params: AllocationParams, result: BacktestResult, score: float, *, reason: str = "swap_entry") -> None:
    entity = entity_by_id.get(cand.entity_id)
    rb = rb_by_entity.get(cand.entity_id)
    if entity is None or rb is None:
        return
    date_close = _close_price_on_day(provider, cand.ticker, day)
    if date_close <= 0.0:
        date_close = float(cand.price or 0.0)
    if date_close <= 0.0:
        return
    spend_cap = min(float(proceeds or 0.0), float(broker.cash or 0.0))
    shares = _cash_capped_shares(spend_cap, spend_cap / date_close, date_close, alloc_params.cash_buffer_ratio)
    if shares <= MIN_ORDER_SHARES:
        return
    decision = BuyDecision(
        entity_id=str(cand.entity_id or ""),
        ticker=normalize_ticker(cand.ticker),
        shares=shares,
        notional=shares * date_close,
        score=float(score or 0.0),
        confidence=float(cand.confidence or 0.0),
        strength=float(cand.strength or 0.0),
        purpose="entry",
        target_position_id="",
    )
    _execute_buy(day, decision, ledger, broker, provider, entity, rb, alloc_params, result, reason=str(reason or "swap_entry"))


def _append_candidate_log_rows(rows: list[dict], day, candidates: list[BuyCandidate], decisions, ledger: EntityPositionLedger, provider: CacheOnlyDataProvider, alloc_params: AllocationParams) -> None:
    date_str = pd.Timestamp(day).strftime("%Y-%m-%d")
    open_snapshot = _open_positions_snapshot(day, ledger, provider)
    selected_entity_ids = {str(getattr(decision, "entity_id", "") or "") for decision in decisions or []}
    for cand in candidates:
        selected = str(cand.entity_id or "") in selected_entity_ids
        allocation_score = _allocation_score_for_candidate(cand, alloc_params)
        rows.append(
            {
                "date": date_str,
                "entity_id": str(cand.entity_id or ""),
                "ticker": normalize_ticker(cand.ticker),
                "confidence": float(cand.confidence or 0.0),
                "signal_strength": float(cand.strength or 0.0),
                "allocation_score": float(allocation_score),
                "signal_score": float(cand.signal_score or 0.0),
                "selected": bool(selected),
                "rejection_reason": _candidate_rejection_reason(cand, selected, ledger, alloc_params, allocation_score),
                "open_positions_snapshot": open_snapshot,
                "price_at_decision": float(cand.price or 0.0),
            }
        )


def _allocation_score_for_candidate(cand: BuyCandidate, alloc_params: AllocationParams) -> float:
    return float(alloc_params.confidence_weight) * float(cand.confidence or 0.0) + float(alloc_params.signal_strength_weight) * float(cand.strength or 0.0)


def _candidate_rejection_reason(cand: BuyCandidate, selected: bool, ledger: EntityPositionLedger, alloc_params: AllocationParams, allocation_score: float) -> str:
    if selected:
        return ""
    ticker = normalize_ticker(cand.ticker)
    price = float(cand.price or 0.0)
    if not ticker or price <= 0.0:
        return "invalid_ticker_or_price"
    confidence = float(cand.confidence or 0.0)
    if confidence < float(alloc_params.min_confidence or 0.0):
        return "below_min_confidence"
    active_open_positions = [p for p in list(ledger.open_positions()) if normalize_shares(getattr(p, "open_shares", 0.0)) > 0.0]
    open_by_entity = {str(getattr(p, "entity_id", "") or ""): p for p in active_open_positions}
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in active_open_positions if normalize_ticker(getattr(p, "ticker", ""))}
    existing = open_by_entity.get(str(cand.entity_id or ""))
    if existing is not None:
        rb = dict(cand.rulebook or {})
        if not bool(rb.get("add_buy_enabled", False)):
            return "already_open_entity_add_buy_disabled"
        if int(getattr(existing, "add_buy_count", 0) or 0) >= int(rb.get("add_buy_max_count", 0) or 0):
            return "add_buy_max_count"
    elif ticker in open_tickers:
        return "already_held_ticker"
    if allocation_score <= 0.0:
        return "non_positive_allocation_score"
    max_positions = max(int(alloc_params.max_positions or 0), 0)
    if len(open_tickers) >= max_positions:
        return "max_positions_full_or_ranked_out"
    return "ranked_out_or_duplicate_ticker"


def _open_positions_snapshot(day, ledger: EntityPositionLedger, provider: CacheOnlyDataProvider) -> list[dict]:
    snapshot: list[dict] = []
    for pos in list(ledger.open_positions()):
        shares = normalize_shares(getattr(pos, "open_shares", 0.0))
        if shares <= 0.0:
            continue
        ticker = normalize_ticker(getattr(pos, "ticker", ""))
        current_price = _close_price_on_day(provider, ticker, day)
        entry_price = float(getattr(pos, "avg_entry_price", 0.0) or 0.0)
        unrealized_pnl_pct = ((current_price - entry_price) / entry_price * 100.0) if current_price > 0.0 and entry_price > 0.0 else 0.0
        snapshot.append(
            {
                "position_id": str(getattr(pos, "position_id", "") or ""),
                "entity_id": str(getattr(pos, "entity_id", "") or ""),
                "ticker": ticker,
                "open_shares": float(shares),
                "avg_entry_price": float(entry_price),
                "current_price": float(current_price or 0.0),
                "unrealized_pnl_pct": float(unrealized_pnl_pct),
            }
        )
    return snapshot


def _flush_candidate_log(path: str | Path, rows: list[dict], *, append: bool = False) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(out, mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _initialize_record_from_entry(pos, entity: EntityRecord, rb: Rulebook, provider: CacheOnlyDataProvider, order, signal_day) -> None:
    df = provider.load_price_df(entity.ticker)
    idx = _index_for_date(df, signal_day)
    atr = 0.0
    if idx is not None:
        atr = _float(df.iloc[idx].get("ATR", 0.0))
    if atr <= 0.0:
        atr = float(order.filled_avg_price or 0.0) * 0.02
    state = initialize_position_state(
        ticker=entity.ticker,
        entry_price=float(order.filled_avg_price or 0.0),
        shares=float(order.filled_shares or 0.0),
        rulebook=rb,
        atr_value=atr,
        entry_date=str(order.filled_at or pd.Timestamp(signal_day).strftime("%Y-%m-%d")),
        member_hash=entity.rulebook_hash,
    )
    pos.rulebook_hash = entity.rulebook_hash or compute_rulebook_hash(entity.rulebook)
    pos.member_hash = entity.rulebook_hash
    pos.rulebook_snapshot = dict(entity.rulebook)
    _copy_state_to_record(pos, state)


def _position_state_from_record(pos) -> PositionState:
    return PositionState(
        ticker=pos.ticker,
        direction=pos.direction or "long",
        entry_date=pos.entry_date,
        entry_price=float(pos.avg_entry_price or 0.0),
        avg_cost=float(pos.avg_entry_price or 0.0),
        shares=float(pos.open_shares or 0.0),
        atr_at_entry=float(pos.atr_at_entry or 0.0),
        stop_price=float(pos.stop_price or 0.0),
        target_price=float(pos.target_price or 0.0),
        trailing_stop=float(pos.trailing_stop or 0.0),
        trailing_distance=float(pos.trailing_distance or 0.0),
        highest_price=float(pos.highest_price or pos.avg_entry_price or 0.0),
        max_holding_days=int(pos.max_holding_days or 0),
        exit_strategy=str(pos.exit_strategy or "hybrid"),
        holding_trading_days=0,
        add_buy_count=int(pos.add_buy_count or 0),
        rulebook_snapshot=dict(pos.rulebook_snapshot or {}),
        member_hash=str(pos.member_hash or ""),
    )


def _copy_state_to_record(pos, state: PositionState) -> None:
    pos.direction = state.direction
    pos.atr_at_entry = float(state.atr_at_entry or 0.0)
    pos.stop_price = float(state.stop_price or 0.0)
    pos.target_price = float(state.target_price or 0.0)
    pos.trailing_distance = float(state.trailing_distance or 0.0)
    pos.trailing_stop = float(state.trailing_stop or 0.0)
    pos.highest_price = float(state.highest_price or 0.0)
    pos.exit_strategy = str(state.exit_strategy or "")
    pos.max_holding_days = int(state.max_holding_days or 0)
    pos.add_buy_count = int(state.add_buy_count or pos.add_buy_count or 0)


def _equity_point(day, broker: SimBroker, ledger: EntityPositionLedger) -> EquityPoint:
    holdings_value = sum(h.market_value for h in broker.get_holdings())
    return EquityPoint(
        date=pd.Timestamp(day).strftime("%Y-%m-%d"),
        cash=float(broker.cash),
        holdings_value=float(holdings_value),
        equity=float(broker.cash + holdings_value),
        open_position_count=len(ledger.open_positions()),
    )


def _finalize_result(result: BacktestResult, initial_capital: float, ledger: EntityPositionLedger, broker: SimBroker) -> None:
    result.final_equity = float(result.equity_curve[-1].equity if result.equity_curve else initial_capital)
    result.total_return = ((result.final_equity - initial_capital) / initial_capital * 100.0) if initial_capital else 0.0
    result.max_drawdown_pct = _max_drawdown([p.equity for p in result.equity_curve])
    pnl: dict[str, float] = {}
    for pos in ledger._positions.values():  # central module internal diagnostic summary
        pnl[pos.entity_id] = pnl.get(pos.entity_id, 0.0) + float(pos.realized_pnl or 0.0)
        if pos.open_shares > 0:
            mark = broker.get_mark_price(pos.ticker)
            pnl[pos.entity_id] += float(pos.open_shares) * (mark - float(pos.avg_entry_price or 0.0))
    result.per_entity_pnl = pnl


def _trading_days(dfs: Iterable[pd.DataFrame], start: str, end: str) -> list[pd.Timestamp]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    all_days = set()
    for df in dfs:
        for value in df.index:
            ts = pd.Timestamp(value).normalize()
            if start_ts <= ts <= end_ts:
                all_days.add(ts)
    return sorted(all_days)


def _close_price_on_day(provider: CacheOnlyDataProvider, ticker: str, day) -> float:
    df = provider.load_price_df(ticker)
    idx = _index_for_date(df, day)
    if idx is None:
        return 0.0
    row = df.iloc[idx]
    return _float(row.get("Close", row.get("close", 0.0)))


def _index_for_date(df: pd.DataFrame, date) -> Optional[int]:
    ts = pd.Timestamp(date).normalize()
    idx = df.index.normalize().get_indexer([ts], method=None)
    if len(idx) and idx[0] >= 0:
        return int(idx[0])
    return None


def _holding_days(df: pd.DataFrame, entry_date: str, current_idx: int) -> int:
    entry_idx = _index_for_date(df, entry_date)
    if entry_idx is None:
        return max(int(current_idx), 0)
    return max(0, int(current_idx) - int(entry_idx))


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak * 100.0)
    return float(worst)


def _record_reject(result: BacktestResult, date: str, entity_id: str, ticker: str, side: str, requested_shares: float, order) -> None:
    result.rejected_orders.append(
        RejectedOrderRecord(
            date=str(date),
            entity_id=str(entity_id),
            ticker=str(ticker),
            side=str(side),
            requested_shares=float(requested_shares or 0.0),
            reason=str(getattr(order, "message", "") or getattr(order, "raw_status", "rejected") or "rejected"),
            order_id=str(getattr(order, "order_id", "") or ""),
            client_order_id=str(getattr(order, "client_order_id", "") or ""),
        )
    )


def _client_order_id(side: str, entity_id: str, ticker: str, date: str) -> str:
    seed = f"cbt-{side}-{ticker}-{entity_id}-{date}"
    return seed[:48]


def _optional_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0
