"""Alpaca broker adapter. BN-2/BT-5: client_order_id submit/recovery."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest
from dotenv import dotenv_values

from .base import Balance, Broker, BrokerError, Holding, Order, OrderSide, OrderStatus, OrderType

log = logging.getLogger("alpaca_broker")
DEFAULT_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
ENV_PATH = Path.home() / "kingmaker" / ".env"
SHARE_EPS = 1e-6
CLIENT_LOOKUP_FOUND = "FOUND"
CLIENT_LOOKUP_NOT_FOUND = "NOT_FOUND"
CLIENT_LOOKUP_UNKNOWN = "UNKNOWN"


class AlpacaBroker(Broker):
    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None,
                 base_url: Optional[str] = None, paper: bool = True, env_path: Optional[str] = None):
        env = dotenv_values(env_path or str(ENV_PATH))
        self.api_key = (api_key or os.environ.get("ALPACA_API_KEY") or env.get("ALPACA_API_KEY") or "").strip()
        self.secret_key = (secret_key or os.environ.get("ALPACA_SECRET_KEY") or env.get("ALPACA_SECRET_KEY") or "").strip()
        self.base_url = (base_url or os.environ.get("ALPACA_BASE_URL") or env.get("ALPACA_BASE_URL") or DEFAULT_ALPACA_BASE_URL).strip()
        self.paper = bool(paper)
        if not self.api_key:
            raise BrokerError("ALPACA_API_KEY 환경변수/.env 누락")
        if not self.secret_key:
            raise BrokerError("ALPACA_SECRET_KEY 환경변수/.env 누락")
        self.trading = TradingClient(self.api_key, self.secret_key, paper=self.paper, url_override=self.base_url)
        self.data = StockHistoricalDataClient(self.api_key, self.secret_key)

    @property
    def mode(self) -> str:
        return "alpaca_paper" if self.paper else "alpaca_live"

    @staticmethod
    def _f(v: Any, default: float = 0.0) -> float:
        try:
            return default if v is None else float(v)
        except Exception:
            return default

    @staticmethod
    def _qty(shares: float) -> float:
        q = round(float(shares), 6)
        if q <= SHARE_EPS:
            raise BrokerError(f"shares must be positive (got {shares})")
        return q

    @staticmethod
    def _raw(v: Any) -> str:
        return str(getattr(v, "value", v) or "").lower()

    @staticmethod
    def _is_not_found_exception(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None and getattr(exc, "response", None) is not None:
            status = getattr(exc.response, "status_code", None)
        text = f"{type(exc).__name__} {exc}".lower()
        return status == 404 or "404" in text or "not found" in text

    @classmethod
    def _status(cls, raw: Any, filled: float, qty: float) -> OrderStatus:
        s = cls._raw(raw)
        if s == "filled" or (s == "calculated" and qty > SHARE_EPS and filled + SHARE_EPS >= qty):
            return OrderStatus.FILLED
        if s == "partially_filled":
            return OrderStatus.PARTIAL
        if s in {"canceled", "cancelled", "expired", "replaced"}:
            return OrderStatus.CANCELLED
        if s == "rejected":
            return OrderStatus.REJECTED
        if s == "failed":
            return OrderStatus.FAILED
        return OrderStatus.PENDING

    def _map(self, o: Any) -> Order:
        qty = self._f(getattr(o, "qty", 0))
        filled = self._f(getattr(o, "filled_qty", 0))
        raw = self._raw(getattr(o, "status", ""))
        client_id = str(getattr(o, "client_order_id", "") or "")
        side = OrderSide.SELL if self._raw(getattr(o, "side", "")) == "sell" else OrderSide.BUY
        typ = OrderType.LIMIT if self._raw(getattr(o, "type", "")) == "limit" else OrderType.MARKET
        return Order(
            order_id=str(getattr(o, "id", "") or ""), ticker=str(getattr(o, "symbol", "") or "").upper(),
            side=side, order_type=typ, shares=qty, price=self._f(getattr(o, "limit_price", 0)),
            status=self._status(getattr(o, "status", ""), filled, qty), filled_shares=filled,
            filled_avg_price=self._f(getattr(o, "filled_avg_price", 0)), commission=0.0,
            submitted_at=str(getattr(o, "submitted_at", "") or ""), filled_at=str(getattr(o, "filled_at", "") or ""),
            message=client_id, raw_status=raw, client_order_id=client_id,
            replaced_by=str(getattr(o, "replaced_by", "") or ""),
        )

    def get_balance(self) -> Balance:
        try:
            acct = self.trading.get_account()
            holdings = self.get_holdings()
            cash = self._f(getattr(acct, "cash", 0))
            total = self._f(getattr(acct, "portfolio_value", 0), cash)
            return Balance(cash, total, sum(h.shares * h.avg_cost for h in holdings), holdings, datetime.now().isoformat())
        except Exception as e:
            raise BrokerError(f"Alpaca get_balance 실패: {e}") from e

    def get_holdings(self) -> List[Holding]:
        try:
            rows = self.trading.get_all_positions()
        except Exception as e:
            raise BrokerError(f"Alpaca get_holdings 실패: {e}") from e
        out: List[Holding] = []
        for p in rows:
            qty = self._f(getattr(p, "qty", 0))
            if qty <= 0:
                continue
            avg = self._f(getattr(p, "avg_entry_price", 0))
            cur = self._f(getattr(p, "current_price", 0), avg)
            out.append(Holding(str(getattr(p, "symbol", "") or "").upper(), qty, avg, cur,
                               self._f(getattr(p, "market_value", 0), qty * cur),
                               self._f(getattr(p, "unrealized_pl", 0)),
                               self._f(getattr(p, "unrealized_plpc", 0)) * 100.0))
        return out

    def get_current_price(self, ticker: str) -> Optional[float]:
        t = str(ticker).strip().upper()
        try:
            data = self.data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=t))
            trade = data.get(t) if isinstance(data, dict) else None
            price = self._f(getattr(trade, "price", None), 0.0) if trade else 0.0
            return price if price > 0 else None
        except Exception as e:
            log.warning(f"Alpaca get_current_price 실패 {t}: {e}")
            return None

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        try:
            return bool(getattr(self.trading.get_clock(), "is_open", False))
        except Exception as e:
            log.warning(f"Alpaca market clock 조회 실패: {e}")
            return False

    def _submit_order(self, side: OrderSide, ticker: str, shares: float, order_type: OrderType,
                      price: float, client_order_id: str = "") -> Order:
        qty = self._qty(shares)
        t = str(ticker).strip().upper()
        if abs(qty - round(qty)) > SHARE_EPS and order_type != OrderType.MARKET:
            raise BrokerError(f"Alpaca fractional qty requires market order + DAY TIF (ticker={t}, qty={qty:g})")
        common = {"symbol": t, "qty": qty, "side": AlpacaOrderSide.BUY if side == OrderSide.BUY else AlpacaOrderSide.SELL, "time_in_force": TimeInForce.DAY}
        client_order_id = str(client_order_id or "").strip()
        if client_order_id:
            common["client_order_id"] = client_order_id
        try:
            req = LimitOrderRequest(limit_price=float(price), **common) if order_type == OrderType.LIMIT else MarketOrderRequest(**common)
            return self._map(self.trading.submit_order(order_data=req))
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(f"Alpaca submit_order 실패 {side.value} {t} {qty:g}: {e}") from e

    def place_buy(self, ticker: str, shares: float, order_type: OrderType = OrderType.MARKET,
                  price: float = 0.0, client_order_id: str = "") -> Order:
        return self._submit_order(OrderSide.BUY, ticker, shares, order_type, price, client_order_id)

    def place_sell(self, ticker: str, shares: float, order_type: OrderType = OrderType.MARKET,
                   price: float = 0.0, client_order_id: str = "") -> Order:
        return self._submit_order(OrderSide.SELL, ticker, shares, order_type, price, client_order_id)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.trading.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            log.warning(f"Alpaca cancel_order 실패 {order_id}: {e}")
            return False

    def get_order(self, order_id: str) -> Optional[Order]:
        try:
            return self._map(self.trading.get_order_by_id(order_id))
        except Exception as e:
            log.warning(f"Alpaca get_order 실패 {order_id}: {e}")
            return None

    def get_open_orders(self) -> List[Order]:
        try:
            rows = self.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
            return [self._map(row) for row in rows or []]
        except Exception as e:
            raise BrokerError(f"Alpaca get_open_orders 실패: {e}") from e

    def get_order_by_client_order_id_result(self, client_order_id: str) -> Tuple[str, Optional[Order]]:
        cid = str(client_order_id or "").strip()
        if not cid:
            return CLIENT_LOOKUP_NOT_FOUND, None
        getter = getattr(self.trading, "get_order_by_client_id", None) or getattr(self.trading, "get_order_by_client_order_id", None)
        if getter is None:
            return CLIENT_LOOKUP_UNKNOWN, None
        try:
            return CLIENT_LOOKUP_FOUND, self._map(getter(cid))
        except Exception as e:
            if self._is_not_found_exception(e):
                log.info(f"Alpaca client_order_id 미접수 확인 {cid}: {e}")
                return CLIENT_LOOKUP_NOT_FOUND, None
            log.warning(f"Alpaca get_order_by_client_order_id 장애 {cid}: {e}")
            return CLIENT_LOOKUP_UNKNOWN, None

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Order]:
        status, order = self.get_order_by_client_order_id_result(client_order_id)
        return order if status == CLIENT_LOOKUP_FOUND else None


if __name__ == "__main__":
    print("AlpacaBroker loaded")
