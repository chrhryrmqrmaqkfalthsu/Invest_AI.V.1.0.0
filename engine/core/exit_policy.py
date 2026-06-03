"""Common ExitPolicy primitives for backtest and live execution.

Phase 1 introduces the shared decision engine only. Existing backtest/live
callers are intentionally not modified yet.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass
class MarketContext:
    """Market state used for dynamic exit parameters."""

    market_score: float = 50.0
    vix_level: float = 18.0
    sector_score: float = 50.0
    current_trade_date: Optional[str] = None
    holding_trading_days: Optional[int] = None


@dataclass
class ExitExecutionConfig:
    """Execution-price estimation settings."""

    mode: str = "base"  # "base" | "stress" | "live"
    base_slippage_bps: float = 5.0
    stress_slippage_bps: float = 25.0
    use_next_open: bool = True
    fallback_to_trigger_price: bool = True
    trailing_activation_bars: int = 2


@dataclass
class PositionState:
    """Normalized position state shared by backtest and live code."""

    ticker: str
    direction: str
    entry_date: str
    entry_price: float
    avg_cost: float
    shares: float
    atr_at_entry: float
    stop_price: float
    target_price: float
    trailing_stop: float
    trailing_distance: float
    highest_price: float
    max_holding_days: int
    exit_strategy: str
    holding_trading_days: int = 0
    add_buy_count: int = 0
    rulebook_snapshot: Dict[str, Any] = field(default_factory=dict)
    member_hash: str = ""


@dataclass
class PriceSnapshot:
    """A single OHLC bar or live current-price snapshot."""

    date: str = ""
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    current_price: Optional[float] = None
    next_open: Optional[float] = None


@dataclass
class ExitDecision:
    """ExitPolicy decision result."""

    should_exit: bool
    reason: Optional[str] = None
    trigger_price: Optional[float] = None
    fill_price_base: Optional[float] = None
    fill_price_stress: Optional[float] = None
    updated_position: Optional[PositionState] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _rulebook_snapshot(rulebook: Any) -> Dict[str, Any]:
    if rulebook is None:
        return {}
    if isinstance(rulebook, Mapping):
        return dict(rulebook)
    if dataclasses.is_dataclass(rulebook):
        try:
            return dataclasses.asdict(rulebook)
        except Exception:
            return {}
    method = getattr(rulebook, "to_dict", None)
    if callable(method):
        try:
            value = method()
            if isinstance(value, Mapping):
                return dict(value)
        except Exception:
            pass
    raw = getattr(rulebook, "__dict__", None)
    if isinstance(raw, Mapping):
        return {str(k): v for k, v in raw.items() if not str(k).startswith("_")}
    return {}


def _compute_member_hash_safe(rulebook: Any) -> str:
    try:
        from engine.core.metadata import compute_member_hash

        return compute_member_hash(rulebook)
    except Exception:
        return ""


def _assert_long_only(direction: str) -> None:
    if str(direction or "long").lower() != "long":
        raise NotImplementedError("ExitPolicy Phase 1 supports long-only positions; short/inverse is deferred.")


def resolve_exit_params(
    rulebook: Any,
    market_context: Optional[MarketContext] = None,
) -> Tuple[float, float, float]:
    """Resolve dynamic ATR multipliers for stop, target, and trailing."""
    ctx = market_context or MarketContext()

    base_sl = _to_float(_get_attr(rulebook, "stop_loss_atr", 2.0), 2.0)
    base_tp = _to_float(_get_attr(rulebook, "take_profit_atr", 3.0), 3.0)
    base_trail = _to_float(_get_attr(rulebook, "trailing_atr", 1.5), 1.5)

    sl = _to_float(_get_attr(rulebook, "stop_loss_atr_bear", base_sl), base_sl) if ctx.market_score < 40 else base_sl
    tp = _to_float(_get_attr(rulebook, "take_profit_atr_bull", base_tp), base_tp) if ctx.market_score >= 70 else base_tp
    trail = _to_float(_get_attr(rulebook, "trailing_atr_volatile", base_trail), base_trail) if ctx.vix_level > 25 else base_trail

    return float(sl), float(tp), float(trail)


def initialize_position_state(
    ticker: str,
    entry_price: float,
    shares: float,
    rulebook: Any,
    atr_value: float,
    market_context: Optional[MarketContext] = None,
    entry_date: str = "",
    member_hash: Optional[str] = None,
) -> PositionState:
    """Create a long-only PositionState from a filled entry."""
    direction = str(_get_attr(rulebook, "direction", "long") or "long").lower()
    _assert_long_only(direction)

    entry = _to_float(entry_price, 0.0)
    atr = _to_float(atr_value, max(entry * 0.02, 0.0))
    sl_atr, tp_atr, trail_atr = resolve_exit_params(rulebook, market_context)
    trail_dist = atr * trail_atr

    return PositionState(
        ticker=ticker,
        direction="long",
        entry_date=entry_date,
        entry_price=entry,
        avg_cost=entry,
        shares=_to_float(shares, 0.0),
        atr_at_entry=atr,
        stop_price=entry - atr * sl_atr,
        target_price=entry + atr * tp_atr,
        trailing_stop=entry - trail_dist,
        trailing_distance=trail_dist,
        highest_price=entry,
        max_holding_days=_to_int(_get_attr(rulebook, "max_holding_days", 20), 20),
        exit_strategy=str(_get_attr(rulebook, "exit_strategy", "hybrid") or "hybrid"),
        holding_trading_days=0,
        add_buy_count=0,
        rulebook_snapshot=_rulebook_snapshot(rulebook),
        member_hash=member_hash if member_hash is not None else _compute_member_hash_safe(rulebook),
    )


def update_position_for_add_buy(
    position: PositionState,
    add_price: float,
    add_shares: float,
    rulebook: Any,
    atr_value: float,
    market_context: Optional[MarketContext] = None,
) -> PositionState:
    """Return a new PositionState after an add-buy fill.

    This function only updates position accounting. Add-buy trigger checks and
    SafetyLayer approval belong to a separate AddBuyPolicy path.
    """
    _assert_long_only(position.direction)

    old_shares = max(_to_float(position.shares, 0.0), 0.0)
    new_add_shares = max(_to_float(add_shares, 0.0), 0.0)
    price = _to_float(add_price, position.avg_cost)
    new_shares = old_shares + new_add_shares
    new_avg = price if new_shares <= 0 else ((position.avg_cost * old_shares) + (price * new_add_shares)) / new_shares

    atr = _to_float(atr_value, position.atr_at_entry)
    sl_atr, tp_atr, trail_atr = resolve_exit_params(rulebook, market_context)
    trail_dist = atr * trail_atr
    new_trailing = new_avg - trail_dist

    return replace(
        position,
        entry_price=new_avg,
        avg_cost=new_avg,
        shares=new_shares,
        atr_at_entry=atr,
        stop_price=new_avg - atr * sl_atr,
        target_price=new_avg + atr * tp_atr,
        trailing_distance=trail_dist,
        trailing_stop=max(position.trailing_stop, new_trailing),
        highest_price=max(position.highest_price, price),
        add_buy_count=position.add_buy_count + 1,
        rulebook_snapshot=position.rulebook_snapshot or _rulebook_snapshot(rulebook),
        member_hash=position.member_hash or _compute_member_hash_safe(rulebook),
    )


def _snapshot_values(price: PriceSnapshot) -> Tuple[float, float, float, bool]:
    """Return high, low, reference close/current, and whether OHLC is available."""
    has_ohlc = price.high is not None or price.low is not None
    ref = price.current_price
    if ref is None:
        ref = price.close
    if ref is None:
        ref = price.open
    ref_value = _to_float(ref, 0.0)

    high = _to_float(price.high, ref_value)
    low = _to_float(price.low, ref_value)
    if price.current_price is not None and not has_ohlc:
        high = low = ref_value
    return high, low, ref_value, has_ohlc


def estimate_exit_fills(
    trigger_price: float,
    price: PriceSnapshot,
    execution_config: Optional[ExitExecutionConfig] = None,
    direction: str = "long",
) -> Tuple[float, float]:
    """Estimate base/stress exit fills from a trigger price.

    Phase 1 supports long exits only. For a long sell, slippage lowers fill.
    """
    _assert_long_only(direction)
    cfg = execution_config or ExitExecutionConfig()

    basis = None
    if cfg.use_next_open and price.next_open is not None:
        basis = price.next_open
    if basis is None and cfg.fallback_to_trigger_price:
        basis = trigger_price
    if basis is None:
        basis = trigger_price

    basis_value = _to_float(basis, trigger_price)
    base = basis_value * (1.0 - cfg.base_slippage_bps / 10000.0)
    stress = basis_value * (1.0 - cfg.stress_slippage_bps / 10000.0)
    return float(base), float(stress)


def evaluate_exit(
    position: PositionState,
    price: PriceSnapshot,
    rulebook: Any,
    market_context: Optional[MarketContext] = None,
    execution_config: Optional[ExitExecutionConfig] = None,
) -> ExitDecision:
    """Evaluate whether a long position should exit at this point.

    OHLC snapshots use intraday high/low touch semantics. Live snapshots with
    only ``current_price`` use polling semantics. The policy order is shared.
    """
    cfg = execution_config or ExitExecutionConfig()
    ctx = market_context or MarketContext()
    _assert_long_only(position.direction)
    _assert_long_only(str(_get_attr(rulebook, "direction", position.direction) or position.direction))

    high, low, ref_price, has_ohlc = _snapshot_values(price)
    holding_days = ctx.holding_trading_days if ctx.holding_trading_days is not None else position.holding_trading_days
    holding_days = _to_int(holding_days, position.holding_trading_days)

    highest = max(position.highest_price, high, ref_price)
    updated_trailing = position.trailing_stop
    if highest > position.highest_price:
        updated_trailing = max(position.trailing_stop, highest - position.trailing_distance)

    updated_position = replace(
        position,
        highest_price=highest,
        trailing_stop=updated_trailing,
        holding_trading_days=holding_days,
    )

    strategy = str(position.exit_strategy or _get_attr(rulebook, "exit_strategy", "hybrid") or "hybrid").lower()
    trailing_active = holding_days > cfg.trailing_activation_bars

    diagnostics: Dict[str, Any] = {
        "strategy": strategy,
        "high": high,
        "low": low,
        "reference_price": ref_price,
        "has_ohlc": has_ohlc,
        "holding_trading_days": holding_days,
        "trailing_active": trailing_active,
        "trailing_activation_bars": cfg.trailing_activation_bars,
        "stop_price": position.stop_price,
        "target_price": position.target_price,
        "trailing_stop": updated_trailing,
    }

    stop_hit = low <= position.stop_price
    target_hit = high >= position.target_price
    trailing_hit = trailing_active and low <= updated_trailing
    timeout_hit = holding_days >= position.max_holding_days

    reason: Optional[str] = None
    trigger_price: Optional[float] = None

    if strategy == "fixed":
        if stop_hit:
            reason, trigger_price = "stop_loss", position.stop_price
        elif target_hit:
            reason, trigger_price = "take_profit", position.target_price
        elif timeout_hit:
            reason, trigger_price = "time_out", ref_price
    elif strategy == "trailing":
        if trailing_hit:
            reason, trigger_price = "trailing", updated_trailing
        elif timeout_hit:
            reason, trigger_price = "time_out", ref_price
    elif strategy == "hybrid":
        # 확정 우선순위: stop_loss → trailing_stop → take_profit → max_holding.
        if stop_hit:
            reason, trigger_price = "stop_loss", position.stop_price
        elif trailing_hit:
            reason, trigger_price = "trailing", updated_trailing
        elif target_hit:
            reason, trigger_price = "take_profit", position.target_price
        elif timeout_hit:
            reason, trigger_price = "time_out", ref_price
    else:
        diagnostics["warning"] = f"unknown exit_strategy: {strategy}"

    if reason is None or trigger_price is None:
        return ExitDecision(
            should_exit=False,
            updated_position=updated_position,
            diagnostics=diagnostics,
        )

    fill_base, fill_stress = estimate_exit_fills(trigger_price, price, cfg, direction=position.direction)
    diagnostics.update(
        {
            "stop_hit": stop_hit,
            "target_hit": target_hit,
            "trailing_hit": trailing_hit,
            "timeout_hit": timeout_hit,
        }
    )

    return ExitDecision(
        should_exit=True,
        reason=reason,
        trigger_price=float(trigger_price),
        fill_price_base=fill_base,
        fill_price_stress=fill_stress,
        updated_position=updated_position,
        diagnostics=diagnostics,
    )
