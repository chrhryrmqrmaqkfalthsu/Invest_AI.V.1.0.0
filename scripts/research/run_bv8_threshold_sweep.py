#!/usr/bin/env python3
"""BV-8 signal-threshold dispersion sweep.

Read-only experiment: promoted rulebooks are never modified.
Base virtual rulebook:
- sizing 2.0x
- TP/trailing 1.5x
- conservative re-signal add-buy max 1 (BV-7 safe option)

Only signal_threshold is changed in memory. Outputs are isolated under
`data/_system/research/bv8_20260607/`.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook
from scripts.research.run_bv1_lift import DATA_END, DATA_YEARS, WARMUP, load_rulebook, safe_float
from scripts.research.run_bv2_risk_lift import STRESS_COMMISSION, STRESS_SLIPPAGE
from scripts.research.run_bv5_sizing_sweep import FULL_CAPACITY, quant, write_jsonl
from scripts.research.run_bv7_add_buy_sweep import (
    POSITION_LIMIT,
    SIZING_FACTOR,
    TP_FACTOR,
    TRAILING_FACTOR,
    make_base_rulebook,
    run_stage_for_ticker_year,
    signal_for_bar,
    simulate_resignal_trade,
    summary_bv7,
    stress_trade_bv7,
)

OUT = Path("data/_system/research/bv8_20260607")
YEARS_DEFAULT = [2022, 2023, 2024, 2025]

ADD_BUY_STAGE = {
    "stage": "conservative",
    "label": "max1_size25_trigger1_score3",
    "add_buy_enabled": True,
    "add_buy_trigger_profit_pct": 1.0,
    "add_buy_max_count": 1,
    "add_buy_size_ratio": 0.25,
    "add_buy_min_signal_score": 3.0,
}

THRESHOLD_STAGES_DEFAULT = [
    {"stage": "thr1_00", "threshold_factor": 1.00, "label": "current_threshold"},
    {"stage": "thr0_90", "threshold_factor": 0.90, "label": "threshold_90pct"},
    {"stage": "thr0_80", "threshold_factor": 0.80, "label": "threshold_80pct"},
]


def parse_stage_filter(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return list(THRESHOLD_STAGES_DEFAULT)
    wanted = {x.strip() for x in raw.split(",") if x.strip()}
    out = [s for s in THRESHOLD_STAGES_DEFAULT if s["stage"] in wanted]
    if not out:
        raise SystemExit(f"No matching threshold stages: {sorted(wanted)}")
    return out


def make_threshold_rulebook(rb0: Rulebook, factor: float) -> tuple[Rulebook, float, float]:
    rb = make_base_rulebook(rb0)
    original_threshold = safe_float(getattr(rb, "signal_threshold", 0.0))
    rb.signal_threshold = max(0.1, original_threshold * factor)
    return rb, original_threshold, safe_float(rb.signal_threshold)


def trade_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return (str(row.get("ticker")), int(row.get("year") or 0), str(row.get("entry_date")))


def summarize_subset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return summary_bv7([])
    return summary_bv7(rows)


def add_new_trade_flags(all_rows: list[dict[str, Any]], baseline_stage: str = "thr1_00") -> None:
    base_keys = {trade_key(r) for r in all_rows if r.get("threshold_stage") == baseline_stage}
    for r in all_rows:
        is_new = r.get("threshold_stage") != baseline_stage and trade_key(r) not in base_keys
        r["is_new_trade_vs_current_threshold"] = bool(is_new)
        r["trade_origin"] = "new_from_lower_threshold" if is_new else "baseline_or_retained"


def marginal_signal_scan(
    ticker: str,
    year: int,
    rb0: Rulebook,
    df: pd.DataFrame,
    market_history,
    ticker_sentiment: dict | None,
    member_hash: str,
    threshold_stage: dict[str, Any],
) -> list[dict[str, Any]]:
    factor = safe_float(threshold_stage["threshold_factor"], 1.0)
    if factor >= 1.0:
        return []
    rb_base, original_threshold, _ = make_threshold_rulebook(rb0, 1.0)
    rb_low, _, low_threshold = make_threshold_rulebook(rb0, factor)
    start_ts = pd.Timestamp(f"{year}-01-01")
    end_ts = pd.Timestamp(f"{year}-12-31")
    out: list[dict[str, Any]] = []
    for i in range(max(WARMUP, 0), len(df)):
        cur_ts = pd.Timestamp(df.index[i])
        if cur_ts < start_ts:
            continue
        if cur_ts > end_ts:
            break
        sig_base, *_ = signal_for_bar(rb_base, df, i, market_history, ticker_sentiment)
        if sig_base.should_buy:
            continue
        sig_low, *_ = signal_for_bar(rb_low, df, i, market_history, ticker_sentiment)
        if not sig_low.should_buy:
            continue
        trade_obj, _, add_signal_count, rejected_cap, rejected_conditions = simulate_resignal_trade(
            ticker,
            year,
            rb_low,
            df,
            i,
            ADD_BUY_STAGE,
            market_history,
            ticker_sentiment,
            member_hash,
        )
        if trade_obj is None:
            pnl_krw = 0.0
            pnl_pct = 0.0
            exit_reason = "no_fill"
            holding_days = 0
            total_invested = 0.0
        else:
            td = trade_obj.__dict__ if hasattr(trade_obj, "__dict__") else dict(trade_obj)
            pnl_krw = safe_float(td.get("pnl_krw"))
            pnl_pct = safe_float(td.get("pnl_pct"))
            exit_reason = str(td.get("exit_reason"))
            holding_days = int(td.get("holding_days") or 0)
            add_buys = td.get("add_buys") or []
            add_notional = 0.0
            for add in add_buys:
                if isinstance(add, dict):
                    add_notional += safe_float(add.get("notional"), safe_float(add.get("price")) * safe_float(add.get("shares")))
                else:
                    try:
                        _, p, sh, *_ = add
                        add_notional += safe_float(p) * safe_float(sh)
                    except Exception:
                        pass
            total_invested = safe_float(td.get("entry_price")) * safe_float(td.get("entry_shares")) + add_notional
        out.append({
            "threshold_stage": str(threshold_stage["stage"]),
            "threshold_factor": factor,
            "ticker": ticker,
            "year": year,
            "date": str(df.index[i].date()),
            "score": safe_float(sig_low.score),
            "original_threshold": original_threshold,
            "lowered_threshold": low_threshold,
            "gap_below_original": original_threshold - safe_float(sig_low.score),
            "entry_price": safe_float(df.iloc[i]["Close"]),
            "hypothetical_pnl_krw": pnl_krw,
            "hypothetical_pnl_pct": pnl_pct,
            "hypothetical_total_invested": total_invested,
            "hypothetical_exit_reason": exit_reason,
            "hypothetical_holding_days": holding_days,
            "hypothetical_add_signal_count": add_signal_count,
            "hypothetical_add_signal_rejected_cap": rejected_cap,
            "hypothetical_add_signal_rejected_conditions": rejected_conditions,
        })
    return out


def summarize_marginal(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "count": 0,
            "avg_pnl_pct": 0.0,
            "median_pnl_pct": 0.0,
            "win_rate_pct": 0.0,
            "avg_pnl_krw": 0.0,
            "invested_return_pct": 0.0,
            "avg_score": 0.0,
            "avg_gap_below_original": 0.0,
        }
    pnl_pct = [safe_float(r.get("hypothetical_pnl_pct")) for r in rows]
    pnl_krw = [safe_float(r.get("hypothetical_pnl_krw")) for r in rows]
    invested = [safe_float(r.get("hypothetical_total_invested")) for r in rows]
    return {
        "count": len(rows),
        "avg_pnl_pct": statistics.mean(pnl_pct),
        "median_pnl_pct": statistics.median(pnl_pct),
        "win_rate_pct": sum(1 for x in pnl_krw if x > 0) / len(pnl_krw) * 100.0 if pnl_krw else 0.0,
        "avg_pnl_krw": statistics.mean(pnl_krw),
        "invested_return_pct": sum(pnl_krw) / sum(invested) * 100.0 if sum(invested) else 0.0,
        "avg_score": statistics.mean([safe_float(r.get("score")) for r in rows]),
        "avg_gap_below_original": statistics.mean([safe_float(r.get("gap_below_original")) for r in rows]),
    }


def per_trade_max_limit_pct(rows: list[dict[str, Any]]) -> float:
    vals = [safe_float(r.get("total_invested_notional")) / POSITION_LIMIT * 100.0 for r in rows]
    return max(vals) if vals else 0.0


def stage_summary_dict(stage: dict[str, Any], rows: list[dict[str, Any]], baseline_summary: dict[str, Any] | None) -> dict[str, Any]:
    stress_rows = [stress_trade_bv7(r) for r in rows]
    s = summary_bv7(rows)
    st = summary_bv7(stress_rows)
    new_rows = [r for r in rows if r.get("is_new_trade_vs_current_threshold")]
    retained_rows = [r for r in rows if not r.get("is_new_trade_vs_current_threshold")]
    ns = summarize_subset(new_rows)
    rs = summarize_subset(retained_rows)
    out = {
        "threshold_stage": stage["stage"],
        "label": stage["label"],
        "threshold_factor": safe_float(stage["threshold_factor"]),
        "summary": s,
        "stress": st,
        "new_trades": ns,
        "retained_trades": rs,
        "new_trade_count": len(new_rows),
        "retained_trade_count": len(retained_rows),
        "max_trade_pct_of_120k": per_trade_max_limit_pct(rows),
        "pnl_multiplier_vs_current": None,
        "mdd_multiplier_vs_current": None,
        "exposure_multiplier_vs_current": None,
        "invested_return_delta_pctp_vs_current": None,
        "active_multiplier_vs_current": None,
    }
    if baseline_summary:
        bs = baseline_summary
        out["pnl_multiplier_vs_current"] = s["total_pnl_krw"] / bs["total_pnl_krw"] if bs.get("total_pnl_krw") else None
        out["mdd_multiplier_vs_current"] = s["mdd_krw"] / bs["mdd_krw"] if bs.get("mdd_krw") else None
        out["exposure_multiplier_vs_current"] = s["avg_exposure_pct"] / bs["avg_exposure_pct"] if bs.get("avg_exposure_pct") else None
        out["active_multiplier_vs_current"] = s["avg_active_positions"] / bs["avg_active_positions"] if bs.get("avg_active_positions") else None
        out["invested_return_delta_pctp_vs_current"] = s["invested_return_pct"] - bs.get("invested_return_pct", 0.0)
    return out


def render_report(summary: dict[str, Any], report_path: Path) -> None:
    marginal = summary["marginal_signal_quality"]
    rows = summary["summary_by_stage"]
    lines = [
        "# BV-8 signal-threshold dispersion sweep report",
        "",
        f"- 실행시간: {summary['elapsed_seconds']:.1f}초",
        f"- 종목수: {summary['config']['symbol_count']}",
        f"- 연도: {summary['config']['years']}",
        f"- 기준 룰북: sizing {SIZING_FACTOR:.1f}x + TP/trailing {TP_FACTOR:.1f}x + conservative add-buy 1회",
        f"- 원거래: `{summary['files']['trades']}`",
        f"- by ticker-year: `{summary['files']['by_ticker_year']}`",
        f"- marginal diagnostics: `{summary['files']['marginal_signals']}`",
        "",
        "## 1) 한계 신호 품질 진단",
        "",
        "| threshold stage | marginal signals | avg pnl % | median pnl % | win rate | invested return | avg score | avg gap below original |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for stage_id, m in marginal.items():
        lines.append(
            f"| {stage_id} | {m['count']} | {m['avg_pnl_pct']:.2f}% | {m['median_pnl_pct']:.2f}% | "
            f"{m['win_rate_pct']:.1f}% | {m['invested_return_pct']:.2f}% | {m['avg_score']:.2f} | {m['avg_gap_below_original']:.2f} |"
        )
    lines.extend([
        "",
        "## 2) threshold sweep 비교",
        "",
        "| stage | threshold x | trades | new trades | total pnl | invested return | MDD | MDD x | avg active | avg exposure | p95 exposure | max exposure | stress pnl | pnl x | exposure x | capital exceeded |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for item in rows:
        s = item["summary"]
        st = item["stress"]
        lines.append(
            f"| {item['threshold_stage']} | {item['threshold_factor']:.2f} | {s['trade_count']} | {item['new_trade_count']} | "
            f"{s['total_pnl_krw']:.0f} | {s['invested_return_pct']:.2f}% | {s['mdd_krw']:.0f} | {(item['mdd_multiplier_vs_current'] or 0):.2f} | "
            f"{s['avg_active_positions']:.2f} | {s['avg_exposure_pct']:.1f}% | {s['p95_exposure_pct']:.1f}% | {s['max_exposure_pct']:.1f}% | "
            f"{st['total_pnl_krw']:.0f} | {(item['pnl_multiplier_vs_current'] or 0):.2f} | {(item['exposure_multiplier_vs_current'] or 0):.2f} | {s['capital_exceeded_days']} |"
        )
    lines.extend([
        "",
        "## 3) 신규거래 품질 분해",
        "",
        "| stage | new trades | new pnl | new invested return | new win rate | new expectancy | retained invested return |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for item in rows:
        ns = item["new_trades"]
        rs = item["retained_trades"]
        lines.append(
            f"| {item['threshold_stage']} | {item['new_trade_count']} | {ns['total_pnl_krw']:.0f} | {ns['invested_return_pct']:.2f}% | "
            f"{ns['win_rate_pct']:.1f}% | {ns['expectancy_krw']:.0f} | {rs['invested_return_pct']:.2f}% |"
        )
    lines.extend([
        "",
        "## 4) 판정 메모",
    ])
    base = next((x for x in rows if x["threshold_stage"] == "thr1_00"), None)
    best = summary.get("best_return_over_mdd_stage")
    if base:
        bs = base["summary"]
        lines.append(f"- baseline(thr1_00): avg_exposure {bs['avg_exposure_pct']:.1f}%, avg_active {bs['avg_active_positions']:.2f}, invested_return {bs['invested_return_pct']:.2f}%, MDD {bs['mdd_krw']:.0f}.")
    if best:
        b = next((x for x in rows if x["threshold_stage"] == best), None)
        if b:
            s = b["summary"]
            lines.append(f"- best_return_over_mdd({best}): avg_exposure {s['avg_exposure_pct']:.1f}%, avg_active {s['avg_active_positions']:.2f}, invested_return {s['invested_return_pct']:.2f}%, MDD {s['mdd_krw']:.0f}, stress_pnl {b['stress']['total_pnl_krw']:.0f}.")
    # Overall verdict from most aggressive stage.
    last = rows[-1] if rows else None
    if base and last:
        bs = base["summary"]
        ls = last["summary"]
        if ls["avg_exposure_pct"] >= 20 and ls["invested_return_pct"] >= bs["invested_return_pct"] * 0.9 and last["stress"]["total_pnl_krw"] > 0:
            verdict = "문턱 인하는 20%+ 노출 목표 후보로 유효하다. 수익률도 대부분 유지됐다."
        elif ls["avg_exposure_pct"] > bs["avg_exposure_pct"] * 1.25 and ls["invested_return_pct"] > 0 and last["stress"]["total_pnl_krw"] > 0:
            verdict = "문턱 인하는 노출을 늘리지만 수익률 희석이 있다. 균형점 선택 문제다."
        else:
            verdict = "문턱 인하만으로도 20%+ 노출에는 부족하거나, 수익률 희석 대비 노출 증가가 작다."
        lines.append(f"- 판정: {verdict}")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default=",".join(str(y) for y in YEARS_DEFAULT))
    ap.add_argument("--sample-size", type=int, default=None)
    ap.add_argument("--stages", default=None, help="comma-separated threshold stages")
    args = ap.parse_args()

    t0 = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=True)
    years = [int(x.strip()) for x in args.years.split(",") if x.strip()]
    threshold_stages = parse_stage_filter(args.stages)
    symbols = list(load_live_universe(LiveUniverseConfig(market="US")).symbols)
    if args.sample_size is not None:
        symbols = symbols[: args.sample_size]
    market_history = get_market_history(years=DATA_YEARS)

    all_rows: list[dict[str, Any]] = []
    by_ticker_year: list[dict[str, Any]] = []
    marginal_rows: list[dict[str, Any]] = []

    for si, ticker in enumerate(symbols, 1):
        print(f"[{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            rb0, member_hash = load_rulebook(ticker)
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
        except Exception as exc:
            for ts in threshold_stages:
                by_ticker_year.append({"threshold_stage": ts["stage"], "ticker": ticker, "error": str(exc)})
            continue
        for ts in threshold_stages:
            rb, original_threshold, lowered_threshold = make_threshold_rulebook(rb0, safe_float(ts["threshold_factor"], 1.0))
            for year in years:
                rows, _ = run_stage_for_ticker_year(
                    ticker,
                    year,
                    rb,
                    df,
                    market_history,
                    ticker_sentiment,
                    member_hash,
                    ADD_BUY_STAGE,
                    collect_resignals=False,
                )
                for r in rows:
                    r["threshold_stage"] = ts["stage"]
                    r["threshold_label"] = ts["label"]
                    r["threshold_factor"] = safe_float(ts["threshold_factor"], 1.0)
                    r["original_signal_threshold"] = original_threshold
                    r["virtual_signal_threshold"] = lowered_threshold
                all_rows.extend(rows)
                by_ticker_year.append({
                    "threshold_stage": ts["stage"],
                    "threshold_factor": safe_float(ts["threshold_factor"], 1.0),
                    "ticker": ticker,
                    "year": year,
                    "trade_count": len(rows),
                    "summary": summary_bv7(rows),
                    "stress": summary_bv7([stress_trade_bv7(r) for r in rows]),
                })
        for ts in threshold_stages:
            if safe_float(ts["threshold_factor"], 1.0) >= 1.0:
                continue
            for year in years:
                marginal_rows.extend(marginal_signal_scan(ticker, year, rb0, df, market_history, ticker_sentiment, member_hash, ts))

    add_new_trade_flags(all_rows)

    trades_path = OUT / "bv8_threshold_trades.jsonl"
    ty_path = OUT / "bv8_by_ticker_year.jsonl"
    marginal_path = OUT / "bv8_marginal_signals.jsonl"
    summary_path = OUT / "bv8_summary.json"
    report_path = OUT / "bv8_report.md"

    write_jsonl(trades_path, all_rows)
    write_jsonl(ty_path, by_ticker_year)
    write_jsonl(marginal_path, marginal_rows)

    baseline_rows = [r for r in all_rows if r.get("threshold_stage") == "thr1_00"]
    baseline_summary = summary_bv7(baseline_rows)
    summary_by_stage = []
    for ts in threshold_stages:
        rows = [r for r in all_rows if r.get("threshold_stage") == ts["stage"]]
        summary_by_stage.append(stage_summary_dict(ts, rows, baseline_summary))

    marginal_quality = {}
    for ts in threshold_stages:
        if safe_float(ts["threshold_factor"], 1.0) >= 1.0:
            continue
        rows = [r for r in marginal_rows if r.get("threshold_stage") == ts["stage"]]
        marginal_quality[ts["stage"]] = summarize_marginal(rows)

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
                "add_buy_stage": ADD_BUY_STAGE,
                "per_symbol_position_limit": POSITION_LIMIT,
                "full_capacity_85x120k": FULL_CAPACITY,
            },
            "threshold_stages": threshold_stages,
            "stress_commission": STRESS_COMMISSION,
            "stress_slippage": STRESS_SLIPPAGE,
        },
        "files": {
            "trades": str(trades_path),
            "by_ticker_year": str(ty_path),
            "marginal_signals": str(marginal_path),
        },
        "marginal_signal_quality": marginal_quality,
        "summary_by_stage": summary_by_stage,
        "best_return_over_mdd_stage": best["threshold_stage"] if best else None,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    render_report(summary, report_path)

    print(json.dumps({
        "out": str(OUT),
        "marginal_signal_quality": marginal_quality,
        "best_return_over_mdd_stage": summary["best_return_over_mdd_stage"],
        "summary_by_stage": summary_by_stage,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
