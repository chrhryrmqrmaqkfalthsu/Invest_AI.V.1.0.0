#!/usr/bin/env python3
"""정규장 전 운용 리셋: 현재 Elite Shadow/Lab 가상 보유를 모두 비운다.

실제 broker 주문은 절대 내지 않는다.
Shadow open_positions와 Exit Policy Lab open_positions만 가상 청산/기록한다.
"""
from __future__ import annotations

import json
from typing import Any

from engine.live.elite_shadow_trader import (
    _acquire_lock as shadow_acquire_lock,
    _event as shadow_event,
    _holding_days,
    _latest_price,
    _load_ohlcv,
    _release_lock as shadow_release_lock,
    _safe_float,
    _safe_int,
    _summarize_state,
    append_trade,
    load_state,
    save_state,
    utc_now,
)
from engine.live.elite_exit_policy_lab import (
    _acquire_lock as lab_acquire_lock,
    _close_lab_position,
    _latest_price as lab_latest_price,
    _load_ohlcv as lab_load_ohlcv,
    _release_lock as lab_release_lock,
    _summarize_lab,
    _update_position_mark,
    load_lab_state,
    save_lab_state,
)

RESET_REASON = "manual_regular_hours_reset"


def _price_for_shadow(pos: dict[str, Any]) -> float:
    ticker = str(pos.get("ticker") or "").upper().strip()
    price = None
    if ticker:
        df = _load_ohlcv(ticker)
        price = _latest_price(ticker, df)
    if not price:
        price = _safe_float(pos.get("last_price"), 0.0) or _safe_float(pos.get("entry_price"), 0.0)
    return float(price or 0.0)


def close_shadow_positions() -> dict[str, Any]:
    if not shadow_acquire_lock():
        return {"ok": False, "reason": "shadow_state_lock_busy"}
    try:
        state = load_state()
        closed = 0
        samples: list[dict[str, Any]] = []
        for pos_key, pos in list((state.get("open_positions") or {}).items()):
            entry = _safe_float(pos.get("entry_price"), 0.0)
            shares = _safe_float(pos.get("shares"), 0.0)
            price = _price_for_shadow(pos)
            if entry <= 0.0 or shares <= 0.0 or price <= 0.0:
                price = entry
            pnl_pct = (price / entry - 1.0) * 100.0 if entry > 0.0 else 0.0
            pnl_usd = shares * (price - entry)
            highest = max(_safe_float(pos.get("highest_price"), entry), price)
            lowest = min(_safe_float(pos.get("lowest_price"), entry), price)
            max_profit_pct = max(_safe_float(pos.get("max_profit_pct", 0.0)), (highest / entry - 1.0) * 100.0 if entry > 0.0 else 0.0)
            max_loss_pct = min(_safe_float(pos.get("max_loss_pct", 0.0)), (lowest / entry - 1.0) * 100.0 if entry > 0.0 else 0.0)
            trade = {
                "_comment": "Elite shadow virtual reset close. No broker order was placed.",
                "manual_reset": True,
                "reset_reason": RESET_REASON,
                "position_id": pos.get("position_id"),
                "candidate_id": pos_key,
                "ticker": pos.get("ticker"),
                "stage": pos.get("stage"),
                "bucket": pos.get("bucket"),
                "rulebook_hash_short": pos.get("rulebook_hash_short"),
                "opened_at": pos.get("opened_at"),
                "closed_at": utc_now(),
                "entry_price": entry,
                "exit_price": price,
                "shares": shares,
                "notional": _safe_float(pos.get("notional"), 0.0),
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "exit_reason": RESET_REASON,
                "holding_days": _holding_days(str(pos.get("opened_at") or "")),
                "max_profit_pct": max_profit_pct,
                "max_loss_pct": max_loss_pct,
                "entry_score": pos.get("entry_score"),
                "entry_threshold": pos.get("entry_threshold"),
                "entry_ratio": pos.get("entry_ratio"),
                "entry_quality_score": pos.get("entry_quality_score"),
                "entry_quality_label": pos.get("entry_quality_label"),
                "entry_quality_primary_reason": (pos.get("entry_quality") or {}).get("primary_reason"),
                "entry_concentration_score": pos.get("entry_concentration_score"),
                "entry_concentration_action": pos.get("entry_concentration_action"),
                "entry_concentration_allowed": pos.get("entry_concentration_allowed"),
                "entry_concentration_rank_at_entry": pos.get("entry_concentration_rank_at_entry"),
                "entry_concentration_rank_total": pos.get("entry_concentration_rank_total"),
                "entry_concentration_blocks": pos.get("entry_concentration_blocks"),
                "entry_concentration_caps": pos.get("entry_concentration_caps"),
                "entry_concentration_confidence": pos.get("entry_concentration_confidence"),
                "entry_concentration_rank_scope": pos.get("entry_concentration_rank_scope"),
            }
            append_trade(trade)
            state["open_positions"].pop(pos_key, None)
            state["closed_count"] = _safe_int(state.get("closed_count"), 0) + 1
            shadow_event(state, "CLOSE", str(pos.get("ticker") or ""), f"{RESET_REASON} pnl={pnl_pct:+.2f}% price={price:.2f}", trade)
            closed += 1
            samples.append({"ticker": pos.get("ticker"), "pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "exit_price": price})
        state["last_manual_regular_hours_reset"] = {"time": utc_now(), "closed": closed, "samples": samples[:50], "reason": RESET_REASON}
        state["summary"] = _summarize_state(state)
        save_state(state)
        return {"ok": True, "closed": closed, "samples": samples}
    finally:
        shadow_release_lock()


def _price_for_lab(pos: dict[str, Any]) -> float:
    ticker = str(pos.get("ticker") or "").upper().strip()
    price = None
    if ticker:
        df = lab_load_ohlcv(ticker)
        price = lab_latest_price(ticker, df)
    if not price:
        price = _safe_float(pos.get("last_price"), 0.0) or _safe_float(pos.get("entry_price"), 0.0)
    return float(price or 0.0)


def close_lab_positions() -> dict[str, Any]:
    if not lab_acquire_lock():
        return {"ok": False, "reason": "lab_state_lock_busy"}
    try:
        state = load_lab_state()
        closed = 0
        samples: list[dict[str, Any]] = []
        for lab_id, pos in list((state.get("open_positions") or {}).items()):
            price = _price_for_lab(pos)
            if price <= 0.0:
                price = _safe_float(pos.get("entry_price"), 0.0)
            mark = _update_position_mark(pos, price)
            decision = {"close": True, "reason": RESET_REASON, "detail": {"manual_reset": True, "regular_hours_restart": True}}
            trade = _close_lab_position(state, lab_id, pos, price, mark, decision)
            closed += 1
            samples.append({"ticker": trade.get("ticker"), "policy_id": trade.get("policy_id"), "pnl_pct": trade.get("pnl_pct"), "pnl_usd": trade.get("pnl_usd")})
        state["summary"] = _summarize_lab(state)
        state["last_manual_regular_hours_reset"] = {"time": utc_now(), "closed": closed, "samples": samples[:50], "reason": RESET_REASON}
        save_lab_state(state)
        return {"ok": True, "closed": closed, "samples": samples[:50]}
    finally:
        lab_release_lock()


def main() -> int:
    result = {"shadow": close_shadow_positions(), "lab": close_lab_positions(), "reason": RESET_REASON, "no_broker_orders": True}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["shadow"].get("ok") and result["lab"].get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
