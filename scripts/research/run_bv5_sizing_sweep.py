#!/usr/bin/env python3
"""BV-5 sizing sweep experiment.

Read-only experiment: original promoted rulebooks are never modified.
In-memory virtual rulebooks multiply base_position_ratio by sizing_factor and cap at 1.0,
matching calc_position_size_krw's per-symbol cap behavior.
Writes only under data/_system/research/bv5_20260607/.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.rulebook import Rulebook
from scripts.research.run_bv1_lift import (
    COMMISSION,
    DATA_END,
    DATA_YEARS,
    POSITION_LIMIT,
    load_rulebook,
    run_rulebook_baseline,
    safe_float,
    summarize,
)
from scripts.research.run_bv2_risk_lift import STRESS_COMMISSION, STRESS_SLIPPAGE, risk_metrics, stress_trade, summary_with_risk

OUT = Path("data/_system/research/bv5_20260607")
YEARS_DEFAULT = [2022, 2023, 2024, 2025]
FACTORS_DEFAULT = [1.0, 1.5, 2.0]
FULL_CAPACITY = 85 * POSITION_LIMIT


def make_scaled_rulebook(rb: Rulebook, factor: float) -> Rulebook:
    data = rb.to_dict()
    base = safe_float(data.get("base_position_ratio"), 1.0)
    data["base_position_ratio"] = max(0.0, min(base * factor, 1.0))
    data["sizing_factor_experiment"] = factor
    data["original_base_position_ratio"] = base
    return Rulebook.from_dict(data)


def d(v: Any):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def entry_notional(row: dict[str, Any]) -> float:
    return abs(safe_float(row.get("entry_price")) * safe_float(row.get("entry_shares")))


def total_notional(row: dict[str, Any]) -> float:
    shares = safe_float(row.get("total_shares"), safe_float(row.get("entry_shares")))
    return abs(safe_float(row.get("avg_cost"), safe_float(row.get("entry_price"))) * shares)


def exposure_curve(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dates = []
    for r in rows:
        ed = d(r.get("entry_date")); xd = d(r.get("exit_date"))
        if ed: dates.append(ed)
        if xd: dates.append(xd)
    if not dates:
        return []
    cur = min(dates); last = max(dates)
    out = []
    while cur <= last:
        active = [r for r in rows if d(r.get("entry_date")) and d(r.get("exit_date")) and d(r.get("entry_date")) <= cur <= d(r.get("exit_date"))]
        exposure = sum(total_notional(r) for r in active)
        out.append({
            "date": cur.isoformat(),
            "active_positions": len(active),
            "active_exposure_krw": exposure,
            "active_exposure_pct_of_85x120k": exposure / FULL_CAPACITY * 100.0,
            "capital_exceeded": exposure > FULL_CAPACITY,
        })
        cur += timedelta(days=1)
    return out


def quant(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    xs = sorted(vals)
    pos = (len(xs) - 1) * q
    lo = int(pos); hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def exposure_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    curve = exposure_curve(rows)
    exp = [safe_float(r["active_exposure_pct_of_85x120k"]) for r in curve]
    active = [int(r["active_positions"]) for r in curve]
    entries = [entry_notional(r) for r in rows]
    ratios = [x / POSITION_LIMIT * 100.0 for x in entries]
    return {
        "calendar_days": len(curve),
        "avg_exposure_pct": statistics.mean(exp) if exp else 0.0,
        "p95_exposure_pct": quant(exp, 0.95),
        "max_exposure_pct": max(exp) if exp else 0.0,
        "avg_active_positions": statistics.mean(active) if active else 0.0,
        "max_active_positions": max(active) if active else 0,
        "capital_exceeded_days": sum(1 for r in curve if r["capital_exceeded"]),
        "avg_entry_notional": statistics.mean(entries) if entries else 0.0,
        "median_entry_notional": statistics.median(entries) if entries else 0.0,
        "avg_entry_ratio_to_120k_pct": statistics.mean(ratios) if ratios else 0.0,
        "median_entry_ratio_to_120k_pct": statistics.median(ratios) if ratios else 0.0,
        "invested_return_pct": sum(safe_float(r.get("pnl_krw")) for r in rows) / sum(entries) * 100.0 if entries and sum(entries) else 0.0,
    }


def combined_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = summary_with_risk(rows)
    s.update(exposure_summary(rows))
    return s


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def run_factor(factor: float, symbols: list[str], years: list[int], market_history) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    by_ticker_year: list[dict[str, Any]] = []
    for si, ticker in enumerate(symbols, 1):
        print(f"[factor {factor:g}] [{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            rb, member_hash = load_rulebook(ticker)
            scaled = make_scaled_rulebook(rb, factor)
            original_base = safe_float(rb.to_dict().get("base_position_ratio"), 1.0)
            scaled_base = safe_float(scaled.to_dict().get("base_position_ratio"), 1.0)
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
        except Exception as exc:
            by_ticker_year.append({"sizing_factor": factor, "ticker": ticker, "error": str(exc)})
            continue
        for year in years:
            trs = run_rulebook_baseline(
                ticker,
                year,
                f"current_sizing_{factor:g}x",
                scaled,
                df,
                market_history,
                ticker_sentiment,
                member_hash,
            )
            for r in trs:
                r["sizing_factor"] = factor
                r["original_base_position_ratio"] = original_base
                r["scaled_base_position_ratio"] = scaled_base
            rows.extend(trs)
            base_s = combined_summary(trs)
            stress_s = combined_summary([stress_trade(r) for r in trs])
            by_ticker_year.append({
                "sizing_factor": factor,
                "ticker": ticker,
                "year": year,
                "original_base_position_ratio": original_base,
                "scaled_base_position_ratio": scaled_base,
                "summary": base_s,
                "stress": stress_s,
            })
    return rows, by_ticker_year


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factors", default=",".join(str(x) for x in FACTORS_DEFAULT))
    ap.add_argument("--years", default=",".join(str(y) for y in YEARS_DEFAULT))
    ap.add_argument("--sample-size", type=int, default=None)
    args = ap.parse_args()
    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    factors = [float(x.strip()) for x in args.factors.split(",") if x.strip()]
    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    symbols = list(load_live_universe(LiveUniverseConfig(market="US")).symbols)
    if args.sample_size is not None:
        symbols = symbols[: args.sample_size]
    market_history = get_market_history(years=DATA_YEARS)

    all_rows: list[dict[str, Any]] = []
    ty_rows: list[dict[str, Any]] = []
    for factor in factors:
        rows, ty = run_factor(factor, symbols, years, market_history)
        all_rows.extend(rows)
        ty_rows.extend(ty)

    trades_path = OUT / "bv5_sizing_trades.jsonl"
    ty_path = OUT / "bv5_by_ticker_year.jsonl"
    summary_path = OUT / "bv5_summary.json"
    report_path = OUT / "bv5_report.md"
    exposure_path = OUT / "bv5_exposure_daily.jsonl"
    write_jsonl(trades_path, all_rows)
    write_jsonl(ty_path, ty_rows)

    summary_by_factor = []
    exposure_rows = []
    for factor in factors:
        fr = [r for r in all_rows if safe_float(r.get("sizing_factor")) == factor]
        stress_rows = [stress_trade(r) for r in fr]
        base = combined_summary(fr)
        stress = combined_summary(stress_rows)
        curve = exposure_curve(fr)
        for c in curve:
            c["sizing_factor"] = factor
            exposure_rows.append(c)
        summary_by_factor.append({
            "sizing_factor": factor,
            "summary": base,
            "stress": stress,
            "mdd_multiplier_vs_1x": None,
            "pnl_multiplier_vs_1x": None,
            "edge_retention_invested_return_vs_1x": None,
        })
    base1 = next((x for x in summary_by_factor if abs(x["sizing_factor"] - 1.0) < 1e-9), None)
    if base1:
        for item in summary_by_factor:
            s = item["summary"]
            item["mdd_multiplier_vs_1x"] = s["mdd_krw"] / base1["summary"]["mdd_krw"] if base1["summary"]["mdd_krw"] else None
            item["pnl_multiplier_vs_1x"] = s["total_pnl_krw"] / base1["summary"]["total_pnl_krw"] if base1["summary"]["total_pnl_krw"] else None
            item["edge_retention_invested_return_vs_1x"] = s["invested_return_pct"] / base1["summary"]["invested_return_pct"] if base1["summary"]["invested_return_pct"] else None
    write_jsonl(exposure_path, exposure_rows)

    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {
            "symbols": symbols,
            "symbol_count": len(symbols),
            "years": years,
            "sizing_factors": factors,
            "position_limit": POSITION_LIMIT,
            "full_capacity_85x120k": FULL_CAPACITY,
            "commission": COMMISSION,
            "stress_commission": STRESS_COMMISSION,
            "stress_slippage": STRESS_SLIPPAGE,
        },
        "files": {
            "trades": str(trades_path),
            "by_ticker_year": str(ty_path),
            "exposure_daily": str(exposure_path),
        },
        "summary_by_factor": summary_by_factor,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# BV-5 sizing sweep report",
        "",
        f"- 실행시간: {summary['elapsed_seconds']:.1f}초",
        f"- 종목수: {len(symbols)}",
        f"- 연도: {years}",
        f"- 원거래: `{trades_path}`",
        f"- daily exposure: `{exposure_path}`",
        "",
        "## 사이징 단계별 비교",
        "| factor | trades | total_pnl | invested_return_pct | MDD | MDD x | avg_exposure | p95_exposure | max_exposure | capital_exceeded_days | stress_pnl | pnl x | edge_retention |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary_by_factor:
        s = item["summary"]
        st = item["stress"]
        lines.append(
            f"| {item['sizing_factor']:.2f} | {s['trade_count']} | {s['total_pnl_krw']:.0f} | {s['invested_return_pct']:.2f} | {s['mdd_krw']:.0f} | "
            f"{(item['mdd_multiplier_vs_1x'] or 0):.2f} | {s['avg_exposure_pct']:.1f}% | {s['p95_exposure_pct']:.1f}% | {s['max_exposure_pct']:.1f}% | "
            f"{s['capital_exceeded_days']} | {st['total_pnl_krw']:.0f} | {(item['pnl_multiplier_vs_1x'] or 0):.2f} | {(item['edge_retention_invested_return_vs_1x'] or 0):.2f} |"
        )
    lines.extend([
        "",
        "## 판정 메모",
    ])
    if base1:
        two = next((x for x in summary_by_factor if abs(x["sizing_factor"] - 2.0) < 1e-9), None)
        if two:
            lines.append(f"- 2.0x 총손익 배수: {(two['pnl_multiplier_vs_1x'] or 0):.2f}x")
            lines.append(f"- 2.0x MDD 배수: {(two['mdd_multiplier_vs_1x'] or 0):.2f}x")
            lines.append(f"- 2.0x 투입자본 대비 수익률 유지율: {(two['edge_retention_invested_return_vs_1x'] or 0):.2f}x")
            lines.append(f"- 2.0x stress 손익: {two['stress']['total_pnl_krw']:.0f}")
            lines.append(f"- 2.0x capital exceeded days: {two['summary']['capital_exceeded_days']}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary_by_factor": summary_by_factor}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
