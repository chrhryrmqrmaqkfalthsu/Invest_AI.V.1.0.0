"""Score-based allocation policy for central-controller backtests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from engine.central.models import normalize_shares, normalize_ticker

MIN_ORDER_SHARES = 1e-6


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
    purpose: str = "entry"
    target_position_id: str = ""


def decide_buys(buy_candidates: Iterable[BuyCandidate], current_ledger, params: AllocationParams) -> list[BuyDecision]:
    """Select and size BUY candidates.

    ``max_positions`` is interpreted as a concurrent distinct-ticker cap for
    entry positions. Existing positions for the same entity are skipped unless
    the candidate rulebook explicitly allows add-buy.
    """
    open_positions = list(current_ledger.open_positions()) if current_ledger is not None else []
    active_open_positions = [p for p in open_positions if normalize_shares(getattr(p, "open_shares", 0.0)) > 0.0]
    open_by_entity = {p.entity_id: p for p in active_open_positions}
    open_tickers = {normalize_ticker(getattr(p, "ticker", "")) for p in active_open_positions if normalize_ticker(getattr(p, "ticker", ""))}
    ticker_exposure = _ticker_exposure(active_open_positions)
    max_tickers = max(int(params.max_positions or 0), 0)
    remaining_new_ticker_slots = max(max_tickers - len(open_tickers), 0)

    candidate_rows = []
    for cand in buy_candidates:
        ticker = normalize_ticker(cand.ticker)
        price = float(cand.price or 0.0)
        if not ticker or price <= 0.0:
            continue
        confidence = float(cand.confidence or 0.0)
        if confidence < params.min_confidence:
            continue
        purpose = "entry"
        target_position_id = ""
        existing = open_by_entity.get(cand.entity_id)
        if existing is not None:
            rb = dict(cand.rulebook or {})
            if not bool(rb.get("add_buy_enabled", False)):
                continue
            if int(getattr(existing, "add_buy_count", 0) or 0) >= int(rb.get("add_buy_max_count", 0) or 0):
                continue
            purpose = "add_buy"
            target_position_id = str(getattr(existing, "position_id", "") or "")
        elif ticker in open_tickers:
            # A different rulebook for an already-held ticker must not consume a
            # new slot or create duplicate ticker exposure.
            continue
        score = float(params.confidence_weight) * confidence + float(params.signal_strength_weight) * float(cand.strength or 0.0)
        if score <= 0.0:
            continue
        candidate_rows.append((score, cand, purpose, target_position_id, ticker, price))

    candidate_rows.sort(key=lambda row: (row[0], row[1].confidence, row[1].strength, row[1].entity_id), reverse=True)
    selected_rows = []
    selected_new_tickers: set[str] = set()
    for row in candidate_rows:
        purpose = row[2]
        ticker = row[4]
        if purpose == "add_buy":
            selected_rows.append(row)
            continue
        if ticker in selected_new_tickers:
            continue
        if len(selected_new_tickers) >= remaining_new_ticker_slots:
            continue
        selected_rows.append(row)
        selected_new_tickers.add(ticker)
    if not selected_rows:
        return []

    capital = float(params.total_capital or 0.0)
    investable_capital = capital * _cash_use_ratio(params.cash_buffer_ratio)
    weights = _weights([row[0] for row in selected_rows], params.position_sizing)
    decisions: list[BuyDecision] = []
    for weight, (score, cand, purpose, target_position_id, ticker, price) in zip(weights, selected_rows):
        desired_notional = investable_capital * float(weight)
        cap_notional = capital * max(float(params.per_ticker_exposure_cap or 0.0), 0.0)
        used = ticker_exposure.get(ticker, 0.0)
        allowed = max(0.0, cap_notional - used)
        notional = min(desired_notional, allowed)
        if notional <= max(float(params.min_notional or 0.0), 0.0):
            continue
        shares = normalize_shares(notional / price)
        if shares <= MIN_ORDER_SHARES:
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
                purpose=purpose,
                target_position_id=target_position_id,
            )
        )
        ticker_exposure[ticker] = ticker_exposure.get(ticker, 0.0) + shares * price
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


def _ticker_exposure(open_positions) -> dict[str, float]:
    exposure: dict[str, float] = {}
    for pos in open_positions or []:
        ticker = normalize_ticker(getattr(pos, "ticker", ""))
        if not ticker:
            continue
        shares = normalize_shares(getattr(pos, "open_shares", 0.0))
        price = float(
            getattr(pos, "current_price", 0.0)
            or getattr(pos, "market_price", 0.0)
            or getattr(pos, "avg_entry_price", 0.0)
            or 0.0
        )
        exposure[ticker] = exposure.get(ticker, 0.0) + shares * price
    return exposure
