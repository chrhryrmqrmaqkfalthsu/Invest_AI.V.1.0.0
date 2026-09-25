"""Alpaca reserved exit-order routes and UI patch for dashboard-real.

This patch turns the real holding chart's TP/SL values into explicit Alpaca
reserved exit orders, but keeps the existing local Save button as display-only.
Actual live orders are submitted only through the separate "Alpaca 예약 적용"
button and only when KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from alpaca.trading.enums import OrderClass, OrderSide as AlpacaOrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, StopLossRequest, StopOrderRequest

from engine.live import real_dashboard_api as real_api

STATE_PATH = Path("data/_system/real_dashboard_alpaca_exit_orders.json")
CLIENT_ID_PREFIX = "km-real-exit"
_INSTALLED = False
_ORIG_SLOT_OVERLAY_JS = None


class RealAlpacaExitOrderRequest(BaseModel):
    ticker: str
    take_profit_price: float | None = None
    stop_loss_price: float | None = None
    shares: float | None = None
    replace_existing: bool = True
    time_in_force: str = "gtc"
    source: str = "dashboard_real_exit_panel"


def _now_iso() -> str:
    try:
        return real_api.utc_now_iso()
    except Exception:
        import datetime as _dt

        return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _safe_float(value: Any, default: float | None = None) -> float | None:
    return real_api._safe_float(value, default)


def _positive(value: Any, name: str) -> float:
    out = _safe_float(value)
    if out is None or out <= 0.0:
        raise ValueError(f"{name} must be positive")
    return float(out)


def _read_state() -> dict[str, Any]:
    data = real_api.read_json(STATE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("updated_at", "")
    if not isinstance(data.get("orders"), dict):
        data["orders"] = {}
    return data


def _write_state(data: dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    real_api.atomic_write_json(STATE_PATH, data)


def _order_obj_to_dict(order: Any) -> dict[str, Any]:
    if order is None:
        return {}
    attrs = [
        "id",
        "client_order_id",
        "symbol",
        "side",
        "type",
        "order_class",
        "qty",
        "filled_qty",
        "limit_price",
        "stop_price",
        "status",
        "time_in_force",
        "submitted_at",
        "filled_at",
        "canceled_at",
        "expired_at",
        "replaced_by",
        "replaces",
    ]
    out: dict[str, Any] = {}
    for key in attrs:
        val = getattr(order, key, None)
        if val is not None:
            out[key] = str(getattr(val, "value", val))
    legs = getattr(order, "legs", None)
    if legs:
        out["legs"] = [_order_obj_to_dict(leg) for leg in list(legs or [])]
    return out


def _order_summary(order: Any) -> dict[str, Any]:
    return _order_obj_to_dict(order)


def _held_position(ticker: str) -> dict[str, Any]:
    tk = str(ticker or "").upper().strip()
    if not tk:
        raise ValueError("ticker required")
    rows = real_api._real_positions_payload()
    for row in rows or []:
        if str(row.get("ticker") or "").upper().strip() == tk:
            shares = _safe_float(row.get("shares"), 0.0) or 0.0
            if shares > 0:
                return row
    raise ValueError(f"{tk} is not held in Alpaca live positions")


def _open_orders_for_ticker(broker: Any, ticker: str) -> list[Any]:
    tk = str(ticker or "").upper().strip()
    try:
        rows = broker.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[tk], nested=True))
    except TypeError:
        rows = broker.trading.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[tk]))
    except Exception as exc:
        raise RuntimeError(f"open orders lookup failed: {exc}") from exc
    return list(rows or [])


def _is_kingmaker_exit_order(order: Any) -> bool:
    cid = str(getattr(order, "client_order_id", "") or "")
    if cid.startswith(CLIENT_ID_PREFIX):
        return True
    for leg in list(getattr(order, "legs", None) or []):
        if str(getattr(leg, "client_order_id", "") or "").startswith(CLIENT_ID_PREFIX):
            return True
    return False


def _is_sell_order(order: Any) -> bool:
    side = str(getattr(getattr(order, "side", ""), "value", getattr(order, "side", "")) or "").lower()
    if side == "sell":
        return True
    return any(_is_sell_order(leg) for leg in list(getattr(order, "legs", None) or []))


def _cancel_order_obj(broker: Any, order: Any) -> dict[str, Any]:
    oid = str(getattr(order, "id", "") or "")
    if not oid:
        return {"ok": False, "reason": "missing_order_id", "order": _order_summary(order)}
    try:
        broker.trading.cancel_order_by_id(oid)
        return {"ok": True, "order_id": oid, "client_order_id": str(getattr(order, "client_order_id", "") or "")}
    except Exception as exc:
        return {"ok": False, "order_id": oid, "reason": type(exc).__name__, "message": str(exc), "order": _order_summary(order)}


def _round_price(value: float) -> float:
    # US equity prices above $1 generally require 2 decimal places.  Keep this
    # conservative; Alpaca rejects invalid increments rather than silently fixing.
    return round(float(value), 2)


def _time_in_force(value: str) -> TimeInForce:
    raw = str(value or "gtc").strip().lower()
    if raw == "day":
        return TimeInForce.DAY
    return TimeInForce.GTC


def _validate_exit_prices(position: dict[str, Any], take_profit: float | None, stop_loss: float | None) -> tuple[float | None, float | None]:
    current = _safe_float(position.get("current_price"), None)
    entry = _safe_float(position.get("entry_price"), current)
    tp = _round_price(take_profit) if take_profit is not None else None
    sl = _round_price(stop_loss) if stop_loss is not None else None
    if tp is None and sl is None:
        raise ValueError("take_profit_price or stop_loss_price is required")
    ref = current or entry
    if tp is not None and ref is not None and tp <= float(ref):
        raise ValueError(f"take_profit_price must be above current price ({ref:.4f}) to avoid immediate/marketable sell")
    if sl is not None and ref is not None and sl >= float(ref):
        raise ValueError(f"stop_loss_price must be below current price ({ref:.4f})")
    if tp is not None and sl is not None and tp <= sl:
        raise ValueError("take_profit_price must be greater than stop_loss_price")
    return tp, sl


def _submit_exit_order(broker: Any, *, ticker: str, shares: float, take_profit: float | None, stop_loss: float | None, tif: TimeInForce, client_order_id: str) -> Any:
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
        req = LimitOrderRequest(
            limit_price=float(take_profit),
            order_class=OrderClass.OCO,
            stop_loss=StopLossRequest(stop_price=float(stop_loss)),
            **common,
        )
    elif take_profit is not None:
        req = LimitOrderRequest(limit_price=float(take_profit), **common)
    else:
        req = StopOrderRequest(stop_price=float(stop_loss), **common)
    return broker.trading.submit_order(order_data=req)


def _create_or_replace_exit_order(req: RealAlpacaExitOrderRequest) -> dict[str, Any]:
    if not real_api._direct_orders_enabled():
        raise HTTPException(status_code=403, detail=f"direct order env is disabled; set {real_api.DIRECT_ORDER_ENV}=1")
    broker = real_api._get_real_broker()
    if broker is None:
        raise HTTPException(status_code=503, detail=real_api._real_broker_error or "Alpaca live broker unavailable")
    ticker = str(req.ticker or "").upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker required")
    try:
        position = _held_position(ticker)
        held_shares = _positive(position.get("shares"), "held shares")
        requested_shares = _safe_float(req.shares, None)
        shares = held_shares if requested_shares is None else min(float(requested_shares), held_shares)
        shares = round(shares, 6)
        if shares <= 0:
            raise ValueError("shares must be positive")
        take_profit, stop_loss = _validate_exit_prices(position, req.take_profit_price, req.stop_loss_price)
        open_orders = _open_orders_for_ticker(broker, ticker)
        km_exit_orders = [o for o in open_orders if _is_kingmaker_exit_order(o)]
        non_km_sell_orders = [o for o in open_orders if _is_sell_order(o) and not _is_kingmaker_exit_order(o)]
        if non_km_sell_orders:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "existing_non_kingmaker_sell_orders",
                    "message": "Alpaca에 이미 다른 매도/예약 주문이 있습니다. 중복 청산 방지를 위해 먼저 확인/취소하세요.",
                    "orders": [_order_summary(o) for o in non_km_sell_orders],
                },
            )
        cancellations: list[dict[str, Any]] = []
        if km_exit_orders:
            if not req.replace_existing:
                raise HTTPException(status_code=409, detail={"reason": "existing_kingmaker_exit_orders", "orders": [_order_summary(o) for o in km_exit_orders]})
            cancellations = [_cancel_order_obj(broker, o) for o in km_exit_orders]
            failed = [x for x in cancellations if not x.get("ok")]
            if failed:
                raise HTTPException(status_code=409, detail={"reason": "cancel_existing_failed", "cancellations": cancellations})
        client_order_id = f"{CLIENT_ID_PREFIX}-{int(time.time())}-{ticker}"
        order = _submit_exit_order(
            broker,
            ticker=ticker,
            shares=shares,
            take_profit=take_profit,
            stop_loss=stop_loss,
            tif=_time_in_force(req.time_in_force),
            client_order_id=client_order_id,
        )
        order_row = _order_summary(order)
        state = _read_state()
        state["orders"][ticker] = {
            "ticker": ticker,
            "status": "submitted",
            "mode": "alpaca_reserved_exit_order",
            "order_kind": "oco" if take_profit is not None and stop_loss is not None else ("limit_sell" if take_profit is not None else "stop_sell"),
            "take_profit_price": take_profit,
            "stop_loss_price": stop_loss,
            "shares": shares,
            "held_shares_at_submit": held_shares,
            "time_in_force": str(getattr(_time_in_force(req.time_in_force), "value", _time_in_force(req.time_in_force))),
            "client_order_id": client_order_id,
            "order_id": str(getattr(order, "id", "") or ""),
            "submitted_at": _now_iso(),
            "source": req.source,
            "position_snapshot": position,
            "cancellations": cancellations,
            "broker_order": order_row,
        }
        _write_state(state)
        return {"ok": True, "ticker": ticker, "order": order_row, "state": state["orders"][ticker], "cancellations": cancellations}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


def _status_payload(ticker: str = "") -> dict[str, Any]:
    broker = real_api._get_real_broker()
    state = _read_state()
    tk_filter = str(ticker or "").upper().strip()
    open_rows: list[dict[str, Any]] = []
    if broker is not None:
        symbols = [tk_filter] if tk_filter else None
        try:
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=symbols, nested=True)
            rows = broker.trading.get_orders(filter=req)
            for order in rows or []:
                if not symbols or str(getattr(order, "symbol", "") or "").upper() == tk_filter:
                    if _is_sell_order(order) or _is_kingmaker_exit_order(order):
                        open_rows.append(_order_summary(order))
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "state": state, "open_orders": []}
    orders_state = state.get("orders") or {}
    if tk_filter:
        orders_state = {tk_filter: orders_state.get(tk_filter)} if orders_state.get(tk_filter) else {}
    return {
        "ok": True,
        "direct_orders_enabled": real_api._direct_orders_enabled(),
        "state": {"orders": orders_state, "updated_at": state.get("updated_at", "")},
        "open_orders": open_rows,
    }


def _cancel_exit_orders(ticker: str) -> dict[str, Any]:
    if not real_api._direct_orders_enabled():
        raise HTTPException(status_code=403, detail=f"direct order env is disabled; set {real_api.DIRECT_ORDER_ENV}=1")
    broker = real_api._get_real_broker()
    if broker is None:
        raise HTTPException(status_code=503, detail=real_api._real_broker_error or "Alpaca live broker unavailable")
    tk = str(ticker or "").upper().strip()
    if not tk:
        raise HTTPException(status_code=400, detail="ticker required")
    try:
        rows = _open_orders_for_ticker(broker, tk)
        targets = [o for o in rows if _is_kingmaker_exit_order(o)]
        cancellations = [_cancel_order_obj(broker, o) for o in targets]
        state = _read_state()
        entry = dict((state.get("orders") or {}).get(tk) or {})
        entry.update({"ticker": tk, "status": "cancel_requested", "cancel_requested_at": _now_iso(), "cancellations": cancellations})
        state["orders"][tk] = entry
        _write_state(state)
        return {"ok": True, "ticker": tk, "cancellations": cancellations, "state": entry}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


def _patch_slot_overlay_js() -> None:
    global _ORIG_SLOT_OVERLAY_JS
    if _ORIG_SLOT_OVERLAY_JS is not None:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    injection = r'''
(function(){
  function kmExitNum(v){ if(v==null||v==='') return null; const n=Number(v); return Number.isFinite(n)?n:null; }
  function kmExitMoney(v){ const n=kmExitNum(v); return n==null?'—':'$'+n.toFixed(2); }
  function kmExitTickerFromActive(){
    try{ return String((window._activeRealHolding&&window._activeRealHolding.ticker) || (_activeChart&&_activeChart.ticker) || '').toUpperCase(); }catch(e){ return ''; }
  }
  function kmExitPanelHolding(ticker){
    const tk=String(ticker||'').toUpperCase();
    try{ return (window.slotData||slotData||[]).find(x=>x && !x.empty && String(x.ticker||'').toUpperCase()===tk) || window._activeRealHolding || null; }catch(e){ return window._activeRealHolding || null; }
  }
  function kmExitPlanFromPanel(s){
    try{ syncPreviewExitInputs(s, 'save'); }catch(e){}
    let plan=null;
    try{ plan=previewPlanDefaults(s); }catch(e){ plan=(s&&s._previewExitPlan)||{}; }
    const takeOn=!!(plan&&plan.take_enabled);
    const stopOn=!!(plan&&plan.stop_enabled);
    return {
      ticker:String((s&&s.ticker)||kmExitTickerFromActive()).toUpperCase(),
      take_profit_price: takeOn ? kmExitNum(plan&&plan.take_profit_price) : null,
      stop_loss_price: stopOn ? kmExitNum(plan&&plan.stop_loss_price) : null,
      shares: kmExitNum(s&&s.shares),
      plan
    };
  }
  function kmSetAlpacaExitStatus(msg, kind){
    const el=document.getElementById('alpaca-exit-order-state') || document.getElementById('preview-exit-save-state');
    if(!el) return;
    el.textContent=msg;
    el.style.color=kind==='bad'?'#fecdd3':(kind==='good'?'#86efac':'#fbbf24');
  }
  function kmEnsureAlpacaExitButtons(){
    const panel=document.getElementById('preview-exit-panel');
    if(!panel || document.getElementById('alpaca-exit-apply')) return;
    const saveRow=document.getElementById('preview-exit-save');
    const btnWrap=saveRow && saveRow.parentElement;
    if(!btnWrap) return;
    const apply=document.createElement('button');
    apply.id='alpaca-exit-apply';
    apply.textContent='Alpaca 예약 적용';
    apply.style.cssText='background:#16a34a;color:white;border:0;border-radius:12px;padding:10px 15px;font-weight:950;cursor:pointer;';
    const cancel=document.createElement('button');
    cancel.id='alpaca-exit-cancel';
    cancel.textContent='예약 취소';
    cancel.style.cssText='background:#7f1d1d;color:#fecaca;border:0;border-radius:12px;padding:10px 15px;font-weight:950;cursor:pointer;';
    btnWrap.insertBefore(apply, btnWrap.firstChild);
    btnWrap.appendChild(cancel);
    let state=document.getElementById('alpaca-exit-order-state');
    if(!state){
      state=document.createElement('div');
      state.id='alpaca-exit-order-state';
      state.style.cssText='width:100%;font-size:11px;color:#fbbf24;font-weight:900;margin-top:8px;line-height:1.45;';
      panel.appendChild(state);
    }
    const note=panel.querySelector('.km-alpaca-exit-note');
    if(!note){
      const n=document.createElement('div');
      n.className='km-alpaca-exit-note';
      n.style.cssText='font-size:11px;color:#fbbf24;margin-top:8px;line-height:1.6;font-weight:800;';
      n.textContent='Alpaca 예약 적용은 실제 live OCO/limit/stop 주문을 제출합니다. 저장 버튼은 화면 참고선만 저장합니다.';
      panel.appendChild(n);
    }
    apply.onclick=(ev)=>{ ev.stopPropagation(); kmApplyAlpacaExitOrder(); };
    cancel.onclick=(ev)=>{ ev.stopPropagation(); kmCancelAlpacaExitOrder(); };
    kmLoadAlpacaExitStatus();
  }
  async function kmLoadAlpacaExitStatus(){
    const ticker=kmExitTickerFromActive();
    if(!ticker) return;
    try{
      const r=await fetch(`${API}/api/real/alpaca_exit_orders?ticker=${encodeURIComponent(ticker)}`, {cache:'no-store'});
      const d=await r.json();
      const n=(d.open_orders||[]).length;
      if(n>0) kmSetAlpacaExitStatus(`Alpaca 예약 주문 ${n}건 열려 있음`, 'good');
      else kmSetAlpacaExitStatus('Alpaca 예약 주문 없음 · 저장값은 아직 참고선', 'warn');
    }catch(e){}
  }
  async function kmApplyAlpacaExitOrder(){
    const ticker=kmExitTickerFromActive();
    const s=kmExitPanelHolding(ticker);
    if(!s){ kmSetAlpacaExitStatus('보유 포지션을 찾지 못함', 'bad'); return; }
    const p=kmExitPlanFromPanel(s);
    if(!p.take_profit_price && !p.stop_loss_price){ kmSetAlpacaExitStatus('익절 또는 손절 가격 필요', 'bad'); return; }
    const cur=kmExitNum(s.current_price);
    if(p.take_profit_price!=null && cur!=null && p.take_profit_price<=cur){ kmSetAlpacaExitStatus(`익절가는 현재가 ${kmExitMoney(cur)}보다 높아야 함`, 'bad'); return; }
    if(p.stop_loss_price!=null && cur!=null && p.stop_loss_price>=cur){ kmSetAlpacaExitStatus(`손절가는 현재가 ${kmExitMoney(cur)}보다 낮아야 함`, 'bad'); return; }
    const kind=(p.take_profit_price&&p.stop_loss_price)?'OCO':(p.take_profit_price?'지정가 매도':'스탑 매도');
    const msg=`${ticker} ${kind} 예약 주문을 Alpaca LIVE에 제출할까요?\n\n수량: ${Number(p.shares||0).toFixed(6)}주\n익절: ${kmExitMoney(p.take_profit_price)}\n손절: ${kmExitMoney(p.stop_loss_price)}\n\n기존 kingmaker 예약 주문은 취소 후 새로 겁니다.`;
    if(!confirm(msg)) return;
    kmSetAlpacaExitStatus('Alpaca 예약 주문 제출 중…', 'warn');
    try{
      const r=await fetch(`${API}/api/real/alpaca_exit_order`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ticker, take_profit_price:p.take_profit_price, stop_loss_price:p.stop_loss_price, shares:p.shares, replace_existing:true, time_in_force:'gtc', source:'dashboard_real_exit_panel'})});
      const d=await r.json().catch(()=>({}));
      if(!r.ok || d.ok===false) throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));
      const st=d.state||{};
      kmSetAlpacaExitStatus(`Alpaca ${st.order_kind||'예약'} 제출 완료 · ${st.client_order_id||''}`, 'good');
      if(typeof toast==='function') toast('Alpaca 예약 주문 완료', `${ticker} · ${st.order_kind||kind} · TP ${kmExitMoney(p.take_profit_price)} · SL ${kmExitMoney(p.stop_loss_price)}`, 'good');
      kmLoadAlpacaExitStatus();
    }catch(e){
      kmSetAlpacaExitStatus(`예약 실패: ${String(e.message||e)}`, 'bad');
      if(typeof toast==='function') toast('Alpaca 예약 실패', String(e.message||e), 'warn');
    }
  }
  async function kmCancelAlpacaExitOrder(){
    const ticker=kmExitTickerFromActive();
    if(!ticker) return;
    if(!confirm(`${ticker} kingmaker Alpaca 예약 주문을 취소할까요?`)) return;
    kmSetAlpacaExitStatus('Alpaca 예약 취소 요청 중…', 'warn');
    try{
      const r=await fetch(`${API}/api/real/alpaca_exit_order/${encodeURIComponent(ticker)}`, {method:'DELETE'});
      const d=await r.json().catch(()=>({}));
      if(!r.ok || d.ok===false) throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));
      const ok=(d.cancellations||[]).filter(x=>x.ok).length;
      kmSetAlpacaExitStatus(`예약 취소 요청 완료 · ${ok}건`, 'good');
      if(typeof toast==='function') toast('Alpaca 예약 취소', `${ticker} · ${ok}건`, 'good');
    }catch(e){
      kmSetAlpacaExitStatus(`취소 실패: ${String(e.message||e)}`, 'bad');
      if(typeof toast==='function') toast('Alpaca 예약 취소 실패', String(e.message||e), 'warn');
    }
  }
  const _kmAlpacaExitOldBind=window.bindPreviewExitControls || (typeof bindPreviewExitControls==='function'?bindPreviewExitControls:null);
  if(_kmAlpacaExitOldBind && !_kmAlpacaExitOldBind.__kmAlpacaExitPatched){
    const wrapped=function(s){ const out=_kmAlpacaExitOldBind.apply(this, arguments); try{ setTimeout(kmEnsureAlpacaExitButtons, 30); }catch(e){} return out; };
    wrapped.__kmAlpacaExitPatched=true;
    window.bindPreviewExitControls=wrapped;
    try{ bindPreviewExitControls=wrapped; }catch(e){}
  }
  const _kmAlpacaExitOldRender=window.renderRealHoldingLiveEnhancements || (typeof renderRealHoldingLiveEnhancements==='function'?renderRealHoldingLiveEnhancements:null);
  if(_kmAlpacaExitOldRender && !_kmAlpacaExitOldRender.__kmAlpacaExitPatched){
    const wrapped=function(s){ const out=_kmAlpacaExitOldRender.apply(this, arguments); try{ setTimeout(kmEnsureAlpacaExitButtons, 60); }catch(e){} return out; };
    wrapped.__kmAlpacaExitPatched=true;
    window.renderRealHoldingLiveEnhancements=wrapped;
    try{ renderRealHoldingLiveEnhancements=wrapped; }catch(e){}
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', ()=>setTimeout(kmEnsureAlpacaExitButtons, 500), {once:true});
  else setTimeout(kmEnsureAlpacaExitButtons, 500);
})();
'''

    def patched_real_slot_overlay_js() -> str:
        js = _ORIG_SLOT_OVERLAY_JS()
        if "kmApplyAlpacaExitOrder" in js:
            return js
        return js + "\n" + injection

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js


def install_real_dashboard_alpaca_exit_order_routes(app: Any) -> None:
    """Install real-dashboard Alpaca reserved exit order routes and UI patch."""
    global _INSTALLED
    if _INSTALLED:
        return

    @app.get("/api/real/alpaca_exit_orders")
    def real_alpaca_exit_orders(ticker: str = ""):
        return _status_payload(ticker=ticker)

    @app.post("/api/real/alpaca_exit_order")
    def real_alpaca_exit_order(req: RealAlpacaExitOrderRequest):
        return _create_or_replace_exit_order(req)

    @app.delete("/api/real/alpaca_exit_order/{ticker}")
    def real_alpaca_exit_order_cancel(ticker: str):
        return _cancel_exit_orders(ticker)

    _patch_slot_overlay_js()
    _INSTALLED = True
