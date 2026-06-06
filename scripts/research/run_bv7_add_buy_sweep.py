#!/usr/bin/env python3
"""BV-7 re-signal pyramiding / add-buy sweep experiment.

Read-only experiment: promoted rulebooks are never modified.
Base virtual rulebook is BV-6 efficient candidate:
- sizing fixed at 2.0x base_position_ratio, capped at 1.0
- take_profit_atr / take_profit_atr_bull × 1.5
- trailing_atr / trailing_atr_volatile × 1.5
- max_holding_days unchanged

Unlike the production simulator's legacy add-buy trigger, this research script
adds only when a fresh buy signal reappears while the same ticker is already held.
Writes only under data/_system/research/bv7_20260607/.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.core.feature_lag import DEFAULT_LAG_DAYS, DEFAULT_MAX_AGE_DAYS, lookup_lagged_daily_dict, lookup_market_at_lagged
from engine.core.exit_policy import (
    ExitExecutionConfig,
    MarketContext,
    initialize_position_state,
    update_position_for_add_buy,
    evaluate_exit,
)
from engine.learning.backtest import FEATURE_LAG_DAYS, FEATURE_LAG_MAX_AGE_DAYS
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.evaluator import calc_position_size_krw, evaluate_signal
from engine.strategies.exit_simulator import _build_trade, _make_price_snapshot
from engine.strategies.rulebook import Rulebook
from scripts.research.run_bv1_lift import (
    COMMISSION,
    DATA_END,
    DATA_YEARS,
    POSITION_LIMIT,
    WARMUP,
    load_rulebook,
    safe_float,
)
from scripts.research.run_bv2_risk_lift import STRESS_COMMISSION, STRESS_SLIPPAGE, risk_metrics, summarize
from scripts.research.run_bv5_sizing_sweep import FULL_CAPACITY, quant, write_jsonl
from scripts.research.run_bv6_holding_sweep import exit_reason_breakdown

OUT = Path("data/_system/research/bv7_20260607")
YEARS_DEFAULT = [2022, 2023, 2024, 2025]
SIZING_FACTOR = 2.0
TP_FACTOR = 1.5
TRAILING_FACTOR = 1.5

STAGES_DEFAULT = [
    {
        "stage": "none",
        "label": "no_add_buy",
        "add_buy_enabled": False,
        "add_buy_trigger_profit_pct": 999.0,
        "add_buy_max_count": 0,
        "add_buy_size_ratio": 0.0,
        "add_buy_min_signal_score": 999.0,
    },
    {
        "stage": "conservative",
        "label": "max1_size25_trigger1_score3",
        "add_buy_enabled": True,
        "add_buy_trigger_profit_pct": 1.0,
        "add_buy_max_count": 1,
        "add_buy_size_ratio": 0.25,
        "add_buy_min_signal_score": 3.0,
    },
    {
        "stage": "medium",
        "label": "max2_size40_trigger0_75_score2_5",
        "add_buy_enabled": True,
        "add_buy_trigger_profit_pct": 0.75,
        "add_buy_max_count": 2,
        "add_buy_size_ratio": 0.40,
        "add_buy_min_signal_score": 2.5,
    },
    {
        "stage": "aggressive",
        "label": "max3_size60_trigger0_5_score2",
        "add_buy_enabled": True,
        "add_buy_trigger_profit_pct": 0.5,
        "add_buy_max_count": 3,
        "add_buy_size_ratio": 0.60,
        "add_buy_min_signal_score": 2.0,
    },
]


def parse_stage_filter(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return list(STAGES_DEFAULT)
    wanted = {x.strip() for x in raw.split(",") if x.strip()}
    stages = [s for s in STAGES_DEFAULT if s["stage"] in wanted]
    if not stages:
        raise SystemExit(f"No matching stages in {sorted(wanted)}")
    return stages


def make_base_rulebook(rb: Rulebook) -> Rulebook:
    data = rb.to_dict()
    base = safe_float(data.get("base_position_ratio"), 1.0)
    data["base_position_ratio"] = max(0.0, min(base * SIZING_FACTOR, 1.0))
    for key in ("take_profit_atr", "take_profit_atr_bull"):
        data[key] = max(0.01, safe_float(data.get(key), 0.0) * TP_FACTOR)
    for key in ("trailing_atr", "trailing_atr_volatile"):
        data[key] = max(0.01, safe_float(data.get(key), 0.0) * TRAILING_FACTOR)
    # Disable production add-buy path semantics. This script applies its own re-signal policy.
    data["add_buy_enabled"] = False
    data["add_buy_max_count"] = 0
    return Rulebook.from_dict(data)


def row_date(df: pd.DataFrame, i: int) -> str:
    return str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i])


def market_context_for_bar(rb: Rulebook, df: pd.DataFrame, i: int, market_history: pd.DataFrame) -> tuple[float, float, float, dict[str, int]]:
    mkt = lookup_market_at_lagged(market_history, df.index[i], lag_days=DEFAULT_LAG_DAYS)
    cur_market = float(mkt.get("score", 50.0))
    cur_sector = float(mkt.get(f"sector_{getattr(rb, 'sector_name', 'tech') or 'tech'}", 50.0))
    cur_vix = float(mkt.get("vix", 18.0))
    flags: dict[str, int] = {}
    for key in (
        "has_war", "has_rate_hike", "has_rate_cut", "has_geopolitical",
        "has_tariff", "has_export_ban", "has_earnings_shock", "has_oil_surge",
        "has_banking_crisis", "has_inflation", "has_fed_statement",
    ):
        flags[key] = int(mkt.get(key, 0) or 0)
    return cur_market, cur_sector, cur_vix, flags


def sentiment_for_bar(df: pd.DataFrame, i: int, ticker_sentiment: dict | None) -> float:
    if not ticker_sentiment:
        return 0.0
    try:
        s = lookup_lagged_daily_dict(
            ticker_sentiment,
            df.index[i],
            lag_days=DEFAULT_LAG_DAYS,
            max_age_days=DEFAULT_MAX_AGE_DAYS,
        )
        return float(s.get("sentiment_avg", 0.0)) if s else 0.0
    except Exception:
        return 0.0


def signal_for_bar(rb: Rulebook, df: pd.DataFrame, i: int, market_history: pd.DataFrame, ticker_sentiment: dict | None):
    cur_market, cur_sector, cur_vix, flags = market_context_for_bar(rb, df, i, market_history)
    cur_sentiment = sentiment_for_bar(df, i, ticker_sentiment)
    sig = evaluate_signal(
        rb,
        df.iloc[: i + 1],
        market_score=cur_market,
        sector_score=cur_sector,
        vix_level=cur_vix,
        news_sentiment=cur_sentiment,
        event_flags=flags,
    )
    return sig, cur_market, cur_sector, cur_vix, flags, cur_sentiment


def trade_to_row(
    ticker: str,
    year: int,
    stage: dict[str, Any],
    member_hash: str,
    trade_obj: Any,
    entry_signal_score: float,
    add_signal_count: int,
    add_signal_rejected_cap: int,
    add_signal_rejected_conditions: int,
) -> dict[str, Any]:
    d = asdict(trade_obj) if hasattr(trade_obj, "__dataclass_fields__") else dict(trade_obj)
    add_buys = d.get("add_buys") or []
    add_notional = 0.0
    normalized_adds = []
    for item in add_buys:
        if isinstance(item, dict):
            date = item.get("date")
            price = safe_float(item.get("price"))
            shares = int(item.get("shares") or 0)
            score = safe_float(item.get("signal_score"))
            pnl = safe_float(item.get("current_pnl_pct"))
        else:
            date, price, shares, *rest = item
            price = safe_float(price)
            shares = int(shares or 0)
            score = safe_float(rest[0]) if len(rest) > 0 else 0.0
            pnl = safe_float(rest[1]) if len(rest) > 1 else 0.0
        notional = price * shares
        add_notional += notional
        normalized_adds.append({
            "date": str(date),
            "price": price,
            "shares": shares,
            "notional": notional,
            "signal_score": score,
            "current_pnl_pct": pnl,
        })
    entry_notional = safe_float(d.get("entry_price")) * int(d.get("entry_shares") or 0)
    total_invested = entry_notional + add_notional
    total_shares = int(d.get("total_shares") or d.get("entry_shares") or 0)
    avg_cost = safe_float(d.get("avg_cost"), safe_float(d.get("entry_price")))
    return {
        "ticker": ticker,
        "year": year,
        "baseline": f"bv7_{stage['stage']}",
        "stage": stage["stage"],
        "stage_label": stage["label"],
        "member_hash": member_hash,
        "entry_date": d.get("entry_date"),
        "entry_price": safe_float(d.get("entry_price")),
        "entry_shares": int(d.get("entry_shares") or 0),
        "entry_signal_score": entry_signal_score,
        "exit_date": d.get("exit_date"),
        "exit_price": safe_float(d.get("exit_price")),
        "exit_reason": d.get("exit_reason"),
        "holding_days": int(d.get("holding_days") or 0),
        "total_shares": total_shares,
        "avg_cost": avg_cost,
        "pnl_pct": safe_float(d.get("pnl_pct")),
        "pnl_krw": safe_float(d.get("pnl_krw")),
        "commission": safe_float(d.get("commission")),
        "add_buys": normalized_adds,
        "add_buy_count": len(normalized_adds),
        "add_buy_notional": add_notional,
        "initial_notional": entry_notional,
        "total_invested_notional": total_invested,
        "max_position_notional": avg_cost * total_shares,
        "add_signal_count": add_signal_count,
        "add_signal_rejected_cap": add_signal_rejected_cap,
        "add_signal_rejected_conditions": add_signal_rejected_conditions,
        "sizing_factor": SIZING_FACTOR,
        "tp_factor": TP_FACTOR,
        "trailing_factor": TRAILING_FACTOR,
        "add_buy_enabled": bool(stage["add_buy_enabled"]),
        "add_buy_trigger_profit_pct": safe_float(stage["add_buy_trigger_profit_pct"]),
        "add_buy_max_count": int(stage["add_buy_max_count"]),
        "add_buy_size_ratio": safe_float(stage["add_buy_size_ratio"]),
        "add_buy_min_signal_score": safe_float(stage["add_buy_min_signal_score"]),
    }


def build_trade_from_position(
    rb: Rulebook,
    df: pd.DataFrame,
    entry_idx: int,
    exit_idx: int,
    exit_price: float,
    exit_reason: str,
    initial_shares: int,
    position,
    add_buys: list[dict[str, Any]],
    commission_rate: float,
    trigger_price: float | None = None,
    fill_price_stress: float | None = None,
):
    return _build_trade(
        str(df.index[entry_idx].date()),
        float(df.iloc[entry_idx]["Close"]),
        initial_shares,
        df.index[exit_idx],
        float(exit_price),
        exit_reason,
        int(exit_idx - entry_idx),
        add_buys,
        int(position.shares),
        float(position.avg_cost),
        False,
        commission_rate,
        trigger_price=trigger_price,
        fill_price_base=exit_price,
        fill_price_stress=fill_price_stress,
        entry_context={
            "entry_market_score": None,
            "entry_vix_level": None,
            "entry_sector_score": None,
            "entry_atr": float(position.atr_at_entry),
            "stop_price_at_entry": None,
            "target_price_at_entry": None,
            "trailing_stop_at_entry": None,
            "trailing_distance_at_entry": None,
            "exit_strategy": str(getattr(rb, "exit_strategy", "hybrid")),
            "rulebook_hash": None,
            "member_hash": str(position.member_hash or ""),
        },
    )


def simulate_resignal_trade(
    ticker: str,
    year: int,
    rb: Rulebook,
    df: pd.DataFrame,
    entry_idx: int,
    stage: dict[str, Any],
    market_history: pd.DataFrame,
    ticker_sentiment: dict | None,
    member_hash: str,
) -> tuple[Any | None, list[dict[str, Any]], int, int, int]:
    entry_price = float(df.iloc[entry_idx]["Close"])
    if entry_price <= 0 or pd.isna(entry_price):
        return None, [], 0, 0, 0
    sig, entry_market, entry_sector, entry_vix, _, _ = signal_for_bar(rb, df, entry_idx, market_history, ticker_sentiment)
    amt_krw = calc_position_size_krw(rb, sig.score, POSITION_LIMIT)
    initial_shares = int(amt_krw / entry_price) if entry_price > 0 else 0
    if initial_shares <= 0:
        return None, [], 0, 0, 0

    atr = float(df.iloc[entry_idx].get("ATR", entry_price * 0.02))
    if pd.isna(atr) or atr <= 0:
        atr = entry_price * 0.02

    entry_ctx = MarketContext(market_score=entry_market, vix_level=entry_vix, sector_score=entry_sector)
    position = initialize_position_state(
        ticker=ticker,
        entry_price=entry_price,
        shares=initial_shares,
        rulebook=rb,
        atr_value=atr,
        market_context=entry_ctx,
        entry_date=str(df.index[entry_idx].date()),
        member_hash=member_hash,
    )
    execution_config = ExitExecutionConfig(trailing_activation_bars=2)
    used_krw = entry_price * initial_shares
    add_buys: list[dict[str, Any]] = []
    resignal_rows: list[dict[str, Any]] = []
    add_signal_count = 0
    rejected_cap = 0
    rejected_conditions = 0

    last_i = min(entry_idx + int(getattr(rb, "max_holding_days", 20) or 20), len(df) - 1)
    for i in range(entry_idx + 1, last_i + 1):
        close = float(df.iloc[i]["Close"])
        holding_days = i - entry_idx
        sig_i, cur_market, cur_sector, cur_vix, _, _ = signal_for_bar(rb, df, i, market_history, ticker_sentiment)
        current_pnl_pct = (close - float(position.avg_cost)) / float(position.avg_cost) * 100.0 if float(position.avg_cost) > 0 else 0.0
        signal_is_rebuy = bool(sig_i.should_buy)
        if signal_is_rebuy:
            future_to_parent_unknown = None
            resignal_rows.append({
                "ticker": ticker,
                "year": year,
                "date": str(df.index[i].date()),
                "entry_date": str(df.index[entry_idx].date()),
                "holding_days": holding_days,
                "signal_score": float(sig_i.score),
                "threshold": float(sig_i.threshold),
                "price": close,
                "position_avg_cost": float(position.avg_cost),
                "current_pnl_pct": current_pnl_pct,
                "eligible_for_stage": bool(
                    stage["add_buy_enabled"]
                    and position.add_buy_count < int(stage["add_buy_max_count"])
                    and current_pnl_pct >= safe_float(stage["add_buy_trigger_profit_pct"])
                    and float(sig_i.score) >= safe_float(stage["add_buy_min_signal_score"])
                ),
                "future_to_parent_exit_pct": future_to_parent_unknown,
            })

        if bool(stage["add_buy_enabled"]) and signal_is_rebuy:
            if (
                position.add_buy_count < int(stage["add_buy_max_count"])
                and current_pnl_pct >= safe_float(stage["add_buy_trigger_profit_pct"])
                and float(sig_i.score) >= safe_float(stage["add_buy_min_signal_score"])
            ):
                add_signal_count += 1
                remaining = max(0.0, POSITION_LIMIT - used_krw)
                add_budget = min(used_krw * safe_float(stage["add_buy_size_ratio"]), remaining)
                add_shares = int(add_budget / close) if close > 0 else 0
                if remaining <= 0 or add_shares <= 0:
                    rejected_cap += 1
                else:
                    add_notional = add_shares * close
                    add_buys.append({
                        "date": str(df.index[i].date()),
                        "price": close,
                        "shares": add_shares,
                        "signal_score": float(sig_i.score),
                        "current_pnl_pct": current_pnl_pct,
                        "notional": add_notional,
                    })
                    used_krw += add_notional
                    position = update_position_for_add_buy(
                        position,
                        add_price=close,
                        add_shares=add_shares,
                        rulebook=rb,
                        atr_value=atr,
                        market_context=entry_ctx,
                    )
            else:
                rejected_conditions += 1

        price_snapshot = _make_price_snapshot(df, i)
        bar_ctx = MarketContext(
            market_score=entry_market,
            vix_level=entry_vix,
            sector_score=entry_sector,
            holding_trading_days=holding_days,
            current_trade_date=price_snapshot.date,
        )
        decision = evaluate_exit(position, price_snapshot, rb, bar_ctx, execution_config)
        if decision.updated_position is not None:
            position = decision.updated_position
        if decision.should_exit:
            exit_price = decision.fill_price_base if decision.fill_price_base is not None else decision.trigger_price
            if exit_price is None:
                exit_price = close
            trade = build_trade_from_position(
                rb,
                df,
                entry_idx,
                i,
                float(exit_price),
                str(decision.reason),
                initial_shares,
                position,
                add_buys,
                COMMISSION,
                trigger_price=decision.trigger_price,
                fill_price_stress=decision.fill_price_stress,
            )
            exit_price_for_resignal = float(exit_price)
            for rr in resignal_rows:
                rr["parent_exit_date"] = str(df.index[i].date())
                rr["parent_exit_reason"] = str(decision.reason)
                rr["parent_exit_price"] = exit_price_for_resignal
                rr["future_to_parent_exit_pct"] = (exit_price_for_resignal - safe_float(rr["price"])) / safe_float(rr["price"]) * 100.0 if safe_float(rr["price"]) else 0.0
            return trade, resignal_rows, add_signal_count, rejected_cap, rejected_conditions

    exit_i = last_i
    exit_price = float(df.iloc[exit_i]["Close"])
    trade = build_trade_from_position(
        rb,
        df,
        entry_idx,
        exit_i,
        exit_price,
        "time_out",
        initial_shares,
        position,
        add_buys,
        COMMISSION,
        trigger_price=exit_price,
        fill_price_stress=exit_price,
    )
    for rr in resignal_rows:
        rr["parent_exit_date"] = str(df.index[exit_i].date())
        rr["parent_exit_reason"] = "time_out"
        rr["parent_exit_price"] = exit_price
        rr["future_to_parent_exit_pct"] = (exit_price - safe_float(rr["price"])) / safe_float(rr["price"]) * 100.0 if safe_float(rr["price"]) else 0.0
    return trade, resignal_rows, add_signal_count, rejected_cap, rejected_conditions


def run_stage_for_ticker_year(
    ticker: str,
    year: int,
    rb: Rulebook,
    df: pd.DataFrame,
    market_history: pd.DataFrame,
    ticker_sentiment: dict | None,
    member_hash: str,
    stage: dict[str, Any],
    collect_resignals: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    resignals: list[dict[str, Any]] = []
    start_ts = pd.Timestamp(f"{year}-01-01")
    end_ts = pd.Timestamp(f"{year}-12-31")
    i = max(WARMUP, 0)
    n = len(df)
    while i < n:
        cur_ts = pd.Timestamp(df.index[i])
        if cur_ts < start_ts:
            i += 1
            continue
        if cur_ts > end_ts:
            break
        sig, _, _, _, _, _ = signal_for_bar(rb, df, i, market_history, ticker_sentiment)
        if not sig.should_buy:
            i += 1
            continue
        trade_obj, rs, add_signal_count, rejected_cap, rejected_conditions = simulate_resignal_trade(
            ticker,
            year,
            rb,
            df,
            i,
            stage,
            market_history,
            ticker_sentiment,
            member_hash,
        )
        if trade_obj is None:
            i += 1
            continue
        row = trade_to_row(
            ticker,
            year,
            stage,
            member_hash,
            trade_obj,
            entry_signal_score=float(sig.score),
            add_signal_count=add_signal_count,
            add_signal_rejected_cap=rejected_cap,
            add_signal_rejected_conditions=rejected_conditions,
        )
        trades.append(row)
        if collect_resignals:
            for rr in rs:
                rr["stage"] = stage["stage"]
                rr["stage_label"] = stage["label"]
                resignals.append(rr)
        exit_date = row.get("exit_date")
        try:
            exit_idx = df.index.get_loc(pd.Timestamp(exit_date))
            if isinstance(exit_idx, slice):
                exit_idx = exit_idx.start
        except Exception:
            exit_idx = i + 1
        i = max(int(exit_idx) + 2, i + 1)  # exit_idx + 1 + cooldown_days(1)
    return trades, resignals


def stress_trade_bv7(row: dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    entry_notional = safe_float(r.get("initial_notional"))
    add_notional = safe_float(r.get("add_buy_notional"))
    exit_notional = abs(safe_float(r.get("exit_price")) * safe_float(r.get("total_shares")))
    turnover = entry_notional + add_notional + exit_notional
    already_commission = safe_float(r.get("commission"))
    stress_cost = turnover * (STRESS_COMMISSION + STRESS_SLIPPAGE)
    extra_cost = max(0.0, stress_cost - already_commission)
    r["pnl_krw"] = safe_float(r.get("pnl_krw")) - extra_cost
    invested = safe_float(r.get("total_invested_notional"), entry_notional + add_notional)
    r["pnl_pct"] = r["pnl_krw"] / invested * 100.0 if invested else 0.0
    r["stress_extra_cost"] = extra_cost
    return r


def invested_notional(row: dict[str, Any]) -> float:
    v = safe_float(row.get("total_invested_notional"))
    if v > 0:
        return v
    return abs(safe_float(row.get("entry_price")) * safe_float(row.get("entry_shares")))


def position_notional_on(row: dict[str, Any], cur_date: pd.Timestamp) -> float:
    total = abs(safe_float(row.get("entry_price")) * safe_float(row.get("entry_shares")))
    for add in row.get("add_buys") or []:
        try:
            if pd.Timestamp(str(add.get("date"))[:10]) <= cur_date:
                total += safe_float(add.get("notional"), safe_float(add.get("price")) * safe_float(add.get("shares")))
        except Exception:
            continue
    return total


def parse_dt(value: Any) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        return pd.Timestamp(str(value)[:10])
    except Exception:
        return None


def exposure_curve_bv7(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates = []
    for r in rows:
        ed = parse_dt(r.get("entry_date")); xd = parse_dt(r.get("exit_date"))
        if ed is not None: dates.append(ed)
        if xd is not None: dates.append(xd)
    if not dates:
        return []
    cur = min(dates); last = max(dates)
    out: list[dict[str, Any]] = []
    while cur <= last:
        active_items = []
        by_symbol = defaultdict(float)
        for r in rows:
            ed = parse_dt(r.get("entry_date")); xd = parse_dt(r.get("exit_date"))
            if ed is None or xd is None or not (ed <= cur <= xd):
                continue
            notional = position_notional_on(r, cur)
            active_items.append((r, notional))
            by_symbol[str(r.get("ticker"))] += notional
        exposure = sum(x[1] for x in active_items)
        max_single = max(by_symbol.values()) if by_symbol else 0.0
        out.append({
            "date": cur.date().isoformat(),
            "active_positions": len(active_items),
            "active_exposure_krw": exposure,
            "active_exposure_pct_of_85x120k": exposure / FULL_CAPACITY * 100.0,
            "max_single_symbol_exposure_krw": max_single,
            "max_single_symbol_pct_of_85x120k": max_single / FULL_CAPACITY * 100.0,
            "max_single_symbol_pct_of_active_exposure": max_single / exposure * 100.0 if exposure else 0.0,
            "max_single_symbol_pct_of_position_limit": max_single / POSITION_LIMIT * 100.0 if POSITION_LIMIT else 0.0,
            "capital_exceeded": exposure > FULL_CAPACITY,
        })
        cur += pd.Timedelta(days=1)
    return out


def summary_bv7(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = summarize(rows)
    s.update(risk_metrics(rows))
    curve = exposure_curve_bv7(rows)
    exp = [safe_float(r["active_exposure_pct_of_85x120k"]) for r in curve]
    active = [int(r["active_positions"]) for r in curve]
    max_single_full = [safe_float(r["max_single_symbol_pct_of_85x120k"]) for r in curve]
    max_single_active = [safe_float(r["max_single_symbol_pct_of_active_exposure"]) for r in curve]
    max_single_limit = [safe_float(r["max_single_symbol_pct_of_position_limit"]) for r in curve]
    invested = [invested_notional(r) for r in rows]
    initial = [safe_float(r.get("initial_notional")) for r in rows]
    add_counts = [int(r.get("add_buy_count") or 0) for r in rows]
    add_notional = [safe_float(r.get("add_buy_notional")) for r in rows]
    total_invested = sum(invested)
    s.update({
        "calendar_days": len(curve),
        "avg_exposure_pct": statistics.mean(exp) if exp else 0.0,
        "p95_exposure_pct": quant(exp, 0.95),
        "max_exposure_pct": max(exp) if exp else 0.0,
        "avg_active_positions": statistics.mean(active) if active else 0.0,
        "max_active_positions": max(active) if active else 0,
        "capital_exceeded_days": sum(1 for r in curve if r["capital_exceeded"]),
        "avg_initial_notional": statistics.mean(initial) if initial else 0.0,
        "avg_total_invested_notional": statistics.mean(invested) if invested else 0.0,
        "median_total_invested_notional": statistics.median(invested) if invested else 0.0,
        "avg_total_invested_ratio_to_120k_pct": statistics.mean([x / POSITION_LIMIT * 100.0 for x in invested]) if invested else 0.0,
        "median_total_invested_ratio_to_120k_pct": statistics.median([x / POSITION_LIMIT * 100.0 for x in invested]) if invested else 0.0,
        "invested_return_pct": sum(safe_float(r.get("pnl_krw")) for r in rows) / total_invested * 100.0 if total_invested else 0.0,
        "avg_add_buy_count": statistics.mean(add_counts) if add_counts else 0.0,
        "trades_with_add_buy": sum(1 for x in add_counts if x > 0),
        "trades_with_add_buy_pct": sum(1 for x in add_counts if x > 0) / len(add_counts) * 100.0 if add_counts else 0.0,
        "total_add_buy_notional": sum(add_notional),
        "avg_add_buy_notional": statistics.mean(add_notional) if add_notional else 0.0,
        "add_signal_count": sum(int(r.get("add_signal_count") or 0) for r in rows),
        "add_signal_rejected_cap": sum(int(r.get("add_signal_rejected_cap") or 0) for r in rows),
        "add_signal_rejected_conditions": sum(int(r.get("add_signal_rejected_conditions") or 0) for r in rows),
        "max_single_symbol_pct_of_85x120k": max(max_single_full) if max_single_full else 0.0,
        "max_single_symbol_pct_of_active_exposure": max(max_single_active) if max_single_active else 0.0,
        "max_single_symbol_pct_of_position_limit": max(max_single_limit) if max_single_limit else 0.0,
        "exit_reason_breakdown": exit_reason_breakdown(rows),
    })
    return s


def summarize_resignals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [safe_float(r.get("future_to_parent_exit_pct")) for r in rows if r.get("future_to_parent_exit_pct") is not None]
    eligible = [r for r in rows if bool(r.get("eligible_for_stage"))]
    e_vals = [safe_float(r.get("future_to_parent_exit_pct")) for r in eligible if r.get("future_to_parent_exit_pct") is not None]
    cur_pnl = [safe_float(r.get("current_pnl_pct")) for r in rows]
    return {
        "resignal_count": len(rows),
        "future_return_mean_pct": statistics.mean(vals) if vals else 0.0,
        "future_return_median_pct": statistics.median(vals) if vals else 0.0,
        "future_return_win_rate_pct": sum(1 for x in vals if x > 0) / len(vals) * 100.0 if vals else 0.0,
        "future_return_p25_pct": quant(vals, 0.25),
        "future_return_p75_pct": quant(vals, 0.75),
        "current_pnl_mean_pct": statistics.mean(cur_pnl) if cur_pnl else 0.0,
        "eligible_count_for_none_stage": len(eligible),
        "eligible_future_return_mean_pct": statistics.mean(e_vals) if e_vals else 0.0,
        "eligible_future_return_win_rate_pct": sum(1 for x in e_vals if x > 0) / len(e_vals) * 100.0 if e_vals else 0.0,
    }


def run_stage(stage: dict[str, Any], symbols: list[str], years: list[int], market_history) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    all_rows: list[dict[str, Any]] = []
    by_ticker_year: list[dict[str, Any]] = []
    resignal_rows: list[dict[str, Any]] = []
    for si, ticker in enumerate(symbols, 1):
        print(f"[stage {stage['stage']}] [{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            rb0, member_hash = load_rulebook(ticker)
            rb = make_base_rulebook(rb0)
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
        except Exception as exc:
            by_ticker_year.append({"stage": stage["stage"], "ticker": ticker, "error": str(exc)})
            continue
        for year in years:
            rows, rs = run_stage_for_ticker_year(
                ticker,
                year,
                rb,
                df,
                market_history,
                ticker_sentiment,
                member_hash,
                stage,
                collect_resignals=(stage["stage"] == "none"),
            )
            all_rows.extend(rows)
            resignal_rows.extend(rs)
            by_ticker_year.append({
                "stage": stage["stage"],
                "ticker": ticker,
                "year": year,
                "trade_count": len(rows),
                "summary": summary_bv7(rows),
                "stress": summary_bv7([stress_trade_bv7(r) for r in rows]),
            })
    return all_rows, by_ticker_year, resignal_rows


def add_multipliers(summary_by_stage: list[dict[str, Any]], baseline_stage: str = "none") -> None:
    base = next((x for x in summary_by_stage if x["stage"] == baseline_stage), None)
    if not base:
        return
    bs = base["summary"]
    for item in summary_by_stage:
        s = item["summary"]
        item["pnl_multiplier_vs_none"] = s["total_pnl_krw"] / bs["total_pnl_krw"] if bs["total_pnl_krw"] else None
        item["mdd_multiplier_vs_none"] = s["mdd_krw"] / bs["mdd_krw"] if bs["mdd_krw"] else None
        item["exposure_multiplier_vs_none"] = s["avg_exposure_pct"] / bs["avg_exposure_pct"] if bs["avg_exposure_pct"] else None
        item["invested_return_delta_pctp_vs_none"] = s["invested_return_pct"] - bs["invested_return_pct"]
        item["avg_invested_multiplier_vs_none"] = s["avg_total_invested_notional"] / bs["avg_total_invested_notional"] if bs["avg_total_invested_notional"] else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default=",".join(str(y) for y in YEARS_DEFAULT))
    ap.add_argument("--sample-size", type=int, default=None)
    ap.add_argument("--stages", default=None, help="comma-separated stage ids")
    args = ap.parse_args()

    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    stages = parse_stage_filter(args.stages)
    symbols = list(load_live_universe(LiveUniverseConfig(market="US")).symbols)
    if args.sample_size is not None:
        symbols = symbols[: args.sample_size]
    market_history = get_market_history(years=DATA_YEARS)

    all_rows: list[dict[str, Any]] = []
    by_ticker_year: list[dict[str, Any]] = []
    resignal_rows: list[dict[str, Any]] = []
    for stage in stages:
        rows, ty, rs = run_stage(stage, symbols, years, market_history)
        all_rows.extend(rows)
        by_ticker_year.extend(ty)
        resignal_rows.extend(rs)

    trades_path = OUT / "bv7_add_buy_trades.jsonl"
    ty_path = OUT / "bv7_by_ticker_year.jsonl"
    exposure_path = OUT / "bv7_exposure_daily.jsonl"
    resignal_path = OUT / "bv7_resignal_diagnostics.jsonl"
    summary_path = OUT / "bv7_summary.json"
    report_path = OUT / "bv7_report.md"

    write_jsonl(trades_path, all_rows)
    write_jsonl(ty_path, by_ticker_year)
    write_jsonl(resignal_path, resignal_rows)

    summary_by_stage: list[dict[str, Any]] = []
    exposure_rows: list[dict[str, Any]] = []
    for stage in stages:
        stage_id = stage["stage"]
        rows = [r for r in all_rows if r.get("stage") == stage_id]
        stress_rows = [stress_trade_bv7(r) for r in rows]
        base_s = summary_bv7(rows)
        stress_s = summary_bv7(stress_rows)
        curve = exposure_curve_bv7(rows)
        for c in curve:
            c["stage"] = stage_id
            exposure_rows.append(c)
        summary_by_stage.append({
            "stage": stage_id,
            "label": stage["label"],
            "params": {k: v for k, v in stage.items() if k not in {"stage", "label"}},
            "summary": base_s,
            "stress": stress_s,
            "pnl_multiplier_vs_none": None,
            "mdd_multiplier_vs_none": None,
            "exposure_multiplier_vs_none": None,
            "invested_return_delta_pctp_vs_none": None,
            "avg_invested_multiplier_vs_none": None,
        })
    add_multipliers(summary_by_stage)
    write_jsonl(exposure_path, exposure_rows)

    resignal_summary = summarize_resignals(resignal_rows)
    best = max(summary_by_stage, key=lambda x: (x["summary"].get("return_over_mdd", 0.0), x["summary"].get("total_pnl_krw", 0.0))) if summary_by_stage else None

    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {
            "symbols": symbols,
            "symbol_count": len(symbols),
            "years": years,
            "base_rulebook": {
                "sizing_factor": SIZING_FACTOR,
                "tp_factor": TP_FACTOR,
                "trailing_factor": TRAILING_FACTOR,
                "holding_factor": 1.0,
                "per_symbol_position_limit": POSITION_LIMIT,
            },
            "stages": stages,
            "full_capacity_85x120k": FULL_CAPACITY,
            "commission": COMMISSION,
            "stress_commission": STRESS_COMMISSION,
            "stress_slippage": STRESS_SLIPPAGE,
            "signal_lag_days": DEFAULT_LAG_DAYS,
            "signal_max_age_days": DEFAULT_MAX_AGE_DAYS,
        },
        "files": {
            "trades": str(trades_path),
            "by_ticker_year": str(ty_path),
            "exposure_daily": str(exposure_path),
            "resignal_diagnostics": str(resignal_path),
        },
        "resignal_diagnostics": resignal_summary,
        "summary_by_stage": summary_by_stage,
        "best_return_over_mdd_stage": best["stage"] if best else None,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# BV-7 re-signal add-buy / pyramiding sweep report",
        "",
        f"- 실행시간: {summary['elapsed_seconds']:.1f}초",
        f"- 종목수: {len(symbols)}",
        f"- 연도: {years}",
        f"- 기준 룰북: sizing {SIZING_FACTOR:.1f}x + TP/trailing {TP_FACTOR:.1f}x + holding 1.0x",
        f"- 원거래: `{trades_path}`",
        f"- by ticker-year: `{ty_path}`",
        f"- daily exposure: `{exposure_path}`",
        f"- re-signal diagnostics: `{resignal_path}`",
        "",
        "## 1) 재신호 추가매수 잠재력 진단",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| re-signal count | {resignal_summary['resignal_count']} |",
        f"| future return mean | {resignal_summary['future_return_mean_pct']:.2f}% |",
        f"| future return median | {resignal_summary['future_return_median_pct']:.2f}% |",
        f"| future return win rate | {resignal_summary['future_return_win_rate_pct']:.1f}% |",
        f"| future return p25 | {resignal_summary['future_return_p25_pct']:.2f}% |",
        f"| future return p75 | {resignal_summary['future_return_p75_pct']:.2f}% |",
        f"| current pnl at re-signal mean | {resignal_summary['current_pnl_mean_pct']:.2f}% |",
        "",
        "## 2) add-buy sweep 비교",
        "",
        "| stage | trades | add trades | avg add count | total_pnl | invested_return_pct | MDD | MDD x | avg exposure | p95 exposure | max exposure | avg invested | invested x | max single / limit | max single / active | capital exceeded | stress pnl | pnl x | exposure x |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary_by_stage:
        s = item["summary"]
        st = item["stress"]
        lines.append(
            f"| {item['stage']} | {s['trade_count']} | {s['trades_with_add_buy']} ({s['trades_with_add_buy_pct']:.1f}%) | {s['avg_add_buy_count']:.2f} | "
            f"{s['total_pnl_krw']:.0f} | {s['invested_return_pct']:.2f} | {s['mdd_krw']:.0f} | {(item['mdd_multiplier_vs_none'] or 0):.2f} | "
            f"{s['avg_exposure_pct']:.1f}% | {s['p95_exposure_pct']:.1f}% | {s['max_exposure_pct']:.1f}% | {s['avg_total_invested_notional']:.0f} | "
            f"{(item['avg_invested_multiplier_vs_none'] or 0):.2f} | {s['max_single_symbol_pct_of_position_limit']:.1f}% | {s['max_single_symbol_pct_of_active_exposure']:.1f}% | "
            f"{s['capital_exceeded_days']} | {st['total_pnl_krw']:.0f} | {(item['pnl_multiplier_vs_none'] or 0):.2f} | {(item['exposure_multiplier_vs_none'] or 0):.2f} |"
        )
    lines.extend([
        "",
        "## 3) 청산 사유 분해",
        "",
        "| stage | take_profit | trailing | stop_loss | time_out | other |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for item in summary_by_stage:
        pct = item["summary"]["exit_reason_breakdown"]["pct"]
        known = {"take_profit", "trailing", "stop_loss", "time_out"}
        other = sum(v for k, v in pct.items() if k not in known)
        lines.append(
            f"| {item['stage']} | {pct.get('take_profit', 0.0):.1f}% | {pct.get('trailing', 0.0):.1f}% | {pct.get('stop_loss', 0.0):.1f}% | {pct.get('time_out', 0.0):.1f}% | {other:.1f}% |"
        )
    lines.extend(["", "## 4) 판정 메모"])
    base = next((x for x in summary_by_stage if x["stage"] == "none"), None)
    if base and best:
        bs = base["summary"]
        cs = best["summary"]
        lines.append(f"- baseline(none) avg_exposure {bs['avg_exposure_pct']:.1f}%, invested_return {bs['invested_return_pct']:.2f}%, MDD {bs['mdd_krw']:.0f}, avg invested {bs['avg_total_invested_notional']:.0f}.")
        lines.append(f"- best_return_over_mdd({best['stage']}) avg_exposure {cs['avg_exposure_pct']:.1f}%, invested_return {cs['invested_return_pct']:.2f}%, MDD {cs['mdd_krw']:.0f}, avg invested {cs['avg_total_invested_notional']:.0f}, stress_pnl {best['stress']['total_pnl_krw']:.0f}.")
        lines.append(f"- 노출 배수 {best['exposure_multiplier_vs_none']:.2f}x, 평균 투입금액 배수 {best['avg_invested_multiplier_vs_none']:.2f}x, MDD 배수 {best['mdd_multiplier_vs_none']:.2f}x.")
        if cs["avg_exposure_pct"] >= 20.0 and cs["invested_return_pct"] >= bs["invested_return_pct"] * 0.9 and best["stress"]["total_pnl_krw"] > 0:
            lines.append("- 판정: 추가매수는 20%+ 노출 목표 후보로 유효하다.")
        elif cs["avg_exposure_pct"] > bs["avg_exposure_pct"] * 1.2 and best["stress"]["total_pnl_krw"] > 0:
            lines.append("- 판정: 추가매수는 노출을 늘리지만 20%+ 목표에는 아직 부족하다.")
        else:
            lines.append("- 판정: 추가매수만으로는 노출 병목 해소 효과가 약하다. 다음 실험은 신호 분산(signal threshold/종목 확산)을 봐야 한다.")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "out": str(OUT),
        "resignal_diagnostics": resignal_summary,
        "best_return_over_mdd_stage": summary["best_return_over_mdd_stage"],
        "summary_by_stage": summary_by_stage,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
