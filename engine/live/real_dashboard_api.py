"""Separate real-trading dashboard routes.

This module installs a second dashboard that reuses the main KINGMAKER UI but
routes account/position/order-related calls to /api/real/* instead of /api/live/*.

Safety design:
- The existing paper/live dashboard state files are not used for real order intents.
- Real dashboard BUY/SELL requests are stored in separate real_dashboard_* intent files.
- Direct broker order submission is disabled unless explicitly enabled by env var
  KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from engine.live.broker.base import BrokerError, OrderType
from engine.live.manual_buy_intent import atomic_write_json, read_json, utc_now_iso

ALPACA_LIVE_BASE_URL = "https://api.alpaca.markets"
REAL_BUY_INTENT_PATH = Path("data/_system/real_dashboard_manual_buy_intent.json")
REAL_SELL_INTENT_PATH = Path("data/_system/real_dashboard_manual_sell_intent.json")
DIRECT_ORDER_ENV = "KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS"

_real_broker = None
_real_broker_error: str = ""
_real_broker_error_logged = False


class RealBuyIntentRequest(BaseModel):
    candidate_id: str
    source: str = "real_dashboard"
    notional: float | None = None


class RealSellIntentRequest(BaseModel):
    ticker: str
    shares_requested: float | None = None
    source: str = "real_dashboard"


def _direct_orders_enabled() -> bool:
    return str(os.environ.get(DIRECT_ORDER_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _positive_float(value: Any, *, name: str) -> float:
    out = _safe_float(value)
    if out is None or out <= 0.0:
        raise ValueError(f"{name} must be positive")
    return float(out)


def _get_real_broker():
    """Lazy Alpaca live broker.

    Do not initialize at import time: missing live credentials must not break the
    paper dashboard/API server.
    """
    global _real_broker, _real_broker_error, _real_broker_error_logged
    if _real_broker is not None:
        return _real_broker
    try:
        from engine.live.broker.alpaca import AlpacaBroker

        base_url = str(os.environ.get("ALPACA_LIVE_BASE_URL") or ALPACA_LIVE_BASE_URL).strip()
        _real_broker = AlpacaBroker(base_url=base_url, paper=False)
        _real_broker_error = ""
        return _real_broker
    except Exception as exc:
        _real_broker_error = f"{type(exc).__name__}: {exc}"
        if not _real_broker_error_logged:
            _real_broker_error_logged = True
        return None


def _broker_unavailable_payload() -> dict[str, Any]:
    return {
        "ok": False,
        "account_source": "alpaca_live_unavailable",
        "broker_mode": "alpaca_live",
        "direct_orders_enabled": _direct_orders_enabled(),
        "error": _real_broker_error or "Alpaca live broker is not initialized",
        "cash": 0.0,
        "invested": 0.0,
        "total_value": 0.0,
        "unrealized_pnl": 0.0,
        "holdings_count": 0,
        "orders_today": 0,
    }


def _holding_to_position(holding: Any) -> dict[str, Any]:
    ticker = str(getattr(holding, "ticker", "") or "").upper().strip()
    shares = _safe_float(getattr(holding, "shares", None), 0.0) or 0.0
    entry = _safe_float(getattr(holding, "avg_cost", None), 0.0) or 0.0
    cur = _safe_float(getattr(holding, "current_price", None), entry) or entry
    market_value = _safe_float(getattr(holding, "market_value", None), shares * cur) or 0.0
    unreal = _safe_float(getattr(holding, "unrealized_pnl", None), (cur - entry) * shares) or 0.0
    pnl_pct = _safe_float(getattr(holding, "unrealized_pnl_pct", None))
    if pnl_pct is None and entry > 0.0 and cur > 0.0:
        pnl_pct = (cur / entry - 1.0) * 100.0
    return {
        "ticker": ticker,
        "entry_price": entry,
        "current_price": cur,
        "stop_price": None,
        "target_price": None,
        "trailing_stop": None,
        "highest_price": cur,
        "lowest_price": cur,
        "shares": shares,
        "direction": "long",
        "exit_strategy": "manual_real",
        "max_holding_days": None,
        "entry_date": "",
        "holding_days": None,
        "member_hash": "",
        "pnl_pct": pnl_pct,
        "target_return_pct": None,
        "stop_return_pct": None,
        "trailing_return_pct": None,
        "rulebook_win_rate": None,
        "rulebook_expectancy_pct": None,
        "rulebook_avg_return_pct": None,
        "rulebook_trade_count": None,
        "holding_news": None,
        "early_exit_profile": None,
        "rulebook": {},
        "entry": {},
        "market_value": market_value,
        "unrealized_pnl": unreal,
        "account_source": "alpaca_live",
    }


def _real_positions_payload() -> list[dict[str, Any]]:
    broker = _get_real_broker()
    if broker is None:
        return []
    try:
        holdings = broker.get_holdings()
    except Exception:
        return []
    rows = [_holding_to_position(h) for h in holdings]
    return [r for r in rows if r.get("ticker") and _safe_float(r.get("shares"), 0.0)]


def _intent_state(path: Path, *, default_trade_date: str = "") -> dict[str, Any]:
    data = read_json(path, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", 1)
    data.setdefault("trade_date", default_trade_date)
    data.setdefault("updated_at", "")
    if not isinstance(data.get("intents"), dict):
        data["intents"] = {}
    return data


def _write_intent_state(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = utc_now_iso()
    atomic_write_json(path, data)


def _candidate_state_for_real(base_module: Any, *, include_blocked: bool = False) -> dict[str, Any]:
    try:
        state = base_module.central_candidates(include_blocked=include_blocked)
    except Exception:
        state = {"schema_version": 1, "trade_date": "", "candidates": {}}
    if not isinstance(state, dict):
        state = {"schema_version": 1, "trade_date": "", "candidates": {}}
    state = dict(state)
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    state["candidates"] = {str(cid): dict(row) for cid, row in candidates.items() if isinstance(row, dict)}
    state["api_namespace"] = "real"
    state["order_intent_path"] = str(REAL_BUY_INTENT_PATH)
    state["direct_orders_enabled"] = _direct_orders_enabled()
    intents = _intent_state(REAL_BUY_INTENT_PATH, default_trade_date=str(state.get("trade_date") or ""))
    for intent_id, intent in (intents.get("intents") or {}).items():
        if not isinstance(intent, dict) or str(intent.get("status") or "") not in {"pending", "submitted"}:
            continue
        cid = str(intent.get("candidate_id") or "")
        if not cid or cid not in state["candidates"]:
            continue
        row = state["candidates"][cid]
        row["status"] = "manual_requested"
        row["manual_intent_id"] = str(intent_id)
        row["manual_buy_enabled"] = False
        row["manual_requested_notional"] = intent.get("notional")
        row["manual_notional"] = intent.get("notional")
        row["notional_source"] = "real_dashboard_amount"
        row["action_label"] = "실거래 요청"
    return state


def _candidate_for_real(base_module: Any, candidate_id: str) -> dict[str, Any]:
    state = _candidate_state_for_real(base_module, include_blocked=True)
    row = (state.get("candidates") or {}).get(candidate_id)
    if not isinstance(row, dict):
        raise ValueError(f"candidate not found or stale: {candidate_id}")
    return row


def _order_dict(order: Any) -> dict[str, Any]:
    if hasattr(order, "to_dict"):
        return order.to_dict()
    return dict(order) if isinstance(order, dict) else {"raw": str(order)}


def _create_real_buy_intent(base_module: Any, req: RealBuyIntentRequest) -> dict[str, Any]:
    candidate_id = str(req.candidate_id or "").strip()
    if not candidate_id:
        raise ValueError("candidate_id required")
    candidate = _candidate_for_real(base_module, candidate_id)
    ticker = str(candidate.get("ticker") or "").upper().strip()
    if not ticker:
        raise ValueError("candidate ticker missing")
    default_notional = _safe_float(candidate.get("notional"), 0.0) or 0.0
    notional = _positive_float(req.notional if req.notional is not None else default_notional, name="notional")
    trade_date = str(candidate.get("trade_date") or candidate.get("execution_session") or "")
    data = _intent_state(REAL_BUY_INTENT_PATH, default_trade_date=trade_date)
    intents = data.setdefault("intents", {})
    now = utc_now_iso()
    intent_id = f"real-buy:{candidate_id}"
    row = dict(intents.get(intent_id) or {})
    row.update(
        {
            "intent_id": intent_id,
            "candidate_id": candidate_id,
            "trade_date": trade_date,
            "status": "pending",
            "source": req.source or "real_dashboard",
            "ticker": ticker,
            "entity_id": candidate.get("entity_id"),
            "notional": notional,
            "candidate_notional": default_notional,
            "price": _safe_float(candidate.get("price"), 0.0) or 0.0,
            "created_at": row.get("created_at") or now,
            "updated_at": now,
            "execution_mode": "real_intent_only",
            "direct_orders_enabled": _direct_orders_enabled(),
            "note": "real dashboard separated intent; no paper/live intent file touched",
        }
    )
    if _direct_orders_enabled():
        broker = _get_real_broker()
        if broker is None:
            raise ValueError(f"real broker unavailable: {_real_broker_error}")
        price = _safe_float(broker.get_current_price(ticker)) or _safe_float(candidate.get("price"))
        if price is None or price <= 0.0:
            raise ValueError(f"current price unavailable for {ticker}")
        shares = notional / price
        order = broker.place_buy(ticker, shares, order_type=OrderType.MARKET, price=0.0, client_order_id=f"km-real-buy-{int(time.time())}-{ticker}")
        row.update(
            {
                "status": "submitted",
                "submitted_at": utc_now_iso(),
                "shares_requested": shares,
                "execution_mode": "direct_alpaca_live_market_order",
                "broker_order": _order_dict(order),
            }
        )
    intents[intent_id] = row
    _write_intent_state(REAL_BUY_INTENT_PATH, data)
    return row


def _create_real_sell_intent(req: RealSellIntentRequest) -> dict[str, Any]:
    ticker = str(req.ticker or "").upper().strip()
    if not ticker:
        raise ValueError("ticker required")
    positions = {str(p.get("ticker") or "").upper(): p for p in _real_positions_payload()}
    pos = positions.get(ticker)
    if not pos:
        raise ValueError("not held in real account")
    held_shares = _positive_float(pos.get("shares"), name="held shares")
    shares = req.shares_requested
    if shares is None:
        shares_value = held_shares
    else:
        shares_value = _positive_float(shares, name="shares_requested")
        if shares_value > held_shares + 1e-6:
            raise ValueError("shares_requested exceeds real holding")
    data = _intent_state(REAL_SELL_INTENT_PATH)
    intents = data.setdefault("intents", {})
    now = utc_now_iso()
    intent_id = f"real-sell:{ticker}:{int(time.time())}"
    row: dict[str, Any] = {
        "intent_id": intent_id,
        "ticker": ticker,
        "status": "pending",
        "source": req.source or "real_dashboard",
        "shares_requested": shares_value,
        "held_shares_at_request": held_shares,
        "created_at": now,
        "updated_at": now,
        "execution_mode": "real_intent_only",
        "direct_orders_enabled": _direct_orders_enabled(),
        "note": "real dashboard separated sell intent; no paper/live intent file touched",
    }
    if _direct_orders_enabled():
        broker = _get_real_broker()
        if broker is None:
            raise ValueError(f"real broker unavailable: {_real_broker_error}")
        order = broker.place_sell(ticker, shares_value, order_type=OrderType.MARKET, price=0.0, client_order_id=f"km-real-sell-{int(time.time())}-{ticker}")
        row.update(
            {
                "status": "submitted",
                "submitted_at": utc_now_iso(),
                "execution_mode": "direct_alpaca_live_market_order",
                "broker_order": _order_dict(order),
            }
        )
    intents[intent_id] = row
    _write_intent_state(REAL_SELL_INTENT_PATH, data)
    return row


def _real_buy_amount_overlay_js() -> str:
    return r"""
