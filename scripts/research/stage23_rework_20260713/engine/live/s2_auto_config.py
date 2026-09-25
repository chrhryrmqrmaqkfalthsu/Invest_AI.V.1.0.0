"""Configuration helpers for S2 automated live trading.

All defaults are fail-closed. This module never places orders and does not
modify environment variables.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CONFIG_PATH = Path("data/_system/live_auto_config.json")
STATE_PATH = Path("data/_system/live_auto_state.json")
EVENTS_PATH = Path("data/_system/live_auto_events.jsonl")
ORDER_INTENTS_PATH = Path("data/_system/live_auto_order_intents.jsonl")
DIRECT_ORDER_ENV = "KINGMAKER_REAL_DASHBOARD_ALLOW_DIRECT_ORDERS"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "s2_auto_live",
        "master_enabled": False,
        "auto_buy_enabled": False,
        "auto_exit_enabled": False,
        "real_orders_enabled": False,
        "dry_run": True,
        "entry_timing": "next_open",
        "allow_intraday_immediate": False,
        "display_slots": 8,
        "portfolio_K": 20,
        "total_capital_mode": "fixed_from_account_at_start",
        "capital_source": "available_cash",
        "selection": {
            "gate_policy": "KEEP_80_DROP_BAD_MAE_13",
            "signal_policy": "evaluate_signal_should_buy_true",
            "priority_policy": "final_score_desc_spy_down_high_vol_deprioritize",
            "eq_policy": "ignored_unverified",
        },
        "capital": {
            "total_capital_usd": None,
            "allocation_mode": "equal_weight_fixed_slot",
            "cash_buffer_usd": 10.0,
            "cash_buffer_pct": 0.02,
            "rebalance_existing_positions": False,
        },
        "exit": {
            "engine": "PositionManager_ExitPolicy",
            "require_exit_live_policy": True,
            "allow_legacy_fallback": False,
            "s2_take_profit_enabled": False,
            "allow_manual_chart_exit_plan": False,
        },
        "risk_limits": {
            "max_order_notional_usd": 100.0,
            "max_daily_orders": 3,
            "max_daily_buy_notional_usd": 300.0,
            "max_total_exposure_usd": 650.0,
            "min_cash_buffer_usd": 10.0,
            "one_position_per_ticker": True,
            "min_fractional_shares": 0.0001,
        },
        "operator_approval": {
            "operator_armed": False,
            "armed_until_utc": "",
            "confirmation_phrase_required": True,
            "confirmation_phrase": "S2_AUTO_LIVE_APPROVE",
        },
        "kill_switch": {
            "path": "data/_system/live_auto_kill_switch",
            "scope": "master_enabled_false_blocks_all_auto_actions",
        },
        "state_paths": {
            "state": str(STATE_PATH),
            "events": str(EVENTS_PATH),
            "order_intents": str(ORDER_INTENTS_PATH),
        },
    }


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in dict(override).items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def load_live_auto_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    cfg = default_config()
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, Mapping):
                cfg = _deep_merge(cfg, data)
        except Exception:
            cfg["_config_error"] = f"failed_to_read:{p}"
    return cfg


def bool_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def direct_order_env_enabled() -> bool:
    return bool_enabled(os.environ.get(DIRECT_ORDER_ENV, ""))


def s2_take_profit_enabled(config: Mapping[str, Any] | None = None, default: bool = False) -> bool:
    cfg = config or load_live_auto_config()
    exit_cfg = cfg.get("exit") if isinstance(cfg.get("exit"), Mapping) else {}
    return bool_enabled(exit_cfg.get("s2_take_profit_enabled", default))


def s2_auto_exit_requires_policy(config: Mapping[str, Any] | None = None) -> bool:
    cfg = config or load_live_auto_config()
    if not bool_enabled(cfg.get("master_enabled")) or not bool_enabled(cfg.get("auto_exit_enabled")):
        return False
    exit_cfg = cfg.get("exit") if isinstance(cfg.get("exit"), Mapping) else {}
    return bool_enabled(exit_cfg.get("require_exit_live_policy", True)) and not bool_enabled(exit_cfg.get("allow_legacy_fallback", False))


def parse_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def operator_arm_valid(config: Mapping[str, Any], confirmation_phrase: str = "") -> tuple[bool, str]:
    approval = config.get("operator_approval") if isinstance(config.get("operator_approval"), Mapping) else {}
    if not bool_enabled(approval.get("operator_armed")):
        return False, "operator_not_armed"
    armed_until = parse_utc(approval.get("armed_until_utc"))
    if armed_until is None or armed_until <= datetime.now(timezone.utc):
        return False, "operator_arm_expired"
    if bool_enabled(approval.get("confirmation_phrase_required", True)):
        expected = str(approval.get("confirmation_phrase") or "").strip()
        if not expected or str(confirmation_phrase or "").strip() != expected:
            return False, "confirmation_phrase_mismatch"
    return True, "operator_arm_valid"


def kill_switch_active(config: Mapping[str, Any]) -> tuple[bool, str]:
    if not bool_enabled(config.get("master_enabled")):
        return True, "master_enabled_false"
    kill = config.get("kill_switch") if isinstance(config.get("kill_switch"), Mapping) else {}
    path = Path(str(kill.get("path") or "data/_system/live_auto_kill_switch"))
    if path.exists():
        return True, f"kill_switch_file:{path}"
    return False, "clear"


def real_order_gate(config: Mapping[str, Any], confirmation_phrase: str = "") -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not bool_enabled(config.get("master_enabled")):
        reasons.append("master_disabled")
    if not bool_enabled(config.get("real_orders_enabled")):
        reasons.append("real_orders_disabled")
    if bool_enabled(config.get("dry_run", True)):
        reasons.append("dry_run_enabled")
    if not direct_order_env_enabled():
        reasons.append(f"env_{DIRECT_ORDER_ENV}_disabled")
    ok, reason = operator_arm_valid(config, confirmation_phrase=confirmation_phrase)
    if not ok:
        reasons.append(reason)
    killed, kill_reason = kill_switch_active(config)
    if killed:
        reasons.append(kill_reason)
    return not reasons, reasons


@dataclass(frozen=True)
class CapitalPlan:
    total_capital_usd: float
    portfolio_k: int
    position_notional_usd: float
    source: str


def portfolio_k(config: Mapping[str, Any]) -> int:
    try:
        return max(1, int(config.get("portfolio_K", 20) or 20))
    except Exception:
        return 20
