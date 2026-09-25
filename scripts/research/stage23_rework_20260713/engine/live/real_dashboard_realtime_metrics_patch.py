"""Realtime PnL/stat display patch for dashboard-real.

Real holdings come from Alpaca positions, then candidate/rulebook context is
attached at the top level.  Some legacy dashboard widgets read stats from
``row.rulebook`` while the newer real-holding widgets read top-level fields.  This
patch normalizes both shapes and adds a lightweight position refresh loop so PnL
updates without a full page refresh.
"""
from __future__ import annotations

from typing import Any

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_POSITIONS_PAYLOAD = None
_ORIG_SLOT_OVERLAY_JS = None


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out != out:
            return None
        return out
    except Exception:
        return None


def _normalize_real_holding_stats(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row or {})
    # Top-level compatibility: legacy cards/detail use rulebook_* names, while
    # real candidate context uses short names.
    pairs = (
        ("win_rate", "rulebook_win_rate"),
        ("expectancy_pct", "rulebook_expectancy_pct"),
        ("trade_count", "rulebook_trade_count"),
    )
    for a, b in pairs:
        if out.get(a) in (None, "") and out.get(b) not in (None, ""):
            out[a] = out.get(b)
        if out.get(b) in (None, "") and out.get(a) not in (None, ""):
            out[b] = out.get(a)
    if out.get("mdd_pct") in (None, ""):
        rb0 = out.get("rulebook") if isinstance(out.get("rulebook"), dict) else {}
        if rb0.get("max_drawdown_pct") not in (None, ""):
            out["mdd_pct"] = rb0.get("max_drawdown_pct")
    rb = dict(out.get("rulebook") or {}) if isinstance(out.get("rulebook"), dict) else {}
    # Populate rulebook stats so base renderRulebook() does not show blanks.
    stat_map = {
        "win_rate": out.get("win_rate") or out.get("rulebook_win_rate"),
        "expectancy_pct": out.get("expectancy_pct") or out.get("rulebook_expectancy_pct"),
        "max_drawdown_pct": out.get("mdd_pct"),
        "trade_count": out.get("trade_count") or out.get("rulebook_trade_count"),
        "fitness": out.get("fitness"),
        "signal_threshold": out.get("threshold"),
        "max_holding_days": out.get("max_holding_days"),
        "stop_loss_atr": out.get("stop_loss_atr"),
        "take_profit_atr": out.get("take_profit_atr"),
        "trailing_atr": out.get("trailing_atr"),
        "exit_strategy": out.get("exit_strategy_name") or out.get("exit_strategy"),
    }
    for key, value in stat_map.items():
        if rb.get(key) in (None, "") and value not in (None, ""):
            rb[key] = value
    if rb.get("direction") in (None, ""):
        rb["direction"] = out.get("direction") or "long"
    if rb.get("sell_omen_enabled") in (None, "") and out.get("candidate_id"):
        # Candidate-universe rulebooks used by the real slot context enable this
        # for CE and similar S2 no-TP real holdings; keep display explicit when
        # detailed candidate context is present.
        rb["sell_omen_enabled"] = True
    out["rulebook"] = rb
    # Recalculate PnL if a broker row omitted it, keeping broker value when valid.
    pnl = _num(out.get("pnl_pct"))
    entry = _num(out.get("entry_price"))
    cur = _num(out.get("current_price"))
    shares = _num(out.get("shares"))
    if pnl is None and entry and cur:
        out["pnl_pct"] = (cur / entry - 1.0) * 100.0
    if out.get("unrealized_pnl") in (None, "") and entry is not None and cur is not None and shares is not None:
        out["unrealized_pnl"] = (cur - entry) * shares
    if out.get("market_value") in (None, "") and cur is not None and shares is not None:
        out["market_value"] = cur * shares
    return out


def _patch_positions_payload() -> None:
    global _ORIG_POSITIONS_PAYLOAD
    if _ORIG_POSITIONS_PAYLOAD is not None:
        return
    _ORIG_POSITIONS_PAYLOAD = real_api._real_positions_payload

    def patched_real_positions_payload() -> list[dict[str, Any]]:
        rows = _ORIG_POSITIONS_PAYLOAD()
        if not isinstance(rows, list):
            return []
        return [_normalize_real_holding_stats(r) for r in rows if isinstance(r, dict)]

    real_api._real_positions_payload = patched_real_positions_payload


