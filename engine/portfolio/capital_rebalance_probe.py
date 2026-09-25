"""100%-exposure / cap-binding rebalance probe for capital allocation.

This is an offline research probe only. It does not touch live order code.

Model summary
-------------
* Baseline trades define the eligible active holdings and their entry/exit dates.
* The probe compares an equal-active 100%-target baseline with candidate dynamic
  reweighting policies under the same trade universe and exit dates.
* Transaction cost is charged on turnover.
* Rebalance is gated by a dead-zone and a minimum interval.
* Concentration guard caps per-ticker gross weight.

Because the 20% concentration guard makes 100% exposure infeasible when fewer
than five tickers are active, the simulator records unallocated cash exposure.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pandas as pd

from engine.portfolio.noop_gate import load_fixed_histories, load_promoted_rulebooks

BASELINE_CSV = Path("data/_system/research/central_portfolio/conservative_core_exit/candidate_trades.csv")
OUT_DIR = Path("data/_system/research/central_portfolio/capital_rebalance_100pct_probe")
MIN_EFFECT_PCT = 1.50
MAX_TICKER_GROSS_SHARE_PCT = 20.0
PATH_DEPENDENT_TIMEOUT = {"time_out", "timeout"}


@dataclass(frozen=True)
class RebalanceConfig:
    target_exposure_pct: float = 100.0
    cash_buffer_pct: float = 0.0
    transaction_cost_bps: float = 10.0
    deadzone_weight_pct: float = 3.0
    min_rebalance_days: int = 5
    max_ticker_gross_share_pct: float = MAX_TICKER_GROSS_SHARE_PCT


@dataclass(frozen=True)
class RebalanceCandidate:
    name: str
    description: str
    implementation_eligible: bool
    scorer: Callable[[dict[str, Any]], float]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _load_rows(path: Path = BASELINE_CSV) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _date(value: Any) -> pd.Timestamp:
    return pd.Timestamp(str(value)[:10])


def _live_strength(row: dict[str, Any]) -> float:
    threshold = _to_float(row.get("entry_signal_threshold"))
    if threshold <= 0:
        return 1.0
    return _to_float(row.get("entry_signal_score")) / threshold


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _percent_return(entry: float, price: float) -> float:
    if entry <= 0 or price <= 0:
        return 0.0
    return price / entry - 1.0


def _price_lookup(histories: dict[str, pd.DataFrame], ticker: str, day: pd.Timestamp, field: str = "Close") -> Optional[float]:
    df = histories.get(ticker)
    if df is None or df.empty:
        return None
    try:
        if day in df.index:
            return float(df.loc[day][field])
        date_col = pd.to_datetime(df["date"]) if "date" in df.columns else None
        if date_col is not None:
            mask = date_col.dt.date == day.date()
            if mask.any():
                return float(df.loc[mask, field].iloc[0])
    except Exception:
        return None
    return None


def _daily_axis(rows: list[dict[str, Any]]) -> list[pd.Timestamp]:
    if not rows:
        return []
    start = min(_date(row["entry_date"]) for row in rows)
    end = max(_date(row["exit_date"]) for row in rows)
    return list(pd.date_range(start, end, freq="B"))


def _row_id(row: dict[str, Any]) -> str:
    return f"{row.get('ticker')}:{row.get('trade_index')}:{row.get('entry_date')}:{row.get('exit_date')}"


def _is_active(row: dict[str, Any], day: pd.Timestamp) -> bool:
    return _date(row["entry_date"]) <= day < _date(row["exit_date"])


def _active_rows(rows: list[dict[str, Any]], day: pd.Timestamp) -> list[dict[str, Any]]:
    return [row for row in rows if _is_active(row, day)]


def _build_feature_context(
    row: dict[str, Any],
    day: pd.Timestamp,
    histories: dict[str, pd.DataFrame],
    max_unrealized_by_id: dict[str, float],
) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "")
    entry_price = _to_float(row.get("entry_price"))
    price = _price_lookup(histories, ticker, day) or entry_price
    unrealized = _percent_return(entry_price, price)
    rid = _row_id(row)
    max_unrealized = max(max_unrealized_by_id.get(rid, unrealized), unrealized)
    max_unrealized_by_id[rid] = max_unrealized
    drawdown_from_peak = max(0.0, max_unrealized - unrealized)
    trailing_active_proxy = max_unrealized >= 0.03 and drawdown_from_peak <= 0.02
    return {
        "row": row,
        "ticker": ticker,
        "trade_id": rid,
        "day": str(day.date()),
        "live_strength": _live_strength(row),
        "unrealized_return": unrealized,
        "max_unrealized_return": max_unrealized,
        "drawdown_from_peak": drawdown_from_peak,
        "trailing_active_proxy": trailing_active_proxy,
        "exit_reason": str(row.get("exit_reason") or ""),
    }


def _candidate_set() -> list[RebalanceCandidate]:
    def equal_active(ctx: dict[str, Any]) -> float:
        return 1.0

    def entry_strength(ctx: dict[str, Any]) -> float:
        # Conservative: do not reward extremely high entry strength monotonically forever.
        strength = _to_float(ctx.get("live_strength"), 1.0)
        if strength < 1.05:
            return 0.75
        if strength < 1.10:
            return 1.00
        if strength < 1.30:
            return 1.10
        return 1.00

    def path_winner(ctx: dict[str, Any]) -> float:
        unrealized = _to_float(ctx.get("unrealized_return"))
        max_unrealized = _to_float(ctx.get("max_unrealized_return"))
        drawdown = _to_float(ctx.get("drawdown_from_peak"))
        trailing = bool(ctx.get("trailing_active_proxy"))
        score = 1.0 + 4.0 * max(0.0, unrealized) - 2.0 * max(0.0, -unrealized)
        score += 0.35 if trailing else 0.0
        score += 1.0 * max(0.0, max_unrealized - drawdown)
        return _clamp(score, 0.20, 2.00)

    def hybrid_entry_path(ctx: dict[str, Any]) -> float:
        return _clamp(0.50 * entry_strength(ctx) + 0.50 * path_winner(ctx), 0.20, 2.00)

    def anti_loser_to_winner(ctx: dict[str, Any]) -> float:
        unrealized = _to_float(ctx.get("unrealized_return"))
        if unrealized <= -0.03:
            return 0.25
        if unrealized >= 0.04:
            return 1.60
        return 1.0

    return [
        RebalanceCandidate("equal_active_100pct", "100%-target equal active weight baseline.", False, equal_active),
        RebalanceCandidate("entry_strength_bucket", "Entry live_strength buckets 0.75/1.00/1.10/1.00.", True, entry_strength),
        RebalanceCandidate("path_winner_scaling", "Increase winners with positive path/trailing proxy; cut losers.", True, path_winner),
        RebalanceCandidate("hybrid_entry_path", "50% entry strength + 50% post-entry path score.", True, hybrid_entry_path),
        RebalanceCandidate("anti_loser_to_winner", "Explicit loser trim / winner boost buckets.", True, anti_loser_to_winner),
    ]


def _weights_from_scores(contexts: list[dict[str, Any]], candidate: RebalanceCandidate, config: RebalanceConfig) -> dict[str, float]:
    if not contexts:
        return {}
    max_w = max(0.0, config.max_ticker_gross_share_pct / 100.0)
    target_exposure = max(0.0, min(1.0, (config.target_exposure_pct - config.cash_buffer_pct) / 100.0))
    # Concentration guard can make 100% infeasible when active tickers < 5.
    max_feasible = min(target_exposure, max_w * len({ctx["ticker"] for ctx in contexts}))
    raw_scores = {ctx["trade_id"]: max(0.0, float(candidate.scorer(ctx))) for ctx in contexts}
    if sum(raw_scores.values()) <= 0:
        raw_scores = {ctx["trade_id"]: 1.0 for ctx in contexts}

    by_id = {ctx["trade_id"]: ctx for ctx in contexts}
    active = set(raw_scores)
    weights = {trade_id: 0.0 for trade_id in active}
    remaining_exposure = max_feasible
    remaining = set(active)
    while remaining and remaining_exposure > 1e-12:
        score_sum = sum(raw_scores[trade_id] for trade_id in remaining)
        if score_sum <= 0:
            score_sum = float(len(remaining))
            alloc = {trade_id: remaining_exposure / len(remaining) for trade_id in remaining}
        else:
            alloc = {trade_id: remaining_exposure * raw_scores[trade_id] / score_sum for trade_id in remaining}
        capped: set[str] = set()
        for trade_id, value in alloc.items():
            ticker = by_id[trade_id]["ticker"]
            ticker_existing = sum(weights[tid] for tid, ctx in by_id.items() if ctx["ticker"] == ticker)
            ticker_room = max(0.0, max_w - ticker_existing)
            if value >= ticker_room - 1e-12:
                weights[trade_id] += ticker_room
                remaining_exposure -= ticker_room
                capped.add(trade_id)
        if not capped:
            for trade_id, value in alloc.items():
                weights[trade_id] += value
            break
        remaining -= capped
    return weights


def _should_rebalance(
    day_index: int,
    days_since_last: int,
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    config: RebalanceConfig,
) -> bool:
    if day_index == 0 or not current_weights:
        return True
    current_ids = set(current_weights)
    target_ids = set(target_weights)
    if current_ids != target_ids:
        return True
    if days_since_last < max(1, int(config.min_rebalance_days)):
        return False
    max_diff = max((abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in current_ids | target_ids), default=0.0)
    return max_diff >= config.deadzone_weight_pct / 100.0


def _next_day_return(ctx: dict[str, Any], day: pd.Timestamp, next_day: pd.Timestamp, histories: dict[str, pd.DataFrame]) -> float:
    ticker = ctx["ticker"]
    price0 = _price_lookup(histories, ticker, day)
    price1 = _price_lookup(histories, ticker, next_day)
    if price0 is None or price1 is None or price0 <= 0:
        return 0.0
    return price1 / price0 - 1.0


def simulate_rebalance_candidate(
    rows: list[dict[str, Any]],
    histories: dict[str, pd.DataFrame],
    candidate: RebalanceCandidate,
    config: RebalanceConfig,
) -> dict[str, Any]:
    days = _daily_axis(rows)
    if len(days) < 2:
        raise ValueError("not enough days to simulate")
    capital = 1.0
    current_weights: dict[str, float] = {}
    days_since_last_rebalance = 9999
    max_unrealized_by_id: dict[str, float] = {}
    contribution_by_trade: dict[str, float] = defaultdict(float)
    ticker_gross_day: dict[str, float] = defaultdict(float)
    ticker_gross_sum: dict[str, float] = defaultdict(float)
    year_start_capital: dict[str, float] = {}
    year_end_capital: dict[str, float] = {}
    total_turnover = 0.0
    total_cost = 0.0
    rebalance_count = 0
    gross_exposure_sum = 0.0
    max_ticker_gross_share = 0.0
    low_feasibility_days = 0
    active_day_count = 0

    row_by_id = {_row_id(row): row for row in rows}
    for idx, day in enumerate(days[:-1]):
        year = str(day.year)
        year_start_capital.setdefault(year, capital)
        active = _active_rows(rows, day)
        contexts = [_build_feature_context(row, day, histories, max_unrealized_by_id) for row in active]
        target_weights = _weights_from_scores(contexts, candidate, config)
        active_tickers = {ctx["ticker"] for ctx in contexts}
        if active_tickers:
            active_day_count += 1
            if len(active_tickers) * config.max_ticker_gross_share_pct < config.target_exposure_pct - config.cash_buffer_pct - 1e-9:
                low_feasibility_days += 1
        if _should_rebalance(idx, days_since_last_rebalance, current_weights, target_weights, config):
            turnover = sum(abs(target_weights.get(k, 0.0) - current_weights.get(k, 0.0)) for k in set(current_weights) | set(target_weights))
            cost = turnover * config.transaction_cost_bps / 10000.0
            capital *= max(0.0, 1.0 - cost)
            total_turnover += turnover
            total_cost += cost
            current_weights = dict(target_weights)
            days_since_last_rebalance = 0
            rebalance_count += 1
        else:
            days_since_last_rebalance += 1

        id_to_ctx = {ctx["trade_id"]: ctx for ctx in contexts}
        next_day = days[idx + 1]
        daily_return = 0.0
        ticker_weights: dict[str, float] = defaultdict(float)
        for trade_id, weight in list(current_weights.items()):
            ctx = id_to_ctx.get(trade_id)
            if ctx is None:
                continue
            r = _next_day_return(ctx, day, next_day, histories)
            daily_return += weight * r
            contribution_by_trade[trade_id] += capital * weight * r
            ticker_weights[ctx["ticker"]] += weight
        gross_exposure = sum(max(0.0, w) for w in current_weights.values())
        gross_exposure_sum += gross_exposure
        for ticker, weight in ticker_weights.items():
            max_ticker_gross_share = max(max_ticker_gross_share, weight * 100.0)
            ticker_gross_sum[ticker] += weight
            ticker_gross_day[ticker] += 1
        capital *= 1.0 + daily_return
        year_end_capital[year] = capital

    total_return_pct = (capital - 1.0) * 100.0
    yearly_returns = {
        year: (year_end_capital.get(year, start) / start - 1.0) * 100.0
        for year, start in sorted(year_start_capital.items())
        if start > 0
    }
    time_out_loss_pnl = 0.0
    time_out_total_pnl = 0.0
    for trade_id, pnl in contribution_by_trade.items():
        row = row_by_id.get(trade_id, {})
        reason = str(row.get("exit_reason") or "")
        if reason in PATH_DEPENDENT_TIMEOUT:
            time_out_total_pnl += pnl
            if pnl < 0:
                time_out_loss_pnl += pnl
    avg_exposure_pct = gross_exposure_sum / max(1, len(days) - 1) * 100.0
    return {
        "candidate": candidate.name,
        "description": candidate.description,
        "implementation_eligible": candidate.implementation_eligible,
        "total_return_net_pct": total_return_pct,
        "ending_capital_multiple": capital,
        "yearly_return_net_pct": yearly_returns,
        "turnover_sum": total_turnover,
        "transaction_cost_pct_of_initial_capital": total_cost * 100.0,
        "rebalance_count": rebalance_count,
        "avg_gross_exposure_pct": avg_exposure_pct,
        "low_feasibility_days": low_feasibility_days,
        "active_day_count": active_day_count,
        "low_feasibility_day_pct": low_feasibility_days / active_day_count * 100.0 if active_day_count else 0.0,
        "max_ticker_gross_share_pct": max_ticker_gross_share,
        "time_out_loss_pnl_units": time_out_loss_pnl,
        "time_out_total_pnl_units": time_out_total_pnl,
        "ticker_avg_gross_share_pct": {
            ticker: (ticker_gross_sum[ticker] / ticker_gross_day[ticker] * 100.0 if ticker_gross_day[ticker] else 0.0)
            for ticker in sorted(ticker_gross_sum)
        },
    }


def _leave_one_ticker_out(
    rows: list[dict[str, Any]],
    histories: dict[str, pd.DataFrame],
    candidate: RebalanceCandidate,
    baseline: RebalanceCandidate,
    config: RebalanceConfig,
) -> dict[str, float]:
    tickers = sorted({str(row.get("ticker") or "") for row in rows})
    out: dict[str, float] = {}
    for ticker in tickers:
        subset = [row for row in rows if str(row.get("ticker") or "") != ticker]
        if not subset:
            continue
        base = simulate_rebalance_candidate(subset, histories, baseline, config)
        cand = simulate_rebalance_candidate(subset, histories, candidate, config)
        out[ticker] = float(cand["total_return_net_pct"] - base["total_return_net_pct"])
    return out


def _evaluate_result(
    baseline_metrics: dict[str, Any],
    metrics: dict[str, Any],
    loo_deltas: dict[str, float],
) -> dict[str, Any]:
    delta = float(metrics["total_return_net_pct"] - baseline_metrics["total_return_net_pct"])
    yearly_deltas = {
        year: float(metrics["yearly_return_net_pct"].get(year, 0.0) - baseline_metrics["yearly_return_net_pct"].get(year, 0.0))
        for year in sorted(set(metrics["yearly_return_net_pct"]) | set(baseline_metrics["yearly_return_net_pct"]))
    }
    year_2024_2025_positive = all(yearly_deltas.get(year, 0.0) > 0.0 for year in ["2024", "2025"])
    loo_positive = all(value > 0.0 for value in loo_deltas.values()) if loo_deltas else False
    time_out_not_worse = metrics["time_out_loss_pnl_units"] >= baseline_metrics["time_out_loss_pnl_units"] - 1e-12
    concentration_ok = metrics["max_ticker_gross_share_pct"] <= MAX_TICKER_GROSS_SHARE_PCT + 1e-9
    passed = bool(
        metrics.get("implementation_eligible")
        and delta >= MIN_EFFECT_PCT
        and year_2024_2025_positive
        and loo_positive
        and time_out_not_worse
        and concentration_ok
    )
    return {
        "passed": passed,
        "return_delta_net_pct": delta,
        "yearly_return_delta_net_pct": yearly_deltas,
        "min_leave_one_ticker_out_delta_net_pct": min(loo_deltas.values()) if loo_deltas else None,
        "leave_one_ticker_out_delta_net_pct": loo_deltas,
        "time_out_loss_pnl_delta_units": float(metrics["time_out_loss_pnl_units"] - baseline_metrics["time_out_loss_pnl_units"]),
        "max_ticker_gross_share_delta_pct": float(metrics["max_ticker_gross_share_pct"] - baseline_metrics["max_ticker_gross_share_pct"]),
        "criteria": {
            "min_effect_net_pct": MIN_EFFECT_PCT,
            "requires_2024_and_2025_positive": True,
            "requires_all_leave_one_ticker_out_positive": True,
            "requires_time_out_loss_not_worse": True,
            "max_ticker_gross_share_pct": MAX_TICKER_GROSS_SHARE_PCT,
            "transaction_cost_included": True,
        },
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


def _flat_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        metrics = result["metrics"]
        rows.append({
            "candidate": result["candidate"],
            "implementation_eligible": result["implementation_eligible"],
            "passed": result["passed"],
            "return_delta_net_pct": result["return_delta_net_pct"],
            "total_return_net_pct": metrics["total_return_net_pct"],
            "transaction_cost_pct_of_initial_capital": metrics["transaction_cost_pct_of_initial_capital"],
            "turnover_sum": metrics["turnover_sum"],
            "rebalance_count": metrics["rebalance_count"],
            "avg_gross_exposure_pct": metrics["avg_gross_exposure_pct"],
            "max_ticker_gross_share_pct": metrics["max_ticker_gross_share_pct"],
            "low_feasibility_day_pct": metrics["low_feasibility_day_pct"],
            "time_out_loss_pnl_delta_units": result["time_out_loss_pnl_delta_units"],
            "min_leave_one_ticker_out_delta_net_pct": result["min_leave_one_ticker_out_delta_net_pct"],
            "description": metrics["description"],
        })
    return rows


def dry_run_plan(baseline_csv: Path = BASELINE_CSV, config: RebalanceConfig = RebalanceConfig()) -> dict[str, Any]:
    rows = _load_rows(baseline_csv)
    required = {"ticker", "trade_index", "entry_date", "exit_date", "entry_price", "entry_signal_score", "entry_signal_threshold", "exit_reason"}
    missing = sorted(required - set(rows[0])) if rows else sorted(required)
    axis = _daily_axis(rows)
    active_counts = [len({str(row.get("ticker") or "") for row in _active_rows(rows, day)}) for day in axis] if axis else []
    infeasible_days = sum(1 for count in active_counts if count > 0 and count * config.max_ticker_gross_share_pct < config.target_exposure_pct - config.cash_buffer_pct)
    candidates = _candidate_set()
    return {
        "gate": "capital_rebalance_100pct_probe_dry_plan",
        "baseline_csv": str(baseline_csv),
        "trade_count": len(rows),
        "ticker_count": len({str(row.get("ticker") or "") for row in rows}),
        "date_min": str(axis[0].date()) if axis else None,
        "date_max": str(axis[-1].date()) if axis else None,
        "trading_days": len(axis),
        "missing_required_fields": missing,
        "config": config.__dict__,
        "candidate_names": [candidate.name for candidate in candidates],
        "active_ticker_count_min": min(active_counts) if active_counts else 0,
        "active_ticker_count_max": max(active_counts) if active_counts else 0,
        "active_ticker_count_avg": sum(active_counts) / len(active_counts) if active_counts else 0.0,
        "concentration_infeasible_days": infeasible_days,
        "concentration_infeasible_day_pct": infeasible_days / len(active_counts) * 100.0 if active_counts else 0.0,
        "will_not_execute_heavy_backtest": True,
    }


def run_capital_rebalance_100pct_probe(
    *,
    baseline_csv: Path = BASELINE_CSV,
    out_dir: Path = OUT_DIR,
    config: RebalanceConfig = RebalanceConfig(),
    start_date: str = "2024-01-01",
    end_date: str = "2025-12-31",
    history_end_date: str = "2026-06-09",
    years: int = 3,
) -> dict[str, Any]:
    rows = _load_rows(baseline_csv)
    if not rows:
        raise ValueError(f"baseline trade log is empty: {baseline_csv}")
    required = {"ticker", "trade_index", "entry_date", "exit_date", "entry_price", "entry_signal_score", "entry_signal_threshold", "exit_reason"}
    missing = sorted(required - set(rows[0]))
    if missing:
        raise ValueError(f"missing required trade fields: {missing}")

    rulebooks = load_promoted_rulebooks()
    histories = load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)
    candidates = _candidate_set()
    baseline = candidates[0]
    baseline_metrics = simulate_rebalance_candidate(rows, histories, baseline, config)
    baseline_metrics["candidate"] = baseline.name
    baseline_metrics["implementation_eligible"] = baseline.implementation_eligible

    results: list[dict[str, Any]] = []
    for candidate in candidates:
        metrics = simulate_rebalance_candidate(rows, histories, candidate, config)
        loo = _leave_one_ticker_out(rows, histories, candidate, baseline, config) if candidate.implementation_eligible else {}
        eval_result = _evaluate_result(baseline_metrics, metrics, loo)
        results.append({
            "candidate": candidate.name,
            "description": candidate.description,
            "implementation_eligible": candidate.implementation_eligible,
            "metrics": metrics,
            **eval_result,
        })

    passed = [result for result in results if result["passed"]]
    summary = {
        "gate": "capital_rebalance_100pct_probe",
        "baseline_csv": str(baseline_csv),
        "out_dir": str(out_dir),
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "trade_count": len(rows),
        "ticker_count": len({str(row.get("ticker") or "") for row in rows}),
        "config": config.__dict__,
        "baseline_candidate": baseline.name,
        "baseline_metrics": baseline_metrics,
        "candidate_count": len(results),
        "eligible_candidate_count": sum(1 for result in results if result["implementation_eligible"]),
        "passed_candidate_count": len(passed),
        "passed_candidates": [result["candidate"] for result in passed],
        "implementation_recommended": len(passed) > 0,
        "passed": True,
        "results": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "probe_results.csv", _flat_results(results))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
