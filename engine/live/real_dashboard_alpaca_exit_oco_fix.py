"""Correct Alpaca OCO exit-order request shape.

Alpaca OCO exit orders require the take-profit price inside
``take_profit.limit_price``.  The initial reserved-exit implementation put the
limit price on the parent order, which Alpaca rejects with code 40010001.
"""
from __future__ import annotations

from typing import Any

from alpaca.trading.enums import OrderClass, OrderSide as AlpacaOrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, StopOrderRequest, TakeProfitRequest

from engine.live import real_dashboard_alpaca_exit_orders_patch as exit_orders

_INSTALLED = False


def _submit_exit_order_fixed(
    broker: Any,
    *,
    ticker: str,
    shares: float,
    take_profit: float | None,
    stop_loss: float | None,
    tif: TimeInForce,
    client_order_id: str,
) -> Any:
    tk = str(ticker or "").upper().strip()
    qty = round(float(shares), 6)
    if qty <= 0.0:
        raise ValueError("shares must be positive")
    common = {
        "symbol": tk,
        "qty": qty,
        "side": AlpacaOrderSide.SELL,
        "time_in_force": tif,
        "client_order_id": client_order_id,
    }
    if take_profit is not None and stop_loss is not None:
        # Alpaca OCO requires the target price in take_profit.limit_price.
        # Putting it only on the parent limit_price is rejected with:
        # {"code":40010001,"message":"oco orders require take_profit.limit_price"}
        req = LimitOrderRequest(
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=float(take_profit)),
            stop_loss=StopLossRequest(stop_price=float(stop_loss)),
            **common,
        )
    elif take_profit is not None:
        req = LimitOrderRequest(limit_price=float(take_profit), **common)
    else:
        req = StopOrderRequest(stop_price=float(stop_loss), **common)
    return broker.trading.submit_order(order_data=req)


def install_real_dashboard_alpaca_exit_oco_fix() -> None:
    """Patch the reserved-exit order submitter once per API process."""
    global _INSTALLED
    if _INSTALLED:
        return
    exit_orders._submit_exit_order = _submit_exit_order_fixed
    _INSTALLED = True
