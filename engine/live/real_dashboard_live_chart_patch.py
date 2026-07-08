"""Runtime patch for real-dashboard held-position intraday charts.

This patch is intentionally separated from ``real_dashboard_api.py`` so the large
HTML/JS generator does not need invasive edits.  It fixes real-trading UI issues:

1. Held-position detail pages should open/refresh the 1m chart, not silently stay
   on the base dashboard's default daily chart.
2. Held-position 1m candles must be stable and truthful.  The earlier live-tail
   display candle used position/current-price marks on top of yfinance bars; that
   made candle bodies/wicks differ after a full refresh.  For actually held
   symbols, use Alpaca IEX 1m OHLCV bars first and do not invent missing bars.
3. Automatic refresh must not reset the user's zoom/scroll range.  The detail
   refresh reloads the full candle dataset with the existing viewport restored,
   so delayed/revised recent bars are reflected without the chart jumping.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_CANDLES_CACHED = None
_ORIG_SLOT_OVERLAY_JS = None
_ALPACA_1M_BAR_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_ALPACA_1M_BAR_TTL_SEC = 8.0


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out != out or out <= 0.0:
            return None
        return out
    except Exception:
        return None


def _is_real_holding(ticker: str) -> bool:
    tk = str(ticker or "").upper().strip()
    if not tk:
        return False
    try:
        for row in real_api._real_positions_payload():
            if str(row.get("ticker") or "").upper().strip() == tk and _to_float(row.get("shares")):
                return True
    except Exception:
        pass
    return False


def _alpaca_iex_1m_candles(ticker: str, *, refresh: bool = False) -> list[dict[str, Any]]:
    """Return actual Alpaca IEX 1m OHLCV bars for a held ticker.

    We intentionally do not fill missing minutes and do not append a synthetic
    current-price candle.  Missing bars mean there was no IEX print for that
    minute; fabricating OHLC values creates fake wicks/bodies and is exactly what
    made the chart look different after refresh.
    """
    tk = str(ticker or "").upper().strip()
    if not tk:
        return []
    cache_key = (tk, "1m:alpaca_iex")
    now = time.time()
    if not refresh:
        hit = _ALPACA_1M_BAR_CACHE.get(cache_key)
        if hit and now - hit[0] <= _ALPACA_1M_BAR_TTL_SEC:
            return [dict(x) for x in hit[1]]
    broker = real_api._get_real_broker()
    if broker is None or getattr(broker, "data", None) is None:
        return []
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=5)
    req = StockBarsRequest(
        symbol_or_symbols=tk,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        adjustment=Adjustment.RAW,
        feed=DataFeed.IEX,
    )
    bars = broker.data.get_stock_bars(req)
    raw_bars = []
    try:
        raw_bars = list((getattr(bars, "data", {}) or {}).get(tk) or [])
    except Exception:
        raw_bars = []
    out: list[dict[str, Any]] = []
    for bar in raw_bars:
        try:
            ts = getattr(bar, "timestamp", None)
            if isinstance(ts, datetime):
                epoch = int(ts.astimezone(timezone.utc).timestamp())
            else:
                epoch = int(float(ts))
            o = _to_float(getattr(bar, "open", None))
            h = _to_float(getattr(bar, "high", None))
            l = _to_float(getattr(bar, "low", None))
            c = _to_float(getattr(bar, "close", None))
            if o is None or h is None or l is None or c is None:
                continue
            high = max(o, h, l, c)
            low = min(o, h, l, c)
            out.append({
                "time": epoch,
                "open": round(o, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(c, 4),
                "volume": int(float(getattr(bar, "volume", 0) or 0)),
                "trade_count": int(float(getattr(bar, "trade_count", 0) or 0)),
                "vwap": round(float(getattr(bar, "vwap", 0) or 0), 4) if getattr(bar, "vwap", None) is not None else None,
                "source": "alpaca_iex_1m_bar",
            })
        except Exception:
            continue
    # De-duplicate by time while preserving newest value for each minute.
    by_time: dict[int, dict[str, Any]] = {}
    for row in out:
        by_time[int(row["time"])] = row
    out = [by_time[k] for k in sorted(by_time)]
    if out:
        _ALPACA_1M_BAR_CACHE[cache_key] = (now, [dict(x) for x in out])
    return out


def _patch_real_candles_cached() -> None:
    global _ORIG_CANDLES_CACHED
    if _ORIG_CANDLES_CACHED is not None:
        return
    _ORIG_CANDLES_CACHED = real_api._real_candles_cached

    def patched_real_candles_cached(base_module: Any, *, ticker: str, interval: str = "1d", period: str | None = None, refresh: bool = False) -> list[dict[str, Any]]:
        tk = str(ticker or "").upper().strip()
        iv = str(interval or "1d").strip()
        if iv == "1m" and _is_real_holding(tk):
            try:
                alpaca_bars = _alpaca_iex_1m_candles(tk, refresh=refresh)
                if alpaca_bars:
                    return alpaca_bars
            except Exception:
                pass
        data = _ORIG_CANDLES_CACHED(base_module, ticker=ticker, interval=interval, period=period, refresh=refresh)
        return data if isinstance(data, list) else []

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
      return candles.slice(-12).map(c=>[c.time,c.open,c.high,c.low,c.close,c.volume,c.source||''].join(':')).join('|');
    }catch(e){ return String(Date.now()); }
  }
  async function refreshRealDetailCandlesPreservingRange(ticker, interval){
    try{
      if(interval!=='1m') return false;
      if(typeof series==='undefined' || !series || typeof series.setData!=='function') return false;
      const r=await fetch(`${API}/api/real/candles/${encodeURIComponent(ticker)}?interval=${encodeURIComponent(interval)}`, {cache:'no-store'});
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
