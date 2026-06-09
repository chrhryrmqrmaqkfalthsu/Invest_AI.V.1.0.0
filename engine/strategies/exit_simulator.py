"""ExitPolicy 기반 청산 시뮬레이터."""
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

import pandas as pd

from engine.core.exit_policy import (
    ExitExecutionConfig,
    MarketContext,
    PriceSnapshot,
    apply_hard_stop_guard,
    evaluate_exit,
    initialize_position_state,
    update_position_for_add_buy,
)
from engine.core.metadata import compute_rulebook_hash
from engine.strategies.rulebook import Rulebook


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    entry_shares: float
    exit_date: str
    exit_price: float
    exit_reason: str
    holding_days: int
    add_buys: list = field(default_factory=list)
    total_shares: float = 0.0
    avg_cost: float = 0.0
    pnl_pct: float = 0.0
    pnl_krw: float = 0.0
    commission: float = 0.0
    trigger_price: Optional[float] = None
    fill_price_base: Optional[float] = None
    fill_price_stress: Optional[float] = None
    stress_pnl_pct: Optional[float] = None
    stress_pnl_krw: Optional[float] = None
    max_profit_during_hold: Optional[float] = None
    max_loss_during_hold: Optional[float] = None
    entry_market_score: Optional[float] = None
    entry_vix_level: Optional[float] = None
    entry_sector_score: Optional[float] = None
    entry_atr: Optional[float] = None
    stop_price_at_entry: Optional[float] = None
    target_price_at_entry: Optional[float] = None
    trailing_stop_at_entry: Optional[float] = None
    trailing_distance_at_entry: Optional[float] = None
    trailing_activation_profit_pct: Optional[float] = None
    breakeven_enabled: Optional[bool] = None
    breakeven_trigger_profit_pct: Optional[float] = None
    breakeven_floor_profit_pct: Optional[float] = None
    sell_omen_enabled: Optional[bool] = None
    sell_omen_score: Optional[float] = None
    sell_omen_threshold: Optional[float] = None
    exit_strategy: Optional[str] = None
    rulebook_hash: Optional[str] = None
    member_hash: Optional[str] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _row_date(row) -> str:
    return str(row.name.date()) if hasattr(row.name, "date") else str(row.name)


def _make_price_snapshot(df: pd.DataFrame, i: int) -> PriceSnapshot:
    row = df.iloc[i]
    next_row = df.iloc[i + 1] if i + 1 < len(df) else None
    return PriceSnapshot(
        date=_row_date(row),
        open=_safe_float(row.get("Open", row.get("Close"))),
        high=_safe_float(row.get("High", row.get("Close"))),
        low=_safe_float(row.get("Low", row.get("Close"))),
        close=_safe_float(row.get("Close")),
        next_open=_safe_float(next_row.get("Open", next_row.get("Close"))) if next_row is not None else None,
    )


def _rulebook_breakeven_enabled(rb: Rulebook) -> bool:
    raw = getattr(rb, "breakeven_enabled", None)
    if raw is not None:
        return bool(raw)
    return float(getattr(rb, "breakeven_trigger_profit_pct", 0.0) or 0.0) > 0.0


def _rulebook_sell_omen_enabled(rb: Rulebook) -> bool:
    return bool(getattr(rb, "sell_omen_enabled", False))


def _rulebook_sell_omen_threshold(rb: Rulebook) -> float:
    value = _safe_float(getattr(rb, "sell_omen_threshold", 1.0))
    if value is None:
        return 1.0
    return max(0.0, min(1.0, float(value)))


def _entry_context(rb: Rulebook, position, market_score: float, vix_level: float, sector_score: float) -> dict:
    breakeven_enabled = _rulebook_breakeven_enabled(rb)
    sell_omen_enabled = _rulebook_sell_omen_enabled(rb)
    return {
        "entry_market_score": float(market_score),
        "entry_vix_level": float(vix_level),
        "entry_sector_score": float(sector_score),
        "entry_atr": float(position.atr_at_entry),
        "stop_price_at_entry": float(position.stop_price),
        "target_price_at_entry": float(position.target_price),
        "trailing_stop_at_entry": float(position.trailing_stop),
        "trailing_distance_at_entry": float(position.trailing_distance),
        "trailing_activation_profit_pct": float(getattr(rb, "trailing_activation_profit_pct", 0.0) or 0.0),
        "breakeven_enabled": bool(breakeven_enabled),
        "breakeven_trigger_profit_pct": float(getattr(rb, "breakeven_trigger_profit_pct", 0.0) or 0.0) if breakeven_enabled else 0.0,
        "breakeven_floor_profit_pct": float(getattr(rb, "breakeven_floor_profit_pct", 0.0) or 0.0) if breakeven_enabled else 0.0,
        "sell_omen_enabled": bool(sell_omen_enabled),
        "sell_omen_threshold": _rulebook_sell_omen_threshold(rb) if sell_omen_enabled else 1.0,
        "exit_strategy": str(position.exit_strategy),
        "rulebook_hash": compute_rulebook_hash(rb),
        "member_hash": str(position.member_hash or ""),
    }


