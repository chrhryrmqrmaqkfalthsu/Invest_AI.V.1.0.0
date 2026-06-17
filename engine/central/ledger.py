"""Entity-level position ledger for the central controller.

The existing live runner treats a ticker as the execution/position unit. This
ledger treats an entity position as the accounting unit and reconciles ticker
sums against the broker's ticker-level holdings.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from engine.central.broker_port import LedgerBrokerPort
from engine.central.models import ExecutionRecord, IntentRecord, PositionRecord, enum_value, normalize_shares, normalize_ticker
from engine.live.broker.base import Order, OrderSide, OrderStatus, OrderType

SCHEMA_VERSION = 1
DEFAULT_LEDGER_DIR = Path("data/_system/central")
POSITIONS_FILE = "ledger_positions.json"
EXECUTIONS_FILE = "ledger_executions.json"
INTENTS_FILE = "ledger_intents.json"
EPSILON = 1e-6
TERMINAL_STATUSES = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED}
REJECTED_STATUSES = {OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED}
UNRESOLVED_INTENT_STATUSES = {"open", "dispatched", "pending", "partial"}


class LedgerError(Exception):
    """Base exception for central ledger errors."""


class LedgerUnavailableError(LedgerError):
    """Raised when persisted ledger state failed to load and the ledger is fail-closed."""


class ReconcileBlockedError(LedgerError):
    """Raised when a ticker is blocked after reconcile mismatch."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _order_status_value(status) -> str:
    return enum_value(status).lower()


def _order_side_value(side) -> str:
    return enum_value(side).lower()


