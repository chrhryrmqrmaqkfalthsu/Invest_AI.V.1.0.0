"""BN-1/BN-2 pending 주문 상태머신.

BN-2:
- 주문 제출 전 SUBMITTING intent를 atomic 저장한다.
- client_order_id로 submit 후 order_id 저장 전 크래시를 복구한다.
- BT-2: 복구/제출 결과가 FILLED여도 finalization 전에는 레코드를 삭제하지 않고 RECONCILING으로 유지한다.
- BT-3: 같은 order_id/client_order_id 재추적은 기존 레코드를 보존한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from engine.live.broker.base import Broker, Order, OrderSide, OrderStatus, OrderType

log = logging.getLogger("pending_order_manager")

PENDING_ORDERS_PATH = Path("data/_system/pending_orders.json")
DEFAULT_TERMINAL_LOCK_SECONDS = 300
TERMINAL_STATUSES = {OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED}

STATE_SUBMITTING = "SUBMITTING"
STATE_OPEN = "OPEN"
STATE_PARTIAL = "PARTIAL"
STATE_UNKNOWN_OPEN = "UNKNOWN_OPEN"
STATE_RECONCILING = "RECONCILING"
STATE_TERMINAL = "TERMINAL"
STATE_DONE = "DONE"
CLIENT_LOOKUP_FOUND = "FOUND"
CLIENT_LOOKUP_NOT_FOUND = "NOT_FOUND"
CLIENT_LOOKUP_UNKNOWN = "UNKNOWN"


@dataclass
class PendingOrderRecord:
    order_id: str
    ticker: str
    side: str
    purpose: str
    requested_shares: float
    internal_status: str
    state: str
    created_at: str
    updated_at: str
    raw_status: str = ""
    client_order_id: str = ""
    replaced_by: str = ""
    submitted_at: str = ""
    last_polled_at: str = ""
    filled_shares: float = 0.0
    filled_avg_price: float = 0.0
    exit_reason: str = ""
    approval_request_id: str = ""
    metadata: dict = field(default_factory=dict)
    retry_count: int = 0
    last_error: str = ""
    finalization_state: str = "pending"
    lock_until: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "PendingOrderRecord":
        known = set(cls.__dataclass_fields__)
        data = {k: v for k, v in dict(payload).items() if k in known}
        data.setdefault("metadata", {})
        return cls(**data)

    @property
    def is_terminal(self) -> bool:
        return self.internal_status in {OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value, OrderStatus.FAILED.value}


class PendingOrderManager:
    def __init__(self, broker: Broker, path: Optional[Path] = None, terminal_lock_seconds: int = DEFAULT_TERMINAL_LOCK_SECONDS):
        self.broker = broker
        self.path = Path(path or PENDING_ORDERS_PATH)
        self.terminal_lock_seconds = max(0, int(terminal_lock_seconds))
        self._records: Dict[str, PendingOrderRecord] = {}
        self._load_error = ""
        self._load()

    @staticmethod
    def _now() -> datetime:
        return datetime.now().astimezone()

    @classmethod
    def _now_iso(cls) -> str:
        return cls._now().isoformat()

    @staticmethod
    def _status_value(status) -> str:
        return status.value if isinstance(status, OrderStatus) else str(status or "").lower()

    @staticmethod
    def _side_value(side) -> str:
        return str(getattr(side, "value", side) or "").lower()

    @staticmethod
    def _parse_time(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return parsed
        except Exception:
            return None

    @staticmethod
    def make_client_order_id(*, ticker: str, side: str, purpose: str, seed: str) -> str:
        raw = f"{datetime.now().astimezone():%Y%m%d}|{ticker}|{side}|{purpose}|{seed}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", f"km2-{ticker}-{side}-{purpose}-{digest}")
        return safe[:48]

    @property
    def load_error(self) -> str:
        return self._load_error

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("orders", payload) if isinstance(payload, dict) else {}
            if not isinstance(rows, dict):
                raise ValueError("pending_orders root/orders must be an object")
            self._records = {str(order_id): PendingOrderRecord.from_dict(record) for order_id, record in rows.items()}
            log.info("pending_orders.json 로드: %s건", len(self._records))
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            self._records = {}
            log.error("pending_orders.json 로드 실패 → 모든 신규 주문 fail-closed: %s", self._load_error)

    def _save(self) -> None:
        if self._load_error:
            raise RuntimeError(f"pending order state load error: {self._load_error}")
        if not self._records:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"schema_version": 2, "updated_at": self._now_iso(), "orders": {k: v.to_dict() for k, v in self._records.items()}}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _purge_expired_terminal_locks(self) -> bool:
        now = self._now()
        removed = []
        for order_id, record in self._records.items():
            if record.state == STATE_TERMINAL and record.finalization_state == "done":
                until = self._parse_time(record.lock_until)
                if until is not None and now >= until:
                    removed.append(order_id)
        for order_id in removed:
            self._records.pop(order_id, None)
        if removed:
            self._save()
        return bool(removed)

    def all(self) -> List[PendingOrderRecord]:
        return list(self._records.values())

    def get_record(self, order_id: str) -> Optional[PendingOrderRecord]:
        return self._records.get(str(order_id))

    def get_record_by_client_order_id(self, client_order_id: str) -> Optional[PendingOrderRecord]:
        cid = str(client_order_id or "").strip()
        return next((r for r in self._records.values() if r.client_order_id == cid), None)

    def is_ticker_locked(self, ticker: str) -> bool:
        if self._load_error:
            return True
        self._purge_expired_terminal_locks()
        ticker_u = str(ticker).strip().upper()
        for record in self._records.values():
            if record.ticker.upper() != ticker_u or record.state == STATE_DONE:
                continue
            if record.state == STATE_TERMINAL and record.finalization_state == "done":
                until = self._parse_time(record.lock_until)
                if until is not None and self._now() >= until:
                    continue
            return True
        return False

    def has_pending_exit(self, ticker: str) -> bool:
        t = str(ticker).strip().upper()
        return any(r.ticker.upper() == t and r.side == "sell" and r.state != STATE_DONE for r in self._records.values())

    def has_pending_buy(self, ticker: str) -> bool:
        t = str(ticker).strip().upper()
        return any(r.ticker.upper() == t and r.side == "buy" and r.state != STATE_DONE for r in self._records.values())

    def create_submitting_intent(self, *, client_order_id: str, ticker: str, side: str, purpose: str,
                                 requested_shares: float, metadata: Optional[dict] = None,
                                 exit_reason: str = "", approval_request_id: str = "") -> PendingOrderRecord:
        if self._load_error:
            raise RuntimeError(f"pending order state unavailable: {self._load_error}")
        cid = str(client_order_id or "").strip()
        if not cid:
            raise ValueError("client_order_id required")
        existing = self.get_record_by_client_order_id(cid)
        if existing is not None:
            return existing
        now = self._now_iso()
        key = f"SUBMITTING-{cid}"
        record = PendingOrderRecord(
            order_id="", ticker=str(ticker).strip().upper(), side=self._side_value(side), purpose=str(purpose or "entry"),
            requested_shares=float(requested_shares or 0.0), internal_status=OrderStatus.PENDING.value,
            state=STATE_SUBMITTING, created_at=now, updated_at=now, client_order_id=cid, submitted_at="",
            exit_reason=str(exit_reason or ""), approval_request_id=str(approval_request_id or ""), metadata=dict(metadata or {}),
        )
        self._records[key] = record
        self._save()
        log.info("[SUBMITTING-INTENT] %s %s %s cid=%s", record.ticker, record.side, record.purpose, cid)
        return record

    def mark_submitted(self, client_order_id: str, order: Order, *, purpose: str = "", metadata: Optional[dict] = None,
                       exit_reason: str = "", approval_request_id: str = "") -> Optional[PendingOrderRecord]:
        cid = str(client_order_id or getattr(order, "client_order_id", "") or "").strip()
        existing = self.get_record_by_client_order_id(cid) if cid else None
        base_metadata = dict(getattr(existing, "metadata", {}) or {})
        base_metadata.update(metadata or {})
        purpose_value = purpose or (existing.purpose if existing else "entry")
        exit_reason_value = exit_reason or (existing.exit_reason if existing else "")
        approval_id_value = approval_request_id or (existing.approval_request_id if existing else "")

        for key, record in list(self._records.items()):
            if record is existing:
                self._records.pop(key, None)
                break

        if order.status == OrderStatus.FILLED:
            now = self._now_iso()
            order_id = str(order.order_id or f"LOCAL-FILLED-{uuid.uuid4().hex}")
            record = PendingOrderRecord(
                order_id=order_id, ticker=str(order.ticker).strip().upper(), side=self._side_value(order.side),
                purpose=str(purpose_value or "entry"), requested_shares=float(order.shares or 0.0),
                internal_status=OrderStatus.FILLED.value, state=STATE_RECONCILING,
                created_at=existing.created_at if existing is not None else now, updated_at=now,
                raw_status=str(getattr(order, "raw_status", "") or ""), client_order_id=cid or str(getattr(order, "client_order_id", "") or ""),
                replaced_by=str(getattr(order, "replaced_by", "") or ""), submitted_at=str(order.submitted_at or getattr(existing, "submitted_at", "") or now),
                filled_shares=float(order.filled_shares or 0.0), filled_avg_price=float(order.filled_avg_price or 0.0),
                exit_reason=str(exit_reason_value or ""), approval_request_id=str(approval_id_value or ""),
                metadata=base_metadata, finalization_state="pending",
            )
            self._records[order_id] = record
            self._save()
            return record

        return self.track_order(order, purpose=purpose_value, metadata=base_metadata,
                                exit_reason=exit_reason_value, approval_request_id=approval_id_value)

    def resolve_submit_exception(self, client_order_id: str) -> Optional[Order]:
        cid = str(client_order_id or "").strip()
        if not cid:
            return None
        result_getter = getattr(self.broker, "get_order_by_client_order_id_result", None)
        if result_getter is not None:
            try:
                status, order = result_getter(cid)
            except Exception as exc:
                status, order = CLIENT_LOOKUP_UNKNOWN, None
                err = f"{type(exc).__name__}: {exc}"
            else:
                err = f"client_order_id lookup status={status}"
            if status == CLIENT_LOOKUP_FOUND and order is not None:
                self.mark_submitted(cid, order)
                return order
            rec = self.get_record_by_client_order_id(cid)
            if rec and status == CLIENT_LOOKUP_NOT_FOUND:
                for key, value in list(self._records.items()):
                    if value is rec:
                        self._records.pop(key, None)
                        break
                self._save()
                return None
            if rec:
                rec.state = STATE_UNKNOWN_OPEN
                rec.last_error = err
                rec.retry_count += 1
                rec.updated_at = self._now_iso()
                self._save()
            return None

        getter = getattr(self.broker, "get_order_by_client_order_id", None)
        if getter is None:
            rec = self.get_record_by_client_order_id(cid)
            if rec:
                rec.state = STATE_UNKNOWN_OPEN
                rec.last_error = "broker does not support client_order_id recovery"
                rec.retry_count += 1
                rec.updated_at = self._now_iso()
                self._save()
            return None
        try:
            order = getter(cid)
        except Exception as exc:
            order = None
            err = f"{type(exc).__name__}: {exc}"
        else:
            err = "client_order_id lookup unknown/none"
        if order is not None:
            self.mark_submitted(cid, order)
            return order
        rec = self.get_record_by_client_order_id(cid)
        if rec:
            rec.state = STATE_UNKNOWN_OPEN
            rec.last_error = err
            rec.retry_count += 1
            rec.updated_at = self._now_iso()
            self._save()
        return None

    def track_order(self, order: Order, *, purpose: str, metadata: Optional[dict] = None,
                    exit_reason: str = "", approval_request_id: str = "") -> Optional[PendingOrderRecord]:
        if order.status == OrderStatus.FILLED:
            return None
        if self._load_error:
            raise RuntimeError(f"pending order state unavailable: {self._load_error}")
        order_id = str(order.order_id or f"LOCAL-{uuid.uuid4().hex}")
        order_client_id = str(getattr(order, "client_order_id", "") or "")
        existing = self._records.get(order_id)
        if existing is not None and (not order_client_id or existing.client_order_id == order_client_id):
            return existing

        now = self._now_iso()
        status = self._status_value(order.status)
        state = STATE_PARTIAL if order.status == OrderStatus.PARTIAL else (STATE_TERMINAL if order.status in TERMINAL_STATUSES and float(order.filled_shares or 0.0) <= 0 else (STATE_RECONCILING if order.status in TERMINAL_STATUSES else STATE_OPEN))
        record = PendingOrderRecord(
            order_id=order_id, ticker=str(order.ticker).strip().upper(), side=self._side_value(order.side),
            purpose=str(purpose or "entry"), requested_shares=float(order.shares or 0.0), internal_status=status,
            state=state, created_at=now, updated_at=now, raw_status=str(getattr(order, "raw_status", "") or ""),
            client_order_id=order_client_id, replaced_by=str(getattr(order, "replaced_by", "") or ""),
            submitted_at=str(order.submitted_at or now), filled_shares=float(order.filled_shares or 0.0),
            filled_avg_price=float(order.filled_avg_price or 0.0), exit_reason=str(exit_reason or ""),
            approval_request_id=str(approval_request_id or ""), metadata=dict(metadata or {}),
        )
        self._records[order_id] = record
        self._save()
        log.info("[PENDING-TRACK] %s %s %s id=%s status=%s raw=%s", record.ticker, record.side, record.purpose, order_id, status, record.raw_status)
        return record

    def track_reconciliation(self, order: Order, *, purpose: str, metadata: Optional[dict] = None,
                             approval_request_id: str = "", error: str = "") -> PendingOrderRecord:
        if self._load_error:
            raise RuntimeError(f"pending order state unavailable: {self._load_error}")
        now = self._now_iso()
        order_id = str(order.order_id or f"LOCAL-RECON-{uuid.uuid4().hex}")
        existing = self._records.get(order_id)
        merged = dict(getattr(existing, "metadata", {}) or {})
        merged.update(dict(metadata or {}))
        merged["local_reconciliation"] = True
        record = PendingOrderRecord(
            order_id=order_id, ticker=str(order.ticker).strip().upper(), side=self._side_value(order.side),
            purpose=str(purpose or "entry"), requested_shares=float(order.shares or order.filled_shares or 0.0),
            internal_status=self._status_value(order.status), state=STATE_RECONCILING,
            created_at=existing.created_at if existing else now, updated_at=now,
            raw_status=str(getattr(order, "raw_status", "") or getattr(existing, "raw_status", "")),
            client_order_id=str(getattr(order, "client_order_id", "") or getattr(existing, "client_order_id", "")),
            replaced_by=str(getattr(order, "replaced_by", "") or getattr(existing, "replaced_by", "")),
            submitted_at=str(order.submitted_at or getattr(existing, "submitted_at", "") or now),
            last_polled_at=getattr(existing, "last_polled_at", ""), filled_shares=float(order.filled_shares or order.shares or 0.0),
            filled_avg_price=float(order.filled_avg_price or order.price or 0.0), approval_request_id=str(approval_request_id or getattr(existing, "approval_request_id", "")),
            metadata=merged, retry_count=int(getattr(existing, "retry_count", 0) or 0),
            last_error=str(error or getattr(existing, "last_error", "") or "reconciliation required")[:500], finalization_state="pending",
        )
        self._records[order_id] = record
        self._save()
        log.error("[RECONCILING-TRACK] %s %s %s id=%s error=%s", record.ticker, record.side, record.purpose, order_id, record.last_error)
        return record

    def _update_record(self, record: PendingOrderRecord, order: Order) -> None:
        record.internal_status = self._status_value(order.status)
        record.raw_status = str(getattr(order, "raw_status", "") or record.raw_status)
        record.client_order_id = str(getattr(order, "client_order_id", "") or record.client_order_id)
        record.replaced_by = str(getattr(order, "replaced_by", "") or record.replaced_by)
        record.filled_shares = float(order.filled_shares or 0.0)
        record.filled_avg_price = float(order.filled_avg_price or 0.0)
        record.last_polled_at = self._now_iso()
        record.updated_at = record.last_polled_at
        record.last_error = ""

    def _order_from_record(self, record: PendingOrderRecord) -> Order:
        try:
            status = OrderStatus(record.internal_status)
        except Exception:
            status = OrderStatus.FILLED if record.filled_shares > 0 else OrderStatus.PENDING
        return Order(record.order_id, record.ticker, OrderSide.SELL if record.side == "sell" else OrderSide.BUY,
                     OrderType.MARKET, record.requested_shares, 0.0, status, record.filled_shares,
                     record.filled_avg_price, submitted_at=record.submitted_at, raw_status=record.raw_status,
                     client_order_id=record.client_order_id, replaced_by=record.replaced_by)

    def _migrate_replacement(self, old_id: str, record: PendingOrderRecord, new_id: str) -> None:
        self._records.pop(old_id, None)
        record.order_id = new_id
        record.state = STATE_OPEN
        record.internal_status = OrderStatus.PENDING.value
        record.finalization_state = "pending"
        record.last_error = f"replaced order followed from {old_id}"
        record.updated_at = self._now_iso()
        self._records[new_id] = record
        log.warning("[PENDING-REPLACED] %s %s → %s", record.ticker, old_id, new_id)

    def _recover_submitting(self, key: str, record: PendingOrderRecord) -> Optional[Order]:
        if not record.client_order_id:
            record.state = STATE_UNKNOWN_OPEN
            record.last_error = "SUBMITTING without client_order_id"
            return None
        order = self.resolve_submit_exception(record.client_order_id)
        if order is None:
            return None
        return order

    def poll_all(self) -> List[Tuple[PendingOrderRecord, Order]]:
        if self._load_error:
            log.error("pending poll 차단: %s", self._load_error)
            return []
        self._purge_expired_terminal_locks()
        events: List[Tuple[PendingOrderRecord, Order]] = []
        changed = False
        for order_id, record in list(self._records.items()):
            if record.state == STATE_DONE or (record.state == STATE_TERMINAL and record.finalization_state == "done"):
                continue
            if record.state == STATE_SUBMITTING or (not record.order_id and record.client_order_id):
                recovered = self._recover_submitting(order_id, record)
                changed = True
                if recovered is None:
                    continue
                record = self.get_record(recovered.order_id) or record
                order_id = recovered.order_id
                if recovered.status == OrderStatus.FILLED:
                    record.state = STATE_RECONCILING
                    record.finalization_state = "pending"
                    events.append((record, recovered))
                    continue
            if record.state == STATE_RECONCILING and bool(record.metadata.get("local_reconciliation")):
                record.last_polled_at = self._now_iso(); record.updated_at = record.last_polled_at; changed = True
                events.append((record, self._order_from_record(record))); continue
            if order_id.startswith("LOCAL-"):
                record.state = STATE_UNKNOWN_OPEN; record.retry_count += 1; record.last_error = "synthetic order id cannot be polled"; record.updated_at = self._now_iso(); changed = True; continue
            try:
                order = self.broker.get_order(order_id)
            except Exception as exc:
                order = None; record.last_error = f"{type(exc).__name__}: {exc}"
            if order is None:
                record.state = STATE_UNKNOWN_OPEN; record.retry_count += 1; record.last_polled_at = self._now_iso(); record.updated_at = record.last_polled_at
                if not record.last_error:
                    record.last_error = "broker.get_order returned None"
                changed = True; continue
            self._update_record(record, order); changed = True
            if record.raw_status == "replaced" and record.replaced_by:
                self._migrate_replacement(order_id, record, record.replaced_by); continue
            if order.status == OrderStatus.PARTIAL:
                record.state = STATE_PARTIAL; continue
            if order.status == OrderStatus.PENDING:
                record.state = STATE_OPEN; continue
            if order.status == OrderStatus.FILLED:
                record.state = STATE_RECONCILING; record.finalization_state = "pending"; events.append((record, order)); continue
            if order.status in TERMINAL_STATUSES:
                record.state = STATE_RECONCILING if float(order.filled_shares or 0.0) > 0 else STATE_TERMINAL
                record.finalization_state = "pending"; events.append((record, order)); continue
            record.state = STATE_UNKNOWN_OPEN; record.last_error = f"unhandled internal status: {order.status}"
        if changed:
            self._save()
        return events

    def mark_finalized(self, order_id: str) -> None:
        record = self._records.get(str(order_id))
        if record is None:
            return
        if record.is_terminal or record.state == STATE_TERMINAL:
            record.state = STATE_TERMINAL; record.finalization_state = "done"
            record.lock_until = (self._now() + timedelta(seconds=self.terminal_lock_seconds)).isoformat(); record.updated_at = self._now_iso()
        else:
            self._records.pop(str(order_id), None)
        self._save()

    def mark_reconcile_error(self, order_id: str, error: str) -> None:
        record = self._records.get(str(order_id))
        if record is None:
            return
        record.state = STATE_RECONCILING; record.finalization_state = "pending"; record.retry_count += 1
        record.last_error = str(error or "reconcile failed")[:500]; record.updated_at = self._now_iso(); self._save()

    def finalize_immediate_terminal(self, order_id: str) -> None:
        self.mark_finalized(order_id)
