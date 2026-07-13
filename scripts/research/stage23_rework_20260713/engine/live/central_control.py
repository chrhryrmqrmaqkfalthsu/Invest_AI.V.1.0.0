"""Live adapter for the central-controller buy selection path.

This module intentionally keeps the live integration narrow:

* it only controls new BUY selection/sizing when explicitly enabled;
* it does not change live exit handling or force-liquidate existing positions;
* it reuses the central backtester allocation helpers instead of introducing a
  separate selection algorithm.
"""
from __future__ import annotations

import csv
import json
import logging
import math
import statistics
from dataclasses import dataclass, replace
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from engine.central.allocation_policy import AllocationParams, BuyCandidate, BuyDecision, decide_buys
from engine.central.backtester import _decide_buys_with_selection_metric, _turnover_score_for_entity
from engine.central.entity_loader import EntityRecord, load_entities_from_catalog
from engine.central.models import normalize_ticker
from engine.central.policy_search import apply_confidence_metric
from engine.central.stage2_survivor_loader import load_stage2_survivors_with_report
from engine.live.manual_buy_intent import (
    CENTRAL_BUY_CANDIDATES_PATH,
    MANUAL_BUY_INTENT_PATH,
    TERMINAL_CANDIDATE_STATUSES,
    candidate_from_decision,
    candidate_id_for,
    load_candidate_state,
    load_pending_manual_intents,
    mark_candidate_status,
    mark_intent_status,
    publish_candidate_state,
    trade_date_et,
)
from engine.live.position_manager import TRADE_LOG_PATH
from engine.live.event_policy import append_shadow_direct_event_log, live_event_flags
from engine.market.context import get_market_context
from engine.strategies.demo_rulebook import Signal, SignalResult
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook

logger = logging.getLogger("live_central_control")

DEFAULT_BATCH_ROOT = Path("exp_batch_stage123_2009_20260616_full")
DEFAULT_CENTRAL_INDEX = DEFAULT_BATCH_ROOT / "central_index.jsonl"
DEFAULT_ENTITY_CONFIDENCE_PATH = Path("data/_system/central/stage2_b/swap_score_test2/entity_confidence_oos.json")
DEFAULT_STAGE3_LIVE_POOL_PATH = Path("data/_system/central/stage3_live_pool/stage3_live_pool.jsonl")
DEFAULT_ORDER_NOTIONAL_SAFETY_BUFFER = 0.003
MAX_ORDER_NOTIONAL_SAFETY_BUFFER = 0.005
# SafetyLayer와 동일한 절대 화이트리스트 기준을 사용한다.
WHITELIST_SYMBOLS_DIR = Path.home() / "kingmaker" / "data" / "symbols"
ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
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
    buy_mode: str = "auto"  # auto | semi_auto
    auto_fallback_hour_et: int = 15
    auto_fallback_minute_et: int = 30
    order_notional_safety_buffer: float = DEFAULT_ORDER_NOTIONAL_SAFETY_BUFFER
    manual_intent_path: Path = MANUAL_BUY_INTENT_PATH
    candidate_state_path: Path = CENTRAL_BUY_CANDIDATES_PATH
    batch_root: Path = DEFAULT_BATCH_ROOT
    central_index_path: Path = DEFAULT_CENTRAL_INDEX
    entity_confidence_path: Path = DEFAULT_ENTITY_CONFIDENCE_PATH
    stage3_mix_enabled: bool = False
    stage3_live_pool_path: Path = DEFAULT_STAGE3_LIVE_POOL_PATH
    stage3_pool_limit: int = 0
    central_strength_cap: float = 4.0
    central_stage3_strength_cap: float = 3.0
    central_stage3_min_confidence: float = 0.0


@dataclass(frozen=True)
class _LiveOpenPosition:
    entity_id: str
    ticker: str
    open_shares: float
    avg_entry_price: float
    current_price: float = 0.0
    position_id: str = ""
    add_buy_count: int = 0
    source: str = "position"


class _LiveLedgerView:
    def __init__(self, positions) -> None:
        self._positions = list(positions or [])

    def open_positions(self) -> list[_LiveOpenPosition]:
        return list(self._positions)


