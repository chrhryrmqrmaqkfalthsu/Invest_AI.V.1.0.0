"""Persistent mark-to-market for Elite Shadow positions.

기존 Shadow max_profit_pct는 tick 시점의 마지막 가격만 반영했다.
그 방식은 1분봉 중간 고가를 놓쳐서 +5%를 찍은 포지션도 state/trade에는
+0~1%로 남는 문제가 생긴다.

이 모듈은 열린 Shadow 포지션에 대해 intraday 1m High/Low를 entry 이후로
스캔해서 highest_price/lowest_price/max_profit_pct/max_loss_pct를 state에 저장한다.
실제 broker 주문은 없고, Elite Shadow 가상 ledger만 갱신한다.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

import yfinance as yf

from engine.live.elite_shadow_trader import (
    _acquire_lock,
    _holding_days,
    _release_lock,
    _safe_float,
    load_state,
    save_state,
    utc_now,
)

_INTRADAY_CACHE_TTL_SEC = 45.0
_intraday_cache: dict[str, tuple[dict[str, Any] | None, float]] = {}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _clean_positive(value: Any) -> float | None:
    v = _num(value, 0.0)
    return v if v > 0.0 else None


def _intraday_snapshot_since_open(ticker: str, opened_at: Any) -> dict[str, Any] | None:
    ticker_u = str(ticker or "").upper().strip()
    opened = _parse_dt(opened_at)
    if not ticker_u or opened is None:
        return None
    cache_key = f"{ticker_u}|{opened.isoformat()}"
    now = time.time()
    cached = _intraday_cache.get(cache_key)
    if cached and now - cached[1] < _INTRADAY_CACHE_TTL_SEC:
        return cached[0]

    snapshot = None
    try:
        hist = yf.Ticker(ticker_u).history(period="5d", interval="1m", prepost=True)
        if hist is not None and not hist.empty:
            idx = hist.index
            if getattr(idx, "tz", None) is None:
                # yfinance intraday는 보통 tz-aware지만, 방어적으로 UTC 취급한다.
                hist = hist.copy()
                hist.index = hist.index.tz_localize(timezone.utc)
            hist_utc = hist.copy()
            hist_utc.index = hist_utc.index.tz_convert(timezone.utc)
            post = hist_utc[hist_utc.index >= opened]
            if post is not None and not post.empty:
                high_s = post["High"].dropna()
                low_s = post["Low"].dropna()
                close_s = post["Close"].dropna()
                if not high_s.empty and not low_s.empty and not close_s.empty:
                    high_idx = high_s.idxmax()
                    low_idx = low_s.idxmin()
                    snapshot = {
                        "ticker": ticker_u,
                        "source": "yfinance_1m_prepost_since_entry",
                        "last_price": _clean_positive(close_s.iloc[-1]),
                        "high_price": _clean_positive(high_s.loc[high_idx]),
                        "low_price": _clean_positive(low_s.loc[low_idx]),
                        "high_time": high_idx.isoformat(),
                        "low_time": low_idx.isoformat(),
                        "last_time": close_s.index[-1].isoformat(),
                        "bar_count": int(len(post)),
                    }
    except Exception:
        snapshot = None

    _intraday_cache[cache_key] = (snapshot, now)
    return snapshot


def apply_mark_to_market_snapshot(pos: dict[str, Any], snapshot: dict[str, Any], *, source: str = "shadow_mtm") -> bool:
    """Apply high/low/last snapshot to a single position.

    Returns True if position fields changed materially.
    """
    entry = _safe_float(pos.get("entry_price"), 0.0)
    shares = _safe_float(pos.get("shares"), 0.0)
    if entry <= 0.0:
        return False

    last_price = _clean_positive(snapshot.get("last_price")) or _clean_positive(pos.get("last_price")) or entry
    high_price = _clean_positive(snapshot.get("high_price")) or last_price
    low_price = _clean_positive(snapshot.get("low_price")) or last_price

    prev_high = _safe_float(pos.get("highest_price"), entry)
    prev_low = _safe_float(pos.get("lowest_price"), entry)
    highest = max(prev_high, high_price, last_price)
    lowest = min(prev_low, low_price, last_price)
    pnl_pct = (last_price / entry - 1.0) * 100.0
    pnl_usd = shares * (last_price - entry)
    max_profit_pct = max(_safe_float(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0)
    max_loss_pct = min(_safe_float(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0)

    changed = False
    old_max = _safe_float(pos.get("max_profit_pct"), 0.0)
    old_loss = _safe_float(pos.get("max_loss_pct"), 0.0)
    old_last = _safe_float(pos.get("last_price"), 0.0)

    update = {
        "highest_price": highest,
        "lowest_price": lowest,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "last_price": last_price,
        "last_seen_at": utc_now(),
        "unrealized_pnl_pct": pnl_pct,
        "unrealized_pnl_usd": pnl_usd,
        "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        "mark_to_market_source": source,
        "mark_to_market_intraday_source": snapshot.get("source"),
        "mark_to_market_bar_count": snapshot.get("bar_count"),
    }
    if max_profit_pct > old_max + 1e-9:
        update["max_profit_observed_at"] = snapshot.get("high_time") or utc_now()
        update["max_profit_source"] = source
        changed = True
    if max_loss_pct < old_loss - 1e-9:
        update["max_loss_observed_at"] = snapshot.get("low_time") or utc_now()
        update["max_loss_source"] = source
        changed = True
    if abs(last_price - old_last) > 1e-9:
        changed = True
    if abs(pnl_pct - _safe_float(pos.get("unrealized_pnl_pct"), 0.0)) > 1e-9:
        changed = True

    pos.update(update)
    return changed


def persist_shadow_mark_to_market(*, source: str = "shadow_1m_high_low_persist", lock_ttl_sec: float = 30.0) -> dict[str, Any]:
    """Persist 1m high/low based MFE/MAE for all open Elite Shadow positions."""
    if not _acquire_lock(ttl_sec=lock_ttl_sec):
        return {"ok": False, "reason": "shadow_state_lock_busy", "updated": 0, "evaluated": 0, "errors": []}
    updated = 0
    evaluated = 0
    errors: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    changed = False
    try:
        state = load_state()
        for pos_key, pos in list((state.get("open_positions") or {}).items()):
            if not isinstance(pos, dict):
                continue
            ticker = str(pos.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            evaluated += 1
            snapshot = _intraday_snapshot_since_open(ticker, pos.get("opened_at"))
            if not snapshot:
                errors.append({"ticker": ticker, "candidate_id": pos_key, "reason": "intraday_snapshot_missing"})
                continue
            old_max = _safe_float(pos.get("max_profit_pct"), 0.0)
            old_loss = _safe_float(pos.get("max_loss_pct"), 0.0)
            if apply_mark_to_market_snapshot(pos, snapshot, source=source):
                changed = True
                updated += 1
                new_max = _safe_float(pos.get("max_profit_pct"), 0.0)
                new_loss = _safe_float(pos.get("max_loss_pct"), 0.0)
                if len(samples) < 20 and (new_max > old_max + 1e-9 or new_loss < old_loss - 1e-9):
                    samples.append({
                        "ticker": ticker,
                        "candidate_id": pos_key,
                        "old_max_profit_pct": old_max,
                        "new_max_profit_pct": new_max,
                        "old_max_loss_pct": old_loss,
                        "new_max_loss_pct": new_loss,
                        "high_price": snapshot.get("high_price"),
                        "high_time": snapshot.get("high_time"),
                        "low_price": snapshot.get("low_price"),
                        "low_time": snapshot.get("low_time"),
                    })
        result = {
            "ok": True,
            "time": utc_now(),
            "source": source,
            "evaluated": evaluated,
            "updated": updated,
            "errors": errors[-20:],
            "samples": samples,
        }
        state["last_shadow_mark_to_market"] = result
        if changed:
            save_state(state)
        else:
            # last_shadow_mark_to_market도 관측 로그이므로 저장한다.
            save_state(state)
        return result
    finally:
        _release_lock()
