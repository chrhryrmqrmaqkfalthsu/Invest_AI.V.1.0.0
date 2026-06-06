#!/usr/bin/env python3
"""BV-0 stage 2 AAPL smoke baseline.

Read-only engine calls. Writes only under:
  data/_system/research/bv0_20260607/baseline/

Scope:
- AAPL only
- 2025 OOS only
- current promoted rulebook vs random entry seeds 0,1,2
- same Rulebook ATR exit simulator / same sizing / same commission
"""
from __future__ import annotations

import json
import random
import statistics
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.core.feature_lag import DEFAULT_LAG_DAYS, lookup_market_at_lagged
from engine.learning.backtest import run_backtest
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.rulebook import Rulebook

OUT = Path("data/_system/research/bv0_20260607/baseline")
TRADES_PATH = OUT / "aapl_2025_smoke_trades.jsonl"
SUMMARY_PATH = OUT / "aapl_2025_smoke_summary.json"
REPORT_PATH = OUT / "aapl_2025_smoke_report.md"
TICKER = "AAPL"
YEAR = 2025
RANDOM_SEEDS = [0, 1, 2]
POSITION_LIMIT = 120_000.0
COMMISSION = 0.0005
WARMUP = 200
DATA_YEARS = 7
DATA_END = "2025-12-31"


