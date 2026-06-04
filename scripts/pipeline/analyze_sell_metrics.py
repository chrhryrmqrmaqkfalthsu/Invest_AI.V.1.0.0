#!/usr/bin/env python3
"""Sell-side metrics preview from rolling_validation.json trades.

Read-only against pipeline run outputs. Designed to be safe while a batch is
still running: JSON files that are being written or are temporarily invalid are
skipped and counted.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "data/_system/pipeline/v1/runs"
DEFAULT_RUN_ID = "au_1173_20260604"
EXIT_REASON_ORDER = ["trailing", "time_out", "stop_loss", "take_profit"]


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def percentile(values: list[float], p: float) -> float | None:
    clean = sorted(v for v in values if safe_float(v) is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return float(clean[0])
    rank = (len(clean) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(clean) - 1)
    frac = rank - lo
    return float(clean[lo] * (1.0 - frac) + clean[hi] * frac)


def avg(values: list[float]) -> float | None:
    clean = [float(v) for v in values if safe_float(v) is not None]
    return sum(clean) / len(clean) if clean else None


def distribution(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if safe_float(v) is not None]
    return {
        "count": len(clean),
        "avg": avg(clean),
        "min": percentile(clean, 0.0),
        "p10": percentile(clean, 0.10),
        "p25": percentile(clean, 0.25),
        "p50": percentile(clean, 0.50),
        "median": median(clean) if clean else None,
        "p75": percentile(clean, 0.75),
        "p90": percentile(clean, 0.90),
        "max": percentile(clean, 1.0),
    }


def normalize_trade(raw: dict[str, Any], *, ticker: str, year: int | None, run_id: str) -> dict[str, Any]:
    pnl_pct = safe_float(raw.get("pnl_pct"))
    stress_pnl_pct = safe_float(raw.get("stress_pnl_pct"))
    holding_days = safe_int(raw.get("holding_days"), 0)
    fill_base = safe_float(raw.get("fill_price_base"))
    fill_stress = safe_float(raw.get("fill_price_stress"))
    return {
        "run_id": run_id,
        "ticker": ticker,
        "year": year,
        "exit_reason": str(raw.get("exit_reason") or "unknown"),
        "entry_date": raw.get("entry_date"),
        "exit_date": raw.get("exit_date"),
        "pnl_pct": pnl_pct,
        "stress_pnl_pct": stress_pnl_pct,
        "slippage_cost_pct": (pnl_pct - stress_pnl_pct) if pnl_pct is not None and stress_pnl_pct is not None else None,
        "holding_days": holding_days,
        "trigger_price": safe_float(raw.get("trigger_price")),
        "exit_price": safe_float(raw.get("exit_price")),
        "fill_price_base": fill_base,
        "fill_price_stress": fill_stress,
        "fill_price_diff": (fill_base - fill_stress) if fill_base is not None and fill_stress is not None else None,
        "commission": safe_float(raw.get("commission")),
        "add_buy_count": len(raw.get("add_buys") or []),
    }


def collect_trades(run_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run_dir = RUNS_ROOT / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"run_id directory not found: {run_dir}")

    trades: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    rolling_files = sorted(run_dir.glob("*/rolling_validation.json"))
    loaded_tickers: set[str] = set()
    tickers_with_trades: set[str] = set()
    period_count = 0

    for path in rolling_files:
        ticker = path.parent.name.upper()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped.append({"path": str(path), "ticker": ticker, "error": f"{type(exc).__name__}: {exc}"})
            continue
        loaded_tickers.add(ticker)
        for period in data.get("periods") or []:
            period_count += 1
            year = period.get("year")
            try:
                year = int(year) if year is not None else None
            except Exception:
                year = None
            for raw_trade in period.get("trades") or []:
                if not isinstance(raw_trade, dict):
                    continue
                trades.append(normalize_trade(raw_trade, ticker=ticker, year=year, run_id=run_id))
                tickers_with_trades.add(ticker)

    meta = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "rolling_file_count": len(rolling_files),
        "loaded_ticker_count": len(loaded_tickers),
        "ticker_count_with_trades": len(tickers_with_trades),
        "period_count_loaded": period_count,
        "skip_count": len(skipped),
        "skipped_files": skipped[:50],
    }
    return trades, meta


def summarize_reason(reason: str, trades: list[dict[str, Any]], total_count: int) -> dict[str, Any]:
    pnl = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
    stress = [t["stress_pnl_pct"] for t in trades if t.get("stress_pnl_pct") is not None]
    holding = [t["holding_days"] for t in trades if t.get("holding_days") is not None]
    slippage = [t["slippage_cost_pct"] for t in trades if t.get("slippage_cost_pct") is not None]
    wins = [t for t in trades if (t.get("pnl_pct") is not None and t["pnl_pct"] > 0)]
    return {
        "exit_reason": reason,
        "count": len(trades),
        "ratio": (len(trades) / total_count * 100.0) if total_count else 0.0,
        "avg_pnl_pct": avg(pnl),
        "median_pnl_pct": median(pnl) if pnl else None,
        "avg_stress_pnl_pct": avg(stress),
        "median_stress_pnl_pct": median(stress) if stress else None,
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "avg_holding_days": avg([float(x) for x in holding]),
        "slippage_cost_pct_avg": avg(slippage),
    }


def analyze_trades(trades: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = dict(meta or {})
    total = len(trades)
    by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_reason[trade.get("exit_reason") or "unknown"].append(trade)

    ordered_reasons = list(EXIT_REASON_ORDER)
    for reason in sorted(by_reason):
        if reason not in ordered_reasons:
            ordered_reasons.append(reason)

    reason_summary = {reason: summarize_reason(reason, by_reason.get(reason, []), total) for reason in ordered_reasons}
    pnl = [t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]
    stress = [t["stress_pnl_pct"] for t in trades if t.get("stress_pnl_pct") is not None]
    slippage = [t["slippage_cost_pct"] for t in trades if t.get("slippage_cost_pct") is not None]
    holding = [float(t["holding_days"]) for t in trades if t.get("holding_days") is not None]
    time_out_trades = by_reason.get("time_out", [])
    time_out_pnl = [t["pnl_pct"] for t in time_out_trades if t.get("pnl_pct") is not None]

    base_avg = avg(pnl)
    stress_avg = avg(stress)
    analysis = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "meta": meta,
        "trade_count": total,
        "exit_reason_counts": dict(Counter(t.get("exit_reason") or "unknown" for t in trades)),
        "exit_reason_summary": reason_summary,
        "slippage": {
            "avg_pnl_pct_base": base_avg,
            "avg_pnl_pct_stress": stress_avg,
            "avg_base_minus_stress_pct": (base_avg - stress_avg) if base_avg is not None and stress_avg is not None else None,
            "base_minus_stress_distribution": distribution(slippage),
            "by_exit_reason": {reason: reason_summary[reason]["slippage_cost_pct_avg"] for reason in reason_summary},
        },
        "holding_days_distribution": distribution(holding),
        "time_out": {
            "count": len(time_out_trades),
            "ratio": (len(time_out_trades) / total * 100.0) if total else 0.0,
            "pnl_pct_distribution": distribution(time_out_pnl),
            "win_rate": (sum(1 for x in time_out_pnl if x > 0) / len(time_out_pnl) * 100.0) if time_out_pnl else 0.0,
            "avg_pnl_pct": avg(time_out_pnl),
            "median_pnl_pct": median(time_out_pnl) if time_out_pnl else None,
        },
    }
    return analysis


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def render_text(analysis: dict[str, Any]) -> str:
    meta = analysis.get("meta", {}) or {}
    lines: list[str] = []
    lines.append("=" * 120)
    lines.append("Sell-side metrics preview")
    lines.append("=" * 120)
    lines.append(f"generated_at: {analysis.get('generated_at')}")
    lines.append(f"run_id: {meta.get('run_id')}")
    lines.append(f"rolling_files_loaded: {meta.get('loaded_ticker_count')} / {meta.get('rolling_file_count')}")
    lines.append(f"tickers_with_trades: {meta.get('ticker_count_with_trades')}")
    lines.append(f"periods_loaded: {meta.get('period_count_loaded')}")
    lines.append(f"skip_count: {meta.get('skip_count')}")
    lines.append(f"trade_count: {analysis.get('trade_count')}")
    lines.append("")
    lines.append("Exit reason summary")
    lines.append("reason | count | ratio% | avg_pnl | med_pnl | avg_stress | med_stress | win% | avg_hold | slip_cost")
    lines.append("-" * 108)
    for reason, row in analysis.get("exit_reason_summary", {}).items():
        if row.get("count", 0) == 0 and reason in EXIT_REASON_ORDER:
            continue
        lines.append(
            f"{reason:12s} | "
            f"{row.get('count', 0):5d} | "
            f"{fmt(row.get('ratio'), 2):>6s} | "
            f"{fmt(row.get('avg_pnl_pct')):>8s} | "
            f"{fmt(row.get('median_pnl_pct')):>8s} | "
            f"{fmt(row.get('avg_stress_pnl_pct')):>10s} | "
            f"{fmt(row.get('median_stress_pnl_pct')):>10s} | "
            f"{fmt(row.get('win_rate'), 2):>6s} | "
            f"{fmt(row.get('avg_holding_days'), 2):>8s} | "
            f"{fmt(row.get('slippage_cost_pct_avg')):>9s}"
        )
    lines.append("")
    slip = analysis.get("slippage", {}) or {}
    lines.append("Slippage impact")
    lines.append(f"avg pnl_pct base:      {fmt(slip.get('avg_pnl_pct_base'))}")
    lines.append(f"avg pnl_pct stress:    {fmt(slip.get('avg_pnl_pct_stress'))}")
    lines.append(f"avg base-stress cost:  {fmt(slip.get('avg_base_minus_stress_pct'))}")
    lines.append("by exit_reason base-stress avg: " + json.dumps({k: round(v, 6) if isinstance(v, (int, float)) else v for k, v in (slip.get('by_exit_reason') or {}).items()}, ensure_ascii=False, sort_keys=True))
    lines.append("")
    holding = analysis.get("holding_days_distribution", {}) or {}
    lines.append("Holding days distribution")
    lines.append(
        f"count={holding.get('count')}, p10={fmt(holding.get('p10'))}, p25={fmt(holding.get('p25'))}, "
        f"p50={fmt(holding.get('p50'))}, p75={fmt(holding.get('p75'))}, p90={fmt(holding.get('p90'))}"
    )
    lines.append("")
    timeout = analysis.get("time_out", {}) or {}
    todist = timeout.get("pnl_pct_distribution", {}) or {}
    lines.append("time_out pnl tendency")
    lines.append(
        f"count={timeout.get('count')}, ratio={fmt(timeout.get('ratio'), 2)}%, "
        f"avg={fmt(timeout.get('avg_pnl_pct'))}, median={fmt(timeout.get('median_pnl_pct'))}, "
        f"win_rate={fmt(timeout.get('win_rate'), 2)}%, p25={fmt(todist.get('p25'))}, p75={fmt(todist.get('p75'))}"
    )
    if meta.get("skipped_files"):
        lines.append("")
        lines.append("Skipped files sample")
        for skipped in meta["skipped_files"][:10]:
            lines.append(f"- {skipped.get('path')}: {skipped.get('error')}")
    return "\n".join(lines)


def default_out_dir(run_id: str) -> Path:
    return RUNS_ROOT / run_id


def save_outputs(analysis: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "sell_metrics_preview.json"
    txt_path = out_dir / "sell_metrics_preview.txt"
    text = render_text(analysis)
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    return json_path, txt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze sell-side metrics from rolling_validation trades.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--out", help="Output directory. Default: run directory.")
    args = parser.parse_args(argv)

    started = time.time()
    trades, meta = collect_trades(args.run_id)
    meta["elapsed_sec_collect"] = time.time() - started
    analysis = analyze_trades(trades, meta)
    out_dir = Path(args.out) if args.out else default_out_dir(args.run_id)
    json_path, txt_path = save_outputs(analysis, out_dir)
    text = render_text(analysis)
    elapsed = time.time() - started
    print(text)
    print(f"\nelapsed_sec: {elapsed:.2f}")
    print(f"json_out: {json_path}")
    print(f"txt_out:  {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
