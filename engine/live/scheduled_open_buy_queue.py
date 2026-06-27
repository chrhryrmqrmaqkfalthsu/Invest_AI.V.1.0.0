"""D-1 close selection → D open BUY queue for live central-control.

The module is intentionally file-backed and small.  Selection stores the exact
BuyDecision rulebook into the queue, and open execution passes that rulebook back
through the existing central-control order path so entity identity is preserved.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from engine.central.allocation_policy import BuyCandidate, BuyDecision, decide_buys
from engine.central.backtester import _decide_buys_with_selection_metric
from engine.central.models import normalize_ticker
from engine.live.trading_day import current_or_next_session, previous_session_date, session_open_dt
from engine.strategies.demo_rulebook import Signal

log = logging.getLogger("live.scheduled_open_buy_queue")

DEFAULT_SCHEDULED_OPEN_BUY_QUEUE_PATH = Path("data/_system/scheduled_open_buy_queue.json")
ET = ZoneInfo("America/New_York")


def _utc_now_iso() -> str:
    return datetime.now(ZoneInfo("UTC")).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _date_text(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)


def load_queue(path: str | Path = DEFAULT_SCHEDULED_OPEN_BUY_QUEUE_PATH) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"schema_version": 1, "items": [], "updated_at": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"scheduled open buy queue read failed: {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"scheduled open buy queue root must be object: {p}")
    data.setdefault("schema_version", 1)
    data.setdefault("items", [])
    return data


def save_queue(payload: dict[str, Any], path: str | Path = DEFAULT_SCHEDULED_OPEN_BUY_QUEUE_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload or {})
    payload["updated_at"] = _utc_now_iso()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def pending_items(payload: dict[str, Any], execution_session: date | str) -> list[dict[str, Any]]:
    session = _date_text(execution_session)
    return [
        row
        for row in payload.get("items", []) or []
        if isinstance(row, dict)
        and str(row.get("execution_session") or "") == session
        and str(row.get("status") or "") == "pending"
    ]


def mark_item_status(payload: dict[str, Any], candidate_id: str, status: str, *, note: str = "", fills: dict[str, Any] | None = None) -> None:
    for row in payload.get("items", []) or []:
        if isinstance(row, dict) and str(row.get("candidate_id") or "") == str(candidate_id or ""):
            row["status"] = str(status)
            row["status_updated_at"] = _utc_now_iso()
            if note:
                row["note"] = note
            if fills:
                row["fills"] = dict(fills)
            return


def decision_to_queue_item(
    decision: BuyDecision,
    *,
    signal_session: date | str,
    execution_session: date | str,
    reference_price: float,
    signal_score: float,
    signal_threshold: float,
    stage: str = "stage2",
) -> dict[str, Any]:
    ticker = normalize_ticker(decision.ticker)
    entity_id = str(decision.entity_id or "")
    return {
        "candidate_id": f"{_date_text(execution_session)}:{entity_id}",
        "ticker": ticker,
        "entity_id": entity_id,
        "stage": str(stage or "stage2"),
        "rulebook_hash": entity_id.split("_", 1)[1] if "_" in entity_id else "",
        "rulebook": dict(decision.rulebook or {}),
        "signal_session": _date_text(signal_session),
        "execution_session": _date_text(execution_session),
        "reference_price": float(reference_price or 0.0),
        "signal_score": float(signal_score or 0.0),
        "signal_threshold": float(signal_threshold or 0.0),
        "decision": asdict(decision),
        "notional": float(decision.notional or 0.0),
        "shares": float(decision.shares or 0.0),
        "status": "pending",
        "created_at": _utc_now_iso(),
    }


def _last_bar_date(df) -> date | None:
    if df is None or len(df) == 0:
        return None
    try:
        return pd.Timestamp(df.index[-1]).date()
    except Exception:
        return None


def _close_price(df) -> float:
    try:
        return float(df.iloc[-1].get("Close", df.iloc[-1].get("close", 0.0)) or 0.0)
    except Exception:
        return 0.0


class _EmptyLedger:
    def open_positions(self):
        return []


class NextOpenBuyCoordinator:
    """Prepare and execute next-open BUY queue for a LiveCentralController."""

    def __init__(
        self,
        *,
        controller,
        market_clock,
        queue_path: str | Path = DEFAULT_SCHEDULED_OPEN_BUY_QUEUE_PATH,
        preopen_select_minutes_before_open: int = 10,
        open_buy_delay_sec: int = 5,
    ) -> None:
        self.controller = controller
        self.market_clock = market_clock
        self.queue_path = Path(queue_path)
        self.preopen_select_minutes_before_open = max(1, int(preopen_select_minutes_before_open or 10))
        self.open_buy_delay_sec = max(0, int(open_buy_delay_sec or 0))

    def _now_et(self) -> datetime:
        try:
            return self.controller._now_et()
        except Exception:
            return datetime.now(ET)

    def prepare_if_due(self) -> dict[str, Any]:
        now = self._now_et()
        execution_session = current_or_next_session(self.market_clock, now)
        opened = session_open_dt(self.market_clock, execution_session)
        start = opened - timedelta(minutes=self.preopen_select_minutes_before_open)
        if not (start <= now < opened):
            return {"status": "not_due", "now": now.isoformat(), "window_start": start.isoformat(), "open": opened.isoformat()}
        payload = load_queue(self.queue_path)
        if any(
            str(row.get("execution_session") or "") == execution_session.isoformat()
            and str(row.get("status") or "") in {"pending", "executed", "blocked"}
            for row in payload.get("items", []) or []
            if isinstance(row, dict)
        ):
            return {"status": "already_prepared", "execution_session": execution_session.isoformat()}
        return self.prepare_queue(execution_session=execution_session)

    def prepare_queue(self, *, execution_session: date | None = None) -> dict[str, Any]:
        execution_session = execution_session or current_or_next_session(self.market_clock, self._now_et())
        signal_session = previous_session_date(self.market_clock, execution_session)
        provider = getattr(self.controller.runner, "rulebook", None)
        if provider is None or not hasattr(provider, "_get_ohlcv"):
            raise RuntimeError("next-open selection requires runner.rulebook._get_ohlcv")

        candidates: list[BuyCandidate] = []
        signal_by_entity: dict[str, Any] = {}
        price_by_entity: dict[str, float] = {}
        stage_by_entity: dict[str, str] = {}
        skipped: list[dict[str, Any]] = []
        evaluated_symbols = 0

        for ticker in sorted(set(self.controller.entity_by_ticker)):
            ticker_u = normalize_ticker(ticker)
            if not self.controller._is_live_whitelisted_ticker(ticker_u):
                skipped.append({"ticker": ticker_u, "reason": "whitelist_missing"})
                continue
            try:
                df = provider._get_ohlcv(ticker_u)
            except Exception as exc:
                skipped.append({"ticker": ticker_u, "reason": "ohlcv_error", "error": str(exc)})
                continue
            last_date = _last_bar_date(df)
            if last_date != signal_session:
                skipped.append({
                    "ticker": ticker_u,
                    "reason": "stale_bar",
                    "last_bar_date": last_date.isoformat() if last_date else "",
                    "expected": signal_session.isoformat(),
                })
                continue
            price = _close_price(df)
            if price <= 0.0:
                skipped.append({"ticker": ticker_u, "reason": "invalid_reference_price"})
                continue
            evaluated_symbols += 1
            for entity in self.controller.entity_by_ticker.get(ticker_u, []):
                sig = self.controller._evaluate_entity_signal(entity, price)
                if sig.signal != Signal.BUY:
                    continue
                confidence = float(getattr(entity, "confidence", 0.0) or 0.0)
                strength, _orig_strength, guard_reason = self.controller._candidate_strength_for_entity(entity, sig, confidence)
                if strength is None:
                    skipped.append({"ticker": ticker_u, "entity_id": entity.entity_id, "reason": "strength_guard", "note": guard_reason})
                    continue
                candidates.append(
                    BuyCandidate(
                        entity_id=entity.entity_id,
                        ticker=entity.ticker,
                        confidence=confidence,
                        strength=float(strength or 0.0),
                        price=price,
                        signal_score=float(getattr(sig, "score", 0.0) or 0.0),
                        threshold=float(getattr(sig, "threshold", 0.0) or 0.0),
                        rulebook=dict(getattr(entity, "rulebook", {}) or {}),
                    )
                )
                signal_by_entity[entity.entity_id] = sig
                price_by_entity[entity.entity_id] = price
                try:
                    from engine.live.central_control import _entity_stage

                    stage_by_entity[entity.entity_id] = _entity_stage(entity)
                except Exception:
                    stage_by_entity[entity.entity_id] = "stage2"

        alloc = self.controller._allocation_params()
        # Queue preparation is explicitly for a clean reset/open entry cycle.  The
        # execution guard below prevents any BUY until local positions, broker
        # holdings, and pending orders are all flat.
        if self.controller.selection_metric == "confidence":
            decisions = decide_buys(candidates, _EmptyLedger(), alloc)
        else:
            decisions = _decide_buys_with_selection_metric(candidates, _EmptyLedger(), alloc, self.controller.selection_scores)

        items = []
        for decision in decisions:
            sig = signal_by_entity.get(decision.entity_id)
            items.append(
                decision_to_queue_item(
                    decision,
                    signal_session=signal_session,
                    execution_session=execution_session,
                    reference_price=price_by_entity.get(decision.entity_id, 0.0),
                    signal_score=float(getattr(sig, "score", 0.0) or 0.0),
                    signal_threshold=float(getattr(sig, "threshold", 0.0) or 0.0),
                    stage=stage_by_entity.get(decision.entity_id, "stage2"),
                )
            )

        payload = {
            "schema_version": 1,
            "status": "pending" if items else "empty",
            "signal_session": signal_session.isoformat(),
            "execution_session": execution_session.isoformat(),
            "created_at": _utc_now_iso(),
            "items": items,
            "diagnostics": {
                "evaluated_symbols": evaluated_symbols,
                "candidate_count": len(candidates),
                "decision_count": len(decisions),
                "skipped_count": len(skipped),
                "skipped": skipped[:200],
            },
        }
        save_queue(payload, self.queue_path)
        log.warning(
            "[NEXT-OPEN] queue prepared execution_session=%s signal_session=%s candidates=%s decisions=%s skipped=%s path=%s",
            execution_session,
            signal_session,
            len(candidates),
            len(decisions),
            len(skipped),
            self.queue_path,
        )
        return {"status": payload["status"], "decision_count": len(decisions), "candidate_count": len(candidates), "skipped_count": len(skipped)}

    def execute_if_due(self) -> dict[str, Any]:
        now = self._now_et()
        execution_session = current_or_next_session(self.market_clock, now)
        opened = session_open_dt(self.market_clock, execution_session)
        due = opened + timedelta(seconds=self.open_buy_delay_sec)
        if now < due:
            return {"status": "not_due", "now": now.isoformat(), "due": due.isoformat()}
        return self.execute_queue(execution_session=execution_session)

    def _flat_guard(self) -> tuple[bool, str]:
        try:
            local_positions = list(self.controller.runner.position_manager.all())
        except Exception as exc:
            return False, f"position_manager_unavailable:{exc}"
        if local_positions:
            return False, f"local_positions_not_empty:{len(local_positions)}"
        pending_mgr = getattr(self.controller.runner, "pending_order_manager", None)
        if pending_mgr is not None:
            try:
                pending = list(pending_mgr.all())
            except Exception as exc:
                return False, f"pending_manager_unavailable:{exc}"
            if pending:
                return False, f"pending_orders_not_empty:{len(pending)}"
        try:
            holdings = list(self.controller.runner.broker.get_holdings())
        except Exception as exc:
            return False, f"broker_holdings_unavailable:{exc}"
        live_holdings = [h for h in holdings if float(getattr(h, "shares", 0.0) or 0.0) > 1e-6]
        if live_holdings:
            return False, f"broker_positions_not_empty:{len(live_holdings)}"
        return True, "flat"

    def execute_queue(self, *, execution_session: date | None = None) -> dict[str, Any]:
        execution_session = execution_session or current_or_next_session(self.market_clock, self._now_et())
        payload = load_queue(self.queue_path)
        rows = pending_items(payload, execution_session)
        if not rows:
            return {"status": "no_pending", "execution_session": execution_session.isoformat()}
        clear, reason = self._flat_guard()
        if not clear:
            log.warning("[NEXT-OPEN] BUY execution waiting: %s", reason)
            return {"status": "waiting_for_clear", "reason": reason, "pending": len(rows)}

        executed = 0
        blocked = 0
        for row in rows:
            candidate_id = str(row.get("candidate_id") or "")
            decision_raw = dict(row.get("decision") or {})
            ticker = normalize_ticker(decision_raw.get("ticker") or row.get("ticker"))
            try:
                reference_price = float(self.controller.runner.broker.get_current_price(ticker) or 0.0)
            except Exception:
                reference_price = 0.0
            if reference_price <= 0.0:
                reference_price = float(row.get("reference_price") or 0.0)
            try:
                decision = BuyDecision(
                    entity_id=str(decision_raw.get("entity_id") or row.get("entity_id") or ""),
                    ticker=ticker,
                    shares=float(decision_raw.get("shares") or row.get("shares") or 0.0),
                    notional=float(decision_raw.get("notional") or row.get("notional") or 0.0),
                    score=float(decision_raw.get("score") or 0.0),
                    confidence=float(decision_raw.get("confidence") or 0.0),
                    strength=float(decision_raw.get("strength") or 0.0),
                    rulebook=dict(row.get("rulebook") or decision_raw.get("rulebook") or {}),
                    purpose=str(decision_raw.get("purpose") or "entry"),
                    target_position_id=str(decision_raw.get("target_position_id") or ""),
                )
                ok = self.controller._execute_decision(
                    decision,
                    None,
                    reference_price,
                    execution_reason="next_open_queue",
                )
            except Exception as exc:
                ok = False
                mark_item_status(payload, candidate_id, "blocked", note=f"exception:{type(exc).__name__}:{exc}")
                blocked += 1
                continue
            if ok:
                mark_item_status(payload, candidate_id, "executed", fills={"reference_price": reference_price, "executed_at": _utc_now_iso()})
                executed += 1
            else:
                mark_item_status(payload, candidate_id, "blocked", note="runner blocked or did not attempt order")
                blocked += 1
        payload["status"] = "executed" if blocked == 0 else "partial_or_blocked"
        save_queue(payload, self.queue_path)
        log.warning("[NEXT-OPEN] queue execution complete executed=%s blocked=%s path=%s", executed, blocked, self.queue_path)
        return {"status": payload["status"], "executed": executed, "blocked": blocked}