def to_plain(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(getattr(obj, "__dict__", {}) or {})


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def shares_for_entry(price: float) -> int:
    return int(POSITION_LIMIT / price) if price > 0 else 0


def trade_to_row(baseline: str, seed: int | None, member_hash: str, trade: Any) -> dict[str, Any]:
    d = to_plain(trade)
    return {
        "ticker": TICKER,
        "baseline": baseline,
        "seed": seed,
        "year": YEAR,
        "member_hash": member_hash,
        "entry_date": d.get("entry_date"),
        "entry_price": safe_float(d.get("entry_price")),
        "exit_date": d.get("exit_date"),
        "exit_price": safe_float(d.get("exit_price")),
        "holding_days": int(d.get("holding_days") or 0),
        "pnl_krw": safe_float(d.get("pnl_krw")),
        "pnl_pct": safe_float(d.get("pnl_pct")),
        "exit_reason": d.get("exit_reason"),
        "entry_shares": int(d.get("entry_shares") or 0),
        "total_shares": int(d.get("total_shares") or 0),
        "avg_cost": safe_float(d.get("avg_cost")),
        "commission": safe_float(d.get("commission")),
    }


def load_promoted_rulebook() -> tuple[Rulebook, str]:
    path = Path("data/symbols") / TICKER / "parameters.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rb = Rulebook.from_dict(payload["rulebook"])
    member_hash = payload.get("member_hash") or payload.get("promotion", {}).get("member_hash") or ""
    return rb, member_hash


def current_rulebook_trades(rb: Rulebook, df: pd.DataFrame, market_history: pd.DataFrame, ticker_sentiment: dict | None, member_hash: str) -> list[dict[str, Any]]:
    result = run_backtest(
        rb,
        df,
        start_date=f"{YEAR}-01-01",
        end_date=f"{YEAR}-12-31",
        position_limit_krw=POSITION_LIMIT,
        commission_rate=COMMISSION,
        warmup=WARMUP,
        market_history_df=market_history,
        sector_name=getattr(rb, "sector_name", "tech") or "tech",
        ticker_sentiment=ticker_sentiment,
        fitness_mode="legacy",
    )
    return [trade_to_row("current_rulebook", None, member_hash, t) for t in result.trades]


def market_context(market_history: pd.DataFrame, date: Any, sector_name: str) -> tuple[float, float, float]:
    try:
        row = lookup_market_at_lagged(market_history, date, lag_days=DEFAULT_LAG_DAYS)
        return (
            safe_float(row.get("score", 50.0), 50.0),
            safe_float(row.get(f"sector_{sector_name}", 50.0), 50.0),
            safe_float(row.get("vix", 18.0), 18.0),
        )
    except Exception:
        return 50.0, 50.0, 18.0


def candidate_indices_for_2025(df: pd.DataFrame) -> list[int]:
    start = pd.Timestamp(f"{YEAR}-01-01")
    end = pd.Timestamp(f"{YEAR}-12-31")
    out: list[int] = []
    for i in range(WARMUP, len(df) - 1):
        ts = pd.Timestamp(df.index[i])
        if start <= ts <= end and shares_for_entry(safe_float(df.iloc[i].get("Close"))) > 0:
            out.append(i)
    return out


def random_entry_trades(rb: Rulebook, df: pd.DataFrame, market_history: pd.DataFrame, member_hash: str, target_count: int, seed: int) -> list[dict[str, Any]]:
    if target_count <= 0:
        return []
    rng = random.Random(f"{seed}|{TICKER}|{YEAR}")
    candidates = candidate_indices_for_2025(df)
    rows: list[dict[str, Any]] = []
    blocked_until = pd.Timestamp(f"{YEAR}-01-01")
    sector_name = getattr(rb, "sector_name", "tech") or "tech"
    max_attempts = max(400, target_count * 100)
    for _ in range(max_attempts):
        if len(rows) >= target_count or not candidates:
            break
        idx = rng.choice(candidates)
        entry_date = pd.Timestamp(df.index[idx])
        if entry_date < blocked_until:
            continue
        price = safe_float(df.iloc[idx].get("Close"))
        shares = shares_for_entry(price)
        if shares <= 0:
            continue
        cur_market_score, cur_sector_score, cur_vix_level = market_context(market_history, df.index[idx], sector_name)
        trade = simulate_exit(
            rb,
            df,
            idx,
            shares,
            POSITION_LIMIT,
            commission_rate=COMMISSION,
            cur_market_score=cur_market_score,
            cur_sector_score=cur_sector_score,
            cur_vix_level=cur_vix_level,
        )
        if trade is None:
            continue
        row = trade_to_row("random_entry", seed, member_hash, trade)
        rows.append(row)
        blocked_until = pd.Timestamp(row["exit_date"]) + pd.Timedelta(days=1)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_krw = [safe_float(r.get("pnl_krw")) for r in rows]
    pnl_pct = [safe_float(r.get("pnl_pct")) for r in rows]
    return {
        "trade_count": len(rows),
        "total_pnl_krw": sum(pnl_krw),
        "avg_pnl_krw": statistics.mean(pnl_krw) if pnl_krw else 0.0,
        "avg_pnl_pct": statistics.mean(pnl_pct) if pnl_pct else 0.0,
        "win_rate_pct": (sum(1 for x in pnl_krw if x > 0) / len(pnl_krw) * 100.0) if pnl_krw else 0.0,
    }


def main() -> None:
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    rb, member_hash = load_promoted_rulebook()
    df = load_ohlcv(TICKER, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
    market_history = get_market_history(years=DATA_YEARS)
    ticker_sentiment = load_ticker_sentiment(TICKER)

    rows: list[dict[str, Any]] = []
    current_rows = current_rulebook_trades(rb, df, market_history, ticker_sentiment, member_hash)
    rows.extend(current_rows)
    target_count = len(current_rows)
    for seed in RANDOM_SEEDS:
        rows.extend(random_entry_trades(rb, df, market_history, member_hash, target_count, seed))

    with TRADES_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    by_group: dict[str, list[dict[str, Any]]] = {"current_rulebook": current_rows}
    for seed in RANDOM_SEEDS:
        by_group[f"random_entry_seed_{seed}"] = [r for r in rows if r["baseline"] == "random_entry" and r["seed"] == seed]
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {
            "ticker": TICKER,
            "year": YEAR,
            "random_seeds": RANDOM_SEEDS,
            "position_limit": POSITION_LIMIT,
            "commission": COMMISSION,
            "warmup": WARMUP,
            "data_years": DATA_YEARS,
            "data_end": DATA_END,
            "member_hash": member_hash,
        },
        "summaries": {name: summarize(group_rows) for name, group_rows in by_group.items()},
        "raw_trades_path": str(TRADES_PATH),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# AAPL 2025 baseline smoke",
        "",
        f"- 원자료: `{TRADES_PATH}`",
        f"- 실행시간: {summary['elapsed_seconds']:.2f}초",
        "",
        "| group | trades | total_pnl_krw | avg_pnl_pct | win_rate_pct |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, s in summary["summaries"].items():
        lines.append(f"| {name} | {s['trade_count']} | {s['total_pnl_krw']:.2f} | {s['avg_pnl_pct']:.3f} | {s['win_rate_pct']:.1f} |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "trades": len(rows), "summaries": summary["summaries"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
