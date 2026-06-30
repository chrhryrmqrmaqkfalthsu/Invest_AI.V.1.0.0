"""KINGMAKER dashboard API wrapper with true pre/post-market display prices.

이 파일은 기존 `api_server.py`의 FastAPI 앱을 그대로 재사용하면서,
대시보드 보유 종목 현재가 함수만 장전/장후 prepost 가격 우선으로 패치하는
라이브 대시보드 전용 wrapper입니다.

무엇을 하는 파일인가:
- `/dashboard`, `/api/live/*` 등 기존 api_server 라우트는 그대로 사용한다.
- 기존 `api_server._get_price()`를 런타임에 교체한다.
- 가격 조회 순서는 yfinance 1분봉 pre/post → Alpaca Market Data latest trade → yfinance fast_info → yfinance 2일봉 fallback이다.
- Alpaca free/IEX latest trade가 15:59 ET 정규장 마지막 체결에 머무르는 경우가 있어, 애프터마켓 표시값은 yfinance prepost를 우선한다.
- 수동청산 버튼은 intent 파일을 쓴 뒤 localhost runner command server를 즉시 wake한다.
- `/elite-shadow`에서 broker 주문 없는 정예 후보 shadow 성적표를 제공한다.
- 목적은 텔레그램 `/positions`와 웹 대시보드의 보유 종목 현재가를 장외/애프터마켓에서도 같은 기준으로 맞추는 것이다.

주의:
- 이 파일은 broker 주문을 직접 내지 않는다.
- 수동청산 주문 제출은 항상 live runner 내부 기존 manual SELL 경로에서만 수행된다.
- elite-shadow는 기존 batch artifact를 읽기만 하며 live runner, positions.json, parameters.json을 수정하지 않는다.
- API 서버 실행 시 `uvicorn api_server_aftermarket:app`으로 띄워야 이 패치가 적용된다.
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
    """Create AlpacaBroker lazily so dashboard import never fails hard."""
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
    """Return latest visible dashboard price, including after-market prints.

    yfinance 1m pre/post is intentionally first because Alpaca free/IEX latest
    trade can remain at the regular-session last print around 15:59 ET.
    """
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
    """Ask live runner to consume a manual sell intent immediately.

    This is intentionally only a wake-up RPC.  api_server still does not import
    broker credentials or place orders.  If the runner command server is missing
    or busy, the file-backed intent remains and fast-exit/tick_market consumes it.
    """
    state = _base._read_json(str(RUNNER_COMMAND_STATE_PATH), {})
    if not isinstance(state, dict) or not state.get("url") or not state.get("token"):
        return {
            "ok": False,
            "mode": "intent_fallback",
            "reason": "runner_command_state_missing",
            "state_path": str(RUNNER_COMMAND_STATE_PATH),
        }
    url = str(state.get("url") or "").rstrip("/") + "/manual_sell/wake"
    payload = {
        "ticker": str(intent_row.get("ticker") or "").upper(),
        "intent_id": str(intent_row.get("intent_id") or ""),
        "source": "api_server_aftermarket",
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Kingmaker-Token": str(state.get("token") or ""),
        },
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
            return {
                "ok": True,
                "mode": "runner_rpc",
                "http_status": int(getattr(resp, "status", 0) or 0),
                "elapsed_ms": round((time.time() - started) * 1000.0, 3),
                "runner_response": body,
            }
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"error": str(exc)}
        return {
            "ok": False,
            "mode": "intent_fallback",
            "reason": "runner_rpc_http_error",
            "http_status": int(getattr(exc, "code", 0) or 0),
            "elapsed_ms": round((time.time() - started) * 1000.0, 3),
            "runner_response": body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "intent_fallback",
            "reason": type(exc).__name__,
            "message": str(exc),
            "elapsed_ms": round((time.time() - started) * 1000.0, 3),
        }


def manual_sell_intent_immediate(req: _base.ManualSellIntentRequest):
    """Create manual SELL intent and immediately wake the live runner.

    The wake call does not place orders in the API process.  It simply asks the
    runner process to consume the just-written intent through its existing safe
    manual-sell path.  Fallback remains the JSON intent + fast-exit poller.
    """
    try:
        row = _base.create_manual_sell_intent(
            ticker=req.ticker,
            shares_requested=req.shares_requested,
            source=req.source or "dashboard",
            positions_path=_base.MANUAL_SELL_POSITIONS_PATH,
            intent_path=_base.MANUAL_SELL_INTENT_PATH,
        )
        wake = _wake_runner_manual_sell(row)
        return {
            "ok": True,
            "intent": row,
            "runner_wake": wake,
            "execution_mode": "runner_rpc" if wake.get("ok") else "intent_fallback",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _replace_manual_sell_route() -> None:
    target_path = "/api/live/manual_sell_intent"
    app.router.routes = [
        route for route in app.router.routes
        if not (
            getattr(route, "path", "") == target_path
            and "POST" in set(getattr(route, "methods", set()) or set())
        )
    ]
    app.post(target_path)(manual_sell_intent_immediate)
    log.warning("manual sell route patched: intent + immediate runner wake")


@app.get("/elite-shadow", include_in_schema=False)
def elite_shadow_page():
    """Serve read-only elite shadow dashboard page."""
    if not ELITE_SHADOW_PAGE_PATH.exists():
        raise HTTPException(status_code=500, detail=f"elite shadow page missing: {ELITE_SHADOW_PAGE_PATH}")
    return HTMLResponse(ELITE_SHADOW_PAGE_PATH.read_text(encoding="utf-8"), media_type="text/html")


@app.get("/api/live/elite_shadow")
def elite_shadow_report(refresh: bool = False):
    """Return cached FIX-type elite shadow candidate report.

    This endpoint reads historical batch artifacts only.  It does not place
    broker orders and does not modify live state.
    """
    now = time.time()
    if (
        not refresh
        and _elite_shadow_cache.get("payload") is not None
        and now - float(_elite_shadow_cache.get("ts") or 0.0) < _ELITE_SHADOW_CACHE_TTL_SEC
    ):
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


# Patch the original route functions' global lookup.  Existing routes call
# api_server._get_price at execution time, so replacing it here updates
# /api/live/positions, /api/live/slots, and /api/live/account together.
_base._get_price = _get_price_aftermarket
_base._price_cache = _price_cache
_replace_manual_sell_route()
