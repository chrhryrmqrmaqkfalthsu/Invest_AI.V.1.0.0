#!/usr/bin/env python3
"""BV-3 raw log diagnostics.

Read-only analysis of BV-2 outputs. No backtest rerun.
Writes only under data/_system/research/bv3_20260607/.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

BV2 = Path("data/_system/research/bv2_20260607")
OUT = Path("data/_system/research/bv3_20260607")
TRADES = BV2 / "bv2_trades.jsonl"
SUMMARY = BV2 / "bv2_summary.json"
LIFT = BV2 / "bv2_lift_by_ticker_year.jsonl"
POSITION_LIMIT = 120_000.0
YEARS = [2022, 2023, 2024, 2025]
BASELINES = ["current_rulebook", "buy_hold", "naive_momentum"]


def f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def date_of(v: Any):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def percentile(value: float, values: list[float]) -> float | None:
    if not values:
        return None
    below = sum(1 for x in values if x < value)
    equal = sum(1 for x in values if x == value)
    return (below + 0.5 * equal) / len(values) * 100.0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [f(r.get("pnl_krw")) for r in rows]
    pct = [f(r.get("pnl_pct")) for r in rows]
    holds = [int(r.get("holding_days") or 0) for r in rows]
    gp = sum(x for x in pnl if x > 0)
    gl = -sum(x for x in pnl if x < 0)
    return {
        "trade_count": len(rows),
        "total_pnl_krw": sum(pnl),
        "expectancy_krw": statistics.mean(pnl) if pnl else 0.0,
        "expectancy_pct": statistics.mean(pct) if pct else 0.0,
        "win_rate_pct": sum(1 for x in pnl if x > 0) / len(pnl) * 100 if pnl else 0.0,
        "profit_factor": gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0),
        "avg_holding_days": statistics.mean(holds) if holds else 0.0,
        "median_holding_days": statistics.median(holds) if holds else 0.0,
    }


def trade_notional(row: dict[str, Any]) -> float:
    entry_shares = f(row.get("entry_shares"))
    total_shares = f(row.get("total_shares"), entry_shares)
    entry = abs(f(row.get("entry_price")) * entry_shares)
    exit_ = abs(f(row.get("exit_price")) * total_shares)
    return entry + exit_


def exposure_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"avg_exposure_fraction": 0.0, "avg_concurrent_positions": 0.0, "max_concurrent_positions": 0, "turnover_krw": 0.0, "invested_return_pct": 0.0}
    events = []
    active_days = 0
    total_pos_days = 0
    for r in rows:
        ed = date_of(r.get("entry_date"))
        xd = date_of(r.get("exit_date"))
        if ed and xd:
            events.append((ed, 1))
            events.append((xd, -1))
            days = max(1, (xd - ed).days + 1)
            active_days += days
            total_pos_days += days
    events.sort(key=lambda x: (x[0], -x[1]))
    cur = 0
    max_cur = 0
    daily_counts = []
    if events:
        start = events[0][0]
        last = start
        for day, delta in events:
            gap = max(0, (day - last).days)
            if gap:
                daily_counts.extend([cur] * gap)
            cur += delta
            max_cur = max(max_cur, cur)
            last = day
        daily_counts.append(cur)
    avg_conc = statistics.mean(daily_counts) if daily_counts else 0.0
    avg_exposure = min(1.0, avg_conc / 85.0) if rows and rows[0].get("baseline") != "buy_hold" else 1.0
    turnover = sum(trade_notional(r) for r in rows)
    capital_used = sum(abs(f(r.get("entry_price")) * f(r.get("entry_shares"))) for r in rows)
    total_pnl = sum(f(r.get("pnl_krw")) for r in rows)
    return {
        "avg_exposure_fraction_approx": avg_exposure,
        "avg_concurrent_positions": avg_conc,
        "max_concurrent_positions": max_cur,
        "turnover_krw": turnover,
        "capital_used_krw_sum": capital_used,
        "invested_return_pct": total_pnl / capital_used * 100.0 if capital_used else 0.0,
        "pnl_per_turnover_pct": total_pnl / turnover * 100.0 if turnover else 0.0,
    }


def load_rows() -> list[dict[str, Any]]:
    return [r for r in read_jsonl(TRADES) if r.get("baseline") in BASELINES]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def q1_exposure(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for baseline in BASELINES:
        br = [r for r in rows if r.get("baseline") == baseline]
        row = {"baseline": baseline, **summarize(br), **exposure_stats(br)}
        out.append(row)
    for year in YEARS:
        for baseline in BASELINES:
            br = [r for r in rows if r.get("baseline") == baseline and int(r.get("year") or 0) == year]
            out.append({"year": year, "baseline": baseline, **summarize(br), **exposure_stats(br)})
    return out


def q2_exit_decomp(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    current = [r for r in rows if r.get("baseline") == "current_rulebook"]
    by_reason = defaultdict(list)
    for r in current:
        by_reason[str(r.get("exit_reason") or "unknown")].append(r)
    for reason, rs in sorted(by_reason.items()):
        out.append({"exit_reason": reason, **summarize(rs)})
    for year in YEARS:
        yr = [r for r in current if int(r.get("year") or 0) == year]
        by = defaultdict(list)
        for r in yr:
            by[str(r.get("exit_reason") or "unknown")].append(r)
        for reason, rs in sorted(by.items()):
            out.append({"year": year, "exit_reason": reason, **summarize(rs)})
    return out


def q3_regime(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for year in YEARS:
        for baseline in BASELINES:
            rs = [r for r in rows if r.get("baseline") == baseline and int(r.get("year") or 0) == year]
            out.append({"year": year, "baseline": baseline, **summarize(rs), **exposure_stats(rs)})
    # add excess by year
    for year in YEARS:
        cur = next(x for x in out if x.get("year") == year and x.get("baseline") == "current_rulebook")
        for comp in ["buy_hold", "naive_momentum"]:
            c = next(x for x in out if x.get("year") == year and x.get("baseline") == comp)
            out.append({
                "year": year,
                "comparison": f"current_vs_{comp}",
                "excess_pnl_krw": cur["total_pnl_krw"] - c["total_pnl_krw"],
                "excess_expectancy_krw": cur["expectancy_krw"] - c["expectancy_krw"],
                "invested_return_gap_pct": cur.get("invested_return_pct", 0) - c.get("invested_return_pct", 0),
            })
    return out


def q4_market_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    all_keys = sorted({k for r in rows[:1000] for k in r.keys()})
    wanted = [k for k in all_keys if "market" in k.lower() or "vix" in k.lower() or "score" in k.lower() or "sector" in k.lower()]
    examples = []
    for r in rows[:20]:
        examples.append({k: r.get(k) for k in wanted})
    return [{
        "available_market_like_fields": wanted,
        "field_count": len(wanted),
        "verdict": "추가 데이터 필요: bv2_trades.jsonl에는 진입 당시 market_score/VIX/sector score가 저장돼 있지 않음" if not wanted else "market-like fields present",
        "examples": examples[:3],
    }]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    q1 = q1_exposure(rows)
    q2 = q2_exit_decomp(rows)
    q3 = q3_regime(rows)
    q4 = q4_market_fields(rows)
    write_jsonl(OUT / "q1_exposure.jsonl", q1)
    write_jsonl(OUT / "q2_exit_reason_decomp.jsonl", q2)
    write_jsonl(OUT / "q3_regime_yearly.jsonl", q3)
    write_jsonl(OUT / "q4_market_adjustment_fields.jsonl", q4)

    q1_top = {r["baseline"]: r for r in q1 if "year" not in r}
    q2_sorted = sorted([r for r in q2 if "year" not in r], key=lambda x: abs(x["total_pnl_krw"]), reverse=True)
    q3_years = [r for r in q3 if r.get("baseline") == "current_rulebook"]
    # Cause scoring.
    cur = q1_top["current_rulebook"]
    bh = q1_top["buy_hold"]
    naive = q1_top["naive_momentum"]
    cause_rows = []
    if cur["avg_holding_days"] < bh["avg_holding_days"] / 10 and cur["total_pnl_krw"] < bh["total_pnl_krw"]:
        cause_rows.append({"rank": 1, "cause": "보유기간/노출 부족", "evidence": f"current avg holding {cur['avg_holding_days']:.1f}d vs buy-hold {bh['avg_holding_days']:.1f}d; total pnl gap {cur['total_pnl_krw']-bh['total_pnl_krw']:.0f}"})
    target = next((x for x in q2_sorted if "target" in x.get("exit_reason", "").lower() or "take" in x.get("exit_reason", "").lower()), None)
    if target and target["trade_count"] / max(1, cur["trade_count"]) > 0.25:
        cause_rows.append({"rank": len(cause_rows)+1, "cause": "익절/짧은 청산으로 추세 포기 가능성", "evidence": f"target-like exits {target['trade_count']} trades, avg hold {target['avg_holding_days']:.1f}d, pnl {target['total_pnl_krw']:.0f}"})
    if cur["total_pnl_krw"] < naive["total_pnl_krw"] and cur["avg_holding_days"] <= naive["avg_holding_days"] * 1.3:
        cause_rows.append({"rank": len(cause_rows)+1, "cause": "단순 모멘텀 대비 상승 추세 포착 부족", "evidence": f"current pnl {cur['total_pnl_krw']:.0f} vs naive {naive['total_pnl_krw']:.0f}; current hold {cur['avg_holding_days']:.1f}d vs naive {naive['avg_holding_days']:.1f}d"})
    write_jsonl(OUT / "cause_ranking.jsonl", cause_rows)

    report = []
    report.append("# BV-3 raw log diagnostics")
    report.append("")
    report.append(f"- source: `{TRADES}`")
    report.append(f"- rows analysed: {len(rows)} (current/buy_hold/naive only; random excluded for focused diagnosis)")
    report.append("")
    report.append("## Q1 Exposure / invested capital comparison")
    report.append("| baseline | trades | total_pnl | invested_return_pct | pnl_per_turnover_pct | avg_hold_days | avg_concurrent | turnover |")
    report.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for b in BASELINES:
        r = q1_top[b]
        report.append(f"| {b} | {r['trade_count']} | {r['total_pnl_krw']:.0f} | {r['invested_return_pct']:.2f} | {r['pnl_per_turnover_pct']:.2f} | {r['avg_holding_days']:.1f} | {r['avg_concurrent_positions']:.2f} | {r['turnover_krw']:.0f} |")
    report.append("")
    report.append("## Q2 Exit reason decomposition (current rulebook)")
    report.append("| exit_reason | trades | total_pnl | expectancy | avg_hold_days | win_rate |")
    report.append("|---|---:|---:|---:|---:|---:|")
    for r in q2_sorted:
        report.append(f"| {r['exit_reason']} | {r['trade_count']} | {r['total_pnl_krw']:.0f} | {r['expectancy_krw']:.0f} | {r['avg_holding_days']:.1f} | {r['win_rate_pct']:.1f} |")
    report.append("")
    report.append("## Q3 Year / regime decomposition")
    report.append("| year | current_pnl | buy_hold_pnl | naive_pnl | current_excess_vs_bh | current_excess_vs_naive |")
    report.append("|---:|---:|---:|---:|---:|---:|")
    for year in YEARS:
        c = next(r for r in q3 if r.get("year") == year and r.get("baseline") == "current_rulebook")
        b = next(r for r in q3 if r.get("year") == year and r.get("baseline") == "buy_hold")
        n = next(r for r in q3 if r.get("year") == year and r.get("baseline") == "naive_momentum")
        report.append(f"| {year} | {c['total_pnl_krw']:.0f} | {b['total_pnl_krw']:.0f} | {n['total_pnl_krw']:.0f} | {c['total_pnl_krw']-b['total_pnl_krw']:.0f} | {c['total_pnl_krw']-n['total_pnl_krw']:.0f} |")
    report.append("")
    report.append("## Q4 Market adjustment traceability")
    report.append(f"- {q4[0]['verdict']}")
    report.append("")
    report.append("## 상승을 놓치는 범인 순위")
    for r in cause_rows:
        report.append(f"{r['rank']}. **{r['cause']}** — {r['evidence']}")
    report.append("")
    report.append("## files")
    for name in ["q1_exposure.jsonl", "q2_exit_reason_decomp.jsonl", "q3_regime_yearly.jsonl", "q4_market_adjustment_fields.jsonl", "cause_ranking.jsonl"]:
        report.append(f"- `{OUT / name}`")
    (OUT / "bv3_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (OUT / "bv3_summary.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "source": str(TRADES),
        "rows_analysed": len(rows),
        "q1_overall": q1_top,
        "q2_exit_overall": q2_sorted,
        "q3_year_current": q3_years,
        "q4_market_fields": q4[0],
        "cause_ranking": cause_rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "q1": q1_top, "top_causes": cause_rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
