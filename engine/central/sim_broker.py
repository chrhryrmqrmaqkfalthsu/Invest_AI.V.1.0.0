"""Deterministic simulated broker for central-controller backtests.

The real broker aggregates holdings by ticker. This simulator intentionally does
the same so EntityPositionLedger can test the core invariant:

    sum(entity ledger shares by ticker) == simulated broker shares by ticker

No network calls or real orders are performed here.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Mapping, Optional

import pandas as pd

from engine.central.models import normalize_shares, normalize_ticker
from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType


@dataclass(frozen=True)
class FillPolicy:
    """Execution-price policy for daily-bar simulations.

    ``next_open`` means a signal produced on day D is filled at D+1 open. If the
    next row is unavailable, the simulator falls back to D close.
    """

    mode: str = "next_open"
    fallback: str = "same_day_close"
    slippage_bps: float = 0.0
    commission_rate: float = 0.0


class SimBroker:
    """In-memory broker compatible with ``LedgerBrokerPort``.

    Price data must be supplied up front. The orchestrator calls ``set_date(D)``
    before placing orders; market orders then fill immediately according to the
    configured ``FillPolicy``.
    """

    def __init__(
        self,
        price_data_by_ticker: Optional[Mapping[str, pd.DataFrame]] = None,
        *,
        initial_cash: float = 100_000.0,
        fill_policy: Optional[FillPolicy] = None,
    ) -> None:
        self.price_data_by_ticker: Dict[str, pd.DataFrame] = {}
        for ticker, df in dict(price_data_by_ticker or {}).items():
            self.set_price_data(ticker, df)
        self.initial_cash = float(initial_cash or 0.0)
        self.cash = float(initial_cash or 0.0)
        self.fill_policy = fill_policy or FillPolicy()
        self.current_date: Optional[pd.Timestamp] = None
        self._holdings: Dict[str, Dict[str, float]] = {}
        self._orders: Dict[str, Order] = {}
        self._client_to_order: Dict[str, str] = {}
        self._ids = itertools.count(1)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def set_price_data(self, ticker: str, df: pd.DataFrame) -> None:
        ticker_u = normalize_ticker(ticker)
        if df is None or df.empty:
            raise ValueError(f"empty price data for {ticker_u}")
        prepared = df.copy()
        if "Date" in prepared.columns:
            prepared.index = pd.to_datetime(prepared["Date"], errors="coerce")
        elif "date" in prepared.columns:
            prepared.index = pd.to_datetime(prepared["date"], errors="coerce")
        else:
            prepared.index = pd.to_datetime(prepared.index, errors="coerce")
        prepared = prepared[~prepared.index.isna()].sort_index()
        if prepared.empty:
            raise ValueError(f"price data for {ticker_u} has no valid dates")
        self.price_data_by_ticker[ticker_u] = prepared

    def set_date(self, value) -> None:
        self.current_date = pd.Timestamp(value).normalize()

    def get_holdings(self) -> list[Holding]:
        rows: list[Holding] = []
        for ticker, data in sorted(self._holdings.items()):
            shares = normalize_shares(data.get("shares"))
            if shares <= 0.0:
                continue
            avg = float(data.get("avg_cost") or 0.0)
            current = self.get_mark_price(ticker)
            market_value = shares * current
            unrealized = shares * (current - avg)
            pnl_pct = ((current - avg) / avg * 100.0) if avg else 0.0
            rows.append(Holding(ticker, shares, avg, current, market_value, unrealized, pnl_pct))
        return rows

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(str(order_id or ""))

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Order]:
        order_id = self._client_to_order.get(str(client_order_id or ""))
        return self.get_order(order_id) if order_id else None

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

    def portfolio_value(self) -> float:
        return float(self.cash + sum(h.market_value for h in self.get_holdings()))

    def get_mark_price(self, ticker: str, date=None) -> float:
        row, _ = self._row_for_date(ticker, date if date is not None else self.current_date)
        return float(row.get("Close", row.get("close", row.get("Open", 0.0))) or 0.0)

    def execution_price(self, ticker: str, side: OrderSide, date=None) -> tuple[float, str]:
        ticker_u = normalize_ticker(ticker)
        row, idx = self._row_for_date(ticker_u, date if date is not None else self.current_date)
        df = self.price_data_by_ticker[ticker_u]
        fill_date = df.index[idx]
        base = 0.0
        if self.fill_policy.mode == "same_day_close":
            base = float(row.get("Close", row.get("close", 0.0)) or 0.0)
        else:
            if idx + 1 < len(df):
                next_row = df.iloc[idx + 1]
                fill_date = df.index[idx + 1]
                base = float(next_row.get("Open", next_row.get("Close", 0.0)) or 0.0)
            if base <= 0.0 and self.fill_policy.fallback == "same_day_close":
                base = float(row.get("Close", row.get("close", 0.0)) or 0.0)
        if base <= 0.0:
            raise ValueError(f"non-positive execution price for {ticker_u} at {self.current_date}")
        slip = float(self.fill_policy.slippage_bps or 0.0) / 10000.0
        price = base * (1.0 + slip if side == OrderSide.BUY else 1.0 - slip)
        return float(price), pd.Timestamp(fill_date).strftime("%Y-%m-%d")

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
        if shares_n <= 0.0:
            raise ValueError("shares must be positive")
        fill_price, fill_date = self.execution_price(ticker_u, side)
        if side == OrderSide.SELL:
            held = normalize_shares(self._holdings.get(ticker_u, {}).get("shares", 0.0))
            if shares_n - held > 1e-6:
                raise ValueError(f"sell exceeds simulated holding: {ticker_u} sell={shares_n} held={held}")
        notional = shares_n * fill_price
        commission = notional * float(self.fill_policy.commission_rate or 0.0)
        if side == OrderSide.BUY and notional + commission - self.cash > 1e-6:
            raise ValueError(f"insufficient simulated cash: need={notional + commission:.2f} cash={self.cash:.2f}")

        self._apply_fill_to_holdings(ticker_u, side, shares_n, fill_price, commission)
        order_id = f"SIM-{next(self._ids):08d}"
        order = Order(
            order_id=order_id,
            ticker=ticker_u,
            side=side,
            order_type=order_type,
            shares=shares_n,
            price=float(price or fill_price),
            status=OrderStatus.FILLED,
            filled_shares=shares_n,
            filled_avg_price=fill_price,
            commission=commission,
            submitted_at=fill_date,
            filled_at=fill_date,
            message="simulated immediate fill",
            raw_status="filled",
            client_order_id=str(client_order_id or ""),
        )
        self._orders[order_id] = order
        if order.client_order_id:
            self._client_to_order[order.client_order_id] = order_id
        return order

    def _apply_fill_to_holdings(self, ticker: str, side: OrderSide, shares: float, price: float, commission: float) -> None:
        row = self._holdings.get(ticker, {"shares": 0.0, "avg_cost": 0.0})
        old_shares = normalize_shares(row.get("shares"))
        old_avg = float(row.get("avg_cost") or 0.0)
        if side == OrderSide.BUY:
            new_shares = normalize_shares(old_shares + shares)
            new_avg = ((old_shares * old_avg) + (shares * price)) / new_shares if new_shares else 0.0
            self.cash -= shares * price + commission
            self._holdings[ticker] = {"shares": new_shares, "avg_cost": new_avg}
            return
        new_shares = normalize_shares(old_shares - shares)
        self.cash += shares * price - commission
        if new_shares <= 0.0:
            self._holdings.pop(ticker, None)
        else:
            row["shares"] = new_shares
            self._holdings[ticker] = row

    def _row_for_date(self, ticker: str, date) -> tuple[pd.Series, int]:
        ticker_u = normalize_ticker(ticker)
        if ticker_u not in self.price_data_by_ticker:
            raise KeyError(f"no price data for {ticker_u}")
        if date is None:
            raise ValueError("current simulation date is not set")
        df = self.price_data_by_ticker[ticker_u]
        ts = pd.Timestamp(date).normalize()
        positions = df.index.normalize().get_indexer([ts], method=None)
        idx = int(positions[0]) if len(positions) and positions[0] >= 0 else -1
        if idx < 0:
            eligible = df.index.normalize() <= ts
            if not bool(eligible.any()):
                raise KeyError(f"no price row for {ticker_u} at or before {ts.date()}")
            idx = int(eligible.nonzero()[0][-1])
        return df.iloc[idx], idx
