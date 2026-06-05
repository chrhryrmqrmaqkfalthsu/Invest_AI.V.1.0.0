"""
AlpacaBroker - Alpaca paper trading broker adapter.

목표
----
- 기존 Broker 인터페이스에 맞춰 Alpaca paper 계좌를 연결한다.
- shares는 float를 허용한다. Alpaca fractional 주문은 market + DAY 조건에서만 허용한다.
- 인증 정보는 환경변수 또는 프로젝트 루트 .env에서만 읽는다. 키 값을 코드에 넣지 않는다.

필수 설정
---------
- ALPACA_API_KEY
- ALPACA_SECRET_KEY
- ALPACA_BASE_URL (선택, 기본 https://paper-api.alpaca.markets)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Any

from dotenv import dotenv_values
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from .base import (
    Balance,
    Broker,
    BrokerError,
    Holding,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

log = logging.getLogger("alpaca_broker")

DEFAULT_ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
ENV_PATH = Path.home() / "kingmaker" / ".env"
SHARE_ROUND_DIGITS = 6
SHARE_EPS = 10 ** (-SHARE_ROUND_DIGITS)


class AlpacaBroker(Broker):
    """Alpaca paper broker implementation.

    Alpaca fractional qty를 지원한다. 단, stage 4/4 정책상 fractional 주문은
    market order + DAY time-in-force로만 허용한다. 정수 qty는 market/limit 모두 허용한다.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        paper: bool = True,
        env_path: Optional[str] = None,
    ):
        env = dotenv_values(env_path or str(ENV_PATH))
        self.api_key = (api_key or os.environ.get("ALPACA_API_KEY") or env.get("ALPACA_API_KEY") or "").strip()
        self.secret_key = (secret_key or os.environ.get("ALPACA_SECRET_KEY") or env.get("ALPACA_SECRET_KEY") or "").strip()
        self.base_url = (
            base_url
            or os.environ.get("ALPACA_BASE_URL")
            or env.get("ALPACA_BASE_URL")
            or DEFAULT_ALPACA_BASE_URL
        ).strip()
        self.paper = bool(paper)

        if not self.api_key:
            raise BrokerError("ALPACA_API_KEY 환경변수/.env 누락")
        if not self.secret_key:
            raise BrokerError("ALPACA_SECRET_KEY 환경변수/.env 누락")

        # TradingClient paper=True + url_override로 paper endpoint를 명시한다.
        self.trading = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=self.paper,
            url_override=self.base_url,
        )
        self.data = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
        )

    @property
    def mode(self) -> str:
        return "alpaca_paper" if self.paper else "alpaca_live"

    # ---------- helpers ----------
    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_qty(shares: int | float) -> float:
        try:
            qty = round(float(shares), SHARE_ROUND_DIGITS)
        except Exception as e:
            raise BrokerError(f"shares must be numeric (got {shares!r})") from e
        if qty <= SHARE_EPS:
            raise BrokerError(f"shares must be positive (got {shares})")
        return qty

    @staticmethod
    def _is_fractional_qty(qty: float) -> bool:
        return abs(qty - round(qty)) > SHARE_EPS

    @staticmethod
    def _raw_status(status: Any) -> str:
        return str(getattr(status, "value", status) or "").lower()

    @staticmethod
    def _map_status(status: Any, *, filled_qty: float = 0.0, qty: float = 0.0) -> OrderStatus:
        raw = AlpacaBroker._raw_status(status)
        if raw == "filled":
            return OrderStatus.FILLED
        # Alpaca의 calculated는 체결 후 정산 단계일 수 있다. 수량이 전량이면
        # BN-1 상태머신이 불필요하게 영구 PENDING으로 남지 않도록 FILLED로 본다.
        if raw == "calculated" and qty > SHARE_EPS and filled_qty + SHARE_EPS >= qty:
            return OrderStatus.FILLED
        if raw == "partially_filled":
            return OrderStatus.PARTIAL
        if raw in {"canceled", "cancelled", "expired", "replaced"}:
            return OrderStatus.CANCELLED
        if raw == "rejected":
            return OrderStatus.REJECTED
        if raw == "failed":
            return OrderStatus.FAILED
        # new/accepted/pending_new/pending_cancel/pending_replace/pending_review/
        # accepted_for_bidding/stopped/suspended/calculated/held/done_for_day 등은
        # 원본 상태를 보존한 채 fail-closed PENDING으로 축약한다.
        return OrderStatus.PENDING

    @staticmethod
    def _map_side(side: Any) -> OrderSide:
        raw = str(getattr(side, "value", side) or "").lower()
        return OrderSide.SELL if raw == "sell" else OrderSide.BUY

    @staticmethod
    def _map_order_type(order_type: Any) -> OrderType:
        raw = str(getattr(order_type, "value", order_type) or "").lower()
        return OrderType.LIMIT if raw == "limit" else OrderType.MARKET

    def _alpaca_order_to_order(self, o: Any) -> Order:
        order_id = str(getattr(o, "id", "") or "")
        ticker = str(getattr(o, "symbol", "") or "").upper()
        side = self._map_side(getattr(o, "side", ""))
        order_type = self._map_order_type(getattr(o, "type", ""))
        qty = self._to_float(getattr(o, "qty", 0), 0.0)
        filled_qty = self._to_float(getattr(o, "filled_qty", 0), 0.0)
        limit_price = self._to_float(getattr(o, "limit_price", 0), 0.0)
        filled_avg = self._to_float(getattr(o, "filled_avg_price", 0), 0.0)
        submitted_at = getattr(o, "submitted_at", "") or ""
        filled_at = getattr(o, "filled_at", "") or ""
        raw_status = self._raw_status(getattr(o, "status", ""))
        client_order_id = str(getattr(o, "client_order_id", "") or "")
        replaced_by = str(getattr(o, "replaced_by", "") or "")

        return Order(
            order_id=order_id,
            ticker=ticker,
            side=side,
            order_type=order_type,
            shares=qty,
            price=limit_price,
            status=self._map_status(getattr(o, "status", ""), filled_qty=filled_qty, qty=qty),
            filled_shares=filled_qty,
            filled_avg_price=filled_avg,
            commission=0.0,
            submitted_at=str(submitted_at),
            filled_at=str(filled_at),
            message=client_order_id,
            raw_status=raw_status,
            client_order_id=client_order_id,
            replaced_by=replaced_by,
        )

    # ---------- account / holdings ----------
    def get_balance(self) -> Balance:
        try:
            acct = self.trading.get_account()
            positions = self.get_holdings()
            cash = self._to_float(getattr(acct, "cash", 0), 0.0)
            total_value = self._to_float(getattr(acct, "portfolio_value", 0), cash)
            invested = sum(h.shares * h.avg_cost for h in positions)
            return Balance(
                # 기존 인터페이스 필드명은 KRW지만 Alpaca에서는 USD 값이 들어간다.
                cash_krw=cash,
                total_value_krw=total_value,
                invested_krw=invested,
                holdings=positions,
                fetched_at=datetime.now().isoformat(),
            )
        except Exception as e:
            raise BrokerError(f"Alpaca get_balance 실패: {e}") from e

    def get_holdings(self) -> List[Holding]:
        try:
            rows = self.trading.get_all_positions()
        except Exception as e:
            raise BrokerError(f"Alpaca get_holdings 실패: {e}") from e

        out: List[Holding] = []
        for p in rows:
            qty = self._to_float(getattr(p, "qty", 0), 0.0)
            if qty <= 0:
                continue
            avg_cost = self._to_float(getattr(p, "avg_entry_price", 0), 0.0)
            cur = self._to_float(getattr(p, "current_price", 0), avg_cost)
            market_value = self._to_float(getattr(p, "market_value", 0), qty * cur)
            pnl = self._to_float(getattr(p, "unrealized_pl", 0), 0.0)
            pnl_pct = self._to_float(getattr(p, "unrealized_plpc", 0), 0.0) * 100.0
            out.append(
                Holding(
                    ticker=str(getattr(p, "symbol", "") or "").upper(),
                    shares=qty,
                    avg_cost=avg_cost,
                    current_price=cur,
                    market_value=market_value,
                    unrealized_pnl=pnl,
                    unrealized_pnl_pct=pnl_pct,
                )
            )
        return out

    # ---------- quotes / market ----------
    def get_current_price(self, ticker: str) -> Optional[float]:
        ticker_u = str(ticker).strip().upper()
        try:
            req = StockLatestTradeRequest(symbol_or_symbols=ticker_u)
            data = self.data.get_stock_latest_trade(req)
            trade = data.get(ticker_u) if isinstance(data, dict) else None
            if trade is None:
                return None
            price = self._to_float(getattr(trade, "price", None), 0.0)
            return price if price > 0 else None
        except Exception as e:
            log.warning(f"Alpaca get_current_price 실패 {ticker_u}: {e}")
            return None

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        try:
            clock = self.trading.get_clock()
            return bool(getattr(clock, "is_open", False))
        except Exception as e:
            log.warning(f"Alpaca market clock 조회 실패: {e}")
            return False

    # ---------- orders ----------
    def _submit_order(
        self,
        side: OrderSide,
        ticker: str,
        shares: float,
        order_type: OrderType,
        price: float,
    ) -> Order:
        qty = self._normalize_qty(shares)
        ticker_u = str(ticker).strip().upper()
        alpaca_side = AlpacaOrderSide.BUY if side == OrderSide.BUY else AlpacaOrderSide.SELL
        is_fractional = self._is_fractional_qty(qty)

        try:
            if is_fractional and order_type != OrderType.MARKET:
                raise BrokerError(
                    f"Alpaca fractional qty requires market order + DAY TIF (ticker={ticker_u}, qty={qty:g})"
                )

            if order_type == OrderType.LIMIT:
                if price <= 0:
                    raise BrokerError("limit order requires price > 0")
                req = LimitOrderRequest(
                    symbol=ticker_u,
                    qty=qty,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=float(price),
                )
            else:
                req = MarketOrderRequest(
                    symbol=ticker_u,
                    qty=qty,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                )
            o = self.trading.submit_order(order_data=req)
            return self._alpaca_order_to_order(o)
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(f"Alpaca submit_order 실패 {side.value} {ticker_u} {qty:g}: {e}") from e

    def place_buy(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        return self._submit_order(OrderSide.BUY, ticker, shares, order_type, price)

    def place_sell(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        return self._submit_order(OrderSide.SELL, ticker, shares, order_type, price)

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.trading.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            log.warning(f"Alpaca cancel_order 실패 {order_id}: {e}")
            return False

    def get_order(self, order_id: str) -> Optional[Order]:
        try:
            o = self.trading.get_order_by_id(order_id)
            return self._alpaca_order_to_order(o)
        except Exception as e:
            log.warning(f"Alpaca get_order 실패 {order_id}: {e}")
            return None


if __name__ == "__main__":
    # 네트워크/API 키 없이 가능한 import-level smoke 안내.
    print("AlpacaBroker loaded. Required env/.env: ALPACA_API_KEY, ALPACA_SECRET_KEY")
    print(f"Default paper base URL: {DEFAULT_ALPACA_BASE_URL}")
