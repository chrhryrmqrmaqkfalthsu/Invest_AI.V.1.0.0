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
            "[CENTRAL-CONTROL] enabled metric=%s max_positions=%s sizing=%s promoted_symbols=%s central_entities=%s tickers=%s",
            self.selection_metric,
            self.config.max_positions,
            self.position_sizing,
            len(getattr(self.runner, "symbols", []) or []),
            len(self.entities),
            len(self.entity_by_ticker),
        )

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
            stored = confidence_rows.get(entity.entity_id)
            if isinstance(stored, dict) and "confidence" in stored:
                try:
                    entity = replace(entity, confidence=float(stored.get("confidence") or 0.0))
                except Exception:
                    pass
            filtered.append(entity)
        if not filtered:
            raise RuntimeError(
                "central-control promoted ∩ central survivor pool is empty "
                f"(symbols={len(symbols)}, stage2_loaded={report.loaded})"
            )
        return filtered

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
            "[CENTRAL-CONTROL] tick=%s evaluated_symbols=%s candidates=%s decisions=%s open_positions=%s max_positions=%s metric=%s",
            self.runner.stats.market_ticks,
            evaluated_symbols,
            len(candidates),
            len(decisions),
            len(self.runner.position_manager.all()),
            alloc.max_positions,
            self.selection_metric,
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
                f"central_control metric={self.selection_metric} entity={decision.entity_id} "
                f"score={decision.score:.4f} conf={decision.confidence:.4f} strength={decision.strength:.4f}"
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


def _signal_strength(signal_result) -> float:
    try:
        score = float(getattr(signal_result, "score", 0.0) or 0.0)
        threshold = float(getattr(signal_result, "threshold", 0.0) or 0.0)
        if threshold > 0.0:
            return max(0.0, score / threshold)
        return max(0.0, score)
    except Exception:
        return 0.0
