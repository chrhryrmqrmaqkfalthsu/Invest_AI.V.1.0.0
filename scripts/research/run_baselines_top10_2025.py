#!/usr/bin/env python3
"""BV-0 stage 2 top-10 baseline smoke.

Read-only engine calls. Writes only under:
  data/_system/research/bv0_20260607/baseline/

Scope:
- Top 10 promoted US symbols by 2025 average dollar volume
- 2025 OOS only
- current promoted rulebook vs random entry seeds 0..4
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
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.rulebook import Rulebook

OUT = Path("data/_system/research/bv0_20260607/baseline")
TRADES_PATH = OUT / "top10_2025_trades.jsonl"
SUMMARY_PATH = OUT / "top10_2025_summary.json"
REPORT_PATH = OUT / "top10_2025_report.md"
YEAR = 2025
RANDOM_SEEDS = [0, 1, 2, 3, 4]
POSITION_LIMIT = 120_000.0
COMMISSION = 0.0005
WARMUP = 200
DATA_YEARS = 7
DATA_END = "2025-12-31"
TOP_N = 10


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


def load_rulebook(ticker: str) -> tuple[Rulebook, str]:
    path = Path("data/symbols") / ticker / "parameters.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rb = Rulebook.from_dict(payload["rulebook"])
    member_hash = payload.get("member_hash") or payload.get("promotion", {}).get("member_hash") or ""
    return rb, member_hash


def trade_to_row(ticker: str, baseline: str, seed: int | None, member_hash: str, trade: Any) -> dict[str, Any]:
    d = to_plain(trade)
    return {
        "ticker": ticker,
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


def select_top_symbols() -> list[str]:
    universe = load_live_universe(LiveUniverseConfig(market="US")).symbols
    scored: list[tuple[float, str]] = []
    for ticker in universe:
        try:
            df = load_ohlcv(ticker, years=2, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            df_2025 = df[(df.index >= pd.Timestamp(f"{YEAR}-01-01")) & (df.index <= pd.Timestamp(f"{YEAR}-12-31"))]
            if df_2025.empty:
                continue
            dollar_vol = (df_2025["Close"].astype(float) * df_2025["Volume"].astype(float)).mean()
            scored.append((float(dollar_vol), ticker))
        except Exception:
            continue
    scored.sort(reverse=True)
    return [ticker for _, ticker in scored[:TOP_N]]


def current_rulebook_trades(ticker: str, rb: Rulebook, df: pd.DataFrame, market_history: pd.DataFrame, ticker_sentiment: dict | None, member_hash: str) -> list[dict[str, Any]]:
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
    return [trade_to_row(ticker, "current_rulebook", None, member_hash, t) for t in result.trades]


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


def candidate_indices(df: pd.DataFrame) -> list[int]:
    start = pd.Timestamp(f"{YEAR}-01-01")
    end = pd.Timestamp(f"{YEAR}-12-31")
    out: list[int] = []
    for i in range(WARMUP, len(df) - 1):
        ts = pd.Timestamp(df.index[i])
        if start <= ts <= end and shares_for_entry(safe_float(df.iloc[i].get("Close"))) > 0:
            out.append(i)
    return out


def random_entry_trades(ticker: str, rb: Rulebook, df: pd.DataFrame, market_history: pd.DataFrame, member_hash: str, target_count: int, seed: int) -> list[dict[str, Any]]:
    if target_count <= 0:
        return []
    rng = random.Random(f"{seed}|{ticker}|{YEAR}")
    candidates = candidate_indices(df)
    rows: list[dict[str, Any]] = []
    blocked_until = pd.Timestamp(f"{YEAR}-01-01")
    sector_name = getattr(rb, "sector_name", "tech") or "tech"
    max_attempts = max(500, target_count * 120)
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
        row = trade_to_row(ticker, "random_entry", seed, member_hash, trade)
        rows.append(row)
        row_exit = row.get("exit_date")
        blocked_until = pd.Timestamp(row_exit) + pd.Timedelta(days=1) if row_exit else entry_date + pd.Timedelta(days=1)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_krw = [safe_float(r.get("pnl_krw")) for r in rows]
    pnl_pct = [safe_float(r.get("pnl_pct")) for r in rows]
    gross_profit = sum(x for x in pnl_krw if x > 0)
    gross_loss = -sum(x for x in pnl_krw if x < 0)
    return {
        "trade_count": len(rows),
        "total_pnl_krw": sum(pnl_krw),
        "expectancy_krw": statistics.mean(pnl_krw) if pnl_krw else 0.0,
        "expectancy_pct": statistics.mean(pnl_pct) if pnl_pct else 0.0,
        "win_rate_pct": (sum(1 for x in pnl_krw if x > 0) / len(pnl_krw) * 100.0) if pnl_krw else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
    }


def percentile_rank(value: float, distribution: list[float]) -> float | None:
    if not distribution:
        return None
    below = sum(1 for x in distribution if x < value)
    equal = sum(1 for x in distribution if x == value)
    return (below + 0.5 * equal) / len(distribution) * 100.0


def main() -> None:
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    symbols = select_top_symbols()
    market_history = get_market_history(years=DATA_YEARS)
    rows: list[dict[str, Any]] = []
    ticker_summaries: dict[str, Any] = {}

    for idx, ticker in enumerate(symbols, start=1):
        print(f"[{idx}/{len(symbols)}] {ticker}", flush=True)
        rb, member_hash = load_rulebook(ticker)
        df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
        ticker_sentiment = load_ticker_sentiment(ticker)
        current_rows = current_rulebook_trades(ticker, rb, df, market_history, ticker_sentiment, member_hash)
        rows.extend(current_rows)
        random_by_seed: dict[int, list[dict[str, Any]]] = {}
        for seed in RANDOM_SEEDS:
            seed_rows = random_entry_trades(ticker, rb, df, market_history, member_hash, len(current_rows), seed)
            random_by_seed[seed] = seed_rows
            rows.extend(seed_rows)
        current_summary = summarize(current_rows)
        random_summaries = {str(seed): summarize(seed_rows) for seed, seed_rows in random_by_seed.items()}
        random_pnls = [v["total_pnl_krw"] for v in random_summaries.values()]
        ticker_summaries[ticker] = {
            "current": current_summary,
            "random_by_seed": random_summaries,
            "rulebook_random_pnl_percentile": percentile_rank(current_summary["total_pnl_krw"], random_pnls),
        }

    with TRADES_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    current_all = [r for r in rows if r["baseline"] == "current_rulebook"]
    random_seed_all = {seed: [r for r in rows if r["baseline"] == "random_entry" and r["seed"] == seed] for seed in RANDOM_SEEDS}
    current_summary = summarize(current_all)
    random_summaries = {str(seed): summarize(seed_rows) for seed, seed_rows in random_seed_all.items()}
    random_pnls = [v["total_pnl_krw"] for v in random_summaries.values()]
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {
            "symbols": symbols,
            "year": YEAR,
            "random_seeds": RANDOM_SEEDS,
            "position_limit": POSITION_LIMIT,
            "commission": COMMISSION,
            "warmup": WARMUP,
            "top_n_by": "2025 avg dollar volume among promoted US universe",
        },
        "overall": {
            "current": current_summary,
            "random_by_seed": random_summaries,
            "rulebook_random_pnl_percentile": percentile_rank(current_summary["total_pnl_krw"], random_pnls),
            "random_total_pnl_distribution": random_pnls,
        },
        "by_ticker": ticker_summaries,
        "raw_trades_path": str(TRADES_PATH),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Top10 2025 current rulebook vs random baseline",
        "",
        f"- 원자료: `{TRADES_PATH}`",
        f"- 실행시간: {summary['elapsed_seconds']:.2f}초",
        f"- 종목: {', '.join(symbols)}",
        "",
        "## 전체 요약",
        "| group | trades | total_pnl_krw | expectancy_krw | expectancy_pct | win_rate_pct | profit_factor |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    cs = current_summary
    lines.append(f"| current_rulebook | {cs['trade_count']} | {cs['total_pnl_krw']:.2f} | {cs['expectancy_krw']:.2f} | {cs['expectancy_pct']:.3f} | {cs['win_rate_pct']:.1f} | {cs['profit_factor']:.3f} |")
    for seed, rs in random_summaries.items():
        lines.append(f"| random_seed_{seed} | {rs['trade_count']} | {rs['total_pnl_krw']:.2f} | {rs['expectancy_krw']:.2f} | {rs['expectancy_pct']:.3f} | {rs['win_rate_pct']:.1f} | {rs['profit_factor']:.3f} |")
    lines.append("")
    lines.append(f"- 현재 룰북 총손익 랜덤 5seed 대비 percentile: {summary['overall']['rulebook_random_pnl_percentile']:.1f}")
    lines.append("")
    lines.append("## 종목별 판정")
    lines.append("| ticker | current_trades | current_pnl | random_pnl_min | random_pnl_median | random_pnl_max | percentile |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for ticker, ts in ticker_summaries.items():
        rp = [v["total_pnl_krw"] for v in ts["random_by_seed"].values()]
        lines.append(f"| {ticker} | {ts['current']['trade_count']} | {ts['current']['total_pnl_krw']:.2f} | {min(rp):.2f} | {statistics.median(rp):.2f} | {max(rp):.2f} | {ts['rulebook_random_pnl_percentile']:.1f} |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "symbols": symbols, "trades": len(rows), "overall": summary["overall"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
