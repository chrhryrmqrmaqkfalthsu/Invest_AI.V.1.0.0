#!/usr/bin/env python3
"""BV-2 risk-adjusted baseline lift analysis.

Read-only engine calls. Writes only under data/_system/research/bv2_20260607/.
Default scope: promoted US 85, years 2022-2025, random rulebook 50 seeds.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.learning.backtest import run_backtest
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from scripts.research.run_bv1_lift import (
    COMMISSION,
    DATA_END,
    DATA_YEARS,
    POSITION_LIMIT,
    WARMUP,
    buy_hold_row,
    load_rulebook,
    naive_momentum_rulebook,
    percentile_rank,
    quantile,
    random_rulebook_like,
    run_rulebook_baseline,
    safe_float,
    summarize,
)

OUT = Path("data/_system/research/bv2_20260607")
YEARS_DEFAULT = [2022, 2023, 2024, 2025]
RANDOM_SEEDS_DEFAULT = 50
STRESS_COMMISSION = 0.0015
STRESS_SLIPPAGE = 0.0010


def stress_trade(row: dict[str, Any]) -> dict[str, Any]:
    r = dict(row)
    entry_notional = abs(safe_float(r.get("entry_price")) * safe_float(r.get("entry_shares")))
    exit_notional = abs(safe_float(r.get("exit_price")) * safe_float(r.get("total_shares")))
    turnover = entry_notional + exit_notional
    already_commission = safe_float(r.get("commission"))
    stress_cost = turnover * (STRESS_COMMISSION + STRESS_SLIPPAGE)
    extra_cost = max(0.0, stress_cost - already_commission)
    r["pnl_krw"] = safe_float(r.get("pnl_krw")) - extra_cost
    base = entry_notional if entry_notional > 0 else POSITION_LIMIT
    r["pnl_pct"] = r["pnl_krw"] / base * 100.0 if base else 0.0
    r["stress_extra_cost"] = extra_cost
    return r


def risk_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "mdd_krw": 0.0,
            "mdd_pct_of_capital": 0.0,
            "max_consecutive_losses": 0,
            "sharpe_trade": 0.0,
            "sortino_trade": 0.0,
            "return_over_mdd": 0.0,
        }
    ordered = sorted(rows, key=lambda r: (str(r.get("exit_date") or r.get("entry_date") or ""), str(r.get("ticker") or ""), str(r.get("seed") or "")))
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    cur_loss = 0
    max_loss = 0
    pnls = []
    for r in ordered:
        pnl = safe_float(r.get("pnl_krw"))
        pnls.append(pnl)
        equity += pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
        if pnl < 0:
            cur_loss += 1
            max_loss = max(max_loss, cur_loss)
        else:
            cur_loss = 0
    mean = statistics.mean(pnls) if pnls else 0.0
    sd = statistics.pstdev(pnls) if len(pnls) > 1 else 0.0
    downside = [x for x in pnls if x < 0]
    dsd = statistics.pstdev(downside) if len(downside) > 1 else 0.0
    total = sum(pnls)
    mdd_abs = abs(max_dd)
    return {
        "trade_count": len(rows),
        "mdd_krw": mdd_abs,
        "mdd_pct_of_capital": mdd_abs / POSITION_LIMIT * 100.0,
        "max_consecutive_losses": max_loss,
        "sharpe_trade": mean / sd * math.sqrt(len(pnls)) if sd > 0 else (999.0 if mean > 0 else 0.0),
        "sortino_trade": mean / dsd * math.sqrt(len(pnls)) if dsd > 0 else (999.0 if mean > 0 else 0.0),
        "return_over_mdd": total / mdd_abs if mdd_abs > 0 else (999.0 if total > 0 else 0.0),
    }


def summary_with_risk(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = summarize(rows)
    s.update(risk_metrics(rows))
    return s


def select_symbols(use_all: bool, sample_size: int | None) -> list[str]:
    symbols = list(load_live_universe(LiveUniverseConfig(market="US")).symbols)
    if use_all or sample_size is None or sample_size >= len(symbols):
        return symbols
    # Deterministic unbiased prefix from sorted promotion universe only if explicitly requested.
    return sorted(symbols)[:sample_size]


def run_current(ticker: str, year: int, rb, df, market_history, ticker_sentiment, member_hash: str) -> list[dict[str, Any]]:
    return run_rulebook_baseline(ticker, year, "current_rulebook", rb, df, market_history, ticker_sentiment, member_hash)


def run_naive(ticker: str, year: int, rb, df, market_history, ticker_sentiment, member_hash: str) -> list[dict[str, Any]]:
    return run_rulebook_baseline(ticker, year, "naive_momentum", naive_momentum_rulebook(rb, ticker), df, market_history, ticker_sentiment, member_hash)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", default=True)
    ap.add_argument("--sample-size", type=int, default=None)
    ap.add_argument("--random-seeds", type=int, default=RANDOM_SEEDS_DEFAULT)
    ap.add_argument("--years", default=",".join(str(y) for y in YEARS_DEFAULT))
    args = ap.parse_args()

    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    trades_path = OUT / "bv2_trades.jsonl"
    lift_path = OUT / "bv2_lift_by_ticker_year.jsonl"
    random_dist_path = OUT / "bv2_random_distribution.jsonl"
    risk_path = OUT / "bv2_risk_summary.jsonl"
    summary_path = OUT / "bv2_summary.json"
    report_path = OUT / "bv2_report.md"

    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    random_seeds = list(range(args.random_seeds))
    symbols = select_symbols(args.all, args.sample_size)
    market_history = get_market_history(years=DATA_YEARS)

    all_rows: list[dict[str, Any]] = []
    lift_rows: list[dict[str, Any]] = []
    random_dist: list[dict[str, Any]] = []

    for si, ticker in enumerate(symbols, 1):
        print(f"[{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            rb, member_hash = load_rulebook(ticker)
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
        except Exception as exc:
            lift_rows.append({"ticker": ticker, "error": str(exc)})
            continue
        for year in years:
            current_rows = run_current(ticker, year, rb, df, market_history, ticker_sentiment, member_hash)
            naive_rows = run_naive(ticker, year, rb, df, market_history, ticker_sentiment, member_hash)
            bh = buy_hold_row(ticker, year, member_hash, df)
            bh_rows = [bh] if bh else []
            all_rows.extend(current_rows + naive_rows + bh_rows)
            random_by_seed: dict[int, list[dict[str, Any]]] = {}
            for seed in random_seeds:
                rr = random_rulebook_like(rb, ticker, seed)
                rows = run_rulebook_baseline(ticker, year, "random_rulebook", rr, df, market_history, ticker_sentiment, member_hash, seed=seed)
                random_by_seed[seed] = rows
                all_rows.extend(rows)
                random_dist.append({"ticker": ticker, "year": year, "seed": seed, **summary_with_risk(rows), "stress": summary_with_risk([stress_trade(x) for x in rows])})
            cs = summary_with_risk(current_rows)
            ns = summary_with_risk(naive_rows)
            bs = summary_with_risk(bh_rows)
            cstress = summary_with_risk([stress_trade(x) for x in current_rows])
            nstress = summary_with_risk([stress_trade(x) for x in naive_rows])
            bstress = summary_with_risk([stress_trade(x) for x in bh_rows])
            random_summaries = [summary_with_risk(v) for v in random_by_seed.values()]
            rstress_summaries = [summary_with_risk([stress_trade(x) for x in v]) for v in random_by_seed.values()]
            rpnl = [x["total_pnl_krw"] for x in random_summaries]
            rmdd = [x["mdd_krw"] for x in random_summaries]
            rspnl = [x["total_pnl_krw"] for x in rstress_summaries]
            lift_rows.append({
                "ticker": ticker,
                "year": year,
                "current": cs,
                "buy_hold": bs,
                "naive_momentum": ns,
                "stress": {"current": cstress, "buy_hold": bstress, "naive_momentum": nstress},
                "random_rulebook": {
                    "seed_count": len(random_seeds),
                    "pnl_p05": quantile(rpnl, 0.05),
                    "pnl_p50": quantile(rpnl, 0.50),
                    "pnl_p95": quantile(rpnl, 0.95),
                    "mdd_p50": quantile(rmdd, 0.50),
                    "stress_pnl_p50": quantile(rspnl, 0.50),
                    "current_pnl_percentile": percentile_rank(cs["total_pnl_krw"], rpnl),
                    "current_mdd_percentile_low_is_good": percentile_rank(-cs["mdd_krw"], [-x for x in rmdd]),
                    "current_stress_pnl_percentile": percentile_rank(cstress["total_pnl_krw"], rspnl),
                    "p_value_ge_current_pnl": sum(1 for x in rpnl if x >= cs["total_pnl_krw"]) / len(rpnl) if rpnl else None,
                },
                "excess_vs_buy_hold_krw": cs["total_pnl_krw"] - bs["total_pnl_krw"],
                "mdd_reduction_vs_buy_hold_krw": bs["mdd_krw"] - cs["mdd_krw"],
                "excess_vs_naive_krw": cs["total_pnl_krw"] - ns["total_pnl_krw"],
                "stress_current_survives": cstress["total_pnl_krw"] > 0,
            })

    with trades_path.open("w", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    with lift_path.open("w", encoding="utf-8") as f:
        for r in lift_rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    random_dist_path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in random_dist) + "\n", encoding="utf-8")

    def rows_for(name: str, seed: int | None = None) -> list[dict[str, Any]]:
        return [r for r in all_rows if r.get("baseline") == name and (seed is None or r.get("seed") == seed)]

    current = summary_with_risk(rows_for("current_rulebook"))
    buyhold = summary_with_risk(rows_for("buy_hold"))
    naive = summary_with_risk(rows_for("naive_momentum"))
    current_stress = summary_with_risk([stress_trade(x) for x in rows_for("current_rulebook")])
    buyhold_stress = summary_with_risk([stress_trade(x) for x in rows_for("buy_hold")])
    naive_stress = summary_with_risk([stress_trade(x) for x in rows_for("naive_momentum")])
    random_port = [{"seed": seed, **summary_with_risk(rows_for("random_rulebook", seed))} for seed in random_seeds]
    random_stress = [{"seed": seed, **summary_with_risk([stress_trade(x) for x in rows_for("random_rulebook", seed)])} for seed in random_seeds]
    rpnl = [x["total_pnl_krw"] for x in random_port]
    rmdd = [x["mdd_krw"] for x in random_port]
    rspnl = [x["total_pnl_krw"] for x in random_stress]
    valid = [r for r in lift_rows if "current" in r]
    by_year: dict[str, Any] = {}
    for year in years:
        yr = [r for r in valid if r.get("year") == year]
        by_year[str(year)] = {
            "ticker_years": len(yr),
            "positive_excess_vs_buy_hold": sum(1 for r in yr if r.get("excess_vs_buy_hold_krw", 0) > 0),
            "mdd_reduction_vs_buy_hold": sum(1 for r in yr if r.get("mdd_reduction_vs_buy_hold_krw", 0) > 0),
            "stress_survival": sum(1 for r in yr if r.get("stress_current_survives")),
            "random_pnl_percentile_median": statistics.median([r["random_rulebook"].get("current_pnl_percentile") or 0 for r in yr]) if yr else 0,
        }
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {"symbols": symbols, "years": years, "random_seed_count": len(random_seeds), "commission": COMMISSION, "stress_commission": STRESS_COMMISSION, "stress_slippage": STRESS_SLIPPAGE, "position_limit": POSITION_LIMIT},
        "files": {"trades": str(trades_path), "lift_by_ticker_year": str(lift_path), "random_distribution": str(random_dist_path), "risk_summary": str(risk_path)},
        "portfolio": {
            "current_rulebook": current,
            "buy_hold": buyhold,
            "naive_momentum": naive,
            "stress": {"current_rulebook": current_stress, "buy_hold": buyhold_stress, "naive_momentum": naive_stress, "random_p50_pnl": quantile(rspnl, 0.5)},
            "random_rulebook_by_seed": random_port,
            "current_pnl_vs_random_percentile": percentile_rank(current["total_pnl_krw"], rpnl),
            "current_mdd_vs_random_percentile_low_is_good": percentile_rank(-current["mdd_krw"], [-x for x in rmdd]),
            "current_stress_pnl_vs_random_percentile": percentile_rank(current_stress["total_pnl_krw"], rspnl),
            "random_pnl_p05": quantile(rpnl, 0.05),
            "random_pnl_p50": quantile(rpnl, 0.50),
            "random_pnl_p95": quantile(rpnl, 0.95),
            "p_value_ge_current_pnl": sum(1 for x in rpnl if x >= current["total_pnl_krw"]) / len(rpnl) if rpnl else None,
            "excess_vs_buy_hold_krw": current["total_pnl_krw"] - buyhold["total_pnl_krw"],
            "mdd_reduction_vs_buy_hold_krw": buyhold["mdd_krw"] - current["mdd_krw"],
            "excess_vs_naive_krw": current["total_pnl_krw"] - naive["total_pnl_krw"],
        },
        "by_year": by_year,
        "ticker_year_counts": {"total": len(valid), "positive_excess_vs_buy_hold": sum(1 for r in valid if r.get("excess_vs_buy_hold_krw", 0) > 0), "mdd_reduction_vs_buy_hold": sum(1 for r in valid if r.get("mdd_reduction_vs_buy_hold_krw", 0) > 0), "stress_survival": sum(1 for r in valid if r.get("stress_current_survives")), "pnl_random_percentile_ge_95": sum(1 for r in valid if (r["random_rulebook"].get("current_pnl_percentile") or 0) >= 95)},
    }
    risk_rows = [
        {"baseline": "current_rulebook", **current, "stress": current_stress},
        {"baseline": "buy_hold", **buyhold, "stress": buyhold_stress},
        {"baseline": "naive_momentum", **naive, "stress": naive_stress},
    ]
    risk_path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in risk_rows) + "\n", encoding="utf-8")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    p = summary["portfolio"]
    if p["mdd_reduction_vs_buy_hold_krw"] > 0 and p["excess_vs_buy_hold_krw"] < 0:
        scenario = "방어형 가능성: buy-and-hold보다 수익은 낮지만 MDD 감소"
    elif p["excess_vs_buy_hold_krw"] < 0 and p["excess_vs_naive_krw"] < 0 and (p["current_pnl_vs_random_percentile"] or 0) < 50:
        scenario = "edge 없음/약함: 랜덤·B&H·naive 대비 불충분"
    else:
        scenario = "혼재: 추가 원인분해 필요"
    lines = [
        "# BV-2 risk-adjusted baseline lift report",
        "",
        f"- 실행시간: {summary['elapsed_seconds']:.1f}초",
        f"- 종목수: {len(symbols)}",
        f"- 연도: {years}",
        f"- 랜덤 룰북 seed: {len(random_seeds)}",
        f"- 원거래: `{trades_path}`",
        f"- lift: `{lift_path}`",
        f"- risk: `{risk_path}`",
        f"- 최종 시나리오: **{scenario}**",
        "",
        "## 포트폴리오 요약",
        "| baseline | trades | total_pnl | expectancy | MDD | max_loss_streak | sharpe_trade | sortino_trade | stress_pnl |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| current_rulebook | {current['trade_count']} | {current['total_pnl_krw']:.2f} | {current['expectancy_krw']:.2f} | {current['mdd_krw']:.2f} | {current['max_consecutive_losses']} | {current['sharpe_trade']:.3f} | {current['sortino_trade']:.3f} | {current_stress['total_pnl_krw']:.2f} |",
        f"| buy_hold | {buyhold['trade_count']} | {buyhold['total_pnl_krw']:.2f} | {buyhold['expectancy_krw']:.2f} | {buyhold['mdd_krw']:.2f} | {buyhold['max_consecutive_losses']} | {buyhold['sharpe_trade']:.3f} | {buyhold['sortino_trade']:.3f} | {buyhold_stress['total_pnl_krw']:.2f} |",
        f"| naive_momentum | {naive['trade_count']} | {naive['total_pnl_krw']:.2f} | {naive['expectancy_krw']:.2f} | {naive['mdd_krw']:.2f} | {naive['max_consecutive_losses']} | {naive['sharpe_trade']:.3f} | {naive['sortino_trade']:.3f} | {naive_stress['total_pnl_krw']:.2f} |",
        f"| random_rulebook p50 seed | - | {p['random_pnl_p50']:.2f} | - | - | - | - | - | {p['stress']['random_p50_pnl']:.2f} |",
        "",
        "## 핵심 판정",
        f"1. 랜덤 대비: 총손익 percentile {p['current_pnl_vs_random_percentile']:.1f}, MDD percentile(low good) {p['current_mdd_vs_random_percentile_low_is_good']:.1f}, stress pnl percentile {p['current_stress_pnl_vs_random_percentile']:.1f}, p(random>=current)={p['p_value_ge_current_pnl']:.3f}.",
        f"2. buy-and-hold 대비 초과손익 {p['excess_vs_buy_hold_krw']:.2f}, MDD 감소액 {p['mdd_reduction_vs_buy_hold_krw']:.2f}.",
        f"3. naive momentum 대비 초과손익 {p['excess_vs_naive_krw']:.2f}.",
        "",
        "## 연도별 방어성",
        json.dumps(summary["by_year"], ensure_ascii=False, indent=2),
        "",
        "## ticker-year count",
        json.dumps(summary["ticker_year_counts"], ensure_ascii=False, indent=2),
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "scenario": scenario, "portfolio": summary["portfolio"], "by_year": by_year, "counts": summary["ticker_year_counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
