"""File-backed manual SELL intent bridge.

Dashboard/API code records an intent only. The live paper process consumes the
intent and exits through the existing Runner/PositionManager pending-order path.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from engine.live.position_manager import POSITIONS_PATH

UTC = ZoneInfo("UTC")
KST = ZoneInfo("Asia/Seoul")
SYS_DIR = Path("data/_system")
MANUAL_SELL_INTENT_PATH = SYS_DIR / "manual_sell_intent.json"
SHARE_EPS = 1e-6

PENDING_STATUSES = {"pending"}
ACTIVE_STATUSES = {"pending", "submitted"}
IDEMPOTENT_STATUSES = {"pending", "submitted", "consumed"}
TERMINAL_STATUSES = {"consumed", "rejected"}


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def trade_date_kst(now: Optional[datetime] = None) -> str:
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST).date().isoformat()


def read_json(path: Path | str, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if data is not None else default
    except Exception:
        return default


def atomic_write_json(path: Path | str, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def _normalize_ticker(ticker: str) -> str:
    return str(ticker or "").strip().upper()


def _to_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _intent_id_for(ticker: str, entry_date: str = "") -> str:
    ticker_u = _normalize_ticker(ticker)
    digest = hashlib.sha256(str(entry_date or ticker_u).encode("utf-8")).hexdigest()[:12]
    return f"manual_sell:{ticker_u}:{digest}"


def _load_positions(positions_path: Path | str | None = None) -> dict:
    data = read_json(positions_path or POSITIONS_PATH, {})
    if not isinstance(data, dict):
        raise ValueError("positions root is not an object")
    return data


def load_manual_sell_state(path: Path | str | None = None) -> dict:
    data = read_json(path or MANUAL_SELL_INTENT_PATH, {})
    if not isinstance(data, dict):
        return {"schema_version": 1, "trade_date": "", "intents": {}}
    data.setdefault("schema_version", 1)
    data.setdefault("trade_date", "")
    data.setdefault("intents", {})
    if not isinstance(data.get("intents"), dict):
        data["intents"] = {}
    return data


def _iter_intents_by_status(path: Path | str | None, statuses: Iterable[str], trade_date: Optional[str] = None) -> list[dict]:
    wanted = {str(s or "") for s in statuses}
    data = load_manual_sell_state(path)
    if trade_date is not None and data.get("trade_date") != trade_date:
        return []
    out = []
    for intent_id, row in (data.get("intents") or {}).items():
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "") not in wanted:
            continue
        copy = dict(row)
        copy["intent_id"] = intent_id
        out.append(copy)
    return out


def load_pending_manual_sell_intents(*, intent_path: Path | str | None = None, trade_date: Optional[str] = None) -> list[dict]:
    return _iter_intents_by_status(intent_path, PENDING_STATUSES, trade_date=trade_date)


def load_submitted_manual_sell_intents(*, intent_path: Path | str | None = None, trade_date: Optional[str] = None) -> list[dict]:
    return _iter_intents_by_status(intent_path, {"submitted"}, trade_date=trade_date)


def create_manual_sell_intent(
    *,
    ticker: str,
    shares_requested: Optional[float] = None,
    source: str = "dashboard",
    positions_path: Path | str | None = None,
    intent_path: Path | str | None = None,
) -> dict:
    ticker_u = _normalize_ticker(ticker)
    if not ticker_u:
        raise ValueError("ticker required")
    positions = _load_positions(positions_path or POSITIONS_PATH)
    position = positions.get(ticker_u)
    if not isinstance(position, dict):
        raise ValueError("not held")
    held_shares = _to_float(position.get("shares"), 0.0)
    if held_shares <= SHARE_EPS:
        raise ValueError("not held")

    requested = held_shares
    if shares_requested is not None:
        raw = _to_float(shares_requested, 0.0)
        if raw <= SHARE_EPS:
            raise ValueError("shares_requested must be positive")
        if raw + SHARE_EPS < held_shares:
            raise ValueError("partial sell not supported")
        requested = held_shares

    entry_date = str(position.get("entry_date") or "")
    intent_id = _intent_id_for(ticker_u, entry_date)
    trade_date = trade_date_kst()
    state = load_manual_sell_state(intent_path or MANUAL_SELL_INTENT_PATH)
    if state.get("trade_date") != trade_date:
        state = {"schema_version": 1, "trade_date": trade_date, "intents": {}}
    intents = state.setdefault("intents", {})

    existing = intents.get(intent_id)
    if isinstance(existing, dict) and str(existing.get("status") or "") in IDEMPOTENT_STATUSES:
        out = dict(existing)
        out["intent_id"] = intent_id
        return out

    now = utc_now_iso()
    row = {
        "intent_id": intent_id,
        "ticker": ticker_u,
        "trade_date": trade_date,
        "status": "pending",
        "source": source or "dashboard",
        "shares_requested": float(requested),
        "shares_at_request": float(held_shares),
        "entry_date": entry_date,
        "entry_price": _to_float(position.get("entry_price"), 0.0),
        "created_at": now,
        "updated_at": now,
        "submitted_at": "",
        "consumed_at": "",
        "rejected_at": "",
        "reason": "manual_exit",
        "note": "",
    }
    intents[intent_id] = row
    state["updated_at"] = now
    atomic_write_json(intent_path or MANUAL_SELL_INTENT_PATH, state)
    return row


def mark_sell_intent_status(
    intent_id: str,
    status: str,
    *,
    intent_path: Path | str | None = None,
    note: str = "",
    order_id: str = "",
) -> dict:
    state = load_manual_sell_state(intent_path or MANUAL_SELL_INTENT_PATH)
    row = (state.get("intents") or {}).get(str(intent_id or ""))
    if not isinstance(row, dict):
        return {}
    now = utc_now_iso()
    row["status"] = str(status or "")
    row["updated_at"] = now
    if status == "submitted":
        row["submitted_at"] = now
    elif status == "consumed":
        row["consumed_at"] = now
    elif status == "rejected":
        row["rejected_at"] = now
    if note:
        row["note"] = note
    if order_id:
        row["order_id"] = order_id
    state["updated_at"] = now
    atomic_write_json(intent_path or MANUAL_SELL_INTENT_PATH, state)
    return row
