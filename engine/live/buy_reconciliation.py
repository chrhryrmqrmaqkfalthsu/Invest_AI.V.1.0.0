"""BS-1b BUY 체결 후 PositionManager reconciliation.

BUY가 브로커에서 체결된 뒤 PositionEntry 등록/추가매수 반영이 끝날 때까지
주문을 완료로 보지 않는다. 실패하면 pending_orders.json의 RECONCILING 상태로
영속화하고 ticker 잠금을 유지한다. 단, 동일 BUY reconciliation 실패가 반복되고
브로커 실보유가 시간 간격을 둔 연속 2회 확인에도 없을 때만 pending lock을 정리한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from engine.live.broker.base import Holding, Order, OrderSide, OrderStatus, OrderType

log = logging.getLogger("buy_reconciliation")
SHARE_EPS = 1e-6
DEFAULT_MAX_RECONCILE_RETRIES = 3
DEFAULT_EMPTY_HOLDING_CONFIRM_SECONDS = 10
ZERO_HOLDING_COUNT_KEY = "zero_holding_seen_count"
ZERO_HOLDING_FIRST_SEEN_KEY = "zero_holding_first_seen_at"


@dataclass(frozen=True)
class BuyPreflight:
    atr: float
    rulebook: object
    entry_market_context: Optional[dict]


class BuyReconciliationService:
    def __init__(
        self,
        *,
        broker,
        rulebook_provider,
        position_manager,
        pending_manager=None,
        notifier=None,
        max_reconcile_retries: int = DEFAULT_MAX_RECONCILE_RETRIES,
        empty_holding_confirm_seconds: int = DEFAULT_EMPTY_HOLDING_CONFIRM_SECONDS,
    ):
        self.broker = broker
        self.rulebook_provider = rulebook_provider
        self.position_manager = position_manager
        self.pending_manager = pending_manager
        self.notifier = notifier
        self.max_reconcile_retries = max(1, int(max_reconcile_retries or DEFAULT_MAX_RECONCILE_RETRIES))
        self.empty_holding_confirm_seconds = max(0, int(empty_holding_confirm_seconds or 0))

    def preflight(self, ticker: str) -> BuyPreflight:
        provider = self.rulebook_provider
        if not hasattr(provider, "get_last_atr") or not hasattr(provider, "get_rulebook"):
            raise RuntimeError(f"{ticker} BUY preflight 실패: ATR/rulebook provider 없음")
        atr = provider.get_last_atr(ticker)
        rulebook = provider.get_rulebook(ticker)
        if atr is None or float(atr) <= 0:
            raise RuntimeError(f"{ticker} BUY preflight 실패: 유효한 ATR 없음")
        if rulebook is None:
            raise RuntimeError(f"{ticker} BUY preflight 실패: rulebook 없음")
        context = provider.get_last_market_context(ticker) if hasattr(provider, "get_last_market_context") else None
        return BuyPreflight(float(atr), rulebook, context)

    def reconcile(self, order: Order, *, purpose: str, preflight: Optional[BuyPreflight] = None):
        ticker = str(order.ticker)
        filled_shares = float(order.filled_shares or 0.0)
        filled_price = float(order.filled_avg_price or 0.0)
        if filled_shares <= SHARE_EPS:
            raise RuntimeError(f"{ticker} BUY reconciliation 실패: filled_shares 없음")
        if filled_price <= 0:
            raise RuntimeError(f"{ticker} BUY reconciliation 실패: filled_avg_price 없음")

        metadata = preflight or self.preflight(ticker)
        purpose_lower = str(purpose or "entry").lower()
        if purpose_lower == "add_buy":
            updated = self.position_manager.add_to_position(
                ticker,
                filled_price,
                filled_shares,
                metadata.rulebook,
                metadata.atr,
            )
            if updated is None:
                raise RuntimeError(f"{ticker} 추가매수 체결 후 PositionEntry 갱신 실패")
            return updated

        if hasattr(self.position_manager, "get"):
            existing = self.position_manager.get(ticker)
            if existing is not None:
                return existing

        created = self.position_manager.register_entry(
            ticker,
            filled_price,
            filled_shares,
            metadata.rulebook,
            metadata.atr,
            entry_market_context=metadata.entry_market_context,
        )
        if hasattr(self.position_manager, "get") and self.position_manager.get(ticker) is None:
            raise RuntimeError(f"{ticker} BUY 체결 후 PositionEntry 검증 실패")
        return created

    def track_failure(
        self,
        order: Order,
        *,
        purpose: str,
        error: Exception | str,
        approval_request_id: str = "",
        metadata: Optional[dict] = None,
    ):
        message = str(error or "BUY reconciliation failed")
        record = None
        if self.pending_manager is not None:
            record = self.pending_manager.track_reconciliation(
                order,
                purpose=purpose,
                approval_request_id=approval_request_id,
                metadata=metadata,
                error=message,
            )
            if self._should_drop_reconciling_record(record):
                dropped = self._drop_reconciling_record(record, message)
                if dropped:
                    return record
        critical = (
            f"[CRITICAL][ORPHAN-BUY] {order.ticker} BUY 체결 후 포지션 등록 미완료 — "
            f"신규 주문 잠금 및 재조정 유지: {message}"
        )
        log.error(critical)
        if self.notifier is not None:
            try:
                self.notifier.send_error(critical)
            except Exception as exc:
                log.warning("고아 포지션 긴급 알림 실패: %s", exc)
        return record

    def _should_drop_reconciling_record(self, record) -> bool:
        if record is None:
            return False
        retry_count = int(getattr(record, "retry_count", 0) or 0)
        # Runner.mark_reconcile_error increments retry_count after this call. Drop
        # on the Nth attempt, not the N+1th.
        return retry_count >= max(self.max_reconcile_retries - 1, 0)

    def _drop_reconciling_record(self, record, message: str) -> bool:
        ticker = str(getattr(record, "ticker", "") or "").strip().upper()
        holding_shares = self._broker_holding_shares(ticker)
        if holding_shares is None:
            warning = (
                f"[ORPHAN-BUY-KEEP] {ticker or '?'} retry limit reached but holdings lookup failed; "
                f"pending lock 유지: {message}"
            )
            log.error(warning)
            self._notify_error(warning)
            return False
        if holding_shares > SHARE_EPS:
            self._clear_zero_holding_probe(record)
            warning = (
                f"[ORPHAN-BUY-KEEP] {ticker} retry limit reached but broker holding exists "
                f"shares={holding_shares:g}; pending lock 유지: {message}"
            )
            log.error(warning)
            self._notify_error(warning)
            return False
        if not self._confirm_repeated_zero_holding(record, ticker, message):
            return False

        warning = (
            f"[ORPHAN-BUY-DROP] {ticker or getattr(record, 'ticker', '?')} BUY reconciliation retry limit "
            f"reached ({getattr(record, 'retry_count', 0)}+1/{self.max_reconcile_retries}); "
            f"broker holding 없음 2회 확인 → pending lock 정리: {message}"
        )
        log.error(warning)
        self._notify_error(warning)
        try:
            self.pending_manager.mark_finalized(record.order_id)
            return True
        except Exception as exc:
            log.error("%s pending 정리 실패: %s", getattr(record, "ticker", "?"), exc)
            return False

    def _broker_holding_shares(self, ticker: str) -> Optional[float]:
        if not ticker:
            return 0.0
        try:
            holdings = self.broker.get_holdings()
        except Exception as exc:
            log.error("%s holdings 조회 실패; pending lock 유지: %s", ticker, exc)
            return None
        for holding in holdings or []:
            if str(getattr(holding, "ticker", "") or "").strip().upper() != ticker:
                continue
            try:
                return float(getattr(holding, "shares", 0.0) or 0.0)
            except Exception:
                return None
        return 0.0

    def _confirm_repeated_zero_holding(self, record, ticker: str, message: str) -> bool:
        metadata = dict(getattr(record, "metadata", {}) or {})
        now = datetime.now().astimezone()
        first_seen_raw = str(metadata.get(ZERO_HOLDING_FIRST_SEEN_KEY) or "")
        count = int(metadata.get(ZERO_HOLDING_COUNT_KEY) or 0)
        first_seen = _parse_time(first_seen_raw)
        if count <= 0 or first_seen is None:
            metadata[ZERO_HOLDING_COUNT_KEY] = 1
            metadata[ZERO_HOLDING_FIRST_SEEN_KEY] = now.isoformat()
            record.metadata = metadata
            record.updated_at = _manager_now_iso(self.pending_manager)
            self._save_pending_state()
            warning = (
                f"[ORPHAN-BUY-KEEP] {ticker or '?'} retry limit reached but broker holding 0 is first observation; "
                f"pending lock 유지: {message}"
            )
            log.error(warning)
            self._notify_error(warning)
            return False
        elapsed = (now - first_seen).total_seconds()
        if elapsed < self.empty_holding_confirm_seconds:
            warning = (
                f"[ORPHAN-BUY-KEEP] {ticker or '?'} broker holding 0 repeated too soon "
                f"elapsed={elapsed:.1f}s < {self.empty_holding_confirm_seconds}s; pending lock 유지: {message}"
            )
            log.error(warning)
            self._notify_error(warning)
            return False
        metadata[ZERO_HOLDING_COUNT_KEY] = count + 1
        record.metadata = metadata
        record.updated_at = _manager_now_iso(self.pending_manager)
        self._save_pending_state()
        return True

    def _clear_zero_holding_probe(self, record) -> None:
        metadata = dict(getattr(record, "metadata", {}) or {})
        changed = False
        for key in (ZERO_HOLDING_COUNT_KEY, ZERO_HOLDING_FIRST_SEEN_KEY):
            if key in metadata:
                metadata.pop(key, None)
                changed = True
        if changed:
            record.metadata = metadata
            record.updated_at = _manager_now_iso(self.pending_manager)
            self._save_pending_state()

    def _save_pending_state(self) -> None:
        saver = getattr(self.pending_manager, "_save", None)
        if callable(saver):
            saver()

    def _notify_error(self, message: str) -> None:
        if self.notifier is None:
            return
        try:
            self.notifier.send_error(message)
        except Exception as exc:
            log.warning("orphan retry limit 알림 실패: %s", exc)

    def detect_orphan_holdings(self, holdings: list[Holding]) -> list[str]:
        """브로커 보유는 있으나 PositionEntry가 없는 ticker를 durable lock으로 만든다."""
        detected: list[str] = []
        if self.pending_manager is None or not hasattr(self.position_manager, "get"):
            return detected
        for holding in holdings:
            ticker = str(getattr(holding, "ticker", "") or "").strip().upper()
            shares = float(getattr(holding, "shares", 0.0) or 0.0)
            if not ticker or shares <= SHARE_EPS or self.position_manager.get(ticker) is not None:
                continue
            if self.pending_manager.has_pending_buy(ticker):
                continue
            avg_cost = float(getattr(holding, "avg_cost", 0.0) or 0.0)
            current_price = float(getattr(holding, "current_price", 0.0) or 0.0)
            recovery_price = avg_cost if avg_cost > 0 else current_price
            order = Order(
                order_id=f"ORPHAN-{ticker}",
                ticker=ticker,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                shares=shares,
                price=0.0,
                status=OrderStatus.FILLED,
                filled_shares=shares,
                filled_avg_price=recovery_price,
                raw_status="orphan_holding",
            )
            self.track_failure(
                order,
                purpose="orphan_recovery",
                error="startup broker holding without PositionEntry",
                metadata={"source": "startup_holdings_reconciliation"},
            )
            detected.append(ticker)
        return detected


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


def _manager_now_iso(manager) -> str:
    now_iso = getattr(manager, "_now_iso", None)
    if callable(now_iso):
        return now_iso()
    return datetime.now().astimezone().isoformat()