def _normalize_date_key(value: Any) -> str:
    try:
        return str(pd.Timestamp(value).date())
    except Exception:
        return str(value)


def _lookup_from_mapping(source: Mapping, ticker: str, date_key: str) -> Optional[float]:
    if ticker in source and isinstance(source.get(ticker), Mapping):
        nested = source.get(ticker) or {}
        return _safe_float(nested.get(date_key))
    return _safe_float(source.get(date_key))


def _lookup_sell_omen_score(source: Any, ticker: str, snap: PriceSnapshot, row: Any) -> Optional[float]:
    if row is not None:
        try:
            row_score = row.get("sell_omen_score", None)
        except Exception:
            row_score = None
        score = _safe_float(row_score)
        if score is not None:
            return max(0.0, min(1.0, score))

    if source is None:
        return None

    date_key = _normalize_date_key(snap.date)
    score: Optional[float] = None
    if callable(source):
        try:
            score = _safe_float(source(ticker, date_key))
        except TypeError:
            score = _safe_float(source(date_key))
        except Exception:
            score = None
    elif isinstance(source, Mapping):
        score = _lookup_from_mapping(source, ticker, date_key)
    elif isinstance(source, pd.Series):
        try:
            score = _safe_float(source.loc[pd.Timestamp(date_key)])
        except Exception:
            try:
                score = _safe_float(source.loc[date_key])
            except Exception:
                score = None
    elif isinstance(source, pd.DataFrame):
        try:
            subset = source
            if "ticker" in subset.columns:
                subset = subset[subset["ticker"].astype(str) == str(ticker)]
            date_col = "Date" if "Date" in subset.columns else ("date" if "date" in subset.columns else None)
            if date_col is not None and "sell_omen_score" in subset.columns:
                dates = pd.to_datetime(subset[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
                matched = subset.loc[dates == date_key, "sell_omen_score"]
                if not matched.empty:
                    score = _safe_float(matched.iloc[-1])
        except Exception:
            score = None

    if score is None:
        return None
    return max(0.0, min(1.0, float(score)))


def _build_trade(
    *,
    entry_date: str,
    entry_price: float,
    entry_shares: float,
    exit_date,
    exit_price: float,
    exit_reason: str,
    holding_days: int,
    add_buys: list,
    total_shares: float,
    avg_cost: float,
    commission_rate: float,
    trigger_price: Optional[float],
    fill_price_base: Optional[float],
    fill_price_stress: Optional[float],
    ctx: dict,
    max_profit_during_hold: float,
    max_loss_during_hold: float,
    sell_omen_score: Optional[float] = None,
) -> Trade:
    commission = (avg_cost * total_shares + exit_price * total_shares) * (commission_rate / 2)
    pnl_krw = (exit_price - avg_cost) * total_shares - commission
    pnl_pct = pnl_krw / (avg_cost * total_shares) * 100 if avg_cost > 0 and total_shares > 0 else 0.0
    stress_pnl_krw = None
    stress_pnl_pct = None
    if fill_price_stress is not None and avg_cost > 0 and total_shares > 0:
        stress_commission = (avg_cost * total_shares + fill_price_stress * total_shares) * (commission_rate / 2)
        stress_pnl_krw = (fill_price_stress - avg_cost) * total_shares - stress_commission
        stress_pnl_pct = stress_pnl_krw / (avg_cost * total_shares) * 100

    return Trade(
        entry_date=entry_date,
        entry_price=entry_price,
        entry_shares=entry_shares,
        exit_date=str(exit_date.date()),
        exit_price=exit_price,
        exit_reason=exit_reason,
        holding_days=holding_days,
        add_buys=add_buys,
        total_shares=total_shares,
        avg_cost=avg_cost,
        pnl_pct=pnl_pct,
        pnl_krw=pnl_krw,
        commission=commission,
        trigger_price=trigger_price,
        fill_price_base=fill_price_base if fill_price_base is not None else exit_price,
        fill_price_stress=fill_price_stress,
        stress_pnl_pct=stress_pnl_pct,
        stress_pnl_krw=stress_pnl_krw,
        max_profit_during_hold=max_profit_during_hold,
        max_loss_during_hold=max_loss_during_hold,
        entry_market_score=ctx.get("entry_market_score"),
        entry_vix_level=ctx.get("entry_vix_level"),
        entry_sector_score=ctx.get("entry_sector_score"),
        entry_atr=ctx.get("entry_atr"),
        stop_price_at_entry=ctx.get("stop_price_at_entry"),
        target_price_at_entry=ctx.get("target_price_at_entry"),
        trailing_stop_at_entry=ctx.get("trailing_stop_at_entry"),
        trailing_distance_at_entry=ctx.get("trailing_distance_at_entry"),
        trailing_activation_profit_pct=ctx.get("trailing_activation_profit_pct"),
        breakeven_enabled=ctx.get("breakeven_enabled"),
        breakeven_trigger_profit_pct=ctx.get("breakeven_trigger_profit_pct"),
        breakeven_floor_profit_pct=ctx.get("breakeven_floor_profit_pct"),
        sell_omen_enabled=ctx.get("sell_omen_enabled"),
        sell_omen_score=sell_omen_score,
        sell_omen_threshold=ctx.get("sell_omen_threshold"),
        exit_strategy=ctx.get("exit_strategy"),
        rulebook_hash=ctx.get("rulebook_hash"),
        member_hash=ctx.get("member_hash"),
    )


def simulate_exit(
    rb: Rulebook,
    df: pd.DataFrame,
    entry_idx: int,
    initial_shares: float,
    initial_budget_krw: float,
    commission_rate: float = 0.0005,
    cur_market_score: float = 50.0,
    cur_vix_level: float = 18.0,
    cur_sector_score: float = 50.0,
    sell_omen_scores: Any = None,
    fractional_shares: bool = False,
    disable_add_buy: bool = False,
    live_hard_stop_guard: bool = False,
) -> Optional[Trade]:
    if entry_idx + 1 >= len(df):
        return None
    if str(getattr(rb, "direction", "long") or "long").lower() != "long":
        raise NotImplementedError("ExitPolicy cutover supports long-only backtests; short/inverse is deferred.")

    entry_row = df.iloc[entry_idx]
    entry_price = float(entry_row["Close"])
    if entry_price <= 0 or pd.isna(entry_price):
        return None
    atr = float(entry_row.get("ATR", entry_price * 0.02))
    if pd.isna(atr) or atr <= 0:
        atr = entry_price * 0.02

    entry_date = str(df.index[entry_idx].date())
    mctx = MarketContext(market_score=cur_market_score, vix_level=cur_vix_level, sector_score=cur_sector_score)
    breakeven_enabled = _rulebook_breakeven_enabled(rb)
    sell_omen_enabled = _rulebook_sell_omen_enabled(rb)
    exec_cfg = ExitExecutionConfig(
        trailing_activation_bars=2,
        trailing_activation_profit_pct=float(getattr(rb, "trailing_activation_profit_pct", 0.0) or 0.0),
        breakeven_enabled=breakeven_enabled,
        breakeven_trigger_profit_pct=float(getattr(rb, "breakeven_trigger_profit_pct", 0.0) or 0.0) if breakeven_enabled else 0.0,
        breakeven_floor_profit_pct=float(getattr(rb, "breakeven_floor_profit_pct", 0.0) or 0.0) if breakeven_enabled else 0.0,
        sell_omen_enabled=sell_omen_enabled,
        sell_omen_threshold=_rulebook_sell_omen_threshold(rb) if sell_omen_enabled else 1.0,
    )
    position = initialize_position_state(
        ticker=str(getattr(rb, "ticker", "") or ""),
        entry_price=entry_price,
        shares=initial_shares,
        rulebook=rb,
        atr_value=atr,
        market_context=mctx,
        entry_date=entry_date,
    )
    ctx = _entry_context(rb, position, cur_market_score, cur_vix_level, cur_sector_score)

    used_krw = entry_price * initial_shares
    add_buys: list = []
    mfe = 0.0
    mae = 0.0

    for i in range(entry_idx + 1, min(entry_idx + int(rb.max_holding_days) + 1, len(df))):
        row = df.iloc[i]
        close = float(row["Close"])
        holding_days = i - entry_idx
        snap = _make_price_snapshot(df, i)

        if position.avg_cost > 0:
            if snap.high is not None:
                mfe = max(mfe, (float(snap.high) - position.avg_cost) / position.avg_cost * 100.0)
            if snap.low is not None:
                mae = min(mae, (float(snap.low) - position.avg_cost) / position.avg_cost * 100.0)

        current_pnl_pct = (close - position.avg_cost) / position.avg_cost * 100 if position.avg_cost > 0 else 0.0
        if (
            not disable_add_buy
            and rb.add_buy_enabled
            and position.add_buy_count < rb.add_buy_max_count
            and current_pnl_pct >= rb.add_buy_trigger_profit_pct
        ):
            add_budget = used_krw * rb.add_buy_size_ratio
            remaining = initial_budget_krw - used_krw
            if remaining > add_budget * 0.5:
                add_budget = min(add_budget, remaining)
                add_shares = int(add_budget / close) if close > 0 else 0
                if add_shares > 0:
                    add_buys.append((str(row.name.date()), close, add_shares))
                    used_krw += close * add_shares
                    position = update_position_for_add_buy(position, close, add_shares, rb, atr, mctx)

        bctx = MarketContext(
            market_score=cur_market_score,
            vix_level=cur_vix_level,
            sector_score=cur_sector_score,
            holding_trading_days=holding_days,
            current_trade_date=snap.date,
        )
        sell_omen_score = _lookup_sell_omen_score(sell_omen_scores, str(getattr(rb, "ticker", "") or ""), snap, row)
        day_exec_cfg = replace(exec_cfg, sell_omen_score=sell_omen_score)
        decision = evaluate_exit(position, snap, rb, bctx, day_exec_cfg)
        if live_hard_stop_guard:
            stop_price = float(position.stop_price)
            snap_open = snap.open if snap.open is not None else None
            guard_fill = float(snap_open) if snap_open is not None and float(snap_open) <= stop_price else stop_price
            decision = apply_hard_stop_guard(
                decision,
                state=position,
                probe_price=(snap.low if snap.low is not None else 0.0),
                fill_price=guard_fill,
                trigger_source="backtest_live_hard_stop_guard",
                diagnostics_prefix="bt_live_hard_stop",
            )
        if decision.updated_position is not None:
            position = decision.updated_position
        if decision.should_exit:
            exit_price = decision.fill_price_base if decision.fill_price_base is not None else decision.trigger_price
            if exit_price is None:
                exit_price = close
            return _build_trade(
                entry_date=entry_date,
                entry_price=entry_price,
                entry_shares=initial_shares,
                exit_date=row.name,
                exit_price=float(exit_price),
                exit_reason=str(decision.reason),
                holding_days=holding_days,
                add_buys=add_buys,
                total_shares=(float(position.shares) if fractional_shares else int(position.shares)),
                avg_cost=float(position.avg_cost),
                commission_rate=commission_rate,
                trigger_price=decision.trigger_price,
                fill_price_base=decision.fill_price_base,
                fill_price_stress=decision.fill_price_stress,
                ctx=ctx,
                max_profit_during_hold=mfe,
                max_loss_during_hold=mae,
                sell_omen_score=decision.diagnostics.get("sell_omen_score"),
            )

    last_idx = min(entry_idx + int(rb.max_holding_days), len(df) - 1)
    last_row = df.iloc[last_idx]
    snap = _make_price_snapshot(df, last_idx)
    sell_omen_score = _lookup_sell_omen_score(sell_omen_scores, str(getattr(rb, "ticker", "") or ""), snap, last_row)
    return _build_trade(
        entry_date=entry_date,
        entry_price=entry_price,
        entry_shares=initial_shares,
        exit_date=last_row.name,
        exit_price=float(last_row["Close"]),
        exit_reason="time_out",
        holding_days=last_idx - entry_idx,
        add_buys=add_buys,
        total_shares=(float(position.shares) if fractional_shares else int(position.shares)),
        avg_cost=float(position.avg_cost),
        commission_rate=commission_rate,
        trigger_price=float(last_row["Close"]),
        fill_price_base=float(last_row["Close"]),
        fill_price_stress=float(last_row["Close"]),
        ctx=ctx,
        max_profit_during_hold=mfe,
        max_loss_during_hold=mae,
        sell_omen_score=sell_omen_score,
    )
