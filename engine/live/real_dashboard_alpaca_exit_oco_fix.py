"""Correct Alpaca reserved exit-order request shape and fractional handling.

Alpaca OCO exit orders require the take-profit price inside
``take_profit.limit_price``.  Fractional stock orders must also use DAY and must
be SIMPLE orders, so fractional quantities cannot be submitted as OCO.

Dashboard policy:
- TP+SL together: submit Alpaca OCO only for the whole-share portion.
- Any fractional remainder is stored as a local watch; when TP/SL is touched, the
  real-dashboard direct sell function is called for that fractional remainder.
- TP only or SL only: submit a SIMPLE DAY order and allow fractional quantity.
"""
from __future__ import annotations

import math
import time
from typing import Any

from fastapi import HTTPException
from alpaca.trading.enums import OrderClass, OrderSide as AlpacaOrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, StopOrderRequest, TakeProfitRequest

from engine.live import real_dashboard_alpaca_exit_orders_patch as exit_orders

_INSTALLED = False
SHARE_EPS = 1e-6


def _has_fractional_qty(qty: float) -> bool:
    return abs(float(qty) - round(float(qty))) > SHARE_EPS


def _time_in_force_day_default(value: str = "day") -> TimeInForce:
    # Dashboard-created reserved exits are DAY orders.  This avoids Alpaca's
    # fractional-order TIF rejection and keeps the recorded state consistent with
    # the actual submitted order.
    return TimeInForce.DAY


def _oco_safe_shares(shares: float, *, take_profit: float | None, stop_loss: float | None) -> tuple[float, float, bool]:
    """Return (submitted_shares, unreserved_fractional, adjusted_for_oco)."""
    qty = round(float(shares), 6)
    if qty <= 0.0:
        raise ValueError("shares must be positive")
    if take_profit is not None and stop_loss is not None and _has_fractional_qty(qty):
        whole = int(math.floor(qty + SHARE_EPS))
        remainder = round(max(0.0, qty - float(whole)), 6)
        if whole <= 0:
            raise ValueError(
                "Alpaca fractional shares cannot use OCO. "
                "보유수량이 1주 미만이면 익절만 또는 손절만 simple DAY 예약을 사용하세요."
            )
        return float(whole), remainder, True
    return qty, 0.0, False


def _position_price(ticker: str) -> float | None:
    tk = str(ticker or "").upper().strip()
    try:
        for row in exit_orders.real_api._real_positions_payload() or []:
            if str(row.get("ticker") or "").upper().strip() == tk:
                return exit_orders._safe_float(row.get("current_price"), None)
    except Exception:
        pass
    try:
        broker = exit_orders.real_api._get_real_broker()
        if broker is not None:
            return exit_orders._safe_float(broker.get_current_price(tk), None)
    except Exception:
        pass
    return None


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
    qty, _, _ = _oco_safe_shares(shares, take_profit=take_profit, stop_loss=stop_loss)
    common = {
        "symbol": tk,
        "qty": qty,
        "side": AlpacaOrderSide.SELL,
        "time_in_force": TimeInForce.DAY,
        "client_order_id": client_order_id,
    }
    if take_profit is not None and stop_loss is not None:
        # Alpaca OCO requires the target price in take_profit.limit_price and
        # OCO cannot be fractional.  _oco_safe_shares already reduced qty to the
        # whole-share portion if necessary.
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


