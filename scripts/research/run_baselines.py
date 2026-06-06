#!/usr/bin/env python3
"""BV-0 stage 2 baseline experiment.

Read-only engine calls. Writes only under:
  data/_system/research/bv0_20260607/baseline/
"""
from __future__ import annotations

import copy
import json
import math
import random
import statistics
import time
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.core.feature_lag import DEFAULT_LAG_DAYS, lookup_market_at_lagged
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.learning.backtest import run_backtest
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.rulebook import Rulebook

OUT = Path("data/_system/research/bv0_20260607/baseline")
TRADES_PATH = OUT / "baseline_trades.jsonl"
SUMMARY_PATH = OUT / "baseline_summary.json"
REPORT_PATH = OUT / "baseline_report.md"
MANIFEST_PATH = OUT / "manifest.json"
YEARS = list(range(2020, 2026))
RANDOM_SEEDS = list(range(20))
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
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return default
        return float(v)
    except Exception:
        return default


def finite(v: float | None) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def trade_to_row(ticker: str, baseline: str, seed: int | None, year: int, member_hash: str, trade: Any) -> dict[str, Any]:
    d = to_plain(trade)
    return {
        "ticker": ticker,
        "baseline": baseline,
        "seed": seed,
        "year": year,
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


def load_rulebooks() -> list[dict[str, Any]]:
    universe = load_live_universe(LiveUniverseConfig(market="US"))
    rows = []
    for ticker in universe.symbols:
        path = Path("data/symbols") / ticker / "parameters.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        rb = Rulebook.from_dict(payload["rulebook"])
        member_hash = payload.get("member_hash") or payload.get("promotion", {}).get("member_hash") or ""
        rows.append({"ticker": ticker, "path": str(path), "rulebook": rb, "member_hash": member_hash})
    return rows


def shares_for_entry(price: float) -> int:
    return int(POSITION_LIMIT / price) if price > 0 else 0


def market_ctx(market_history: pd.DataFrame | None, date: Any, sector_name: str) -> tuple[float, float, float]:
    if market_history is None:
        return 50.0, 50.0, 18.0
    try:
        m = lookup_market_at_lagged(market_history, date, lag_days=DEFAULT_LAG_DAYS)
        return float(m.get("score", 50.0)), float(m.get(f"sector_{sector_name}", 50.0)), float(m.get("vix", 18.0))
    except Exception:
        return 50.0, 50.0, 18.0


def run_rb_trades(
    baseline: str,
    ticker: str,
    rb: Rulebook,
    df: pd.DataFrame,
    year: int,
    member_hash: str,
    market_history: pd.DataFrame | None,
    ticker_sentiment: dict | None,
) -> list[dict[str, Any]]:
    result = run_backtest(
        rb,
        df,
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31",
        position_limit_krw=POSITION_LIMIT,
        commission_rate=COMMISSION,
        warmup=WARMUP,
        market_history_df=market_history,
        sector_name=getattr(rb, "sector_name", "tech") or "tech",
        ticker_sentiment=ticker_sentiment,
        fitness_mode="legacy",
    )
    return [trade_to_row(ticker, baseline, None, year, member_hash, t) for t in result.trades]


def no_market_adjustment_trades(ticker, rb, df, year, member_hash, market_history, ticker_sentiment):
    rb2 = copy.deepcopy(rb)
    rb2.market_adjustment_strength = 0.0
    return run_rb_trades("market_off", ticker, rb2, df, year, member_hash, market_history, ticker_sentiment)


def simple_rb(base: Rulebook, kind: str) -> Rulebook:
    rb = copy.deepcopy(base)
    rb.position_sizing_strategy = "fixed"
    rb.base_position_ratio = 1.0
    rb.signal_multiplier = 1.0
    rb.market_score_weight = 0.0
    rb.sector_strength_weight = 0.0
    rb.vix_sensitivity = 0.0
    rb.market_adjustment_strength = 0.0
    for name in list(rb.__dataclass_fields__):
        if name.startswith("weight_"):
            setattr(rb, name, 0.0)
    if kind == "simple_rsi":
        rb.weight_rsi_zone = 1.0
        rb.rsi_low = 20.0
        rb.rsi_high = 35.0
        rb.signal_threshold = 1.0
    elif kind == "simple_macd":
        rb.weight_macd_golden = 1.0
        rb.signal_threshold = 1.0
    else:
        raise ValueError(kind)
    return rb


def candidate_indices_for_year(df: pd.DataFrame, year: int) -> list[int]:
    start = pd.Timestamp(f"{year}-01-01")
    end = pd.Timestamp(f"{year}-12-31")
    out = []
    for i in range(max(WARMUP, 0), len(df) - 1):
        ts = pd.Timestamp(df.index[i])
        if start <= ts <= end and shares_for_entry(safe_float(df.iloc[i].get("Close"))) > 0:
            out.append(i)
    return out


def random_entry_trades(ticker, rb, df, year, member_hash, target_count, seed, market_history):
    if target_count <= 0:
        return []
    rng = random.Random(f"{seed}|{ticker}|{year}")
    candidates = candidate_indices_for_year(df, year)
    rows = []
    blocked_until = pd.Timestamp(f"{year}-01-01")
    max_attempts = max(400, target_count * 100)
    sector_name = getattr(rb, "sector_name", "tech") or "tech"
    for _ in range(max_attempts):
        if len(rows) >= target_count or not candidates:
            break
        idx = rng.choice(candidates)
        ts = pd.Timestamp(df.index[idx])
        if ts < blocked_until:
            continue
        price = safe_float(df.iloc[idx].get("Close"))
        shares = shares_for_entry(price)
        if shares <= 0:
            continue
        cm, cs, cv = market_ctx(market_history, df.index[idx], sector_name)
        trade = simulate_exit(rb, df, idx, shares, POSITION_LIMIT, commission_rate=COMMISSION,
                              cur_market_score=cm, cur_sector_score=cs, cur_vix_level=cv)
        if trade is None:
            continue
        row = trade_to_row(ticker, "random_entry", seed, year, member_hash, trade)
        rows.append(row)
        blocked_until = pd.Timestamp(row["exit_date"]) + pd.Timedelta(days=1)
    return rows


def annual_returns(rows: list[dict[str, Any]]) -> dict[str, float]:
    by_year = defaultdict(float)
    for r in rows:
        by_year[str(r["year"])] += safe_float(r.get("pnl_krw")) / POSITION_LIMIT
    return dict(sorted((k, v * 100.0) for k, v in by_year.items()))


def equity_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda r: (str(r.get("exit_date") or ""), str(r.get("ticker") or ""), str(r.get("entry_date") or "")))
    equity = 1.0
    curve = []
    for r in ordered:
        equity *= 1.0 + safe_float(r.get("pnl_krw")) / POSITION_LIMIT
        curve.append({"date": r.get("exit_date"), "equity": equity})
    return curve


