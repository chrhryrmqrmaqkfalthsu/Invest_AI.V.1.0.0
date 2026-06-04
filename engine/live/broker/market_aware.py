"""Calendar-aware/fail-closed broker wrappers used by the live factory.

The underlying AlpacaBroker is intentionally untouched.  PaperBroker delegates
market-open checks to the shared MarketClock.  KisBroker remains a domestic
order implementation; US tickers are blocked before any domestic quote/order
endpoint can be called.
"""
from __future__ import annotations

import logging
from typing import Optional

from engine.live.market_clock import market_clock_for_ticker, market_region_for_ticker

from .base import BrokerError, Order, OrderType
from .kis import KisBroker
from .paper import PaperBroker

log = logging.getLogger("market_aware_broker")


class CalendarAwarePaperBroker(PaperBroker):
    """PaperBroker whose market-open guard uses the shared exact calendar."""

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        try:
            return bool(market_clock_for_ticker(ticker or "379800").is_open())
        except Exception as exc:
            log.warning("Paper market clock check failed (%s)", type(exc).__name__)
            return False


class GuardedKisBroker(KisBroker):
    """KIS domestic broker that fails closed for unsupported US live paths."""

    @staticmethod
    def _ensure_domestic_ticker(ticker: str) -> None:
        if market_region_for_ticker(ticker) != "KRX":
            raise BrokerError(
                f"KisBroker domestic live path does not support US ticker {str(ticker).strip().upper()}; "
                "use Alpaca/Paper or the explicit KIS overseas dry-run path"
            )

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        if ticker and market_region_for_ticker(ticker) != "KRX":
            log.warning("KIS market-open check blocked for unsupported US ticker %s", str(ticker).strip().upper())
            return False
        return bool(market_clock_for_ticker(ticker or "379800").is_open())

    def get_current_price(self, ticker: str):
        try:
            self._ensure_domestic_ticker(ticker)
        except BrokerError as exc:
            log.warning("KIS quote blocked: %s", exc)
            return None
        return super().get_current_price(ticker)

    def place_buy(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        self._ensure_domestic_ticker(ticker)
        return super().place_buy(ticker, shares, order_type, price)

    def place_sell(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        self._ensure_domestic_ticker(ticker)
        return super().place_sell(ticker, shares, order_type, price)
