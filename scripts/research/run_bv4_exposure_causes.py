#!/usr/bin/env python3
"""BV-4 exposure shortage cause analysis.

Read-only analysis of BV-2/BV-3 outputs plus code-line references.
No backtest rerun.
Writes only under data/_system/research/bv4_20260607/.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BV2 = Path("data/_system/research/bv2_20260607")
BV3 = Path("data/_system/research/bv3_20260607")
OUT = Path("data/_system/research/bv4_20260607")
TRADES = BV2 / "bv2_trades.jsonl"
SUMMARY = BV2 / "bv2_summary.json"
POSITION_LIMIT = 120_000.0
FULL_CAPACITY = 85 * POSITION_LIMIT
YEARS = [2022, 2023, 2024, 2025]

CODE_REFS = {
    "signal_gate": [
        {"path": "engine/learning/backtest.py", "line": 278, "text": "sig = evaluate_signal(...)"},
        {"path": "engine/learning/backtest.py", "line": 286, "text": "if not sig.should_buy: i += 1; continue"},
    ],
    "position_sizing": [
        {"path": "engine/learning/backtest.py", "line": 291, "text": "amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)"},
        {"path": "engine/strategies/evaluator.py", "line": 290, "text": "fixed: ratio = rb.base_position_ratio"},
        {"path": "engine/strategies/evaluator.py", "line": 293, "text": "signal_scaled: ratio = rb.base_position_ratio * min(score/threshold * multiplier, 1.0)"},
        {"path": "engine/strategies/evaluator.py", "line": 309, "text": "return position_limit_krw * clamp(ratio, 0, 1)"},
        {"path": "engine/strategies/rulebook.py", "line": 180, "text": "base_position_ratio range: 0.3~1.0"},
    ],
    "slot_limit": [
        {"path": "engine/learning/backtest.py", "line": 76, "text": "position_limit_krw is per-symbol limit, default 120000"},
        {"path": "engine/learning/backtest.py", "line": 322, "text": "after exit, same ticker jumps to exit_idx + cooldown_days"},
        {"path": "engine/learning/backtest.py", "line": 70, "text": "run_backtest runs one ticker independently; no portfolio-level max positions parameter"},
    ],
}


def f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def d(v: Any) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def read_current_trades() -> list[dict[str, Any]]:
    rows = []
    with TRADES.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("baseline") == "current_rulebook":
                rows.append(r)
    return rows


def entry_notional(r: dict[str, Any]) -> float:
    return abs(f(r.get("entry_price")) * f(r.get("entry_shares")))


def total_notional(r: dict[str, Any]) -> float:
    shares = f(r.get("total_shares"), f(r.get("entry_shares")))
    return abs(f(r.get("avg_cost"), f(r.get("entry_price"))) * shares)


def date_range(rows: list[dict[str, Any]]) -> list[date]:
    starts = [d(r.get("entry_date")) for r in rows]
    ends = [d(r.get("exit_date")) for r in rows]
    dates = [x for x in starts + ends if x]
    if not dates:
        return []
    cur = min(dates)
    last = max(dates)
    out = []
    while cur <= last:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def daily_activity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    days = date_range(rows)
    by_entry = defaultdict(list)
    by_exit = defaultdict(list)
    for r in rows:
        ed = d(r.get("entry_date")); xd = d(r.get("exit_date"))
        if ed: by_entry[ed].append(r)
        if xd: by_exit[xd].append(r)
    out = []
    for day in days:
        active = []
        exposure = 0.0
        for r in rows:
            ed = d(r.get("entry_date")); xd = d(r.get("exit_date"))
            if ed and xd and ed <= day <= xd:
                active.append(r)
                exposure += total_notional(r)
        out.append({
            "date": day.isoformat(),
            "entry_count": len(by_entry.get(day, [])),
            "exit_count": len(by_exit.get(day, [])),
            "active_positions": len(active),
            "active_exposure_krw": exposure,
            "active_exposure_pct_of_full_85x120k": exposure / FULL_CAPACITY * 100.0,
        })
    return out


def quant(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    xs = sorted(vals)
    pos = (len(xs)-1)*q
    lo = int(pos); hi = min(lo+1, len(xs)-1)
    return xs[lo]*(1-(pos-lo)) + xs[hi]*(pos-lo)


def summarize_daily(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [int(r["entry_count"]) for r in rows]
    active = [int(r["active_positions"]) for r in rows]
    exp = [f(r["active_exposure_pct_of_full_85x120k"]) for r in rows]
    return {
        "calendar_days": len(rows),
        "days_with_entry": sum(1 for x in entries if x > 0),
        "entry_count_mean_per_calendar_day": statistics.mean(entries) if entries else 0.0,
        "entry_count_median": statistics.median(entries) if entries else 0.0,
        "entry_count_p95": quant(entries, 0.95),
        "entry_count_max": max(entries) if entries else 0,
        "active_positions_mean": statistics.mean(active) if active else 0.0,
        "active_positions_p95": quant(active, 0.95),
        "active_positions_max": max(active) if active else 0,
        "actual_exposure_pct_mean": statistics.mean(exp) if exp else 0.0,
        "actual_exposure_pct_p95": quant(exp, 0.95),
        "actual_exposure_pct_max": max(exp) if exp else 0.0,
    }


def position_size_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [entry_notional(r) for r in rows]
    totals = [total_notional(r) for r in rows]
    ratios = [x / POSITION_LIMIT * 100.0 for x in entries]
    return {
        "trade_count": len(rows),
        "entry_notional_mean": statistics.mean(entries) if entries else 0.0,
        "entry_notional_median": statistics.median(entries) if entries else 0.0,
        "entry_notional_p25": quant(entries, 0.25),
        "entry_notional_p75": quant(entries, 0.75),
        "entry_ratio_to_120k_mean_pct": statistics.mean(ratios) if ratios else 0.0,
        "entry_ratio_to_120k_median_pct": statistics.median(ratios) if ratios else 0.0,
        "entry_ratio_to_120k_p25_pct": quant(ratios, 0.25),
        "entry_ratio_to_120k_p75_pct": quant(ratios, 0.75),
        "entry_ratio_full_or_near_full_count": sum(1 for x in ratios if x >= 90.0),
        "entry_ratio_below_half_count": sum(1 for x in ratios if x < 50.0),
        "total_position_notional_mean": statistics.mean(totals) if totals else 0.0,
    }


def by_year(rows: list[dict[str, Any]], daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for year in YEARS:
        rs = [r for r in rows if int(r.get("year") or 0) == year]
        ds = [r for r in daily if r["date"].startswith(str(year))]
        s = position_size_stats(rs)
        s.update(summarize_daily(ds))
        s["year"] = year
        s["total_pnl_krw"] = sum(f(r.get("pnl_krw")) for r in rs)
        out.append(s)
    return out


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = read_current_trades()
    daily = daily_activity(rows)
    daily_summary = summarize_daily(daily)
    size_summary = position_size_stats(rows)
    year_rows = by_year(rows, daily)

    candidate1 = {
        "candidate": "신호 부족",
        "verdict": "부분 사실",
        "evidence": {
            "entry_count_mean_per_calendar_day": daily_summary["entry_count_mean_per_calendar_day"],
            "days_with_entry": daily_summary["days_with_entry"],
            "calendar_days": daily_summary["calendar_days"],
            "active_positions_mean": daily_summary["active_positions_mean"],
            "active_positions_max": daily_summary["active_positions_max"],
            "note": "raw 로그는 실제 진입만 포함하므로 threshold 미통과 후보 수는 직접 볼 수 없다. 다만 실제 진입 빈도와 동시보유가 낮아 신호/청산 후 재진입 빈도가 노출의 상한을 크게 낮춘 것은 확인된다.",
        },
        "code_refs": CODE_REFS["signal_gate"],
    }
    candidate2 = {
        "candidate": "슬롯/포지션 수 제한",
        "verdict": "거짓(백테스트 기준 글로벌 슬롯 제한 없음), 단 종목별 1포지션+청산까지 재진입 금지 존재",
        "evidence": {
            "portfolio_global_slot_limit_found": False,
            "per_ticker_sequential_lock": True,
            "active_positions_max_observed": daily_summary["active_positions_max"],
            "comment": "run_backtest는 종목별 독립 실행이며 포트폴리오 max positions 변수가 없다. 단 같은 종목은 청산일까지 건너뛰므로 보유기간이 길면 그 종목의 추가 진입은 막힌다.",
        },
        "code_refs": CODE_REFS["slot_limit"],
    }
    candidate3 = {
        "candidate": "사이징 과소",
        "verdict": "사실(강한 원인)",
        "evidence": size_summary,
        "code_refs": CODE_REFS["position_sizing"],
    }

    cause_rank = [
        {
            "rank": 1,
            "cause": "사이징 과소 + 낮은 동시보유의 곱",
            "evidence": f"평균 진입금액 {size_summary['entry_notional_mean']:.0f}원({size_summary['entry_ratio_to_120k_mean_pct']:.1f}% of 120k) × 평균 동시보유 {daily_summary['active_positions_mean']:.1f}개 → 실제 평균 노출 {daily_summary['actual_exposure_pct_mean']:.1f}%",
            "exposure_if_full_120k_same_active_pct": daily_summary["active_positions_mean"] * POSITION_LIMIT / FULL_CAPACITY * 100.0,
        },
        {
            "rank": 2,
            "cause": "신호/보유기간 구조로 동시보유 수가 낮음",
            "evidence": f"일평균 신규진입 {daily_summary['entry_count_mean_per_calendar_day']:.2f}건, 평균 동시보유 {daily_summary['active_positions_mean']:.1f}/85, max {daily_summary['active_positions_max']}개",
            "exposure_if_30_active_at_current_size_pct": 30 * size_summary["entry_notional_mean"] / FULL_CAPACITY * 100.0,
            "exposure_if_50_active_at_current_size_pct": 50 * size_summary["entry_notional_mean"] / FULL_CAPACITY * 100.0,
        },
        {
            "rank": 3,
            "cause": "글로벌 슬롯 제한은 원인 아님",
            "evidence": "백테스트 코드에는 포트폴리오 max positions 제한 없음. 관측 max active 56개까지 가능했으므로 12개 평균은 제한 때문이 아님.",
        },
    ]

    exposure_estimates = {
        "current_actual_mean_pct": daily_summary["actual_exposure_pct_mean"],
        "same_active_full_120k_pct": daily_summary["active_positions_mean"] * POSITION_LIMIT / FULL_CAPACITY * 100.0,
        "current_size_30_active_pct": 30 * size_summary["entry_notional_mean"] / FULL_CAPACITY * 100.0,
        "current_size_50_active_pct": 50 * size_summary["entry_notional_mean"] / FULL_CAPACITY * 100.0,
        "full_120k_30_active_pct": 30 * POSITION_LIMIT / FULL_CAPACITY * 100.0,
        "full_120k_50_active_pct": 50 * POSITION_LIMIT / FULL_CAPACITY * 100.0,
        "upper_bound_85_full_pct": 100.0,
    }

    write_jsonl(OUT / "candidate1_signal_scarcity_daily.jsonl", daily)
    write_jsonl(OUT / "candidate2_code_limits.jsonl", [candidate2])
    write_jsonl(OUT / "candidate3_position_sizing.jsonl", [{"overall": size_summary}, *year_rows])
    write_jsonl(OUT / "cause_ranking.jsonl", cause_rank)
    (OUT / "bv4_summary.json").write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "source_trades": str(TRADES),
        "position_limit": POSITION_LIMIT,
        "full_capacity_85x120k": FULL_CAPACITY,
        "candidate1_signal_scarcity": candidate1,
        "candidate2_slot_limit": candidate2,
        "candidate3_position_sizing": candidate3,
        "daily_summary": daily_summary,
        "position_size_summary": size_summary,
        "yearly": year_rows,
        "cause_ranking": cause_rank,
        "exposure_estimates": exposure_estimates,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# BV-4 노출 부족 원인 분석",
        "",
        f"- source: `{TRADES}`",
        f"- current trades: {len(rows)}",
        f"- capacity model: 85 symbols × {POSITION_LIMIT:,.0f} = {FULL_CAPACITY:,.0f}",
        "",
        "## 후보별 판정",
        "| 후보 | 판정 | 핵심 근거 |",
        "|---|---|---|",
        f"| 신호 부족 | 부분 사실 | 일평균 신규진입 {daily_summary['entry_count_mean_per_calendar_day']:.2f}건, 평균 동시보유 {daily_summary['active_positions_mean']:.1f}/85 |",
        f"| 슬롯/포지션 수 제한 | 거짓(글로벌 제한 없음) | max active {daily_summary['active_positions_max']}까지 관측, 코드상 portfolio max positions 없음 |",
        f"| 사이징 과소 | 사실 | 평균 진입 {size_summary['entry_notional_mean']:.0f}원 = per-symbol 120k의 {size_summary['entry_ratio_to_120k_mean_pct']:.1f}% |",
        "",
        "## 노출 추정",
        "| 시나리오 | 평균 노출 추정 |",
        "|---|---:|",
        f"| 현재 실제 | {exposure_estimates['current_actual_mean_pct']:.1f}% |",
        f"| 현재 동시보유 수 그대로, 종목당 120k full sizing | {exposure_estimates['same_active_full_120k_pct']:.1f}% |",
        f"| 현재 평균 사이즈, 동시보유 30개 | {exposure_estimates['current_size_30_active_pct']:.1f}% |",
        f"| 현재 평균 사이즈, 동시보유 50개 | {exposure_estimates['current_size_50_active_pct']:.1f}% |",
        f"| full sizing, 동시보유 30개 | {exposure_estimates['full_120k_30_active_pct']:.1f}% |",
        f"| full sizing, 동시보유 50개 | {exposure_estimates['full_120k_50_active_pct']:.1f}% |",
        "",
        "## 진짜 원인 순위",
    ]
    for r in cause_rank:
        lines.append(f"{r['rank']}. **{r['cause']}** — {r['evidence']}")
    lines.extend([
        "",
        "## 코드 근거",
        "- 신호 게이트: `engine/learning/backtest.py:278-286`에서 `evaluate_signal()` 후 `sig.should_buy`가 아니면 진입 없음.",
        "- 사이징: `engine/learning/backtest.py:291-293`에서 `calc_position_size_krw()` 금액을 주수로 변환.",
        "- 사이징 공식: `engine/strategies/evaluator.py:290-309`, `base_position_ratio`와 `signal_multiplier`가 per-symbol 한도 안에서 금액 결정.",
        "- 슬롯: `run_backtest()`는 종목별 독립 루프이며 글로벌 max position 제한 없음. `backtest.py:322`에서 같은 종목만 청산 후 cooldown까지 건너뜀.",
        "",
        "## 파일",
        f"- `{OUT / 'candidate1_signal_scarcity_daily.jsonl'}`",
        f"- `{OUT / 'candidate2_code_limits.jsonl'}`",
        f"- `{OUT / 'candidate3_position_sizing.jsonl'}`",
        f"- `{OUT / 'cause_ranking.jsonl'}`",
        f"- `{OUT / 'bv4_summary.json'}`",
    ])
    (OUT / "bv4_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "out": str(OUT),
        "candidates": [candidate1, candidate2, candidate3],
        "exposure_estimates": exposure_estimates,
        "cause_ranking": cause_rank,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