def max_drawdown_pct(curve: list[dict[str, Any]]) -> float:
    peak = 1.0
    mdd = 0.0
    for p in curve:
        equity = safe_float(p.get("equity"), 1.0)
        peak = max(peak, equity)
        mdd = min(mdd, (equity / peak - 1.0) * 100.0)
    return mdd


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [safe_float(r.get("pnl_pct")) for r in rows]
    pnl_krw = [safe_float(r.get("pnl_krw")) for r in rows]
    wins = [x for x in pnl_krw if x > 0]
    losses = [x for x in pnl_krw if x < 0]
    curve = equity_curve(rows)
    total_return = (curve[-1]["equity"] - 1.0) * 100.0 if curve else 0.0
    n_years = max(1, max(YEARS) - min(YEARS) + 1)
    cagr = ((1 + total_return / 100.0) ** (1 / n_years) - 1) * 100.0 if total_return > -100 else -100.0
    pf = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    return {
        "trade_count": len(rows),
        "total_return_pct": total_return,
        "cagr_pct": cagr,
        "mdd_pct": max_drawdown_pct(curve),
        "expectancy_pct": statistics.mean(pnl) if pnl else 0.0,
        "profit_factor": finite(pf),
        "avg_holding_days": statistics.mean([int(r.get("holding_days") or 0) for r in rows]) if rows else 0.0,
        "win_rate_pct": (sum(1 for x in pnl if x > 0) / len(pnl) * 100.0) if pnl else 0.0,
        "annual_returns_pct": annual_returns(rows),
        "equity_curve": curve,
    }


