"""Real-trading dashboard API routes separated from paper/live dashboard state.

The real dashboard reuses the KINGMAKER UI, but every account/order/news/market/
candidate endpoint is served under /api/real/* and uses real-dashboard-specific
state files.  Public candle data is still fetched with the existing candle loader,
but it is exposed through /api/real/candles/* so the browser never calls /api/live/*.

Safety design:
- Real BUY/SELL intents are stored in real_dashboard_* files, not paper/live files.
- Real candidates/news/market/rulebook/universe state is stored separately.
- Direct live orders are disabled unless KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1.
- Secret values are never returned by connection diagnostics.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from engine.live.broker.base import BrokerError, OrderType
from engine.live.manual_buy_intent import atomic_write_json, read_json, utc_now_iso

ALPACA_LIVE_BASE_URL = "https://api.alpaca.markets"
ENV_PATH = Path.home() / "kingmaker" / ".env"
DIRECT_ORDER_ENV = "KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS"

REAL_BUY_CANDIDATES_PATH = Path("data/_system/real_dashboard_buy_candidates.json")
REAL_BUY_INTENT_PATH = Path("data/_system/real_dashboard_manual_buy_intent.json")
REAL_SELL_INTENT_PATH = Path("data/_system/real_dashboard_manual_sell_intent.json")
REAL_MARKET_STATE_PATH = Path("data/_system/real_dashboard_market_state.json")
LIVE_MARKET_STATE_PATH = Path("data/_system/market_state.json")
REAL_NEWS_STATE_PATH = Path("data/_system/real_dashboard_news_state.json")
REAL_RULEBOOKS_PATH = Path("data/_system/real_dashboard_rulebooks.json")
REAL_UNIVERSE_PATH = Path("data/_system/real_dashboard_universe.json")
REAL_TRADES_HISTORY_PATH = Path("data/_system/real_dashboard_trades_history.json")
LIVE_SLOTS_STATE_PATH = Path("data/_system/live_slots_state.json")
LIVE_SLOTS_EVENTS_PATH = Path("data/_system/live_slots_events.jsonl")

LIVE_KEY_NAMES = (
    "ALPACA_LIVE_API_KEY",
    "ALPACA_LIVE_KEY_ID",
    "ALPACA_REAL_API_KEY",
    "ALPACA_API_KEY_LIVE",
    "APCA_LIVE_API_KEY_ID",
)
LIVE_SECRET_NAMES = (
    "ALPACA_LIVE_SECRET_KEY",
    "ALPACA_LIVE_API_SECRET",
    "ALPACA_REAL_SECRET_KEY",
    "ALPACA_SECRET_KEY_LIVE",
    "APCA_LIVE_API_SECRET_KEY",
)
GENERIC_KEY_NAMES = ("ALPACA_API_KEY", "APCA_API_KEY_ID")
GENERIC_SECRET_NAMES = ("ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY")
LIVE_BASE_URL_NAMES = (
    "ALPACA_LIVE_BASE_URL",
    "ALPACA_REAL_BASE_URL",
    "ALPACA_BASE_URL_LIVE",
    "APCA_LIVE_API_BASE_URL",
)

_real_broker = None
_real_broker_error: str = ""
_real_broker_error_logged = False
_real_broker_config_cache: dict[str, Any] | None = None
_real_candle_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
_REAL_CANDLE_TTL_SEC = {
    "1m": 25,
    "2m": 35,
    "5m": 55,
    "15m": 90,
    "30m": 120,
    "60m": 180,
    "1h": 180,
    "1d": 300,
}


class RealBuyIntentRequest(BaseModel):
    candidate_id: str
    source: str = "real_dashboard"
    notional: float | None = None


class RealSellIntentRequest(BaseModel):
    ticker: str
    shares_requested: float | None = None
    source: str = "real_dashboard"


class RealSlotBuyRequest(BaseModel):
    slot: int | None = None
    candidate_id: str | None = None
    notional: float | None = None
    note: str = ""
    source: str = "dashboard_real_slots"


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


def _dotenv_values_safe() -> dict[str, Any]:
    try:
        if ENV_PATH.exists():
            return dict(dotenv_values(str(ENV_PATH)) or {})
    except Exception:
        pass
    return {}


def _lookup_secret(names: tuple[str, ...], env_file: dict[str, Any]) -> tuple[str, str, str]:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value, name, "process_env"
    for name in names:
        value = str(env_file.get(name) or "").strip()
        if value:
            return value, name, ".env"
    return "", "", ""


def _lookup_public(names: tuple[str, ...], env_file: dict[str, Any], default: str = "") -> tuple[str, str, str]:
    value, name, source = _lookup_secret(names, env_file)
    if value:
        return value, name, source
    return default, "default", "code_default"


def _real_connection_config(refresh: bool = False) -> dict[str, Any]:
    global _real_broker_config_cache
    if _real_broker_config_cache is not None and not refresh:
        return dict(_real_broker_config_cache)

    env_file = _dotenv_values_safe()
    api_key, key_name, key_location = _lookup_secret(LIVE_KEY_NAMES, env_file)
    secret_key, secret_name, secret_location = _lookup_secret(LIVE_SECRET_NAMES, env_file)
    credential_source = "live_specific"

    if not api_key:
        api_key, key_name, key_location = _lookup_secret(GENERIC_KEY_NAMES, env_file)
        if api_key:
            credential_source = "generic_fallback"
    if not secret_key:
        secret_key, secret_name, secret_location = _lookup_secret(GENERIC_SECRET_NAMES, env_file)
        if secret_key and credential_source != "live_specific":
            credential_source = "generic_fallback"
    if not api_key or not secret_key:
        credential_source = "missing"

    base_url, base_url_name, base_url_location = _lookup_public(LIVE_BASE_URL_NAMES, env_file, ALPACA_LIVE_BASE_URL)
    cfg = {
        "api_key": api_key,
        "secret_key": secret_key,
        "base_url": base_url or ALPACA_LIVE_BASE_URL,
        "key_name": key_name,
        "key_location": key_location,
        "secret_name": secret_name,
        "secret_location": secret_location,
        "base_url_name": base_url_name,
        "base_url_location": base_url_location,
        "credential_source": credential_source,
        "has_api_key": bool(api_key),
        "has_secret_key": bool(secret_key),
        "direct_orders_enabled": _direct_orders_enabled(),
    }
    _real_broker_config_cache = dict(cfg)
    return cfg


def _public_connection_config(refresh: bool = False) -> dict[str, Any]:
    cfg = _real_connection_config(refresh=refresh)
    return {
        "base_url": cfg.get("base_url"),
        "credential_source": cfg.get("credential_source"),
        "has_api_key": bool(cfg.get("has_api_key")),
        "has_secret_key": bool(cfg.get("has_secret_key")),
        "key_name": cfg.get("key_name") or "",
        "key_location": cfg.get("key_location") or "",
        "secret_name": cfg.get("secret_name") or "",
        "secret_location": cfg.get("secret_location") or "",
        "base_url_name": cfg.get("base_url_name") or "",
        "base_url_location": cfg.get("base_url_location") or "",
        "direct_orders_enabled": _direct_orders_enabled(),
        "direct_order_env": DIRECT_ORDER_ENV,
        "recommended_key_names": {
            "api_key": LIVE_KEY_NAMES[0],
            "secret_key": LIVE_SECRET_NAMES[0],
            "base_url": LIVE_BASE_URL_NAMES[0],
        },
    }


def _clear_real_broker_cache() -> None:
    global _real_broker, _real_broker_error, _real_broker_config_cache
    _real_broker = None
    _real_broker_error = ""
    _real_broker_config_cache = None


def _get_real_broker(refresh: bool = False):
    global _real_broker, _real_broker_error, _real_broker_error_logged
    if refresh:
        _clear_real_broker_cache()
    if _real_broker is not None:
        return _real_broker
    try:
        from engine.live.broker.alpaca import AlpacaBroker

        cfg = _real_connection_config(refresh=refresh)
        if not cfg.get("api_key") or not cfg.get("secret_key"):
            raise BrokerError("Alpaca live credentials missing; set ALPACA_LIVE_API_KEY and ALPACA_LIVE_SECRET_KEY")
        _real_broker = AlpacaBroker(
            api_key=str(cfg.get("api_key") or ""),
            secret_key=str(cfg.get("secret_key") or ""),
            base_url=str(cfg.get("base_url") or ALPACA_LIVE_BASE_URL),
            paper=False,
        )
        _real_broker_error = ""
        return _real_broker
    except Exception as exc:
        _real_broker_error = f"{type(exc).__name__}: {exc}"
        if not _real_broker_error_logged:
            _real_broker_error_logged = True
        return None


def _connection_hint(cfg_public: dict[str, Any], error: str = "") -> str:
    if not cfg_public.get("has_api_key") or not cfg_public.get("has_secret_key"):
        return "실거래 전용 키 ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY를 서버 환경 또는 .env에 추가해야 합니다."
    if cfg_public.get("credential_source") == "generic_fallback":
        return "현재 generic ALPACA_API_KEY/ALPACA_SECRET_KEY를 fallback으로 쓰고 있습니다. paper 키일 가능성이 있으니 live 전용 키 이름을 따로 넣는 것을 권장합니다."
    if "401" in str(error) or "not authorized" in str(error).lower():
        return "Alpaca live 인증이 거부되었습니다. live 계정용 key/secret인지, base_url이 https://api.alpaca.markets인지 확인해야 합니다."
    return "실거래 API 연결 상태를 확인하세요."


def _real_connection_status(*, refresh: bool = False, account_check: bool = True) -> dict[str, Any]:
    if refresh:
        _clear_real_broker_cache()
    cfg_public = _public_connection_config(refresh=refresh)
    broker = _get_real_broker(refresh=False)
    payload: dict[str, Any] = {
        "ok": False,
        "broker_mode": "alpaca_live",
        "connection": cfg_public,
        "direct_orders_enabled": _direct_orders_enabled(),
        "error": _real_broker_error,
        "account_check": "skipped" if not account_check else "not_run",
    }
    if broker is None:
        payload["hint"] = _connection_hint(cfg_public, _real_broker_error)
        return payload
    payload["broker_mode"] = getattr(broker, "mode", "alpaca_live")
    if not account_check:
        payload["ok"] = True
        payload["account_check"] = "skipped"
        return payload
    try:
        bal = broker.get_balance()
        payload.update(
            {
                "ok": True,
                "account_check": "passed",
                "account_source": "alpaca_live",
                "total_value": _safe_float(getattr(bal, "total_value_usd", getattr(bal, "total_value_krw", 0.0)), 0.0),
                "cash": _safe_float(getattr(bal, "cash_usd", getattr(bal, "cash_krw", 0.0)), 0.0),
                "holdings_count": len(list(getattr(bal, "holdings", []) or [])),
                "error": "",
            }
        )
    except Exception as exc:
        payload.update(
            {
                "ok": False,
                "account_check": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "hint": _connection_hint(cfg_public, f"{type(exc).__name__}: {exc}"),
            }
        )
    return payload


def _broker_unavailable_payload() -> dict[str, Any]:
    return {
        "ok": False,
        "account_source": "alpaca_live_unavailable",
        "broker_mode": "alpaca_live",
        "direct_orders_enabled": _direct_orders_enabled(),
        "connection": _public_connection_config(),
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


def _default_real_market_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "real_dashboard_market_state",
        "isolated": True,
        "updated_at": "",
        "regime": "neutral",
        "score": None,
        "vix_level": None,
        "risk_events": [],
        "benefit_events": [],
        "active_events": {},
        "sector_strength": {},
        "note": "실거래 대시보드 전용 시장상태 파일이 아직 없습니다.",
        "state_path": str(REAL_MARKET_STATE_PATH),
    }


def _market_state_effectively_empty(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or not data:
        return True
    return (
        data.get("score") is None
        and data.get("vix_level") is None
        and not data.get("risk_events")
        and not data.get("benefit_events")
        and not data.get("active_events")
        and not data.get("sector_strength")
    )


def _live_market_state_fallback() -> dict[str, Any]:
    live = read_json(LIVE_MARKET_STATE_PATH, {})
    if not isinstance(live, dict) or not live:
        return _default_real_market_state()
    out = dict(_default_real_market_state())
    out.update(live)
    out["source"] = "live_market_state_fallback"
    out["isolated"] = True
    out["state_path"] = str(REAL_MARKET_STATE_PATH)
    out["fallback_source_path"] = str(LIVE_MARKET_STATE_PATH)
    out["note"] = "real_dashboard_market_state.json이 비어 있어 live market_state.json을 읽기 전용 fallback으로 표시합니다."
    return out


def _real_market_state() -> dict[str, Any]:
    data = read_json(REAL_MARKET_STATE_PATH, {})
    if not isinstance(data, dict) or not data:
        return _live_market_state_fallback()
    out = dict(_default_real_market_state())
    out.update(data)
    out["source"] = data.get("source") or "real_dashboard_market_state"
    out["isolated"] = True
    out["state_path"] = str(REAL_MARKET_STATE_PATH)
    if _market_state_effectively_empty(out):
        fallback = _live_market_state_fallback()
        fallback["real_state_empty"] = True
        return fallback
    return out


def _default_real_news_state() -> dict[str, Any]:
    held = [str(p.get("ticker") or "").upper() for p in _real_positions_payload() if p.get("ticker")]
    entries = {
        t: {
            "ticker": t,
            "score": None,
            "risk_label": "missing",
            "missing": True,
            "stale": True,
            "article_count": 0,
            "source": "real_news_state_missing",
            "articles": [],
        }
        for t in sorted(set(held))
    }
    return {
        "sentiment": {
            "entries": entries,
            "meta": {
                "source": "real_dashboard_news_state",
                "isolated": True,
                "held_count": len(entries),
                "held_tickers": sorted(entries),
                "cache_count": 0,
                "cache_updated_at": "",
                "state_path": str(REAL_NEWS_STATE_PATH),
                "note": "실거래 대시보드 전용 뉴스 파일이 아직 없습니다.",
            },
        },
        "alerts": {},
    }


def _real_news_state() -> dict[str, Any]:
    data = read_json(REAL_NEWS_STATE_PATH, {})
    if not isinstance(data, dict) or not data:
        return _default_real_news_state()
    if "sentiment" not in data:
        entries = data.get("entries") if isinstance(data.get("entries"), dict) else {}
        data = {"sentiment": {"entries": entries, "meta": data.get("meta") or {}}, "alerts": data.get("alerts") or {}}
    out = dict(data)
    sentiment = out.get("sentiment") if isinstance(out.get("sentiment"), dict) else {}
    meta = sentiment.get("meta") if isinstance(sentiment.get("meta"), dict) else {}
    meta.update({"source": "real_dashboard_news_state", "isolated": True, "state_path": str(REAL_NEWS_STATE_PATH)})
    sentiment["meta"] = meta
    if not isinstance(sentiment.get("entries"), dict):
        sentiment["entries"] = {}
    out["sentiment"] = sentiment
    out.setdefault("alerts", {})
    return out


def _default_real_candidate_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "buy_mode": "real_isolated",
        "source": "real_dashboard_buy_candidates",
        "isolated": True,
        "trade_date": "",
        "updated_at": "",
        "manual_buy_enabled": True,
        "direct_orders_enabled": _direct_orders_enabled(),
        "order_intent_path": str(REAL_BUY_INTENT_PATH),
        "state_path": str(REAL_BUY_CANDIDATES_PATH),
        "connection": _public_connection_config(),
        "candidates": {},
        "note": "실거래 대시보드 전용 매수 후보 파일이 아직 없거나 비어 있습니다.",
    }


def _real_candidate_state(*, include_blocked: bool = False) -> dict[str, Any]:
    data = read_json(REAL_BUY_CANDIDATES_PATH, {})
    if not isinstance(data, dict) or not data:
        state = _default_real_candidate_state()
    else:
        state = dict(_default_real_candidate_state())
        state.update(data)
        if not isinstance(state.get("candidates"), dict):
            state["candidates"] = {}
    state["source"] = "real_dashboard_buy_candidates"
    state["isolated"] = True
    state["state_path"] = str(REAL_BUY_CANDIDATES_PATH)
    state["order_intent_path"] = str(REAL_BUY_INTENT_PATH)
    state["direct_orders_enabled"] = _direct_orders_enabled()
    state["connection"] = _public_connection_config()

    hidden = {"manual_executed", "auto_executed", "expired", "cancelled", "canceled"}
    if not include_blocked:
        hidden.add("blocked")
    candidates = {
        str(cid): dict(row)
        for cid, row in (state.get("candidates") or {}).items()
        if isinstance(row, dict) and str(row.get("status") or "pending") not in hidden
    }
    intents = _intent_state(REAL_BUY_INTENT_PATH, default_trade_date=str(state.get("trade_date") or ""))
    for intent_id, intent in (intents.get("intents") or {}).items():
        if not isinstance(intent, dict) or str(intent.get("status") or "") not in {"pending", "submitted"}:
            continue
        cid = str(intent.get("candidate_id") or "")
        if not cid or cid not in candidates:
            continue
        row = candidates[cid]
        row["status"] = "manual_requested"
        row["manual_intent_id"] = str(intent_id)
        row["manual_buy_enabled"] = False
        row["manual_requested_notional"] = intent.get("notional")
        row["manual_notional"] = intent.get("notional")
        row["notional_source"] = "real_dashboard_amount"
        row["action_label"] = "실거래 요청"
    state["candidates"] = candidates
    return state


def _candidate_for_real(candidate_id: str) -> dict[str, Any]:
    cid = str(candidate_id or "").strip()
    if not cid:
        raise ValueError("candidate_id required")
    state = _real_candidate_state(include_blocked=True)
    row = (state.get("candidates") or {}).get(cid)
    if not isinstance(row, dict):
        raise ValueError(f"real candidate not found or stale: {cid}")
    status = str(row.get("status") or "pending")
    if status not in {"pending", "manual_requested"}:
        raise ValueError(f"real candidate is not pending: {cid}")
    if row.get("manual_buy_enabled") is False:
        raise ValueError(f"real candidate manual buy disabled: {cid}")
    return row


def _order_dict(order: Any) -> dict[str, Any]:
    if hasattr(order, "to_dict"):
        return order.to_dict()
    return dict(order) if isinstance(order, dict) else {"raw": str(order)}


def _create_real_buy_intent(req: RealBuyIntentRequest) -> dict[str, Any]:
    candidate = _candidate_for_real(req.candidate_id)
    candidate_id = str(req.candidate_id or "").strip()
    ticker = str(candidate.get("ticker") or "").upper().strip()
    if not ticker:
        raise ValueError("real candidate ticker missing")
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
            "candidate_state_path": str(REAL_BUY_CANDIDATES_PATH),
            "connection": _public_connection_config(),
            "note": "real dashboard isolated candidate/intents; no paper/live state touched",
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
    broker = _get_real_broker()
    if broker is None:
        raise ValueError(f"real broker unavailable: {_real_broker_error}")
    positions = {str(p.get("ticker") or "").upper(): p for p in _real_positions_payload()}
    pos = positions.get(ticker)
    if not pos:
        raise ValueError("not held in real account")
    held_shares = _positive_float(pos.get("shares"), name="held shares")
    shares_value = held_shares if req.shares_requested is None else _positive_float(req.shares_requested, name="shares_requested")
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
        "connection": _public_connection_config(),
        "note": "real dashboard separated sell intent; no paper/live intent file touched",
    }
    if _direct_orders_enabled():
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


def _real_trades_history() -> dict[str, Any]:
    data = read_json(REAL_TRADES_HISTORY_PATH, {})
    if isinstance(data, dict) and data:
        return data
    return {
        "stats": {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "avg_win_pct": 0, "avg_loss_pct": 0},
        "trades": [],
        "account_source": "alpaca_live",
        "isolated": True,
        "state_path": str(REAL_TRADES_HISTORY_PATH),
        "note": "실거래 대시보드 전용 거래내역 파일이 아직 없습니다.",
    }


def _live_slots_state() -> dict[str, Any]:
    data = read_json(LIVE_SLOTS_STATE_PATH, {})
    return data if isinstance(data, dict) else {}


def _active_live_slot_held_ids(state: dict[str, Any]) -> set[str]:
    held = state.get("held_exclusions") if isinstance(state.get("held_exclusions"), dict) else {}
    out: set[str] = set()
    for cid, row in held.items():
        if isinstance(row, dict) and str(row.get("status") or "open").lower() in {"open", "held", "active"}:
            out.add(str(cid))
    return out


def _candidate_slot_to_dashboard(slot: dict[str, Any], idx: int) -> dict[str, Any]:
    if not isinstance(slot, dict) or not slot.get("candidate_id"):
        return {"slot": idx, "slot_no": idx, "empty": True, "status": "WAITING_FOR_SIGNAL", "slot_type": "buy_candidate"}
    price = _safe_float(slot.get("price"), None)
    return {
        "slot": int(slot.get("slot_no") or slot.get("slot") or idx),
        "slot_no": int(slot.get("slot_no") or slot.get("slot") or idx),
        "empty": False,
        "status": slot.get("status") or "FILLED",
        "slot_type": "buy_candidate",
        "candidate_id": slot.get("candidate_id"),
        "ticker": str(slot.get("ticker") or "").upper(),
        "stage": slot.get("stage"),
        "bucket": slot.get("bucket"),
        "rulebook_hash_short": slot.get("rulebook_hash_short"),
        "final_score": _safe_float(slot.get("final_score"), None),
        "raw_score": _safe_float(slot.get("raw_score"), None),
        "threshold": _safe_float(slot.get("threshold"), None),
        "ratio": _safe_float(slot.get("ratio"), None),
        "price": price,
        "current_price": price,
        "entry_price": None,
        "shares": 0,
        "pnl_pct": None,
        "vol_group": slot.get("vol_group"),
        "down_deprioritize": bool(slot.get("down_deprioritize")),
        "gate_status": slot.get("gate_status"),
        "entry_quality_allow": slot.get("entry_quality_allow"),
        "entry_quality_label": slot.get("entry_quality_label"),
        "entry_quality_score": slot.get("entry_quality_score"),
        "entry_quality_primary_reason": slot.get("entry_quality_primary_reason"),
        "win_rate": _safe_float(slot.get("win_rate"), None),
        "expectancy_pct": _safe_float(slot.get("expectancy_pct"), None),
        "mdd_pct": _safe_float(slot.get("mdd_pct"), None),
        "fitness": _safe_float(slot.get("fitness"), None),
        "trade_count": _safe_float(slot.get("trade_count"), None),
        "first_signal_at": slot.get("first_signal_at"),
        "first_signal_price": _safe_float(slot.get("first_signal_price"), None),
        "first_final_score": _safe_float(slot.get("first_final_score"), None),
        "last_seen_at": slot.get("last_seen_at"),
        "max_holding_days": slot.get("max_holding_days"),
        "exit_strategy_name": slot.get("exit_strategy"),
        "stop_loss_atr": _safe_float(slot.get("stop_loss_atr"), None),
        "take_profit_atr": _safe_float(slot.get("take_profit_atr"), None),
        "trailing_atr": _safe_float(slot.get("trailing_atr"), None),
        "market_score": _safe_float(slot.get("market_score"), None),
        "sector_score": _safe_float(slot.get("sector_score"), None),
        "vix_level": _safe_float(slot.get("vix_level"), None),
        "reasons": slot.get("reasons") or [],
        "exit_strategy": "BUY_CANDIDATE",
        "direction": str(slot.get("vol_group") or ""),
        "max_holding_days": None,
        "target_price": None,
        "stop_price": None,
        "trailing_stop": None,
        "manual_buy_enabled": True,
        "action_label": "매수 선택",
    }


def _real_candidate_slots_payload(max_slots: int = 8) -> list[dict[str, Any]]:
    state = _live_slots_state()
    slots = state.get("slots") if isinstance(state.get("slots"), list) else []
    limit = max(1, int(max_slots or 8))
    return [_candidate_slot_to_dashboard(slots[i] if i < len(slots) else {}, i + 1) for i in range(limit)]


def _find_live_slot_candidate_raw(state: dict[str, Any], candidate_id: str) -> dict[str, Any] | None:
    cid = str(candidate_id or "").strip()
    if not cid:
        return None
    for source in ("slots", "candidate_pool", "waitlist"):
        rows = state.get(source) if isinstance(state.get(source), list) else []
        for row in rows:
            if isinstance(row, dict) and str(row.get("candidate_id") or "") == cid:
                return dict(row)
    return None


def _real_buy_preview_dashboard_payload(candidate_id: str, notional: float | None = None) -> dict[str, Any]:
    """Read-only dashboard preview for a hypothetical candidate buy.

    This does not write live_slots_state, held_exclusions, positions.json, or
    broker orders. It only returns the dashboard arrays as they would look after
    the selected candidate becomes a holding and is removed from the buy list.
    """
    state = _live_slots_state()
    cid = str(candidate_id or "").strip()
    cand = _find_live_slot_candidate_raw(state, cid)
    if not cand:
        raise ValueError("candidate_id not found in live slot state")
    price = _safe_float(cand.get("price") or cand.get("current_price"), None)
    if price is None or price <= 0:
        raise ValueError("candidate price unavailable")
    amount = _safe_float(notional, None)
    if amount is None or amount <= 0:
        amount = 100.0
    shares = amount / price if price > 0 else 0.0
    ticker = str(cand.get("ticker") or "").upper().strip()
    holdings = _real_positions_payload()
    news_state = _real_news_state()
    news_entries = (((news_state.get("sentiment") or {}).get("entries") or {}) if isinstance(news_state, dict) else {})
    news_entry = news_entries.get(ticker) if isinstance(news_entries, dict) else None
    virtual_holding = {
        "slot": 1,
        "slot_no": 1,
        "empty": False,
        "preview": True,
        "preview_mode": True,
        "slot_type": "virtual_holding_preview",
        "status": "PREVIEW_ONLY_NO_ORDER",
        "ticker": ticker,
        "candidate_id": cid,
        "entry_price": round(price, 6),
        "current_price": round(price, 6),
        "shares": round(shares, 6),
        "pnl_pct": 0.0,
        "unrealized_pnl": 0.0,
        "invested": round(amount, 6),
        "final_score": _safe_float(cand.get("final_score"), None),
        "stage": cand.get("stage"),
        "bucket": cand.get("bucket"),
        "vol_group": cand.get("vol_group"),
        "gate_status": cand.get("gate_status"),
        "rulebook_hash_short": cand.get("rulebook_hash_short"),
        "first_signal_at": cand.get("first_signal_at"),
        "first_signal_price": _safe_float(cand.get("first_signal_price"), None),
        "win_rate": _safe_float(cand.get("win_rate"), None),
        "expectancy_pct": _safe_float(cand.get("expectancy_pct"), None),
        "mdd_pct": _safe_float(cand.get("mdd_pct"), None),
        "trade_count": _safe_float(cand.get("trade_count"), None),
        "news_score": _safe_float((news_entry or {}).get("score"), None),
        "news_risk_label": (news_entry or {}).get("risk_label"),
        "news_article_count": int((news_entry or {}).get("article_count") or 0),
        "news_fresh": bool((news_entry or {}).get("fresh")),
        "news_entry": news_entry or {},
        "exit_strategy": "S2 no-TP PREVIEW",
        "exit_strategy_name": cand.get("exit_strategy"),
        "max_holding_days": cand.get("max_holding_days"),
        "take_profit_enabled": False,
        "target_price": None,
        "stop_price": None,
        "trailing_stop": None,
        "stop_loss_atr": _safe_float(cand.get("stop_loss_atr"), None),
        "trailing_atr": _safe_float(cand.get("trailing_atr"), None),
        "take_profit_atr": _safe_float(cand.get("take_profit_atr"), None),
        "preview_note": "화면 확인용 가상 보유입니다. 실제 주문/상태변경 없음.",
    }
    preview_holdings = [virtual_holding]
    for idx, row in enumerate(holdings, start=2):
        h = dict(row)
        h["slot"] = idx
        h["slot_no"] = idx
        preview_holdings.append(h)

    held = _active_live_slot_held_ids(state)
    pool = state.get("candidate_pool") if isinstance(state.get("candidate_pool"), list) else []
    filtered = []
    for row in pool:
        if not isinstance(row, dict):
            continue
        rcid = str(row.get("candidate_id") or "")
        if rcid == cid or rcid in held:
            continue
        filtered.append(dict(row))
    filtered = _sort_live_slot_pool(filtered)
    slots = []
    for idx in range(8):
        raw = dict(filtered[idx]) if idx < len(filtered) else {}
        if raw:
            raw["slot_no"] = idx + 1
            raw["slot"] = idx + 1
            raw["status"] = "PREVIEW_AFTER_BUY"
        slots.append(_candidate_slot_to_dashboard(raw, idx + 1))

    return {
        "ok": True,
        "preview": True,
        "candidate_id": cid,
        "ticker": ticker,
        "notional": round(amount, 6),
        "price": round(price, 6),
        "shares": round(shares, 6),
        "holdings": preview_holdings,
        "candidate_slots": slots,
        "note": "read-only preview; no broker order, no held_exclusions write, no positions write",
    }


def _sort_live_slot_pool(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]):
        return (
            int(row.get("priority_group") or 0),
            -float(_safe_float(row.get("final_score"), 0.0) or 0.0),
            str(row.get("ticker") or ""),
            str(row.get("candidate_id") or ""),
        )
    return sorted([r for r in pool if isinstance(r, dict)], key=key)


def _rebuild_live_slots_state(state: dict[str, Any], reason: str) -> dict[str, Any]:
    held = _active_live_slot_held_ids(state)
    pool = state.get("candidate_pool") if isinstance(state.get("candidate_pool"), list) else []
    pool = _sort_live_slot_pool([r for r in pool if str(r.get("candidate_id") or "") not in held])
    slots: list[dict[str, Any]] = []
    for idx in range(8):
        if idx < len(pool):
            row = dict(pool[idx])
            row["slot_no"] = idx + 1
            row["slot"] = idx + 1
            row["status"] = "FILLED"
            slots.append(row)
        else:
            slots.append({"slot_no": idx + 1, "slot": idx + 1, "status": "WAITING_FOR_SIGNAL"})
    state["slots"] = slots
    state["current_slots"] = slots
    state["slots_filled"] = sum(1 for r in slots if r.get("candidate_id"))
    state["waitlist"] = [dict(r, wait_rank=i + 1) for i, r in enumerate(pool[8:])]
    state["waitlist_count"] = len(state["waitlist"])
    state["held_count"] = len(held)
    state["last_rebuild_reason"] = reason
    state["updated_at"] = utc_now_iso()
    return state


def _append_live_slot_event(row: dict[str, Any]) -> None:
    try:
        LIVE_SLOTS_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        with LIVE_SLOTS_EVENTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(_json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _mark_real_slot_manual_buy(req: RealSlotBuyRequest) -> dict[str, Any]:
    state = _live_slots_state()
    if not state:
        raise ValueError(f"live slot state missing: {LIVE_SLOTS_STATE_PATH}")
    selected: dict[str, Any] | None = None
    if req.slot is not None:
        for row in state.get("slots") or []:
            if int(row.get("slot_no") or row.get("slot") or 0) == int(req.slot) and row.get("candidate_id"):
                selected = dict(row)
                break
    if selected is None and req.candidate_id:
        cid = str(req.candidate_id)
        for row in (state.get("slots") or []) + (state.get("waitlist") or []) + (state.get("candidate_pool") or []):
            if str(row.get("candidate_id") or "") == cid:
                selected = dict(row)
                break
        if selected is None:
            selected = {"candidate_id": cid}
    if not selected or not selected.get("candidate_id"):
        raise ValueError("slot candidate not found")
    cid = str(selected.get("candidate_id"))
    event = {
        "time": utc_now_iso(),
        "candidate_id": cid,
        "ticker": selected.get("ticker"),
        "slot_no": selected.get("slot_no") or selected.get("slot"),
        "note": req.note or "dashboard-real manual buy",
        "source": req.source or "dashboard_real_slots",
        "notional": req.notional,
        "status": "open",
        "snapshot": selected,
    }
    state.setdefault("held_exclusions", {})[cid] = event
    state.setdefault("manual_buy_events", []).append(event)
    state = _rebuild_live_slots_state(state, "dashboard_real_slot_buy")
    atomic_write_json(LIVE_SLOTS_STATE_PATH, state)
    _append_live_slot_event({"event": "DASHBOARD_REAL_SLOT_BUY", **event})
    return {"ok": True, "event": event, "slots": _real_candidate_slots_payload(8), "state_path": str(LIVE_SLOTS_STATE_PATH)}


def _real_slot_overlay_js() -> str:
    return r"""
