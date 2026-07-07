"""Dashboard slot display override for chart TP/SL plans.

이 모듈은 대시보드 API 응답의 표시값만 바꾼다.
- broker 주문을 직접 호출하지 않는다.
- positions.json을 수정하지 않는다.
- chart_exit_plans.json의 active 계획이 있을 때만 /api/live/slots 게이지용
  stop_price/target_price를 수동값으로 대체한다.
"""
from __future__ import annotations

from typing import Any

from engine.live.chart_exit_plan import load_chart_exit_state


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out != out or out <= 0.0:
            return None
        return out
    except Exception:
        return None


def _return_pct(entry: Any, price: Any) -> float | None:
    e = _num(entry)
    p = _num(price)
    if e is None or p is None:
        return None
    return (p / e - 1.0) * 100.0


def _ticker(value: Any) -> str:
    return str(value or "").upper().strip()


def _active_plan_for_ticker(ticker: str, plans: dict[str, Any]) -> dict[str, Any] | None:
    plan = plans.get(_ticker(ticker)) if isinstance(plans, dict) else None
    if not isinstance(plan, dict):
        return None
    if not bool(plan.get("enabled")) or str(plan.get("status") or "") != "active":
        return None
    tp = _num(plan.get("take_profit_price"))
    sl = _num(plan.get("stop_loss_price"))
    if tp is None and sl is None:
        return None
    return plan


def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """프론트 표시/디버그에 필요한 필드만 내려준다."""
    return {
        "ticker": _ticker(plan.get("ticker")),
        "enabled": bool(plan.get("enabled")),
        "status": plan.get("status"),
        "take_profit_price": _num(plan.get("take_profit_price")),
        "stop_loss_price": _num(plan.get("stop_loss_price")),
        "take_profit_pct": plan.get("take_profit_pct"),
        "stop_loss_pct": plan.get("stop_loss_pct"),
        "take_profit_basis": plan.get("take_profit_basis"),
        "stop_loss_basis": plan.get("stop_loss_basis"),
        "updated_at": plan.get("updated_at"),
        "source": plan.get("source"),
    }


def apply_chart_exit_display_override(row: dict[str, Any], plans: dict[str, Any]) -> dict[str, Any]:
    """Return a dashboard row with manual chart TP/SL values applied for display.

    슬롯 게이지는 기존 dashboard_home.html이 row.stop_price/row.target_price를 기준으로
    계산하므로, active chart_exit_plan이 있으면 응답 row에서만 해당 값을 교체한다.
    실제 매매/청산 판단 파일인 positions.json은 건드리지 않는다.
    """
    if not isinstance(row, dict) or row.get("empty"):
        return row
    ticker = _ticker(row.get("ticker"))
    plan = _active_plan_for_ticker(ticker, plans)
    if plan is None:
        return row

    tp = _num(plan.get("take_profit_price"))
    sl = _num(plan.get("stop_loss_price"))
    if tp is None and sl is None:
        return row

    out = dict(row)
    out.setdefault("rulebook_target_price", row.get("target_price"))
    out.setdefault("rulebook_stop_price", row.get("stop_price"))
    out["manual_exit_plan_active"] = True
    out["display_exit_plan_source"] = "chart_exit_plan"
    out["chart_exit_plan"] = _compact_plan(plan)

    if tp is not None:
        out["target_price"] = tp
        out["target_return_pct"] = _return_pct(out.get("entry_price"), tp)
    if sl is not None:
        out["stop_price"] = sl
        out["stop_return_pct"] = _return_pct(out.get("entry_price"), sl)
    return out


def _load_plans() -> dict[str, Any]:
    state = load_chart_exit_state()
    plans = state.get("plans") if isinstance(state, dict) else {}
    return plans if isinstance(plans, dict) else {}


def install_slot_display_routes(app, base_module: Any) -> None:
    """Replace dashboard positions/slots API routes with display-only overrides."""
    target_paths = {"/api/live/positions", "/api/live/slots"}
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", "") in target_paths
            and "GET" in set(getattr(route, "methods", set()) or set())
        )
    ]

    @app.get("/api/live/positions")
    def live_positions_chart_exit_display():
        rows = base_module.live_positions()
        plans = _load_plans()
        if not isinstance(rows, list):
            return rows
        return [apply_chart_exit_display_override(row, plans) for row in rows]

    @app.get("/api/live/slots")
    def live_slots_chart_exit_display(max_slots: int = 8):
        filled = live_positions_chart_exit_display()
        if not isinstance(filled, list):
            filled = []
        slots: list[dict[str, Any]] = []
        for i in range(max_slots):
            if i < len(filled) and isinstance(filled[i], dict):
                slots.append({"slot": i + 1, "empty": False, **filled[i]})
            else:
                slots.append({"slot": i + 1, "empty": True})
        return slots
