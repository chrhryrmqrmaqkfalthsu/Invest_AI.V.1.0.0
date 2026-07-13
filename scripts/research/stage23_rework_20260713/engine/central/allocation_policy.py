"""Score-based allocation policy for central-controller backtests."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from engine.central.models import normalize_shares, normalize_ticker

MIN_ORDER_SHARES = 1e-6
SHARE_ROUND_DIGITS = 6
SHARE_SCALE = 10 ** SHARE_ROUND_DIGITS
MAX_ORDER_NOTIONAL_SAFETY_BUFFER = 0.005


@dataclass(frozen=True)
class AllocationParams:
    max_positions: int = 10
    confidence_weight: float = 1.0
    signal_strength_weight: float = 1.0
    min_confidence: float = 0.0
    per_ticker_exposure_cap: float = 0.25
    total_capital: float = 100_000.0
    position_sizing: str = "score_weighted"  # score_weighted | equal
    min_notional: float = 0.0
    cash_buffer_ratio: float = 0.98
    # Apply only when desired_notional is clipped by per-ticker cap.  The live
    # SafetyLayer remains the final 25% cap; this buffer sizes cap-bound orders
    # slightly below that cap so small quote moves do not trip LIMIT_NOTIONAL.
    order_notional_safety_buffer: float = 0.0
    # False preserves the original behavior: max_positions is a distinct-ticker
    # cap and same-ticker duplicate entities are de-duped. True makes
    # max_positions an entity/position cap while per_ticker_exposure_cap still
    # aggregates exposure across all entities for the ticker.
    allow_same_ticker_entities: bool = False
    # Optional mutable diagnostics sink used by research backtests. The dataclass
    # remains frozen, but the referenced dict may be updated by decide_buys().
    allocation_stats: Optional[dict] = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class BuyCandidate:
    entity_id: str
    ticker: str
    confidence: float
    strength: float
    price: float
    signal_score: float = 0.0
    threshold: float = 0.0
    rulebook: Optional[dict] = None


@dataclass(frozen=True)
class BuyDecision:
    entity_id: str
    ticker: str
    shares: float
    notional: float
    score: float
    confidence: float
    strength: float
    rulebook: Optional[dict] = None
    purpose: str = "entry"
    target_position_id: str = ""


def decide_buys(buy_candidates: Iterable[BuyCandidate], current_ledger, params: AllocationParams) -> list[BuyDecision]:
    """Select and size BUY candidates.

    Default mode keeps the historical central-controller semantics: ``max_positions``
    is a concurrent distinct-ticker cap, and duplicate tickers are de-duped.

    When ``allow_same_ticker_entities=True``, ``max_positions`` becomes an
    entity/position cap. Multiple entities for the same ticker may be selected,
    while ticker-level exposure is still capped by the sum of all open/selected
    positions for that ticker.
    """
    allow_same_ticker_entities = bool(getattr(params, "allow_same_ticker_entities", False))
    open_positions = list(current_ledger.open_positions()) if current_ledger is not None else []
    active_open_positions = [p for p in open_positions if normalize_shares(getattr(p, "open_shares", 0.0)) > 0.0]
    open_by_entity = {str(getattr(p, "entity_id", "") or ""): p for p in active_open_positions}
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in active_open_positions if normalize_ticker(getattr(p, "ticker", ""))}
    ticker_exposure, unknown_exposure_tickers = _ticker_exposure(active_open_positions)
    max_positions = max(int(params.max_positions or 0), 0)
    if allow_same_ticker_entities:
        remaining_new_slots = max(max_positions - len(active_open_positions), 0)
    else:
        remaining_new_slots = max(max_positions - len(open_tickers), 0)

    stats = _stats_sink(params)
    _bump(stats, "calls")
    _bump(stats, "open_position_count", len(active_open_positions))
    _bump(stats, "open_ticker_count", len(open_tickers))

    candidate_rows = []
    for cand in buy_candidates:
        _bump(stats, "candidates_seen")
        ticker = normalize_ticker(cand.ticker)
        price = float(cand.price or 0.0)
        if not ticker or price <= 0.0:
            _bump(stats, "rejected_invalid_ticker_or_price")
            continue
        confidence = float(cand.confidence or 0.0)
        if confidence < params.min_confidence:
            _bump(stats, "rejected_below_min_confidence")
            continue
        entity_id = str(cand.entity_id or "")
        purpose = "entry"
        target_position_id = ""
        existing = open_by_entity.get(entity_id)
        if existing is not None:
            rb = dict(cand.rulebook or {})
            if not bool(rb.get("add_buy_enabled", False)):
                _bump(stats, "rejected_already_open_entity_add_buy_disabled")
                continue
            if int(getattr(existing, "add_buy_count", 0) or 0) >= int(rb.get("add_buy_max_count", 0) or 0):
                _bump(stats, "rejected_add_buy_max_count")
                continue
            if ticker in unknown_exposure_tickers:
                # Existing exposure cannot be valued safely. Fail closed for add-buy
                # instead of treating unknown exposure as zero and bypassing caps.
                _bump(stats, "rejected_unknown_existing_exposure")
                continue
            purpose = "add_buy"
            target_position_id = str(getattr(existing, "position_id", "") or "")
        elif (not allow_same_ticker_entities) and ticker in open_tickers:
            # Historical ticker-mode behavior: a different rulebook for an
            # already-held ticker must not create duplicate ticker exposure.
            _bump(stats, "rejected_already_held_ticker")
            continue
        score = float(params.confidence_weight) * confidence + float(params.signal_strength_weight) * float(cand.strength or 0.0)
        if score <= 0.0:
            _bump(stats, "rejected_non_positive_score")
            continue
        candidate_rows.append((score, cand, purpose, target_position_id, ticker, price))

    candidate_rows.sort(key=lambda row: (row[0], row[1].confidence, row[1].strength, row[1].entity_id), reverse=True)
    selected_rows = []
    selected_new_tickers: set[str] = set()
    selected_new_entities: set[str] = set()
    for row in candidate_rows:
        purpose = row[2]
        ticker = row[4]
        entity_id = str(row[1].entity_id or "")
        if purpose == "add_buy":
            selected_rows.append(row)
            continue
        if allow_same_ticker_entities:
            if entity_id in selected_new_entities:
                _bump(stats, "rejected_duplicate_entity_same_tick")
                continue
            if len(selected_new_entities) >= remaining_new_slots:
                _bump(stats, "rejected_entity_slots_full")
                continue
            selected_rows.append(row)
            selected_new_entities.add(entity_id)
        else:
            if ticker in selected_new_tickers:
                _bump(stats, "rejected_duplicate_ticker_same_tick")
                continue
            if len(selected_new_tickers) >= remaining_new_slots:
                _bump(stats, "rejected_ticker_slots_full")
                continue
            selected_rows.append(row)
            selected_new_tickers.add(ticker)
    if not selected_rows:
        _bump(stats, "empty_decision_days")
        return []

    capital = float(params.total_capital or 0.0)
    investable_capital = capital * _cash_use_ratio(params.cash_buffer_ratio)
    buffer = _order_notional_safety_buffer(getattr(params, "order_notional_safety_buffer", 0.0))
    weights = _weights([row[0] for row in selected_rows], params.position_sizing)
    decisions: list[BuyDecision] = []
    for weight, (score, cand, purpose, target_position_id, ticker, price) in zip(weights, selected_rows):
        if ticker in unknown_exposure_tickers:
            _bump(stats, "rejected_unknown_ticker_exposure")
            continue
        desired_notional = investable_capital * float(weight)
        cap_notional = capital * max(float(params.per_ticker_exposure_cap or 0.0), 0.0)
        used = ticker_exposure.get(ticker, 0.0)
        allowed = max(0.0, cap_notional - used)
        cap_limited = allowed <= 0.0 or desired_notional > allowed + 1e-9
        effective_allowed = allowed
        if cap_limited and buffer > 0.0:
            effective_allowed = allowed * (1.0 - buffer)
            _bump(stats, "order_notional_safety_buffer_applied")
        notional = min(desired_notional, effective_allowed)
        if cap_limited:
            _bump(stats, "ticker_cap_hit_events")
            _bump_nested(stats, "ticker_cap_hit_by_ticker", ticker)
        if notional <= max(float(params.min_notional or 0.0), 0.0):
            _bump(stats, "rejected_min_notional_or_cap")
            continue
        shares = _floor_shares(notional / price) if cap_limited and buffer > 0.0 else normalize_shares(notional / price)
        if shares <= MIN_ORDER_SHARES:
            _bump(stats, "rejected_dust_shares")
            continue
        decisions.append(
            BuyDecision(
                entity_id=cand.entity_id,
                ticker=ticker,
                shares=shares,
                notional=shares * price,
                score=score,
                confidence=float(cand.confidence or 0.0),
                strength=float(cand.strength or 0.0),
                rulebook=dict(cand.rulebook or {}),
                purpose=purpose,
                target_position_id=target_position_id,
            )
        )
        ticker_exposure[ticker] = ticker_exposure.get(ticker, 0.0) + shares * price
        _bump(stats, "decisions")
        _bump(stats, "allocated_notional", shares * price)
        _bump_nested(stats, "selected_entities_by_ticker", ticker)
    return decisions


def _weights(scores: list[float], mode: str) -> list[float]:
    if not scores:
        return []
    if str(mode or "").lower() == "equal":
        return [1.0 / len(scores)] * len(scores)
    total = sum(max(float(s), 0.0) for s in scores)
    if total <= 0:
        return [1.0 / len(scores)] * len(scores)
    return [max(float(s), 0.0) / total for s in scores]


def _cash_use_ratio(cash_buffer_ratio: float) -> float:
    """Return the fraction of total capital that may be allocated.

    Existing central-controller configs use ``cash_buffer_ratio=0.98`` to mean
    "use 98% of capital and keep roughly 2% cash". Keep that convention here and
    finally apply it inside ``decide_buys``.
    """
    return max(0.0, min(float(cash_buffer_ratio or 0.0), 1.0))


def _order_notional_safety_buffer(value: float) -> float:
    try:
        out = float(value or 0.0)
    except Exception:
        out = 0.0
    return max(0.0, min(out, MAX_ORDER_NOTIONAL_SAFETY_BUFFER))


def _floor_shares(value: float) -> float:
    try:
        raw = float(value or 0.0)
    except Exception:
        return 0.0
    if raw <= 0.0:
        return 0.0
    floored = math.floor(raw * SHARE_SCALE) / SHARE_SCALE
    return 0.0 if floored <= MIN_ORDER_SHARES else floored


def _ticker_exposure(open_positions) -> tuple[dict[str, float], set[str]]:
    exposure: dict[str, float] = {}
    unknown: set[str] = set()
    for pos in open_positions or []:
        ticker = normalize_ticker(getattr(pos, "ticker", ""))
        if not ticker:
            continue
        shares = normalize_shares(getattr(pos, "open_shares", 0.0))
        if shares <= 0.0:
            continue
        price = _first_positive(
            getattr(pos, "current_price", 0.0),
            getattr(pos, "market_price", 0.0),
            getattr(pos, "avg_entry_price", 0.0),
        )
        if price <= 0.0:
            unknown.add(ticker)
            continue
        exposure[ticker] = exposure.get(ticker, 0.0) + shares * price
    return exposure, unknown


def _first_positive(*values) -> float:
    for value in values:
        try:
            out = float(value or 0.0)
        except Exception:
            out = 0.0
        if out > 0.0:
            return out
    return 0.0


def _stats_sink(params: AllocationParams) -> Optional[dict]:
    stats = getattr(params, "allocation_stats", None)
    return stats if isinstance(stats, dict) else None


def _bump(stats: Optional[dict], key: str, amount: float = 1.0) -> None:
    if stats is None:
        return
    stats[key] = stats.get(key, 0.0) + amount


def _bump_nested(stats: Optional[dict], key: str, item: str) -> None:
    if stats is None:
        return
    nested = stats.setdefault(key, {})
    nested[item] = nested.get(item, 0) + 1
