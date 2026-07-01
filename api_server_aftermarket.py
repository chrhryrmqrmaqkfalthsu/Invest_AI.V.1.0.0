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
from collections import Counter
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


def _num(value: Any, default: float = 0.0) -> float:
    """Dashboard 계산 전용 숫자 변환. 손익처럼 음수도 허용한다."""
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


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


def _refresh_positions_mark_to_market(positions: list[Any], *, refreshed_at: str) -> tuple[int, list[dict[str, str]]]:
    """열린 가상 포지션의 현재가/미실현 손익 필드를 채운다.

    Shadow/Strategy 모두 대시보드 GET 때 화면 표시용 mark-to-market만 수행한다.
    신규 진입/청산 기록은 각 tick 루프에서만 수행한다.
    """
    refreshed = 0
    errors: list[dict[str, str]] = []
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        ticker = str(pos.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        price = _get_price_aftermarket(ticker)
        entry = _safe_float(pos.get("entry_price"))
        notional = _num(pos.get("notional"), 0.0)
        shares = _safe_float(pos.get("shares"))
        if shares is None and entry:
            shares = notional / entry if notional > 0 else None
        if price is None:
            price = _safe_float(pos.get("last_price")) or entry
        if price is None or entry is None or shares is None:
            errors.append({"ticker": ticker, "reason": "price_or_entry_missing"})
            continue
        highest = max(_num(pos.get("highest_price"), entry), price)
        lowest = min(_num(pos.get("lowest_price"), entry), price)
        pnl_pct = (price / entry - 1.0) * 100.0
        pnl_usd = shares * (price - entry)
        pos.update(
            {
                "last_price": price,
                "last_seen_at": refreshed_at,
                "highest_price": highest,
                "lowest_price": lowest,
                "max_profit_pct": max(_num(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0),
                "max_loss_pct": min(_num(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0),
                "unrealized_pnl_pct": pnl_pct,
                "unrealized_pnl_usd": pnl_usd,
                "price_source": "dashboard_prepost_refresh",
            }
        )
        refreshed += 1
    return refreshed, errors


def _refresh_strategy_sim_prices(payload: dict[str, Any]) -> dict[str, Any]:
    """전략 시뮬 화면용 mark-to-market 보정.

    strategy tick은 후보 93개 재평가 때문에 무겁다. 대신 대시보드 GET 때는
    이미 열린 가상 포지션의 현재가/미실현 손익만 가볍게 다시 계산해서
    화면 숫자가 tick 완료 전에도 움직이도록 한다. 실제 주문/청산 기록은 하지 않는다.
    """
    strategies = payload.get("strategies") or {}
    total_refreshed = 0
    refreshed_at = _utc_now()
    for name, sim in strategies.items():
        positions = sim.get("open_positions") or []
        if not isinstance(positions, list):
            positions = []
        refreshed, errors = _refresh_positions_mark_to_market(positions, refreshed_at=refreshed_at)
        total_refreshed += refreshed

        summary = dict(sim.get("summary") or {})
        open_pnl = sum(_num(p.get("unrealized_pnl_usd"), 0.0) for p in positions if isinstance(p, dict))
        open_notional = sum(_num(p.get("notional"), 0.0) for p in positions if isinstance(p, dict))
        realized_pnl = _num(summary.get("realized_pnl_usd"), 0.0)
        closed_notional = _num(summary.get("closed_notional"), 0.0)
        total_notional = open_notional + closed_notional
        total_pnl = open_pnl + realized_pnl
        summary.update(
            {
                "open_count": len(positions),
                "open_unrealized_usd": open_pnl,
                "open_notional": open_notional,
                "total_notional": total_notional,
                "total_pnl_usd": total_pnl,
                "open_roi_pct": open_pnl / open_notional * 100.0 if open_notional else 0.0,
                "total_roi_pct": total_pnl / total_notional * 100.0 if total_notional else 0.0,
            }
        )
        sim["summary"] = summary
        sim["open_gate_counts"] = dict(Counter(str(p.get("gate") or "UNKNOWN") for p in positions if isinstance(p, dict)))
        sim["price_refresh"] = {
            "refreshed_at": refreshed_at,
            "refreshed": refreshed,
            "open_count": len(positions),
            "errors": errors[:8],
            "ttl_sec": _PRICE_CACHE_TTL_SEC,
            "note": "GET 응답 시 열린 가상 포지션 가격만 갱신. 신규 진입/청산 판단은 strategy tick에서만 수행.",
        }
        log.debug("strategy sim price refresh %s: %s/%s", name, refreshed, len(positions))
    payload["price_refresh"] = {"refreshed_at": refreshed_at, "refreshed": total_refreshed, "ttl_sec": _PRICE_CACHE_TTL_SEC}
    return payload


def _refresh_shadow_trader_prices(payload: dict[str, Any]) -> dict[str, Any]:
    """Elite Shadow 화면용 mark-to-market 보정.

    Shadow tick에서 신규 OPEN 직후에는 unrealized_pnl_pct/usd가 아직 없을 수 있다.
    대시보드 GET 때 열린 가상 포지션의 현재가와 미실현 손익을 채워 화면 수익률이
    비지 않도록 한다. 실제 주문/청산/ledger 저장은 하지 않는다.
    """
    positions = payload.get("open_positions") or []
    if not isinstance(positions, list):
        positions = []
    refreshed_at = _utc_now()
    refreshed, errors = _refresh_positions_mark_to_market(positions, refreshed_at=refreshed_at)
    trades = payload.get("recent_trades") or []
    open_pnl = sum(_num(p.get("unrealized_pnl_usd"), 0.0) for p in positions if isinstance(p, dict))
    open_notional = sum(_num(p.get("notional"), 0.0) for p in positions if isinstance(p, dict))
    realized_pnl = sum(_num(t.get("pnl_usd"), 0.0) for t in trades if isinstance(t, dict))
    realized_notional = sum(_num(t.get("notional"), 0.0) for t in trades if isinstance(t, dict))
    pnls = [_num(t.get("pnl_pct"), 0.0) for t in trades if isinstance(t, dict)]
    wins = [p for p in pnls if p > 0]
    total_notional = open_notional + realized_notional
    total_pnl = open_pnl + realized_pnl
    summary = dict(payload.get("summary") or {})
    summary.update(
        {
            "open_count": len(positions),
            "closed_count": len(trades),
            "win_rate": len(wins) / len(pnls) * 100.0 if pnls else 0.0,
            "avg_pnl_pct": sum(pnls) / len(pnls) if pnls else 0.0,
            "realized_pnl_usd": realized_pnl,
            "open_unrealized_usd": open_pnl,
            "open_unrealized_pct_avg": (sum(_num(p.get("unrealized_pnl_pct"), 0.0) for p in positions if isinstance(p, dict)) / len(positions)) if positions else 0.0,
            "open_notional": open_notional,
            "closed_notional": realized_notional,
            "total_notional": total_notional,
            "total_pnl_usd": total_pnl,
            "open_roi_pct": open_pnl / open_notional * 100.0 if open_notional else 0.0,
            "total_roi_pct": total_pnl / total_notional * 100.0 if total_notional else 0.0,
        }
    )
    payload["summary"] = summary
    payload["price_refresh"] = {
        "refreshed_at": refreshed_at,
        "refreshed": refreshed,
        "open_count": len(positions),
        "errors": errors[:8],
        "ttl_sec": _PRICE_CACHE_TTL_SEC,
        "note": "GET 응답 시 열린 Shadow 가상 포지션 가격/미실현 손익만 갱신. 신규 진입/청산 판단은 shadow tick에서만 수행.",
    }
    return payload


def _strategy_payload_with_price_and_forecast() -> dict[str, Any]:
    from engine.live.elite_pullback_forecast import attach_pullback_forecasts_to_strategy_payload
    from engine.live.elite_strategy_sim import strategy_sim_payload

    payload = strategy_sim_payload(recent_trade_limit=300)
    payload = _refresh_strategy_sim_prices(payload)
    return attach_pullback_forecasts_to_strategy_payload(payload)


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
        return _refresh_shadow_trader_prices(shadow_dashboard_payload(recent_trade_limit=300))
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
        return _strategy_payload_with_price_and_forecast()
    except Exception as exc:
        log.exception("elite strategy sim state failed")
        raise HTTPException(status_code=500, detail=f"elite strategy sim state failed: {type(exc).__name__}: {exc}")


@app.get("/api/live/elite_pullback_forecast")
def elite_pullback_forecast_state():
    try:
        payload = _strategy_payload_with_price_and_forecast()
        return {
            "_comment": "Pullback outcome forecast extracted from elite strategy sim payload. Virtual/read-only only.",
            "pullback_forecast": payload.get("pullback_forecast") or {},
            "strategies": {
                name: {
                    "summary": sim.get("pullback_forecast_summary") or {},
                    "positions": [
                        {
                            "ticker": p.get("ticker"),
                            "stage": p.get("stage"),
                            "gate": p.get("gate"),
                            "forecast": p.get("pullback_forecast"),
                        }
                        for p in sim.get("open_positions") or []
                        if isinstance(p, dict) and (p.get("pullback_forecast") or {}).get("scope") == "pullback_gate"
                    ],
                }
                for name, sim in (payload.get("strategies") or {}).items()
            },
        }
    except Exception as exc:
        log.exception("elite pullback forecast state failed")
        raise HTTPException(status_code=500, detail=f"elite pullback forecast failed: {type(exc).__name__}: {exc}")


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
