"""Live adapter for the central-controller buy selection path.

This module intentionally keeps the live integration narrow:

* it only controls new BUY selection/sizing when explicitly enabled;
* it does not change live exit handling or force-liquidate existing positions;
* it reuses the central backtester allocation helpers instead of introducing a
  separate selection algorithm.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from engine.central.allocation_policy import AllocationParams, BuyCandidate, BuyDecision, decide_buys
from engine.central.backtester import _decide_buys_with_selection_metric, _turnover_score_for_entity
from engine.central.entity_loader import EntityRecord
from engine.central.models import normalize_ticker
from engine.central.policy_search import apply_confidence_metric
from engine.central.stage2_survivor_loader import load_stage2_survivors_with_report
from engine.strategies.demo_rulebook import Signal

logger = logging.getLogger("live_central_control")

DEFAULT_BATCH_ROOT = Path("exp_batch_stage123_2009_20260616_full")
DEFAULT_CENTRAL_INDEX = DEFAULT_BATCH_ROOT / "central_index.jsonl"
DEFAULT_ENTITY_CONFIDENCE_PATH = Path("data/_system/central/stage2_b/swap_score_test2/entity_confidence_oos.json")
PERIOD_ORDER = (
    "stress_pre_2022h1",
    "train_1_eval",
    "train_2_eval",
    "train_3_eval",
    "oos_2025h2",
)


@dataclass(frozen=True)
class LiveCentralControlConfig:
    enabled: bool = False
    selection_metric: str = "confidence"
    max_positions: int = 8
    position_sizing: str = "score_weighted"
    confidence_weight: float = 0.5
    signal_strength_weight: float = 0.5
    per_ticker_exposure_cap: float = 0.25
    cash_buffer_ratio: float = 0.98
    min_confidence: float = 0.0
    pool_limit: int = 533
    confidence_mode: str = "adjusted"
    pf_cap: float = 10.0
    min_trades: int = 15
    batch_root: Path = DEFAULT_BATCH_ROOT
    central_index_path: Path = DEFAULT_CENTRAL_INDEX
    entity_confidence_path: Path = DEFAULT_ENTITY_CONFIDENCE_PATH


@dataclass(frozen=True)
class _LiveOpenPosition:
    entity_id: str
    ticker: str
    open_shares: float
    avg_entry_price: float
    position_id: str = ""
    add_buy_count: int = 0


class _LiveLedgerView:
    def __init__(self, positions) -> None:
        self._positions = list(positions or [])

    def open_positions(self) -> list[_LiveOpenPosition]:
        output: list[_LiveOpenPosition] = []
        for pos in self._positions:
            ticker = normalize_ticker(getattr(pos, "ticker", ""))
            if not ticker:
                continue
            try:
                shares = float(getattr(pos, "shares", 0.0) or 0.0)
            except Exception:
                shares = 0.0
            if shares <= 0.0:
                continue
            try:
                entry_price = float(getattr(pos, "entry_price", 0.0) or 0.0)
            except Exception:
                entry_price = 0.0
            member_hash = str(getattr(pos, "member_hash", "") or "").strip()
            entity_id = f"{ticker}_{member_hash[:12]}" if member_hash else f"{ticker}__live_position"
            output.append(
                _LiveOpenPosition(
                    entity_id=entity_id,
                    ticker=ticker,
                    open_shares=shares,
                    avg_entry_price=entry_price,
                    position_id=entity_id,
                    add_buy_count=int(getattr(pos, "add_buy_count", 0) or 0),
                )
            )
        return output


class LiveCentralController:
    """Central-controller wrapper for live new BUY decisions only."""

    def __init__(self, runner, config: LiveCentralControlConfig) -> None:
        self.runner = runner
        self.config = config
        self.selection_metric = _normalize_selection_metric(config.selection_metric)
        self.position_sizing = _normalize_position_sizing(config.position_sizing)
        self.confidence_mode = _normalize_confidence_mode(config.confidence_mode)
        self.entities = self._load_entities()
        self.entity_by_ticker: dict[str, list[EntityRecord]] = {}
        for entity in self.entities:
            self.entity_by_ticker.setdefault(normalize_ticker(entity.ticker), []).append(entity)
        self.selection_scores: dict[str, Optional[float]] = {}
        if self.selection_metric == "turnover_score":
            self.selection_scores = {
                entity.entity_id: _turnover_score_for_entity(entity)
                for entity in self.entities
            }
        logger.warning(
            "[CENTRAL-CONTROL] enabled metric=%s confidence_mode=%s pf_cap=%s min_trades=%s max_positions=%s sizing=%s promoted_symbols=%s central_entities=%s tickers=%s",
            self.selection_metric,
            self.confidence_mode,
            self.config.pf_cap,
            self.config.min_trades,
            self.config.max_positions,
            self.position_sizing,
            len(getattr(self.runner, "symbols", []) or []),
            len(self.entities),
            len(self.entity_by_ticker),
        )
        self._log_confidence_preview()

    def _load_entities(self) -> list[EntityRecord]:
        symbols = {normalize_ticker(t) for t in getattr(self.runner, "symbols", []) or []}
        if not symbols:
            raise RuntimeError("central-control requires a non-empty promoted symbol universe")
        confidence_path = Path(self.config.entity_confidence_path)
        if not confidence_path.exists():
            raise FileNotFoundError(f"central-control entity confidence file missing: {confidence_path}")
        confidence_rows = json.loads(confidence_path.read_text(encoding="utf-8"))
        if not isinstance(confidence_rows, dict):
            raise ValueError(f"central-control entity confidence file must be object: {confidence_path}")
        allowed_ids = set(confidence_rows.keys())
        if self.config.pool_limit and self.config.pool_limit > 0:
            allowed_ids = set(list(confidence_rows.keys())[: int(self.config.pool_limit)])

        report = load_stage2_survivors_with_report(
            self.config.central_index_path,
            self.config.batch_root,
            tickers=symbols,
        )
        entities = apply_confidence_metric(report.entities, "profit_factor")
        filtered: list[EntityRecord] = []
        for entity in entities:
            if entity.entity_id not in allowed_ids:
                continue
            if normalize_ticker(entity.ticker) not in symbols:
                continue
            if self.confidence_mode == "raw":
                entity = self._apply_stored_raw_confidence(entity, confidence_rows)
            else:
                entity = self._apply_adjusted_confidence(entity)
            filtered.append(entity)
        if not filtered:
            raise RuntimeError(
                "central-control promoted ∩ central survivor pool is empty "
                f"(symbols={len(symbols)}, stage2_loaded={report.loaded})"
            )
        return filtered

    def _apply_stored_raw_confidence(self, entity: EntityRecord, confidence_rows: dict) -> EntityRecord:
        stored = confidence_rows.get(entity.entity_id)
        if isinstance(stored, dict) and "confidence" in stored:
            try:
                return replace(entity, confidence=float(stored.get("confidence") or 0.0))
            except Exception:
                return entity
        return entity

    def _apply_adjusted_confidence(self, entity: EntityRecord) -> EntityRecord:
        adjusted = _adjusted_confidence_from_metrics(
            getattr(entity, "validation_metrics", None) or {},
            pf_cap=float(self.config.pf_cap),
            min_trades=int(self.config.min_trades),
        )
        return replace(entity, confidence=adjusted)

    def _log_confidence_preview(self) -> None:
        values = [float(entity.confidence or 0.0) for entity in self.entities]
        if not values:
            return
        p90 = _quantile(values, 0.9)
        logger.warning(
            "[CENTRAL-CONTROL] confidence preview mode=%s min=%.4f median=%.4f p90=%.4f max=%.4f mean=%.4f",
            self.confidence_mode,
            min(values),
            statistics.median(values),
            p90,
            max(values),
            statistics.mean(values),
        )
        preview = _theoretical_top_n_unique_tickers(self.entities, n=min(8, max(1, int(self.config.max_positions or 8))))
        logger.warning(
            "[CENTRAL-CONTROL] theoretical_top%s_no_positions=%s",
            len(preview),
            ",".join(f"{ticker}:{score:.4f}" for ticker, score in preview),
        )

    def tick_market(self) -> None:
        """Run the existing live tick with central-controller BUY selection.

        Exit checks, pending-order handling, approvals, and order submission still
        use the existing Runner/PositionManager paths.
        """
        try:
            self.runner._poll_pending_orders(context="tick_market.pre_exit")
            if self.runner.pending_order_manager.all():
                logger.info("pending 주문 존재 → 자동청산 체크 1 tick 보류")
                exited = []
            else:
                exited = self.runner.position_manager.check_exits(
                    self.runner.broker,
                    self.runner.notifier,
                    pending_manager=self.runner.pending_order_manager,
                )
            if exited:
                for record in exited:
                    self.runner._record_realized_pnl_from_trade(record)
                logger.info("자동 청산 %d건 완료", len(exited))
        except Exception as exc:
            self.runner._handle_error("central_control.position_manager.check_exits", exc)
        try:
            self.runner._process_pending_approvals()
        except Exception as exc:
            self.runner._handle_error("central_control._process_pending_approvals", exc)

        self.runner.stats.market_ticks += 1
        try:
            self.runner._poll_pending_orders(context="tick_market.pre_signal")
            self.runner._tick_locked_tickers = set()
            self._process_central_buy_selection()
        except Exception as exc:
            self.runner._handle_error("central_control.tick_market", exc)

    def _process_central_buy_selection(self) -> None:
        pending_mgr = getattr(self.runner, "pending_order_manager", None)
        candidates: list[BuyCandidate] = []
        candidate_signal_by_entity = {}
        candidate_price_by_entity: dict[str, float] = {}
        evaluated_symbols = 0
        eligible_symbols = sorted(set(getattr(self.runner, "symbols", []) or []) & set(self.entity_by_ticker))
        for ticker in eligible_symbols:
            if pending_mgr is not None and pending_mgr.is_ticker_locked(ticker):
                logger.info("[CENTRAL-CONTROL] %s pending 주문 잠금 → 시그널 처리 스킵", ticker)
                continue
            price = self.runner.broker.get_current_price(ticker)
            if price is None or float(price or 0.0) <= 0.0:
                logger.warning("[CENTRAL-CONTROL] %s 현재가 조회 실패", ticker)
                continue
            evaluated_symbols += 1
            self.runner._maybe_reconfirm_existing(ticker, float(price))
            sig = self.runner.rulebook.evaluate(ticker, float(price))
            if sig.signal == Signal.BUY:
                self.runner.stats.signals_buy += 1
                strength = _signal_strength(sig)
                for entity in self.entity_by_ticker.get(ticker, []):
                    cand = BuyCandidate(
                        entity_id=entity.entity_id,
                        ticker=entity.ticker,
                        confidence=float(entity.confidence or 0.0),
                        strength=strength,
                        price=float(price),
                        signal_score=float(getattr(sig, "score", 0.0) or 0.0),
                        threshold=float(getattr(sig, "threshold", 0.0) or 0.0),
                        rulebook=entity.rulebook,
                    )
                    candidates.append(cand)
                    candidate_signal_by_entity[entity.entity_id] = sig
                    candidate_price_by_entity[entity.entity_id] = float(price)
            elif sig.signal == Signal.SELL:
                self.runner.stats.signals_sell += 1
                self.runner._try_order("SELL", ticker, float(price), sig.reason, signal_result=sig)
            else:
                self.runner.stats.signals_hold += 1
                logger.debug("[CENTRAL-CONTROL] %s HOLD: %s", ticker, sig.reason)

        if not candidates:
            logger.info(
                "[CENTRAL-CONTROL] tick=%s evaluated_symbols=%s candidates=0 open_positions=%s",
                self.runner.stats.market_ticks,
                evaluated_symbols,
                len(self.runner.position_manager.all()),
            )
            return

        ledger = _LiveLedgerView(self.runner.position_manager.all())
        alloc = self._allocation_params()
        if self.selection_metric == "confidence":
            decisions = decide_buys(candidates, ledger, alloc)
        else:
            decisions = _decide_buys_with_selection_metric(candidates, ledger, alloc, self.selection_scores)
        logger.info(
            "[CENTRAL-CONTROL] tick=%s evaluated_symbols=%s candidates=%s decisions=%s open_positions=%s max_positions=%s metric=%s confidence_mode=%s",
            self.runner.stats.market_ticks,
            evaluated_symbols,
            len(candidates),
            len(decisions),
            len(self.runner.position_manager.all()),
            alloc.max_positions,
            self.selection_metric,
            self.confidence_mode,
        )
        for decision in decisions:
            sig = candidate_signal_by_entity.get(decision.entity_id)
            price = candidate_price_by_entity.get(decision.entity_id, 0.0)
            self._execute_decision(decision, sig, price)

    def _allocation_params(self) -> AllocationParams:
        return AllocationParams(
            max_positions=max(0, int(self.config.max_positions or 0)),
            confidence_weight=float(self.config.confidence_weight),
            signal_strength_weight=float(self.config.signal_strength_weight),
            min_confidence=float(self.config.min_confidence),
            per_ticker_exposure_cap=float(self.config.per_ticker_exposure_cap),
            total_capital=max(float(self._account_total_value_notional() or 0.0), 0.0),
            position_sizing=self.position_sizing,
            cash_buffer_ratio=float(self.config.cash_buffer_ratio),
        )

    def _account_total_value_notional(self) -> float:
        try:
            balance = self.runner.broker.get_balance()
            for key in ("total_value_usd", "total_value", "total_value_krw"):
                value = getattr(balance, key, None)
                if value is not None:
                    out = float(value or 0.0)
                    if out > 0.0:
                        return out
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL] 계좌 총액 조회 실패: %s", exc)
        return float(getattr(self.runner, "order_notional", 0.0) or 0.0) * max(int(self.config.max_positions or 0), 1)

    def _execute_decision(self, decision: BuyDecision, signal_result, price: float) -> None:
        if float(decision.notional or 0.0) <= 0.0 or float(price or 0.0) <= 0.0:
            logger.info("[CENTRAL-CONTROL] %s 주문 스킵: invalid notional/price", decision.ticker)
            return
        original_notional = getattr(self.runner, "order_notional", None)
        try:
            self.runner.order_notional = float(decision.notional)
            reason = (
                f"central_control metric={self.selection_metric} confidence_mode={self.confidence_mode} "
                f"entity={decision.entity_id} score={decision.score:.4f} "
                f"conf={decision.confidence:.4f} strength={decision.strength:.4f}"
            )
            self.runner._try_order("BUY", decision.ticker, float(price), reason, signal_result=signal_result)
        finally:
            self.runner.order_notional = original_notional


def _normalize_selection_metric(value: str) -> str:
    metric = str(value or "confidence").strip().lower()
    if metric not in {"confidence", "turnover_score"}:
        raise ValueError(f"unsupported central selection metric: {value}")
    return metric


def _normalize_position_sizing(value: str) -> str:
    mode = str(value or "score_weighted").strip().lower()
    if mode not in {"score_weighted", "equal"}:
        raise ValueError(f"unsupported central position sizing: {value}")
    return mode


def _normalize_confidence_mode(value: str) -> str:
    mode = str(value or "adjusted").strip().lower()
    if mode not in {"raw", "adjusted"}:
        raise ValueError(f"unsupported central confidence mode: {value}")
    return mode


def _adjusted_confidence_from_metrics(validation_metrics, *, pf_cap: float, min_trades: int) -> float:
    cap = max(float(pf_cap), 1.0)
    floor = max(int(min_trades), 0)
    adjusted: list[float] = []
    for label in PERIOD_ORDER:
        metric = validation_metrics.get(label) if isinstance(validation_metrics, dict) else None
        if not isinstance(metric, dict):
            adjusted.append(1.0)
            continue
        trade_count = _safe_float(metric.get("trade_count"), 0.0)
        profit_factor = _safe_float(metric.get("profit_factor"), 0.0)
        if trade_count < floor:
            adjusted.append(1.0)
        else:
            adjusted.append(min(max(profit_factor, 0.0), cap))
    return sum(adjusted) / len(adjusted) - 1.0


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _quantile(values: list[float], q: float) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def _theoretical_top_n_unique_tickers(entities: list[EntityRecord], *, n: int) -> list[tuple[str, float]]:
    selected: list[tuple[str, float]] = []
    used: set[str] = set()
    for entity in sorted(entities, key=lambda e: (float(e.confidence or 0.0), e.entity_id), reverse=True):
        ticker = normalize_ticker(entity.ticker)
        if not ticker or ticker in used:
            continue
        score = float(entity.confidence or 0.0)
        if score <= 0.0:
            continue
        selected.append((ticker, score))
        used.add(ticker)
        if len(selected) >= n:
            break
    return selected


def _signal_strength(signal_result) -> float:
    try:
        score = float(getattr(signal_result, "score", 0.0) or 0.0)
        threshold = float(getattr(signal_result, "threshold", 0.0) or 0.0)
        if threshold > 0.0:
            return max(0.0, score / threshold)
        return max(0.0, score)
    except Exception:
        return 0.0
