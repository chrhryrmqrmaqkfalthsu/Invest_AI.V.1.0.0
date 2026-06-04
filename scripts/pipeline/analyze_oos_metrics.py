#!/usr/bin/env python3
"""Analyze yearly OOS metrics from rolling_validation.json files.

Read-only: this script scans completed ticker directories under
`data/_system/pipeline/v1/runs/{run_id}/` and reads only rolling_validation.json.
It does not modify pipeline progress, source data, or live files.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "data/_system/pipeline/v1/runs"

OOS_PASS_CRITERIA = {
    "trade_count_min": 5,
    "win_rate_gt": 50.0,
    "expectancy_pct_gt": 1.0,
    "profit_factor_gt": 1.2,
}

METRICS = [
    "trade_count",
    "win_rate",
    "expectancy_pct",
    "profit_factor",
    "max_drawdown_pct",
]

STRESS_METRICS = [
    "stress_trade_count",
    "stress_win_rate",
    "stress_expectancy_pct",
    "stress_profit_factor",
]

COST_NOTE = (
    "expectancy_pct is the OOS summary stored in rolling_validation.json. "
    "It is not a full live net-after-all-costs estimate. Current trade rows may "
    "include base fill/commission fields, and stress_* metrics are separately "
    "computed from stress_pnl_pct when available. Use stress_expectancy_pct as a "
    "slippage-sensitive reference."
)


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


def distribution(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if safe_float(v) is not None]
    return {
        "count": len(clean),
        "avg": sum(clean) / len(clean) if clean else None,
        "min": percentile(clean, 0.0),
        "p10": percentile(clean, 0.10),
        "p25": percentile(clean, 0.25),
        "p50": percentile(clean, 0.50),
        "p75": percentile(clean, 0.75),
        "p90": percentile(clean, 0.90),
        "max": percentile(clean, 1.0),
    }


def passes_oos(oos: dict[str, Any]) -> bool:
    return (
        safe_int(oos.get("trade_count"), 0) >= OOS_PASS_CRITERIA["trade_count_min"]
        and (safe_float(oos.get("win_rate"), 0.0) or 0.0) > OOS_PASS_CRITERIA["win_rate_gt"]
        and (safe_float(oos.get("expectancy_pct"), 0.0) or 0.0) > OOS_PASS_CRITERIA["expectancy_pct_gt"]
        and (safe_float(oos.get("profit_factor"), 0.0) or 0.0) > OOS_PASS_CRITERIA["profit_factor_gt"]
    )


def year_from_period(period: dict[str, Any]) -> int | None:
    if period.get("year") is not None:
        try:
            return int(period["year"])
        except Exception:
            pass
    test_period = period.get("test_period") or []
    if test_period:
        try:
            return int(str(test_period[0])[:4])
        except Exception:
            return None
    return None


def stress_metrics_from_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = []
    for trade in trades or []:
        v = safe_float(trade.get("stress_pnl_pct"))
        if v is not None:
            pnl.append(v)
    if not pnl:
        return {
            "stress_available": False,
            "stress_trade_count": None,
            "stress_win_rate": None,
            "stress_expectancy_pct": None,
            "stress_profit_factor": None,
        }
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf = gross_profit / gross_loss
    else:
        pf = gross_profit if gross_profit > 0 else 0.0
    return {
        "stress_available": True,
        "stress_trade_count": len(pnl),
        "stress_win_rate": len(wins) / len(pnl) * 100.0,
        "stress_expectancy_pct": sum(pnl) / len(pnl),
        "stress_profit_factor": pf,
    }


def iter_rolling_files(run_id: str) -> list[Path]:
    run_dir = RUNS_ROOT / str(run_id)
    if not run_dir.exists():
        raise FileNotFoundError(f"run_id directory not found: {run_dir}")
    return sorted(run_dir.glob("*/rolling_validation.json"))


def collect_records(run_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    ticker_rows: list[dict[str, Any]] = []
    for run_id in run_ids:
        for path in iter_rolling_files(run_id):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                ticker_rows.append({
                    "run_id": run_id,
                    "ticker": path.parent.name,
                    "path": str(path),
                    "load_error": f"{type(exc).__name__}: {exc}",
                })
                continue
            ticker = str(data.get("ticker") or path.parent.name).upper()
            periods = data.get("periods") or []
            ticker_records = []
            for period in periods:
                oos = period.get("oos") or {}
                stress = stress_metrics_from_trades(period.get("trades") or [])
                rec = {
                    "run_id": run_id,
                    "ticker": ticker,
                    "year": year_from_period(period),
                    "test_period": period.get("test_period"),
                    "pass": passes_oos(oos),
                    "trade_count": safe_int(oos.get("trade_count"), 0),
                    "win_rate": safe_float(oos.get("win_rate"), 0.0),
                    "expectancy_pct": safe_float(oos.get("expectancy_pct"), 0.0),
                    "profit_factor": safe_float(oos.get("profit_factor"), 0.0),
                    "max_drawdown_pct": safe_float(oos.get("max_drawdown_pct"), 0.0),
                    "fitness": safe_float(period.get("fitness"), 0.0),
                    "rolling_file": str(path),
                }
                rec.update(stress)
                records.append(rec)
                ticker_records.append(rec)
            pass_count = sum(1 for r in ticker_records if r["pass"])
            score_block = data.get("stock_score", {}) or {}
            ticker_rows.append({
                "run_id": run_id,
                "ticker": ticker,
                "rolling_file": str(path),
                "period_count": len(ticker_records),
                "pass_count": pass_count,
                "stock_score": score_block.get("stock_score"),
                "excluded": score_block.get("excluded"),
                "exclude_reason": score_block.get("exclude_reason"),
            })
            for r in ticker_records:
                r["pass_count"] = pass_count
                r["stock_score"] = score_block.get("stock_score")
    return records, ticker_rows


def summarize_metric_set(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"period_count": len(records)}
    for metric in METRICS + STRESS_METRICS:
        values = [r[metric] for r in records if r.get(metric) is not None]
        summary[metric] = distribution(values)
    return summary


def summarize_by_pass_count(records: list[dict[str, Any]], ticker_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ticker_counts = Counter(row.get("pass_count", 0) for row in ticker_rows if row.get("period_count", 0) > 0)
    grouped_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        grouped_records[int(rec.get("pass_count", 0))].append(rec)
    out = {}
    for pc in sorted(set(ticker_counts) | set(grouped_records)):
        out[str(pc)] = {
            "ticker_count": int(ticker_counts.get(pc, 0)),
            "period_count": len(grouped_records.get(pc, [])),
            "metrics": summarize_metric_set(grouped_records.get(pc, [])),
            "tickers": sorted(row["ticker"] for row in ticker_rows if row.get("pass_count") == pc),
        }
    return out


def summarize_by_year(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        if rec.get("year") is not None:
            groups[int(rec["year"])].append(rec)
    return {str(year): summarize_metric_set(groups[year]) for year in sorted(groups)}


def analyze(run_ids: list[str]) -> dict[str, Any]:
    records, ticker_rows = collect_records(run_ids)
    passed_records = [r for r in records if r.get("pass")]
    failed_records = [r for r in records if not r.get("pass")]
    pass_count_dist = Counter(row.get("pass_count", 0) for row in ticker_rows if row.get("period_count", 0) > 0)
    excluded_count = sum(1 for row in ticker_rows if row.get("excluded"))
    zero_score_count = sum(1 for row in ticker_rows if safe_float(row.get("stock_score"), 0.0) == 0.0)
    return {
        "run_ids": run_ids,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "criteria": OOS_PASS_CRITERIA,
        "cost_note": COST_NOTE,
        "ticker_count_with_rolling": len([r for r in ticker_rows if r.get("period_count", 0) > 0]),
        "ticker_count_total_rows": len(ticker_rows),
        "oos_period_count": len(records),
        "passed_oos_period_count": len(passed_records),
        "failed_oos_period_count": len(failed_records),
        "pass_count_distribution": {str(k): int(v) for k, v in sorted(pass_count_dist.items())},
        "excluded_ticker_count": excluded_count,
        "zero_score_ticker_count": zero_score_count,
        "all_oos_periods": summarize_metric_set(records),
        "passed_oos_periods": summarize_metric_set(passed_records),
        "failed_oos_periods": summarize_metric_set(failed_records),
        "by_pass_count": summarize_by_pass_count(records, ticker_rows),
        "by_year": summarize_by_year(records),
        "ticker_rows": ticker_rows,
        "records": records,
    }


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def metric_table(summary: dict[str, Any], metrics: list[str]) -> list[str]:
    lines = []
    header = "metric | count | avg | p10 | p25 | p50 | p75 | p90"
    lines.append(header)
    lines.append("-" * len(header))
    for metric in metrics:
        dist = summary.get(metric, {}) or {}
        lines.append(
            f"{metric:22s} | "
            f"{str(dist.get('count', 0)):5s} | "
            f"{fmt(dist.get('avg')):>8s} | "
            f"{fmt(dist.get('p10')):>8s} | "
            f"{fmt(dist.get('p25')):>8s} | "
            f"{fmt(dist.get('p50')):>8s} | "
            f"{fmt(dist.get('p75')):>8s} | "
            f"{fmt(dist.get('p90')):>8s}"
        )
    return lines


def render_text(analysis: dict[str, Any]) -> str:
    lines = []
    lines.append("=" * 110)
    lines.append("OOS yearly metrics analysis")
    lines.append("=" * 110)
    lines.append(f"run_ids: {', '.join(analysis['run_ids'])}")
    lines.append(f"generated_at: {analysis['generated_at']}")
    lines.append(f"ticker_count_with_rolling: {analysis['ticker_count_with_rolling']}")
    lines.append(f"oos_period_count: {analysis['oos_period_count']}")
    lines.append(f"passed_oos_period_count: {analysis['passed_oos_period_count']}")
    lines.append(f"failed_oos_period_count: {analysis['failed_oos_period_count']}")
    lines.append(f"pass_count_distribution: {analysis['pass_count_distribution']}")
    lines.append(f"zero_score_ticker_count: {analysis['zero_score_ticker_count']}")
    lines.append(f"excluded_ticker_count: {analysis['excluded_ticker_count']}")
    lines.append("")
    lines.append("OOS pass criteria")
    lines.append(json.dumps(analysis["criteria"], ensure_ascii=False, sort_keys=True))
    lines.append("")
    lines.append("Cost / stress note")
    lines.append(analysis["cost_note"])
    lines.append("")
    lines.append("All ticker-year OOS periods")
    lines.extend(metric_table(analysis["all_oos_periods"], METRICS + STRESS_METRICS))
    lines.append("")
    lines.append("Passed OOS periods only")
    lines.extend(metric_table(analysis["passed_oos_periods"], METRICS + STRESS_METRICS))
    lines.append("")
    lines.append("By pass_count group")
    for pc, group in sorted(analysis["by_pass_count"].items(), key=lambda kv: int(kv[0])):
        metrics = group["metrics"]
        lines.append(
            f"pass_count={pc}: ticker_count={group['ticker_count']}, period_count={group['period_count']}, "
            f"expectancy_p50={fmt(metrics['expectancy_pct']['p50'])}, "
            f"expectancy_p75={fmt(metrics['expectancy_pct']['p75'])}, "
            f"win_rate_p50={fmt(metrics['win_rate']['p50'])}, "
            f"trade_count_p50={fmt(metrics['trade_count']['p50'])}, "
            f"stress_expectancy_p50={fmt(metrics['stress_expectancy_pct']['p50'])}"
        )
    lines.append("")
    lines.append("By OOS year")
    for year, summary in sorted(analysis["by_year"].items()):
        lines.append(
            f"{year}: periods={summary['period_count']}, "
            f"exp_p50={fmt(summary['expectancy_pct']['p50'])}, "
            f"exp_p75={fmt(summary['expectancy_pct']['p75'])}, "
            f"win_p50={fmt(summary['win_rate']['p50'])}, "
            f"pf_p50={fmt(summary['profit_factor']['p50'])}, "
            f"trades_p50={fmt(summary['trade_count']['p50'])}"
        )
    return "\n".join(lines)


def default_output_paths(run_ids: list[str]) -> tuple[Path, Path]:
    if len(run_ids) == 1:
        base = RUNS_ROOT / run_ids[0]
    else:
        base = RUNS_ROOT / run_ids[-1]
    return base / "oos_metrics_analysis.json", base / "oos_metrics_analysis.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze yearly OOS metrics from rolling_validation.json files.")
    parser.add_argument("run_ids", nargs="+", help="One or more pipeline run IDs.")
    parser.add_argument("--json-out", help="Optional JSON output path. Default: run_id/oos_metrics_analysis.json")
    parser.add_argument("--txt-out", help="Optional text output path. Default: run_id/oos_metrics_analysis.txt")
    parser.add_argument("--no-save", action="store_true", help="Print only; do not save analysis files.")
    args = parser.parse_args(argv)

    analysis = analyze(args.run_ids)
    text = render_text(analysis)
    print(text)
    if not args.no_save:
        default_json, default_txt = default_output_paths(args.run_ids)
        json_path = Path(args.json_out) if args.json_out else default_json
        txt_path = Path(args.txt_out) if args.txt_out else default_txt
        json_path.parent.mkdir(parents=True, exist_ok=True)
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        txt_path.write_text(text, encoding="utf-8")
        print(f"\njson_out: {json_path}")
        print(f"txt_out:  {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
