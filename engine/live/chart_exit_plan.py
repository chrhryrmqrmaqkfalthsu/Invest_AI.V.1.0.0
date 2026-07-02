"""Dashboard chart TP/SL exit plans for real live positions.

목적:
- 대시보드 보유 차트에서 사용자가 지정한 익절/손절 라인을 파일로 저장한다.
- 정규장 중 현재가가 라인을 터치하면 manual_sell_intent를 생성한다.
- 실제 broker 주문은 기존 live Runner가 manual_sell_intent를 소비하면서만 발생한다.

주의:
- 이 모듈은 broker.submit_order를 직접 호출하지 않는다.
- 정규장 밖에서는 평가/트리거를 fail-closed 한다.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from engine.live.manual_sell_intent import create_manual_sell_intent
from engine.live.position_manager import POSITIONS_PATH
from engine.live.regular_hours_gate import regular_hours_snapshot

PLAN_PATH = Path("data/_system/chart_exit_plans.json")
SCHEMA_VERSION = 1
SHARE_EPS = 1e-6

PriceLookup = Callable[[str], float | None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _positive_or_none(value: Any) -> float | None:
    v = _num(value, 0.0)
    return v if v > 0.0 else None


def _ticker(value: Any) -> str:
    return str(value or "").upper().strip()


def _read_json(path: Path | str, default: Any) -> Any:
    try:
        p = Path(path)
        if not p.exists():
            return default
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if data is not None else default
    except Exception:
        return default


def _atomic_write_json(path: Path | str, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def load_chart_exit_state(path: Path | str | None = None) -> dict[str, Any]:
    data = _read_json(path or PLAN_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema_version", SCHEMA_VERSION)
    data.setdefault("created_at", utc_now())
    data.setdefault("updated_at", data.get("created_at") or utc_now())
    data.setdefault("plans", {})
    data.setdefault("last_evaluation", None)
    if not isinstance(data.get("plans"), dict):
        data["plans"] = {}
    return data


def save_chart_exit_state(state: dict[str, Any], path: Path | str | None = None) -> None:
    state["schema_version"] = SCHEMA_VERSION
    state["updated_at"] = utc_now()
    _atomic_write_json(path or PLAN_PATH, state)


def _load_positions(path: Path | str | None = None) -> dict[str, Any]:
    data = _read_json(path or POSITIONS_PATH, {})
    return data if isinstance(data, dict) else {}


def _held_position(ticker: str, positions: dict[str, Any]) -> dict[str, Any] | None:
    row = positions.get(_ticker(ticker))
    if not isinstance(row, dict):
        return None
    if _num(row.get("shares"), 0.0) <= SHARE_EPS:
        return None
    return row


def upsert_chart_exit_plan(
    *,
    ticker: str,
    take_profit_price: float | None = None,
    stop_loss_price: float | None = None,
    enabled: bool = True,
    source: str = "dashboard_chart",
    plan_path: Path | str | None = None,
    positions_path: Path | str | None = None,
) -> dict[str, Any]:
    ticker_u = _ticker(ticker)
    if not ticker_u:
        raise ValueError("ticker required")
    positions = _load_positions(positions_path)
    position = _held_position(ticker_u, positions)
    if position is None:
        raise ValueError("not held")

    tp = _positive_or_none(take_profit_price)
    sl = _positive_or_none(stop_loss_price)
    if tp is None and sl is None:
        raise ValueError("take_profit_price or stop_loss_price required")
    if tp is not None and sl is not None and tp <= sl:
        raise ValueError("take_profit_price must be greater than stop_loss_price")

    now = utc_now()
    state = load_chart_exit_state(plan_path)
    plans = state.setdefault("plans", {})
    old = plans.get(ticker_u) if isinstance(plans.get(ticker_u), dict) else {}
    plan = {
        **old,
        "ticker": ticker_u,
        "enabled": bool(enabled),
        "status": "active" if enabled else "disabled",
        "take_profit_price": tp,
        "stop_loss_price": sl,
        "source": source or "dashboard_chart",
        "entry_price": _num(position.get("entry_price"), 0.0),
        "shares_at_plan": _num(position.get("shares"), 0.0),
        "position_entry_date": str(position.get("entry_date") or ""),
        "created_at": old.get("created_at") or now,
        "updated_at": now,
        "triggered_at": "",
        "triggered_price": None,
        "trigger_kind": "",
        "triggered_intent_id": "",
        "last_checked_at": old.get("last_checked_at", ""),
        "last_price": old.get("last_price"),
        "last_message": "",
        "note": "Dashboard chart TP/SL. Actual order is placed only by live Runner through manual_sell_intent.",
    }
    plans[ticker_u] = plan
    save_chart_exit_state(state, plan_path)
    return plan


def disable_chart_exit_plan(*, ticker: str, plan_path: Path | str | None = None) -> dict[str, Any]:
    ticker_u = _ticker(ticker)
    if not ticker_u:
        raise ValueError("ticker required")
    state = load_chart_exit_state(plan_path)
    plans = state.setdefault("plans", {})
    plan = plans.get(ticker_u)
    if not isinstance(plan, dict):
        raise ValueError("plan not found")
    plan["enabled"] = False
    plan["status"] = "disabled"
    plan["updated_at"] = utc_now()
    plan["last_message"] = "disabled_by_user"
    save_chart_exit_state(state, plan_path)
    return plan


def _trigger_reason(kind: str) -> str:
    return "chart_take_profit" if kind == "take_profit" else "chart_stop_loss"


def _evaluate_plan_hit(plan: dict[str, Any], price: float) -> str | None:
    tp = _positive_or_none(plan.get("take_profit_price"))
    sl = _positive_or_none(plan.get("stop_loss_price"))
    if sl is not None and price <= sl:
        return "stop_loss"
    if tp is not None and price >= tp:
        return "take_profit"
    return None


def evaluate_chart_exit_plans(
    *,
    price_lookup: PriceLookup,
    plan_path: Path | str | None = None,
    positions_path: Path | str | None = None,
    intent_path: Path | str | None = None,
    force_regular_hours: bool = True,
    source: str = "chart_exit_plan",
) -> dict[str, Any]:
    started = utc_now()
    gate = regular_hours_snapshot()
    state = load_chart_exit_state(plan_path)
    plans = state.setdefault("plans", {})
    if force_regular_hours and not bool(gate.get("allow_decision")):
        result = {
            "time": started,
            "ok": True,
            "skipped": True,
            "reason": "outside_regular_hours",
            "decision_gate": gate,
            "evaluated": 0,
            "triggered": [],
            "errors": [],
        }
        state["last_evaluation"] = result
        save_chart_exit_state(state, plan_path)
        return {**result, "state": state}

    positions = _load_positions(positions_path)
    evaluated = 0
    triggered: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    now = utc_now()

    for ticker_u, plan in list(plans.items()):
        if not isinstance(plan, dict):
            continue
        ticker_norm = _ticker(plan.get("ticker") or ticker_u)
        if not ticker_norm:
            continue
        if not bool(plan.get("enabled", False)) or str(plan.get("status") or "") != "active":
            continue
        pos = _held_position(ticker_norm, positions)
        if pos is None:
            plan["enabled"] = False
            plan["status"] = "orphaned"
            plan["updated_at"] = now
            plan["last_message"] = "position_not_held"
            continue
        try:
            price = price_lookup(ticker_norm)
        except Exception as exc:
            price = None
            errors.append({"ticker": ticker_norm, "reason": f"price_lookup_error:{type(exc).__name__}", "message": str(exc)})
        price_f = _positive_or_none(price)
        if price_f is None:
            errors.append({"ticker": ticker_norm, "reason": "price_missing"})
            continue
        evaluated += 1
        plan["last_checked_at"] = now
        plan["last_price"] = price_f
        hit = _evaluate_plan_hit(plan, price_f)
        if hit is None:
            plan["last_message"] = "hold"
            continue

        reason = _trigger_reason(hit)
        try:
            intent = create_manual_sell_intent(
                ticker=ticker_norm,
                shares_requested=None,
                source=source,
                positions_path=positions_path or POSITIONS_PATH,
                intent_path=intent_path,
                reason=reason,
                note=f"chart_exit_plan {hit} hit at {price_f:.4f}",
                metadata={
                    "chart_exit_plan": True,
                    "trigger_kind": hit,
                    "trigger_price": price_f,
                    "take_profit_price": plan.get("take_profit_price"),
                    "stop_loss_price": plan.get("stop_loss_price"),
                },
            )
        except TypeError:
            # 구버전 create_manual_sell_intent 호환. reason 지원 전이면 source만 남긴다.
            intent = create_manual_sell_intent(
                ticker=ticker_norm,
                shares_requested=None,
                source=source,
                positions_path=positions_path or POSITIONS_PATH,
                intent_path=intent_path,
            )
        except Exception as exc:
            errors.append({"ticker": ticker_norm, "reason": f"intent_error:{type(exc).__name__}", "message": str(exc)})
            plan["last_message"] = f"intent_error:{type(exc).__name__}"
            continue

        plan["enabled"] = False
        plan["status"] = "triggered"
        plan["triggered_at"] = now
        plan["triggered_price"] = price_f
        plan["trigger_kind"] = hit
        plan["triggered_intent_id"] = str((intent or {}).get("intent_id") or "")
        plan["updated_at"] = now
        plan["last_message"] = reason
        triggered.append({
            "ticker": ticker_norm,
            "trigger_kind": hit,
            "trigger_price": price_f,
            "reason": reason,
            "intent": intent,
            "plan": dict(plan),
        })

    result = {
        "time": now,
        "ok": True,
        "skipped": False,
        "reason": "evaluated",
        "decision_gate": gate,
        "evaluated": evaluated,
        "triggered": triggered,
        "errors": errors[-20:],
    }
    state["last_evaluation"] = {k: v for k, v in result.items() if k != "triggered"}
    state["last_evaluation"]["triggered_count"] = len(triggered)
    state["last_evaluation"]["triggered_tickers"] = [r.get("ticker") for r in triggered]
    save_chart_exit_state(state, plan_path)
    return {**result, "state": state}