(function(){
  const BASE = (typeof API !== 'undefined' ? API : window.location.origin);
  const PREFIX = '/api/real';
  const state = {candidates:{}, lastLoad:0, busy:{}};
  const css = `
  .manual-buy-amount{width:104px;background:#090d14;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:7px 8px;font-size:12px;font-weight:800;font-variant-numeric:tabular-nums;}
  .manual-buy-amount:focus{outline:none;border-color:#ef4444;box-shadow:0 0 0 1px rgba(239,68,68,.35) inset;}
  .cand-right.manual-buy-enhanced{align-items:flex-end;}
  .manual-buy-controls{display:flex;flex-direction:column;gap:3px;align-items:flex-end;}
  .manual-buy-row{display:flex;gap:6px;align-items:center;}
  .manual-buy-hint{font-size:10px;color:var(--dim);margin-top:3px;text-align:right;font-variant-numeric:tabular-nums;}
  `;
  function injectCss(){ if(document.getElementById('real-manual-buy-amount-style')) return; const el=document.createElement('style'); el.id='real-manual-buy-amount-style'; el.textContent=css; document.head.appendChild(el); }
  function toast2(t,b,type){ try{ if(typeof toast==='function') toast(t,b,type); else console.log(t,b); }catch(e){ console.log(t,b); } }
  function num(v){ if(v==null) return null; const x=Number(String(v).replace(/,/g,'').trim()); return isFinite(x)?x:null; }
  function money(v){ const x=num(v); return x==null?'—':'$'+x.toLocaleString(undefined,{maximumFractionDigits:0}); }
  async function loadCandidateMap(force){ const now=Date.now(); if(!force && now-state.lastLoad<10000) return state.candidates; state.lastLoad=now; try{ const r=await fetch(`${BASE}${PREFIX}/central_candidates`); const d=await r.json(); state.candidates=(d&&d.candidates)||{}; }catch(e){} return state.candidates; }
  function defaultNotional(c){ const n=num(c && (c.manual_requested_notional || c.manual_notional || c.notional)); return n!=null && n>0 ? n : null; }
  function decorateButtons(){
    injectCss();
    document.querySelectorAll('.cand-buy[data-candidate-id]').forEach(btn=>{
      if(btn.dataset.realAmountEnhanced==='1') return;
      if(btn.disabled) return;
      const cid=String(btn.dataset.candidateId||''); if(!cid) return;
      const c=state.candidates[cid]||{};
      const parent=btn.parentElement; if(!parent) return;
      parent.classList.add('manual-buy-enhanced');
      const controls=document.createElement('div'); controls.className='manual-buy-controls';
      const row=document.createElement('div'); row.className='manual-buy-row';
      const input=document.createElement('input'); input.className='manual-buy-amount'; input.type='number'; input.min='1'; input.step='100'; input.placeholder='실매수 $'; input.dataset.candidateId=cid;
      const dn=defaultNotional(c); if(dn!=null) input.value=String(Math.round(dn));
      input.title='실거래 계좌에 요청할 매수 금액(USD)';
      input.addEventListener('click',ev=>ev.stopPropagation()); input.addEventListener('mousedown',ev=>ev.stopPropagation()); input.addEventListener('keydown',ev=>{ev.stopPropagation(); if(ev.key==='Enter') btn.click();});
      btn.textContent=state.busy[cid]?'요청 중…':'실거래 요청'; btn.dataset.realAmountEnhanced='1';
      row.appendChild(input); row.appendChild(btn);
      const hint=document.createElement('div'); hint.className='manual-buy-hint'; hint.textContent=dn!=null?`기본 ${money(dn)}`:'실거래 금액';
      controls.appendChild(row); controls.appendChild(hint); parent.appendChild(controls);
    });
  }
  async function sendBuyIntent(btn){
    const cid=String(btn.dataset.candidateId||''); if(!cid || state.busy[cid]) return;
    const box=btn.closest('.manual-buy-controls')||btn.parentElement; const input=box&&box.querySelector?box.querySelector('.manual-buy-amount'):null;
    const amount=num(input&&input.value); const c=state.candidates[cid]||{}; const ticker=String(c.ticker || cid).toUpperCase();
    if(amount==null || amount<=0){ toast2('실거래 매수 금액 필요', `${ticker} 매수 금액을 달러 기준으로 입력하세요`, 'warn'); if(input) input.focus(); return; }
    if(!confirm(`[실거래 대시보드]\n${ticker} ${money(amount)} 매수 요청을 별도 real API에 기록할까요?\n\n직접 주문 활성화 환경변수가 켜져 있으면 실제 Alpaca live 주문이 제출될 수 있습니다.`)) return;
    state.busy[cid]=true; btn.disabled=true; if(input) input.disabled=true; btn.textContent='요청 중…';
    try{
      const r=await fetch(`${BASE}${PREFIX}/manual_buy_intent`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({candidate_id:cid, source:'real_dashboard_amount', notional:amount})});
      const d=await r.json().catch(()=>({})); if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);
      const mode=(d.intent&&d.intent.execution_mode)||'real_intent_only';
      toast2('실거래 매수 요청됨', `${ticker} · ${money(amount)} · ${mode}`, 'good');
    }catch(e){ toast2('실거래 매수 요청 거부', String(e.message||e), 'warn'); btn.disabled=false; if(input) input.disabled=false; btn.textContent='실거래 요청'; }
    finally{ state.busy[cid]=false; try{ if(typeof loadCandidates==='function') await loadCandidates(); }catch(e){} setTimeout(()=>loadCandidateMap(true).then(decorateButtons),300); }
  }
  document.addEventListener('click', ev=>{ const btn=ev.target&&ev.target.closest?ev.target.closest('.cand-buy[data-candidate-id]'):null; if(!btn || btn.dataset.realAmountEnhanced!=='1') return; ev.preventDefault(); ev.stopPropagation(); ev.stopImmediatePropagation(); sendBuyIntent(btn); }, true);
  async function tick(){ await loadCandidateMap(false); decorateButtons(); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', tick); else tick();
  setInterval(tick, 1500);
  try{ const target=document.getElementById('cand-list')||document.body; new MutationObserver(()=>tick()).observe(target,{childList:true,subtree:true}); }catch(e){}
})();
"""


def _real_dashboard_html(base_module: Any) -> HTMLResponse:
    path = base_module.DASHBOARD_MAIN_PATH
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"dashboard file missing: {path}")
    html = path.read_text(encoding="utf-8")
    html = html.replace("<title>KINGMAKER</title>", "<title>KINGMAKER REAL</title>")
    html = html.replace('const API="http://localhost:8001";', 'const API=window.location.origin;\nwindow.KM_DASHBOARD_MODE="real";')
    replacements = {
        "/api/live/account": "/api/real/account",
        "/api/live/slots": "/api/real/slots",
        "/api/live/positions": "/api/real/positions",
        "/api/live/equity_curve": "/api/real/equity_curve",
        "/api/live/trades_history": "/api/real/trades_history",
        "/api/live/central_candidates": "/api/real/central_candidates",
        "/api/live/manual_buy_intents": "/api/real/manual_buy_intents",
        "/api/live/manual_buy_intent": "/api/real/manual_buy_intent",
        "/api/live/manual_sell_intents": "/api/real/manual_sell_intents",
        "/api/live/manual_sell_intent": "/api/real/manual_sell_intent",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    html = html.replace("<div class=\"logo\">", "<div class=\"logo\"><span style=\"color:#ef4444;margin-right:8px;\">REAL</span>")
    html = html.replace(
        "<body>",
        "<body>\n<div style=\"background:#3b0d0d;color:#fecaca;border-bottom:1px solid #7f1d1d;padding:8px 22px;font-size:12px;font-weight:800;letter-spacing:.2px;\">"
        "⚠️ 실거래용 복제 대시보드 · 계좌/보유/주문요청 API는 /api/real/* 사용 · 기존 paper/live intent 파일과 분리됨 · 직접 주문은 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1일 때만 제출"
        "</div>",
    )
    html = html.replace(
        "실제 매도 주문이 들어가며 되돌릴 수 없습니다.",
        "실거래용 별도 청산 요청이 기록됩니다. 직접 주문 환경변수가 켜져 있으면 실제 Alpaca live 주문이 제출될 수 있습니다.",
    )
    snippet = '<script src="/real-buy-amount-overlay.js?v=real_dashboard_v1"></script>\n'
    if "real-buy-amount-overlay.js" not in html:
        html = html.replace("</body>", snippet + "</body>")
    return HTMLResponse(content=html, media_type="text/html")


def install_real_dashboard_routes(app: Any, base_module: Any) -> None:
    @app.get("/dashboard-real", include_in_schema=False)
    def dashboard_real():
        return _real_dashboard_html(base_module)

    @app.get("/real-dashboard", include_in_schema=False)
    def real_dashboard_alias():
        return _real_dashboard_html(base_module)

    @app.get("/dashboard_real.html", include_in_schema=False)
    def dashboard_real_html_alias():
        return _real_dashboard_html(base_module)

    @app.get("/real-buy-amount-overlay.js", include_in_schema=False)
    def real_buy_amount_overlay_js():
        return Response(content=_real_buy_amount_overlay_js(), media_type="application/javascript; charset=utf-8")

    @app.get("/api/real/account")
    def real_account():
        broker = _get_real_broker()
        if broker is None:
            return _broker_unavailable_payload()
        try:
            bal = broker.get_balance()
            holdings = list(getattr(bal, "holdings", []) or [])
            invested = _safe_float(getattr(bal, "invested_usd", getattr(bal, "invested_krw", 0.0)), 0.0) or 0.0
            total = _safe_float(getattr(bal, "total_value_usd", getattr(bal, "total_value_krw", 0.0)), 0.0) or 0.0
            cash = _safe_float(getattr(bal, "cash_usd", getattr(bal, "cash_krw", 0.0)), 0.0) or 0.0
            unreal = sum(_safe_float(getattr(h, "unrealized_pnl", 0.0), 0.0) or 0.0 for h in holdings)
            try:
                orders_today = len(broker.get_open_orders())
            except Exception:
                orders_today = 0
            return {
                "ok": True,
                "cash": round(cash, 2),
                "invested": round(invested, 6),
                "total_value": round(total, 2),
                "unrealized_pnl": round(unreal, 6),
                "holdings_count": len(holdings),
                "orders_today": orders_today,
                "snapshot_time": getattr(bal, "fetched_at", "") or utc_now_iso(),
                "account_source": "alpaca_live",
                "broker_mode": getattr(broker, "mode", "alpaca_live"),
                "direct_orders_enabled": _direct_orders_enabled(),
                "realized_pnl_today": 0.0,
                "realized_pnl_total": 0.0,
                "total_return_pct": 0.0,
            }
        except Exception as exc:
            payload = _broker_unavailable_payload()
            payload["error"] = f"{type(exc).__name__}: {exc}"
            return payload

    @app.get("/api/real/positions")
    def real_positions():
        return _real_positions_payload()

    @app.get("/api/real/slots")
    def real_slots(max_slots: int = 8):
        filled = _real_positions_payload()
        slots = []
        for i in range(int(max_slots or 8)):
            if i < len(filled):
                slots.append({"slot": i + 1, "empty": False, **filled[i]})
            else:
                slots.append({"slot": i + 1, "empty": True})
        return slots

    @app.get("/api/real/equity_curve")
    def real_equity_curve():
        acct = real_account()
        total = _safe_float(acct.get("total_value"), 0.0) if isinstance(acct, dict) else 0.0
        return [{"time": utc_now_iso(), "value": round(total or 0.0, 2)}]

    @app.get("/api/real/trades_history")
    def real_trades_history():
        return {
            "stats": {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "total_pnl": 0,
                "avg_win_pct": 0,
                "avg_loss_pct": 0,
            },
            "trades": [],
            "account_source": "alpaca_live",
            "note": "real dashboard trade history API is separated; broker historical fills are not merged yet",
        }

    @app.get("/api/real/central_candidates")
    def real_central_candidates(include_blocked: bool = False):
        return _candidate_state_for_real(base_module, include_blocked=include_blocked)

    @app.get("/api/real/manual_buy_intents")
    def real_manual_buy_intents():
        return _intent_state(REAL_BUY_INTENT_PATH)

    @app.post("/api/real/manual_buy_intent")
    def real_manual_buy_intent(req: RealBuyIntentRequest):
        try:
            row = _create_real_buy_intent(base_module, req)
            return {"ok": True, "intent": row, "execution_mode": row.get("execution_mode")}
        except (ValueError, BrokerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/real/manual_sell_intents")
    def real_manual_sell_intents():
        return _intent_state(REAL_SELL_INTENT_PATH)

    @app.post("/api/real/manual_sell_intent")
    def real_manual_sell_intent(req: RealSellIntentRequest):
        try:
            row = _create_real_sell_intent(req)
            return {"ok": True, "intent": row, "execution_mode": row.get("execution_mode")}
        except (ValueError, BrokerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
