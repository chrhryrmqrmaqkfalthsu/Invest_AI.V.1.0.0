#!/usr/bin/env python3
"""Analyze stock_score distributions from one or more pipeline batch runs.

Read-only: this script only reads data/_system/pipeline/v1/runs/{run_id}/batch_summary.json
and prints aggregate distribution/cutoff tables.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = ROOT / "data/_system/pipeline/v1/runs"

# Approximate buckets for analysis only. Keep this intentionally broad; exact
# market-cap/sector data will later come from a proper universe metadata source.
TICKER_META: dict[str, dict[str, str]] = {
    # Technology / communication mega-large
    "MSFT": {"sector": "Technology", "cap_bucket": "Mega"},
    "AAPL": {"sector": "Technology", "cap_bucket": "Mega"},
    "NVDA": {"sector": "Technology", "cap_bucket": "Mega"},
    "AMD": {"sector": "Technology", "cap_bucket": "Large"},
    "INTC": {"sector": "Technology", "cap_bucket": "Large"},
    "IBM": {"sector": "Technology", "cap_bucket": "Large"},
    "ORCL": {"sector": "Technology", "cap_bucket": "Large"},
    "ADBE": {"sector": "Technology", "cap_bucket": "Large"},
    "CRM": {"sector": "Technology", "cap_bucket": "Large"},
    "NOW": {"sector": "Technology", "cap_bucket": "Large"},
    "SNOW": {"sector": "Technology", "cap_bucket": "Mid/Large"},
    "PLTR": {"sector": "Technology", "cap_bucket": "Mid/Large"},
    "DDOG": {"sector": "Technology", "cap_bucket": "Mid"},
    "NET": {"sector": "Technology", "cap_bucket": "Mid"},
    "ZS": {"sector": "Technology", "cap_bucket": "Mid"},
    "MDB": {"sector": "Technology", "cap_bucket": "Mid"},
    "DOCN": {"sector": "Technology", "cap_bucket": "Small/Mid"},
    "GOOGL": {"sector": "Communication", "cap_bucket": "Mega"},
    "META": {"sector": "Communication", "cap_bucket": "Mega"},
    "DIS": {"sector": "Communication", "cap_bucket": "Large"},
    "NFLX": {"sector": "Communication", "cap_bucket": "Large"},
    "TTD": {"sector": "Communication", "cap_bucket": "Mid/Large"},
    "ROKU": {"sector": "Communication", "cap_bucket": "Mid"},
    "PINS": {"sector": "Communication", "cap_bucket": "Mid"},
    # Consumer
    "AMZN": {"sector": "Consumer Discretionary", "cap_bucket": "Mega"},
    "TSLA": {"sector": "Consumer Discretionary", "cap_bucket": "Mega"},
    "HD": {"sector": "Consumer Discretionary", "cap_bucket": "Large"},
    "NKE": {"sector": "Consumer Discretionary", "cap_bucket": "Large"},
    "SBUX": {"sector": "Consumer Discretionary", "cap_bucket": "Large"},
    "LOW": {"sector": "Consumer Discretionary", "cap_bucket": "Large"},
    "F": {"sector": "Consumer Discretionary", "cap_bucket": "Large"},
    "GM": {"sector": "Consumer Discretionary", "cap_bucket": "Large"},
    "RIVN": {"sector": "Consumer Discretionary", "cap_bucket": "Mid"},
    "LCID": {"sector": "Consumer Discretionary", "cap_bucket": "Small/Mid"},
    "WMT": {"sector": "Consumer Staples", "cap_bucket": "Mega"},
    "COST": {"sector": "Consumer Staples", "cap_bucket": "Mega"},
    "PG": {"sector": "Consumer Staples", "cap_bucket": "Large"},
    "KO": {"sector": "Consumer Staples", "cap_bucket": "Large"},
    "PEP": {"sector": "Consumer Staples", "cap_bucket": "Large"},
    "MDLZ": {"sector": "Consumer Staples", "cap_bucket": "Large"},
    "TGT": {"sector": "Consumer Staples", "cap_bucket": "Large"},
    # Financials
    "JPM": {"sector": "Financials", "cap_bucket": "Large"},
    "BAC": {"sector": "Financials", "cap_bucket": "Large"},
    "WFC": {"sector": "Financials", "cap_bucket": "Large"},
    "GS": {"sector": "Financials", "cap_bucket": "Large"},
    "MS": {"sector": "Financials", "cap_bucket": "Large"},
    "V": {"sector": "Financials", "cap_bucket": "Mega"},
    "MA": {"sector": "Financials", "cap_bucket": "Mega"},
    "PYPL": {"sector": "Financials", "cap_bucket": "Large"},
    "COIN": {"sector": "Financials", "cap_bucket": "Mid/Large"},
    "SOFI": {"sector": "Financials", "cap_bucket": "Mid"},
    # Healthcare
    "UNH": {"sector": "Healthcare", "cap_bucket": "Large"},
    "LLY": {"sector": "Healthcare", "cap_bucket": "Mega"},
    "JNJ": {"sector": "Healthcare", "cap_bucket": "Large"},
    "PFE": {"sector": "Healthcare", "cap_bucket": "Large"},
    "MRK": {"sector": "Healthcare", "cap_bucket": "Large"},
    "ABBV": {"sector": "Healthcare", "cap_bucket": "Large"},
    "TMO": {"sector": "Healthcare", "cap_bucket": "Large"},
    "DHR": {"sector": "Healthcare", "cap_bucket": "Large"},
    "ISRG": {"sector": "Healthcare", "cap_bucket": "Large"},
    "MRNA": {"sector": "Healthcare", "cap_bucket": "Mid/Large"},
    "BMY": {"sector": "Healthcare", "cap_bucket": "Large"},
    # Energy / materials / industrials
    "XOM": {"sector": "Energy", "cap_bucket": "Large"},
    "CVX": {"sector": "Energy", "cap_bucket": "Large"},
    "COP": {"sector": "Energy", "cap_bucket": "Large"},
    "SLB": {"sector": "Energy", "cap_bucket": "Large"},
    "OXY": {"sector": "Energy", "cap_bucket": "Large"},
    "ENPH": {"sector": "Energy", "cap_bucket": "Mid"},
    "FCX": {"sector": "Materials", "cap_bucket": "Large"},
    "NEM": {"sector": "Materials", "cap_bucket": "Large"},
    "LIN": {"sector": "Materials", "cap_bucket": "Large"},
    "GE": {"sector": "Industrials", "cap_bucket": "Large"},
    "CAT": {"sector": "Industrials", "cap_bucket": "Large"},
    "BA": {"sector": "Industrials", "cap_bucket": "Large"},
    "HON": {"sector": "Industrials", "cap_bucket": "Large"},
    "UPS": {"sector": "Industrials", "cap_bucket": "Large"},
    "DE": {"sector": "Industrials", "cap_bucket": "Large"},
    "LUV": {"sector": "Industrials", "cap_bucket": "Mid"},
    # Utilities / real estate / telecom / edge
    "NEE": {"sector": "Utilities", "cap_bucket": "Large"},
    "DUK": {"sector": "Utilities", "cap_bucket": "Large"},
    "SO": {"sector": "Utilities", "cap_bucket": "Large"},
    "AEP": {"sector": "Utilities", "cap_bucket": "Large"},
    "PLD": {"sector": "Real Estate", "cap_bucket": "Large"},
    "AMT": {"sector": "Real Estate", "cap_bucket": "Large"},
    "O": {"sector": "Real Estate", "cap_bucket": "Large"},
    "SPG": {"sector": "Real Estate", "cap_bucket": "Large"},
    "T": {"sector": "Telecom", "cap_bucket": "Large"},
    "VZ": {"sector": "Telecom", "cap_bucket": "Large"},
    "BIS": {"sector": "ETF/Edge", "cap_bucket": "Low-liquidity edge"},
    "DZZ": {"sector": "ETF/Edge", "cap_bucket": "Low-liquidity edge"},
}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "min": percentile(values, 0.0),
        "p10": percentile(values, 0.10),
        "p25": percentile(values, 0.25),
        "p50": percentile(values, 0.50),
        "p75": percentile(values, 0.75),
        "p90": percentile(values, 0.90),
        "max": percentile(values, 1.0),
        "avg": sum(values) / len(values) if values else None,
    }


def load_batch(run_id: str) -> dict[str, Any]:
    path = RUNS_ROOT / run_id / "batch_summary.json"
    if not path.exists():
        raise FileNotFoundError(f"batch_summary.json not found for run_id={run_id}: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_run_id"] = run_id
    return data


def normalize_rows(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for batch in batches:
        run_id = batch.get("_run_id") or batch.get("run_id")
        for raw in batch.get("results", []) or []:
            ticker = str(raw.get("ticker", "")).upper()
            meta = TICKER_META.get(ticker, {"sector": "Unknown", "cap_bucket": "Unknown"})
            row = dict(raw)
            row["run_id"] = run_id
            row["ticker"] = ticker
            row["sector"] = meta["sector"]
            row["cap_bucket"] = meta["cap_bucket"]
            try:
                row["stock_score_float"] = float(raw.get("stock_score"))
            except Exception:
                row["stock_score_float"] = None
            try:
                row["pass_count_int"] = int(raw.get("pass_count") or 0)
            except Exception:
                row["pass_count_int"] = 0
            rows.append(row)
    return rows


def dedupe_latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # If multiple run_ids include the same ticker, keep the later row in argv order.
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[row["ticker"]] = row
    return list(latest.values())


def analyze_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(row.get("final_status", "UNKNOWN") for row in rows)
    reason_counts = Counter(row.get("screening_reason_code") or row.get("reason_code") or "PASS" for row in rows)
    rolling = [row for row in rows if row.get("final_status") == "ROLLING_DONE"]
    scores = [row["stock_score_float"] for row in rolling if row.get("stock_score_float") is not None]
    excluded = [row for row in rolling if row.get("excluded")]
    zero_score = [row for row in rolling if row.get("stock_score_float") == 0.0]

    pass_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rolling:
        pass_groups[int(row.get("pass_count_int") or 0)].append(row)

    pass_group_summary = {}
    for pc in sorted(pass_groups):
        vals = [r["stock_score_float"] for r in pass_groups[pc] if r.get("stock_score_float") is not None]
        pass_group_summary[str(pc)] = {
            "count": len(pass_groups[pc]),
            "distribution": distribution(vals),
            "tickers": sorted(r["ticker"] for r in pass_groups[pc]),
        }

    sector_summary = summarize_group(rows, "sector")
    cap_summary = summarize_group(rows, "cap_bucket")

    cutoff_scenarios = []
    scenarios = [
        ("pass_count >= 2", lambda r: r.get("final_status") == "ROLLING_DONE" and int(r.get("pass_count_int") or 0) >= 2),
        ("pass_count >= 1", lambda r: r.get("final_status") == "ROLLING_DONE" and int(r.get("pass_count_int") or 0) >= 1),
        ("stock_score >= 80", lambda r: r.get("stock_score_float") is not None and r["stock_score_float"] >= 80),
        ("stock_score >= 70", lambda r: r.get("stock_score_float") is not None and r["stock_score_float"] >= 70),
        ("stock_score >= 60", lambda r: r.get("stock_score_float") is not None and r["stock_score_float"] >= 60),
        ("stock_score >= 50", lambda r: r.get("stock_score_float") is not None and r["stock_score_float"] >= 50),
        ("stock_score >= 40", lambda r: r.get("stock_score_float") is not None and r["stock_score_float"] >= 40),
        ("stock_score > 0", lambda r: r.get("stock_score_float") is not None and r["stock_score_float"] > 0),
        ("pass_count >= 2 OR score >= 70", lambda r: r.get("final_status") == "ROLLING_DONE" and (int(r.get("pass_count_int") or 0) >= 2 or (r.get("stock_score_float") or 0) >= 70)),
        ("pass_count >= 1 AND score >= 50", lambda r: r.get("final_status") == "ROLLING_DONE" and int(r.get("pass_count_int") or 0) >= 1 and (r.get("stock_score_float") or 0) >= 50),
    ]
    total = len(rows) or 1
    for name, predicate in scenarios:
        selected = [r for r in rows if predicate(r)]
        cutoff_scenarios.append({
            "scenario": name,
            "count": len(selected),
            "pct_total": len(selected) / total * 100.0,
            "tickers": sorted(r["ticker"] for r in selected),
        })

    return {
        "total": len(rows),
        "status_counts": dict(status_counts),
        "screening_reason_counts": dict(reason_counts),
        "screened_out_count": int(status_counts.get("SCREENED_OUT", 0)),
        "rolling_done_count": int(status_counts.get("ROLLING_DONE", 0)),
        "error_count": int(status_counts.get("ERROR", 0)),
        "stock_score_distribution": distribution(scores),
        "zero_score_count": len(zero_score),
        "excluded_count": len(excluded),
        "zero_score_tickers": sorted(r["ticker"] for r in zero_score),
        "excluded_tickers": sorted(r["ticker"] for r in excluded),
        "pass_count_groups": pass_group_summary,
        "sector_summary": sector_summary,
        "cap_bucket_summary": cap_summary,
        "cutoff_scenarios": cutoff_scenarios,
    }


def summarize_group(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "Unknown")].append(row)
    out = {}
    for name in sorted(groups):
        group = groups[name]
        rolling = [r for r in group if r.get("final_status") == "ROLLING_DONE"]
        scores = [r["stock_score_float"] for r in rolling if r.get("stock_score_float") is not None]
        high60 = [r for r in rolling if (r.get("stock_score_float") or 0.0) >= 60.0]
        high70 = [r for r in rolling if (r.get("stock_score_float") or 0.0) >= 70.0]
        out[name] = {
            "total": len(group),
            "rolling_done": len(rolling),
            "screened_out": sum(1 for r in group if r.get("final_status") == "SCREENED_OUT"),
            "error": sum(1 for r in group if r.get("final_status") == "ERROR"),
            "score_distribution": distribution(scores),
            "score_ge_60_count": len(high60),
            "score_ge_60_pct_of_rolling": len(high60) / len(rolling) * 100.0 if rolling else 0.0,
            "score_ge_70_count": len(high70),
            "tickers": sorted(r["ticker"] for r in group),
        }
    return out


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def print_analysis(analysis: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    print("=" * 120)
    print("Pipeline stock_score distribution analysis")
    print("=" * 120)
    print(f"total:         {analysis['total']}")
    print(f"rolling_done:  {analysis['rolling_done_count']}")
    print(f"screened_out:  {analysis['screened_out_count']}")
    print(f"errors:        {analysis['error_count']}")
    print(f"statuses:      {analysis['status_counts']}")
    print(f"reasons:       {analysis['screening_reason_counts']}")
    print()
    print("Stock score distribution")
    for k, v in analysis["stock_score_distribution"].items():
        print(f"  {k:8s}: {fmt(v)}")
    print(f"  zero_score_count: {analysis['zero_score_count']}")
    print(f"  excluded_count:   {analysis['excluded_count']}")
    print()
    print("Pass-count groups")
    for pc, group in analysis["pass_count_groups"].items():
        dist = group["distribution"]
        print(
            f"  pass_count={pc}: count={group['count']:3d}, "
            f"min={fmt(dist['min'])}, p50={fmt(dist['p50'])}, max={fmt(dist['max'])}, "
            f"tickers={','.join(group['tickers'][:20])}{'...' if len(group['tickers'])>20 else ''}"
        )
    print()
    print("Cap bucket summary")
    for name, group in analysis["cap_bucket_summary"].items():
        dist = group["score_distribution"]
        print(
            f"  {name:20s} total={group['total']:3d} rolling={group['rolling_done']:3d} "
            f">=60={group['score_ge_60_count']:2d} ({fmt(group['score_ge_60_pct_of_rolling'],1)}%) "
            f"p50={fmt(dist['p50'])} max={fmt(dist['max'])}"
        )
    print()
    print("Sector summary")
    for name, group in analysis["sector_summary"].items():
        dist = group["score_distribution"]
        print(
            f"  {name:24s} total={group['total']:3d} rolling={group['rolling_done']:3d} "
            f">=60={group['score_ge_60_count']:2d} p50={fmt(dist['p50'])} max={fmt(dist['max'])}"
        )
    print()
    print("Cutoff scenarios")
    for scenario in analysis["cutoff_scenarios"]:
        print(
            f"  {scenario['scenario']:30s} count={scenario['count']:3d} "
            f"pct={fmt(scenario['pct_total'],1)}% tickers={','.join(scenario['tickers'][:25])}{'...' if len(scenario['tickers'])>25 else ''}"
        )
    print()
    print("Top 30 by stock_score")
    top = sorted([r for r in rows if r.get("stock_score_float") is not None], key=lambda r: r["stock_score_float"], reverse=True)[:30]
    for r in top:
        print(
            f"  {r['ticker']:6s} score={fmt(r['stock_score_float'])} pass={r.get('pass_count_int')} "
            f"sector={r.get('sector')} cap={r.get('cap_bucket')} run={r.get('run_id')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze pipeline batch stock_score distributions.")
    parser.add_argument("run_ids", nargs="+", help="One or more batch run IDs to analyze.")
    parser.add_argument("--no-dedupe", action="store_true", help="Keep duplicate tickers across run_ids instead of latest-only.")
    parser.add_argument("--json-out", help="Optional path to write analysis JSON.")
    args = parser.parse_args(argv)

    batches = [load_batch(run_id) for run_id in args.run_ids]
    rows = normalize_rows(batches)
    if not args.no_dedupe:
        rows = dedupe_latest(rows)
    analysis = analyze_rows(rows)
    print_analysis(analysis, rows)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"analysis": analysis, "rows": rows}, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\njson_out: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
