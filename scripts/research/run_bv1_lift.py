#!/usr/bin/env python3
"""BV-1 baseline lift analysis.

Read-only engine calls. Writes only under data/_system/research/bv1_20260607/.
Does not modify rulebooks, symbols, live code, or exit_policy.

Default scope is intentionally bounded for tool reliability:
- 30 deterministic random promoted US symbols
- years 2023, 2024, 2025
- current rulebook vs full-range random rulebooks (50 seeds)
- buy-and-hold and naive momentum baselines
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import statistics
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.learning.backtest import run_backtest
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.rulebook import CATEGORICAL_PARAMS, PARAM_RANGES, Rulebook

OUT = Path("data/_system/research/bv1_20260607")
POSITION_LIMIT = 120_000.0
COMMISSION = 0.0005
WARMUP = 200
DATA_YEARS = 8
DATA_END = "2025-12-31"
DEFAULT_YEARS = [2023, 2024, 2025]
DEFAULT_SAMPLE_SIZE = 30
DEFAULT_RANDOM_SEEDS = 50
SAMPLE_SEED = 20260607


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


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


def load_rulebook(ticker: str) -> tuple[Rulebook, str]:
    path = Path("data/symbols") / ticker / "parameters.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rb = Rulebook.from_dict(payload["rulebook"])
    member_hash = payload.get("member_hash") or payload.get("promotion", {}).get("member_hash") or ""
    return rb, member_hash


def choose_symbols(sample_size: int, use_all: bool) -> list[str]:
    symbols = list(load_live_universe(LiveUniverseConfig(market="US")).symbols)
    if use_all or sample_size >= len(symbols):
        return symbols
    rng = random.Random(SAMPLE_SEED)
    return sorted(rng.sample(symbols, sample_size))


def trade_to_row(ticker: str, year: int, baseline: str, seed: int | None, member_hash: str, trade: Any) -> dict[str, Any]:
    d = to_plain(trade)
    return {
        "ticker": ticker,
        "year": year,
        "baseline": baseline,
        "seed": seed,
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


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [safe_float(r.get("pnl_krw")) for r in rows]
    pct = [safe_float(r.get("pnl_pct")) for r in rows]
    gross_profit = sum(x for x in pnl if x > 0)
    gross_loss = -sum(x for x in pnl if x < 0)
    avg_hold = statistics.mean([int(r.get("holding_days") or 0) for r in rows]) if rows else 0.0
    return {
        "trade_count": len(rows),
        "total_pnl_krw": sum(pnl),
        "expectancy_krw": statistics.mean(pnl) if pnl else 0.0,
        "expectancy_pct": statistics.mean(pct) if pct else 0.0,
        "win_rate_pct": (sum(1 for x in pnl if x > 0) / len(pnl) * 100.0) if pnl else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
        "avg_holding_days": avg_hold,
    }


def percentile_rank(value: float, dist: list[float]) -> float | None:
    if not dist:
        return None
    below = sum(1 for x in dist if x < value)
    equal = sum(1 for x in dist if x == value)
    return (below + 0.5 * equal) / len(dist) * 100.0


def quantile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    xs = sorted(vals)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def random_rulebook_like(base: Rulebook, ticker: str, seed: int) -> Rulebook:
    rng = random.Random(f"bv1|{ticker}|{seed}")
    data = base.to_dict()
    for key, (lo, hi) in PARAM_RANGES.items():
        if key in {"max_holding_days", "earnings_blackout_days"}:
            data[key] = int(round(rng.uniform(lo, hi)))
        else:
            data[key] = rng.uniform(lo, hi)
    for key, vals in CATEGORICAL_PARAMS.items():
        data[key] = rng.choice(list(vals))
    data["ticker"] = ticker
    data["asset_type"] = getattr(base, "asset_type", "us_stock") or "us_stock"
    data["direction"] = getattr(base, "direction", "long") or "long"
    data["sector_name"] = getattr(base, "sector_name", "tech") or "tech"
    return Rulebook.from_dict(data)


def naive_momentum_rulebook(base: Rulebook, ticker: str) -> Rulebook:
    data = base.to_dict()
    weight_keys = [k for k in PARAM_RANGES if k.startswith("weight_")]
    for key in weight_keys:
        data[key] = 0.0
    data["weight_macd_golden"] = 1.0
    data["weight_ma_trend"] = 1.0
    data["weight_rsi_zone"] = 0.0
    data["signal_threshold"] = 1.0
    data["market_adjustment_strength"] = 0.0
    data["ticker"] = ticker
    return Rulebook.from_dict(data)


def run_rulebook_baseline(ticker: str, year: int, baseline: str, rb: Rulebook, df: pd.DataFrame, market_history: pd.DataFrame, ticker_sentiment: dict | None, member_hash: str, seed: int | None = None) -> list[dict[str, Any]]:
    res = run_backtest(
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
    return [trade_to_row(ticker, year, baseline, seed, member_hash, t) for t in res.trades]


def buy_hold_row(ticker: str, year: int, member_hash: str, df: pd.DataFrame) -> dict[str, Any] | None:
    sub = df[(df.index >= pd.Timestamp(f"{year}-01-01")) & (df.index <= pd.Timestamp(f"{year}-12-31"))]
    if len(sub) < 2:
        return None
    entry = sub.iloc[0]
    exit_ = sub.iloc[-1]
    entry_price = safe_float(entry.get("Close"))
    exit_price = safe_float(exit_.get("Close"))
    shares = int(POSITION_LIMIT / entry_price) if entry_price > 0 else 0
    if shares <= 0:
        return None
    gross = (exit_price - entry_price) * shares
    commission = (entry_price * shares + exit_price * shares) * COMMISSION
    pnl = gross - commission
    pct = pnl / (entry_price * shares) * 100.0 if entry_price * shares else 0.0
    return {
        "ticker": ticker,
        "year": year,
        "baseline": "buy_hold",
        "seed": None,
        "member_hash": member_hash,
        "entry_date": str(sub.index[0].date()),
        "entry_price": entry_price,
        "exit_date": str(sub.index[-1].date()),
        "exit_price": exit_price,
        "holding_days": int((pd.Timestamp(sub.index[-1]) - pd.Timestamp(sub.index[0])).days),
        "pnl_krw": pnl,
        "pnl_pct": pct,
        "exit_reason": "year_end",
        "entry_shares": shares,
        "total_shares": shares,
        "avg_cost": entry_price,
        "commission": commission,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--random-seeds", type=int, default=DEFAULT_RANDOM_SEEDS)
    ap.add_argument("--years", default=",".join(str(y) for y in DEFAULT_YEARS))
    args = ap.parse_args()

    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    trades_path = OUT / "bv1_trades.jsonl"
    lift_path = OUT / "bv1_lift_by_ticker_year.jsonl"
    summary_path = OUT / "bv1_summary.json"
    report_path = OUT / "bv1_report.md"

    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    random_seeds = list(range(args.random_seeds))
    symbols = choose_symbols(args.sample_size, args.all)
    market_history = get_market_history(years=DATA_YEARS)

    all_rows: list[dict[str, Any]] = []
    lift_rows: list[dict[str, Any]] = []
    random_distribution_rows: list[dict[str, Any]] = []

    for si, ticker in enumerate(symbols, 1):
        print(f"[{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            current_rb, member_hash = load_rulebook(ticker)
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
        except Exception as exc:
            lift_rows.append({"ticker": ticker, "error": str(exc)})
            continue
        naive_rb = naive_momentum_rulebook(current_rb, ticker)
        for year in years:
            current_rows = run_rulebook_baseline(ticker, year, "current_rulebook", current_rb, df, market_history, ticker_sentiment, member_hash)
            naive_rows = run_rulebook_baseline(ticker, year, "naive_momentum", naive_rb, df, market_history, ticker_sentiment, member_hash)
            bh = buy_hold_row(ticker, year, member_hash, df)
            bh_rows = [bh] if bh else []
            all_rows.extend(current_rows)
            all_rows.extend(naive_rows)
            all_rows.extend(bh_rows)
            random_by_seed: dict[int, list[dict[str, Any]]] = {}
            for seed in random_seeds:
                rr = random_rulebook_like(current_rb, ticker, seed)
                rows = run_rulebook_baseline(ticker, year, "random_rulebook", rr, df, market_history, ticker_sentiment, member_hash, seed=seed)
                random_by_seed[seed] = rows
                all_rows.extend(rows)
                s = summarize(rows)
                random_distribution_rows.append({"ticker": ticker, "year": year, "seed": seed, **s})
            cs = summarize(current_rows)
            ns = summarize(naive_rows)
            bs = summarize(bh_rows)
            random_seed_summaries = [summarize(v) for v in random_by_seed.values()]
            rpnl = [x["total_pnl_krw"] for x in random_seed_summaries]
            rexpect = [x["expectancy_krw"] for x in random_seed_summaries]
            lift_rows.append({
                "ticker": ticker,
                "year": year,
                "current": cs,
                "buy_hold": bs,
                "naive_momentum": ns,
                "random_rulebook": {
                    "seed_count": len(random_seeds),
                    "pnl_p05": quantile(rpnl, 0.05),
                    "pnl_p50": quantile(rpnl, 0.50),
                    "pnl_p95": quantile(rpnl, 0.95),
                    "expectancy_p50": quantile(rexpect, 0.50),
                    "current_pnl_percentile": percentile_rank(cs["total_pnl_krw"], rpnl),
                    "current_expectancy_percentile": percentile_rank(cs["expectancy_krw"], rexpect),
                    "p_value_ge_current_pnl": (sum(1 for x in rpnl if x >= cs["total_pnl_krw"]) / len(rpnl)) if rpnl else None,
                },
                "excess_vs_buy_hold_krw": cs["total_pnl_krw"] - bs["total_pnl_krw"],
                "excess_vs_naive_krw": cs["total_pnl_krw"] - ns["total_pnl_krw"],
            })

    with trades_path.open("w", encoding="utf-8") as fh:
        for row in all_rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with lift_path.open("w", encoding="utf-8") as fh:
        for row in lift_rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (OUT / "bv1_random_distribution.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in random_distribution_rows) + "\n", encoding="utf-8")

    def all_baseline(name: str) -> list[dict[str, Any]]:
        return [r for r in all_rows if r.get("baseline") == name]

    current_summary = summarize(all_baseline("current_rulebook"))
    buy_hold_summary = summarize(all_baseline("buy_hold"))
    naive_summary = summarize(all_baseline("naive_momentum"))
    random_seed_port = []
    for seed in random_seeds:
        random_seed_port.append({"seed": seed, **summarize([r for r in all_rows if r.get("baseline") == "random_rulebook" and r.get("seed") == seed])})
    random_port_pnls = [x["total_pnl_krw"] for x in random_seed_port]
    random_port_expect = [x["expectancy_krw"] for x in random_seed_port]
    valid_lift = [r for r in lift_rows if "current" in r]
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {"symbols": symbols, "sample_size": len(symbols), "years": years, "random_seed_count": len(random_seeds), "sample_seed": SAMPLE_SEED, "position_limit": POSITION_LIMIT, "commission": COMMISSION},
        "files": {"trades": str(trades_path), "lift_by_ticker_year": str(lift_path), "random_distribution": str(OUT / "bv1_random_distribution.jsonl")},
        "portfolio": {
            "current_rulebook": current_summary,
            "buy_hold": buy_hold_summary,
            "naive_momentum": naive_summary,
            "random_rulebook_by_seed": random_seed_port,
            "current_pnl_vs_random_percentile": percentile_rank(current_summary["total_pnl_krw"], random_port_pnls),
            "current_expectancy_vs_random_percentile": percentile_rank(current_summary["expectancy_krw"], random_port_expect),
            "random_pnl_p05": quantile(random_port_pnls, 0.05),
            "random_pnl_p50": quantile(random_port_pnls, 0.50),
            "random_pnl_p95": quantile(random_port_pnls, 0.95),
            "p_value_ge_current_pnl": (sum(1 for x in random_port_pnls if x >= current_summary["total_pnl_krw"]) / len(random_port_pnls)) if random_port_pnls else None,
            "excess_vs_buy_hold_krw": current_summary["total_pnl_krw"] - buy_hold_summary["total_pnl_krw"],
            "excess_vs_naive_krw": current_summary["total_pnl_krw"] - naive_summary["total_pnl_krw"],
        },
        "ticker_year_counts": {
            "total": len(valid_lift),
            "pnl_random_percentile_ge_95": sum(1 for r in valid_lift if (r["random_rulebook"].get("current_pnl_percentile") or 0) >= 95),
            "pnl_random_percentile_ge_50": sum(1 for r in valid_lift if (r["random_rulebook"].get("current_pnl_percentile") or 0) >= 50),
            "positive_excess_vs_buy_hold": sum(1 for r in valid_lift if r.get("excess_vs_buy_hold_krw", 0) > 0),
            "positive_excess_vs_naive": sum(1 for r in valid_lift if r.get("excess_vs_naive_krw", 0) > 0),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    p = summary["portfolio"]
    if (p["current_pnl_vs_random_percentile"] or 0) >= 95 and p["excess_vs_buy_hold_krw"] > 0 and p["excess_vs_naive_krw"] > 0:
        verdict = "edge 입증(샘플 기준)"
    elif (p["current_pnl_vs_random_percentile"] or 0) >= 50 or p["excess_vs_buy_hold_krw"] > 0 or p["excess_vs_naive_krw"] > 0:
        verdict = "edge 불충분/혼재"
    else:
        verdict = "edge 없음/약함"
    lines = [
        "# BV-1 baseline lift report",
        "",
        f"- 실행시간: {summary['elapsed_seconds']:.1f}초",
        f"- 종목수: {len(symbols)}",
        f"- 연도: {years}",
        f"- 랜덤 룰북 seed: {len(random_seeds)}",
        f"- 원거래: `{trades_path}`",
        f"- lift 테이블: `{lift_path}`",
        f"- 종합판정: **{verdict}**",
        "",
        "## 포트폴리오 요약",
        "| baseline | trades | total_pnl | expectancy | win_rate | profit_factor |",
        "|---|---:|---:|---:|---:|---:|",
        f"| current_rulebook | {current_summary['trade_count']} | {current_summary['total_pnl_krw']:.2f} | {current_summary['expectancy_krw']:.2f} | {current_summary['win_rate_pct']:.1f} | {current_summary['profit_factor']:.3f} |",
        f"| buy_hold | {buy_hold_summary['trade_count']} | {buy_hold_summary['total_pnl_krw']:.2f} | {buy_hold_summary['expectancy_krw']:.2f} | {buy_hold_summary['win_rate_pct']:.1f} | {buy_hold_summary['profit_factor']:.3f} |",
        f"| naive_momentum | {naive_summary['trade_count']} | {naive_summary['total_pnl_krw']:.2f} | {naive_summary['expectancy_krw']:.2f} | {naive_summary['win_rate_pct']:.1f} | {naive_summary['profit_factor']:.3f} |",
        f"| random_rulebook p50 seed | - | {p['random_pnl_p50']:.2f} | - | - | - |",
        "",
        "## 핵심 판정",
        f"1. 현재 룰북 vs 랜덤 룰북: 포트폴리오 총손익 percentile {p['current_pnl_vs_random_percentile']:.1f}, expectancy percentile {p['current_expectancy_vs_random_percentile']:.1f}, p(random>=current)={p['p_value_ge_current_pnl']:.3f}.",
        f"2. 현재 룰북 vs buy-and-hold 초과손익: {p['excess_vs_buy_hold_krw']:.2f}.",
        f"3. 현재 룰북 vs naive momentum 초과손익: {p['excess_vs_naive_krw']:.2f}.",
        "",
        "## ticker-year 판정 개수",
        json.dumps(summary["ticker_year_counts"], ensure_ascii=False, indent=2),
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "verdict": verdict, "portfolio": summary["portfolio"], "counts": summary["ticker_year_counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
