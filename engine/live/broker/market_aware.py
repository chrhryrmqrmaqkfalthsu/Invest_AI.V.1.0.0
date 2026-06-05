"""Calendar-aware/fail-closed broker wrappers used by the live factory.

KIS is imported lazily so US-only paper/live startup does not trigger any KRX/KIS
credential checks at module import time.
"""
from __future__ import annotations

import logging
from typing import Optional

from engine.live.market_clock import market_clock_for_ticker, market_region_for_ticker

from .base import Balance, Broker, BrokerError, Holding, Order, OrderType
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


class GuardedKisBroker(Broker):
    """Lazy KIS domestic broker that fails closed for unsupported US live paths."""

    def __init__(self, *args, **kwargs):
        from .kis import KisBroker

        self._inner = KisBroker(*args, **kwargs)
        self.is_guarded_kis_broker = True

    def _require_inner(self):
        inner = getattr(self, "_inner", None)
        if inner is None:
            raise BrokerError("GuardedKisBroker is not initialized")
        return inner

    @staticmethod
    def _ensure_domestic_ticker(ticker: str) -> None:
        if market_region_for_ticker(ticker) != "KRX":
            raise BrokerError(
                f"KisBroker domestic live path does not support US ticker {str(ticker).strip().upper()}; "
                "use Alpaca/Paper or the explicit KIS overseas dry-run path"
            )

    @property
    def mode(self) -> str:
        return "live"

    def get_balance(self) -> Balance:
        return self._require_inner().get_balance()

    def get_holdings(self) -> list[Holding]:
        return self._require_inner().get_holdings()

    def is_market_open(self, ticker: Optional[str] = None) -> bool:
        if ticker and market_region_for_ticker(ticker) != "KRX":
            log.warning("KIS market-open check blocked for unsupported US ticker %s", str(ticker).strip().upper())
            return False
        # Avoid touching KIS credentials/network for object.__new__ test doubles.
        if getattr(self, "_inner", None) is None:
            return bool(market_clock_for_ticker(ticker or "379800").is_open())
        return self._require_inner().is_market_open(ticker)

    def get_current_price(self, ticker: str):
        try:
            self._ensure_domestic_ticker(ticker)
        except BrokerError as exc:
            log.warning("KIS quote blocked: %s", exc)
            return None
        return self._require_inner().get_current_price(ticker)

    def place_buy(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        self._ensure_domestic_ticker(ticker)
        return self._require_inner().place_buy(ticker, shares, order_type, price)

    def place_sell(
        self,
        ticker: str,
        shares: float,
        order_type: OrderType = OrderType.MARKET,
        price: float = 0.0,
    ) -> Order:
        self._ensure_domestic_ticker(ticker)
        return self._require_inner().place_sell(ticker, shares, order_type, price)

    def cancel_order(self, order_id: str) -> bool:
        return self._require_inner().cancel_order(order_id)

    def get_order(self, order_id: str):
        return self._require_inner().get_order(order_id)

    def __getattr__(self, name: str):
        return getattr(self._require_inner(), name)
