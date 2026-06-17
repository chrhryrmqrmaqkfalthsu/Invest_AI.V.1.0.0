"""Dataclasses for the entity-level central position ledger.

These models are deliberately independent from engine.live.position_manager.
The existing live runner stores one position per ticker; central ledger stores
one position per entity/position id.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Type, TypeVar

SHARE_DECIMALS = 6

T = TypeVar("T")


def normalize_shares(value: Any) -> float:
    """Normalize share quantities to the same 6-decimal convention as live code."""
    try:
        return round(float(value or 0.0), SHARE_DECIMALS)
    except (TypeError, ValueError):
        return 0.0


def normalize_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _known_dataclass_payload(cls: Type[T], payload: Dict[str, Any]) -> Dict[str, Any]:
    known = set(getattr(cls, "__dataclass_fields__", {}))
    return {k: v for k, v in dict(payload or {}).items() if k in known}


@dataclass
class PositionRecord:
    position_id: str
    entity_id: str
    ticker: str
    rulebook_hash: str
    member_hash: str
    rulebook_snapshot: dict
    direction: str
    status: str
    opened_shares: float
    closed_shares: float
    open_shares: float
    avg_entry_price: float
    realized_pnl: float
    entry_date: str
    last_updated_at: str
    atr_at_entry: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    trailing_distance: float = 0.0
    trailing_stop: float = 0.0
    highest_price: float = 0.0
    lowest_price: float = 0.0
    exit_strategy: str = ""
    max_holding_days: int = 0
    entry_market_score: float = 50.0
    entry_vix_level: float = 18.0
    entry_sector_score: float = 50.0
    signal_score_at_entry: float = 0.0
    signal_threshold_at_entry: float = 0.0
    win_rate_at_entry: float = 0.0
    add_buy_count: int = 0
    reconcile_blocked: bool = False
    last_reconcile_error: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("opened_shares", "closed_shares", "open_shares"):
            data[key] = normalize_shares(data.get(key))
        data["ticker"] = normalize_ticker(data.get("ticker"))
        data["avg_entry_price"] = normalize_float(data.get("avg_entry_price"))
        data["realized_pnl"] = normalize_float(data.get("realized_pnl"))
        data["rulebook_snapshot"] = dict(data.get("rulebook_snapshot") or {})
        return data

    @classmethod
    def from_dict(cls, payload: dict) -> "PositionRecord":
        data = _known_dataclass_payload(cls, payload)
        data["ticker"] = normalize_ticker(data.get("ticker"))
        data["opened_shares"] = normalize_shares(data.get("opened_shares"))
        data["closed_shares"] = normalize_shares(data.get("closed_shares"))
        data["open_shares"] = normalize_shares(data.get("open_shares"))
        data["avg_entry_price"] = normalize_float(data.get("avg_entry_price"))
        data["realized_pnl"] = normalize_float(data.get("realized_pnl"))
        data.setdefault("rulebook_snapshot", {})
        return cls(**data)


@dataclass
class ExecutionRecord:
    execution_id: str
    intent_id: str
    position_id: str
    entity_id: str
    ticker: str
    side: str
    purpose: str
    order_id: str
    client_order_id: str
    requested_shares: float
    already_applied_filled_shares: float
    filled_shares: float
    filled_avg_price: float
    state: str
    broker_status: str
    raw_status: str
    replaced_by: str
    created_at: str
    submitted_at: str
    last_polled_at: str
    updated_at: str
    retry_count: int = 0
    last_error: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("requested_shares", "already_applied_filled_shares", "filled_shares"):
            data[key] = normalize_shares(data.get(key))
        data["ticker"] = normalize_ticker(data.get("ticker"))
        data["side"] = enum_value(data.get("side")).lower()
        data["filled_avg_price"] = normalize_float(data.get("filled_avg_price"))
        return data

    @classmethod
    def from_dict(cls, payload: dict) -> "ExecutionRecord":
        data = _known_dataclass_payload(cls, payload)
        data["ticker"] = normalize_ticker(data.get("ticker"))
        data["side"] = enum_value(data.get("side")).lower()
        data["requested_shares"] = normalize_shares(data.get("requested_shares"))
        data["already_applied_filled_shares"] = normalize_shares(data.get("already_applied_filled_shares"))
        data["filled_shares"] = normalize_shares(data.get("filled_shares"))
        data["filled_avg_price"] = normalize_float(data.get("filled_avg_price"))
        return cls(**data)


@dataclass
class IntentRecord:
    intent_id: str
    entity_id: str
    ticker: str
    side: str
    purpose: str
    target_position_id: str
    requested_shares: float
    reason: str
    created_at: str
    status: str
    linked_execution_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ticker"] = normalize_ticker(data.get("ticker"))
        data["side"] = enum_value(data.get("side")).lower()
        data["requested_shares"] = normalize_shares(data.get("requested_shares"))
        return data

    @classmethod
    def from_dict(cls, payload: dict) -> "IntentRecord":
        data = _known_dataclass_payload(cls, payload)
        data["ticker"] = normalize_ticker(data.get("ticker"))
        data["side"] = enum_value(data.get("side")).lower()
        data["requested_shares"] = normalize_shares(data.get("requested_shares"))
        return cls(**data)
