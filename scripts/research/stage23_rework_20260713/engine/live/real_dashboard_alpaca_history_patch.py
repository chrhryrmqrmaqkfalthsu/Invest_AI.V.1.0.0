"""Alpaca live order/trade history routes for the real dashboard.

The existing real dashboard trade history endpoint reads only the local
real_dashboard_trades_history.json file.  If the operator closes a position from
Alpaca's app/web UI, that local file is not updated.  These routes read filled
orders directly from Alpaca and expose them in the same dashboard shape.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest
from fastapi import Query

from engine.live import real_dashboard_api as real_api


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "")
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower().strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _iso(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    return str(value)


def _alpaca_order_rows(*, symbol: str = "", limit: int = 100, days: int = 30) -> list[dict[str, Any]]:
    broker = real_api._get_real_broker()
    if broker is None:
        raise RuntimeError(f"real broker unavailable: {getattr(real_api, '_real_broker_error', '')}")
    tk = str(symbol or "").upper().strip()
    lim = max(1, min(int(limit or 100), 500))
    after = datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days or 30), 365)))
    req = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        limit=lim,
        after=after,
        symbols=[tk] if tk else None,
    )
    orders = broker.trading.get_orders(filter=req)
    rows: list[dict[str, Any]] = []
    for order in orders or []:
        filled_qty = _float(getattr(order, "filled_qty", None), 0.0)
        qty = _float(getattr(order, "qty", None), 0.0)
        filled_avg_price = _float(getattr(order, "filled_avg_price", None), 0.0)
        side = _enum_value(getattr(order, "side", ""))
        status = _enum_value(getattr(order, "status", ""))
        rows.append({
            "id": str(getattr(order, "id", "") or ""),
            "order_id": str(getattr(order, "id", "") or ""),
            "client_order_id": str(getattr(order, "client_order_id", "") or ""),
            "ticker": str(getattr(order, "symbol", "") or "").upper(),
            "symbol": str(getattr(order, "symbol", "") or "").upper(),
            "side": side,
            "type": _enum_value(getattr(order, "type", "")),
            "status": status,
            "qty": qty,
            "shares": filled_qty or qty,
            "filled_qty": filled_qty,
            "filled_avg_price": filled_avg_price,
            "notional": _float(getattr(order, "notional", None), 0.0) or None,
            "filled_notional": round((filled_qty or qty) * filled_avg_price, 6) if filled_avg_price and (filled_qty or qty) else 0.0,
            "submitted_at": _iso(getattr(order, "submitted_at", None)),
            "filled_at": _iso(getattr(order, "filled_at", None)),
            "created_at": _iso(getattr(order, "created_at", None)),
            "updated_at": _iso(getattr(order, "updated_at", None)),
            "source": "alpaca_live_orders",
            "is_filled": status == "filled" and (filled_qty or qty) > 0 and filled_avg_price > 0,
        })
    rows.sort(key=lambda x: x.get("filled_at") or x.get("submitted_at") or "", reverse=True)
    return rows


def _closed_trades_from_orders(order_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filled = [r for r in order_rows if r.get("is_filled")]
    filled.sort(key=lambda x: x.get("filled_at") or x.get("submitted_at") or "")
    lots: dict[str, list[dict[str, Any]]] = {}
    trades: list[dict[str, Any]] = []
    for order in filled:
        tk = str(order.get("ticker") or "").upper().strip()
        side = str(order.get("side") or "").lower()
        qty = _float(order.get("filled_qty") or order.get("shares"), 0.0)
        price = _float(order.get("filled_avg_price"), 0.0)
        if not tk or qty <= 0 or price <= 0:
            continue
        if side == "buy":
            lots.setdefault(tk, []).append({"qty": qty, "price": price, "time": order.get("filled_at") or order.get("submitted_at"), "order_id": order.get("order_id")})
            continue
        if side != "sell":
            continue
        remaining = qty
        consumed_qty = 0.0
        cost = 0.0
        entry_times: list[str] = []
        ticker_lots = lots.setdefault(tk, [])
        while remaining > 1e-9 and ticker_lots:
            lot = ticker_lots[0]
            take = min(remaining, _float(lot.get("qty"), 0.0))
            if take <= 0:
                ticker_lots.pop(0)
                continue
            consumed_qty += take
            cost += take * _float(lot.get("price"), 0.0)
            if lot.get("time"):
                entry_times.append(str(lot.get("time")))
            lot["qty"] = _float(lot.get("qty"), 0.0) - take
            remaining -= take
            if _float(lot.get("qty"), 0.0) <= 1e-9:
                ticker_lots.pop(0)
        if consumed_qty <= 0:
            # External/opening position not present in the lookback.  Still show
            # the sell fill, but mark entry stats unknown.
            trades.append({
                "ticker": tk,
                "direction": "long",
                "entry_date": "",
                "entry_price": None,
                "exit_price": price,
                "shares": qty,
                "exited_at": order.get("filled_at") or order.get("submitted_at"),
                "exit_reason": "alpaca_app_or_external_sell",
                "holding_days": None,
                "pnl_pct": None,
                "pnl_usd": None,
                "pnl_krw": 0,
                "exit_strategy": "external_alpaca",
                "source": "alpaca_live_orders_unmatched_sell",
                "sell_order_id": order.get("order_id"),
            })
            continue
        avg_entry = cost / consumed_qty if consumed_qty else 0.0
        exit_value = consumed_qty * price
        pnl_usd = exit_value - cost
        pnl_pct = (price / avg_entry - 1.0) * 100.0 if avg_entry > 0 else 0.0
        entry_time = min(entry_times) if entry_times else ""
        holding_days = None
        try:
            if entry_time and order.get("filled_at"):
                entry_dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
                exit_dt = datetime.fromisoformat(str(order.get("filled_at")).replace("Z", "+00:00"))
                holding_days = round(max(0.0, (exit_dt - entry_dt).total_seconds() / 86400.0), 4)
        except Exception:
            holding_days = None
        trades.append({
            "ticker": tk,
            "direction": "long",
            "entry_date": entry_time,
            "entry_price": round(avg_entry, 6),
            "exit_price": round(price, 6),
            "shares": round(consumed_qty, 6),
            "exited_at": order.get("filled_at") or order.get("submitted_at"),
            "exit_reason": "alpaca_app_or_external_sell" if not str(order.get("client_order_id") or "").startswith("km-") else "dashboard_sell",
            "holding_days": holding_days,
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 6),
            "pnl_krw": round(pnl_usd, 2),
            "exit_strategy": "external_alpaca",
            "source": "alpaca_live_orders_matched_fifo",
            "buy_source": "alpaca_live_orders",
            "sell_order_id": order.get("order_id"),
            "sell_client_order_id": order.get("client_order_id"),
        })
    trades.sort(key=lambda x: x.get("exited_at") or "", reverse=True)
    return trades


def _stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [t for t in trades if t.get("pnl_pct") is not None]
    wins = [t for t in vals if _float(t.get("pnl_pct"), 0.0) > 0]
    losses = [t for t in vals if _float(t.get("pnl_pct"), 0.0) < 0]
    n = len(vals)
    total_pnl = sum(_float(t.get("pnl_usd"), _float(t.get("pnl_krw"), 0.0)) for t in vals)
    avg_win = sum(_float(t.get("pnl_pct"), 0.0) for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(_float(t.get("pnl_pct"), 0.0) for t in losses) / len(losses) if losses else 0.0
    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round((len(wins) / n * 100.0) if n else 0.0, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_usd": round(total_pnl, 6),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
    }


def _remove_route(app: Any, path: str, method: str = "GET") -> None:
    method_u = method.upper()
    try:
        app.router.routes = [
            route for route in app.router.routes
            if not (getattr(route, "path", "") == path and method_u in set(getattr(route, "methods", set()) or set()))
        ]
    except Exception:
        pass


def install_real_dashboard_alpaca_history_routes(app: Any) -> None:
    """Install Alpaca-backed real trade/order history endpoints."""
    _remove_route(app, "/api/real/trades_history", "GET")

    @app.get("/api/real/alpaca_orders")
    def real_alpaca_orders(
        symbol: str = "",
        limit: int = Query(default=100, ge=1, le=500),
        days: int = Query(default=30, ge=1, le=365),
    ):
        rows = _alpaca_order_rows(symbol=symbol, limit=limit, days=days)
        return {
            "ok": True,
            "orders": rows,
            "count": len(rows),
            "symbol": str(symbol or "").upper().strip(),
            "source": "alpaca_live_orders",
            "account_source": "alpaca_live",
        }

    @app.get("/api/real/trades_history")
    def real_trades_history_with_alpaca(
        symbol: str = "",
        limit: int = Query(default=100, ge=1, le=500),
        days: int = Query(default=30, ge=1, le=365),
    ):
        local = real_api._real_trades_history()
        local_trades = list((local or {}).get("trades") or []) if isinstance(local, dict) else []
        orders = _alpaca_order_rows(symbol=symbol, limit=limit, days=days)
        broker_trades = _closed_trades_from_orders(orders)
        trades = broker_trades + local_trades
        seen = set()
        deduped = []
        for trade in trades:
            key = (trade.get("ticker"), trade.get("exited_at"), trade.get("sell_order_id") or trade.get("exit_price"), trade.get("shares"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(trade)
        deduped.sort(key=lambda x: x.get("exited_at") or "", reverse=True)
        return {
            "stats": _stats(deduped),
            "trades": deduped,
            "orders": orders,
            "orders_count": len(orders),
            "alpaca_trades_count": len(broker_trades),
            "local_trades_count": len(local_trades),
            "account_source": "alpaca_live",
            "isolated": True,
            "source": "alpaca_live_orders_plus_local_real_dashboard_trades",
            "note": "Alpaca live filled orders are fetched directly, so sells made in the Alpaca app are reflected here.",
        }