def _create_or_replace_exit_order_fixed(req: Any) -> dict[str, Any]:
    if not exit_orders.real_api._direct_orders_enabled():
        raise HTTPException(status_code=403, detail=f"direct order env is disabled; set {exit_orders.real_api.DIRECT_ORDER_ENV}=1")
    broker = exit_orders.real_api._get_real_broker()
    if broker is None:
        raise HTTPException(status_code=503, detail=exit_orders.real_api._real_broker_error or "Alpaca live broker unavailable")
    ticker = str(req.ticker or "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker required")
    try:
        position = exit_orders._held_position(ticker)
        held_shares = exit_orders._positive(position.get("shares"), "held shares")
        requested_shares = exit_orders._safe_float(req.shares, None)
        desired_shares = held_shares if requested_shares is None else min(float(requested_shares), held_shares)
        desired_shares = round(desired_shares, 6)
        if desired_shares <= 0:
            raise ValueError("shares must be positive")
        take_profit, stop_loss = exit_orders._validate_exit_prices(position, req.take_profit_price, req.stop_loss_price)
        order_shares, unreserved_fractional, fractional_oco_adjusted = _oco_safe_shares(
            desired_shares,
            take_profit=take_profit,
            stop_loss=stop_loss,
        )
        open_orders = exit_orders._open_orders_for_ticker(broker, ticker)
        km_exit_orders = [o for o in open_orders if exit_orders._is_kingmaker_exit_order(o)]
        non_km_sell_orders = [o for o in open_orders if exit_orders._is_sell_order(o) and not exit_orders._is_kingmaker_exit_order(o)]
        if non_km_sell_orders:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "existing_non_kingmaker_sell_orders",
                    "message": "Alpaca에 이미 다른 매도/예약 주문이 있습니다. 중복 청산 방지를 위해 먼저 확인/취소하세요.",
                    "orders": [exit_orders._order_summary(o) for o in non_km_sell_orders],
                },
            )
        cancellations: list[dict[str, Any]] = []
        if km_exit_orders:
            if not req.replace_existing:
                raise HTTPException(status_code=409, detail={"reason": "existing_kingmaker_exit_orders", "orders": [exit_orders._order_summary(o) for o in km_exit_orders]})
            cancellations = [exit_orders._cancel_order_obj(broker, o) for o in km_exit_orders]
            failed = [x for x in cancellations if not x.get("ok")]
            if failed:
                raise HTTPException(status_code=409, detail={"reason": "cancel_existing_failed", "cancellations": cancellations})
        client_order_id = f"{exit_orders.CLIENT_ID_PREFIX}-{int(time.time())}-{ticker}"
        order = _submit_exit_order_fixed(
            broker,
            ticker=ticker,
            shares=order_shares,
            take_profit=take_profit,
            stop_loss=stop_loss,
            tif=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        order_row = exit_orders._order_summary(order)
        order_kind = "oco" if take_profit is not None and stop_loss is not None else ("limit_sell" if take_profit is not None else "stop_sell")
        state = exit_orders._read_state()
        warning = ""
        fractional_watch = None
        if fractional_oco_adjusted:
            warning = f"Alpaca OCO는 소수점 수량을 허용하지 않아 {order_shares:g}주만 OCO 예약했고, {unreserved_fractional:g}주는 TP/SL 도달 시 kingmaker가 즉시 시장가 매도합니다."
            fractional_watch = {
                "status": "active",
                "ticker": ticker,
                "shares": unreserved_fractional,
                "take_profit_price": take_profit,
                "stop_loss_price": stop_loss,
                "created_at": exit_orders._now_iso(),
                "last_checked_at": "",
                "last_price": None,
                "trigger_kind": "",
                "triggered_at": "",
                "sell_intent": None,
                "note": "fractional OCO remainder; sell via real dashboard market order when TP/SL is touched",
            }
        state["orders"][ticker] = {
            "ticker": ticker,
            "status": "submitted",
            "mode": "alpaca_reserved_exit_order",
            "order_kind": order_kind,
            "take_profit_price": take_profit,
            "stop_loss_price": stop_loss,
            "shares": order_shares,
            "requested_shares": desired_shares,
            "unreserved_fractional_shares": unreserved_fractional,
            "fractional_oco_adjusted": fractional_oco_adjusted,
            "fractional_exit_watch": fractional_watch,
            "held_shares_at_submit": held_shares,
            "time_in_force": "day",
            "client_order_id": client_order_id,
            "order_id": str(getattr(order, "id", "") or ""),
            "submitted_at": exit_orders._now_iso(),
            "source": req.source,
            "position_snapshot": position,
            "cancellations": cancellations,
            "broker_order": order_row,
            "warning": warning,
        }
        exit_orders._write_state(state)
        return {
            "ok": True,
            "ticker": ticker,
            "order": order_row,
            "state": state["orders"][ticker],
            "cancellations": cancellations,
            "warning": warning,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


def evaluate_fractional_exit_watches() -> dict[str, Any]:
    """Sell fractional OCO remainders when their TP/SL line is touched."""
    if not exit_orders.real_api._direct_orders_enabled():
        return {"ok": False, "skipped": True, "reason": "direct_orders_disabled", "triggered": [], "evaluated": []}
    state = exit_orders._read_state()
    orders = state.get("orders") if isinstance(state.get("orders"), dict) else {}
    now = exit_orders._now_iso()
    evaluated: list[dict[str, Any]] = []
    triggered: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    changed = False
    for ticker, row in list(orders.items()):
        if not isinstance(row, dict):
            continue
        watch = row.get("fractional_exit_watch") if isinstance(row.get("fractional_exit_watch"), dict) else None
        if not watch or str(watch.get("status") or "").lower() != "active":
            continue
        tk = str(watch.get("ticker") or ticker or "").upper().strip()
        shares = exit_orders._safe_float(watch.get("shares"), 0.0) or 0.0
        if shares <= SHARE_EPS:
            watch["status"] = "ignored"
            watch["last_message"] = "shares_not_positive"
            changed = True
            continue
        price = _position_price(tk)
        watch["last_checked_at"] = now
        watch["last_price"] = price
        tp = exit_orders._safe_float(watch.get("take_profit_price"), None)
        sl = exit_orders._safe_float(watch.get("stop_loss_price"), None)
        hit = ""
        if price is not None:
            if tp is not None and price >= tp:
                hit = "take_profit"
            elif sl is not None and price <= sl:
                hit = "stop_loss"
        evaluated.append({"ticker": tk, "price": price, "shares": shares, "take_profit_price": tp, "stop_loss_price": sl, "hit": hit})
        if not hit:
            changed = True
            continue
        watch["status"] = "triggering"
        watch["trigger_kind"] = hit
        watch["triggered_at"] = now
        changed = True
        try:
            req = exit_orders.real_api.RealSellIntentRequest(
                ticker=tk,
                shares_requested=shares,
                source=f"alpaca_fractional_exit_watch_{hit}",
            )
            intent = exit_orders.real_api._create_real_sell_intent(req)
            watch["status"] = "submitted"
            watch["sell_intent"] = intent
            watch["last_message"] = f"fractional remainder sold by {hit} at {price}"
            row["fractional_exit_watch"] = watch
            row["fractional_exit_triggered_at"] = now
            row["fractional_exit_trigger_kind"] = hit
            row["fractional_exit_price"] = price
            triggered.append({"ticker": tk, "shares": shares, "price": price, "trigger_kind": hit, "sell_intent": intent})
        except Exception as exc:
            watch["status"] = "error"
            watch["last_message"] = f"{type(exc).__name__}: {exc}"
            errors.append({"ticker": tk, "shares": shares, "price": price, "trigger_kind": hit, "error": watch["last_message"]})
        row["fractional_exit_watch"] = watch
        orders[ticker] = row
    if changed:
        state["orders"] = orders
        state["last_fractional_evaluation"] = {"time": now, "evaluated_count": len(evaluated), "triggered_count": len(triggered), "errors": errors[-20:]}
        exit_orders._write_state(state)
    return {"ok": True, "time": now, "evaluated": evaluated, "triggered": triggered, "errors": errors, "state_updated": changed}


def install_real_dashboard_alpaca_exit_oco_fix(app: Any | None = None) -> None:
    """Patch the reserved-exit order submitter once per API process."""
    global _INSTALLED
    if _INSTALLED:
        return
    exit_orders._submit_exit_order = _submit_exit_order_fixed
    exit_orders._time_in_force = _time_in_force_day_default
    exit_orders._create_or_replace_exit_order = _create_or_replace_exit_order_fixed
    if app is not None:
        @app.post("/api/real/alpaca_exit_orders/evaluate_fractional")
        def real_alpaca_exit_orders_evaluate_fractional():
            return evaluate_fractional_exit_watches()
    _INSTALLED = True
