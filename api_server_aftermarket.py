"""KINGMAKER dashboard API wrapper with after-market latest-trade prices.

이 파일은 기존 `api_server.py`의 FastAPI 앱을 그대로 재사용하면서,
대시보드 보유 종목 현재가 함수만 Alpaca latest trade 우선으로 패치하는
라이브 대시보드 전용 wrapper입니다.

무엇을 하는 파일인가:
- `/dashboard`, `/api/live/*` 등 기존 api_server 라우트는 그대로 사용한다.
- 기존 `api_server._get_price()`를 런타임에 교체한다.
- 가격 조회 순서는 Alpaca Market Data latest trade → yfinance 1분봉 pre/post → yfinance fast_info → yfinance 2일봉 fallback이다.
- 목적은 텔레그램 `/positions`와 웹 대시보드의 보유 종목 현재가를 장외/애프터마켓에서도 같은 기준으로 맞추는 것이다.

주의:
- 이 파일은 broker 주문을 내지 않는다.
- live runner, positions.json, parameters.json을 수정하지 않는다.
- API 서버 실행 시 `uvicorn api_server_aftermarket:app`으로 띄워야 이 패치가 적용된다.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import yfinance as yf

import api_server as _base

log = logging.getLogger("api_server.aftermarket")

app = _base.app

_PRICE_CACHE_TTL_SEC = 15.0
_price_cache: dict[str, tuple[float | None, float, str]] = {}
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


def _get_price_aftermarket(ticker: str):
    """Return latest visible dashboard price, including after-market prints.

    Alpaca latest trade is intentionally first because yfinance 1m pre/post and
    fast_info can lag or round differently from the broker/telegram price path.
    """
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        return None
    now = time.time()
    hit = _price_cache.get(symbol)
    if hit and now - hit[1] < _PRICE_CACHE_TTL_SEC:
        return hit[0]

    price = None
    source = "none"

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
            hist = yf.Ticker(symbol).history(period="1d", interval="1m", prepost=True)
            if hist is not None and not hist.empty:
                close = hist["Close"].dropna()
                if not close.empty:
                    price = _safe_float(close.iloc[-1])
                    if price is not None:
                        source = "yfinance_1m_prepost"
        except Exception as exc:
            log.debug("%s yfinance 1m prepost dashboard price failed: %s", symbol, exc)
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


# Patch the original route functions' global lookup.  Existing routes call
# api_server._get_price at execution time, so replacing it here updates
# /api/live/positions, /api/live/slots, and /api/live/account together.
_base._get_price = _get_price_aftermarket
_base._price_cache = _price_cache
