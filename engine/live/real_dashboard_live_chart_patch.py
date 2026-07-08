"""Runtime patch for real-dashboard held-position intraday charts.

This patch is intentionally separated from ``real_dashboard_api.py`` so the large
HTML/JS generator does not need invasive edits.  It fixes two real-trading UI
issues:

1. Held-position detail pages should open/refresh the 1m chart, not silently stay
   on the base dashboard's default daily chart.
2. For held tickers, yfinance 1m bars can lag or skip minutes.  Append a display
   candle from Alpaca's current position price so the held-position chart reflects
   the live account mark while waiting for the next public 1m bar.
"""
from __future__ import annotations

import time
from typing import Any

from engine.live import real_dashboard_api as real_api

_INSTALLED = False
_ORIG_CANDLES_CACHED = None
_ORIG_SLOT_OVERLAY_JS = None


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


def _holding_current_price(ticker: str) -> float | None:
    tk = str(ticker or "").upper().strip()
    if not tk:
        return None
    try:
        broker = real_api._get_real_broker()
        if broker is not None:
            try:
                pos = broker.trading.get_open_position(tk)
                price = _to_float(getattr(pos, "current_price", None))
                if price is not None:
                    return price
            except Exception:
                pass
    except Exception:
        pass
    try:
        for row in real_api._real_positions_payload():
            if str(row.get("ticker") or "").upper().strip() == tk:
                price = _to_float(row.get("current_price"))
                if price is not None:
                    return price
    except Exception:
        pass
    return None


def _append_live_tail_for_holding(ticker: str, interval: str, candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if str(interval or "").strip() != "1m":
        return candles
    price = _holding_current_price(ticker)
    if price is None:
        return candles
    out = [dict(row) for row in candles if isinstance(row, dict)]
    now_minute = int(time.time() // 60 * 60)
    if out:
        last = out[-1]
        try:
            last_time = int(float(last.get("time")))
        except Exception:
            last_time = 0
        if last_time >= now_minute:
            last["close"] = round(price, 4)
            last["high"] = round(max(_to_float(last.get("high")) or price, price), 4)
            last["low"] = round(min(_to_float(last.get("low")) or price, price), 4)
            last["live_tail"] = True
            last["live_tail_source"] = "alpaca_position_current_price"
            return out
        # Do not backfill every missing minute; add one live mark candle so the
        # chart visibly follows the current held-position mark without inventing
        # a full bar sequence.
        prev_close = _to_float(last.get("close")) or price
        open_price = prev_close if last_time and now_minute - last_time <= 3600 else price
    else:
        open_price = price
    out.append({
        "time": now_minute,
        "open": round(open_price, 4),
        "high": round(max(open_price, price), 4),
        "low": round(min(open_price, price), 4),
        "close": round(price, 4),
        "volume": 0,
        "live_tail": True,
        "live_tail_source": "alpaca_position_current_price",
    })
    return out


def _patch_real_candles_cached() -> None:
    global _ORIG_CANDLES_CACHED
    if _ORIG_CANDLES_CACHED is not None:
        return
    _ORIG_CANDLES_CACHED = real_api._real_candles_cached

    def patched_real_candles_cached(base_module: Any, *, ticker: str, interval: str = "1d", period: str | None = None, refresh: bool = False) -> list[dict[str, Any]]:
        data = _ORIG_CANDLES_CACHED(base_module, ticker=ticker, interval=interval, period=period, refresh=refresh)
        if not isinstance(data, list):
            data = []
        return _append_live_tail_for_holding(ticker, interval, data)

    real_api._real_candles_cached = patched_real_candles_cached


def _patch_slot_overlay_js() -> None:
    global _ORIG_SLOT_OVERLAY_JS
    if _ORIG_SLOT_OVERLAY_JS is not None:
        return
    _ORIG_SLOT_OVERLAY_JS = real_api._real_slot_overlay_js

    detail_refresh = r'''
  async function forceRealHoldingOneMinuteChart(ticker){
    try{
      const tk=String(ticker||'').toUpperCase();
      if(!tk) return;
      document.querySelectorAll('.tf').forEach(b=>b.classList.toggle('active', b.dataset.tf==='1m'));
      if(typeof _activeChart !== 'undefined') _activeChart={type:'real_holding', ticker:tk, interval:'1m'};
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
            "      if(!ticker || interval!=='1m') return;",
            "      let interval=String(ac.interval||'1m');\n"
            "      if(String(ac.type||'')==='real_holding') interval='1m';\n"
            "      if(!ticker || interval!=='1m') return;",
        )
        js = js.replace(
            "        if(typeof _activeChart !== 'undefined') _activeChart={type:'real_holding', ticker:String(ticker||'').toUpperCase(), interval:(_activeChart&&_activeChart.interval)||'1d'};\n"
            "        renderRealHoldingLiveEnhancements(s);",
            "        const realHoldingTicker=String(ticker||'').toUpperCase();\n"
            "        if(typeof _activeChart !== 'undefined') _activeChart={type:'real_holding', ticker:realHoldingTicker, interval:'1m'};\n"
            "        renderRealHoldingLiveEnhancements(s);\n"
            "        setTimeout(()=>forceRealHoldingOneMinuteChart(realHoldingTicker),120);",
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
