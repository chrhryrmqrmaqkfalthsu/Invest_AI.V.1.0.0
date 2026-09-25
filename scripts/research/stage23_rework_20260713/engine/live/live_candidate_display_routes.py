"""Live dashboard candidate display override for candidate-only BUY mode."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from engine.live.candidate_only_buy_guard import DEFAULT_MAX_CANDIDATES, prune_candidate_only_state
from engine.live.manual_buy_intent import CENTRAL_BUY_CANDIDATES_PATH, load_candidate_state


def _safe_float(value: Any, default: float = -1e18) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float, str]:
    cid, row = item
    score = _safe_float(row.get("score"))
    confidence = _safe_float(row.get("confidence"), score)
    updated = str(row.get("updated_at") or "")
    return (score, confidence, updated)


def _candidate_only_state_available(state: dict[str, Any]) -> bool:
    if not isinstance(state, dict):
        return False
    return str(state.get("buy_mode") or "") == "candidate_only" or str(state.get("source") or "") == "live_candidate_only_buy_guard"


def _cap_state(state: dict[str, Any], *, max_candidates: int, display_only: bool = True) -> dict[str, Any]:
    out = dict(state or {})
    candidates = out.get("candidates") if isinstance(out.get("candidates"), dict) else {}
    hidden = {"manual_executed", "auto_executed", "expired", "blocked", "cancelled", "canceled"}
    kept = {
        str(cid): dict(row)
        for cid, row in candidates.items()
        if isinstance(row, dict) and str(row.get("status") or "pending") not in hidden
    }
    ordered = sorted(kept.items(), key=_sort_key, reverse=True)[:max_candidates]
    capped = {}
    for cid, row in ordered:
        if display_only:
            row["auto_buy_enabled"] = False
            row["manual_buy_enabled"] = False
            row["action_label"] = "후보 표시"
            row.setdefault("note", "candidate-only 표시 전용: live BUY 비활성")
        capped[cid] = row
    out["candidates"] = capped
    out["candidate_limit"] = max_candidates
    out["candidate_count"] = len(capped)
    if display_only:
        out["auto_buy_enabled"] = False
        out["manual_buy_enabled"] = False
        out["display_only"] = True
        out["note"] = "live 자동/수동 BUY 비활성. BUY 신호는 후보 패널에만 최대 8개 표시."
    return out


def live_candidate_state(base_module: Any, *, include_blocked: bool = False, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> dict[str, Any]:
    max_rows = max(1, min(DEFAULT_MAX_CANDIDATES, int(max_candidates or DEFAULT_MAX_CANDIDATES)))
    current = load_candidate_state(CENTRAL_BUY_CANDIDATES_PATH)
    if _candidate_only_state_available(current):
        pruned = prune_candidate_only_state(CENTRAL_BUY_CANDIDATES_PATH, max_candidates=max_rows)
        return _cap_state(pruned, max_candidates=max_rows, display_only=True)

    try:
        state = base_module.central_candidates(include_blocked=include_blocked)
    except Exception:
        state = current if isinstance(current, dict) else {"schema_version": 1, "trade_date": "", "candidates": {}}
    out = _cap_state(state if isinstance(state, dict) else {}, max_candidates=max_rows, display_only=True)
    out.setdefault("source", "central_candidates_capped")
    out.setdefault("buy_mode", "display_capped")
    out["candidate_limit"] = max_rows
    return out


def install_live_candidate_display_routes(app: Any, base_module: Any, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> None:
    get_path = "/api/live/central_candidates"
    post_buy_path = "/api/live/manual_buy_intent"
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", "") == get_path
            and "GET" in set(getattr(route, "methods", set()) or set())
        )
        and not (
            getattr(route, "path", "") == post_buy_path
            and "POST" in set(getattr(route, "methods", set()) or set())
        )
    ]

    @app.get(get_path)
    def central_candidates_candidate_only(include_blocked: bool = False):
        return live_candidate_state(base_module, include_blocked=include_blocked, max_candidates=max_candidates)

    @app.post(post_buy_path)
    def manual_buy_intent_disabled_candidate_only():
        raise HTTPException(status_code=403, detail="live candidate-only mode: BUY is disabled; candidates are display-only")