(function(){
  if(window.KM_REAL_SLOT_OVERLAY_INSTALLED) return;
  window.KM_REAL_SLOT_OVERLAY_INSTALLED = true;
  window.candidateSlotData = [];
  function esc(v){return String(v??'').replace(/[&<>\"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch];});}
  function num(v){const n=Number(v); return Number.isFinite(n)?n:null;}
  function fmt(n,d=2){n=num(n); return n==null?'—':n.toFixed(d);}
  function money(n){n=num(n); return n==null?'':n.toLocaleString(undefined,{maximumFractionDigits:0});}
  function tag(v){return v?`<span class="tag">${esc(v)}</span>`:'';}
  function kstDateObj(value){
    if(value==null || value==='') return null;
    let d;
    if(typeof value==='number') d=new Date(value*1000);
    else d=new Date(value);
    if(!Number.isFinite(d.getTime())) return null;
    return d;
  }
  function kstTime(value, opts={}){
    const d=kstDateObj(value); if(!d) return '—';
    const mode=opts.mode||'time';
    const base={timeZone:'Asia/Seoul', hour12:false};
    if(mode==='dateTime') return new Intl.DateTimeFormat('ko-KR',{...base, month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'}).format(d).replace(/\. /g,'/').replace('.','');
    return new Intl.DateTimeFormat('ko-KR',{...base, hour:'2-digit', minute:'2-digit'}).format(d);
  }
  function kstAgeText(value){
    const d=kstDateObj(value); if(!d) return '—';
    const mins=Math.max(0, Math.floor((Date.now()-d.getTime())/60000));
    if(mins<1) return '방금';
    if(mins<60) return `${mins}분 전`;
    const h=Math.floor(mins/60), m=mins%60;
    if(h<24) return `${h}시간 ${m}분 전`;
    return `${Math.floor(h/24)}일 ${h%24}시간 전`;
  }
  function ensureRealUpdateBadge(){
    let el=document.getElementById('real-update-badge');
    if(el) return el;
    el=document.createElement('span');
    el.id='real-update-badge';
    el.style.cssText='border:1px solid rgba(59,130,246,.35);background:rgba(59,130,246,.12);color:#bfdbfe;border-radius:999px;padding:4px 10px;font-size:11px;font-weight:900;font-variant-numeric:tabular-nums;white-space:nowrap;';
    const host=document.querySelector('.topbar .regime') || document.querySelector('.topbar') || document.body;
    host.appendChild(el);
    return el;
  }
  function updateRealUpdateBadge(extra={}){
    const el=ensureRealUpdateBadge();
    const candidateUpdated=extra.candidateUpdated || window._lastCandidateUpdatedAt || null;
    const chartLatest=extra.chartLatest || window._lastCandidateChartLatest || null;
    const chartFetch=extra.chartFetch || window._lastCandidateChartFetch || null;
    const parts=[];
    if(candidateUpdated) parts.push(`후보 ${kstTime(candidateUpdated)}`);
    if(chartLatest) parts.push(`1m봉 ${kstTime(chartLatest)} (${kstAgeText(chartLatest)})`);
    if(chartFetch) parts.push(`화면 ${kstTime(chartFetch)}`);
    el.textContent = parts.length ? `최근 업데이트 · ${parts.join(' · ')}` : `최근 업데이트 · ${kstTime(new Date().toISOString())}`;
  }
  function openRealAutoSettingsModal(){
    let modal=document.getElementById('real-auto-settings-modal');
    if(!modal){
      modal=document.createElement('div');
      modal.id='real-auto-settings-modal';
      modal.style.cssText='position:fixed;inset:0;background:rgba(3,7,18,.72);backdrop-filter:blur(6px);z-index:99999;display:flex;align-items:center;justify-content:center;padding:22px;';
      modal.innerHTML=`<div style="width:min(1180px,96vw);height:min(860px,92vh);background:#0b111c;border:1px solid rgba(245,196,81,.35);border-radius:18px;box-shadow:0 24px 80px rgba(0,0,0,.55);display:flex;flex-direction:column;overflow:hidden;"><div style="height:48px;display:flex;align-items:center;justify-content:space-between;padding:0 14px;border-bottom:1px solid #263246;background:#111a2b;"><div style="font-size:14px;font-weight:950;color:#e7eefb;">⚙ S2 자동매매 설정</div><button id="real-auto-settings-close" style="background:#263246;color:#e7eefb;border:0;border-radius:10px;padding:8px 12px;font-weight:900;cursor:pointer;">닫기 ✕</button></div><iframe src="/dashboard-real/auto-settings" style="width:100%;height:100%;border:0;background:#0b111c;"></iframe></div>`;
      modal.addEventListener('click', (e)=>{ if(e.target===modal) closeRealAutoSettingsModal(); });
      document.body.appendChild(modal);
      const closeBtn=document.getElementById('real-auto-settings-close');
      if(closeBtn) closeBtn.onclick=closeRealAutoSettingsModal;
    }
    modal.style.display='flex';
    document.body.style.overflow='hidden';
    const onEsc=(e)=>{ if(e.key==='Escape'){ closeRealAutoSettingsModal(); document.removeEventListener('keydown', onEsc); } };
    document.addEventListener('keydown', onEsc);
  }
  function closeRealAutoSettingsModal(){
    const modal=document.getElementById('real-auto-settings-modal');
    if(modal) modal.style.display='none';
    document.body.style.overflow='';
  }
  function ensureRealAutoSettingsNavButton(){
    const nav=document.querySelector('.topbar .nav');
    if(!nav) return null;
    let btn=document.getElementById('real-auto-settings-nav-btn');
    if(!btn){
      btn=document.createElement('button');
      btn.id='real-auto-settings-nav-btn';
      btn.type='button';
      btn.textContent='⚙ 설정';
      btn.title='S2 자동매매 설정';
      btn.onclick=(e)=>{ e.preventDefault(); openRealAutoSettingsModal(); };
      btn.style.cssText='border-color:rgba(245,196,81,.55);color:#f5c451;font-weight:900;';
    }
    const buttons=[...nav.querySelectorAll('button')];
    const historyBtn=buttons.find(b=>String(b.textContent||'').replace(/\s/g,'').includes('거래내역'));
    const fsBtn=document.getElementById('fs-btn');
    if(historyBtn && historyBtn.nextSibling!==btn) historyBtn.insertAdjacentElement('afterend', btn);
    else if(fsBtn && fsBtn.previousSibling!==btn) nav.insertBefore(btn, fsBtn);
    else if(!btn.parentElement) nav.appendChild(btn);
    return btn;
  }
  function byCid(cid){return (window.candidateSlotData||[]).find(x=>String(x.candidate_id||'')===String(cid||''));}
  function defaultNotional(){
    try{
      const acct=window.accountData||window.acctData||{};
      const cash=num(acct.cash);
      if(cash!=null && cash>0) return Math.max(1, Math.floor(Math.min(100, cash/8)));
    }catch(e){}
    return 100;
  }
  function elapsedSince(iso){
    if(!iso) return '—';
    const t=new Date(iso).getTime();
    if(!Number.isFinite(t)) return '—';
    const mins=Math.max(0, Math.floor((Date.now()-t)/60000));
    const h=Math.floor(mins/60), m=mins%60;
    if(h>=24){ const d=Math.floor(h/24); return `+${d}일 ${h%24}시간`; }
    return `+${h}시간 ${m}분`;
  }
  function priceDeltaText(cur, first){
    cur=num(cur); first=num(first);
    if(cur==null || first==null || first<=0) return {txt:'—', cls:''};
    const d=cur-first, p=d/first*100;
    return {txt:`${d>=0?'+':''}$${d.toFixed(2)} / ${p>=0?'+':''}${p.toFixed(2)}%`, cls:d>=0?'univ-pos':'univ-neg'};
  }
  function reasonList(s){
    const rs=(s.reasons||[]).slice(0,5);
    if(!rs.length && s.entry_quality_primary_reason) rs.push(s.entry_quality_primary_reason);
    return rs.length ? rs.map(r=>`<span class="tag">${esc(r)}</span>`).join(' ') : '<span style="color:var(--dim)">—</span>';
  }
  function injectRealCandidateStyles(){
    if(document.getElementById('real-candidate-ticket-style')) return;
    const css=`
    .real-order-ticket{margin-top:auto;background:linear-gradient(180deg,rgba(59,130,246,.12),rgba(15,23,42,.38));border:1px solid rgba(59,130,246,.30);border-radius:13px;padding:10px 12px;box-shadow:0 10px 28px rgba(0,0,0,.20) inset;}
    .real-order-ticket .ticket-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-size:11px;color:var(--dim);font-weight:800;letter-spacing:.2px;}
    .real-order-ticket .ticket-row{display:grid;grid-template-columns:minmax(140px,190px) repeat(3,62px) minmax(126px,160px);gap:8px;align-items:center;}
    .real-order-ticket .amount-wrap{display:flex;align-items:center;gap:4px;background:#070b12;border:1px solid rgba(148,163,184,.22);border-radius:10px;padding:0 8px;height:38px;}
    .real-order-ticket .amount-prefix{font-size:13px;color:#93c5fd;font-weight:900;}
    .real-order-ticket .slot-buy-amount{width:100%;height:32px;border:0;background:transparent;color:var(--txt);font-weight:900;font-size:15px;font-variant-numeric:tabular-nums;outline:none;padding:0;}
    .real-order-ticket .quick-amt{height:38px;border-radius:10px;border:1px solid rgba(148,163,184,.20);background:rgba(15,23,42,.72);color:#cbd5e1;font-size:12px;font-weight:900;cursor:pointer;}
    .real-order-ticket .quick-amt:hover{border-color:#60a5fa;color:white;background:rgba(37,99,235,.24);}
    .real-order-ticket .slot-buy-real{height:38px;border:0;border-radius:10px;background:linear-gradient(135deg,#2563eb,#7c3aed);color:white;font-size:12px;font-weight:950;letter-spacing:.2px;cursor:pointer;box-shadow:0 8px 20px rgba(37,99,235,.28);}
    .real-order-ticket .slot-buy-real:hover{filter:brightness(1.08);transform:translateY(-1px);}
    .real-order-ticket .slot-buy-real:disabled{opacity:.55;cursor:not-allowed;transform:none;filter:none;}
    .real-order-ticket .ticket-foot{margin-top:7px;font-size:11px;color:var(--dim);display:flex;justify-content:space-between;gap:8px;}
    .real-order-ticket .share-est{color:#c9d4e5;font-weight:800;}
    .real-candidate-row{grid-template-columns:minmax(360px,46%) minmax(0,1fr)!important;min-height:340px!important;}
    .real-candidate-chart-wrap{height:100%;min-height:310px;display:flex;flex-direction:column;gap:6px;}
    .real-candidate-mini-chart{height:100%!important;min-height:286px;flex:1;}
    .real-candidate-chart-meta{font-size:11px;color:var(--dim);font-variant-numeric:tabular-nums;display:flex;justify-content:space-between;gap:8px;}
    .real-signal-vline{position:absolute;top:0;bottom:0;width:2px;background:#f5c451;box-shadow:0 0 10px rgba(245,196,81,.75);z-index:18;pointer-events:none;}
    .real-signal-label{position:absolute;top:8px;transform:translateX(-50%);z-index:19;background:rgba(245,196,81,.16);border:1px solid rgba(245,196,81,.65);color:#fde68a;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:900;white-space:nowrap;pointer-events:none;font-variant-numeric:tabular-nums;}
    @media(max-width:900px){.real-candidate-row{grid-template-columns:1fr!important}.real-candidate-chart-wrap{min-height:260px}.real-candidate-mini-chart{min-height:236px}.real-order-ticket .ticket-row{grid-template-columns:minmax(120px,1fr) repeat(3,56px);}.real-order-ticket .slot-buy-real{grid-column:1/-1;}}
    `;
    const el=document.createElement('style'); el.id='real-candidate-ticket-style'; el.textContent=css; document.head.appendChild(el);
  }
  function ticketHtml(s, opts={}){
    const cid=String(s.candidate_id||'');
    const slot=esc(s.slot||s.slot_no||'');
    const price=num(s.price ?? s.current_price);
    const amount=opts.amount || defaultNotional();
    const shares=(price&&amount)?(amount/price):null;
    const idSuffix=esc(opts.idSuffix||cid.replace(/[^A-Za-z0-9_-]/g,'_'));
    return `<div class="real-order-ticket" onclick="event.stopPropagation()">
      <div class="ticket-head"><span>실매수 금액</span><span class="share-est" id="share-est-${idSuffix}">예상 ${shares==null?'—':shares.toFixed(3)}주</span></div>
      <div class="ticket-row">
        <label class="amount-wrap"><span class="amount-prefix">$</span><input class="slot-buy-amount" data-candidate-id="${esc(cid)}" data-price="${esc(price||'')}" data-est-id="share-est-${idSuffix}" type="number" min="1" step="50" value="${amount}" /></label>
        <button class="quick-amt" data-amount="100" data-candidate-id="${esc(cid)}">$100</button>
        <button class="quick-amt" data-amount="250" data-candidate-id="${esc(cid)}">$250</button>
        <button class="quick-amt" data-amount="500" data-candidate-id="${esc(cid)}">$500</button>
        <button class="slot-preview-real" data-candidate-id="${esc(cid)}" data-slot="${slot}" style="border:1px solid rgba(59,130,246,.55);background:#13233a;color:#bfdbfe;">매수 후 대시보드 미리보기</button>
        <button class="slot-buy-real" data-candidate-id="${esc(cid)}" data-slot="${slot}">매수 후보 선택</button>
      </div>
      <div class="ticket-foot"><span>현재가 기준 예상 수량</span><span>실제 주문 전 최종 확인</span></div>
    </div>`;
  }
  function previewBannerHtml(p){
    return `<div id="real-buy-preview-banner" style="background:linear-gradient(135deg,rgba(59,130,246,.18),rgba(245,196,81,.12));border:1px solid rgba(59,130,246,.45);border-radius:14px;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <div><div style="font-size:15px;font-weight:950;color:#e7eefb;">📦 가상 매수 프리뷰 중 · ${esc(p.ticker||'')}</div><div style="font-size:12px;color:var(--dim);">보유 슬롯/후보 슬롯이 매수 후처럼 임시 재구성되었습니다. 실제 주문 · 후보 제외 · 상태 저장은 없습니다.</div></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;"><button id="open-buy-preview-detail" style="background:#2563eb;color:white;border:0;border-radius:10px;padding:9px 12px;font-weight:900;cursor:pointer;">가상 보유 상세 열기</button><button id="clear-buy-preview" style="background:#263246;color:#e7eefb;border:0;border-radius:10px;padding:9px 12px;font-weight:900;cursor:pointer;">프리뷰 해제</button></div>
    </div>`;
  }
  function updateBuyPreviewBanner(){
    const stack=document.getElementById('real-home-stack');
    if(!stack) return;
    let banner=document.getElementById('real-buy-preview-banner');
    if(window._realBuyDashboardPreview){
      if(!banner){
        const wrap=document.createElement('div');
        wrap.innerHTML=previewBannerHtml(window._realBuyDashboardPreview);
        banner=wrap.firstElementChild;
        stack.insertBefore(banner, stack.firstChild);
      }else{
        banner.outerHTML=previewBannerHtml(window._realBuyDashboardPreview);
        banner=document.getElementById('real-buy-preview-banner');
      }
      const detailBtn=document.getElementById('open-buy-preview-detail');
      if(detailBtn) detailBtn.onclick=()=>openPreviewHoldingDetail(window._realBuyDashboardPreview && window._realBuyDashboardPreview.ticker);
      const btn=document.getElementById('clear-buy-preview');
      if(btn) btn.onclick=clearBuyDashboardPreview;
    }else if(banner){
      banner.remove();
    }
  }
  async function clearBuyDashboardPreview(){
    window._realBuyDashboardPreview=null;
    if(typeof toast==='function') toast('프리뷰 해제', '실제 대시보드 상태로 다시 불러옵니다.', 'good');
    await loadCandidateSlots(true);
    if(typeof oldLoadSlots === 'function') await oldLoadSlots();
    renderRealHoldingSlots();
    updateBuyPreviewBanner();
  }
  async function applyBuyDashboardPreview(cid, amount){
    const r=await fetch(`${API}/api/real/buy_preview_dashboard?candidate_id=${encodeURIComponent(cid)}&notional=${encodeURIComponent(amount||100)}`, {cache:'no-store'});
    const p=await r.json().catch(()=>({}));
    if(!r.ok || p.ok===false) throw new Error(p.detail||p.reason||`HTTP ${r.status}`);
    window._realBuyDashboardPreview=p;
    window.slotData=p.holdings||[];
    window.candidateSlotData=p.candidate_slots||[];
    document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
    const home=document.getElementById('page-home'); if(home) home.classList.add('active');
    document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
    const nav=document.querySelectorAll('.nav button')[0]; if(nav) nav.classList.add('active');
    arrangeRealHome();
    renderRealHoldingSlots();
    renderCandidateSlots();
    updateBuyPreviewBanner();
    if(typeof toast==='function') toast('가상 매수 프리뷰 적용', `${p.ticker} · $${money(p.notional)} · 실제 주문 없음`, 'good');
  }
  async function handlePreview(cid){
    const input=document.querySelector(`.slot-buy-amount[data-candidate-id="${CSS.escape(cid)}"]`);
    const amount=num(input && input.value) || defaultNotional();
    try{ await applyBuyDashboardPreview(cid, amount); }
    catch(e){ if(typeof toast==='function') toast('프리뷰 실패', String(e.message||e), 'warn'); else alert(String(e.message||e)); }
  }
  function updateShareEstimate(input){
    const amount=num(input && input.value);
    const price=num(input && input.dataset.price);
    const el=input && document.getElementById(input.dataset.estId||'');
    if(!el) return;
    if(amount==null || price==null || price<=0){ el.textContent='예상 —'; return; }
    el.textContent=`예상 ${(amount/price).toFixed(3)}주`;
  }
  function bindOrderTicketControls(){
    document.querySelectorAll('.slot-buy-amount[data-est-id]').forEach(input=>{
      if(input.dataset.boundEst==='1') return;
      input.dataset.boundEst='1';
      input.addEventListener('input',()=>updateShareEstimate(input));
      input.addEventListener('click',ev=>ev.stopPropagation());
      input.addEventListener('keydown',ev=>{ ev.stopPropagation(); if(ev.key==='Enter'){ const cid=input.dataset.candidateId; const btn=document.querySelector(`.slot-buy-real[data-candidate-id="${CSS.escape(cid)}"]`); if(btn) btn.click(); } });
      updateShareEstimate(input);
    });
    document.querySelectorAll('.quick-amt[data-candidate-id]').forEach(btn=>{
      if(btn.dataset.boundQuick==='1') return;
      btn.dataset.boundQuick='1';
      btn.onclick=function(ev){
        ev.stopPropagation();
        const cid=btn.dataset.candidateId;
        const scope=btn.closest('.real-order-ticket') || document;
        const input=scope.querySelector(`.slot-buy-amount[data-candidate-id="${CSS.escape(cid)}"]`);
        if(input){ input.value=btn.dataset.amount; updateShareEstimate(input); input.focus(); }
      };
    });
  }
  function candidateSignature(rows){
    return (rows||[]).map(s=>[s.candidate_id,s.price,s.current_price,s.final_score,s.first_signal_at,s.win_rate,s.expectancy_pct,s.mdd_pct].join('|')).join('||');
  }
  function timeLabel(t){
    if(t==null || t==='') return '—';
    if(typeof t==='number') return new Date(t*1000).toLocaleString();
    return String(t);
  }
  function nowTimeLabel(){ return new Date().toLocaleTimeString(); }
  function updateCandidateDynamicText(){
    for(const s of (window.candidateSlotData||[])){
      if(!s || s.empty || !s.candidate_id) continue;
      const safeCid=String(s.candidate_id).replace(/[^A-Za-z0-9_-]/g,'_');
      const elapsed=document.getElementById(`cand-elapsed-${safeCid}`);
      if(elapsed) elapsed.textContent=elapsedSince(s.first_signal_at);
      const deltaEl=document.getElementById(`cand-delta-${safeCid}`);
      if(deltaEl){ const d=priceDeltaText(s.current_price ?? s.price, s.first_signal_price); deltaEl.textContent=d.txt; deltaEl.className=d.cls; }
    }
  }
  function candidateSlotCard(s){
    if(!s || s.empty) return `<div class="mslot empty" style="cursor:default;">후보 ${esc((s&&s.slot)||'')}<br>대기중</div>`;
    const eq=s.entry_quality_label || (s.entry_quality_allow?'ALLOW':'CHECK');
    const eqCls=s.entry_quality_allow===true?'univ-pos':(s.entry_quality_allow===false?'univ-neg':'');
    const deprio=s.down_deprioritize?'<span class="tag" style="border-color:#f59e0b;color:#fbbf24;">DOWN 후순위</span>':'';
    const cid=String(s.candidate_id||'');
    const safeCid=cid.replace(/[^A-Za-z0-9_-]/g,'_');
    const chartId=`cand-mini-chart-${safeCid}`;
    const delta=priceDeltaText(s.current_price ?? s.price, s.first_signal_price);
    return `<div class="mslot real-buy-slot real-candidate-row" data-candidate-id="${esc(cid)}" style="display:grid;gap:14px;align-items:stretch;padding:14px;cursor:pointer;" onclick="openRealCandidateDetail('${esc(cid)}')">
      <div class="real-candidate-chart-wrap">
        <div id="${chartId}" class="real-candidate-mini-chart" style="border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#0b1019;"></div>
        <div id="cand-chart-meta-${safeCid}" class="real-candidate-chart-meta"><span>1m 차트 대기</span><span>KST · 서버 TTL 25초</span></div>
      </div>
      <div style="display:flex;flex-direction:column;gap:8px;min-width:0;">
        <div class="mslot-top"><span class="mslot-tk">${esc(s.ticker)}</span><span class="mslot-pnl" style="color:var(--up)">S ${fmt(s.final_score,2)}</span></div>
        <div class="mslot-sub">현재 ${fmt(s.price ?? s.current_price,2)} · 최초신호 ${fmt(s.first_signal_price,2)} · <span id="cand-delta-${safeCid}" class="${delta.cls}">${delta.txt}</span></div>
        <div class="mslot-sub">최초 신호 후 <b id="cand-elapsed-${safeCid}" style="color:var(--txt)">${elapsedSince(s.first_signal_at)}</b> · 기준 ${fmt(s.threshold,2)} · ratio ${fmt(s.ratio,2)}</div>
        <div class="mslot-sub">${tag(s.vol_group)} ${tag(s.stage)} ${tag(s.gate_status)} ${deprio}</div>
        <div class="mslot-sub ${eqCls}">EQ ${esc(eq)}${s.entry_quality_score!=null?' · Q'+fmt(s.entry_quality_score,0):''}</div>
        <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:2px;">
          <div class="rb-stat"><div class="v" style="color:var(--up)">${fmt(s.win_rate,1)}%</div><div class="l">승률</div></div>
          <div class="rb-stat"><div class="v" style="color:var(--up)">${fmt(s.expectancy_pct,2)}%</div><div class="l">기대값</div></div>
          <div class="rb-stat"><div class="v" style="color:var(--down)">${fmt(s.mdd_pct,2)}%</div><div class="l">MDD</div></div>
          <div class="rb-stat"><div class="v">${s.trade_count==null?'—':fmt(s.trade_count,0)}</div><div class="l">거래수</div></div>
        </div>
        <div class="mslot-sub" style="line-height:1.7;"><b style="color:var(--txt)">진입 사유</b><br>${reasonList(s)}</div>
        ${ticketHtml(s)}
      </div>
    </div>`;
  }
  async function drawCandidateMiniCharts(force=false){
    window._realCandMiniCharts = window._realCandMiniCharts || {};
    const rows=(window.candidateSlotData||[]).filter(s=>s && !s.empty);
    const activeIds=new Set();
    for(const s of rows){
      const cid=String(s.candidate_id||'');
      const safeCid=cid.replace(/[^A-Za-z0-9_-]/g,'_');
      const id=`cand-mini-chart-${safeCid}`;
      activeIds.add(id);
      const el=document.getElementById(id);
      const meta=document.getElementById(`cand-chart-meta-${safeCid}`);
      const ticker=String(s.ticker||'').toUpperCase();
      if(!el || !ticker || !window.LightweightCharts) continue;
      const cached=window._realCandMiniCharts[id];
      const ttlMs=25000;
      if(cached && !force && Date.now()-(cached.lastFetch||0)<ttlMs){
        try{ cached.chart.resize(Math.max(el.clientWidth,280), Math.max(el.clientHeight||0, 286)); }catch(e){}
        if(meta) meta.innerHTML=`<span>1m 최신봉 ${esc(cached.latestLabel||'—')} KST</span><span>차트 캐시 ${Math.max(0,Math.round((Date.now()-(cached.lastFetch||0))/1000))}초</span>`;
        updateRealUpdateBadge({chartLatest: cached.latestRaw, chartFetch: cached.lastFetch ? new Date(cached.lastFetch).toISOString() : null});
        continue;
      }
      try{
        const r=await fetch(`${API}/api/real/candles/${ticker}?interval=1m`);
        const candles=await r.json();
        if(!Array.isArray(candles) || !candles.length){ el.innerHTML='<div class="loading">분봉 없음</div>'; continue; }
        const use=candles.slice(-96);
        const h=Math.max(el.clientHeight||0, 286);
        let entry=window._realCandMiniCharts[id];
        if(!entry || !entry.chart || !entry.ser){
          const chart=LightweightCharts.createChart(el,{layout:{background:{color:'#0b1019'},textColor:'#5f6e85'},grid:{vertLines:{color:'#151d2b'},horzLines:{color:'#151d2b'}},localization:{timeFormatter:(time)=>kstTime(time,{mode:'dateTime'})},timeScale:{borderColor:'#1c2535',timeVisible:true,secondsVisible:false,tickMarkFormatter:(time)=>kstTime(time)},rightPriceScale:{borderColor:'#1c2535'},width:Math.max(el.clientWidth,280),height:h});
          const ser=chart.addCandlestickSeries({upColor:'#26d07c',downColor:'#ff4d6a',wickUpColor:'#26d07c',wickDownColor:'#ff4d6a',borderVisible:false});
          entry={chart,ser,lines:[]};
          window._realCandMiniCharts[id]=entry;
        }else{
          entry.chart.resize(Math.max(el.clientWidth,280), h);
          if(Array.isArray(entry.lines)){
            for(const line of entry.lines){ try{ entry.ser.removePriceLine(line); }catch(e){} }
          }
          entry.lines=[];
        }
        entry.ser.setData(use);
        if(s.first_signal_price){ entry.lines.push(entry.ser.createPriceLine({price:Number(s.first_signal_price),color:'#3b82f6',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'최초'})); }
        if(s.price || s.current_price){ entry.lines.push(entry.ser.createPriceLine({price:Number(s.price ?? s.current_price),color:'#c9d4e5',lineWidth:1,lineStyle:0,axisLabelVisible:true,title:'현재'})); }
        entry.chart.timeScale().fitContent();
        const latest=use[use.length-1] && use[use.length-1].time;
        entry.latestRaw=latest;
        entry.latestLabel=kstTime(latest, {mode:'dateTime'});
        entry.lastFetch=Date.now();
        window._lastCandidateChartLatest = latest;
        window._lastCandidateChartFetch = new Date().toISOString();
        if(meta) meta.innerHTML=`<span>1m 최신봉 ${esc(entry.latestLabel||'—')} KST · ${kstAgeText(latest)}</span><span>갱신 ${kstTime(window._lastCandidateChartFetch)} KST · TTL 25초</span>`;
        updateRealUpdateBadge({chartLatest: latest, chartFetch: window._lastCandidateChartFetch});
      }catch(e){ el.innerHTML='<div class="loading">차트 오류</div>'; }
    }
    for(const [id, entry] of Object.entries(window._realCandMiniCharts||{})){
      if(!activeIds.has(id)){
        try{ entry.chart.remove(); }catch(e){}
        delete window._realCandMiniCharts[id];
      }
    }
  }
  function renderCandidateSlots(){
    injectRealCandidateStyles();
    const html=(window.candidateSlotData||[]).map(candidateSlotCard).join('');
    const mini=document.getElementById('real-candidate-mini-slots');
    const full=document.getElementById('real-candidate-slots-full');
    [mini, full].forEach(el=>{ if(el){ el.style.display='grid'; el.style.gridTemplateColumns='1fr'; el.style.gap='12px'; el.innerHTML=html || '<div class="loading">후보 없음</div>'; } });
    const meta=document.getElementById('real-candidate-meta');
    if(meta){
      const filled=(window.candidateSlotData||[]).filter(x=>x && !x.empty).length;
      meta.textContent=`${filled}/8`;
    }
    bindOrderTicketControls();
    document.querySelectorAll('.slot-preview-real[data-candidate-id]').forEach(btn=>{
      btn.onclick=function(ev){ev.stopPropagation(); handlePreview(btn.dataset.candidateId);};
    });
    document.querySelectorAll('.slot-buy-real[data-candidate-id]').forEach(btn=>{
      btn.onclick=function(ev){ev.stopPropagation(); handleBuy(btn.dataset.candidateId, Number(btn.dataset.slot||0));};
    });
    setTimeout(drawCandidateMiniCharts, 50);
  }
  async function loadCandidateSlots(forceRender=false){
    if(window._realBuyDashboardPreview) return;
    let next=[];
    try{
      const r=await fetch(`${API}/api/real/candidate_slots`, {cache:'no-store'});
      next=await r.json();
    }catch(e){
      window.candidateSlotData=[];
      const msg='<div class="loading">후보 슬롯 API 연결 실패</div>';
      const mini=document.getElementById('real-candidate-mini-slots');
      const full=document.getElementById('real-candidate-slots-full');
      if(mini) mini.innerHTML=msg;
      if(full) full.innerHTML=msg;
      return;
    }
    const sig=candidateSignature(next);
    window.candidateSlotData=next;
    window._lastCandidateUpdatedAt = (next||[]).map(x=>x && (x.last_seen_at || x.first_signal_at)).filter(Boolean).sort().pop() || new Date().toISOString();
    updateRealUpdateBadge({candidateUpdated: window._lastCandidateUpdatedAt});
    if(forceRender || sig !== window._lastCandidateSlotSignature){
      window._lastCandidateSlotSignature=sig;
      renderCandidateSlots();
    }else{
      updateCandidateDynamicText();
      bindOrderTicketControls();
      drawCandidateMiniCharts(false);
    }
  }
  window.loadCandidateSlots = loadCandidateSlots;
  function arrangeRealHome(){
    const home=document.getElementById('page-home');
    const grid=home && home.querySelector('.home-grid');
    const hold=document.getElementById('mini-slots') && document.getElementById('mini-slots').parentElement;
    const cand=document.getElementById('candidates-panel');
    const market=document.getElementById('home-events') && document.getElementById('home-events').parentElement;
    if(!home || !hold || !cand || !market) return;
    let stack=document.getElementById('real-home-stack');
    if(!stack){
      stack=document.createElement('div');
      stack.id='real-home-stack';
      stack.style.display='flex';
      stack.style.flexDirection='column';
      stack.style.gap='16px';
      stack.style.marginTop='0';
      const equity=document.getElementById('equity-chart');
      const equityPanel=equity ? equity.closest('.panel') : null;
      if(equityPanel && equityPanel.parentElement) equityPanel.parentElement.insertBefore(stack, equityPanel.nextSibling);
      else home.appendChild(stack);
    }
    const holdTitle=hold.querySelector('h3');
    if(holdTitle) holdTitle.textContent='📦 보유 슬롯';
    const candSummary=cand.querySelector('summary span:first-child');
    if(candSummary) candSummary.textContent='🛒 매수 대기 후보 슬롯 (8)';
    cand.open=true;
    ensureRealAutoSettingsNavButton();
    if(hold.parentElement!==stack) stack.appendChild(hold);
    if(cand.parentElement!==stack) stack.appendChild(cand);
    if(market.parentElement!==stack) stack.appendChild(market);
    if(grid && grid.parentElement) grid.remove();
  }
  function previewHoldingByTicker(ticker){
    const tk=String(ticker||'').toUpperCase();
    return (window.slotData||[]).find(x=>x && x.preview && String(x.ticker||'').toUpperCase()===tk);
  }
  function holdingEmptyCard(slot){
    return `<div class="mslot empty" style="min-height:250px;border:1px dashed rgba(148,163,184,.22);display:flex;align-items:center;justify-content:center;flex-direction:column;gap:8px;">
      <div style="font-size:20px;font-weight:900;color:#64748b;">빈 보유 슬롯 ${slot}</div>
      <div style="font-size:12px;color:var(--dim);">프리뷰용 빈 칸</div>
    </div>`;
  }
  function clearPreviewExitLines(){
    try{
      if(window._previewExitLines && typeof series!=='undefined'){
        for(const line of window._previewExitLines){ try{ series.removePriceLine(line); }catch(e){} }
      }
    }catch(e){}
    window._previewExitLines=[];
  }
  function previewPlanDefaults(s){
    if(!s._previewExitPlan){
      const entry=num(s.entry_price)||num(s.current_price)||0;
      const stopPct=3.0;
      const takePct=5.0;
      s._previewExitPlan={
        stop_loss_pct: stopPct,
        take_profit_pct: takePct,
        stop_loss_price: entry>0 ? entry*(1-stopPct/100) : null,
        take_profit_price: entry>0 ? entry*(1+takePct/100) : null,
      };
    }
    return s._previewExitPlan;
  }
  function drawPreviewExitLines(s){
    clearPreviewExitLines();
    if(!s || typeof series==='undefined') return;
    const plan=previewPlanDefaults(s);
    const entry=num(s.entry_price);
    const current=num(s.current_price);
    const stop=num(plan.stop_loss_price);
    const take=num(plan.take_profit_price);
    window._previewExitLines=[];
    const add=(title, price, color, width=1, style=2)=>{
      if(price==null || price<=0) return;
      try{ window._previewExitLines.push(series.createPriceLine({price:Number(price),color,lineWidth:width,lineStyle:style,axisLabelVisible:true,title})); }catch(e){}
    };
    add('진입', entry, '#3b82f6', 2, 0);
    add('현재', current, '#c9d4e5', 1, 0);
    add('손절', stop, '#ff4d6a', 2, 2);
    add('익절 참고(no-TP)', take, '#26d07c', 2, 2);
  }
  function syncPreviewExitInputs(s, changed){
    const entry=num(s.entry_price)||0;
    const plan=previewPlanDefaults(s);
    const stopPriceEl=document.getElementById('preview-stop-price');
    const stopPctEl=document.getElementById('preview-stop-pct');
    const takePriceEl=document.getElementById('preview-take-price');
    const takePctEl=document.getElementById('preview-take-pct');
    if(!entry || !stopPriceEl || !stopPctEl || !takePriceEl || !takePctEl) return;
    if(changed==='stop_price'){
      const v=num(stopPriceEl.value); if(v!=null){ plan.stop_loss_price=v; plan.stop_loss_pct=Math.max(0,(1-v/entry)*100); }
    }else if(changed==='stop_pct'){
      const v=num(stopPctEl.value); if(v!=null){ plan.stop_loss_pct=Math.abs(v); plan.stop_loss_price=entry*(1-Math.abs(v)/100); }
    }else if(changed==='take_price'){
      const v=num(takePriceEl.value); if(v!=null){ plan.take_profit_price=v; plan.take_profit_pct=(v/entry-1)*100; }
    }else if(changed==='take_pct'){
      const v=num(takePctEl.value); if(v!=null){ plan.take_profit_pct=Math.abs(v); plan.take_profit_price=entry*(1+Math.abs(v)/100); }
    }
    stopPriceEl.value=plan.stop_loss_price==null?'':Number(plan.stop_loss_price).toFixed(2);
    stopPctEl.value=plan.stop_loss_pct==null?'':Number(plan.stop_loss_pct).toFixed(2);
    takePriceEl.value=plan.take_profit_price==null?'':Number(plan.take_profit_price).toFixed(2);
    takePctEl.value=plan.take_profit_pct==null?'':Number(plan.take_profit_pct).toFixed(2);
    drawPreviewExitLines(s);
  }
  function previewExitControlHtml(s){
    const plan=previewPlanDefaults(s);
    return `<div id="preview-exit-panel" style="margin:12px 0;padding:14px;background:linear-gradient(135deg,rgba(15,23,42,.95),rgba(30,41,59,.82));border:1px solid rgba(59,130,246,.55);border-radius:14px;box-shadow:0 10px 28px rgba(37,99,235,.12);">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px;">
        <b style="color:#e7eefb;font-size:15px;">차트 손절/익절 참고선</b>
        <span style="font-size:11px;color:#fbbf24;">프리뷰 전용 · 저장 없음 · S2 자동익절 OFF</span>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;">
        <label style="font-size:11px;color:var(--dim);">손절가<br><input id="preview-stop-price" type="number" step="0.01" value="${plan.stop_loss_price==null?'':Number(plan.stop_loss_price).toFixed(2)}" style="width:100%;background:#0d1524;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px;"></label>
        <label style="font-size:11px;color:var(--dim);">손절 %<br><input id="preview-stop-pct" type="number" step="0.01" value="${plan.stop_loss_pct==null?'':Number(plan.stop_loss_pct).toFixed(2)}" style="width:100%;background:#0d1524;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px;"></label>
        <label style="font-size:11px;color:var(--dim);">익절가 참고선<br><input id="preview-take-price" type="number" step="0.01" value="${plan.take_profit_price==null?'':Number(plan.take_profit_price).toFixed(2)}" style="width:100%;background:#0d1524;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px;"></label>
        <label style="font-size:11px;color:var(--dim);">익절 % 참고선<br><input id="preview-take-pct" type="number" step="0.01" value="${plan.take_profit_pct==null?'':Number(plan.take_profit_pct).toFixed(2)}" style="width:100%;background:#0d1524;color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:8px;"></label>
      </div>
      <div style="font-size:11px;color:var(--dim);margin-top:8px;line-height:1.6;">가격이나 %를 바꾸면 서로 자동 환산되고 차트 선이 즉시 다시 그려집니다. 익절선은 S2 no-TP 상태에서 참고용입니다.</div>
    </div>`;
  }
  function bindPreviewExitControls(s){
    const bind=(id, kind)=>{ const el=document.getElementById(id); if(el) el.oninput=()=>syncPreviewExitInputs(s, kind); };
    bind('preview-stop-price','stop_price');
    bind('preview-stop-pct','stop_pct');
    bind('preview-take-price','take_price');
    bind('preview-take-pct','take_pct');
    syncPreviewExitInputs(s, 'init');
  }
  async function openPreviewHoldingDetail(ticker){
    const s=previewHoldingByTicker(ticker);
    if(!s) return;
    const tk=String(s.ticker||'').toUpperCase();
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    document.getElementById('page-slots').classList.add('active');
    document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
    const nav=document.querySelectorAll('.nav button')[1]; if(nav) nav.classList.add('active');
    document.getElementById('slot-list-view').style.display='none';
    document.getElementById('slot-detail-view').style.display='block';
    if(window.chart) chart.resize(document.getElementById('chart').clientWidth, 440);
    window._activeRealCandidate=null;
    window._activePreviewHolding=s;
    if(typeof window.drawChart==='function') await window.drawChart(tk, '1m');
    if(typeof _activeChart !== 'undefined') _activeChart = {type:'preview_holding', ticker:tk, interval:'1m'};
    renderPreviewHoldingDetail(s);
  }
  window.openPreviewHoldingDetail = openPreviewHoldingDetail;
  function renderPreviewHoldingDetail(s){
    const ticker=String(s.ticker||'').toUpperCase();
    const invested=num(s.invested) || ((num(s.entry_price)||0)*(num(s.shares)||0));
    const news=s.news_entry||{};
    const title=document.getElementById('detail-title');
    if(title) title.textContent=`${ticker} — 가상 보유 상세 프리뷰`;
    const kv=document.getElementById('detail-kv');
    if(kv){
      kv.innerHTML=`
        <div class="kv"><span>상태</span><span style="color:#bfdbfe;font-weight:900;">가상 PREVIEW · 실제 보유 아님</span></div>
        <div class="kv"><span>매수가</span><span>$${money(num(s.entry_price)||0)}</span></div>
        <div class="kv"><span>현재가</span><span>$${money(num(s.current_price)||0)}</span></div>
        <div class="kv"><span>예상 수량</span><span>${fmt(s.shares,6)}주</span></div>
        <div class="kv"><span>투입금</span><span>$${money(invested)}</span></div>
        <div class="kv"><span>평가손익</span><span style="color:var(--up)">0.00%</span></div>
        <div class="kv"><span>S2 take_profit</span><span style="color:#fbbf24;font-weight:900;">OFF · no-TP</span></div>
        <div class="kv"><span>청산 방식</span><span>${esc(s.exit_strategy_name||s.exit_strategy||'S2')}</span></div>
        <div class="kv"><span>손절 ATR</span><span>${fmt(s.stop_loss_atr,2)}</span></div>
        <div class="kv"><span>트레일 ATR</span><span>${fmt(s.trailing_atr,2)}</span></div>
        <div class="kv"><span>최대 보유</span><span>${esc(s.max_holding_days||'—')}일</span></div>
        <div class="kv"><span>후보 score</span><span style="color:var(--up)">${fmt(s.final_score,3)}</span></div>
        <div class="kv"><span>뉴스 위험</span><span>${s.news_score==null?'—':fmt(s.news_score,3)} · ${esc(s.news_risk_label||'—')} · 기사 ${esc(s.news_article_count||0)}개</span></div>
        <div class="kv"><span>candidate</span><span style="font-size:10px;word-break:break-all">${esc(s.candidate_id||'')}</span></div>`;
    }
    const comm=document.getElementById('commentary');
    if(comm){
      const articles=(news.articles||[]).slice(0,2).map(a=>`<li>${esc(a.title||a.summary||'뉴스 제목 없음')}</li>`).join('');
      comm.innerHTML=`${previewExitControlHtml(s)}<div class="comment"><b>가상 매수 후 보유 상세</b><br>${ticker}를 $${money(invested)} 매수했다고 가정한 화면입니다. 실제 주문/상태 저장은 없습니다.</div>
        <div class="comment" style="margin-top:10px;"><b>S2 no-TP 청산</b><br>익절 target은 끄고, stop_loss · trailing · sell_omen · timeout 기준만 확인하는 보유 상태입니다.</div>
        <div class="comment" style="margin-top:10px;"><b>뉴스</b><br>score ${s.news_score==null?'—':fmt(s.news_score,3)} · ${esc(s.news_risk_label||'—')} · fresh ${s.news_fresh?'true':'false'}${articles?`<ul style="margin:8px 0 0 18px;color:var(--dim);">${articles}</ul>`:''}</div>`;
    }
    const omen=document.getElementById('sellomen-strip');
    if(omen) omen.innerHTML=`<div class="sellomen-metrics"><div class="rb-stat"><div class="v">${fmt(s.win_rate,1)}%</div><div class="l">후보 승률</div></div><div class="rb-stat"><div class="v">${fmt(s.expectancy_pct,2)}%</div><div class="l">기대값</div></div><div class="rb-stat"><div class="v">${fmt(s.mdd_pct,2)}%</div><div class="l">MDD</div></div><div class="rb-stat"><div class="v">${s.news_score==null?'—':fmt(s.news_score,3)}</div><div class="l">뉴스위험</div></div></div>`;
    bindPreviewExitControls(s);
    setTimeout(()=>drawPreviewExitLines(s),80);
  }
  function holdingSlotCard(s, prefix, totalCount=1){
    if(!s || s.empty) return holdingEmptyCard((s&&s.slot)||'');
    const ticker=esc(String(s.ticker||'').toUpperCase());
    const pnl=num(s.pnl_pct);
    const pnlTxt=pnl==null?'—':`${pnl>=0?'+':''}${pnl.toFixed(2)}%`;
    const pnlColor=pnl==null?'var(--dim)':(pnl>=0?'var(--up)':'var(--down)');
    const invested=num(s.invested) || ((num(s.entry_price)||0)*(num(s.shares)||0));
    const chartId=`${prefix}-holding-chart-${ticker}`;
    const preview=!!s.preview;
    const click=preview?`data-preview-ticker="${ticker}"`:`onclick="openDetail('${ticker}')"`;
    const badge=preview?'<span class="tag" style="border-color:#3b82f6;color:#bfdbfe;margin-left:8px;">가상 PREVIEW</span>':'';
    const newsScore=s.news_score==null?'—':fmt(s.news_score,3);
    const newsLabel=s.news_risk_label||'—';
    const ring=preview?'border-color:rgba(59,130,246,.62);box-shadow:0 0 0 1px rgba(59,130,246,.24) inset,0 12px 34px rgba(37,99,235,.12);':'';
    const wide=Number(totalCount||1)<=1;
    const layout=wide?'display:grid;grid-template-columns:minmax(300px,42%) minmax(0,1fr);align-items:stretch;':'display:flex;flex-direction:column;';
    const chartHeight=wide?'245px':'155px';
    return `<div class="mslot real-holding-slot" ${click} style="min-height:${wide?'310':'325'}px;padding:14px;${layout}gap:14px;${ring}">
      <div style="display:flex;flex-direction:column;gap:10px;min-width:0;">
        <div class="mslot-top"><span class="mslot-tk">${ticker}${badge}</span><span class="mslot-pnl" style="color:${pnlColor}">${pnlTxt}</span></div>
        <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;">
          <div class="rb-stat"><div class="v">$${money(num(s.entry_price)||0)}</div><div class="l">매수가</div></div>
          <div class="rb-stat"><div class="v">${fmt(s.shares,4)}</div><div class="l">수량</div></div>
          <div class="rb-stat"><div class="v">$${money(invested)}</div><div class="l">투입</div></div>
        </div>
        <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;">
          <div class="rb-stat"><div class="v" style="color:#fbbf24;">no-TP</div><div class="l">S2 익절</div></div>
          <div class="rb-stat"><div class="v">${esc(s.max_holding_days||'—')}일</div><div class="l">최대 보유</div></div>
          <div class="rb-stat"><div class="v">${newsScore}</div><div class="l">뉴스위험</div></div>
          <div class="rb-stat"><div class="v">${fmt(s.final_score,2)}</div><div class="l">후보점수</div></div>
        </div>
        <div class="mslot-sub" style="line-height:1.7;">${esc(s.stage||'')} · ${esc(s.vol_group||'')} · ${esc(s.exit_strategy_name||s.exit_strategy||'')}<br>뉴스 ${newsScore} · ${esc(newsLabel)} · 기사 ${esc(s.news_article_count||0)}개</div>
        <div class="mslot-sub" style="margin-top:auto;color:var(--accent);font-weight:900;">클릭 → ${preview?'가상 보유 상세':'보유 상세'}</div>
      </div>
      <div id="${chartId}" class="real-holding-mini-chart" style="height:${chartHeight};border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#0b1019;min-width:0;"></div>
    </div>`;
  }
  async function drawHoldingMiniCharts(prefix, holdings){
    window._realHoldMiniCharts = window._realHoldMiniCharts || {};
    for(const s of holdings){
      const ticker=String(s.ticker||'').toUpperCase();
      if(!ticker) continue;
      const id=`${prefix}-holding-chart-${ticker}`;
      const el=document.getElementById(id);
      if(!el || !window.LightweightCharts) continue;
      try{ if(window._realHoldMiniCharts[id]){ window._realHoldMiniCharts[id].remove(); delete window._realHoldMiniCharts[id]; } }catch(e){}
      try{
        const r=await fetch(`${API}/api/real/candles/${ticker}?interval=1d`);
        const candles=await r.json();
        if(!Array.isArray(candles) || !candles.length){ el.innerHTML='<div class="loading">일봉 없음</div>'; continue; }
        const chart=LightweightCharts.createChart(el,{layout:{background:{color:'#0b1019'},textColor:'#5f6e85'},grid:{vertLines:{color:'#151d2b'},horzLines:{color:'#151d2b'}},timeScale:{borderColor:'#1c2535'},rightPriceScale:{borderColor:'#1c2535'},width:Math.max(el.clientWidth,240),height:165});
        const ser=chart.addCandlestickSeries({upColor:'#26d07c',downColor:'#ff4d6a',wickUpColor:'#26d07c',wickDownColor:'#ff4d6a',borderVisible:false});
        ser.setData(candles.slice(-90));
        chart.timeScale().fitContent();
        window._realHoldMiniCharts[id]=chart;
      }catch(e){ el.innerHTML='<div class="loading">차트 오류</div>'; }
    }
  }
  function renderRealHoldingSlots(){
    arrangeRealHome();
    updateBuyPreviewBanner();
    const holdings=(window.slotData||[]).filter(s=>s && !s.empty);
    let display=holdings.slice();
    const n=display.length;
    const gridCols=n<=1?'1fr':(n===2?'repeat(2, minmax(0, 1fr))':'repeat(auto-fit, minmax(280px, 1fr))');
    const empty='<div class="panel" style="padding:26px;text-align:center;color:var(--dim);">현재 보유 슬롯 없음<br><span style="font-size:11px;">빈 슬롯 8칸은 표시하지 않습니다.</span></div>';
    [['mini-slots','home'],['slots-full','full']].forEach(([id,prefix])=>{
      const el=document.getElementById(id);
      if(!el) return;
      el.style.display='grid';
      el.style.gridTemplateColumns=gridCols;
      el.style.gap='12px';
      el.innerHTML=display.length ? display.map(s=>holdingSlotCard(s,prefix,display.length)).join('') : empty;
      el.querySelectorAll('.real-holding-slot[data-preview-ticker]').forEach(card=>{
        card.onclick=function(ev){ ev.stopPropagation(); openPreviewHoldingDetail(card.dataset.previewTicker); };
      });
      if(holdings.length) setTimeout(()=>drawHoldingMiniCharts(prefix, holdings), 50);
    });
  }
  const oldRenderSlots = window.renderSlots;
  window.renderSlots = function(){ renderRealHoldingSlots(); };
  async function markSlotBuy(cid, notional, slot){
    const r=await fetch(`${API}/api/real/live_slot_buy`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({candidate_id:cid, slot:slot||null, notional:notional, source:'dashboard-real-detail', note:`dashboard-real notional ${notional}`})
    });
    const d=await r.json().catch(()=>({}));
    if(!r.ok || d.ok===false) throw new Error(d.detail||d.reason||`HTTP ${r.status}`);
    return d;
  }
  async function handleBuy(cid, slot){
    const s=byCid(cid) || {};
    const input=document.getElementById('real-slot-buy-amount') || document.querySelector(`.slot-buy-amount[data-candidate-id="${CSS.escape(cid)}"]`);
    const amount=num(input && input.value);
    if(amount==null || amount<=0){
      if(typeof toast==='function') toast('매수 금액 필요', '얼마치 살지 달러 금액을 입력하세요', 'warn'); else alert('매수 금액을 입력하세요');
      if(input) input.focus();
      return;
    }
    const ticker=s.ticker||cid;
    if(!confirm(`${ticker} 후보를 $${money(amount)} 매수 대상으로 선택하고 후보 슬롯에서 제외할까요?\n\n이 버튼은 후보 상태 기록/제외용입니다. 실제 주문은 사용하는 매매 화면/브로커에서 별도로 확인하세요.`)) return;
    document.querySelectorAll(`.slot-buy-real[data-candidate-id="${CSS.escape(cid)}"]`).forEach(b=>{b.disabled=true; b.textContent='처리 중…';});
    try{
      await markSlotBuy(cid, amount, slot);
      if(typeof toast==='function') toast('매수 후보 선택 완료', `${ticker} · $${money(amount)} · 후보 슬롯 재갱신`, 'good');
      await loadCandidateSlots();
      if(typeof closeDetail==='function') closeDetail();
    }catch(e){
      if(typeof toast==='function') toast('매수 후보 처리 실패', String(e.message||e), 'warn'); else alert(String(e.message||e));
      document.querySelectorAll(`.slot-buy-real[data-candidate-id="${CSS.escape(cid)}"]`).forEach(b=>{b.disabled=false; b.textContent='매수 선택';});
    }
  }
  function candidateSignalEpochSec(s){
    const t=new Date(s && s.first_signal_at || '').getTime();
    if(!Number.isFinite(t)) return null;
    return Math.floor(t/1000);
  }
  function findSignalCandle(candles, s, interval){
    if(!Array.isArray(candles) || !candles.length || !s || !s.first_signal_at) return {hit:null, reason:'no_candles'};
    const epoch=candidateSignalEpochSec(s);
    if(epoch==null) return {hit:null, reason:'bad_signal_time'};
    const intraday = !['1d','1wk','1mo'].includes(interval);
    const latest=candles[candles.length-1];
    if(intraday){
      const latestEpoch=Number(latest && latest.time);
      if(Number.isFinite(latestEpoch) && latestEpoch < epoch){
        return {hit:null, reason:'signal_after_latest_bar', latest};
      }
      const hit=candles.find(c=>Number(c.time)>=epoch) || null;
      return {hit, reason:hit?'ok':'not_found', latest};
    }
    const day=new Date(epoch*1000).toISOString().slice(0,10);
    if(String(latest && latest.time || '') < day){
      return {hit:null, reason:'signal_after_latest_bar', latest};
    }
    const hit=candles.find(c=>String(c.time)>=day) || null;
    return {hit, reason:hit?'ok':'not_found', latest};
  }
  function clearRealDetailSignalOverlay(){
    try{
      if(window._realDetailSignalLines && typeof series!=='undefined'){
        for(const line of window._realDetailSignalLines){ try{ series.removePriceLine(line); }catch(e){} }
      }
    }catch(e){}
    window._realDetailSignalLines=[];
    try{
      if(window._realDetailSignalUnsub && typeof chart!=='undefined'){
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(window._realDetailSignalUnsub);
      }
    }catch(e){}
    window._realDetailSignalUnsub=null;
    document.querySelectorAll('.real-signal-vline,.real-signal-label').forEach(el=>el.remove());
  }
  function renderRealDetailSignalOverlay(hit, s){
    const wrap=document.getElementById('chart');
    if(!wrap || !hit || typeof chart==='undefined') return;
    try{
      if(window._realDetailSignalUnsub && typeof chart!=='undefined'){
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(window._realDetailSignalUnsub);
      }
    }catch(e){}
    window._realDetailSignalUnsub=null;
    document.querySelectorAll('.real-signal-vline,.real-signal-label').forEach(el=>el.remove());
    const line=document.createElement('div');
    line.className='real-signal-vline';
    const label=document.createElement('div');
    label.className='real-signal-label';
    label.textContent=`최초 신호 ${kstTime(s.first_signal_at,{mode:'dateTime'})} KST`;
    wrap.appendChild(line);
    wrap.appendChild(label);
    const update=()=>{
      try{
        const x=chart.timeScale().timeToCoordinate(hit.time);
        const w=wrap.clientWidth || 0;
        if(x==null || !Number.isFinite(x) || x < 0 || x > w){
          line.style.display='none'; label.style.display='none'; return;
        }
        line.style.display='block'; label.style.display='block';
        const lx=Math.min(Math.max(Math.round(x), 95), Math.max(95, w-95));
        line.style.left=`${Math.round(x)}px`;
        label.style.left=`${lx}px`;
      }catch(e){}
    };
    update();
    setTimeout(update,80);
    try{ chart.timeScale().subscribeVisibleLogicalRangeChange(update); window._realDetailSignalUnsub=update; }catch(e){}
  }
  async function applyRealCandidateDetailSignal(ticker, interval){
    const s=window._activeRealCandidate;
    if(!s || String(s.ticker||'').toUpperCase()!==String(ticker||'').toUpperCase()){
      clearRealDetailSignalOverlay();
      return;
    }
    let candles=[];
    try{ candles=await (await fetch(`${API}/api/real/candles/${ticker}?interval=${interval}`, {cache:'no-store'})).json(); }catch(e){}
    const found=findSignalCandle(candles, s, interval);
    const hit=found && found.hit;
    clearRealDetailSignalOverlay();
    try{
      const intraday = !['1d','1wk','1mo'].includes(interval);
      chart.applyOptions({
        localization:{timeFormatter:(time)=>kstTime(time,{mode:'dateTime'})},
        timeScale:{timeVisible:intraday, secondsVisible:false, borderColor:'#1c2535', tickMarkFormatter:(time)=>kstTime(time)}
      });
    }catch(e){}
    try{ series.setMarkers(hit ? [{time:hit.time, position:'aboveBar', color:'#f5c451', shape:'circle', text:'최초 신호'}] : []); }catch(e){}
    window._realDetailSignalLines=[];
    try{
      if(s.first_signal_price){
        window._realDetailSignalLines.push(series.createPriceLine({price:Number(s.first_signal_price),color:'#f5c451',lineWidth:2,lineStyle:2,axisLabelVisible:true,title:'최초 신호가'}));
      }
      if(s.price || s.current_price){
        window._realDetailSignalLines.push(series.createPriceLine({price:Number(s.price ?? s.current_price),color:'#c9d4e5',lineWidth:1,lineStyle:0,axisLabelVisible:true,title:'현재'}));
      }
    }catch(e){}
    if(hit) renderRealDetailSignalOverlay(hit, s);
    const comm=document.getElementById('commentary');
    if(comm){
      let note=document.getElementById('real-signal-detail-note');
      if(!note){
        note=document.createElement('div');
        note.id='real-signal-detail-note';
        note.className='comment';
        note.style.marginTop='10px';
        comm.appendChild(note);
      }
      if(hit){
        note.innerHTML=`<b>상세 차트 신호 기준</b><br>${esc(interval)} 봉 기준 최초 신호 표시: ${esc(kstTime(s.first_signal_at,{mode:'dateTime'}))} KST · 신호가 ${fmt(s.first_signal_price,2)} · 표시봉 ${esc(kstTime(hit.time,{mode:'dateTime'}))} KST`;
      }else if(found && found.reason==='signal_after_latest_bar'){
        const latest=found.latest && found.latest.time;
        note.innerHTML=`<b>상세 차트 신호 기준</b><br><span style="color:var(--gold)">신호 이후 ${esc(interval)} 봉이 아직 없습니다.</span><br>최초 신호 ${esc(kstTime(s.first_signal_at,{mode:'dateTime'}))} KST · 최신봉 ${esc(kstTime(latest,{mode:'dateTime'}))} KST · 신호가 ${fmt(s.first_signal_price,2)}<br>세로선은 봉 데이터가 들어오면 자동 표시됩니다.`;
      }else{
        note.innerHTML=`<b>상세 차트 신호 기준</b><br><span style="color:var(--gold)">${esc(interval)} 봉에서 최초 신호 봉을 찾지 못했습니다.</span><br>최초 신호 ${esc(kstTime(s.first_signal_at,{mode:'dateTime'}))} KST · 신호가 ${fmt(s.first_signal_price,2)}`;
      }
    }
  }
  const _realOldDrawChart = window.drawChart || (typeof drawChart==='function' ? drawChart : null);
  if(_realOldDrawChart && !window.KM_REAL_DRAWCHART_WRAPPED){
    window.KM_REAL_DRAWCHART_WRAPPED=true;
    window.drawChart = async function(ticker, interval, opts){
      const out = await _realOldDrawChart(ticker, interval, opts);
      if(window._activeRealCandidate && String(window._activeRealCandidate.ticker||'').toUpperCase()===String(ticker||'').toUpperCase()){
        clearPreviewExitLines();
        await applyRealCandidateDetailSignal(ticker, interval);
        if(typeof _activeChart !== 'undefined') _activeChart = {type:'real_candidate', ticker, interval};
      }else if(window._activePreviewHolding && String(window._activePreviewHolding.ticker||'').toUpperCase()===String(ticker||'').toUpperCase()){
        clearRealDetailSignalOverlay();
        if(typeof _activeChart !== 'undefined') _activeChart = {type:'preview_holding', ticker, interval};
        setTimeout(()=>drawPreviewExitLines(window._activePreviewHolding),80);
      }else{
        clearRealDetailSignalOverlay();
        clearPreviewExitLines();
      }
      return out;
    };
    try{ drawChart = window.drawChart; }catch(e){}
  }
  window.openRealCandidateDetail = async function(candidateId){
    const s=byCid(candidateId);
    if(!s) return;
    const ticker=String(s.ticker||'').toUpperCase();
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    document.getElementById('page-slots').classList.add('active');
    document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));
    const nav=document.querySelectorAll('.nav button')[1]; if(nav) nav.classList.add('active');
    document.getElementById('slot-list-view').style.display='none';
    document.getElementById('slot-detail-view').style.display='block';
    if(window.chart) chart.resize(document.getElementById('chart').clientWidth, 440);
    window._activeRealCandidate = s;
    document.querySelectorAll('.tf').forEach(b=>b.classList.toggle('active', b.dataset.tf==='1m'));
    if(typeof window.drawChart==='function') await window.drawChart(ticker, '1m');
    if(typeof _activeChart !== 'undefined') _activeChart = {type:'real_candidate', ticker:ticker, interval:'1m'};
    renderRealCandidateDetail(s);
  };
  function renderRealCandidateDetail(s){
    const ticker=String(s.ticker||'').toUpperCase();
    const amount=defaultNotional();
    const title=document.getElementById('detail-title');
    if(title) title.textContent=`${ticker} — 매수 후보 차트`;
    const kv=document.getElementById('detail-kv');
    if(kv){
      kv.innerHTML=`
        <div class="kv"><span>현재가</span><span>${fmt(s.price ?? s.current_price,2)}</span></div>
        <div class="kv"><span>최초 신호</span><span>${esc(kstTime(s.first_signal_at,{mode:'dateTime'}))} KST</span></div>
        <div class="kv"><span>최초 신호가</span><span>${fmt(s.first_signal_price,2)}</span></div>
        <div class="kv"><span>final_score</span><span style="color:var(--up)">${fmt(s.final_score,3)}</span></div>
        <div class="kv"><span>threshold</span><span>${fmt(s.threshold,3)}</span></div>
        <div class="kv"><span>ratio</span><span>${fmt(s.ratio,3)}</span></div>
        <div class="kv"><span>vol_group</span><span>${esc(s.vol_group||'—')}</span></div>
        <div class="kv"><span>게이트</span><span>${esc(s.gate_status||'—')}</span></div>
        <div class="kv"><span>EQ</span><span class="${s.entry_quality_allow?'univ-pos':'univ-neg'}">${esc(s.entry_quality_label||'—')}</span></div>
        <div class="kv"><span>stage</span><span>${esc(s.stage||'—')}</span></div>
        <div class="kv"><span>candidate</span><span style="font-size:10px;word-break:break-all">${esc(s.candidate_id||'')}</span></div>
        <div class="kv"><span>시장 점수</span><span>${fmt(s.market_score,2)}</span></div>
        <div class="kv"><span>섹터 점수</span><span>${fmt(s.sector_score,2)}</span></div>
        <div class="kv"><span>VIX</span><span>${fmt(s.vix_level,2)}</span></div>
        <div class="kv"><span>후순위</span><span>${s.down_deprioritize?'SPY DOWN + HIGH_VOL':'아님'}</span></div>
        <div class="kv" style="grid-column:1/-1;display:block;background:rgba(59,130,246,.08);border-color:rgba(59,130,246,.35);">
          ${ticketHtml(s, {amount: amount, idSuffix: 'detail_'+String(s.candidate_id||'').replace(/[^A-Za-z0-9_-]/g,'_')})}
          <div style="font-size:11px;color:var(--dim);margin-top:6px;">후보 선택 시 보유/제외 목록에 기록되고 매수 대기 후보 8칸이 전면 재갱신됩니다.</div>
        </div>`;
    }
    const comm=document.getElementById('commentary');
    if(comm){
      const reasons=(s.reasons||[]).map(r=>`<li>${esc(r)}</li>`).join('');
      comm.innerHTML=`<div class="comment"><b>매수 대기 후보</b><br>${ticker} · score ${fmt(s.final_score,3)} · ${esc(s.vol_group||'')}</div>${reasons?`<ul style="margin:10px 0 0 18px;color:var(--dim);font-size:12px;">${reasons}</ul>`:''}`;
    }
    const omen=document.getElementById('sellomen-strip');
    if(omen) omen.innerHTML='';
    bindOrderTicketControls();
    document.querySelectorAll('.slot-preview-real[data-candidate-id]').forEach(btn=>{
      btn.onclick=function(ev){ev.stopPropagation(); handlePreview(btn.dataset.candidateId);};
    });
    document.querySelectorAll('.slot-buy-real[data-candidate-id]').forEach(btn=>{
      btn.onclick=function(ev){ev.stopPropagation(); handleBuy(btn.dataset.candidateId, Number(btn.dataset.slot||0));};
    });
  }
  const _realOldCloseDetail = window.closeDetail || (typeof closeDetail==='function' ? closeDetail : null);
  if(_realOldCloseDetail && !window.KM_REAL_CLOSEDETAIL_WRAPPED){
    window.KM_REAL_CLOSEDETAIL_WRAPPED=true;
    window.closeDetail=function(){
      window._activeRealCandidate=null;
      window._activePreviewHolding=null;
      clearRealDetailSignalOverlay();
      clearPreviewExitLines();
      try{ series.setMarkers([]); }catch(e){}
      return _realOldCloseDetail();
    };
    try{ closeDetail = window.closeDetail; }catch(e){}
  }
  const oldLoadSlots = window.loadSlots;
  if(typeof oldLoadSlots === 'function'){
    window.loadSlots = async function(){ if(window._realBuyDashboardPreview) return; await oldLoadSlots(); await loadCandidateSlots(); };
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', loadCandidateSlots); else loadCandidateSlots();
  setInterval(loadCandidateSlots, 30000);
  setInterval(()=>drawCandidateMiniCharts(false), 15000);
  setInterval(updateCandidateDynamicText, 10000);
  updateRealUpdateBadge({chartFetch: new Date().toISOString()});
})();
"""

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
    if(!confirm(`[실거래 대시보드]\n${ticker} ${money(amount)} 매수 요청을 real 전용 후보/intent API에 기록할까요?\n\n직접 주문 활성화 환경변수가 켜져 있으면 실제 Alpaca live 주문이 제출될 수 있습니다.`)) return;
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


def _real_candles_cached(base_module: Any, *, ticker: str, interval: str = "1d", period: str | None = None, refresh: bool = False) -> list[dict[str, Any]]:
    tk = str(ticker or "").upper().strip()
    iv = str(interval or "1d").strip()
    pd = str(period or "")
    key = (tk, iv, pd)
    now = time.time()
    ttl = float(_REAL_CANDLE_TTL_SEC.get(iv, 60))
    hit = _real_candle_cache.get(key)
    if hit and not refresh and now - hit[0] < ttl:
        return hit[1]
    data = base_module.live_candles(ticker=tk, interval=iv, period=period)
    if not isinstance(data, list):
        data = []
    _real_candle_cache[key] = (now, data)
    if len(_real_candle_cache) > 256:
        for old_key, _ in sorted(_real_candle_cache.items(), key=lambda kv: kv[1][0])[:64]:
            _real_candle_cache.pop(old_key, None)
    return data


def _real_candle_cache_status() -> dict[str, Any]:
    now = time.time()
    rows = []
    for (ticker, interval, period), (ts, data) in sorted(_real_candle_cache.items()):
        latest = None
        if data:
            try:
                latest = data[-1].get("time")
            except Exception:
                latest = None
        latest_kst = ""
        latest_age_sec = None
        if isinstance(latest, (int, float)):
            try:
                from datetime import datetime, timezone, timedelta
                latest_kst = datetime.fromtimestamp(float(latest), timezone(timedelta(hours=9))).isoformat(timespec="seconds")
                latest_age_sec = round(now - float(latest), 3)
            except Exception:
                latest_kst = ""
        rows.append({
            "ticker": ticker,
            "interval": interval,
            "period": period,
            "age_sec": round(now - ts, 3),
            "ttl_sec": _REAL_CANDLE_TTL_SEC.get(interval, 60),
            "rows": len(data or []),
            "latest_candle_time": latest,
            "latest_candle_time_kst": latest_kst,
            "latest_candle_age_sec": latest_age_sec,
        })
    return {"cache_size": len(rows), "items": rows, "ttl_policy_sec": dict(_REAL_CANDLE_TTL_SEC)}


def _real_dashboard_html(base_module: Any) -> HTMLResponse:
    path = base_module.DASHBOARD_MAIN_PATH
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"dashboard file missing: {path}")
    html = path.read_text(encoding="utf-8")
    # dashboard_home.html declares slotData with let, which is not exposed on window.
    # The real-slot overlay is injected as a later script, so it needs slotData to be
    # a window property to re-render /api/real/slots as buy-candidate cards.
    html = html.replace("let slotData=[], marketData={}, _holdingNewsEntries={};", "var slotData=[], marketData={}, _holdingNewsEntries={};")
    html = html.replace("<title>KINGMAKER</title>", "<title>KINGMAKER REAL</title>")
    if "real-candidate-mini-slots" not in html:
        # Reuse the existing "매수 대기 후보" panel as the one and only buy-candidate slot area.
        # Do not add a second candidate section.
        html = html.replace(
            '<summary><span>🛒 매수 대기 후보</span><span id="cand-meta" style="font-size:12px;color:var(--dim);"></span></summary>',
            '<summary><span>🛒 매수 대기 후보 슬롯 (8)</span><span id="real-candidate-meta" style="font-size:12px;color:var(--dim);"></span><span id="cand-meta" style="display:none;"></span></summary>',
        )
        html = html.replace(
            """<div id="cand-list" class="cand-list">
          <div class="cand-empty">대기 중인 후보 없음</div>
        </div>""",
            """<div class="mini-slots" id="real-candidate-mini-slots"><div class="loading">후보 로딩...</div></div>
        <div id="cand-list" class="cand-list" style="display:none;"><div class="cand-empty">대기 중인 후보 없음</div></div>""",
        )
        html = html.replace(
            """      <h3>📦 슬롯 클릭 → 상세</h3>
      <div class="mini-slots" id="slots-full"><div class="loading">로딩...</div></div>""",
            """      <h3>🛒 매수 대기 후보 클릭 → 차트/매수금액</h3>
      <div class="mini-slots" id="real-candidate-slots-full"><div class="loading">후보 로딩...</div></div>
      <h3 style="margin-top:18px;">📦 보유 슬롯 클릭 → 상세</h3>
      <div class="mini-slots" id="slots-full"><div class="loading">로딩...</div></div>""",
        )
    html = html.replace('const API="http://localhost:8001";', 'const API=window.location.origin;\nwindow.KM_DASHBOARD_MODE="real";')
    replacements = {
        "/api/live/account": "/api/real/account",
        "/api/live/positions": "/api/real/positions",
        "/api/live/slots": "/api/real/slots",
        "/api/live/market": "/api/real/market",
        "/api/live/news": "/api/real/news",
        "/api/live/rulebooks": "/api/real/rulebooks",
        "/api/live/candles": "/api/real/candles",
        "/api/live/equity_curve": "/api/real/equity_curve",
        "/api/live/trades_history": "/api/real/trades_history",
        "/api/live/universe": "/api/real/universe",
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
        "⚠️ 실거래용 복제 대시보드 · 모든 운영 정보 API는 /api/real/* 사용 · 연결확인 /api/real/connection · 기존 paper/live state와 분리됨 · 직접 주문은 KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS=1일 때만 제출"
        "</div>",
    )
    html = html.replace(
        "실제 매도 주문이 들어가며 되돌릴 수 없습니다.",
        "실거래용 별도 청산 요청이 기록됩니다. 직접 주문 환경변수가 켜져 있으면 실제 Alpaca live 주문이 제출될 수 있습니다.",
    )
    snippet = '<script src="/real-slot-overlay.js?v=real_slots_v20_preview_exit_panel_fix"></script>\n'
    if "real-slot-overlay.js" not in html:
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

    @app.get("/real-slot-overlay.js", include_in_schema=False)
    def real_slot_overlay_js():
        return Response(content=_real_slot_overlay_js(), media_type="application/javascript; charset=utf-8")

    @app.get("/api/real/connection")
    def real_connection(refresh: bool = False, account_check: bool = True):
        return _real_connection_status(refresh=refresh, account_check=account_check)

    @app.get("/api/real/data_sources")
    def real_data_sources():
        return {
            "isolated": True,
            "market": str(REAL_MARKET_STATE_PATH),
            "news": str(REAL_NEWS_STATE_PATH),
            "buy_candidates": str(REAL_BUY_CANDIDATES_PATH),
            "buy_intents": str(REAL_BUY_INTENT_PATH),
            "sell_intents": str(REAL_SELL_INTENT_PATH),
            "rulebooks": str(REAL_RULEBOOKS_PATH),
            "universe": str(REAL_UNIVERSE_PATH),
            "trades_history": str(REAL_TRADES_HISTORY_PATH),
        }

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
                "connection": _public_connection_config(),
                "realized_pnl_today": 0.0,
                "realized_pnl_total": 0.0,
                "total_return_pct": 0.0,
            }
        except Exception as exc:
            payload = _broker_unavailable_payload()
            payload["error"] = f"{type(exc).__name__}: {exc}"
            payload["hint"] = _connection_hint(payload.get("connection") or {}, str(exc))
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

    @app.get("/api/real/candidate_slots")
    def real_candidate_slots(max_slots: int = 8):
        return _real_candidate_slots_payload(max_slots=max_slots)

    @app.get("/api/real/buy_preview_dashboard")
    def real_buy_preview_dashboard(candidate_id: str, notional: float = 100.0):
        try:
            return _real_buy_preview_dashboard_payload(candidate_id=candidate_id, notional=notional)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/real/live_slots_state")
    def real_live_slots_state():
        state = _live_slots_state()
        state["api_source"] = "data/_system/live_slots_state.json"
        state["slots_payload"] = _real_candidate_slots_payload(8)
        return state

    @app.post("/api/real/live_slot_buy")
    def real_live_slot_buy(req: RealSlotBuyRequest):
        try:
            return _mark_real_slot_manual_buy(req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/real/position_slots")
    def real_position_slots(max_slots: int = 8):
        filled = _real_positions_payload()
        slots = []
        for i in range(int(max_slots or 8)):
            if i < len(filled):
                slots.append({"slot": i + 1, "empty": False, **filled[i]})
            else:
                slots.append({"slot": i + 1, "empty": True})
        return slots

    @app.get("/api/real/open_orders")
    def real_open_orders():
        broker = _get_real_broker()
        if broker is None:
            return {"ok": False, "orders": [], "connection": _public_connection_config(), "error": _real_broker_error}
        try:
            return {"ok": True, "orders": [_order_dict(o) for o in broker.get_open_orders()], "connection": _public_connection_config()}
        except Exception as exc:
            return {"ok": False, "orders": [], "connection": _public_connection_config(), "error": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/real/orders")
    def real_orders_alias():
        return real_open_orders()

    @app.get("/api/real/market")
    def real_market():
        return _real_market_state()

    @app.get("/api/real/news")
    def real_news():
        return _real_news_state()

    @app.get("/api/real/rulebooks")
    def real_rulebooks():
        data = read_json(REAL_RULEBOOKS_PATH, [])
        return data if isinstance(data, list) else []

    @app.get("/api/real/universe")
    def real_universe():
        data = read_json(REAL_UNIVERSE_PATH, {})
        if isinstance(data, dict) and data:
            data = dict(data)
            data.setdefault("isolated", True)
            data.setdefault("state_path", str(REAL_UNIVERSE_PATH))
            return data
        return {"count": 0, "items": [], "isolated": True, "state_path": str(REAL_UNIVERSE_PATH), "note": "실거래 대시보드 전용 유니버스 파일이 아직 없습니다."}

    @app.get("/api/real/candles/{ticker}")
    def real_candles(ticker: str, interval: str = "1d", period: str | None = None, refresh: bool = False):
        return _real_candles_cached(base_module, ticker=ticker, interval=interval, period=period, refresh=refresh)

    @app.get("/api/real/candles_cache_status")
    def real_candles_cache_status():
        return _real_candle_cache_status()

    @app.get("/api/real/equity_curve")
    def real_equity_curve():
        acct = real_account()
        total = _safe_float(acct.get("total_value"), 0.0) if isinstance(acct, dict) else 0.0
        return [{"time": utc_now_iso(), "value": round(total or 0.0, 2)}]

    @app.get("/api/real/trades_history")
    def real_trades_history():
        return _real_trades_history()

    @app.get("/api/real/central_candidates")
    def real_central_candidates(include_blocked: bool = False):
        return _real_candidate_state(include_blocked=include_blocked)

    @app.get("/api/real/manual_buy_intents")
    def real_manual_buy_intents():
        return _intent_state(REAL_BUY_INTENT_PATH)

    @app.post("/api/real/manual_buy_intent")
    def real_manual_buy_intent(req: RealBuyIntentRequest):
        try:
            row = _create_real_buy_intent(req)
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
