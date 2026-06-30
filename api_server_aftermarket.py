"""KINGMAKER dashboard API wrapper with true pre/post-market display prices.

기존 api_server 앱을 재사용하면서 장전/장후 가격 표시, 수동청산 runner wake,
elite shadow/strategy simulation 대시보드 라우트를 추가하는 wrapper입니다.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yfinance as yf
from fastapi import HTTPException
from fastapi.responses import HTMLResponse

import api_server as _base

log = logging.getLogger("api_server.aftermarket")
app = _base.app

_PRICE_CACHE_TTL_SEC = 15.0
_ELITE_SHADOW_CACHE_TTL_SEC = 600.0
RUNNER_COMMAND_STATE_PATH = Path("data/_system/runner_command_lr8d16.json")
ELITE_SHADOW_PAGE_PATH = Path("elite_shadow.html")
ELITE_STRATEGY_SIM_PAGE_PATH = Path("elite_strategy_sim.html")
_price_cache: dict[str, tuple[float | None, float, str]] = {}
_elite_shadow_cache: dict[str, Any] = {"ts": 0.0, "payload": None}
_broker = None
_broker_init_error_logged = False


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out <= 0.0 or out != out:
            return None
        return out
    except Exception:
        return None


def _get_alpaca_broker():
    global _broker, _broker_init_error_logged
    if _broker is not None:
        return _broker
    try:
        from engine.live.broker.alpaca import AlpacaBroker, DEFAULT_ALPACA_BASE_URL
        _broker = AlpacaBroker(base_url=DEFAULT_ALPACA_BASE_URL, paper=True)
        return _broker
    except Exception as exc:
        if not _broker_init_error_logged:
            log.warning("dashboard AlpacaBroker lazy init failed; yfinance fallback only: %s", exc)
            _broker_init_error_logged = True
        return None


def _yfinance_prepost_price(symbol: str) -> float | None:
    try:
        hist = yf.Ticker(symbol).history(period="1d", interval="1m", prepost=True)
        if hist is not None and not hist.empty:
            close = hist["Close"].dropna()
            if not close.empty:
                return _safe_float(close.iloc[-1])
    except Exception as exc:
        log.debug("%s yfinance 1m prepost dashboard price failed: %s", symbol, exc)
    return None


def _get_price_aftermarket(ticker: str):
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        return None
    now = time.time()
    hit = _price_cache.get(symbol)
    if hit and now - hit[1] < _PRICE_CACHE_TTL_SEC:
        return hit[0]

    price = _yfinance_prepost_price(symbol)
    source = "yfinance_1m_prepost" if price is not None else "none"

    if price is None:
        broker = _get_alpaca_broker()
        if broker is not None:
            try:
                price = _safe_float(broker.get_current_price(symbol))
                if price is not None:
                    source = "alpaca_latest_trade"
            except Exception as exc:
                log.debug("%s Alpaca latest trade dashboard price failed: %s", symbol, exc)
                price = None

    if price is None:
        try:
            data = yf.Ticker(symbol).fast_info
            raw = data.get("lastPrice") if hasattr(data, "get") else data["lastPrice"]
            price = _safe_float(raw)
            if price is not None:
                source = "yfinance_fast_info"
        except Exception as exc:
            log.debug("%s yfinance fast_info dashboard price failed: %s", symbol, exc)
            price = None

    if price is None:
        try:
            hist = yf.Ticker(symbol).history(period="2d", prepost=True)
            if hist is not None and not hist.empty:
                price = _safe_float(hist["Close"].iloc[-1])
                if price is not None:
                    source = "yfinance_2d_prepost"
        except Exception as exc:
            log.debug("%s yfinance 2d dashboard price failed: %s", symbol, exc)
            price = None

    _price_cache[symbol] = (price, now, source)
    return price


def _wake_runner_manual_sell(intent_row: dict) -> dict:
    state = _base._read_json(str(RUNNER_COMMAND_STATE_PATH), {})
    if not isinstance(state, dict) or not state.get("url") or not state.get("token"):
        return {"ok": False, "mode": "intent_fallback", "reason": "runner_command_state_missing", "state_path": str(RUNNER_COMMAND_STATE_PATH)}
    url = str(state.get("url") or "").rstrip("/") + "/manual_sell/wake"
    payload = {"ticker": str(intent_row.get("ticker") or "").upper(), "intent_id": str(intent_row.get("intent_id") or ""), "source": "api_server_aftermarket"}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json; charset=utf-8", "X-Kingmaker-Token": str(state.get("token") or "")})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            return {"ok": True, "mode": "runner_rpc", "http_status": int(getattr(resp, "status", 0) or 0), "elapsed_ms": round((time.time() - started) * 1000.0, 3), "runner_response": body}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"error": str(exc)}
        return {"ok": False, "mode": "intent_fallback", "reason": "runner_rpc_http_error", "http_status": int(getattr(exc, "code", 0) or 0), "elapsed_ms": round((time.time() - started) * 1000.0, 3), "runner_response": body}
    except Exception as exc:
        return {"ok": False, "mode": "intent_fallback", "reason": type(exc).__name__, "message": str(exc), "elapsed_ms": round((time.time() - started) * 1000.0, 3)}


def manual_sell_intent_immediate(req: _base.ManualSellIntentRequest):
    try:
        row = _base.create_manual_sell_intent(
            ticker=req.ticker,
            shares_requested=req.shares_requested,
            source=req.source or "dashboard",
            positions_path=_base.MANUAL_SELL_POSITIONS_PATH,
            intent_path=_base.MANUAL_SELL_INTENT_PATH,
        )
        wake = _wake_runner_manual_sell(row)
        return {"ok": True, "intent": row, "runner_wake": wake, "execution_mode": "runner_rpc" if wake.get("ok") else "intent_fallback"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _replace_manual_sell_route() -> None:
    target_path = "/api/live/manual_sell_intent"
    app.router.routes = [route for route in app.router.routes if not (getattr(route, "path", "") == target_path and "POST" in set(getattr(route, "methods", set()) or set()))]
    app.post(target_path)(manual_sell_intent_immediate)
    log.warning("manual sell route patched: intent + immediate runner wake")


@app.get("/elite-shadow", include_in_schema=False)
def elite_shadow_page():
    if not ELITE_SHADOW_PAGE_PATH.exists():
        raise HTTPException(status_code=500, detail=f"elite shadow page missing: {ELITE_SHADOW_PAGE_PATH}")
    return HTMLResponse(ELITE_SHADOW_PAGE_PATH.read_text(encoding="utf-8"), media_type="text/html")


@app.get("/elite-strategy-sim", include_in_schema=False)
def elite_strategy_sim_page():
    if not ELITE_STRATEGY_SIM_PAGE_PATH.exists():
        raise HTTPException(status_code=500, detail=f"elite strategy sim page missing: {ELITE_STRATEGY_SIM_PAGE_PATH}")
    return HTMLResponse(ELITE_STRATEGY_SIM_PAGE_PATH.read_text(encoding="utf-8"), media_type="text/html")


@app.get("/api/live/elite_shadow")
def elite_shadow_report(refresh: bool = False):
    now = time.time()
    if not refresh and _elite_shadow_cache.get("payload") is not None and now - float(_elite_shadow_cache.get("ts") or 0.0) < _ELITE_SHADOW_CACHE_TTL_SEC:
        payload = dict(_elite_shadow_cache["payload"])
        payload["cache"] = {"hit": True, "age_seconds": round(now - float(_elite_shadow_cache.get("ts") or 0.0), 3)}
        return payload
    try:
        from engine.live.elite_shadow_report import build_elite_shadow_report
        payload = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=True)
        payload["cache"] = {"hit": False, "age_seconds": 0.0}
        _elite_shadow_cache["payload"] = payload
        _elite_shadow_cache["ts"] = now
        return payload
    except Exception as exc:
        log.exception("elite shadow report build failed")
        raise HTTPException(status_code=500, detail=f"elite shadow build failed: {type(exc).__name__}: {exc}")


@app.get("/api/live/elite_shadow_trader")
def elite_shadow_trader_state():
    try:
        from engine.live.elite_shadow_trader import shadow_dashboard_payload
        return shadow_dashboard_payload(recent_trade_limit=300)
    except Exception as exc:
        log.exception("elite shadow trader state failed")
        raise HTTPException(status_code=500, detail=f"elite shadow trader state failed: {type(exc).__name__}: {exc}")


@app.post("/api/live/elite_shadow_tick")
def elite_shadow_tick(max_candidates: int = 93):
    try:
        from engine.live.elite_shadow_trader import run_shadow_tick
        return run_shadow_tick(max_candidates=int(max_candidates))
    except Exception as exc:
        log.exception("elite shadow manual tick failed")
        raise HTTPException(status_code=500, detail=f"elite shadow tick failed: {type(exc).__name__}: {exc}")


@app.get("/api/live/elite_strategy_sim")
def elite_strategy_sim_state():
    try:
        from engine.live.elite_strategy_sim import strategy_sim_payload
        return strategy_sim_payload(recent_trade_limit=300)
    except Exception as exc:
        log.exception("elite strategy sim state failed")
        raise HTTPException(status_code=500, detail=f"elite strategy sim state failed: {type(exc).__name__}: {exc}")


@app.post("/api/live/elite_strategy_sim_tick")
def elite_strategy_sim_tick(max_candidates: int = 93):
    try:
        from engine.live.elite_strategy_sim import run_strategy_sim_tick
        return run_strategy_sim_tick(max_candidates=int(max_candidates))
    except Exception as exc:
        log.exception("elite strategy sim manual tick failed")
        raise HTTPException(status_code=500, detail=f"elite strategy sim tick failed: {type(exc).__name__}: {exc}")


_base._get_price = _get_price_aftermarket
_base._price_cache = _price_cache
_replace_manual_sell_route()