def _patch_slot_overlay_js() -> None:
    global _ORIG_SLOT_OVERLAY_JS
    if _ORIG_SLOT_OVERLAY_JS is not None:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    injection = r'''
  function kmNormalizeRealHoldingStatsRow(s){
    if(!s || typeof s!=='object') return s;
    if(s.win_rate==null && s.rulebook_win_rate!=null) s.win_rate=s.rulebook_win_rate;
    if(s.rulebook_win_rate==null && s.win_rate!=null) s.rulebook_win_rate=s.win_rate;
    if(s.expectancy_pct==null && s.rulebook_expectancy_pct!=null) s.expectancy_pct=s.rulebook_expectancy_pct;
    if(s.rulebook_expectancy_pct==null && s.expectancy_pct!=null) s.rulebook_expectancy_pct=s.expectancy_pct;
    if(s.trade_count==null && s.rulebook_trade_count!=null) s.trade_count=s.rulebook_trade_count;
    if(s.rulebook_trade_count==null && s.trade_count!=null) s.rulebook_trade_count=s.trade_count;
    const rb=(s.rulebook && typeof s.rulebook==='object') ? s.rulebook : {};
    if(rb.win_rate==null && s.win_rate!=null) rb.win_rate=s.win_rate;
    if(rb.expectancy_pct==null && s.expectancy_pct!=null) rb.expectancy_pct=s.expectancy_pct;
    if(rb.max_drawdown_pct==null && s.mdd_pct!=null) rb.max_drawdown_pct=s.mdd_pct;
    if(rb.trade_count==null && s.trade_count!=null) rb.trade_count=s.trade_count;
    if(rb.fitness==null && s.fitness!=null) rb.fitness=s.fitness;
    if(rb.signal_threshold==null && s.threshold!=null) rb.signal_threshold=s.threshold;
    if(rb.max_holding_days==null && s.max_holding_days!=null) rb.max_holding_days=s.max_holding_days;
    if(rb.stop_loss_atr==null && s.stop_loss_atr!=null) rb.stop_loss_atr=s.stop_loss_atr;
    if(rb.take_profit_atr==null && s.take_profit_atr!=null) rb.take_profit_atr=s.take_profit_atr;
    if(rb.trailing_atr==null && s.trailing_atr!=null) rb.trailing_atr=s.trailing_atr;
    if(rb.exit_strategy==null && (s.exit_strategy_name!=null || s.exit_strategy!=null)) rb.exit_strategy=s.exit_strategy_name||s.exit_strategy;
    if(rb.direction==null) rb.direction=s.direction||'long';
    s.rulebook=rb;
    return s;
  }
  function kmNormalizeRealSlotData(rows){
    return (Array.isArray(rows)?rows:[]).map(r=>{
      if(r && !r.empty) return kmNormalizeRealHoldingStatsRow(r);
      return r;
    });
  }
  function kmRealFormatMoney(n){
    const v=Number(n);
    if(!Number.isFinite(v)) return '—';
    return v.toFixed(2);
  }
  function kmRealFormatPct(n,d=2){
    const v=Number(n);
    if(!Number.isFinite(v)) return '—';
    return `${v>=0?'+':''}${v.toFixed(d)}%`;
  }
  function kmUpdateKvByLabel(label, html, color){
    try{
      const kv=document.getElementById('detail-kv');
      if(!kv) return false;
      const rows=[...kv.querySelectorAll('.kv')];
      const row=rows.find(x=>String((x.children[0]&&x.children[0].textContent)||'').trim()===label);
      if(!row || !row.children[1]) return false;
      row.children[1].innerHTML=html;
      if(color) row.children[1].style.color=color;
      return true;
    }catch(e){ return false; }
  }
  function kmUpdateRealHoldingDetailNumbers(s){
    try{
      if(!s) return;
      const pnl=Number(s.pnl_pct);
      const pnlColor=Number.isFinite(pnl) ? (pnl>=0?'var(--up)':'var(--down)') : 'var(--dim)';
      kmUpdateKvByLabel('현재가', kmRealFormatMoney(s.current_price));
      kmUpdateKvByLabel('손익률', kmRealFormatPct(s.pnl_pct), pnlColor);
      kmUpdateKvByLabel('수량', Number(s.shares||0).toFixed(4));
      kmUpdateKvByLabel('학습 승률', s.rulebook_win_rate==null?'—':Number(s.rulebook_win_rate).toFixed(1)+'%');
      kmUpdateKvByLabel('기대 수익률', kmRealFormatPct(s.rulebook_expectancy_pct));
      kmUpdateKvByLabel('백테스트 승률', s.win_rate==null?'—':Number(s.win_rate).toFixed(2)+'%');
      kmUpdateKvByLabel('기대수익률', s.expectancy_pct==null?'—':Number(s.expectancy_pct).toFixed(2)+'%');
      kmUpdateKvByLabel('MDD', s.mdd_pct==null?'—':Number(s.mdd_pct).toFixed(2)+'%');
      kmUpdateKvByLabel('거래수', s.trade_count==null?'—':Number(s.trade_count).toFixed(0));
      kmUpdateKvByLabel('fitness', s.fitness==null?'—':Number(s.fitness).toFixed(2));
      const title=document.getElementById('detail-title');
      if(title) title.textContent=`${String(s.ticker||'').toUpperCase()} — 보유 차트`;
    }catch(e){}
  }
  function kmRefreshActiveRealHoldingObject(s){
    if(!s) return;
    try{ window._activeRealHolding=s; }catch(e){}
    try{
      const tk=String(s.ticker||'').toUpperCase();
      const old=(window.slotData||slotData||[]).find(x=>x && String(x.ticker||'').toUpperCase()===tk);
      if(old){ Object.assign(old, s); }
    }catch(e){}
  }
  async function kmRefreshRealHoldingMetricsFast(){
    if(window._kmRealHoldingMetricsBusy) return;
    window._kmRealHoldingMetricsBusy=true;
    const ctrl=(typeof AbortController!=='undefined') ? new AbortController() : null;
    const timer=ctrl ? setTimeout(()=>{try{ctrl.abort();}catch(e){}}, 3500) : null;
    try{
      const r=await fetch(`${API}/api/real/slots?max_slots=8&_=${Date.now()}`, {cache:'no-store', signal:ctrl?ctrl.signal:undefined});
      const rows=await r.json();
      if(!Array.isArray(rows)) return;
      const normalized=kmNormalizeRealSlotData(rows);
      window.slotData=normalized;
      try{ slotData=normalized; }catch(e){}
      try{ if(typeof checkSlotAlerts==='function') checkSlotAlerts(normalized); }catch(e){}
      try{ if(typeof renderSlots==='function') renderSlots(); }catch(e){}
      try{
        const detail=document.getElementById('slot-detail-view');
        const active=(typeof _activeChart!=='undefined') ? _activeChart : null;
        const tk=String((active&&active.ticker) || (window._activeRealHolding&&window._activeRealHolding.ticker) || '').toUpperCase();
        if(detail && detail.style.display==='block' && tk){
          const fresh=normalized.find(x=>x && !x.empty && String(x.ticker||'').toUpperCase()===tk);
          if(fresh){
            kmRefreshActiveRealHoldingObject(fresh);
            // Do not call refreshDetailPanel()/renderRealHoldingLiveEnhancements()
            // here.  They rebuild commentary and the stop/take panel, causing
            // the visible flicker.  Only update numeric text nodes.
            kmUpdateRealHoldingDetailNumbers(fresh);
            try{ updateSellOmenStrip(fresh); }catch(e){}
          }
        }
      }catch(e){}
      try{ updateRealUpdateBadge({positionsFetch:new Date().toISOString()}); }catch(e){}
    }catch(e){}
    finally{
      if(timer) clearTimeout(timer);
      window._kmRealHoldingMetricsBusy=false;
    }
  }
  const _kmRealOldRenderSlots = window.renderSlots;
  if(typeof _kmRealOldRenderSlots==='function' && !_kmRealOldRenderSlots.__kmRealStatsNormalized){
    const wrapped=function(){
      try{
        const normalized=kmNormalizeRealSlotData(window.slotData||slotData||[]);
        window.slotData=normalized;
        try{ slotData=normalized; }catch(e){}
      }catch(e){}
      return _kmRealOldRenderSlots.apply(this, arguments);
    };
    wrapped.__kmRealStatsNormalized=true;
    window.renderSlots=wrapped;
    try{ renderSlots=wrapped; }catch(e){}
  }
  if(!window._kmRealHoldingMetricsInterval){
    window._kmRealHoldingMetricsInterval=setInterval(kmRefreshRealHoldingMetricsFast, 5000);
    setTimeout(kmRefreshRealHoldingMetricsFast, 1200);
  }
'''

    def patched_real_slot_overlay_js() -> str:
        js = _ORIG_SLOT_OVERLAY_JS()
        if "kmRefreshRealHoldingMetricsFast" in js:
            return js
        marker = "})();"
        if marker in js:
            head, tail = js.rsplit(marker, 1)
            return head + injection + "\n" + marker + tail
        return js + "\n" + injection

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js


def install_real_dashboard_realtime_metrics_patch() -> None:
    """Install realtime holding PnL/stat display patches once per API process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_positions_payload()
    _patch_slot_overlay_js()
    _INSTALLED = True
