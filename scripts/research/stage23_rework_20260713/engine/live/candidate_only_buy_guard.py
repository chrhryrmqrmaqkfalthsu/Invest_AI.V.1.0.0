"""Candidate-only BUY guard for live dashboard operation.

This guard disables automatic BUY order submission from the legacy per-ticker
signal path and publishes the would-be BUY signals to the dashboard candidate
file instead. SELL/exit/manual-sell paths are not affected.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.core.metadata import compute_member_hash
from engine.live.manual_buy_intent import CENTRAL_BUY_CANDIDATES_PATH, atomic_write_json, load_candidate_state, utc_now_iso

log = logging.getLogger("candidate_only_buy_guard")
CANDIDATE_ONLY_BUY_DISABLED_CODE = "CANDIDATE_ONLY_BUY_DISABLED"
DEFAULT_MAX_CANDIDATES = 8


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


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _trade_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _rulebook_to_dict(rulebook: Any) -> dict[str, Any]:
    if rulebook is None:
        return {}
    if hasattr(rulebook, "to_dict"):
        try:
            data = rulebook.to_dict()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    if isinstance(rulebook, dict):
        return dict(rulebook)
    return {}


def _signal_reasons(signal_result: Any) -> list[str]:
    reasons = getattr(signal_result, "reasons", None)
    if isinstance(reasons, list):
        return [str(x) for x in reasons[:12]]
    reason = getattr(signal_result, "reason", "")
    return [str(reason)] if reason else []


def _signal_score(signal_result: Any) -> float | None:
    for key in ("score", "signal_score", "effective_strength", "strength"):
        value = _safe_float(getattr(signal_result, key, None))
        if value is not None:
            return value
    return None


def _signal_threshold(signal_result: Any, rulebook: Any = None) -> float | None:
    for key in ("threshold", "signal_threshold"):
        value = _safe_float(getattr(signal_result, key, None))
        if value is not None:
            return value
    return _safe_float(getattr(rulebook, "signal_threshold", None))


def _target_stop_from_rulebook(price: float, atr: float | None, rb: dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    if price <= 0.0 or not atr or atr <= 0.0:
        return None, None, None, None
    direction = str(rb.get("direction") or "long").lower()
    stop_mult = _safe_float(rb.get("stop_loss_atr"), 0.0) or 0.0
    target_mult = _safe_float(rb.get("take_profit_atr"), 0.0) or 0.0
    if direction == "short":
        stop_price = price + atr * stop_mult if stop_mult > 0 else None
        target_price = price - atr * target_mult if target_mult > 0 else None
    else:
        stop_price = price - atr * stop_mult if stop_mult > 0 else None
        target_price = price + atr * target_mult if target_mult > 0 else None
    target_pct = (target_price / price - 1.0) * 100.0 if target_price and price else None
    stop_pct = (stop_price / price - 1.0) * 100.0 if stop_price and price else None
    return target_price, stop_price, target_pct, stop_pct


def _candidate_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float, str]:
    cid, row = item
    score = _safe_float(row.get("score"), -1e18)
    confidence = _safe_float(row.get("confidence"), score if score is not None else -1e18)
    updated = str(row.get("updated_at") or "")
    return (score if score is not None else -1e18, confidence if confidence is not None else -1e18, updated)


def _normalize_max_candidates(max_candidates: int | None) -> int:
    try:
        value = int(max_candidates or DEFAULT_MAX_CANDIDATES)
    except Exception:
        value = DEFAULT_MAX_CANDIDATES
    return max(1, min(DEFAULT_MAX_CANDIDATES, value))


def prune_candidate_only_state(path: Path | str = CENTRAL_BUY_CANDIDATES_PATH, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> dict[str, Any]:
    """Keep only current candidate-only rows, capped by score/confidence."""
    max_rows = _normalize_max_candidates(max_candidates)
    state = load_candidate_state(path)
    if not isinstance(state, dict):
        state = {}
    candidates = state.get("candidates") if isinstance(state.get("candidates"), dict) else {}
    hidden = {"manual_executed", "auto_executed", "expired", "blocked", "cancelled", "canceled"}
    kept = {
        str(cid): dict(row)
        for cid, row in candidates.items()
        if isinstance(row, dict) and str(row.get("status") or "pending") not in hidden
    }
    ordered = sorted(kept.items(), key=_candidate_sort_key, reverse=True)[:max_rows]
    out = {
        "schema_version": 1,
        "trade_date": str(state.get("trade_date") or _trade_date_et()),
        "buy_mode": "candidate_only",
        "source": "live_candidate_only_buy_guard",
        "auto_buy_enabled": False,
        "manual_buy_enabled": True,
        "candidate_limit": max_rows,
        "updated_at": utc_now_iso(),
        "candidates": {cid: row for cid, row in ordered},
        "note": "자동 BUY는 비활성화. BUY 신호는 대시보드 후보로만 발행됨.",
    }
    atomic_write_json(path, out)
    return out


def publish_buy_candidate(
    runner: Any,
    *,
    ticker: str,
    price: float,
    reason: str,
    signal_result: Any = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    candidate_path: Path | str = CENTRAL_BUY_CANDIDATES_PATH,
) -> dict[str, Any] | None:
    """Publish a would-be BUY as a dashboard candidate, without ordering."""
    ticker_u = str(ticker or "").upper().strip()
    price_f = _safe_float(price)
    if not ticker_u or price_f is None or price_f <= 0.0:
        return None
    max_rows = _normalize_max_candidates(max_candidates)
    now = utc_now_iso()
    trade_date = _trade_date_et()
    cid = f"{trade_date}:{ticker_u}_candidate_only"

    preflight = None
    rb_obj = None
    atr = None
    preflight_error = ""
    try:
        preflight = runner._get_buy_reconciler().preflight(ticker_u)
        rb_obj = getattr(preflight, "rulebook", None)
        atr = _safe_float(getattr(preflight, "atr", None))
    except Exception as exc:
        preflight_error = f"{type(exc).__name__}: {exc}"
        try:
            rb_obj = runner.rulebook.get_rulebook(ticker_u) if hasattr(runner.rulebook, "get_rulebook") else None
            atr = _safe_float(runner.rulebook.get_last_atr(ticker_u)) if hasattr(runner.rulebook, "get_last_atr") else None
        except Exception:
            rb_obj = None
            atr = None

    rb = _rulebook_to_dict(rb_obj)
    target_price, stop_price, target_pct, stop_pct = _target_stop_from_rulebook(price_f, atr, rb)
    score = _signal_score(signal_result)
    threshold = _signal_threshold(signal_result, rb_obj)
    confidence = score
    if score is not None and threshold and threshold > 0:
        confidence = score / threshold
    order_notional = _safe_float(getattr(runner, "order_notional", None), None)
    shares = (order_notional / price_f) if order_notional and price_f else None

    row: dict[str, Any] = {
        "candidate_id": cid,
        "ticker": ticker_u,
        "entity_id": f"{ticker_u}_candidate_only",
        "trade_date": trade_date,
        "status": "pending",
        "source": "live_candidate_only_buy_guard",
        "buy_mode": "candidate_only",
        "auto_buy_enabled": False,
        "manual_buy_enabled": True,
        "action_label": "수동 매수",
        "reason": str(reason or ""),
        "signal_reasons": _signal_reasons(signal_result),
        "price": price_f,
        "reference_price": price_f,
        "notional": order_notional,
        "shares": shares,
        "score": score,
        "confidence": confidence,
        "strength": confidence,
        "effective_strength": confidence,
        "signal_score": score,
        "signal_threshold": threshold,
        "atr_at_signal": atr,
        "target_price": target_price,
        "stop_price": stop_price,
        "target_return_pct": target_pct,
        "stop_return_pct": stop_pct,
        "target_basis": "rulebook_atr_display_only" if target_price is not None else "",
        "rulebook": rb,
        "rulebook_hash": compute_member_hash(rb) if rb else "",
        "win_rate": _safe_float(rb.get("win_rate")) if rb else None,
        "expectancy_pct": _safe_float(rb.get("expectancy_pct")) if rb else None,
        "avg_return_pct": _safe_float(rb.get("avg_return_pct")) if rb else None,
        "trade_count": _safe_int(rb.get("trade_count")) if rb else None,
        "exit_strategy": rb.get("exit_strategy") if rb else None,
        "max_holding_days": rb.get("max_holding_days") if rb else None,
        "preflight_ok": preflight is not None,
        "preflight_error": preflight_error,
        "created_at": now,
        "updated_at": now,
        "note": "자동 매수 차단됨: 대시보드 후보로만 표시",
    }

    state = load_candidate_state(candidate_path)
    if not isinstance(state, dict) or state.get("trade_date") != trade_date or str(state.get("buy_mode") or "") != "candidate_only":
        state = {
            "schema_version": 1,
            "trade_date": trade_date,
            "buy_mode": "candidate_only",
            "source": "live_candidate_only_buy_guard",
            "auto_buy_enabled": False,
            "manual_buy_enabled": True,
            "candidate_limit": max_rows,
            "updated_at": now,
            "candidates": {},
            "note": "자동 BUY는 비활성화. BUY 신호는 대시보드 후보로만 발행됨.",
        }
    candidates = state.setdefault("candidates", {})
    old = candidates.get(cid) if isinstance(candidates, dict) else None
    if isinstance(old, dict) and old.get("created_at"):
        row["created_at"] = old.get("created_at")
        if str(old.get("status") or "") == "manual_requested":
            for key in ("status", "manual_intent_id", "manual_requested_notional", "manual_notional", "notional_source"):
                if key in old:
                    row[key] = old[key]
            row["manual_buy_enabled"] = False
            row["action_label"] = "처리 중"
    candidates[cid] = row
    state["candidates"] = candidates
    state["updated_at"] = now
    state["auto_buy_enabled"] = False
    state["manual_buy_enabled"] = True
    state["candidate_limit"] = max_rows
    state["source"] = "live_candidate_only_buy_guard"
    atomic_write_json(candidate_path, state)
    final_state = prune_candidate_only_state(candidate_path, max_candidates=max_rows)
    log.warning("[%s] %s BUY 자동주문 차단 → 후보 발행 score=%s price=%s candidates=%s/%s", CANDIDATE_ONLY_BUY_DISABLED_CODE, ticker_u, score, price_f, len(final_state.get("candidates") or {}), max_rows)
    return row


def install_candidate_only_buy_guard(
    runner: Any,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    candidate_path: Path | str = CENTRAL_BUY_CANDIDATES_PATH,
) -> None:
    """Patch Runner._try_order so BUY becomes dashboard candidate only."""
    if getattr(runner, "_candidate_only_buy_guard_installed", False):
        return
    if not hasattr(runner, "_try_order"):
        log.warning("[%s] runner has no _try_order; guard skipped", CANDIDATE_ONLY_BUY_DISABLED_CODE)
        return
    max_rows = _normalize_max_candidates(max_candidates)
    original_try_order = runner._try_order

    def guarded_try_order(side: str, ticker: str, price: float, reason: str, signal_result=None, rulebook_override=None) -> None:
        side_u = str(side or "").upper()
        if side_u == "BUY":
            try:
                runner.stats.orders_attempted += 1
                runner.stats.orders_blocked += 1
            except Exception:
                pass
            publish_buy_candidate(
                runner,
                ticker=ticker,
                price=price,
                reason=reason,
                signal_result=signal_result,
                max_candidates=max_rows,
                candidate_path=candidate_path,
            )
            try:
                runner.notifier.send_safety_block(
                    CANDIDATE_ONLY_BUY_DISABLED_CODE,
                    f"{str(ticker or '').upper()} BUY 자동매수 차단: 후보 패널에만 표시합니다.",
                )
            except Exception as exc:
                log.debug("candidate-only notify skipped: %s", exc)
            return None
        try:
            return original_try_order(side, ticker, price, reason, signal_result=signal_result, rulebook_override=rulebook_override)
        except TypeError as exc:
            if "rulebook_override" not in str(exc):
                raise
            return original_try_order(side, ticker, price, reason, signal_result=signal_result)

    runner._try_order = guarded_try_order
    runner._candidate_only_buy_guard_installed = True
    try:
        prune_candidate_only_state(candidate_path, max_candidates=max_rows)
    except Exception as exc:
        log.warning("candidate-only state init/prune failed: %s", exc)
    log.warning("[%s] ON: 자동 BUY 차단, BUY 신호는 후보 파일에만 발행, max_candidates=%s", CANDIDATE_ONLY_BUY_DISABLED_CODE, max_rows)