class EntityPositionLedger:
    """Entity-level ledger with fill-delta accounting and broker reconciliation."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        positions_path: Optional[Path] = None,
        executions_path: Optional[Path] = None,
        intents_path: Optional[Path] = None,
    ) -> None:
        root = Path(base_dir) if base_dir is not None else DEFAULT_LEDGER_DIR
        self.positions_path = Path(positions_path or (root / POSITIONS_FILE))
        self.executions_path = Path(executions_path or (root / EXECUTIONS_FILE))
        self.intents_path = Path(intents_path or (root / INTENTS_FILE))
        self._positions: Dict[str, PositionRecord] = {}
        self._executions: Dict[str, ExecutionRecord] = {}
        self._intents: Dict[str, IntentRecord] = {}
        self._reconcile_blocked_tickers: set[str] = set()
        self._load_error = ""
        self._load_all()

    @property
    def load_error(self) -> str:
        return self._load_error

    @property
    def reconcile_blocked_tickers(self) -> List[str]:
        return sorted(self._reconcile_blocked_tickers)

    def get_position(self, position_id: str) -> Optional[PositionRecord]:
        return self._positions.get(str(position_id or ""))

    def get_execution(self, execution_id: str) -> Optional[ExecutionRecord]:
        return self._executions.get(str(execution_id or ""))

    def get_intent(self, intent_id: str) -> Optional[IntentRecord]:
        return self._intents.get(str(intent_id or ""))

    def open_positions(self, ticker: Optional[str] = None, entity_id: Optional[str] = None) -> List[PositionRecord]:
        ticker_u = normalize_ticker(ticker) if ticker else ""
        entity = str(entity_id or "")
        rows = []
        for pos in self._positions.values():
            if normalize_shares(pos.open_shares) <= EPSILON:
                continue
            if ticker_u and pos.ticker != ticker_u:
                continue
            if entity and pos.entity_id != entity:
                continue
            rows.append(pos)
        return sorted(rows, key=lambda p: (p.ticker, p.entity_id, p.position_id))

    def open_intent(
        self,
        entity_id: str,
        ticker: str,
        side: str,
        purpose: str,
        requested_shares: float,
        reason: str,
        target_position_id: Optional[str] = None,
    ) -> IntentRecord:
        self._ensure_available()
        ticker_u = normalize_ticker(ticker)
        side_v = _order_side_value(side)
        purpose_v = str(purpose or "entry").strip().lower()
        shares_n = normalize_shares(requested_shares)
        if not entity_id:
            raise ValueError("entity_id required")
        if not ticker_u:
            raise ValueError("ticker required")
        if side_v not in {OrderSide.BUY.value, OrderSide.SELL.value}:
            raise ValueError(f"unsupported side: {side}")
        if shares_n <= EPSILON:
            raise ValueError("requested_shares must be positive")
        if ticker_u in self._reconcile_blocked_tickers:
            raise ReconcileBlockedError(f"ticker {ticker_u} is reconcile-blocked")
        if side_v == OrderSide.SELL.value and not target_position_id:
            raise ValueError("sell intent requires target_position_id")

        for intent in self._intents.values():
            if (
                intent.entity_id == entity_id
                and intent.ticker == ticker_u
                and intent.side == side_v
                and intent.purpose == purpose_v
                and intent.status in UNRESOLVED_INTENT_STATUSES
                and intent.target_position_id == str(target_position_id or "")
            ):
                return intent

        now = _now_iso()
        intent = IntentRecord(
            intent_id=_new_id("intent"),
            entity_id=str(entity_id),
            ticker=ticker_u,
            side=side_v,
            purpose=purpose_v,
            target_position_id=str(target_position_id or ""),
            requested_shares=shares_n,
            reason=str(reason or ""),
            created_at=now,
            status="open",
            linked_execution_id="",
        )
        self._intents[intent.intent_id] = intent
        self._save_intents()
        return intent

    def dispatch_execution(self, intent_id: str, broker: LedgerBrokerPort, client_order_id: str) -> ExecutionRecord:
        self._ensure_available()
        intent = self._intents.get(str(intent_id or ""))
        if intent is None:
            raise KeyError(f"intent not found: {intent_id}")
        if intent.linked_execution_id and intent.linked_execution_id in self._executions:
            return self._executions[intent.linked_execution_id]
        if intent.ticker in self._reconcile_blocked_tickers:
            raise ReconcileBlockedError(f"ticker {intent.ticker} is reconcile-blocked")

        if intent.side == OrderSide.BUY.value:
            order = broker.place_buy(intent.ticker, intent.requested_shares, OrderType.MARKET, 0.0, client_order_id)
        elif intent.side == OrderSide.SELL.value:
            order = broker.place_sell(intent.ticker, intent.requested_shares, OrderType.MARKET, 0.0, client_order_id)
        else:
            raise ValueError(f"unsupported side: {intent.side}")

        now = _now_iso()
        state = self._state_from_order(order)
        execution = ExecutionRecord(
            execution_id=_new_id("exec"),
            intent_id=intent.intent_id,
            position_id=intent.target_position_id,
            entity_id=intent.entity_id,
            ticker=intent.ticker,
            side=intent.side,
            purpose=intent.purpose,
            order_id=str(order.order_id or ""),
            client_order_id=str(getattr(order, "client_order_id", "") or client_order_id or ""),
            requested_shares=normalize_shares(intent.requested_shares),
            already_applied_filled_shares=0.0,
            filled_shares=normalize_shares(getattr(order, "filled_shares", 0.0)),
            filled_avg_price=float(getattr(order, "filled_avg_price", 0.0) or 0.0),
            state=state,
            broker_status=_order_status_value(order.status),
            raw_status=str(getattr(order, "raw_status", "") or ""),
            replaced_by=str(getattr(order, "replaced_by", "") or ""),
            created_at=now,
            submitted_at=str(getattr(order, "submitted_at", "") or now),
            last_polled_at="",
            updated_at=now,
        )
        self._executions[execution.execution_id] = execution
        intent.linked_execution_id = execution.execution_id
        intent.status = state
        self._save_executions()
        self._save_intents()

        if normalize_shares(getattr(order, "filled_shares", 0.0)) > EPSILON:
            execution = self.apply_fill(execution.execution_id, order)
        elif order.status in REJECTED_STATUSES:
            intent.status = state
            self._save_intents()
        return execution

    def apply_fill(self, execution_id: str, order: Order) -> ExecutionRecord:
        self._ensure_available()
        execution = self._executions.get(str(execution_id or ""))
        if execution is None:
            raise KeyError(f"execution not found: {execution_id}")

        cumulative_filled = normalize_shares(getattr(order, "filled_shares", 0.0))
        previous_applied = normalize_shares(execution.already_applied_filled_shares)
        fill_delta = normalize_shares(cumulative_filled - previous_applied)
        cumulative_avg = float(getattr(order, "filled_avg_price", 0.0) or execution.filled_avg_price or getattr(order, "price", 0.0) or 0.0)

        if fill_delta <= EPSILON:
            self._update_execution_from_order(execution, order, applied_delta=False)
            self._save_executions()
            self._save_intents()
            return execution

        previous_notional = previous_applied * float(execution.filled_avg_price or 0.0)
        new_notional = cumulative_filled * cumulative_avg
        delta_avg_price = (new_notional - previous_notional) / fill_delta if fill_delta else cumulative_avg
        if delta_avg_price <= 0.0:
            delta_avg_price = cumulative_avg
        if delta_avg_price <= 0.0:
            raise ValueError("filled_avg_price required to apply fill")

        if execution.side == OrderSide.BUY.value:
            self._apply_buy_fill(execution, fill_delta, delta_avg_price, order)
        elif execution.side == OrderSide.SELL.value:
            self._apply_sell_fill(execution, fill_delta, delta_avg_price, order)
        else:
            raise ValueError(f"unsupported execution side: {execution.side}")

        execution.already_applied_filled_shares = cumulative_filled
        execution.filled_shares = cumulative_filled
        execution.filled_avg_price = cumulative_avg
        self._update_execution_from_order(execution, order, applied_delta=True)
        self._save_positions()
        self._save_executions()
        self._save_intents()
        return execution

    def reconcile(self, broker: LedgerBrokerPort, epsilon: float = EPSILON) -> dict:
        self._ensure_available()
        broker_by_ticker: Dict[str, float] = {}
        for holding in broker.get_holdings():
            ticker = normalize_ticker(holding.ticker)
            shares = normalize_shares(holding.shares)
            if shares > epsilon:
                broker_by_ticker[ticker] = normalize_shares(broker_by_ticker.get(ticker, 0.0) + shares)

        ledger_by_ticker: Dict[str, float] = {}
        for pos in self._positions.values():
            shares = normalize_shares(pos.open_shares)
            if shares > epsilon:
                ledger_by_ticker[pos.ticker] = normalize_shares(ledger_by_ticker.get(pos.ticker, 0.0) + shares)

        discrepancies = []
        ok_tickers = set()
        for ticker in sorted(set(broker_by_ticker) | set(ledger_by_ticker)):
            broker_shares = normalize_shares(broker_by_ticker.get(ticker, 0.0))
            ledger_shares = normalize_shares(ledger_by_ticker.get(ticker, 0.0))
            diff = normalize_shares(ledger_shares - broker_shares)
            if abs(diff) <= epsilon:
                ok_tickers.add(ticker)
                continue
            if ledger_shares <= epsilon and broker_shares > epsilon:
                kind = "broker-only"
            elif ledger_shares > epsilon and broker_shares <= epsilon:
                kind = "ledger-only"
            else:
                kind = "mismatch"
            discrepancies.append(
                {
                    "ticker": ticker,
                    "kind": kind,
                    "ledger_shares": ledger_shares,
                    "broker_shares": broker_shares,
                    "delta": diff,
                }
            )

        bad_tickers = {d["ticker"] for d in discrepancies}
        self._reconcile_blocked_tickers.difference_update(ok_tickers - bad_tickers)
        self._reconcile_blocked_tickers.update(bad_tickers)
        for pos in self._positions.values():
            if pos.ticker in bad_tickers:
                pos.reconcile_blocked = True
                pos.last_reconcile_error = next((json.dumps(d, sort_keys=True) for d in discrepancies if d["ticker"] == pos.ticker), "reconcile mismatch")
            elif pos.ticker in ok_tickers:
                pos.reconcile_blocked = False
                pos.last_reconcile_error = ""
        self._save_positions()
        return {
            "ok": not discrepancies,
            "discrepancies": discrepancies,
            "blocked_tickers": sorted(self._reconcile_blocked_tickers),
            "broker_shares_by_ticker": broker_by_ticker,
            "ledger_shares_by_ticker": ledger_by_ticker,
        }

    def _apply_buy_fill(self, execution: ExecutionRecord, fill_delta: float, price: float, order: Order) -> None:
        now = _now_iso()
        position = self._positions.get(execution.position_id) if execution.position_id else None
        is_add_buy = position is not None
        if position is None:
            position_id = execution.position_id or _new_id("pos")
            position = PositionRecord(
                position_id=position_id,
                entity_id=execution.entity_id,
                ticker=execution.ticker,
                rulebook_hash="",
                member_hash="",
                rulebook_snapshot={},
                direction="long",
                status="open",
                opened_shares=0.0,
                closed_shares=0.0,
                open_shares=0.0,
                avg_entry_price=0.0,
                realized_pnl=0.0,
                entry_date=str(getattr(order, "filled_at", "") or getattr(order, "submitted_at", "") or now),
                last_updated_at=now,
                highest_price=float(price),
                lowest_price=float(price),
            )
            self._positions[position_id] = position
            execution.position_id = position_id
            intent = self._intents.get(execution.intent_id)
            if intent is not None:
                intent.target_position_id = position_id
        elif position.entity_id != execution.entity_id or position.ticker != execution.ticker:
            raise ValueError("execution target position entity/ticker mismatch")

        old_open = normalize_shares(position.open_shares)
        new_open = normalize_shares(old_open + fill_delta)
        if new_open <= EPSILON:
            raise ValueError("buy fill produced non-positive open shares")
        position.avg_entry_price = ((old_open * position.avg_entry_price) + (fill_delta * price)) / new_open
        position.opened_shares = normalize_shares(position.opened_shares + fill_delta)
        position.open_shares = new_open
        position.status = "open"
        position.last_updated_at = now
        position.highest_price = max(float(position.highest_price or 0.0), float(price))
        low = float(position.lowest_price or 0.0)
        position.lowest_price = float(price) if low <= 0.0 else min(low, float(price))
        if is_add_buy:
            position.add_buy_count += 1

    def _apply_sell_fill(self, execution: ExecutionRecord, fill_delta: float, price: float, order: Order) -> None:
        position = self._positions.get(execution.position_id)
        if position is None:
            raise ValueError("sell fill requires existing target position")
        if position.entity_id != execution.entity_id or position.ticker != execution.ticker:
            raise ValueError("execution target position entity/ticker mismatch")
        old_open = normalize_shares(position.open_shares)
        if fill_delta - old_open > EPSILON:
            raise ValueError(f"sell fill exceeds open shares: delta={fill_delta} open={old_open}")
        now = _now_iso()
        actual_delta = min(fill_delta, old_open)
        position.closed_shares = normalize_shares(position.closed_shares + actual_delta)
        position.open_shares = normalize_shares(old_open - actual_delta)
        position.realized_pnl += actual_delta * (price - position.avg_entry_price)
        position.last_updated_at = now
        position.highest_price = max(float(position.highest_price or 0.0), float(price))
        low = float(position.lowest_price or 0.0)
        position.lowest_price = float(price) if low <= 0.0 else min(low, float(price))
        if position.open_shares <= EPSILON:
            position.open_shares = 0.0
            position.status = "closed"
        intent = self._intents.get(execution.intent_id)
        if intent is not None and position.status == "closed":
            intent.status = "filled"

    def _state_from_order(self, order: Order) -> str:
        if order.status == OrderStatus.FILLED:
            return "filled"
        if order.status == OrderStatus.PARTIAL:
            return "partial"
        if order.status == OrderStatus.PENDING:
            return "pending"
        if order.status == OrderStatus.REJECTED:
            return "rejected"
        if order.status == OrderStatus.FAILED:
            return "failed"
        if order.status == OrderStatus.CANCELLED:
            return "cancelled"
        return _order_status_value(order.status) or "unknown"

    def _update_execution_from_order(self, execution: ExecutionRecord, order: Order, *, applied_delta: bool) -> None:
        now = _now_iso()
        execution.broker_status = _order_status_value(order.status)
        execution.raw_status = str(getattr(order, "raw_status", "") or execution.raw_status)
        execution.replaced_by = str(getattr(order, "replaced_by", "") or execution.replaced_by)
        execution.last_polled_at = now
        execution.updated_at = now
        if not applied_delta:
            execution.filled_shares = normalize_shares(getattr(order, "filled_shares", execution.filled_shares))
            execution.filled_avg_price = float(getattr(order, "filled_avg_price", execution.filled_avg_price) or execution.filled_avg_price or 0.0)
        execution.state = self._state_from_order(order)
        intent = self._intents.get(execution.intent_id)
        if intent is not None:
            intent.status = execution.state
            if execution.state == "filled":
                intent.linked_execution_id = execution.execution_id

    def _ensure_available(self) -> None:
        if self._load_error:
            raise LedgerUnavailableError(f"central ledger state unavailable: {self._load_error}")

    def _load_all(self) -> None:
        try:
            self._positions, blocked = self._load_records(self.positions_path, PositionRecord, extra_blocked=True)
            self._reconcile_blocked_tickers = set(blocked)
            self._executions, _ = self._load_records(self.executions_path, ExecutionRecord)
            self._intents, _ = self._load_records(self.intents_path, IntentRecord)
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            self._positions = {}
            self._executions = {}
            self._intents = {}
            self._reconcile_blocked_tickers = set()

    def _load_records(self, path: Path, model_cls, *, extra_blocked: bool = False):
        if not path.exists():
            return {}, []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{path} root must be an object")
        rows = payload.get("records", payload)
        if not isinstance(rows, dict):
            raise ValueError(f"{path} records must be an object")
        records = {str(key): model_cls.from_dict(value) for key, value in rows.items()}
        blocked = payload.get("reconcile_blocked_tickers", []) if extra_blocked else []
        return records, [normalize_ticker(t) for t in blocked]

    def _save_positions(self) -> None:
        self._atomic_write(
            self.positions_path,
            {k: v.to_dict() for k, v in self._positions.items()},
            extra={"reconcile_blocked_tickers": sorted(self._reconcile_blocked_tickers)},
        )

    def _save_executions(self) -> None:
        self._atomic_write(self.executions_path, {k: v.to_dict() for k, v in self._executions.items()})

    def _save_intents(self) -> None:
        self._atomic_write(self.intents_path, {k: v.to_dict() for k, v in self._intents.items()})

    def _atomic_write(self, path: Path, records: dict, extra: Optional[dict] = None) -> None:
        self._ensure_available()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "updated_at": _now_iso(), "records": records}
        if extra:
            payload.update(extra)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
