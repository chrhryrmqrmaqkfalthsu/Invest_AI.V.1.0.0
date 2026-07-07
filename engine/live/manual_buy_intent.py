"""File-backed semi-auto BUY intent bridge.

Dashboard code never imports a broker or submits orders. It can only request an
already-published central-control candidate. The live paper process consumes the
intent and executes through the existing Runner/_try_order path.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
REPO_ROOT = Path(__file__).resolve().parents[2]
SYS_DIR = Path("data/_system")
MANUAL_BUY_INTENT_PATH = SYS_DIR / "manual_buy_intent.json"
CENTRAL_BUY_CANDIDATES_PATH = SYS_DIR / "central_buy_candidates.json"
TERMINAL_CANDIDATE_STATUSES = {"manual_executed", "auto_executed", "blocked", "expired"}
LIMIT_NOTIONAL_RETRY_CODE = "LIMIT_NOTIONAL"
MAX_LIMIT_NOTIONAL_RETRIES = 1


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def trade_date_et(now: Optional[datetime] = None) -> str:
    current = now or datetime.now(ET)
    if current.tzinfo is None:
        current = current.replace(tzinfo=ET)
    return current.astimezone(ET).date().isoformat()


def _live_path(path: Path | str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def _guard_pytest_live_write(path: Path | str) -> None:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        resolved = Path(path).resolve()
        protected = {
            _live_path(MANUAL_BUY_INTENT_PATH).resolve(): "manual_buy_intent.json",
            _live_path(CENTRAL_BUY_CANDIDATES_PATH).resolve(): "central_buy_candidates.json",
        }
        name = protected.get(resolved)
        if name:
            raise RuntimeError(f"test attempted to write live {name}; inject intent_path/candidate_path")
    except RuntimeError:
        raise
    except Exception:
        return


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
    _guard_pytest_live_write(path)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def candidate_id_for(trade_date: str, entity_id: str) -> str:
    return f"{trade_date}:{str(entity_id or '').strip()}"


def candidate_from_decision(decision, signal_result, price: float, *, trade_date: str) -> dict:
    cid = candidate_id_for(trade_date, getattr(decision, "entity_id", ""))
    return {
        "candidate_id": cid,
        "trade_date": trade_date,
        "status": "pending",
        "ticker": str(getattr(decision, "ticker", "") or "").upper(),
        "entity_id": str(getattr(decision, "entity_id", "") or ""),
        "notional": float(getattr(decision, "notional", 0.0) or 0.0),
        "shares": float(getattr(decision, "shares", 0.0) or 0.0),
        "price": float(price or 0.0),
        "score": float(getattr(decision, "score", 0.0) or 0.0),
        "confidence": float(getattr(decision, "confidence", 0.0) or 0.0),
        "strength": float(getattr(decision, "strength", 0.0) or 0.0),
        "orig_strength": float(getattr(signal_result, "orig_strength", getattr(decision, "strength", 0.0)) or 0.0) if signal_result is not None else float(getattr(decision, "strength", 0.0) or 0.0),
        "effective_strength": float(getattr(signal_result, "effective_strength", getattr(decision, "strength", 0.0)) or 0.0) if signal_result is not None else float(getattr(decision, "strength", 0.0) or 0.0),
        "strength_guard": str(getattr(signal_result, "strength_guard", "") or "") if signal_result is not None else "",
        "signal_score": float(getattr(signal_result, "score", 0.0) or 0.0) if signal_result is not None else 0.0,
        "signal_threshold": float(getattr(signal_result, "threshold", 0.0) or 0.0) if signal_result is not None else 0.0,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }


def load_candidate_state(path: Path | str = CENTRAL_BUY_CANDIDATES_PATH) -> dict:
    data = read_json(path, {})
    if not isinstance(data, dict):
        return {"schema_version": 1, "trade_date": "", "candidates": {}}
    data.setdefault("schema_version", 1)
    data.setdefault("trade_date", "")
    data.setdefault("candidates", {})
    if not isinstance(data.get("candidates"), dict):
        data["candidates"] = {}
    return data


def publish_candidate_state(
    candidates: Iterable[dict],
    *,
    path: Path | str = CENTRAL_BUY_CANDIDATES_PATH,
    buy_mode: str = "semi_auto",
    trade_date: Optional[str] = None,
) -> dict:
    rows = list(candidates or [])
    if trade_date is None:
        trade_date = rows[0].get("trade_date") if rows else trade_date_et()
    previous = load_candidate_state(path)
    previous_candidates = previous.get("candidates", {}) if previous.get("trade_date") == trade_date else {}
    out_candidates: dict[str, dict] = {}
    now = utc_now_iso()
    for row in rows:
        cid = str(row.get("candidate_id") or "")
        if not cid:
            continue
        merged = dict(row)
        old = previous_candidates.get(cid)
        if isinstance(old, dict):
            old_status = str(old.get("status") or "pending")
            if old_status in TERMINAL_CANDIDATE_STATUSES or old_status == "manual_requested":
                merged.update({k: v for k, v in old.items() if k not in {"updated_at"}})
                merged.setdefault("created_at", row.get("created_at") or now)
        merged["updated_at"] = now
        out_candidates[cid] = merged
    for cid, old in previous_candidates.items():
        if cid in out_candidates or not isinstance(old, dict):
            continue
        if str(old.get("status") or "") in TERMINAL_CANDIDATE_STATUSES:
            out_candidates[cid] = old
    state = {
        "schema_version": 1,
        "trade_date": trade_date,
        "buy_mode": buy_mode,
        "updated_at": now,
        "candidates": out_candidates,
    }
    atomic_write_json(path, state)
    return state


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _manual_notional_or_none(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except Exception:
        raise ValueError("manual notional must be numeric")
    if out != out or out <= 0.0:
        raise ValueError("manual notional must be positive")
    return out


def _has_limit_notional_text(*values) -> bool:
    haystack = " ".join(str(v or "") for v in values).upper()
    return LIMIT_NOTIONAL_RETRY_CODE in haystack


def _has_legacy_limit_notional_log_evidence(candidate_id: str, ticker: str, *, logs_dir: Path | str = "logs") -> bool:
    """Return True only for legacy blocked rows whose state lacks block_code.

    Older live code stored a generic note but logged the precise SafetyLayer code.
    This keeps same-day one-off retry possible without hand-editing state files.
    """
    ticker_u = str(ticker or "").upper()
    cid = str(candidate_id or "")
    if not ticker_u or not cid:
        return False
    root = Path(logs_dir)
    try:
        files = sorted(list(root.glob("live_semiauto*.log")) + list(root.glob("live_semiauto_buffer*.log")), key=lambda p: p.stat().st_mtime)[-12:]
    except Exception:
        files = []
    for path in reversed(files):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for idx, line in enumerate(lines):
            line_u = line.upper()
            if ticker_u not in line_u or LIMIT_NOTIONAL_RETRY_CODE not in line_u:
                continue
            window = "\n".join(lines[idx : idx + 6])
            if cid in window:
                return True
    return False


def _limit_notional_retry_allowed(candidate: dict, candidate_id: str) -> bool:
    status = str(candidate.get("status") or "")
    if status != "blocked":
        return False
    if _as_int(candidate.get("retry_count"), 0) >= MAX_LIMIT_NOTIONAL_RETRIES:
        return False
    if _has_limit_notional_text(
        candidate.get("block_code"),
        candidate.get("block_reason"),
        candidate.get("retry_code"),
        candidate.get("retry_reason"),
        candidate.get("note"),
    ):
        return True
    return _has_legacy_limit_notional_log_evidence(candidate_id, str(candidate.get("ticker") or ""))


def create_manual_buy_intent(
    *,
    candidate_id: str,
    source: str = "dashboard",
    manual_notional: float | None = None,
    candidate_path: Path | str = CENTRAL_BUY_CANDIDATES_PATH,
    intent_path: Path | str = MANUAL_BUY_INTENT_PATH,
) -> dict:
    candidate_id = str(candidate_id or "").strip()
    if not candidate_id:
        raise ValueError("candidate_id required")
    manual_notional_value = _manual_notional_or_none(manual_notional)
    candidate_state = load_candidate_state(candidate_path)
    candidate = (candidate_state.get("candidates") or {}).get(candidate_id)
    if not isinstance(candidate, dict):
        raise ValueError(f"candidate not found or stale: {candidate_id}")
    status = str(candidate.get("status") or "pending")
    retrying_limit_notional = False
    retry_count = _as_int(candidate.get("retry_count"), 0)
    if status not in {"pending", "manual_requested"}:
        if _limit_notional_retry_allowed(candidate, candidate_id):
            retrying_limit_notional = True
            retry_count += 1
        else:
            raise ValueError(f"candidate is not pending: {candidate_id}")
    trade_date = str(candidate.get("trade_date") or candidate_state.get("trade_date") or "")
    if not trade_date:
        raise ValueError("candidate trade_date missing")
    intent_state = read_json(intent_path, {})
    if not isinstance(intent_state, dict) or intent_state.get("trade_date") != trade_date:
        intent_state = {"schema_version": 1, "trade_date": trade_date, "intents": {}}
    intents = intent_state.setdefault("intents", {})
    now = utc_now_iso()
    existing_id = ""
    existing_row = None
    for intent_id, row0 in intents.items():
        if not isinstance(row0, dict):
            continue
        if row0.get("candidate_id") == candidate_id and str(row0.get("status") or "") == "pending":
            existing_id = str(intent_id)
            existing_row = row0
            break
    if existing_row is not None:
        if manual_notional_value is not None:
            candidate_notional = float(candidate.get("notional") or 0.0)
            existing_row["notional"] = manual_notional_value
            existing_row["manual_notional"] = manual_notional_value
            existing_row["candidate_notional"] = candidate_notional
            existing_row["notional_source"] = "dashboard_amount"
            existing_row["updated_at"] = now
            intent_state["updated_at"] = now
            atomic_write_json(intent_path, intent_state)
            candidate["manual_requested_notional"] = manual_notional_value
            candidate["manual_notional"] = manual_notional_value
            candidate["notional_source"] = "dashboard_amount"
            candidate["updated_at"] = now
            candidate_state["updated_at"] = now
            atomic_write_json(candidate_path, candidate_state)
        return dict(existing_row, intent_id=existing_id)
    intent_id = f"manual-retry{retry_count}:{candidate_id}" if retrying_limit_notional else f"manual:{candidate_id}"
    candidate_notional = float(candidate.get("notional") or 0.0)
    order_notional = manual_notional_value if manual_notional_value is not None else candidate_notional
    row = {
        "intent_id": intent_id,
        "candidate_id": candidate_id,
        "trade_date": trade_date,
        "status": "pending",
        "source": source,
        "ticker": candidate.get("ticker"),
        "entity_id": candidate.get("entity_id"),
        "notional": float(order_notional or 0.0),
        "candidate_notional": candidate_notional,
        "price": float(candidate.get("price") or 0.0),
        "created_at": now,
        "updated_at": now,
        "consumed_at": "",
        "rejected_at": "",
        "reason": "manual_timing",
    }
    if manual_notional_value is not None:
        row["manual_notional"] = manual_notional_value
        row["notional_source"] = "dashboard_amount"
    if retrying_limit_notional:
        row["retry_count"] = retry_count
        row["retry_of"] = str(candidate.get("manual_intent_id") or "")
        row["retry_code"] = LIMIT_NOTIONAL_RETRY_CODE
        row["retry_reason"] = "blocked LIMIT_NOTIONAL candidate retry requested by dashboard"
    intents[intent_id] = row
    intent_state["updated_at"] = now
    atomic_write_json(intent_path, intent_state)
    candidate["status"] = "manual_requested"
    candidate["manual_intent_id"] = intent_id
    candidate["updated_at"] = now
    if manual_notional_value is not None:
        candidate["manual_requested_notional"] = manual_notional_value
        candidate["manual_notional"] = manual_notional_value
        candidate["notional_source"] = "dashboard_amount"
    if retrying_limit_notional:
        candidate["retry_count"] = retry_count
        candidate["retry_code"] = LIMIT_NOTIONAL_RETRY_CODE
        candidate["retry_reason"] = "blocked LIMIT_NOTIONAL retry requested"
        candidate["retry_requested_at"] = now
    candidate_state["updated_at"] = now
    atomic_write_json(candidate_path, candidate_state)
    return row


def _apply_manual_notional_overrides_to_caller_decisions(intents: list[dict]) -> None:
    """Patch LiveCentralController's local decision map with intent notionals.

    The central controller imports this module-level loader and then immediately
    executes decisions from its local ``decision_by_candidate_id`` dictionary. To
    keep the public controller flow unchanged while allowing dashboard-entered
    amounts, this helper replaces matching frozen BuyDecision objects in that
    local dict before the controller reads them.
    """
    try:
        import inspect
        from dataclasses import replace as dataclass_replace

        frame = inspect.currentframe()
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is None:
            return
        decisions = caller.f_locals.get("decision_by_candidate_id")
        prices = caller.f_locals.get("price_by_candidate_id")
        if not isinstance(decisions, dict):
            return
        if not isinstance(prices, dict):
            prices = {}
        for intent in intents:
            if not isinstance(intent, dict):
                continue
            cid = str(intent.get("candidate_id") or "")
            if not cid or cid not in decisions:
                continue
            amount = _manual_notional_or_none(intent.get("notional"))
            if amount is None:
                continue
            decision = decisions.get(cid)
            if decision is None:
                continue
            try:
                price = float(prices.get(cid) or 0.0)
            except Exception:
                price = 0.0
            try:
                shares = amount / price if price > 0.0 else float(getattr(decision, "shares", 0.0) or 0.0)
                decisions[cid] = dataclass_replace(decision, notional=amount, shares=shares)
            except Exception:
                continue
    except Exception:
        return
    finally:
        try:
            del frame
        except Exception:
            pass


def load_pending_manual_intents(
    *,
    intent_path: Path | str = MANUAL_BUY_INTENT_PATH,
    trade_date: Optional[str] = None,
) -> list[dict]:
    data = read_json(intent_path, {})
    if not isinstance(data, dict):
        return []
    if trade_date is not None and data.get("trade_date") != trade_date:
        return []
    out = []
    for intent_id, row in (data.get("intents") or {}).items():
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "") != "pending":
            continue
        copy = dict(row)
        copy["intent_id"] = intent_id
        out.append(copy)
    _apply_manual_notional_overrides_to_caller_decisions(out)
    return out


def mark_intent_status(
    intent_id: str,
    status: str,
    *,
    intent_path: Path | str = MANUAL_BUY_INTENT_PATH,
    note: str = "",
    block_code: str = "",
    block_reason: str = "",
) -> dict:
    data = read_json(intent_path, {})
    if not isinstance(data, dict):
        return {}
    row = (data.get("intents") or {}).get(intent_id)
    if not isinstance(row, dict):
        return {}
    now = utc_now_iso()
    row["status"] = status
    row["updated_at"] = now
    if status == "consumed":
        row["consumed_at"] = now
    elif status in {"rejected", "blocked"}:
        row["rejected_at"] = now
    if note:
        row["note"] = note
    if block_code:
        row["block_code"] = block_code
    if block_reason:
        row["block_reason"] = block_reason
    data["updated_at"] = now
    atomic_write_json(intent_path, data)
    return row


def mark_candidate_status(
    candidate_id: str,
    status: str,
    *,
    candidate_path: Path | str = CENTRAL_BUY_CANDIDATES_PATH,
    manual_intent_id: str = "",
    note: str = "",
    block_code: str = "",
    block_reason: str = "",
) -> dict:
    data = load_candidate_state(candidate_path)
    row = (data.get("candidates") or {}).get(candidate_id)
    if not isinstance(row, dict):
        return {}
    now = utc_now_iso()
    row["status"] = status
    row["updated_at"] = now
    if manual_intent_id:
        row["manual_intent_id"] = manual_intent_id
    if note:
        row["note"] = note
    if block_code:
        row["block_code"] = block_code
    if block_reason:
        row["block_reason"] = block_reason
    data["updated_at"] = now
    atomic_write_json(candidate_path, data)
    return row