def percentile_rank(value: float, samples: Iterable[float]) -> float | None:
    xs = list(samples)
    if not xs:
        return None
    return sum(1 for x in xs if x <= value) / len(xs) * 100.0


def quantiles(xs: list[float]) -> dict[str, float]:
    if not xs:
        return {"p05": 0.0, "median": 0.0, "p95": 0.0}
    arr = np.array(xs, dtype=float)
    return {"p05": float(np.percentile(arr, 5)), "median": float(np.percentile(arr, 50)), "p95": float(np.percentile(arr, 95))}


def write_row(fh, rows, row):
    rows.append(row)
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    market_history = get_market_history(years=DATA_YEARS)
    universe = load_rulebooks()
    metadata_rows = []
    all_rows: list[dict[str, Any]] = []
    with TRADES_PATH.open("w", encoding="utf-8") as trades_fh:
        for idx, item in enumerate(universe, 1):
            ticker = item["ticker"]
            rb = item["rulebook"]
            member_hash = item["member_hash"]
            sym_t0 = time.perf_counter()
            try:
                df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
                ticker_sentiment = load_ticker_sentiment(ticker)
            except Exception as exc:
                metadata_rows.append({"ticker": ticker, "status": "load_failed", "error": str(exc)})
                continue
            for year in YEARS:
                current_rows = run_rb_trades("current_rulebook", ticker, rb, df, year, member_hash, market_history, ticker_sentiment)
                for row in current_rows:
                    write_row(trades_fh, all_rows, row)
                target = len(current_rows)
                for row in no_market_adjustment_trades(ticker, rb, df, year, member_hash, market_history, ticker_sentiment):
                    write_row(trades_fh, all_rows, row)
                for kind in ("simple_rsi", "simple_macd"):
                    for row in run_rb_trades(kind, ticker, simple_rb(rb, kind), df, year, member_hash, market_history, ticker_sentiment):
                        write_row(trades_fh, all_rows, row)
                for seed in RANDOM_SEEDS:
                    for row in random_entry_trades(ticker, rb, df, year, member_hash, target, seed, market_history):
                        write_row(trades_fh, all_rows, row)
            trades_fh.flush()
            metadata_rows.append({"ticker": ticker, "status": "ok", "rows": len(df), "sentiment_days": len(ticker_sentiment or {}), "seconds": time.perf_counter() - sym_t0})
            if idx % 10 == 0 or idx == len(universe):
                print(f"[{idx}/{len(universe)}] progress trades={len(all_rows)}", flush=True)

    groups = defaultdict(list)
    for r in all_rows:
        key = r["baseline"] if r["baseline"] != "random_entry" else f"random_entry_seed_{r['seed']}"
        groups[key].append(r)
    summaries = {k: summarize_rows(v) for k, v in sorted(groups.items())}
    random_seed_summaries = [summaries[f"random_entry_seed_{s}"] for s in RANDOM_SEEDS if f"random_entry_seed_{s}" in summaries]
    random_cagr = [x["cagr_pct"] for x in random_seed_summaries]
    random_aggregate = {
        "seed_count": len(random_seed_summaries),
        "cagr_pct": quantiles(random_cagr),
        "total_return_pct": quantiles([x["total_return_pct"] for x in random_seed_summaries]),
        "expectancy_pct": quantiles([x["expectancy_pct"] for x in random_seed_summaries]),
        "mdd_pct": quantiles([x["mdd_pct"] for x in random_seed_summaries]),
        "trade_count": quantiles([x["trade_count"] for x in random_seed_summaries]),
    }
    empty = summarize_rows([])
    current = summaries.get("current_rulebook", empty)
    market_off = summaries.get("market_off", empty)
    simple_rsi = summaries.get("simple_rsi", empty)
    simple_macd = summaries.get("simple_macd", empty)
    judgment = {
        "current_vs_random_cagr_lift_pct_point_vs_median": current["cagr_pct"] - random_aggregate["cagr_pct"]["median"],
        "current_random_cagr_percentile": percentile_rank(current["cagr_pct"], random_cagr),
        "current_vs_market_off_cagr_delta_pct_point": current["cagr_pct"] - market_off["cagr_pct"],
        "current_vs_simple_rsi_cagr_delta_pct_point": current["cagr_pct"] - simple_rsi["cagr_pct"],
        "current_vs_simple_macd_cagr_delta_pct_point": current["cagr_pct"] - simple_macd["cagr_pct"],
    }
    per_ticker = {}
    for ticker in sorted({r["ticker"] for r in all_rows}):
        cur_s = summarize_rows([r for r in all_rows if r["ticker"] == ticker and r["baseline"] == "current_rulebook"])
        rnd = [summarize_rows([r for r in all_rows if r["ticker"] == ticker and r["baseline"] == "random_entry" and r["seed"] == seed])["cagr_pct"] for seed in RANDOM_SEEDS]
        per_ticker[ticker] = {
            "current_cagr_pct": cur_s["cagr_pct"],
            "random_cagr_median_pct": quantiles(rnd)["median"],
            "lift_pct_point": cur_s["cagr_pct"] - quantiles(rnd)["median"],
            "random_percentile": percentile_rank(cur_s["cagr_pct"], rnd),
            "current_trade_count": cur_s["trade_count"],
        }
    elapsed = time.perf_counter() - t0
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "config": {"years": YEARS, "random_seeds": RANDOM_SEEDS, "position_limit": POSITION_LIMIT, "commission": COMMISSION, "warmup": WARMUP, "data_years": DATA_YEARS, "data_end": DATA_END, "universe_size": len(universe), "market_history_rows": len(market_history)},
        "elapsed_seconds": elapsed,
        "metadata": metadata_rows,
        "summaries": summaries,
        "random_aggregate": random_aggregate,
        "judgment": judgment,
        "per_ticker_lift": per_ticker,
        "raw_trades_path": str(TRADES_PATH),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    MANIFEST_PATH.write_text(json.dumps({"generated_at": pd.Timestamp.now().isoformat(), "folder": str(OUT), "files": [TRADES_PATH.name, SUMMARY_PATH.name, REPORT_PATH.name, MANIFEST_PATH.name], "source_files": ["scripts/research/run_baselines.py", "engine/learning/backtest.py", "engine/strategies/exit_simulator.py", "engine/strategies/evaluator.py", "engine/strategies/rulebook.py"]}, ensure_ascii=False, indent=2), encoding="utf-8")

    def fmt(x):
        return "n/a" if x is None else f"{float(x):.2f}"
    report = ["# BV-0 2단계 baseline 실험 보고", "", f"- 실행시간: {elapsed:.1f}초", f"- 원자료: `{TRADES_PATH}`", "", "| 비교군 | CAGR% | Total% | MDD% | 거래수 | Expectancy% | PF | Avg hold |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ["current_rulebook", "market_off", "simple_rsi", "simple_macd"]:
        s = summaries.get(name, empty)
        report.append(f"| {name} | {fmt(s['cagr_pct'])} | {fmt(s['total_return_pct'])} | {fmt(s['mdd_pct'])} | {s['trade_count']} | {fmt(s['expectancy_pct'])} | {fmt(s['profit_factor'])} | {fmt(s['avg_holding_days'])} |")
    report.append(f"| random_entry median | {fmt(random_aggregate['cagr_pct']['median'])} | {fmt(random_aggregate['total_return_pct']['median'])} | {fmt(random_aggregate['mdd_pct']['median'])} | {fmt(random_aggregate['trade_count']['median'])} | {fmt(random_aggregate['expectancy_pct']['median'])} | n/a | n/a |")
    report += ["", "## 랜덤 분포", "```json", json.dumps(random_aggregate, ensure_ascii=False, indent=2), "```", "", "## 판정"]
    report.append(f"1. 현재 룰북 vs 랜덤: CAGR lift {fmt(judgment['current_vs_random_cagr_lift_pct_point_vs_median'])}pp, 랜덤 분포 내 percentile {fmt(judgment['current_random_cagr_percentile'])}%.")
    report.append(f"2. 시장보정 기여: current - market_off CAGR delta {fmt(judgment['current_vs_market_off_cagr_delta_pct_point'])}pp.")
    best_simple = max(simple_rsi["cagr_pct"], simple_macd["cagr_pct"])
    report.append(f"3. 복잡도 정당성: current - best(simple RSI/MACD) CAGR delta {fmt(current['cagr_pct'] - best_simple)}pp.")
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(json.dumps({"elapsed_seconds": elapsed, "trades": len(all_rows), "judgment": judgment, "out": str(OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
