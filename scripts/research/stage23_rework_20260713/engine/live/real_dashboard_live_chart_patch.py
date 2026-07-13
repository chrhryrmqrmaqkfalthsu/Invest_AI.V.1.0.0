"""Runtime patch for real-dashboard held-position intraday charts.

This patch is intentionally separated from ``real_dashboard_api.py`` so the large
HTML/JS generator does not need invasive edits.  It fixes real-trading UI issues:

1. Held-position detail pages should open/refresh the 1m chart, not silently stay
   on the base dashboard's default daily chart.
2. 1m candles must be one-minute candles on the dashboard.  Alpaca IEX bars are
   actual OHLCV bars, but for CE they can be sparse because IEX has no print in
   many minutes.  The dashboard therefore uses the existing public 1m candle
   loader for chart continuity, while avoiding synthetic live-tail candles.
3. Automatic refresh must not reset the user's zoom/scroll range.  The detail
   refresh reloads the full candle dataset with the existing viewport restored,
   so revised recent bars are reflected without the chart jumping.
"""
from __future__ import annotations

from typing import Any

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_CANDLES_CACHED = None
_ORIG_SLOT_OVERLAY_JS = None


def _mark_public_1m_source(ticker: str, interval: str, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tag real-dashboard 1m candles with their continuity source.

    Do not append or alter OHLC values here.  The earlier synthetic live-tail
    candle made candle bodies/wicks differ from a full reload.  This function only
    copies rows and records the source for diagnostics.
    """
    if str(interval or "").strip() != "1m":
        return candles
    out: list[dict[str, Any]] = []
    for row in candles or []:
        if not isinstance(row, dict):
            continue
        copied = dict(row)
        copied.setdefault("source", "public_1m_candle_loader")
        out.append(copied)
    return out


def _patch_real_candles_cached() -> None:
    global _ORIG_CANDLES_CACHED
    if _ORIG_CANDLES_CACHED is not None:
        return
    _ORIG_CANDLES_CACHED = real_api._real_candles_cached

    def patched_real_candles_cached(base_module: Any, *, ticker: str, interval: str = "1d", period: str | None = None, refresh: bool = False) -> list[dict[str, Any]]:
        data = _ORIG_CANDLES_CACHED(base_module, ticker=ticker, interval=interval, period=period, refresh=refresh)
        if not isinstance(data, list):
            return []
        return _mark_public_1m_source(ticker, interval, data)

    real_api._real_candles_cached = patched_real_candles_cached


def _patch_slot_overlay_js() -> None:
    global _ORIG_SLOT_OVERLAY_JS
    if _ORIG_SLOT_OVERLAY_JS is not None:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    detail_refresh = r'''
  function realVisibleRange(){
    try{
      return (typeof chart!=='undefined' && chart && chart.timeScale && chart.timeScale().getVisibleLogicalRange) ? chart.timeScale().getVisibleLogicalRange() : null;
    }catch(e){ return null; }
  }
  function restoreRealVisibleRange(range){
    try{
      if(range && typeof chart!=='undefined' && chart && chart.timeScale && chart.timeScale().setVisibleLogicalRange){
        chart.timeScale().setVisibleLogicalRange(range);
      }
    }catch(e){}
  }
  function realCandleSignature(candles){
    try{
      if(!Array.isArray(candles) || !candles.length) return '';
      return candles.slice(-20).map(c=>[c.time,c.open,c.high,c.low,c.close,c.volume,c.source||''].join(':')).join('|');
    }catch(e){ return String(Date.now()); }
  }
  async function refreshRealDetailCandlesPreservingRange(ticker, interval){
    try{
      if(interval!=='1m') return false;
      if(typeof series==='undefined' || !series || typeof series.setData!=='function') return false;
      const r=await fetch(`${API}/api/real/candles/${encodeURIComponent(ticker)}?interval=${encodeURIComponent(interval)}&refresh=true`, {cache:'no-store'});
      const candles=await r.json();
      if(!Array.isArray(candles) || !candles.length) return false;
      const last=candles[candles.length-1];
      if(!last || last.time==null) return false;
      const sig=realCandleSignature(candles);
      const range=realVisibleRange();
      if(window._lastRealDetailCandleSig!==sig){
        series.setData(candles);
        if(typeof volSeries!=='undefined' && volSeries && typeof volSeries.setData==='function'){
          volSeries.setData(candles.map(c=>({
            time:c.time,
            value:c.volume||0,
            color:Number(c.close)>=Number(c.open) ? 'rgba(38,208,124,.5)' : 'rgba(255,77,106,.5)'
          })));
        }
        window._lastRealDetailCandleSig=sig;
      }
      restoreRealVisibleRange(range);
      window._lastRealDetailCandleTime=last.time;
      window._lastRealDetailCandleClose=last.close;
      updateRealUpdateBadge({chartLatest:last.time, chartFetch:new Date().toISOString()});
      return true;
    }catch(e){ return false; }
  }
  async function updateRealDetailLatestCandle(ticker, interval){
    return refreshRealDetailCandlesPreservingRange(ticker, interval);
  }
  async function drawRealDetailPreservingRange(ticker, interval){
    const range=realVisibleRange();
    if(typeof window.drawChart==='function') await window.drawChart(ticker, interval, {preserveRange:true});
    restoreRealVisibleRange(range);
  }
  async function forceRealHoldingOneMinuteChart(ticker){
    try{
      const tk=String(ticker||'').toUpperCase();
      if(!tk) return;
      document.querySelectorAll('.tf').forEach(b=>b.classList.toggle('active', b.dataset.tf==='1m'));
      if(typeof _activeChart !== 'undefined') _activeChart={type:'real_holding', ticker:tk, interval:'1m'};
      window._lastRealDetailCandleSig='';
      if(typeof window.drawChart==='function') await window.drawChart(tk, '1m');
      updateRealUpdateBadge({chartFetch:new Date().toISOString()});
    }catch(e){}
  }
'''

    def patched_real_slot_overlay_js() -> str:
        js = _ORIG_SLOT_OVERLAY_JS()
        if "function forceRealHoldingOneMinuteChart" not in js:
            js = js.replace(
                "  async function refreshActiveRealDetailChart(){",
                detail_refresh + "\n  async function refreshActiveRealDetailChart(){",
            )
        js = js.replace(
            "      const interval=String(ac.interval||'1m');\n"
            "      if(!ticker || interval!=='1m') return;\n"
            "      if(typeof window.drawChart==='function') await window.drawChart(ticker, interval);",
            "      let interval=String(ac.interval||'1m');\n"
            "      if(String(ac.type||'')==='real_holding') interval='1m';\n"
            "      if(!ticker || interval!=='1m') return;\n"
            "      const updated=await refreshRealDetailCandlesPreservingRange(ticker, interval);\n"
            "      if(!updated) await drawRealDetailPreservingRange(ticker, interval);",
        )
        js = js.replace(
            "      let interval=String(ac.interval||'1m');\n"
            "      if(String(ac.type||'')==='real_holding') interval='1m';\n"
            "      if(!ticker || interval!=='1m') return;\n"
            "      const updated=await updateRealDetailLatestCandle(ticker, interval);\n"
            "      if(!updated) await drawRealDetailPreservingRange(ticker, interval);",
            "      let interval=String(ac.interval||'1m');\n"
            "      if(String(ac.type||'')==='real_holding') interval='1m';\n"
            "      if(!ticker || interval!=='1m') return;\n"
            "      const updated=await refreshRealDetailCandlesPreservingRange(ticker, interval);\n"
            "      if(!updated) await drawRealDetailPreservingRange(ticker, interval);",
        )
        js = js.replace(
            "      let interval=String(ac.interval||'1m');\n"
            "      if(String(ac.type||'')==='real_holding') interval='1m';\n"
            "      if(!ticker || interval!=='1m') return;\n"
            "      if(typeof window.drawChart==='function') await window.drawChart(ticker, interval);",
            "      let interval=String(ac.interval||'1m');\n"
            "      if(String(ac.type||'')==='real_holding') interval='1m';\n"
            "      if(!ticker || interval!=='1m') return;\n"
            "      const updated=await refreshRealDetailCandlesPreservingRange(ticker, interval);\n"
            "      if(!updated) await drawRealDetailPreservingRange(ticker, interval);",
        )
        js = js.replace(
            "        if(typeof _activeChart !== 'undefined') _activeChart={type:'real_holding', ticker:String(ticker||'').toUpperCase(), interval:(_activeChart&&_activeChart.interval)||'1d'};\n"
            "        renderRealHoldingLiveEnhancements(s);",
            "        const realHoldingTicker=String(ticker||'').toUpperCase();\n"
            "        if(typeof _activeChart !== 'undefined') _activeChart={type:'real_holding', ticker:realHoldingTicker, interval:'1m'};\n"
            "        renderRealHoldingLiveEnhancements(s);\n"
            "        setTimeout(()=>forceRealHoldingOneMinuteChart(realHoldingTicker),120);",
        )
        # Mini candidate/holding charts should also keep the user's zoom/scroll
        # instead of fitContent() on every refresh.
        js = js.replace(
            "          entry.ser.setData(use);",
            "          const _kmMiniRange=entry.chart&&entry.chart.timeScale&&entry.chart.timeScale().getVisibleLogicalRange ? entry.chart.timeScale().getVisibleLogicalRange() : null;\n          entry.ser.setData(use);",
        )
        js = js.replace(
            "          entry.chart.timeScale().fitContent();",
            "          if(_kmMiniRange) entry.chart.timeScale().setVisibleLogicalRange(_kmMiniRange); else entry.chart.timeScale().fitContent();",
        )
        return js

    real_api._real_slot_overlay_js = patched_real_slot_overlay_js


def install_real_dashboard_live_chart_patch() -> None:
    """Install live held-position chart patches once per API process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_real_candles_cached()
    _patch_slot_overlay_js()
    _INSTALLED = True
