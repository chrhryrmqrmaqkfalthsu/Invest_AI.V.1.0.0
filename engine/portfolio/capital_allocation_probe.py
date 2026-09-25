"""Offline reweighting probe for capital allocation v7.

The probe does not simulate new entries/exits. It takes the realistic baseline
trade log and asks whether pre-registered notional multipliers would have
improved results under the same total gross entry exposure.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from engine.portfolio.noop_gate import load_promoted_rulebooks

BASELINE_CSV = Path("data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv")
OUT_DIR = Path("data/_system/research/central_portfolio/capital_allocation_reweight_probe")
MIN_EFFECT_PCT = 1.50
MAX_TICKER_GROSS_SHARE_PCT = 20.0
PATH_DEPENDENT_TIMEOUT = {"time_out", "timeout"}


@dataclass(frozen=True)
class ProbeCandidate:
    name: str
    description: str
    implementation_eligible: bool
    multiplier: Callable[[dict[str, Any]], float]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _load_rows(path: Path = BASELINE_CSV) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _gross_entry(row: dict[str, Any]) -> float:
    shares = _to_float(row.get("total_shares"), _to_float(row.get("entry_shares")))
    return _to_float(row.get("entry_price")) * shares


def _baseline_pnl(row: dict[str, Any]) -> float:
    return _to_float(row.get("pnl_krw"))


def _live_strength(row: dict[str, Any]) -> float:
    threshold = _to_float(row.get("entry_signal_threshold"))
    if threshold <= 0:
        return 0.0
    return _to_float(row.get("entry_signal_score")) / threshold


def _percentile_map(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1])
    if len(ordered) == 1:
        return {ordered[0][0]: 1.0}
    return {ticker: idx / (len(ordered) - 1) for idx, (ticker, _) in enumerate(ordered)}


def _historical_quality_map() -> dict[str, float]:
    values: dict[str, float] = {}
    for ticker, rulebook in load_promoted_rulebooks():
        values[ticker] = _to_float(getattr(rulebook, "expectancy_pct", None))
    return _percentile_map(values)


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _build_candidates(rows: list[dict[str, Any]]) -> list[ProbeCandidate]:
    scores = [_to_float(row.get("entry_signal_score")) for row in rows]
    score_median = _median(scores)
    quality = _historical_quality_map()
    ticker_gross: dict[str, float] = defaultdict(float)
    for row in rows:
        ticker_gross[str(row.get("ticker") or "")] += _gross_entry(row)
    target_ticker_gross = sum(ticker_gross.values()) / len(ticker_gross) if ticker_gross else 0.0

    def fixed(_: dict[str, Any]) -> float:
        return 1.0

    def entry_day_equal_control(_: dict[str, Any]) -> float:
        # Baseline already uses fixed fractional notional per trade, so this is
        # intentionally identical. It catches accidental probe drift.
        return 1.0

    def score_halves_soft(row: dict[str, Any]) -> float:
        return 1.05 if _to_float(row.get("entry_signal_score")) >= score_median else 0.95

    def live_strength_bucket(row: dict[str, Any]) -> float:
        strength = _live_strength(row)
        if strength < 1.05:
            return 0.75
        if strength < 1.10:
            return 1.00
        if strength < 1.30:
            return 1.10
        return 1.00

    def live_strength_monotone_soft(row: dict[str, Any]) -> float:
        strength = _live_strength(row)
        if strength < 1.05:
            return 0.85
        if strength < 1.10:
            return 0.95
        if strength < 1.30:
            return 1.05
        return 1.10

    def historical_quality_bucket(row: dict[str, Any]) -> float:
        q = quality.get(str(row.get("ticker") or ""), 0.50)
        if q < 0.25:
            return 0.90
        if q >= 0.75:
            return 1.10
        return 1.00

    def hybrid_floor_cap(row: dict[str, Any]) -> float:
        return _clamp(live_strength_bucket(row) * historical_quality_bucket(row), 0.75, 1.15)

    def equal_ticker_gross_exploratory(row: dict[str, Any]) -> float:
        ticker = str(row.get("ticker") or "")
        gross = ticker_gross.get(ticker, 0.0)
        if gross <= 0 or target_ticker_gross <= 0:
            return 1.0
        # Uses full-period realized trade distribution, so it is exploratory only.
        return _clamp(target_ticker_gross / gross, 0.50, 2.00)

    return [
        ProbeCandidate("fixed_30_control", "Baseline fixed notional; must match reference.", False, fixed),
        ProbeCandidate("entry_day_equal_control", "Same-day equal weight control; identical under fixed_30 baseline.", False, entry_day_equal_control),
        ProbeCandidate("entry_signal_score_halves_soft", "Median split score: low 0.95x, high 1.05x.", True, score_halves_soft),
        ProbeCandidate("live_strength_bucket_conservative", "Pre-registered live_strength buckets: 0.75/1.00/1.10/1.00.", True, live_strength_bucket),
        ProbeCandidate("live_strength_monotone_soft", "Sanity monotone live_strength buckets: 0.85/0.95/1.05/1.10.", True, live_strength_monotone_soft),
        ProbeCandidate("historical_quality_bucket", "Expectancy percentile buckets: 0.90/1.00/1.10.", True, historical_quality_bucket),
        ProbeCandidate("hybrid_floor_and_cap", "live_strength bucket * historical quality, clamped 0.75~1.15.", True, hybrid_floor_cap),
        ProbeCandidate("equal_ticker_gross_exploratory", "Ex-post equal ticker gross exposure; not implementation-eligible.", False, equal_ticker_gross_exploratory),
    ]


def _scaled_rows(
    rows: list[dict[str, Any]],
    multipliers: dict[int, float],
) -> list[dict[str, Any]]:
    base_total_gross = sum(_gross_entry(row) for row in rows)
    raw_total_gross = sum(_gross_entry(row) * multipliers.get(id(row), 1.0) for row in rows)
    normalizer = base_total_gross / raw_total_gross if raw_total_gross > 0 else 1.0
    scaled: list[dict[str, Any]] = []
    for row in rows:
        multiplier = multipliers.get(id(row), 1.0)
        gross = _gross_entry(row)
        weighted_gross = gross * multiplier * normalizer
        weighted_pnl = _baseline_pnl(row) * multiplier * normalizer
        scaled.append(
            {
                "row": row,
                "multiplier": multiplier,
                "normalizer": normalizer,
                "weighted_gross": weighted_gross,
                "weighted_pnl": weighted_pnl,
            }
        )
    return scaled


def _metrics(rows: list[dict[str, Any]], multipliers: dict[int, float]) -> dict[str, Any]:
    scaled = _scaled_rows(rows, multipliers)
    gross = sum(item["weighted_gross"] for item in scaled)
    pnl = sum(item["weighted_pnl"] for item in scaled)
    wins = [item for item in scaled if item["weighted_pnl"] > 0]
    losses = [item for item in scaled if item["weighted_pnl"] < 0]
    gross_profit = sum(item["weighted_pnl"] for item in wins)
    gross_loss = abs(sum(item["weighted_pnl"] for item in losses))
    ticker_gross: dict[str, float] = defaultdict(float)
    ticker_pnl: dict[str, float] = defaultdict(float)
    reason_pnl: dict[str, float] = defaultdict(float)
    reason_gross: dict[str, float] = defaultdict(float)
    reason_counts: Counter[str] = Counter()
    time_out_loss_pnl = 0.0
    time_out_loss_count = 0
    for item in scaled:
        row = item["row"]
        ticker = str(row.get("ticker") or "")
        reason = str(row.get("exit_reason") or "")
        ticker_gross[ticker] += item["weighted_gross"]
        ticker_pnl[ticker] += item["weighted_pnl"]
        reason_pnl[reason] += item["weighted_pnl"]
        reason_gross[reason] += item["weighted_gross"]
        reason_counts[reason] += 1
        if reason in PATH_DEPENDENT_TIMEOUT and item["weighted_pnl"] < 0:
            time_out_loss_count += 1
            time_out_loss_pnl += item["weighted_pnl"]

    worst_5 = sorted(scaled, key=lambda item: item["weighted_pnl"])[:5]
    max_ticker_gross_share = max((value / gross * 100.0 for value in ticker_gross.values()), default=0.0)
    top3_ticker_gross_share = sum(sorted((value for value in ticker_gross.values()), reverse=True)[:3]) / gross * 100.0 if gross else 0.0
    return {
        "trade_count": len(rows),
        "gross_entry_krw": float(gross),
        "total_pnl_krw": float(pnl),
        "total_return_on_gross_entry_pct": (pnl / gross * 100.0) if gross > 0 else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "win_rate_pct": (len(wins) / len(rows) * 100.0) if rows else 0.0,
        "time_out_total_pnl_krw": float(sum(value for reason, value in reason_pnl.items() if reason in PATH_DEPENDENT_TIMEOUT)),
        "time_out_loss_count": time_out_loss_count,
        "time_out_loss_pnl_krw": float(time_out_loss_pnl),
        "exit_reason_pnl_krw": {key: float(value) for key, value in sorted(reason_pnl.items())},
        "exit_reason_gross_krw": {key: float(value) for key, value in sorted(reason_gross.items())},
        "exit_reason_counts": dict(sorted(reason_counts.items())),
        "max_ticker_gross_share_pct": float(max_ticker_gross_share),
        "top3_ticker_gross_share_pct": float(top3_ticker_gross_share),
        "ticker_gross_share_pct": {key: (value / gross * 100.0 if gross else 0.0) for key, value in sorted(ticker_gross.items())},
        "ticker_pnl_krw": {key: float(value) for key, value in sorted(ticker_pnl.items())},
        "worst_5_weighted_trades": [
            {
                "ticker": item["row"].get("ticker"),
                "entry_date": item["row"].get("entry_date"),
                "exit_date": item["row"].get("exit_date"),
                "exit_reason": item["row"].get("exit_reason"),
                "pnl_pct": _to_float(item["row"].get("pnl_pct")),
                "weighted_pnl_krw": float(item["weighted_pnl"]),
                "weighted_gross_krw": float(item["weighted_gross"]),
                "multiplier": float(item["multiplier"]),
            }
            for item in worst_5
        ],
    }


def _yearly_deltas(rows: list[dict[str, Any]], baseline_multipliers: dict[int, float], candidate_multipliers: dict[int, float]) -> dict[str, float]:
    by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_year[str(row.get("entry_date") or "")[:4]].append(row)
    deltas: dict[str, float] = {}
    for year, year_rows in sorted(by_year.items()):
        base = _metrics(year_rows, baseline_multipliers)
        cand = _metrics(year_rows, candidate_multipliers)
        deltas[year] = float(cand["total_return_on_gross_entry_pct"] - base["total_return_on_gross_entry_pct"])
    return deltas


def _leave_one_ticker_out_deltas(rows: list[dict[str, Any]], baseline_multipliers: dict[int, float], candidate_multipliers: dict[int, float]) -> dict[str, float]:
    tickers = sorted({str(row.get("ticker") or "") for row in rows})
    deltas: dict[str, float] = {}
    for ticker in tickers:
        subset = [row for row in rows if str(row.get("ticker") or "") != ticker]
        if not subset:
            continue
        base = _metrics(subset, baseline_multipliers)
        cand = _metrics(subset, candidate_multipliers)
        deltas[ticker] = float(cand["total_return_on_gross_entry_pct"] - base["total_return_on_gross_entry_pct"])
    return deltas


def _evaluate_candidate(rows: list[dict[str, Any]], candidate: ProbeCandidate, baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    baseline_multipliers = {id(row): 1.0 for row in rows}
    candidate_multipliers = {id(row): max(0.0, float(candidate.multiplier(row))) for row in rows}
    metrics = _metrics(rows, candidate_multipliers)
    return_delta = float(metrics["total_return_on_gross_entry_pct"] - baseline_metrics["total_return_on_gross_entry_pct"])
    yearly_deltas = _yearly_deltas(rows, baseline_multipliers, candidate_multipliers)
    loo_deltas = _leave_one_ticker_out_deltas(rows, baseline_multipliers, candidate_multipliers)
    yearly_positive = all(delta > 0.0 for delta in yearly_deltas.values()) if yearly_deltas else False
    loo_positive = all(delta > 0.0 for delta in loo_deltas.values()) if loo_deltas else False
    time_out_not_worse = metrics["time_out_loss_pnl_krw"] >= baseline_metrics["time_out_loss_pnl_krw"] - 1e-9
    concentration_ok = metrics["max_ticker_gross_share_pct"] <= MAX_TICKER_GROSS_SHARE_PCT
    passed = bool(
        candidate.implementation_eligible
        and return_delta >= MIN_EFFECT_PCT
        and yearly_positive
        and loo_positive
        and time_out_not_worse
        and concentration_ok
    )
    return {
        "candidate": candidate.name,
        "description": candidate.description,
        "implementation_eligible": candidate.implementation_eligible,
        "passed": passed,
        "return_delta_pct_of_gross_entry": return_delta,
        "yearly_return_deltas_pct": yearly_deltas,
        "min_yearly_return_delta_pct": min(yearly_deltas.values()) if yearly_deltas else None,
        "leave_one_ticker_out_return_deltas_pct": loo_deltas,
        "min_leave_one_ticker_out_delta_pct": min(loo_deltas.values()) if loo_deltas else None,
        "time_out_loss_pnl_delta_krw": float(metrics["time_out_loss_pnl_krw"] - baseline_metrics["time_out_loss_pnl_krw"]),
        "time_out_total_pnl_delta_krw": float(metrics["time_out_total_pnl_krw"] - baseline_metrics["time_out_total_pnl_krw"]),
        "max_ticker_gross_share_delta_pct": float(metrics["max_ticker_gross_share_pct"] - baseline_metrics["max_ticker_gross_share_pct"]),
        "criteria": {
            "min_effect_pct": MIN_EFFECT_PCT,
            "requires_both_years_positive": True,
            "requires_all_leave_one_ticker_out_positive": True,
            "requires_time_out_loss_pnl_not_worse": True,
            "max_ticker_gross_share_pct": MAX_TICKER_GROSS_SHARE_PCT,
        },
        "metrics": metrics,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _flat_result_rows(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        metrics = result["metrics"]
        rows.append(
            {
                "candidate": result["candidate"],
                "implementation_eligible": result["implementation_eligible"],
                "passed": result["passed"],
                "return_delta_pct_of_gross_entry": result["return_delta_pct_of_gross_entry"],
                "min_yearly_return_delta_pct": result["min_yearly_return_delta_pct"],
                "min_leave_one_ticker_out_delta_pct": result["min_leave_one_ticker_out_delta_pct"],
                "time_out_loss_pnl_delta_krw": result["time_out_loss_pnl_delta_krw"],
                "time_out_total_pnl_delta_krw": result["time_out_total_pnl_delta_krw"],
                "max_ticker_gross_share_pct": metrics["max_ticker_gross_share_pct"],
                "max_ticker_gross_share_delta_pct": result["max_ticker_gross_share_delta_pct"],
                "total_return_on_gross_entry_pct": metrics["total_return_on_gross_entry_pct"],
                "total_pnl_krw": metrics["total_pnl_krw"],
                "profit_factor": metrics["profit_factor"],
                "time_out_loss_count": metrics["time_out_loss_count"],
                "description": result["description"],
            }
        )
    return rows


def run_capital_allocation_reweight_probe(
    baseline_csv: Path = BASELINE_CSV,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    rows = _load_rows(baseline_csv)
    if not rows:
        raise ValueError(f"baseline trade log is empty: {baseline_csv}")
    required = {"entry_date", "exit_date", "ticker", "pnl_pct", "pnl_krw", "entry_price", "total_shares", "entry_signal_score", "entry_signal_threshold"}
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"missing required trade fields for probe: {missing}")

    baseline_multipliers = {id(row): 1.0 for row in rows}
    baseline_metrics = _metrics(rows, baseline_multipliers)
    candidates = _build_candidates(rows)
    results = [_evaluate_candidate(rows, candidate, baseline_metrics) for candidate in candidates]
    passed_candidates = [result for result in results if result["passed"]]
    eligible_candidates = [result for result in results if result["implementation_eligible"]]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "probe_results.csv", _flat_result_rows(results))
    summary = {
        "gate": "capital_allocation_reweight_probe",
        "baseline_csv": str(baseline_csv),
        "out_dir": str(out_dir),
        "trade_count": len(rows),
        "ticker_count": len({str(row.get("ticker") or "") for row in rows}),
        "baseline_metrics": baseline_metrics,
        "candidate_count": len(results),
        "eligible_candidate_count": len(eligible_candidates),
        "passed_candidate_count": len(passed_candidates),
        "passed_candidates": [result["candidate"] for result in passed_candidates],
        "implementation_recommended": len(passed_candidates) > 0,
        "passed": True,
        "results": results,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
