"""S2 automated live trading controller.

This module is fail-closed by default. With the default config
``master_enabled=false``, ``real_orders_enabled=false`` and ``dry_run=true``, it
can only read account/candidate state and produce dry-run plans.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from engine.live.broker.base import OrderType
from engine.live.buy_reconciliation import BuyPreflight, BuyReconciliationService
from engine.live.position_manager import PositionManager
from engine.live.s2_auto_config import (
    CONFIG_PATH,
    EVENTS_PATH,
    ORDER_INTENTS_PATH,
    STATE_PATH,
    bool_enabled,
    direct_order_env_enabled,
    kill_switch_active,
    load_live_auto_config,
    operator_arm_valid,
    portfolio_k,
    real_order_gate,
    utc_now_iso,
)
from engine.strategies.rulebook import Rulebook

LIVE_SLOTS_STATE_PATH = Path("data/_system/live_slots_state.json")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return _json_safe(value.__dict__)
    return str(value)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{int(time.time()*1000)}")
    tmp.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


@dataclass
class AutoOrderPlan:
    ok: bool
    status: str
    reason: str
    candidate_id: str = ""
    ticker: str = ""
    entry_timing: str = "next_open"
    execution_session: str = ""
    price: float = 0.0
    total_capital_usd: float = 0.0
    portfolio_k: int = 20
    position_notional_usd: float = 0.0
    shares: float = 0.0
    dry_run: bool = True
    real_orders_enabled: bool = False
    direct_order_env_enabled: bool = False
    would_submit_order: bool = False
    orders_submitted: int = 0
    details: dict[str, Any] | None = None


class _StaticRulebookProvider:
    def __init__(self, ticker: str, rulebook: Rulebook, atr: float, market_context: dict[str, Any] | None = None):
        self.ticker = ticker
        self.rulebook = rulebook
        self.atr = float(atr or 0.0)
        self.market_context = market_context or {}

    def get_rulebook(self, ticker: str):
        return self.rulebook if str(ticker).upper() == self.ticker.upper() else None

    def get_last_atr(self, ticker: str):
        return self.atr if str(ticker).upper() == self.ticker.upper() else None

    def get_last_market_context(self, ticker: str):
        return self.market_context if str(ticker).upper() == self.ticker.upper() else None


class S2AutoTrader:
    def __init__(self, config_path: Path | str = CONFIG_PATH):
        self.config_path = Path(config_path)
        self.config = load_live_auto_config(self.config_path)
        paths = self.config.get("state_paths") if isinstance(self.config.get("state_paths"), Mapping) else {}
        self.state_path = Path(str(paths.get("state") or STATE_PATH))
        self.events_path = Path(str(paths.get("events") or EVENTS_PATH))
        self.order_intents_path = Path(str(paths.get("order_intents") or ORDER_INTENTS_PATH))
        self.state = self.load_state()
        self.position_manager = PositionManager()

    def reload(self) -> None:
        self.config = load_live_auto_config(self.config_path)
        self.state = self.load_state()

    def load_state(self) -> dict[str, Any]:
        data = _load_json(self.state_path, {})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("schema_version", 1)
        data.setdefault("created_at", utc_now_iso())
        data.setdefault("updated_at", utc_now_iso())
        data.setdefault("session", {})
        data.setdefault("orders_today", 0)
        data.setdefault("events_count", 0)
        data.setdefault("last_plan", {})
        data.setdefault("last_reconcile", {})
        data.setdefault("dry_run_order_count", 0)
        data.setdefault("real_order_count", 0)
        return data

    def save_state(self) -> None:
        self.state["updated_at"] = utc_now_iso()
        _atomic_write_json(self.state_path, self.state)

    def event(self, event: str, message: str, **payload: Any) -> None:
        row = {"time": utc_now_iso(), "event": event, "message": message, **payload}
        _append_jsonl(self.events_path, row)
        self.state["events_count"] = int(self.state.get("events_count") or 0) + 1

    def status(self) -> dict[str, Any]:
        killed, kill_reason = kill_switch_active(self.config)
        return {
            "ok": True,
            "config_path": str(self.config_path),
            "state_path": str(self.state_path),
            "master_enabled": bool_enabled(self.config.get("master_enabled")),
            "auto_buy_enabled": bool_enabled(self.config.get("auto_buy_enabled")),
            "auto_exit_enabled": bool_enabled(self.config.get("auto_exit_enabled")),
            "real_orders_enabled": bool_enabled(self.config.get("real_orders_enabled")),
            "dry_run": bool_enabled(self.config.get("dry_run", True)),
            "entry_timing": self.config.get("entry_timing"),
            "portfolio_K": portfolio_k(self.config),
            "s2_take_profit_enabled": bool_enabled((self.config.get("exit") or {}).get("s2_take_profit_enabled", False)),
            "direct_order_env_enabled": direct_order_env_enabled(),
            "kill_switch_active": killed,
            "kill_switch_reason": kill_reason,
            "state": self.state,
        }

    def _broker(self):
        # Read/real broker construction is intentionally delegated to the real dashboard helper.
        from engine.live.real_dashboard_api import _get_real_broker

        return _get_real_broker()

    def available_cash_from_account(self, *, simulate_failure: bool = False) -> tuple[bool, float, str, dict[str, Any]]:
        if simulate_failure:
            return False, 0.0, "simulated_balance_failure", {}
        try:
            broker = self._broker()
            bal = broker.get_balance()
            cash = _safe_float(getattr(bal, "cash_usd", getattr(bal, "cash_krw", 0.0)), 0.0)
            total = _safe_float(getattr(bal, "total_value_usd", getattr(bal, "total_value_krw", cash)), cash)
            details = {
                "broker_mode": str(getattr(broker, "mode", "")),
                "cash_usd": cash,
                "total_value_usd": total,
                "holdings_count": len(getattr(bal, "holdings", []) or []),
            }
            if cash <= 0:
                return False, 0.0, "available_cash_non_positive", details
            return True, cash, "account_available_cash", details
        except Exception as exc:
            return False, 0.0, f"account_cash_read_failed:{exc}", {}

    def session_id(self) -> str:
        # Fixed-at-start capital is scoped to the UTC date for deterministic recovery.
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def ensure_session_capital(self, *, simulate_failure: bool = False, force_refresh: bool = False) -> tuple[bool, float, str, dict[str, Any]]:
        sid = self.session_id()
        session = self.state.setdefault("session", {})
        existing = _safe_float(session.get("total_capital_usd"), 0.0)
        if not force_refresh and session.get("session_id") == sid and existing > 0:
            return True, existing, "session_capital_reused", dict(session)
        ok, cash, reason, details = self.available_cash_from_account(simulate_failure=simulate_failure)
        if not ok:
            self.event("CAPITAL_READ_BLOCKED", "available cash read failed; buy generation blocked", reason=reason, details=details)
            self.save_state()
            return False, 0.0, reason, details
        k = portfolio_k(self.config)
        session = {
            "session_id": sid,
            "started_at": utc_now_iso(),
            "total_capital_mode": self.config.get("total_capital_mode", "fixed_from_account_at_start"),
            "capital_source": self.config.get("capital_source", "available_cash"),
            "total_capital_usd": round(float(cash), 6),
            "portfolio_K": k,
            "position_notional_usd": round(float(cash) / k, 6),
            "account_details": details,
        }
        self.state["session"] = session
        self.event("SESSION_CAPITAL_SET", "session capital fixed from available cash", session=session)
        self.save_state()
        return True, float(cash), "session_capital_set", session

    def load_live_slots(self) -> dict[str, Any]:
        data = _load_json(LIVE_SLOTS_STATE_PATH, {})
        return data if isinstance(data, dict) else {}

    def active_held_ids(self, live_state: Mapping[str, Any]) -> set[str]:
        held = live_state.get("held_exclusions") if isinstance(live_state.get("held_exclusions"), Mapping) else {}
        out = set()
        for cid, row in held.items():
            if str((row or {}).get("status") or "open").lower() in {"open", "held", "active"}:
                out.add(str(cid))
        return out

    def reconcile_snapshot(self) -> dict[str, Any]:
        live_state = self.load_live_slots()
        held_ids = self.active_held_ids(live_state)
        positions = getattr(self.position_manager, "_positions", {}) or {}
        broker_holdings = []
        broker_error = ""
        try:
            broker = self._broker()
            broker_holdings = [getattr(h, "ticker", "") for h in broker.get_holdings()]
        except Exception as exc:
            broker_error = str(exc)
        snap = {
            "time": utc_now_iso(),
            "held_exclusion_count": len(held_ids),
            "held_exclusion_ids": sorted(held_ids)[:50],
            "position_manager_count": len(positions),
            "position_manager_tickers": sorted(map(str, positions.keys()))[:50],
            "broker_holdings_count": len([x for x in broker_holdings if x]),
            "broker_holding_tickers": sorted(str(x).upper() for x in broker_holdings if x)[:50],
            "broker_error": broker_error,
        }
        self.state["last_reconcile"] = snap
        return snap

    def candidate_pool(self) -> list[dict[str, Any]]:
        live_state = self.load_live_slots()
        pool = live_state.get("candidate_pool") or []
        if not isinstance(pool, list):
            return []
        held_ids = self.active_held_ids(live_state)
        rows = [r for r in pool if isinstance(r, Mapping) and r.get("candidate_id") and str(r.get("candidate_id")) not in held_ids]
        rows.sort(key=lambda r: (int(r.get("priority_group") or 0), -_safe_float(r.get("final_score"), 0.0), str(r.get("ticker") or ""), str(r.get("candidate_id") or "")))
        return [dict(r) for r in rows]

    def _candidate_full_payload(self, candidate_id: str) -> dict[str, Any] | None:
        try:
            from engine.live.elite_shadow_report import build_elite_shadow_report

            report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
            for cand in report.get("candidates") or []:
                cid = str(cand.get("candidate_id") or f"{cand.get('stage')}:{cand.get('ticker')}:{cand.get('rulebook_hash_short')}")
                if cid == str(candidate_id):
                    return dict(cand)
        except Exception:
            return None
        return None

    def _validate_candidate_signal(self, row: Mapping[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        candidate_id = str(row.get("candidate_id") or "")
        full = self._candidate_full_payload(candidate_id)
        if not full:
            return False, "candidate_not_found_in_current_report", {}
        try:
            from engine.live.elite_shadow_trader import evaluate_candidate
            from engine.market.context import get_market_context

            ev = evaluate_candidate(full, ctx=get_market_context())
        except Exception as exc:
            return False, f"evaluate_candidate_failed:{exc}", {"candidate": full}
        if not bool(ev.get("ok")):
            return False, str(ev.get("reason") or "evaluate_not_ok"), {"candidate": full, "evaluation": ev}
        if not bool(ev.get("should_buy")):
            return False, "should_buy_false_at_execution_check", {"candidate": full, "evaluation": ev}
        return True, "validated", {"candidate": full, "evaluation": ev}

    def _execution_session(self) -> str:
        # Conservative placeholder: the actual next-open queue can replace this with exchange calendar output.
        return (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    def _active_capacity(self) -> tuple[int, int, int]:
        snap = self.reconcile_snapshot()
        active_positions = max(int(snap.get("broker_holdings_count") or 0), int(snap.get("position_manager_count") or 0))
        pending_buys = 0
        remaining = max(0, portfolio_k(self.config) - active_positions - pending_buys)
        return active_positions, pending_buys, remaining

    def compute_order_plan(
        self,
        *,
        ignore_switches: bool = False,
        simulate_balance_failure: bool = False,
        force_capital_refresh: bool = False,
        candidate_id: str = "",
    ) -> AutoOrderPlan:
        dry_run = bool_enabled(self.config.get("dry_run", True))
        killed, kill_reason = kill_switch_active(self.config)
        if killed and not ignore_switches:
            return AutoOrderPlan(False, "BLOCKED", kill_reason, dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled())
        if not bool_enabled(self.config.get("auto_buy_enabled")) and not ignore_switches:
            return AutoOrderPlan(False, "BLOCKED", "auto_buy_disabled", dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled())
        if str(self.config.get("entry_timing") or "next_open") != "next_open":
            return AutoOrderPlan(False, "BLOCKED", "entry_timing_not_next_open", dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled())
        ok, capital, cap_reason, cap_details = self.ensure_session_capital(simulate_failure=simulate_balance_failure, force_refresh=force_capital_refresh)
        if not ok:
            return AutoOrderPlan(False, "BLOCKED", cap_reason, dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled(), details={"capital": cap_details})
        pool = self.candidate_pool()
        if candidate_id:
            pool = [r for r in pool if str(r.get("candidate_id")) == str(candidate_id)]
        if not pool:
            return AutoOrderPlan(False, "BLOCKED", "candidate_pool_empty", total_capital_usd=capital, portfolio_k=portfolio_k(self.config), dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled())
        row = pool[0]
        valid, reason, payload = self._validate_candidate_signal(row)
        if not valid:
            return AutoOrderPlan(False, "BLOCKED", reason, candidate_id=str(row.get("candidate_id")), ticker=str(row.get("ticker") or ""), total_capital_usd=capital, portfolio_k=portfolio_k(self.config), dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled(), details=payload)
        price = _safe_float((payload.get("evaluation") or {}).get("price"), _safe_float(row.get("price"), 0.0))
        if price <= 0:
            return AutoOrderPlan(False, "BLOCKED", "price_unavailable", candidate_id=str(row.get("candidate_id")), ticker=str(row.get("ticker") or ""), total_capital_usd=capital, portfolio_k=portfolio_k(self.config), dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled())
        k = portfolio_k(self.config)
        active_positions, pending_buys, remaining = self._active_capacity()
        base_notional = capital / k
        risk = self.config.get("risk_limits") if isinstance(self.config.get("risk_limits"), Mapping) else {}
        cash_buffer = max(_safe_float(risk.get("min_cash_buffer_usd"), 0.0), _safe_float((self.config.get("capital") or {}).get("cash_buffer_usd"), 0.0))
        max_order = _safe_float(risk.get("max_order_notional_usd"), base_notional)
        available_budget = max(0.0, capital - cash_buffer)
        if remaining > 0:
            order_notional = min(base_notional, available_budget / max(remaining, 1), max_order)
        else:
            order_notional = 0.0
        shares = round(order_notional / price, 6) if price > 0 and order_notional > 0 else 0.0
        if shares <= 0 or order_notional <= 0:
            return AutoOrderPlan(False, "BLOCKED", "no_remaining_capacity_or_budget", candidate_id=str(row.get("candidate_id")), ticker=str(row.get("ticker") or ""), price=price, total_capital_usd=capital, portfolio_k=k, position_notional_usd=order_notional, shares=shares, dry_run=dry_run, real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")), direct_order_env_enabled=direct_order_env_enabled(), details={"active_positions": active_positions, "pending_buys": pending_buys, "remaining_capacity": remaining})
        would_submit = bool_enabled(self.config.get("real_orders_enabled")) and not dry_run
        plan = AutoOrderPlan(
            ok=True,
            status="PLANNED_DRY_RUN" if dry_run or not would_submit else "READY_TO_SUBMIT",
            reason="next_open_order_plan_created",
            candidate_id=str(row.get("candidate_id")),
            ticker=str(row.get("ticker") or ""),
            entry_timing="next_open",
            execution_session=self._execution_session(),
            price=price,
            total_capital_usd=round(capital, 6),
            portfolio_k=k,
            position_notional_usd=round(order_notional, 6),
            shares=shares,
            dry_run=dry_run,
            real_orders_enabled=bool_enabled(self.config.get("real_orders_enabled")),
            direct_order_env_enabled=direct_order_env_enabled(),
            would_submit_order=would_submit,
            orders_submitted=0,
            details={
                "active_positions": active_positions,
                "pending_buys": pending_buys,
                "remaining_capacity": remaining,
                "base_notional_usd": round(base_notional, 6),
                "cash_buffer_usd": cash_buffer,
                "max_order_notional_usd": max_order,
                "candidate": row,
                "evaluation_summary": {k: (payload.get("evaluation") or {}).get(k) for k in ["score", "threshold", "ratio", "price", "atr", "should_buy"]},
            },
        )
        self.state["last_plan"] = asdict(plan)
        self.save_state()
        return plan

    def submit_plan(self, plan: AutoOrderPlan, *, confirmation_phrase: str = "") -> dict[str, Any]:
        if not plan.ok:
            return {"ok": False, "reason": plan.reason, "orders_submitted": 0}
        gate_ok, gate_reasons = real_order_gate(self.config, confirmation_phrase=confirmation_phrase)
        if not gate_ok:
            return {"ok": False, "reason": "real_order_gate_blocked", "gate_reasons": gate_reasons, "orders_submitted": 0}
        if str(plan.entry_timing) != "next_open":
            return {"ok": False, "reason": "only_next_open_supported", "orders_submitted": 0}
        if plan.shares <= 0 or plan.price <= 0:
            return {"ok": False, "reason": "invalid_plan_size", "orders_submitted": 0}
        payload = plan.details or {}
        cand = (payload.get("candidate") or {}) if isinstance(payload, Mapping) else {}
        full = self._candidate_full_payload(plan.candidate_id)
        if not full or not isinstance(full.get("rulebook"), Mapping):
            return {"ok": False, "reason": "full_rulebook_unavailable", "orders_submitted": 0}
        rb = Rulebook.from_dict(dict(full["rulebook"]))
        atr = _safe_float((payload.get("evaluation_summary") or {}).get("atr"), _safe_float(cand.get("atr"), 0.0))
        broker = self._broker()
        from engine.live.safety.layer import SafetyLayer

        safety = SafetyLayer(broker=broker)
        decision = safety.check_order("BUY", plan.ticker, plan.shares, plan.price, purpose="entry")
        if not decision.allowed:
            return {"ok": False, "reason": "safety_blocked", "safety_code": decision.code, "safety_reason": decision.reason, "orders_submitted": 0}
        intent = {
            "time": utc_now_iso(),
            "candidate_id": plan.candidate_id,
            "ticker": plan.ticker,
            "shares": plan.shares,
            "price": plan.price,
            "notional": plan.position_notional_usd,
            "entry_timing": plan.entry_timing,
            "execution_session": plan.execution_session,
            "selected_rulebook": dict(full["rulebook"]),
            "preflight_atr": atr,
            "dry_run": False,
        }
        _append_jsonl(self.order_intents_path, intent)
        order = broker.place_buy(plan.ticker, plan.shares, order_type=OrderType.MARKET, price=0.0, client_order_id=f"km-s2-auto-{int(time.time())}-{plan.ticker}")
        safety.record_order(order, "BUY", purpose="entry")
        if _safe_float(getattr(order, "filled_shares", 0.0), 0.0) > 0 and _safe_float(getattr(order, "filled_avg_price", 0.0), 0.0) > 0:
            provider = _StaticRulebookProvider(plan.ticker, rb, atr, {})
            reconciler = BuyReconciliationService(
                broker=broker,
                rulebook_provider=provider,
                position_manager=self.position_manager,
                pending_manager=None,
                notifier=None,
            )
            reconciler.reconcile(order, purpose="entry", preflight=BuyPreflight(atr=atr, rulebook=rb, entry_market_context={}))
        self.state["real_order_count"] = int(self.state.get("real_order_count") or 0) + 1
        self.state["orders_today"] = int(self.state.get("orders_today") or 0) + 1
        self.event("REAL_BUY_SUBMITTED", "S2 auto live BUY submitted", candidate_id=plan.candidate_id, ticker=plan.ticker, shares=plan.shares, order=_json_safe(order))
        self.save_state()
        return {"ok": True, "orders_submitted": 1, "order": _json_safe(order)}

    def tick(self, *, ignore_switches_for_dry_run: bool = False, simulate_balance_failure: bool = False) -> dict[str, Any]:
        plan = self.compute_order_plan(ignore_switches=ignore_switches_for_dry_run, simulate_balance_failure=simulate_balance_failure)
        result = {"plan": asdict(plan), "orders_submitted": 0, "real_order_attempted": False}
        if plan.ok and plan.would_submit_order:
            submit = self.submit_plan(plan)
            result["submit"] = submit
            result["orders_submitted"] = int(submit.get("orders_submitted") or 0)
            result["real_order_attempted"] = True
        else:
            self.state["dry_run_order_count"] = int(self.state.get("dry_run_order_count") or 0) + (1 if plan.ok else 0)
            self.event("DRY_RUN_PLAN", "dry-run plan generated; no live order submitted", plan=asdict(plan))
            self.save_state()
        return result

    def exit_trigger_selftest(self) -> dict[str, Any]:
        from engine.core.exit_policy import ExitExecutionConfig, MarketContext, PositionState, PriceSnapshot, evaluate_exit

        rb = {"ticker": "TEST", "direction": "long", "exit_strategy": "hybrid", "max_holding_days": 10}
        pos = PositionState(
            ticker="TEST",
            direction="long",
            entry_date="2026-01-01",
            entry_price=100.0,
            avg_cost=100.0,
            shares=1.0,
            atr_at_entry=2.0,
            stop_price=95.0,
            target_price=103.0,
            trailing_stop=98.0,
            trailing_distance=2.0,
            highest_price=101.0,
            max_holding_days=10,
            exit_strategy="hybrid",
            holding_trading_days=3,
        )
        no_tp = evaluate_exit(pos, PriceSnapshot(high=104.0, low=100.0, close=104.0), rb, market_context=MarketContext(holding_trading_days=3), execution_config=ExitExecutionConfig(take_profit_enabled=False))
        yes_tp = evaluate_exit(pos, PriceSnapshot(high=104.0, low=100.0, close=104.0), rb, market_context=MarketContext(holding_trading_days=3), execution_config=ExitExecutionConfig(take_profit_enabled=True))
        timeout = evaluate_exit(pos, PriceSnapshot(high=101.0, low=100.0, close=101.0), rb, market_context=MarketContext(holding_trading_days=10), execution_config=ExitExecutionConfig(take_profit_enabled=False))
        stop = evaluate_exit(pos, PriceSnapshot(high=100.0, low=94.0, close=94.0), rb, market_context=MarketContext(holding_trading_days=3), execution_config=ExitExecutionConfig(take_profit_enabled=False))
        return {
            "take_profit_enabled_false": {"should_exit": no_tp.should_exit, "reason": no_tp.reason, "target_hit": no_tp.diagnostics.get("target_hit"), "raw_target_hit": no_tp.diagnostics.get("raw_target_hit")},
            "take_profit_enabled_true": {"should_exit": yes_tp.should_exit, "reason": yes_tp.reason, "target_hit": yes_tp.diagnostics.get("target_hit"), "raw_target_hit": yes_tp.diagnostics.get("raw_target_hit")},
            "timeout_trigger": {"should_exit": timeout.should_exit, "reason": timeout.reason},
            "stop_loss_trigger": {"should_exit": stop.should_exit, "reason": stop.reason},
            "s2_trigger_set": ["stop_loss", "trailing", "sell_omen", "time_out", "breakeven_stop"],
            "excluded_trigger_when_s2_no_tp": "take_profit",
        }
