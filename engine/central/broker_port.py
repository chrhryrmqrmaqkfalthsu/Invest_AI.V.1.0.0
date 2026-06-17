"""Broker port and mock broker for the central entity ledger.

No real broker/network calls live here. The production adapter can implement the
Protocol later; tests use MockBroker only.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

from engine.central.models import normalize_shares, normalize_ticker
from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType


class LedgerBrokerPort(Protocol):
    def get_holdings(self) -> List[Holding]:
        ...

    def place_buy(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        client_order_id: str = "",
    ) -> Order:
        ...

    def place_sell(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        client_order_id: str = "",
    ) -> Order:
        ...

    def get_order(self, order_id: str) -> Optional[Order]:
        ...

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Order]:
        ...


@dataclass
class MockOrderScenario:
    status: OrderStatus = OrderStatus.FILLED
    filled_shares: Optional[float] = None
    filled_avg_price: float = 100.0
    raw_status: str = ""
    message: str = ""
    poll_sequence: List[dict] = field(default_factory=list)


class MockBroker:
    """Deterministic in-memory broker for ledger tests.

    The mock exposes ticker-level holdings, just like the real broker adapter.
    It can return immediate fills, partial fills, pending orders, and rejected
    orders. Polling returns cumulative filled_shares, so the ledger must apply
    only the fill delta.
    """

    def __init__(self) -> None:
        self._holdings: Dict[str, Dict[str, float]] = {}
        self._orders: Dict[str, Order] = {}
        self._client_to_order: Dict[str, str] = {}
        self._poll_sequences: Dict[str, List[dict]] = {}
        self._applied_to_holdings: Dict[str, float] = {}
        self._scenarios: List[MockOrderScenario] = []
        self._ids = itertools.count(1)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _status_value(status) -> OrderStatus:
        if isinstance(status, OrderStatus):
            return status
        return OrderStatus(str(status).lower())

    def queue_order(
        self,
        *,
        status: OrderStatus = OrderStatus.FILLED,
        filled_shares: Optional[float] = None,
        filled_avg_price: float = 100.0,
        raw_status: str = "",
        message: str = "",
        poll_sequence: Optional[List[dict]] = None,
    ) -> None:
        self._scenarios.append(
            MockOrderScenario(
                status=self._status_value(status),
                filled_shares=filled_shares,
                filled_avg_price=float(filled_avg_price or 0.0),
                raw_status=str(raw_status or ""),
                message=str(message or ""),
                poll_sequence=list(poll_sequence or []),
            )
        )

    def set_holding(self, ticker: str, shares: float, avg_cost: float = 0.0, current_price: Optional[float] = None) -> None:
        ticker_u = normalize_ticker(ticker)
        shares_n = normalize_shares(shares)
        if shares_n <= 0.0:
            self._holdings.pop(ticker_u, None)
            return
        price = float(current_price if current_price is not None else avg_cost or 0.0)
        self._holdings[ticker_u] = {"shares": shares_n, "avg_cost": float(avg_cost or 0.0), "current_price": price}

    def get_holdings(self) -> List[Holding]:
        rows: List[Holding] = []
        for ticker, data in sorted(self._holdings.items()):
            shares = normalize_shares(data.get("shares"))
            if shares <= 0.0:
                continue
            avg = float(data.get("avg_cost") or 0.0)
            current = float(data.get("current_price") or avg)
            market_value = shares * current
            unrealized = shares * (current - avg)
            pnl_pct = ((current - avg) / avg * 100.0) if avg else 0.0
            rows.append(Holding(ticker, shares, avg, current, market_value, unrealized, pnl_pct))
        return rows

    def place_buy(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        client_order_id: str = "",
    ) -> Order:
        return self._submit(ticker, OrderSide.BUY, shares, order_type, price, client_order_id)

    def place_sell(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
        client_order_id: str = "",
    ) -> Order:
        return self._submit(ticker, OrderSide.SELL, shares, order_type, price, client_order_id)

    def get_order(self, order_id: str) -> Optional[Order]:
        order = self._orders.get(str(order_id))
        if order is None:
            return None
        seq = self._poll_sequences.get(str(order_id)) or []
        if seq:
            spec = seq.pop(0)
            order = self._clone_order(order, **spec)
            self._orders[str(order_id)] = order
            self._apply_cumulative_fill_to_holdings(order)
        return order

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Order]:
        order_id = self._client_to_order.get(str(client_order_id or ""))
        if not order_id:
            return None
        return self.get_order(order_id)

    def _next_scenario(self, requested_shares: float) -> MockOrderScenario:
        if self._scenarios:
            scenario = self._scenarios.pop(0)
        else:
            scenario = MockOrderScenario(status=OrderStatus.FILLED, filled_shares=requested_shares, filled_avg_price=100.0)
        if scenario.filled_shares is None:
            if scenario.status == OrderStatus.FILLED:
                scenario.filled_shares = requested_shares
            elif scenario.status == OrderStatus.PARTIAL:
                scenario.filled_shares = requested_shares / 2.0
            else:
                scenario.filled_shares = 0.0
        return scenario

    def _submit(
        self,
        ticker: str,
        side: OrderSide,
        shares: float,
        order_type: OrderType,
        price: float,
        client_order_id: str,
    ) -> Order:
        ticker_u = normalize_ticker(ticker)
        shares_n = normalize_shares(shares)
        order_id = f"MOCK-{next(self._ids):06d}"
        scenario = self._next_scenario(shares_n)
        submitted_at = self._now_iso()
        filled_at = submitted_at if scenario.status == OrderStatus.FILLED else ""
        order = Order(
            order_id=order_id,
            ticker=ticker_u,
            side=side,
            order_type=order_type,
            shares=shares_n,
            price=float(price or 0.0),
            status=scenario.status,
            filled_shares=normalize_shares(scenario.filled_shares),
            filled_avg_price=float(scenario.filled_avg_price or price or 0.0),
            submitted_at=submitted_at,
            filled_at=filled_at,
            message=scenario.message,
            raw_status=scenario.raw_status or scenario.status.value,
            client_order_id=str(client_order_id or ""),
        )
        self._orders[order_id] = order
        if order.client_order_id:
            self._client_to_order[order.client_order_id] = order_id
        self._poll_sequences[order_id] = list(scenario.poll_sequence)
        self._applied_to_holdings[order_id] = 0.0
        self._apply_cumulative_fill_to_holdings(order)
        return order

    def _clone_order(self, order: Order, **updates) -> Order:
        status = self._status_value(updates.get("status", order.status))
        filled_shares = normalize_shares(updates.get("filled_shares", order.filled_shares))
        filled_avg_price = float(updates.get("filled_avg_price", order.filled_avg_price) or 0.0)
        raw_status = str(updates.get("raw_status", status.value) or status.value)
        filled_at = updates.get("filled_at", order.filled_at)
        if status == OrderStatus.FILLED and not filled_at:
            filled_at = self._now_iso()
        return Order(
            order_id=order.order_id,
            ticker=order.ticker,
            side=order.side,
            order_type=order.order_type,
            shares=order.shares,
            price=order.price,
            status=status,
            filled_shares=filled_shares,
            filled_avg_price=filled_avg_price,
            commission=order.commission,
            submitted_at=order.submitted_at,
            filled_at=str(filled_at or ""),
            message=str(updates.get("message", order.message) or ""),
            raw_status=raw_status,
            client_order_id=order.client_order_id,
            replaced_by=str(updates.get("replaced_by", order.replaced_by) or ""),
        )

    def _apply_cumulative_fill_to_holdings(self, order: Order) -> None:
        cumulative = normalize_shares(order.filled_shares)
        previous = normalize_shares(self._applied_to_holdings.get(order.order_id, 0.0))
        delta = normalize_shares(cumulative - previous)
        if delta <= 0.0:
            return
        previous_notional = previous * float(self._orders.get(order.order_id, order).filled_avg_price or 0.0)
        new_notional = cumulative * float(order.filled_avg_price or order.price or 0.0)
        delta_price = (new_notional - previous_notional) / delta if delta else float(order.filled_avg_price or order.price or 0.0)
        self._apply_delta(order.ticker, order.side, delta, delta_price)
        self._applied_to_holdings[order.order_id] = cumulative

    def _apply_delta(self, ticker: str, side: OrderSide, delta: float, price: float) -> None:
        ticker_u = normalize_ticker(ticker)
        row = self._holdings.get(ticker_u, {"shares": 0.0, "avg_cost": 0.0, "current_price": float(price or 0.0)})
        old_shares = normalize_shares(row.get("shares"))
        old_avg = float(row.get("avg_cost") or 0.0)
        if side == OrderSide.BUY:
            new_shares = normalize_shares(old_shares + delta)
            new_avg = ((old_shares * old_avg) + (delta * price)) / new_shares if new_shares else 0.0
            self._holdings[ticker_u] = {"shares": new_shares, "avg_cost": new_avg, "current_price": float(price or new_avg)}
            return
        new_shares = normalize_shares(old_shares - delta)
        if new_shares <= 0.0:
            self._holdings.pop(ticker_u, None)
        else:
            row["shares"] = new_shares
            row["current_price"] = float(price or row.get("current_price") or old_avg)
            self._holdings[ticker_u] = row