class LiveCentralController:
    """Central-controller wrapper for live new BUY decisions only."""

    def __init__(self, runner, config: LiveCentralControlConfig) -> None:
        self.runner = runner
        self.config = config
        self.selection_metric = _normalize_selection_metric(config.selection_metric)
        self.position_sizing = _normalize_position_sizing(config.position_sizing)
        self.confidence_mode = _normalize_confidence_mode(config.confidence_mode)
        self.buy_mode = _normalize_buy_mode(config.buy_mode)
        self.order_notional_safety_buffer = _normalize_order_notional_safety_buffer(
            getattr(config, "order_notional_safety_buffer", DEFAULT_ORDER_NOTIONAL_SAFETY_BUFFER)
        )
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
            "[CENTRAL-CONTROL] enabled metric=%s confidence_mode=%s pf_cap=%s min_trades=%s max_positions=%s sizing=%s buy_mode=%s order_notional_safety_buffer=%.4f stage3_mix=%s stage3_pool=%s strength_cap=%.4f stage3_strength_cap=%.4f stage3_min_confidence=%.4f promoted_symbols=%s central_entities=%s tickers=%s",
            self.selection_metric,
            self.confidence_mode,
            self.config.pf_cap,
            self.config.min_trades,
            self.config.max_positions,
            self.position_sizing,
            self.buy_mode,
            self.order_notional_safety_buffer,
            bool(getattr(self.config, "stage3_mix_enabled", False)),
            str(getattr(self.config, "stage3_live_pool_path", "")),
            _safe_float(getattr(self.config, "central_strength_cap", 4.0), 4.0),
            _safe_float(getattr(self.config, "central_stage3_strength_cap", 3.0), 3.0),
            _safe_float(getattr(self.config, "central_stage3_min_confidence", 0.0), 0.0),
            len(getattr(self.runner, "symbols", []) or []),
            len(self.entities),
            len(self.entity_by_ticker),
        )
        self._log_confidence_preview()

    def _load_entities(self) -> list[EntityRecord]:
        base_symbols = {normalize_ticker(t) for t in getattr(self.runner, "symbols", []) or []}
        if not base_symbols:
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
            tickers=base_symbols,
        )
        entities = apply_confidence_metric(report.entities, "profit_factor")
        stage2_filtered: list[EntityRecord] = []
        for entity in entities:
            if entity.entity_id not in allowed_ids:
                continue
            if normalize_ticker(entity.ticker) not in base_symbols:
                continue
            if self.confidence_mode == "raw":
                entity = self._apply_stored_raw_confidence(entity, confidence_rows)
            else:
                entity = self._apply_adjusted_confidence(entity)
            stage2_filtered.append(entity)

        stage3_filtered = self._load_stage3_mix_entities()
        filtered = _dedupe_entities_by_id([*stage2_filtered, *stage3_filtered])
        logger.warning(
            "[CENTRAL-CONTROL] entity pool loaded stage2=%s stage3_mix=%s total=%s unique_tickers=%s",
            len(stage2_filtered),
            len(stage3_filtered),
            len(filtered),
            len({normalize_ticker(entity.ticker) for entity in filtered}),
        )
        if not filtered:
            raise RuntimeError(
                "central-control promoted ∩ central survivor pool is empty "
                f"(base_symbols={len(base_symbols)}, stage2_loaded={report.loaded}, stage3_mix={len(stage3_filtered)})"
            )
        return filtered

    def _load_stage3_mix_entities(self) -> list[EntityRecord]:
        if not bool(getattr(self.config, "stage3_mix_enabled", False)):
            return []
        path = Path(getattr(self.config, "stage3_live_pool_path", DEFAULT_STAGE3_LIVE_POOL_PATH))
        if not path.exists():
            raise FileNotFoundError(f"central-control Stage3 live pool missing: {path}")
        entities = load_entities_from_catalog(path, require_eligible=True)
        out: list[EntityRecord] = []
        limit = max(0, int(getattr(self.config, "stage3_pool_limit", 0) or 0))
        for entity in entities:
            ticker = normalize_ticker(entity.ticker)
            if not ticker:
                continue
            tags = dict(getattr(entity, "tags", {}) or {})
            tags["stage"] = "stage3_live_pool"
            tags["source_pool"] = str(path)
            entity = replace(entity, tags=tags)
            if self.confidence_mode == "adjusted":
                entity = self._apply_adjusted_confidence(entity)
            out.append(entity)
            if limit > 0 and len(out) >= limit:
                break
        return out

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
            self.runner._process_manual_sell_intents()
        except Exception as exc:
            self.runner._handle_error("central_control._process_manual_sell_intents", exc)
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

    def _is_live_whitelisted_ticker(self, ticker: str) -> bool:
        ticker_u = normalize_ticker(ticker)
        return bool(ticker_u) and (WHITELIST_SYMBOLS_DIR / ticker_u).is_dir()

    def _process_central_buy_selection(self) -> None:
        if self._state_unavailable_for_new_buys():
            logger.error("[CENTRAL-CONTROL] position/pending state unavailable → 신규 BUY fail-closed")
            return
        pending_mgr = getattr(self.runner, "pending_order_manager", None)
        candidates: list[BuyCandidate] = []
        candidate_signal_by_entity = {}
        candidate_price_by_entity: dict[str, float] = {}
        evaluated_symbols = 0
        skipped_reentry_symbols = 0
        skipped_whitelist_symbols = 0
        trade_date = self._trade_date_et()
        same_day_blocked_tickers = self._same_day_reentry_blocked_tickers(trade_date)
        # Stage2 entities are filtered by the promoted live universe, but Stage3
        # live-pool entities intentionally expand the central BUY evaluation universe.
        # Live order submission still requires data/symbols/<ticker> to exist, so
        # filter by the same whitelist criterion here before exposing candidates.
        eligible_symbols = sorted(set(self.entity_by_ticker))
        for ticker in eligible_symbols:
            ticker_u = normalize_ticker(ticker)
            if not self._is_live_whitelisted_ticker(ticker_u):
                skipped_whitelist_symbols += 1
                logger.info("[CENTRAL-CONTROL] %s whitelist 없음 → 신규 BUY 후보 제외", ticker_u)
                continue
            if ticker_u in same_day_blocked_tickers:
                skipped_reentry_symbols += 1
                logger.info("[CENTRAL-CONTROL] %s same-day terminal/exit → 신규 BUY 후보 제외", ticker_u)
                continue
            if pending_mgr is not None and pending_mgr.is_ticker_locked(ticker):
                logger.info("[CENTRAL-CONTROL] %s pending 주문 잠금 → 시그널 처리 스킵", ticker)
                continue
            price = self.runner.broker.get_current_price(ticker)
            if price is None or float(price or 0.0) <= 0.0:
                logger.warning("[CENTRAL-CONTROL] %s 현재가 조회 실패", ticker)
                continue
            evaluated_symbols += 1
            self.runner._maybe_reconfirm_existing(ticker, float(price))
            ticker_had_buy = False
            ticker_had_sell = False
            last_hold_reason = ""
            for entity in self.entity_by_ticker.get(ticker, []):
                sig = self._evaluate_entity_signal(entity, float(price))
                if sig.signal == Signal.BUY:
                    confidence = float(entity.confidence or 0.0)
                    strength, orig_strength, guard_reason = self._candidate_strength_for_entity(entity, sig, confidence)
                    if strength is None:
                        last_hold_reason = guard_reason
                        logger.info(
                            "[CENTRAL-CONTROL] %s BUY candidate skipped by guard entity=%s conf=%.4f orig_strength=%.4f reason=%s",
                            ticker,
                            entity.entity_id,
                            confidence,
                            orig_strength,
                            guard_reason,
                        )
                        continue
                    ticker_had_buy = True
                    if abs(float(strength or 0.0) - float(orig_strength or 0.0)) > 1e-9:
                        logger.info(
                            "[CENTRAL-CONTROL] %s strength capped entity=%s stage=%s orig=%.4f effective=%.4f",
                            ticker,
                            entity.entity_id,
                            _entity_stage(entity),
                            orig_strength,
                            strength,
                        )
                    cand = BuyCandidate(
                        entity_id=entity.entity_id,
                        ticker=entity.ticker,
                        confidence=confidence,
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
                    ticker_had_sell = True
                    logger.info("[CENTRAL-CONTROL] %s SELL signal ignored; exits are PositionManager-only entity=%s", ticker, entity.entity_id)
                else:
                    last_hold_reason = str(getattr(sig, "reason", "") or "")
            if ticker_had_buy:
                self.runner.stats.signals_buy += 1
            elif ticker_had_sell:
                self.runner.stats.signals_sell += 1
            else:
                self.runner.stats.signals_hold += 1
                logger.debug("[CENTRAL-CONTROL] %s HOLD: %s", ticker, last_hold_reason)

        if skipped_reentry_symbols:
            logger.info(
                "[CENTRAL-CONTROL] same-day reentry filter skipped_symbols=%s blocked=%s trade_date=%s",
                skipped_reentry_symbols,
                ",".join(sorted(same_day_blocked_tickers)),
                trade_date,
            )
        if skipped_whitelist_symbols:
            logger.info(
                "[CENTRAL-CONTROL] whitelist filter skipped_symbols=%s dir=%s",
                skipped_whitelist_symbols,
                str(WHITELIST_SYMBOLS_DIR),
            )

        if not candidates:
            logger.info(
                "[CENTRAL-CONTROL] tick=%s evaluated_symbols=%s candidates=0 open_positions=%s whitelist_skipped=%s",
                self.runner.stats.market_ticks,
                evaluated_symbols,
                len(self.runner.position_manager.all()),
                skipped_whitelist_symbols,
            )
            if getattr(self, "buy_mode", "auto") == "semi_auto":
                publish_candidate_state(
                    [],
                    path=self.config.candidate_state_path,
                    buy_mode=getattr(self, "buy_mode", "auto"),
                    trade_date=trade_date,
                )
            return

        ledger = self._build_live_ledger_view()
        alloc = self._allocation_params()
        if self.selection_metric == "confidence":
            decisions = decide_buys(candidates, ledger, alloc)
        else:
            decisions = _decide_buys_with_selection_metric(candidates, ledger, alloc, self.selection_scores)
        logger.info(
            "[CENTRAL-CONTROL] tick=%s evaluated_symbols=%s candidates=%s decisions=%s open_positions=%s ledger_slots=%s max_positions=%s metric=%s confidence_mode=%s buy_mode=%s sizing_buffer=%.4f reentry_blocked=%s whitelist_skipped=%s",
            self.runner.stats.market_ticks,
            evaluated_symbols,
            len(candidates),
            len(decisions),
            len(self.runner.position_manager.all()),
            len(ledger.open_positions()),
            alloc.max_positions,
            self.selection_metric,
            self.confidence_mode,
            getattr(self, "buy_mode", "auto"),
            alloc.order_notional_safety_buffer,
            skipped_reentry_symbols,
            skipped_whitelist_symbols,
        )
        if getattr(self, "buy_mode", "auto") == "semi_auto":
            self._process_semi_auto_decisions(decisions, candidate_signal_by_entity, candidate_price_by_entity)
            return
        for decision in decisions:
            sig = candidate_signal_by_entity.get(decision.entity_id)
            price = candidate_price_by_entity.get(decision.entity_id, 0.0)
            self._execute_decision(decision, sig, price, execution_reason="auto")

    def _candidate_strength_for_entity(self, entity: EntityRecord, signal_result, confidence: float) -> tuple[Optional[float], float, str]:
        orig_strength = _signal_strength(signal_result)
        # The Stage3 safety guard must be completely inert unless Stage3 mixing
        # is explicitly enabled. This preserves the current Stage2-only paper
        # behavior when --central-stage3-mix is off.
        if not bool(getattr(self.config, "stage3_mix_enabled", False)):
            return orig_strength, orig_strength, ""

        stage = _entity_stage(entity)
        is_stage3 = stage == "stage3_live_pool"
        try:
            setattr(signal_result, "orig_strength", float(orig_strength))
        except Exception:
            pass

        if is_stage3:
            min_confidence = _safe_float(getattr(self.config, "central_stage3_min_confidence", 0.0), 0.0)
            if float(confidence or 0.0) <= min_confidence:
                reason = f"stage3 confidence {float(confidence or 0.0):.4f} <= min {min_confidence:.4f}"
                try:
                    setattr(signal_result, "effective_strength", 0.0)
                    setattr(signal_result, "strength_guard", reason)
                except Exception:
                    pass
                return None, orig_strength, reason
            cap = _safe_float(getattr(self.config, "central_stage3_strength_cap", 3.0), 3.0)
            guard_name = "stage3_strength_cap"
        else:
            cap = _safe_float(getattr(self.config, "central_strength_cap", 4.0), 4.0)
            guard_name = "central_strength_cap"

        strength = min(orig_strength, cap) if cap > 0.0 else orig_strength
        try:
            setattr(signal_result, "effective_strength", float(strength))
            setattr(signal_result, "strength_guard", f"{guard_name}={cap:.4f}" if cap > 0.0 else "")
        except Exception:
            pass
        return strength, orig_strength, ""

    def _evaluate_entity_signal(self, entity: EntityRecord, price: float) -> SignalResult:
        # Evaluate the exact entity rulebook selected by central control.
        # Falling back to ticker-scoped parameters.json here can make selection
        # and execution disagree when one ticker has multiple Stage2 entities.
        return self._evaluate_stage3_entity_signal(entity, float(price))

    def _evaluate_stage3_entity_signal(self, entity: EntityRecord, price: float) -> SignalResult:
        ticker = normalize_ticker(entity.ticker)
        try:
            rb = Rulebook.from_dict(dict(getattr(entity, "rulebook", {}) or {}))
            rb.ticker = ticker
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL][STAGE3] %s rulebook 변환 실패 entity=%s: %s", ticker, entity.entity_id, exc)
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"stage3 rulebook invalid: {exc}")

        provider = getattr(self.runner, "rulebook", None)
        try:
            df = provider._get_ohlcv(ticker) if provider is not None and hasattr(provider, "_get_ohlcv") else None
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL][STAGE3] %s OHLCV 조회 실패 entity=%s: %s", ticker, entity.entity_id, exc)
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"stage3 OHLCV failed: {exc}")
        try:
            if df is not None and "ATR" in df.columns and len(df) > 0 and provider is not None:
                if hasattr(provider, "_last_atr"):
                    provider._last_atr[ticker] = float(df["ATR"].iloc[-1])
                if str((getattr(entity, "tags", {}) or {}).get("stage") or "") == "stage3_live_pool" and hasattr(provider, "_rulebook_by_ticker"):
                    provider._rulebook_by_ticker[ticker] = rb
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL][STAGE3] %s ATR/rulebook 캐시 실패 entity=%s: %s", ticker, entity.entity_id, exc)

        if df is None or len(df) < 60:
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason="Stage3 OHLCV 데이터 부족")

        try:
            ctx = get_market_context()
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL][STAGE3] MarketContext 로드 실패, 중립 사용: %s", exc)
            ctx = None
        if ctx is not None:
            market_score = float(getattr(ctx, "score", 50.0) or 50.0)
            sector_strength = getattr(ctx, "sector_strength", {}) or {}
            sector_score = float(sector_strength.get(str(getattr(rb, "sector_name", "") or ""), 50.0))
            vix_level = float(getattr(ctx, "vix_level", 18.0) or 18.0)
            context_timestamp = str(getattr(ctx, "timestamp", "") or "")
        else:
            market_score, sector_score, vix_level, context_timestamp = 50.0, 50.0, 18.0, ""
        try:
            if provider is not None and hasattr(provider, "_last_market_context"):
                provider._last_market_context[ticker] = {
                    "score": market_score,
                    "market_score": market_score,
                    "vix_level": vix_level,
                    "sector_score": sector_score,
                    "sector_strength": {str(getattr(rb, "sector_name", "") or ""): sector_score},
                    "timestamp": context_timestamp,
                }
        except Exception:
            pass

        news_normalized = 0.0
        topic_features = {}
        try:
            if provider is not None and hasattr(provider, "_signal_date") and hasattr(provider, "_lookup_lagged_news_context"):
                signal_date = provider._signal_date(df)
                news_normalized, topic_features, _news_note = provider._lookup_lagged_news_context(ticker, rb, signal_date)
                if not isinstance(topic_features, dict):
                    topic_features = {}
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL][STAGE3] %s 뉴스 context 실패 entity=%s: %s", ticker, entity.entity_id, exc)
            news_normalized, topic_features = 0.0, {}

        event_flags = live_event_flags(ctx)

        try:
            res = evaluate_signal(
                rb=rb,
                df=df,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=float(news_normalized or 0.0),
                event_flags=event_flags,
                topic_features=topic_features,
            )
        except Exception as exc:
            logger.error("[CENTRAL-CONTROL][STAGE3] %s evaluate_signal 실패 entity=%s: %s", ticker, entity.entity_id, exc)
            return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"stage3 evaluate 예외: {exc}")

        try:
            shadow_off = evaluate_signal(
                rb=rb,
                df=df,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=float(news_normalized or 0.0),
                event_flags=live_event_flags(ctx, enabled_override=False),
                topic_features=topic_features,
            )
            append_shadow_direct_event_log(
                candidate_id=str(entity.entity_id),
                mode="central",
                path="engine.live.central_control._evaluate_stage3_entity_signal",
                market_score_on=market_score,
                market_score_off=market_score,
                result_on=res,
                result_off=shadow_off,
            )
        except Exception as shadow_exc:
            logger.warning("[CENTRAL-CONTROL][STAGE3] %s direct Event shadow compare skipped entity=%s: %s", ticker, entity.entity_id, shadow_exc)

        reason = (
            f"[stage3 {rb.direction}] score={res.score:.2f}/threshold={res.threshold:.2f} "
            f"raw={res.raw_score:.2f} mkt_adj×{res.market_adjustment:.2f} entity={entity.entity_id} "
            f"reasons=[{', '.join(list(res.reasons or [])[:4])}]"
        )
        kwargs = {
            "score": float(res.score),
            "raw_score": float(res.raw_score),
            "threshold": float(res.threshold),
            "market_adjustment": float(res.market_adjustment),
            "reasons": list(res.reasons or []),
        }
        if res.should_buy:
            return SignalResult(ticker=ticker, signal=Signal.BUY, price=price, reason=reason, **kwargs)
        return SignalResult(ticker=ticker, signal=Signal.HOLD, price=price, reason=f"미달 {reason}", **kwargs)

    def _same_day_reentry_blocked_tickers(self, trade_date: str) -> set[str]:
        """Tickers that must not be re-entered on the same US trading day.

        This deliberately filters before decision creation and does not loosen the
        candidate terminal preservation logic. It covers both BUY candidate terminal
        state and SELL exits recorded in trade_log.csv.
        """
        out = set()
        out.update(self._same_day_terminal_candidate_tickers(trade_date))
        out.update(self._same_day_exited_tickers(trade_date))
        return {ticker for ticker in out if ticker}

    def _same_day_terminal_candidate_tickers(self, trade_date: str) -> set[str]:
        state = load_candidate_state(getattr(self.config, "candidate_state_path", CENTRAL_BUY_CANDIDATES_PATH))
        if str(state.get("trade_date") or "") != str(trade_date or ""):
            return set()
        tickers: set[str] = set()
        for row in (state.get("candidates") or {}).values():
            if not isinstance(row, dict):
                continue
            if str(row.get("status") or "") not in TERMINAL_CANDIDATE_STATUSES:
                continue
            ticker = normalize_ticker(row.get("ticker", ""))
            if ticker:
                tickers.add(ticker)
        return tickers

    def _same_day_exited_tickers(self, trade_date: str) -> set[str]:
        path = Path(TRADE_LOG_PATH)
        if not path.exists():
            return set()
        tickers: set[str] = set()
        try:
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ticker = normalize_ticker(row.get("ticker", ""))
                    if not ticker:
                        continue
                    exited_at = self._trade_log_exit_trade_date_et(row.get("exited_at", ""))
                    if exited_at == trade_date:
                        tickers.add(ticker)
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL] trade_log same-day exit filter read failed: %s", exc)
        return tickers

    def _trade_log_exit_trade_date_et(self, exited_at: str) -> str:
        raw = str(exited_at or "").strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            return ""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(ET).date().isoformat()

    def _process_semi_auto_decisions(self, decisions: list[BuyDecision], signal_by_entity: dict, price_by_entity: dict[str, float]) -> None:
        trade_date = self._trade_date_et()
        candidate_rows = []
        decision_by_candidate_id = {}
        signal_by_candidate_id = {}
        price_by_candidate_id = {}
        for decision in decisions:
            price = float(price_by_entity.get(decision.entity_id, 0.0) or 0.0)
            sig = signal_by_entity.get(decision.entity_id)
            row = candidate_from_decision(decision, sig, price, trade_date=trade_date)
            cid = row["candidate_id"]
            candidate_rows.append(row)
            decision_by_candidate_id[cid] = decision
            signal_by_candidate_id[cid] = sig
            price_by_candidate_id[cid] = price
        publish_candidate_state(
            candidate_rows,
            path=self.config.candidate_state_path,
            buy_mode=getattr(self, "buy_mode", "auto"),
            trade_date=trade_date,
        )
        executed_this_tick: set[str] = set()
        pending_intents = load_pending_manual_intents(intent_path=self.config.manual_intent_path, trade_date=trade_date)
        for intent in pending_intents:
            cid = str(intent.get("candidate_id") or "")
            intent_id = str(intent.get("intent_id") or "")
            if not cid or not intent_id:
                continue
            if cid not in decision_by_candidate_id:
                mark_intent_status(intent_id, "rejected", intent_path=self.config.manual_intent_path, note="candidate not current")
                mark_candidate_status(cid, "expired", candidate_path=self.config.candidate_state_path, manual_intent_id=intent_id, note="candidate not current")
                logger.warning("[CENTRAL-CONTROL][SEMI-AUTO] manual intent rejected stale candidate=%s", cid)
                continue
            if cid in executed_this_tick or self._candidate_terminal(cid):
                mark_intent_status(intent_id, "rejected", intent_path=self.config.manual_intent_path, note="candidate already executed or terminal")
                logger.warning("[CENTRAL-CONTROL][SEMI-AUTO] duplicate manual intent rejected candidate=%s", cid)
                continue
            ok = self._execute_decision(
                decision_by_candidate_id[cid],
                signal_by_candidate_id.get(cid),
                price_by_candidate_id.get(cid, 0.0),
                execution_reason="manual_timing",
                manual_intent_id=intent_id,
            )
            if ok:
                executed_this_tick.add(cid)
                mark_intent_status(intent_id, "consumed", intent_path=self.config.manual_intent_path)
                mark_candidate_status(cid, "manual_executed", candidate_path=self.config.candidate_state_path, manual_intent_id=intent_id)
                logger.warning("[CENTRAL-CONTROL][SEMI-AUTO] manual_timing executed candidate=%s", cid)
            else:
                mark_intent_status(intent_id, "blocked", intent_path=self.config.manual_intent_path, note="runner blocked or did not attempt order")
                mark_candidate_status(cid, "blocked", candidate_path=self.config.candidate_state_path, manual_intent_id=intent_id, note="runner blocked or did not attempt order")
                logger.warning("[CENTRAL-CONTROL][SEMI-AUTO] manual_timing blocked candidate=%s", cid)
        if not self._auto_fallback_due():
            logger.info(
                "[CENTRAL-CONTROL][SEMI-AUTO] waiting for manual timing: candidates=%s intents=%s fallback_due=False",
                len(candidate_rows),
                len(pending_intents),
            )
            return
        for cid, decision in decision_by_candidate_id.items():
            if cid in executed_this_tick or self._candidate_terminal(cid):
                continue
            ok = self._execute_decision(
                decision,
                signal_by_candidate_id.get(cid),
                price_by_candidate_id.get(cid, 0.0),
                execution_reason="auto_fallback",
            )
            if ok:
                executed_this_tick.add(cid)
                mark_candidate_status(cid, "auto_executed", candidate_path=self.config.candidate_state_path)
                logger.warning("[CENTRAL-CONTROL][SEMI-AUTO] auto_fallback executed candidate=%s", cid)
            else:
                mark_candidate_status(cid, "blocked", candidate_path=self.config.candidate_state_path, note="runner blocked or did not attempt order")
                logger.warning("[CENTRAL-CONTROL][SEMI-AUTO] auto_fallback blocked candidate=%s", cid)

    def _candidate_terminal(self, candidate_id: str) -> bool:
        state = load_candidate_state(getattr(self.config, "candidate_state_path", CENTRAL_BUY_CANDIDATES_PATH))
        row = (state.get("candidates") or {}).get(candidate_id)
        if not isinstance(row, dict):
            return False
        return str(row.get("status") or "") in TERMINAL_CANDIDATE_STATUSES

    def _trade_date_et(self) -> str:
        return trade_date_et(self._now_et())

    def _now_et(self) -> datetime:
        try:
            trading = getattr(getattr(self.runner, "broker", None), "trading", None)
            if trading is not None and hasattr(trading, "get_clock"):
                clock = trading.get_clock()
                ts = getattr(clock, "timestamp", None)
                if ts is not None:
                    return ts.astimezone(ET) if hasattr(ts, "astimezone") else datetime.now(ET)
        except Exception as exc:
            logger.warning("[CENTRAL-CONTROL] clock 조회 실패 — local ET fallback 사용: %s", exc)
        return datetime.now(ET)

    def _auto_fallback_due(self) -> bool:
        now = self._now_et()
        cutoff = dt_time(
            hour=max(0, min(23, int(self.config.auto_fallback_hour_et or 0))),
            minute=max(0, min(59, int(self.config.auto_fallback_minute_et or 0))),
        )
        return now.time() >= cutoff

    def _state_unavailable_for_new_buys(self) -> bool:
        pm = getattr(self.runner, "position_manager", None)
        if pm is None:
            return True
        if str(getattr(pm, "load_error", "") or getattr(pm, "_load_error", "") or ""):
            return True
        pending_mgr = getattr(self.runner, "pending_order_manager", None)
        if pending_mgr is not None and str(getattr(pending_mgr, "load_error", "") or ""):
            return True
        return False

    def _build_live_ledger_view(self) -> _LiveLedgerView:
        positions: list[_LiveOpenPosition] = []
        seen_tickers: set[str] = set()
        for pos in self.runner.position_manager.all():
            live_pos = self._position_entry_to_live_open(pos, source="position")
            if live_pos is None:
                continue
            positions.append(live_pos)
            seen_tickers.add(live_pos.ticker)

        pending_mgr = getattr(self.runner, "pending_order_manager", None)
        if pending_mgr is not None:
            for record in pending_mgr.all():
                ticker = normalize_ticker(getattr(record, "ticker", ""))
                if not ticker or ticker in seen_tickers:
                    continue
                if str(getattr(record, "side", "") or "").lower() != "buy":
                    continue
                if str(getattr(record, "state", "") or "").upper() == "DONE":
                    continue
                shares = _safe_float(getattr(record, "filled_shares", 0.0), 0.0) or _safe_float(getattr(record, "requested_shares", 0.0), 0.0) or 1.0
                price = _safe_float(getattr(record, "filled_avg_price", 0.0), 0.0) or self._safe_current_price(ticker)
                positions.append(
                    _LiveOpenPosition(
                        entity_id=f"{ticker}__pending_buy",
                        ticker=ticker,
                        open_shares=max(float(shares), 1e-6),
                        avg_entry_price=max(float(price), 0.0),
                        current_price=max(float(price), 0.0),
                        position_id=str(getattr(record, "order_id", "") or f"pending-{ticker}"),
                        source="pending_buy",
                    )
                )
                seen_tickers.add(ticker)

        try:
            holdings = self.runner.broker.get_holdings()
        except Exception as exc:
            logger.error("[CENTRAL-CONTROL] holdings 조회 실패 → 신규 BUY fail-closed: %s", exc)
            raise
        for holding in holdings or []:
            ticker = normalize_ticker(getattr(holding, "ticker", ""))
            if not ticker or ticker in seen_tickers:
                continue
            shares = _safe_float(getattr(holding, "shares", 0.0), 0.0)
            if shares <= 0.0:
                continue
            current_price = _safe_float(getattr(holding, "current_price", 0.0), 0.0) or self._safe_current_price(ticker)
            avg_cost = _safe_float(getattr(holding, "avg_cost", 0.0), 0.0) or current_price
            positions.append(
                _LiveOpenPosition(
                    entity_id=f"{ticker}__orphan_holding",
                    ticker=ticker,
                    open_shares=shares,
                    avg_entry_price=avg_cost,
                    current_price=current_price,
                    position_id=f"orphan-{ticker}",
                    source="orphan_holding",
                )
            )
            seen_tickers.add(ticker)
        return _LiveLedgerView(positions)

    def _position_entry_to_live_open(self, pos, *, source: str) -> Optional[_LiveOpenPosition]:
        ticker = normalize_ticker(getattr(pos, "ticker", ""))
        if not ticker:
            return None
        shares = _safe_float(getattr(pos, "shares", 0.0), 0.0)
        if shares <= 0.0:
            return None
        entry_price = _safe_float(getattr(pos, "entry_price", 0.0), 0.0)
        current_price = self._safe_current_price(ticker) or entry_price
        member_hash = str(getattr(pos, "member_hash", "") or "").strip()
        entity_id = f"{ticker}_{member_hash[:12]}" if member_hash else f"{ticker}__live_position"
        return _LiveOpenPosition(
            entity_id=entity_id,
            ticker=ticker,
            open_shares=shares,
            avg_entry_price=entry_price,
            current_price=current_price,
            position_id=entity_id,
            add_buy_count=int(getattr(pos, "add_buy_count", 0) or 0),
            source=source,
        )

    def _safe_current_price(self, ticker: str) -> float:
        try:
            price = self.runner.broker.get_current_price(ticker)
            return _safe_float(price, 0.0)
        except Exception:
            return 0.0

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
            order_notional_safety_buffer=_normalize_order_notional_safety_buffer(
                getattr(self, "order_notional_safety_buffer", getattr(self.config, "order_notional_safety_buffer", 0.0))
            ),
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

    def _execute_decision(self, decision: BuyDecision, signal_result, price: float, *, execution_reason: str = "auto", manual_intent_id: str = "") -> bool:
        if float(decision.notional or 0.0) <= 0.0 or float(price or 0.0) <= 0.0:
            logger.info("[CENTRAL-CONTROL] %s 주문 스킵: invalid notional/price", decision.ticker)
            return False
        original_notional = getattr(self.runner, "order_notional", None)
        before_blocked = int(getattr(getattr(self.runner, "stats", None), "orders_blocked", 0) or 0)
        before_attempted = int(getattr(getattr(self.runner, "stats", None), "orders_attempted", 0) or 0)
        try:
            self.runner.order_notional = float(decision.notional)
            reason = (
                f"central_control {execution_reason} metric={self.selection_metric} confidence_mode={self.confidence_mode} "
                f"entity={decision.entity_id} score={decision.score:.4f} "
                f"conf={decision.confidence:.4f} strength={decision.strength:.4f}"
            )
            if manual_intent_id:
                reason += f" manual_intent={manual_intent_id}"
            try:
                self.runner._try_order(
                    "BUY",
                    decision.ticker,
                    float(price),
                    reason,
                    signal_result=signal_result,
                    rulebook_override=getattr(decision, "rulebook", None),
                )
            except TypeError as exc:
                if "rulebook_override" not in str(exc):
                    raise
                self.runner._try_order("BUY", decision.ticker, float(price), reason, signal_result=signal_result)
        finally:
            self.runner.order_notional = original_notional
        after_blocked = int(getattr(getattr(self.runner, "stats", None), "orders_blocked", 0) or 0)
        after_attempted = int(getattr(getattr(self.runner, "stats", None), "orders_attempted", 0) or 0)
        if after_blocked > before_blocked:
            return False
        if after_attempted > before_attempted:
            return True
        return True


