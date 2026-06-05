"""BN-1 pending 주문 영속 상태머신.

책임:
- 즉시 FILLED가 아닌 주문을 data/_system/pending_orders.json에 atomic 저장한다.
- ticker 단위 주문 잠금으로 다음 tick/같은 tick 중복 주문을 막는다.
- Broker.get_order(order_id)를 폴링하고 PARTIAL/UNKNOWN_OPEN/RECONCILING 상태를 보존한다.
- 실제 포지션/PnL 반영은 Runner/PositionManager의 단일 최종화 경로에 위임한다.

BN-2 범위인 deterministic client_order_id 기반 제출 중 크래시 복구는 하지 않는다.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from engine.live.broker.base import Broker, Order, OrderStatus

log = logging.getLogger("pending_order_manager")

PENDING_ORDERS_PATH = Path("data/_system/pending_orders.json")
DEFAULT_TERMINAL_LOCK_SECONDS = 300
TERMINAL_STATUSES = {OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.FAILED}

STATE_OPEN = "OPEN"
STATE_PARTIAL = "PARTIAL"
STATE_UNKNOWN_OPEN = "UNKNOWN_OPEN"
STATE_RECONCILING = "RECONCILING"
STATE_TERMINAL = "TERMINAL"
STATE_DONE = "DONE"


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
        return self.internal_status in {
            OrderStatus.CANCELLED.value,
            OrderStatus.REJECTED.value,
            OrderStatus.FAILED.value,
        }


class PendingOrderManager:
    """영속 pending 주문 저장소와 폴러.

    poll_all()은 최종화가 필요한 ``(record, order)`` 쌍만 반환한다. 호출자는
    포지션/PnL 반영 성공 뒤 mark_finalized(), 실패 시 mark_reconcile_error()를
    호출해야 한다.
    """

    def __init__(
        self,
        broker: Broker,
        path: Optional[Path] = None,
        terminal_lock_seconds: int = DEFAULT_TERMINAL_LOCK_SECONDS,
    ):
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
            self._records = {
                str(order_id): PendingOrderRecord.from_dict(record)
                for order_id, record in rows.items()
            }
            log.info("pending_orders.json 로드: %s건", len(self._records))
        except Exception as exc:
            # 손상된 상태를 빈 상태로 덮어써 중복 주문을 허용하지 않는다.
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
        payload = {
            "schema_version": 1,
            "updated_at": self._now_iso(),
            "orders": {order_id: record.to_dict() for order_id, record in self._records.items()},
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _purge_expired_terminal_locks(self) -> bool:
        now = self._now()
        removed = []
        for order_id, record in self._records.items():
            if record.state != STATE_TERMINAL or record.finalization_state != "done":
                continue
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
        ticker_u = str(ticker).strip().upper()
        return any(
            record.ticker.upper() == ticker_u
            and record.side == "sell"
            and record.state != STATE_DONE
            for record in self._records.values()
        )

    def track_order(
        self,
        order: Order,
        *,
        purpose: str,
        metadata: Optional[dict] = None,
        exit_reason: str = "",
        approval_request_id: str = "",
    ) -> Optional[PendingOrderRecord]:
        """즉시 FILLED가 아닌 주문만 영속 추적한다.

        PaperBroker 즉시 FILLED 경로는 이 메서드에서 파일을 만들지 않고 그대로
        반환된다.
        """
        if order.status == OrderStatus.FILLED:
            return None
        if self._load_error:
            raise RuntimeError(f"pending order state unavailable: {self._load_error}")

        now = self._now_iso()
        order_id = str(order.order_id or f"LOCAL-{uuid.uuid4().hex}")
        status = self._status_value(order.status)
        if order.status == OrderStatus.PARTIAL:
            state = STATE_PARTIAL
        elif order.status in TERMINAL_STATUSES:
            state = STATE_TERMINAL if float(order.filled_shares or 0.0) <= 0 else STATE_RECONCILING
        else:
            state = STATE_OPEN

        record = PendingOrderRecord(
            order_id=order_id,
            ticker=str(order.ticker).strip().upper(),
            side=self._side_value(order.side),
            purpose=str(purpose or "entry"),
            requested_shares=float(order.shares or 0.0),
            internal_status=status,
            state=state,
            created_at=now,
            updated_at=now,
            raw_status=str(getattr(order, "raw_status", "") or ""),
            client_order_id=str(getattr(order, "client_order_id", "") or ""),
            replaced_by=str(getattr(order, "replaced_by", "") or ""),
            submitted_at=str(order.submitted_at or now),
            filled_shares=float(order.filled_shares or 0.0),
            filled_avg_price=float(order.filled_avg_price or 0.0),
            exit_reason=str(exit_reason or ""),
            approval_request_id=str(approval_request_id or ""),
            metadata=dict(metadata or {}),
        )
        self._records[order_id] = record
        self._save()
        log.info(
            "[PENDING-TRACK] %s %s %s id=%s status=%s raw=%s",
            record.ticker, record.side, record.purpose, order_id, status, record.raw_status,
        )
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

    def poll_all(self) -> List[Tuple[PendingOrderRecord, Order]]:
        """모든 미완료 주문을 재조회하고 최종화가 필요한 이벤트만 반환한다."""
        if self._load_error:
            log.error("pending poll 차단: %s", self._load_error)
            return []
        self._purge_expired_terminal_locks()
        events: List[Tuple[PendingOrderRecord, Order]] = []
        changed = False

        for order_id, record in list(self._records.items()):
            if record.state == STATE_DONE:
                continue
            if record.state == STATE_TERMINAL and record.finalization_state == "done":
                continue
            # LOCAL synthetic ID는 실제 브로커 조회가 불가능하다. caller가 즉시
            # terminal 최종화를 하지 못한 경우 fail-closed 잠금을 유지한다.
            if order_id.startswith("LOCAL-"):
                record.state = STATE_UNKNOWN_OPEN
                record.retry_count += 1
                record.last_error = "synthetic order id cannot be polled"
                record.updated_at = self._now_iso()
                changed = True
                continue

            try:
                order = self.broker.get_order(order_id)
            except Exception as exc:
                order = None
                record.last_error = f"{type(exc).__name__}: {exc}"

            if order is None:
                record.state = STATE_UNKNOWN_OPEN
                record.retry_count += 1
                record.last_polled_at = self._now_iso()
                record.updated_at = record.last_polled_at
                if not record.last_error:
                    record.last_error = "broker.get_order returned None"
                changed = True
                continue

            self._update_record(record, order)
            changed = True

            if record.raw_status == "replaced" and record.replaced_by:
                self._migrate_replacement(order_id, record, record.replaced_by)
                continue

            if order.status == OrderStatus.PARTIAL:
                record.state = STATE_PARTIAL
                continue
            if order.status == OrderStatus.PENDING:
                record.state = STATE_OPEN
                continue
            if order.status == OrderStatus.FILLED:
                record.state = STATE_RECONCILING
                record.finalization_state = "pending"
                events.append((record, order))
                continue
            if order.status in TERMINAL_STATUSES:
                record.state = STATE_RECONCILING if float(order.filled_shares or 0.0) > 0 else STATE_TERMINAL
                record.finalization_state = "pending"
                events.append((record, order))
                continue

            record.state = STATE_UNKNOWN_OPEN
            record.last_error = f"unhandled internal status: {order.status}"

        if changed:
            self._save()
        return events

    def mark_finalized(self, order_id: str) -> None:
        record = self._records.get(str(order_id))
        if record is None:
            return
        if record.is_terminal or record.state == STATE_TERMINAL:
            record.state = STATE_TERMINAL
            record.finalization_state = "done"
            record.lock_until = (self._now() + timedelta(seconds=self.terminal_lock_seconds)).isoformat()
            record.updated_at = self._now_iso()
        else:
            self._records.pop(str(order_id), None)
        self._save()

    def mark_reconcile_error(self, order_id: str, error: str) -> None:
        record = self._records.get(str(order_id))
        if record is None:
            return
        record.state = STATE_RECONCILING
        record.finalization_state = "pending"
        record.retry_count += 1
        record.last_error = str(error or "reconcile failed")[:500]
        record.updated_at = self._now_iso()
        self._save()

    def finalize_immediate_terminal(self, order_id: str) -> None:
        """즉시 terminal 0체결처럼 로컬 반영이 필요 없는 주문을 잠금 상태로 완료."""
        self.mark_finalized(order_id)
