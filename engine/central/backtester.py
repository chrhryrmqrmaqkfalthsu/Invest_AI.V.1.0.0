"""Daily central-policy backtester skeleton.

This module orchestrates entity-level signals, score-based allocation, simulated
broker fills, EntityPositionLedger accounting, and daily reconcile checks.
"""
from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from engine.central.allocation_policy import AllocationParams, BuyCandidate, decide_buys
from engine.central.entity_loader import EntityRecord
from engine.central.ledger import EntityPositionLedger
from engine.central.models import normalize_shares
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
) -> BacktestResult:
    entity_list = list(entities)
    if not entity_list:
        raise ValueError("entities required")
    provider = data_provider or CacheOnlyDataProvider()
    collector = SignalCollector(provider, use_llm_events=use_llm_events)
    price_data = {ticker: provider.load_price_df(ticker) for ticker in sorted({e.ticker for e in entity_list})}
    broker = SimBroker(price_data, initial_cash=alloc_params.total_capital, fill_policy=fill_policy or FillPolicy())
    if ledger_dir is None:
        ledger_dir = tempfile.mkdtemp(prefix="central_bt_ledger_")
    ledger = EntityPositionLedger(base_dir=Path(ledger_dir))
    entity_by_id = {e.entity_id: e for e in entity_list}
    rb_by_entity = {e.entity_id: Rulebook.from_dict(e.rulebook) for e in entity_list}

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
        decisions = decide_buys(candidates, ledger, alloc_params)
        for decision in decisions:
            _execute_buy(day, decision, ledger, broker, provider, entity_by_id[decision.entity_id], rb_by_entity[decision.entity_id], alloc_params, result)
        rec = ledger.reconcile(broker)
        if not rec.get("ok"):
            result.reconcile_failures.append({"date": pd.Timestamp(day).strftime("%Y-%m-%d"), **rec})
        result.equity_curve.append(_equity_point(day, broker, ledger))
    _finalize_result(result, alloc_params.total_capital, ledger, broker)
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


def _execute_buy(day, decision, ledger, broker: SimBroker, provider: CacheOnlyDataProvider, entity: EntityRecord, rb: Rulebook, alloc_params: AllocationParams, result: BacktestResult) -> None:
    d_close = _close_price_on_day(provider, decision.ticker, day)
    shares = _cash_capped_shares(broker.cash, decision.shares, d_close, alloc_params.cash_buffer_ratio)
    if shares <= 0.0:
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
        "central_policy_buy",
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
            reason="central_policy_buy",
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
    return normalize_shares(min(requested, max_affordable))


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