def _entity_stage(entity: EntityRecord) -> str:
    return str((getattr(entity, "tags", {}) or {}).get("stage") or "stage2")


def _dedupe_entities_by_id(entities: list[EntityRecord]) -> list[EntityRecord]:
    out: list[EntityRecord] = []
    seen: set[str] = set()
    for entity in entities:
        entity_id = str(getattr(entity, "entity_id", "") or "")
        if not entity_id or entity_id in seen:
            continue
        out.append(entity)
        seen.add(entity_id)
    return out


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


def _normalize_buy_mode(value: str) -> str:
    mode = str(value or "auto").strip().lower().replace("-", "_")
    if mode not in {"auto", "semi_auto"}:
        raise ValueError(f"unsupported central buy mode: {value}")
    return mode


def _normalize_order_notional_safety_buffer(value) -> float:
    try:
        out = float(value if value is not None else DEFAULT_ORDER_NOTIONAL_SAFETY_BUFFER)
    except Exception:
        out = DEFAULT_ORDER_NOTIONAL_SAFETY_BUFFER
    return max(0.0, min(out, MAX_ORDER_NOTIONAL_SAFETY_BUFFER))


def order_notional_safety_buffer_from_policy(policy: dict) -> float:
    sa = (policy or {}).get("small_amount_safety", {}) or {}
    raw = sa.get("order_notional_safety_buffer", DEFAULT_ORDER_NOTIONAL_SAFETY_BUFFER)
    return _normalize_order_notional_safety_buffer(raw)


def _adjusted_confidence_from_metrics(validation_metrics, *, pf_cap: float, min_trades: int) -> float:
    cap = max(float(pf_cap), 1.0)
    floor = max(int(min_trades), 0)
    metrics: list[dict] = []
    if isinstance(validation_metrics, dict):
        for label in PERIOD_ORDER:
            metric = validation_metrics.get(label)
            if isinstance(metric, dict):
                metrics.append(metric)
        # Stage3 live-pool rows use labels such as train_1/train_2/recent_1y.
        # If none of the legacy Stage2 labels are present, score the available
        # validation periods instead of neutralizing the whole Stage3 pool.
        if not metrics:
            metrics = [dict(metric) for metric in validation_metrics.values() if isinstance(metric, dict)]
    if not metrics:
        return 0.0
    adjusted: list[float] = []
    for metric in metrics:
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
