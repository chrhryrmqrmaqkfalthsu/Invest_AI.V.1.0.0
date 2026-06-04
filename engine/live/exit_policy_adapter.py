"""Live adapter and shadow helpers for the shared ExitPolicy.

The live PositionManager still owns real order decisions during C-P1 shadow.
This module only normalizes state, evaluates ExitPolicy in parallel, and logs
classified differences.  Trading-day holding counts use the shared calendar.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from engine.core.exit_policy import (
    ExitDecision,
    ExitExecutionConfig,
    MarketContext as ExitMarketContext,
    PositionState,
    PriceSnapshot,
    evaluate_exit,
    initialize_position_state,
)
from engine.core.metadata import compute_member_hash, compute_rulebook_hash
from engine.live.market_clock import UsMarketClock, market_clock_for_ticker
from engine.strategies.rulebook import Rulebook

KST = ZoneInfo("Asia/Seoul")
LIVE_SHADOW_ROOT = Path("logs/live_exit_shadow")
SYMBOLS_DIR = Path("data/symbols")
SEED_PATTERNS_PATH = Path("data/_system/seed_patterns.json")


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return default if value is None else int(float(value))
    except Exception:
        return default


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if dataclasses.is_dataclass(value):
        try:
            return _json_safe(asdict(value))
        except Exception:
            return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    method = getattr(value, "to_dict", None)
    if callable(method):
        try:
            return _json_safe(method())
        except Exception:
            pass
    raw = getattr(value, "__dict__", None)
    return _json_safe(raw) if isinstance(raw, Mapping) else str(value)


def count_holding_trading_days(
    entry_date: Any,
    now: Optional[datetime] = None,
    *,
    ticker: Optional[str] = None,
    market_clock: Any = None,
) -> int:
    """Count exact exchange sessions after entry through ``now``.

    Existing live entry timestamps are stored in KST.  For the current US-only
    C-P1 universe, omitting ticker intentionally defaults to UsMarketClock.
    """
    try:
        entry = datetime.fromisoformat(str(entry_date))
    except Exception:
        return 0
    if entry.tzinfo is None:
        entry = entry.replace(tzinfo=KST)
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    try:
        clock = market_clock or (market_clock_for_ticker(ticker) if ticker else UsMarketClock())
        return max(0, int(clock.session_count(entry, current)))
    except Exception:
        return 0


def approximate_trading_days(
    entry_date: Any,
    now: Optional[datetime] = None,
    *,
    ticker: Optional[str] = None,
    market_clock: Any = None,
) -> int:
    """Backward-compatible alias; now calendar-backed rather than approximate."""
    return count_holding_trading_days(entry_date, now, ticker=ticker, market_clock=market_clock)


def position_entry_to_state(pos: Any, rulebook: Any, holding_trading_days: int) -> PositionState:
    direction = str(_get(pos, "rulebook_direction", _get(rulebook, "direction", "long")) or "long")
    entry_price = _safe_float(_get(pos, "entry_price", 0.0))
    snapshot = _get(pos, "rulebook_snapshot", None)
    if not isinstance(snapshot, Mapping):
        snapshot = _json_safe(rulebook) if rulebook is not None else {}
    member_hash = str(_get(pos, "member_hash", "") or "")
    if not member_hash and rulebook is not None:
        member_hash = compute_member_hash(rulebook)
    return PositionState(
        ticker=str(_get(pos, "ticker", _get(rulebook, "ticker", "")) or ""),
        direction=direction,
        entry_date=str(_get(pos, "entry_date", "") or ""),
        entry_price=entry_price,
        avg_cost=entry_price,
        shares=_safe_float(_get(pos, "shares", 0.0)),
        atr_at_entry=_safe_float(_get(pos, "atr_at_entry", max(entry_price * 0.02, 0.0))),
        stop_price=_safe_float(_get(pos, "stop_price", entry_price)),
        target_price=_safe_float(_get(pos, "target_price", entry_price)),
        trailing_stop=_safe_float(_get(pos, "trailing_stop", entry_price)),
        trailing_distance=_safe_float(_get(pos, "trailing_distance", 0.0)),
        highest_price=_safe_float(_get(pos, "highest_price", entry_price)),
        max_holding_days=_safe_int(_get(pos, "max_holding_days", _get(rulebook, "max_holding_days", 20)), 20),
        exit_strategy=str(_get(pos, "exit_strategy", _get(rulebook, "exit_strategy", "hybrid")) or "hybrid"),
        holding_trading_days=max(0, _safe_int(holding_trading_days, 0)),
        add_buy_count=max(0, _safe_int(_get(pos, "add_buy_count", 0), 0)),
        rulebook_snapshot=dict(snapshot) if isinstance(snapshot, Mapping) else {},
        member_hash=member_hash,
    )


def position_entry_to_dynamic_state(
    pos: Any,
    rulebook: Any,
    holding_trading_days: int,
    market_context: ExitMarketContext,
) -> PositionState:
    entry_price = _safe_float(_get(pos, "entry_price", 0.0))
    highest = _safe_float(_get(pos, "highest_price", entry_price), entry_price)
    state = initialize_position_state(
        ticker=str(_get(pos, "ticker", _get(rulebook, "ticker", "")) or ""),
        entry_price=entry_price,
        shares=_safe_float(_get(pos, "shares", 0.0)),
        rulebook=rulebook,
        atr_value=_safe_float(_get(pos, "atr_at_entry", max(entry_price * 0.02, 0.0))),
        market_context=market_context,
        entry_date=str(_get(pos, "entry_date", "") or ""),
        member_hash=str(_get(pos, "member_hash", "") or "") or None,
    )
    return replace(
        state,
        avg_cost=entry_price,
        highest_price=highest,
        trailing_stop=max(state.trailing_stop, highest - state.trailing_distance),
        holding_trading_days=max(0, _safe_int(holding_trading_days, 0)),
        add_buy_count=max(0, _safe_int(_get(pos, "add_buy_count", 0), 0)),
    )


def market_context_to_exit_context(
    ctx: Any,
    sector_name: str,
    holding_trading_days: Optional[int] = None,
    current_trade_date: Optional[str] = None,
) -> ExitMarketContext:
    sector_map = _get(ctx, "sector_strength", {}) or {}
    sector_score = _safe_float(sector_map.get(sector_name, 50.0) if isinstance(sector_map, Mapping) else 50.0, 50.0)
    return ExitMarketContext(
        market_score=_safe_float(_get(ctx, "score", _get(ctx, "market_score", 50.0)), 50.0),
        vix_level=_safe_float(_get(ctx, "vix_level", 18.0), 18.0),
        sector_score=sector_score,
        current_trade_date=current_trade_date,
        holding_trading_days=holding_trading_days,
    )


def legacy_live_decision(pos: Any, price: float, holding_calendar_days: int) -> dict[str, Any]:
    current = _safe_float(price)
    highest = max(_safe_float(_get(pos, "highest_price", current)), current)
    trailing_distance = _safe_float(_get(pos, "trailing_distance", 0.0))
    trailing_stop = max(_safe_float(_get(pos, "trailing_stop", current)), highest - trailing_distance)
    stop_price = _safe_float(_get(pos, "stop_price", current))
    target_price = _safe_float(_get(pos, "target_price", current))
    max_holding_days = _safe_int(_get(pos, "max_holding_days", 20), 20)
    strategy = str(_get(pos, "exit_strategy", "hybrid") or "hybrid").lower()
    hits = {
        "stop_hit": current <= stop_price,
        "target_hit": current >= target_price,
        "trailing_hit": current <= trailing_stop,
        "timeout_hit": int(holding_calendar_days) >= max_holding_days,
    }
    reason: Optional[str] = None
    if strategy == "fixed":
        if hits["stop_hit"]:
            reason = "stop_loss"
        elif hits["target_hit"]:
            reason = "take_profit"
        elif hits["timeout_hit"]:
            reason = "time_out"
    elif strategy == "trailing":
        if hits["trailing_hit"]:
            reason = "trailing"
        elif hits["timeout_hit"]:
            reason = "time_out"
    elif strategy == "hybrid":
        if hits["target_hit"]:
            reason = "take_profit"
        elif hits["trailing_hit"]:
            reason = "trailing"
        elif hits["stop_hit"]:
            reason = "stop_loss"
        elif hits["timeout_hit"]:
            reason = "time_out"
    return {
        "reason": reason,
        "price": current if reason is not None else None,
        "strategy": strategy,
        "highest_price": highest,
        "trailing_stop": trailing_stop,
        "hits": hits,
    }


def resolve_live_rulebook(ticker: str, provider: Any = None) -> tuple[Optional[Rulebook], str]:
    if provider is not None:
        try:
            getter = getattr(provider, "get_rulebook", None)
            rb = getter(ticker) if callable(getter) else None
            if rb is None:
                loader = getattr(provider, "_load_rulebook", None)
                rb = loader(ticker) if callable(loader) else None
            if rb is not None:
                return rb, "LearnedRuleBook"
        except Exception:
            pass
    params_path = SYMBOLS_DIR / ticker / "parameters.json"
    if params_path.exists():
        try:
            data = json.loads(params_path.read_text(encoding="utf-8"))
            payload = data.get("rulebook") if isinstance(data, Mapping) else None
            if isinstance(payload, Mapping):
                return Rulebook.from_dict(dict(payload)), str(params_path)
        except Exception:
            pass
    if SEED_PATTERNS_PATH.exists():
        try:
            seeds = json.loads(SEED_PATTERNS_PATH.read_text(encoding="utf-8"))
            for direction in ("long", "short"):
                for seed in seeds.get(direction, []) if isinstance(seeds, Mapping) else []:
                    payload = seed.get("rulebook", {}) if isinstance(seed, Mapping) else {}
                    if payload.get("ticker") == ticker:
                        return Rulebook.from_dict(dict(payload)), str(SEED_PATTERNS_PATH)
        except Exception:
            pass
    return None, "missing"


def _levels_differ(static_state: PositionState, dynamic_state: PositionState, tolerance: float = 1e-9) -> bool:
    return any(
        abs(float(a) - float(b)) > tolerance
        for a, b in (
            (static_state.stop_price, dynamic_state.stop_price),
            (static_state.target_price, dynamic_state.target_price),
            (static_state.trailing_distance, dynamic_state.trailing_distance),
            (static_state.trailing_stop, dynamic_state.trailing_stop),
        )
    )


def classify_shadow_difference(
    legacy_reason: Optional[str],
    policy_reason: Optional[str],
    diagnostics: Mapping[str, Any],
) -> str:
    if legacy_reason == policy_reason:
        return "SAME"
    if bool(diagnostics.get("trailing_delay_difference")):
        return "INTENTIONAL_TRAILING_DELAY"
    if bool(diagnostics.get("timeout_boundary")):
        return "INTENTIONAL_TRADING_DAY_TIMEOUT"
    if bool(diagnostics.get("hybrid_priority_difference")):
        return "INTENTIONAL_HYBRID_PRIORITY"
    if bool(diagnostics.get("dynamic_exit_difference")):
        return "INTENTIONAL_DYNAMIC_EXIT"
    return "BUG_CANDIDATE"


def compare_legacy_and_exit_policy(
    legacy_decision: Mapping[str, Any] | None,
    exit_policy_decision: ExitDecision,
    *,
    ticker: str,
    price: float,
    position: Any,
    rulebook: Any,
    market_context: ExitMarketContext,
    holding_calendar_days: int,
    holding_trading_days: int,
    static_state: Optional[PositionState] = None,
    dynamic_state: Optional[PositionState] = None,
    rulebook_source: str = "",
    timestamp: Optional[str] = None,
    extra_diagnostics: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    legacy = dict(legacy_decision or {})
    policy_reason = exit_policy_decision.reason
    legacy_reason = legacy.get("reason")
    diagnostics = dict(exit_policy_decision.diagnostics or {})
    diagnostics.update(dict(extra_diagnostics or {}))
    strategy = str(_get(position, "exit_strategy", _get(rulebook, "exit_strategy", "")) or "").lower()
    legacy_hits = dict(legacy.get("hits") or {})
    policy_hits = {
        "stop_hit": bool(diagnostics.get("stop_hit", False)),
        "target_hit": bool(diagnostics.get("target_hit", False)),
        "trailing_hit": bool(diagnostics.get("trailing_hit", False)),
        "timeout_hit": bool(diagnostics.get("timeout_hit", False)),
    }
    combined_hits = {key: bool(legacy_hits.get(key)) or bool(policy_hits.get(key)) for key in policy_hits}
    diagnostics["hybrid_priority_difference"] = bool(
        diagnostics.get("hybrid_priority_difference")
        or (strategy == "hybrid" and sum(bool(combined_hits[k]) for k in ("stop_hit", "target_hit", "trailing_hit")) >= 2 and legacy_reason != policy_reason)
    )
    diagnostics["trailing_delay_difference"] = bool(
        diagnostics.get("trailing_delay_difference")
        or ({legacy_reason, policy_reason} == {"trailing", None} and int(holding_trading_days) <= int(diagnostics.get("trailing_activation_bars", 2)))
    )
    diagnostics["timeout_boundary"] = bool(
        diagnostics.get("timeout_boundary")
        or (legacy_reason != policy_reason and (legacy_reason == "time_out" or policy_reason == "time_out") and int(holding_calendar_days) != int(holding_trading_days))
    )
    diagnostics["dynamic_exit_difference"] = bool(
        diagnostics.get("dynamic_exit_difference")
        or (static_state is not None and dynamic_state is not None and _levels_differ(static_state, dynamic_state))
    )
    diagnostics["existing_position_context_limit"] = (
        "entry-time market context/rulebook_snapshot/member_hash may be absent; current live rulebook/context used approximately"
    )
    difference_type = classify_shadow_difference(legacy_reason, policy_reason, diagnostics)
    ts = timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "ticker": ticker,
        "ts": ts,
        "price": float(price),
        "legacy_reason": legacy_reason,
        "exit_policy_reason": policy_reason,
        "difference_type": difference_type,
        "legacy": _json_safe(legacy),
        "exit_policy": {
            "should_exit": bool(exit_policy_decision.should_exit),
            "reason": policy_reason,
            "trigger_price": exit_policy_decision.trigger_price,
            "fill_price_base": exit_policy_decision.fill_price_base,
            "fill_price_stress": exit_policy_decision.fill_price_stress,
        },
        "position": _json_safe(position),
        "rulebook": {
            "source": rulebook_source,
            "rulebook_hash": compute_rulebook_hash(rulebook) if rulebook is not None else "",
            "member_hash": str(_get(position, "member_hash", "") or "") or (compute_member_hash(rulebook) if rulebook is not None else ""),
            "exit_strategy": strategy,
        },
        "market": {
            "market_score": market_context.market_score,
            "vix_level": market_context.vix_level,
            "sector_score": market_context.sector_score,
        },
        "holding_calendar_days": int(holding_calendar_days),
        "holding_trading_days": int(holding_trading_days),
        "levels": {
            "legacy_stop": static_state.stop_price if static_state else _get(position, "stop_price", None),
            "legacy_target": static_state.target_price if static_state else _get(position, "target_price", None),
            "legacy_trailing": static_state.trailing_stop if static_state else _get(position, "trailing_stop", None),
            "policy_stop": dynamic_state.stop_price if dynamic_state else None,
            "policy_target": dynamic_state.target_price if dynamic_state else None,
            "policy_trailing": dynamic_state.trailing_stop if dynamic_state else None,
        },
        "diagnostics": _json_safe(diagnostics),
    }


def evaluate_live_shadow(
    *,
    ticker: str,
    pos: Any,
    price: float,
    rulebook: Any,
    raw_market_context: Any,
    holding_calendar_days: int,
    holding_trading_days: int,
    actual_legacy_reason: Optional[str] = None,
    rulebook_source: str = "",
    timestamp: Optional[str] = None,
) -> dict[str, Any]:
    sector_name = str(_get(rulebook, "sector_name", "") or "")
    exit_ctx = market_context_to_exit_context(
        raw_market_context,
        sector_name,
        holding_trading_days=holding_trading_days,
        current_trade_date=timestamp,
    )
    static_state = position_entry_to_state(pos, rulebook, holding_trading_days)
    dynamic_state = position_entry_to_dynamic_state(pos, rulebook, holding_trading_days, exit_ctx)
    decision = evaluate_exit(
        dynamic_state,
        PriceSnapshot(date=timestamp or "", current_price=float(price), close=float(price)),
        rulebook,
        market_context=exit_ctx,
        execution_config=ExitExecutionConfig(
            mode="live",
            base_slippage_bps=0.0,
            stress_slippage_bps=0.0,
            use_next_open=False,
            trailing_activation_bars=2,
        ),
    )
    legacy = legacy_live_decision(pos, price, holding_calendar_days)
    if actual_legacy_reason is not None or legacy.get("reason") is not None:
        legacy["reason"] = actual_legacy_reason
        legacy["price"] = float(price) if actual_legacy_reason is not None else None
    return compare_legacy_and_exit_policy(
        legacy,
        decision,
        ticker=ticker,
        price=price,
        position=pos,
        rulebook=rulebook,
        market_context=exit_ctx,
        holding_calendar_days=holding_calendar_days,
        holding_trading_days=holding_trading_days,
        static_state=static_state,
        dynamic_state=dynamic_state,
        rulebook_source=rulebook_source,
        timestamp=timestamp,
    )


def live_shadow_log_path(record: Mapping[str, Any], root: Path = LIVE_SHADOW_ROOT) -> Path:
    ticker = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(record.get("ticker") or "UNKNOWN"))
    raw_ts = str(record.get("ts") or "")
    try:
        day = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).astimezone(KST).strftime("%Y-%m-%d")
    except Exception:
        day = datetime.now(KST).strftime("%Y-%m-%d")
    return root / day / f"{ticker}.jsonl"


def write_live_shadow_record(record: Mapping[str, Any], root: Path = LIVE_SHADOW_ROOT) -> Path:
    path = live_shadow_log_path(record, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(record), ensure_ascii=False, sort_keys=True) + "\n")
    return path
