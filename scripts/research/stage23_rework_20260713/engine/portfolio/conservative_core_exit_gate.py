"""Persistent conservative_core exit gate and realistic research baseline.

This gate compares the B2-1 reference path (conservative gap-fill with legacy
same-bar activation) against the B2-2 candidate path (conservative_core with
T-1 activation for trailing/breakeven). It also writes the candidate metrics as
realistic_research_baseline.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from engine.portfolio import noop_gate as ng

OUT_DIR_CONSERVATIVE_CORE = Path("data/_system/research/central_portfolio/conservative_core_exit")
PATH_DEPENDENT_REASONS = {"trailing", "breakeven_stop"}


def _run_daily_loop_with_exit_mode(
    *,
    exit_execution_mode: str,
    rulebooks,
    histories,
    start_date: str,
    end_date: str,
    position_limit_krw: float,
    commission_rate: float,
    warmup: int,
):
    """Run the existing no-op daily loop with a scoped exit_execution_mode.

    run_legacy_compat_daily_loop intentionally keeps its public surface stable.
    The gate only needs to vary ExitExecutionConfig.mode, so it wraps the module
    global simulate_exit while the loop is running and restores it immediately.
    """
    original_simulate_exit = ng.simulate_exit

    def wrapped_simulate_exit(*args, **kwargs):
        kwargs["exit_execution_mode"] = exit_execution_mode
        return original_simulate_exit(*args, **kwargs)

    ng.simulate_exit = wrapped_simulate_exit
    try:
        return ng.run_legacy_compat_daily_loop(
            rulebooks,
            histories,
            start_date=start_date,
            end_date=end_date,
            position_limit_krw=position_limit_krw,
            commission_rate=commission_rate,
            warmup=warmup,
            sizing_mode="fractional",
            live_hard_stop_guard=False,
            entry_execution_mode="t_plus_1_open",
        )
    finally:
        ng.simulate_exit = original_simulate_exit


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _exit_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("exit_reason") or "") for row in rows))


def _realized_curve_max_drawdown_krw(rows: list[dict[str, Any]]) -> float:
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in sorted(
        rows,
        key=lambda r: (str(r.get("exit_date") or ""), str(r.get("ticker") or ""), _to_int(r.get("trade_index"))),
    ):
        cumulative += _to_float(row.get("pnl_krw"))
        peak = max(peak, cumulative)
        max_drawdown = min(max_drawdown, cumulative - peak)
    return float(max_drawdown)


def _baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trade_count = len(rows)
    gross_entry = sum(_to_float(row.get("entry_price")) * _to_float(row.get("entry_shares")) for row in rows)
    total_pnl = sum(_to_float(row.get("pnl_krw")) for row in rows)
    wins = [row for row in rows if _to_float(row.get("pnl_krw")) > 0]
    losses = [row for row in rows if _to_float(row.get("pnl_krw")) < 0]
    gross_profit = sum(_to_float(row.get("pnl_krw")) for row in wins)
    gross_loss = abs(sum(_to_float(row.get("pnl_krw")) for row in losses))
    avg_holding = sum(_to_float(row.get("holding_days")) for row in rows) / trade_count if trade_count else 0.0
    avg_pnl_pct = sum(_to_float(row.get("pnl_pct")) for row in rows) / trade_count if trade_count else 0.0
    realized_mdd_krw = _realized_curve_max_drawdown_krw(rows)

    return {
        "trade_count": trade_count,
        "ticker_count": len({str(row.get("ticker") or "") for row in rows}),
        "gross_entry_krw": float(gross_entry),
        "total_pnl_krw": float(total_pnl),
        "total_return_on_gross_entry_pct": (total_pnl / gross_entry * 100.0) if gross_entry > 0 else 0.0,
        "realized_curve_max_drawdown_krw": realized_mdd_krw,
        "realized_curve_max_drawdown_pct_of_gross_entry": (realized_mdd_krw / gross_entry * 100.0) if gross_entry > 0 else 0.0,
        "avg_trade_pnl_pct": float(avg_pnl_pct),
        "win_rate_pct": (len(wins) / trade_count * 100.0) if trade_count else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "avg_holding_days": float(avg_holding),
        "max_holding_days": max((_to_int(row.get("holding_days")) for row in rows), default=0),
        "exit_reason_counts": _exit_reason_counts(rows),
    }


def _field_mismatch_counts(mismatches: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("field") or "") for row in mismatches))


def _first_divergence_summary(
    rulebooks,
    ref_rows: list[dict[str, Any]],
    cand_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ref_by_ticker: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker, _ in rulebooks}
    cand_by_ticker: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker, _ in rulebooks}
    for row in ref_rows:
        ref_by_ticker.setdefault(str(row.get("ticker") or ""), []).append(row)
    for row in cand_rows:
        cand_by_ticker.setdefault(str(row.get("ticker") or ""), []).append(row)

    first_rows: list[dict[str, Any]] = []
    ref_reason_counts: Counter[str] = Counter()
    cand_reason_counts: Counter[str] = Counter()
    reason_pairs: Counter[str] = Counter()
    non_path_dependent: list[dict[str, Any]] = []

    for ticker, _ in rulebooks:
        refs = sorted(ref_by_ticker.get(ticker, []), key=lambda r: _to_int(r.get("trade_index")))
        cands = sorted(cand_by_ticker.get(ticker, []), key=lambda r: _to_int(r.get("trade_index")))
        for idx in range(max(len(refs), len(cands))):
            ref = refs[idx] if idx < len(refs) else None
            cand = cands[idx] if idx < len(cands) else None
            if ref == cand:
                continue
            ref_reason = str(ref.get("exit_reason") or "") if ref else ""
            cand_reason = str(cand.get("exit_reason") or "") if cand else ""
            fields: list[str] = []
            if ref is None or cand is None:
                fields = ["_row_presence"]
            else:
                for key in sorted(set(ref) | set(cand)):
                    if ref.get(key) != cand.get(key):
                        fields.append(str(key))
            item = {
                "ticker": ticker,
                "trade_index": idx,
                "ref_exit_reason": ref_reason,
                "candidate_exit_reason": cand_reason,
                "ref_exit_date": ref.get("exit_date") if ref else None,
                "candidate_exit_date": cand.get("exit_date") if cand else None,
                "changed_fields": fields[:20],
            }
            first_rows.append(item)
            ref_reason_counts[ref_reason] += 1
            cand_reason_counts[cand_reason] += 1
            reason_pairs[f"{ref_reason}->{cand_reason}"] += 1
            if ref_reason not in PATH_DEPENDENT_REASONS:
                non_path_dependent.append(item)
            break

    return {
        "first_divergence_count": len(first_rows),
        "first_ref_reason_counts": dict(ref_reason_counts),
        "first_candidate_reason_counts": dict(cand_reason_counts),
        "first_reason_pairs": dict(reason_pairs),
        "first_non_path_dependent_origin_count": len(non_path_dependent),
        "first_non_path_dependent_origin_samples": non_path_dependent[:12],
        "first_divergence_samples": first_rows[:12],
    }


def run_conservative_core_exit_gate(
    start_date: str,
    end_date: str,
    history_end_date: str,
    position_limit_krw: float = 30.0,
    commission_rate: float = 0.0005,
    warmup: int = 200,
    years: int = 3,
    out_dir: Path = OUT_DIR_CONSERVATIVE_CORE,
) -> dict[str, Any]:
    """Build the realistic research baseline and validate first divergence."""
    rulebooks = ng.load_promoted_rulebooks()
    histories = ng.load_fixed_histories(rulebooks, years=years, history_end_date=history_end_date)

    ref_trades_by_ticker = _run_daily_loop_with_exit_mode(
        exit_execution_mode="conservative_gap_fill",
        rulebooks=rulebooks,
        histories=histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
    )
    cand_trades_by_ticker = _run_daily_loop_with_exit_mode(
        exit_execution_mode="conservative_core",
        rulebooks=rulebooks,
        histories=histories,
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=position_limit_krw,
        commission_rate=commission_rate,
        warmup=warmup,
    )

    ref_rows = ng.normalize_trade_map(rulebooks, ref_trades_by_ticker)
    cand_rows = ng.normalize_trade_map(rulebooks, cand_trades_by_ticker)
    mismatches = ng.compare_trade_rows(ref_rows, cand_rows)
    first_divergence = _first_divergence_summary(rulebooks, ref_rows, cand_rows)
    candidate_baseline_metrics = _baseline_metrics(cand_rows)
    reference_metrics = _baseline_metrics(ref_rows)

    invariant_passed = (
        len(cand_rows) > 0
        and first_divergence["first_divergence_count"] > 0
        and first_divergence["first_non_path_dependent_origin_count"] == 0
    )
    summary = {
        "gate": "conservative_core_exit_gate",
        "reference_mode": "fractional_t_plus_1_open_conservative_gap_fill",
        "candidate_mode": "realistic_research_baseline_fractional_t_plus_1_open_conservative_core",
        "realistic_research_baseline": True,
        "fractional_shares": True,
        "disable_add_buy": True,
        "live_hard_stop_guard": False,
        "entry_execution_mode": "t_plus_1_open",
        "reference_exit_execution_mode": "conservative_gap_fill",
        "candidate_exit_execution_mode": "conservative_core",
        "start_date": start_date,
        "end_date": end_date,
        "history_end_date": history_end_date,
        "position_limit_krw": position_limit_krw,
        "tickers": [ticker for ticker, _ in rulebooks],
        "ref_trade_count": len(ref_rows),
        "candidate_trade_count": len(cand_rows),
        "mismatch_count": len(mismatches),
        "field_mismatch_counts": _field_mismatch_counts(mismatches),
        "invariant_first_divergence_only_path_dependent": first_divergence["first_non_path_dependent_origin_count"] == 0,
        "passed": invariant_passed,
        "reference_metrics": reference_metrics,
        "candidate_baseline_metrics": candidate_baseline_metrics,
        **first_divergence,
    }
    ng.write_gate_outputs(ref_rows, cand_rows, mismatches, summary, out_dir)
    return summary
