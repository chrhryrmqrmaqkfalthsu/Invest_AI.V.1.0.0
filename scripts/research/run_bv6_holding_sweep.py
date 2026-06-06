#!/usr/bin/env python3
"""BV-6 holding-period / exit-distance sweep experiment.

Read-only experiment: promoted rulebooks are never modified.
Creates in-memory virtual rulebooks with:
- BV-5 approved sizing fixed at 2.0x base_position_ratio, capped at 1.0
- take-profit / trailing ATR distance sweep
- max_holding_days sweep

Writes only under data/_system/research/bv6_20260607/.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from engine.core.data_loader import load_ohlcv
from engine.core.feature_lag import DEFAULT_LAG_DAYS, DEFAULT_MAX_AGE_DAYS, lookup_lagged_daily_dict, lookup_market_at_lagged
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook
from scripts.research.run_bv1_lift import (
    COMMISSION,
    DATA_END,
    DATA_YEARS,
    POSITION_LIMIT,
    WARMUP,
    load_rulebook,
    run_rulebook_baseline,
    safe_float,
)
from scripts.research.run_bv2_risk_lift import STRESS_COMMISSION, STRESS_SLIPPAGE, stress_trade
from scripts.research.run_bv5_sizing_sweep import FULL_CAPACITY, combined_summary, exposure_curve, write_jsonl

OUT = Path("data/_system/research/bv6_20260607")
YEARS_DEFAULT = [2022, 2023, 2024, 2025]
SIZING_FACTOR = 2.0

# Full grid: TP/current-1.5-2.0 × max_holding/current-1.5.
# trailing ATR is widened together with TP because otherwise trailing exits can dominate
# and mask the holding-period experiment.
STAGES_DEFAULT = [
    {"stage": "tp1_h1", "tp_factor": 1.0, "trailing_factor": 1.0, "holding_factor": 1.0},
    {"stage": "tp1_5_h1", "tp_factor": 1.5, "trailing_factor": 1.5, "holding_factor": 1.0},
    {"stage": "tp2_h1", "tp_factor": 2.0, "trailing_factor": 2.0, "holding_factor": 1.0},
    {"stage": "tp1_h1_5", "tp_factor": 1.0, "trailing_factor": 1.0, "holding_factor": 1.5},
    {"stage": "tp1_5_h1_5", "tp_factor": 1.5, "trailing_factor": 1.5, "holding_factor": 1.5},
    {"stage": "tp2_h1_5", "tp_factor": 2.0, "trailing_factor": 2.0, "holding_factor": 1.5},
]


def parse_stage_filter(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return list(STAGES_DEFAULT)
    wanted = {x.strip() for x in raw.split(",") if x.strip()}
    stages = [s for s in STAGES_DEFAULT if s["stage"] in wanted]
    if not stages:
        raise SystemExit(f"No matching stages in {sorted(wanted)}")
    return stages


def make_virtual_rulebook(rb: Rulebook, stage: dict[str, Any]) -> Rulebook:
    data = rb.to_dict()

    base = safe_float(data.get("base_position_ratio"), 1.0)
    data["base_position_ratio"] = max(0.0, min(base * SIZING_FACTOR, 1.0))

    tp_factor = safe_float(stage.get("tp_factor"), 1.0)
    trailing_factor = safe_float(stage.get("trailing_factor"), tp_factor)
    holding_factor = safe_float(stage.get("holding_factor"), 1.0)

    for key in ("take_profit_atr", "take_profit_atr_bull"):
        data[key] = max(0.01, safe_float(data.get(key), 0.0) * tp_factor)
    for key in ("trailing_atr", "trailing_atr_volatile"):
        data[key] = max(0.01, safe_float(data.get(key), 0.0) * trailing_factor)
    data["max_holding_days"] = max(1, int(math.ceil(safe_float(data.get("max_holding_days"), 1.0) * holding_factor)))
    return Rulebook.from_dict(data)


def original_exit_snapshot(rb: Rulebook) -> dict[str, Any]:
    return {
        "base_position_ratio": safe_float(getattr(rb, "base_position_ratio", 0.0)),
        "take_profit_atr": safe_float(getattr(rb, "take_profit_atr", 0.0)),
        "take_profit_atr_bull": safe_float(getattr(rb, "take_profit_atr_bull", 0.0)),
        "trailing_atr": safe_float(getattr(rb, "trailing_atr", 0.0)),
        "trailing_atr_volatile": safe_float(getattr(rb, "trailing_atr_volatile", 0.0)),
        "max_holding_days": int(getattr(rb, "max_holding_days", 0) or 0),
        "exit_strategy": str(getattr(rb, "exit_strategy", "")),
    }


def virtual_exit_snapshot(rb: Rulebook) -> dict[str, Any]:
    return original_exit_snapshot(rb)


def exit_reason_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter(str(r.get("exit_reason") or "UNKNOWN") for r in rows)
    total = sum(c.values())
    return {
        "counts": dict(sorted(c.items())),
        "pct": {k: (v / total * 100.0 if total else 0.0) for k, v in sorted(c.items())},
    }


def stage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    s = combined_summary(rows)
    s["exit_reason_breakdown"] = exit_reason_breakdown(rows)
    return s


def run_stage(stage: dict[str, Any], symbols: list[str], years: list[int], market_history) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stage_id = str(stage["stage"])
    rows: list[dict[str, Any]] = []
    by_ticker_year: list[dict[str, Any]] = []
    for si, ticker in enumerate(symbols, 1):
        print(f"[stage {stage_id}] [{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            rb, member_hash = load_rulebook(ticker)
            virtual_rb = make_virtual_rulebook(rb, stage)
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
            original_exit = original_exit_snapshot(rb)
            virtual_exit = virtual_exit_snapshot(virtual_rb)
        except Exception as exc:
            by_ticker_year.append({"stage": stage_id, "ticker": ticker, "error": str(exc)})
            continue

        for year in years:
            trs = run_rulebook_baseline(
                ticker,
                year,
                f"bv6_{stage_id}",
                virtual_rb,
                df,
                market_history,
                ticker_sentiment,
                member_hash,
            )
            for r in trs:
                r["stage"] = stage_id
                r["sizing_factor"] = SIZING_FACTOR
                r["tp_factor"] = safe_float(stage.get("tp_factor"), 1.0)
                r["trailing_factor"] = safe_float(stage.get("trailing_factor"), 1.0)
                r["holding_factor"] = safe_float(stage.get("holding_factor"), 1.0)
                r["original_exit"] = original_exit
                r["virtual_exit"] = virtual_exit
            rows.extend(trs)
            by_ticker_year.append({
                "stage": stage_id,
                "ticker": ticker,
                "year": year,
                "sizing_factor": SIZING_FACTOR,
                "tp_factor": safe_float(stage.get("tp_factor"), 1.0),
                "trailing_factor": safe_float(stage.get("trailing_factor"), 1.0),
                "holding_factor": safe_float(stage.get("holding_factor"), 1.0),
                "original_exit": original_exit,
                "virtual_exit": virtual_exit,
                "summary": stage_summary(trs),
                "stress": stage_summary([stress_trade(r) for r in trs]),
            })
    return rows, by_ticker_year


def parse_date(value: Any):
    if not value:
        return None
    try:
        return pd.Timestamp(str(value)[:10])
    except Exception:
        return None


def idx_for_date(df: pd.DataFrame, value: Any) -> int | None:
    ts = parse_date(value)
    if ts is None:
        return None
    try:
        loc = df.index.get_loc(ts)
        if isinstance(loc, slice):
            return int(loc.start)
        if isinstance(loc, (list, tuple)):
            return int(loc[0]) if loc else None
        if hasattr(loc, "nonzero"):
            nz = loc.nonzero()[0]
            return int(nz[0]) if len(nz) else None
        return int(loc)
    except Exception:
        return None


def classify_signal_index(j: int, entry_indices: set[int], active_intervals: list[tuple[int, int]], cooldown_indices: set[int]) -> str:
    if j in entry_indices:
        return "actual_entry"
    if any(start <= j <= end for start, end in active_intervals):
        return "blocked_existing_position"
    if j in cooldown_indices:
        return "blocked_cooldown"
    return "flat_unentered_signal"


def signal_diagnostics_for_ticker_year(
    ticker: str,
    year: int,
    rb: Rulebook,
    df: pd.DataFrame,
    market_history: pd.DataFrame,
    ticker_sentiment: dict | None,
    baseline_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    start_ts = pd.Timestamp(f"{year}-01-01")
    end_ts = pd.Timestamp(f"{year}-12-31")
    entry_indices: set[int] = set()
    active_intervals: list[tuple[int, int]] = []
    cooldown_indices: set[int] = set()
    for r in baseline_rows:
        ei = idx_for_date(df, r.get("entry_date"))
        xi = idx_for_date(df, r.get("exit_date"))
        if ei is None or xi is None:
            continue
        entry_indices.add(ei)
        active_intervals.append((ei, xi))
        if xi + 1 < len(df):
            cooldown_indices.add(xi + 1)

    counts = Counter()
    score_sum = 0.0
    trading_days_considered = 0
    for i in range(max(WARMUP, 0), len(df)):
        cur_ts = pd.Timestamp(df.index[i])
        if cur_ts < start_ts:
            continue
        if cur_ts > end_ts:
            break
        trading_days_considered += 1

        cur_event_flags: dict[str, int] = {}
        mkt = lookup_market_at_lagged(market_history, df.index[i], lag_days=DEFAULT_LAG_DAYS)
        cur_market = float(mkt.get("score", 50.0))
        cur_sector = float(mkt.get(f"sector_{getattr(rb, 'sector_name', 'tech') or 'tech'}", 50.0))
        cur_vix = float(mkt.get("vix", 18.0))
        for key in (
            "has_war", "has_rate_hike", "has_rate_cut", "has_geopolitical",
            "has_tariff", "has_export_ban", "has_earnings_shock", "has_oil_surge",
            "has_banking_crisis", "has_inflation", "has_fed_statement",
        ):
            cur_event_flags[key] = int(mkt.get(key, 0) or 0)

        cur_sentiment = 0.0
        if ticker_sentiment:
            try:
                s = lookup_lagged_daily_dict(
                    ticker_sentiment,
                    df.index[i],
                    lag_days=DEFAULT_LAG_DAYS,
                    max_age_days=DEFAULT_MAX_AGE_DAYS,
                )
                if s:
                    cur_sentiment = float(s.get("sentiment_avg", 0.0))
            except Exception:
                cur_sentiment = 0.0

        sig = evaluate_signal(
            rb,
            df.iloc[: i + 1],
            market_score=cur_market,
            sector_score=cur_sector,
            vix_level=cur_vix,
            news_sentiment=cur_sentiment,
            event_flags=cur_event_flags,
        )
        if sig.should_buy:
            cls = classify_signal_index(i, entry_indices, active_intervals, cooldown_indices)
            counts[cls] += 1
            counts["signal_days_total"] += 1
            score_sum += float(sig.score)

    actual_entries = len(entry_indices)
    flat_available = counts["actual_entry"] + counts["flat_unentered_signal"]
    return {
        "ticker": ticker,
        "year": year,
        "trading_days_considered": trading_days_considered,
        "actual_entries": actual_entries,
        "signal_days_total": counts["signal_days_total"],
        "actual_entry_signal_days": counts["actual_entry"],
        "blocked_existing_position_signal_days": counts["blocked_existing_position"],
        "blocked_cooldown_signal_days": counts["blocked_cooldown"],
        "flat_unentered_signal_days": counts["flat_unentered_signal"],
        "flat_available_signal_days": flat_available,
        "entry_to_signal_ratio_pct": actual_entries / counts["signal_days_total"] * 100.0 if counts["signal_days_total"] else 0.0,
        "entry_to_flat_available_signal_ratio_pct": actual_entries / flat_available * 100.0 if flat_available else 0.0,
        "blocked_existing_signal_ratio_pct": counts["blocked_existing_position"] / counts["signal_days_total"] * 100.0 if counts["signal_days_total"] else 0.0,
        "avg_signal_score": score_sum / counts["signal_days_total"] if counts["signal_days_total"] else 0.0,
    }


def run_signal_diagnostics(symbols: list[str], years: list[int], market_history, baseline_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_ticker_year: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for r in baseline_rows:
        rows_by_ticker_year[(str(r.get("ticker")), int(r.get("year") or 0))].append(r)

    diagnostics: list[dict[str, Any]] = []
    for si, ticker in enumerate(symbols, 1):
        print(f"[signal-diagnostic] [{si}/{len(symbols)}] {ticker}", flush=True)
        try:
            rb, _ = load_rulebook(ticker)
            # Sizing/exit values do not affect the signal; use the same 2.0x baseline wrapper for metadata consistency.
            rb = make_virtual_rulebook(rb, STAGES_DEFAULT[0])
            df = load_ohlcv(ticker, years=DATA_YEARS, end_date=DATA_END, use_cache=True, max_retries=1).sort_index()
            ticker_sentiment = load_ticker_sentiment(ticker)
        except Exception as exc:
            diagnostics.append({"ticker": ticker, "error": str(exc)})
            continue
        for year in years:
            diagnostics.append(
                signal_diagnostics_for_ticker_year(
                    ticker,
                    year,
                    rb,
                    df,
                    market_history,
                    ticker_sentiment,
                    rows_by_ticker_year.get((ticker, year), []),
                )
            )
    return diagnostics


def aggregate_signal_diagnostics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if "error" not in r]
    keys = [
        "trading_days_considered",
        "actual_entries",
        "signal_days_total",
        "actual_entry_signal_days",
        "blocked_existing_position_signal_days",
        "blocked_cooldown_signal_days",
        "flat_unentered_signal_days",
        "flat_available_signal_days",
    ]
    agg = {k: sum(int(r.get(k) or 0) for r in valid) for k in keys}
    total_signals = agg["signal_days_total"]
    flat_available = agg["flat_available_signal_days"]
    agg.update({
        "ticker_years": len(valid),
        "entry_to_signal_ratio_pct": agg["actual_entries"] / total_signals * 100.0 if total_signals else 0.0,
        "entry_to_flat_available_signal_ratio_pct": agg["actual_entries"] / flat_available * 100.0 if flat_available else 0.0,
        "blocked_existing_signal_ratio_pct": agg["blocked_existing_position_signal_days"] / total_signals * 100.0 if total_signals else 0.0,
        "blocked_cooldown_signal_ratio_pct": agg["blocked_cooldown_signal_days"] / total_signals * 100.0 if total_signals else 0.0,
        "flat_unentered_signal_ratio_pct": agg["flat_unentered_signal_days"] / total_signals * 100.0 if total_signals else 0.0,
        "avg_signal_score": statistics.mean([safe_float(r.get("avg_signal_score")) for r in valid if safe_float(r.get("avg_signal_score")) > 0.0]) if valid else 0.0,
    })
    return agg


def add_stage_multipliers(summary_by_stage: list[dict[str, Any]], baseline_stage: str) -> None:
    base = next((x for x in summary_by_stage if x["stage"] == baseline_stage), None)
    if not base:
        return
    bs = base["summary"]
    for item in summary_by_stage:
        s = item["summary"]
        item["pnl_multiplier_vs_baseline"] = s["total_pnl_krw"] / bs["total_pnl_krw"] if bs["total_pnl_krw"] else None
        item["mdd_multiplier_vs_baseline"] = s["mdd_krw"] / bs["mdd_krw"] if bs["mdd_krw"] else None
        item["exposure_multiplier_vs_baseline"] = s["avg_exposure_pct"] / bs["avg_exposure_pct"] if bs["avg_exposure_pct"] else None
        item["active_multiplier_vs_baseline"] = s["avg_active_positions"] / bs["avg_active_positions"] if bs["avg_active_positions"] else None
        item["holding_multiplier_vs_baseline"] = s["avg_holding_days"] / bs["avg_holding_days"] if bs["avg_holding_days"] else None
        item["invested_return_delta_pctp_vs_baseline"] = s["invested_return_pct"] - bs["invested_return_pct"]


def best_stage(summary_by_stage: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not summary_by_stage:
        return None
    # Prefer high total pnl with positive stress and avoid severe MDD blow-up.
    viable = [x for x in summary_by_stage if x["stress"]["total_pnl_krw"] > 0]
    pool = viable or summary_by_stage
    return max(pool, key=lambda x: (x["summary"].get("return_over_mdd", 0.0), x["summary"].get("total_pnl_krw", 0.0)))


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
    for stage in stages:
        rows, ty = run_stage(stage, symbols, years, market_history)
        all_rows.extend(rows)
        by_ticker_year.extend(ty)

    baseline_stage = stages[0]["stage"]
    baseline_rows = [r for r in all_rows if r.get("stage") == baseline_stage]
    signal_diag_rows = run_signal_diagnostics(symbols, years, market_history, baseline_rows)
    signal_diag_summary = aggregate_signal_diagnostics(signal_diag_rows)

    trades_path = OUT / "bv6_holding_trades.jsonl"
    ty_path = OUT / "bv6_by_ticker_year.jsonl"
    exposure_path = OUT / "bv6_exposure_daily.jsonl"
    signal_diag_path = OUT / "bv6_signal_diagnostics.jsonl"
    summary_path = OUT / "bv6_summary.json"
    report_path = OUT / "bv6_report.md"

    write_jsonl(trades_path, all_rows)
    write_jsonl(ty_path, by_ticker_year)
    write_jsonl(signal_diag_path, signal_diag_rows)

    summary_by_stage: list[dict[str, Any]] = []
    exposure_rows: list[dict[str, Any]] = []
    for stage in stages:
        stage_id = stage["stage"]
        rows = [r for r in all_rows if r.get("stage") == stage_id]
        stress_rows = [stress_trade(r) for r in rows]
        base_summary = stage_summary(rows)
        stress_summary = stage_summary(stress_rows)
        curve = exposure_curve(rows)
        for c in curve:
            c["stage"] = stage_id
            c["tp_factor"] = safe_float(stage.get("tp_factor"), 1.0)
            c["trailing_factor"] = safe_float(stage.get("trailing_factor"), 1.0)
            c["holding_factor"] = safe_float(stage.get("holding_factor"), 1.0)
            exposure_rows.append(c)
        summary_by_stage.append({
            "stage": stage_id,
            "tp_factor": safe_float(stage.get("tp_factor"), 1.0),
            "trailing_factor": safe_float(stage.get("trailing_factor"), 1.0),
            "holding_factor": safe_float(stage.get("holding_factor"), 1.0),
            "sizing_factor": SIZING_FACTOR,
            "summary": base_summary,
            "stress": stress_summary,
            "pnl_multiplier_vs_baseline": None,
            "mdd_multiplier_vs_baseline": None,
            "exposure_multiplier_vs_baseline": None,
            "active_multiplier_vs_baseline": None,
            "holding_multiplier_vs_baseline": None,
            "invested_return_delta_pctp_vs_baseline": None,
        })
    add_stage_multipliers(summary_by_stage, baseline_stage)
    write_jsonl(exposure_path, exposure_rows)

    chosen = best_stage(summary_by_stage)
    summary = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "elapsed_seconds": time.perf_counter() - t0,
        "config": {
            "symbols": symbols,
            "symbol_count": len(symbols),
            "years": years,
            "sizing_factor": SIZING_FACTOR,
            "stages": stages,
            "position_limit": POSITION_LIMIT,
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
            "signal_diagnostics": str(signal_diag_path),
        },
        "signal_diagnostics": signal_diag_summary,
        "summary_by_stage": summary_by_stage,
        "best_return_over_mdd_stage": chosen["stage"] if chosen else None,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# BV-6 holding-period / exit-distance sweep report",
        "",
        f"- 실행시간: {summary['elapsed_seconds']:.1f}초",
        f"- 종목수: {len(symbols)}",
        f"- 연도: {years}",
        f"- 사이징: BV-5 검증값 {SIZING_FACTOR:.1f}x 고정",
        f"- 원거래: `{trades_path}`",
        f"- by ticker-year: `{ty_path}`",
        f"- daily exposure: `{exposure_path}`",
        f"- signal diagnostics: `{signal_diag_path}`",
        "",
        "## 1) 병목 진단",
        "",
        "현재 baseline은 종목별 백테스트를 합산하는 구조라 글로벌 포트폴리오 슬롯/자본 초과로 주문이 거절되는 로그는 없다. BV-5/BV-6 노출 곡선 기준 capital_exceeded_days로만 자본 한계를 본다.",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| ticker-years | {signal_diag_summary['ticker_years']} |",
        f"| signal_days_total | {signal_diag_summary['signal_days_total']} |",
        f"| actual_entries | {signal_diag_summary['actual_entries']} |",
        f"| entry/signal | {signal_diag_summary['entry_to_signal_ratio_pct']:.1f}% |",
        f"| blocked_existing_position_signal_days | {signal_diag_summary['blocked_existing_position_signal_days']} |",
        f"| blocked_existing/signal | {signal_diag_summary['blocked_existing_signal_ratio_pct']:.1f}% |",
        f"| blocked_cooldown_signal_days | {signal_diag_summary['blocked_cooldown_signal_days']} |",
        f"| flat_unentered_signal_days | {signal_diag_summary['flat_unentered_signal_days']} |",
        "",
        "## 2) 보유기간/청산거리 sweep 비교",
        "",
        "| stage | TP x | trail x | hold x | trades | total_pnl | invested_return_pct | MDD | MDD x | avg_hold | avg_active | avg_exposure | p95_exposure | max_exposure | capital_exceeded_days | stress_pnl | pnl x | exposure x |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary_by_stage:
        s = item["summary"]
        st = item["stress"]
        lines.append(
            f"| {item['stage']} | {item['tp_factor']:.1f} | {item['trailing_factor']:.1f} | {item['holding_factor']:.1f} | "
            f"{s['trade_count']} | {s['total_pnl_krw']:.0f} | {s['invested_return_pct']:.2f} | {s['mdd_krw']:.0f} | "
            f"{(item['mdd_multiplier_vs_baseline'] or 0):.2f} | {s['avg_holding_days']:.2f} | {s['avg_active_positions']:.2f} | "
            f"{s['avg_exposure_pct']:.1f}% | {s['p95_exposure_pct']:.1f}% | {s['max_exposure_pct']:.1f}% | {s['capital_exceeded_days']} | "
            f"{st['total_pnl_krw']:.0f} | {(item['pnl_multiplier_vs_baseline'] or 0):.2f} | {(item['exposure_multiplier_vs_baseline'] or 0):.2f} |"
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
    base = next((x for x in summary_by_stage if x["stage"] == baseline_stage), None)
    if base and chosen:
        bs = base["summary"]
        cs = chosen["summary"]
        lines.append(f"- baseline({baseline_stage}) avg_active {bs['avg_active_positions']:.2f}, avg_exposure {bs['avg_exposure_pct']:.1f}%, invested_return {bs['invested_return_pct']:.2f}%, MDD {bs['mdd_krw']:.0f}.")
        lines.append(f"- best_return_over_mdd({chosen['stage']}) avg_active {cs['avg_active_positions']:.2f}, avg_exposure {cs['avg_exposure_pct']:.1f}%, invested_return {cs['invested_return_pct']:.2f}%, MDD {cs['mdd_krw']:.0f}, stress_pnl {chosen['stress']['total_pnl_krw']:.0f}.")
        lines.append(f"- 노출 배수 {chosen['exposure_multiplier_vs_baseline']:.2f}x, 보유일 배수 {chosen['holding_multiplier_vs_baseline']:.2f}x, MDD 배수 {chosen['mdd_multiplier_vs_baseline']:.2f}x.")
        if cs["avg_exposure_pct"] >= 20.0 and cs["invested_return_pct"] >= bs["invested_return_pct"] * 0.9 and chosen["stress"]["total_pnl_krw"] > 0:
            lines.append("- 판정: 보유기간/청산거리 확대는 노출 병목 해소 후보로 유효하다.")
        elif cs["avg_exposure_pct"] > bs["avg_exposure_pct"] * 1.25 and chosen["stress"]["total_pnl_krw"] > 0:
            lines.append("- 판정: 보유기간/청산거리 확대는 노출을 늘리지만, 20%+ 노출 목표에는 아직 부족하다.")
        else:
            lines.append("- 판정: 보유기간/청산거리 확대만으로는 노출 병목 해소 효과가 약하다. 다음 실험은 신호 빈도 또는 포트폴리오 동시진입 구조를 분리해야 한다.")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "out": str(OUT),
        "signal_diagnostics": signal_diag_summary,
        "best_return_over_mdd_stage": summary["best_return_over_mdd_stage"],
        "summary_by_stage": summary_by_stage,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
