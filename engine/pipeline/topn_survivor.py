"""Top-N rolling OOS survivor analysis helpers.

LR-8C/LR-8D keeps this module analysis-only. It does not write promote artifacts
and it is not called by live trading paths.

LR-8D-A change:
- survivor grouping is ticker-level, not exact rulebook_hash-level.
- GA creates new rulebook hashes per year, so exact-hash consistency is
  structurally incompatible with the training process.
"""
from __future__ import annotations

from statistics import mean
from typing import Any, Iterable, Mapping

from engine.pipeline.scoring import (
    MEMBER_SCORE_DRAWDOWN_WEIGHT,
    MEMBER_SCORE_EXPECTANCY_WEIGHT,
    MEMBER_SCORE_PROFIT_FACTOR_WEIGHT,
    OOS_MIN_TRADES,
)

GENERAL_YEARS = (2022, 2023, 2024)
STRESS_LABELS = ("2025H2",)
DEFAULT_MEMBER_SCORE_THRESHOLDS = (0.0, 5.0, 10.0)
DEFAULT_SURVIVOR_K_VALUES = (2, 3)
DEFAULT_MIN_EXPECTANCY_PCT = 1.0
DEFAULT_MIN_STRESS_EXPECTANCY_PCT = 0.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _percentile_ranks(values: list[float], higher_is_better: bool = True) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]

    indexed = list(enumerate(values))
    indexed.sort(key=lambda x: x[1], reverse=not higher_is_better)
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (n - 1)
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = pct
        i = j + 1
    return ranks


def _period_label(period: Mapping[str, Any]) -> str:
    for key in ("label", "period_label", "year"):
        value = period.get(key)
        if value is not None:
            return str(value)
    return ""


def _period_year(period: Mapping[str, Any]) -> int | None:
    raw = period.get("year")
    try:
        return int(raw)
    except Exception:
        return None


def _oos_metrics(candidate: Mapping[str, Any]) -> dict[str, float | int]:
    oos = candidate.get("oos") if isinstance(candidate.get("oos"), Mapping) else {}
    data = dict(candidate)
    data.update(oos)
    return {
        "trade_count": _safe_int(data.get("trade_count"), 0),
        "win_rate": _safe_float(data.get("win_rate"), 0.0),
        "expectancy_pct": _safe_float(data.get("expectancy_pct"), 0.0),
        "profit_factor": _safe_float(data.get("profit_factor"), 0.0),
        "max_drawdown_pct": _safe_float(data.get("max_drawdown_pct"), 0.0),
    }


def _score_period_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Score candidates inside one period.

    LR-8D-A removes win-rate from member scoring. Win-rate stays as a diagnostic
    metric, but selection is expectancy / profit-factor / drawdown oriented.
    """
    rows = [dict(c) for c in candidates or []]
    if not rows:
        return []

    metrics = [_oos_metrics(row) for row in rows]
    exp_rank = _percentile_ranks([float(m["expectancy_pct"]) for m in metrics], higher_is_better=True)
    pf_rank = _percentile_ranks([min(float(m["profit_factor"]), 4.0) for m in metrics], higher_is_better=True)
    dd_rank = _percentile_ranks([-abs(float(m["max_drawdown_pct"])) for m in metrics], higher_is_better=True)

    w_exp = float(MEMBER_SCORE_EXPECTANCY_WEIGHT)
    w_pf = float(MEMBER_SCORE_PROFIT_FACTOR_WEIGHT)
    w_dd = float(MEMBER_SCORE_DRAWDOWN_WEIGHT)
    total_w = max(w_exp + w_pf + w_dd, 1e-9)

    scored: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        score_norm = (exp_rank[idx] * w_exp + pf_rank[idx] * w_pf + dd_rank[idx] * w_dd) / total_w
        score_points = round(max(0.0, min(1.0, float(score_norm))) * 100.0, 6)
        out = dict(row)
        out["oos_metrics"] = metrics[idx]
        out["oos_member_score"] = score_points
        out["oos_member_score_components"] = {
            "expectancy_percentile": round(exp_rank[idx], 6),
            "profit_factor_percentile_capped_4": round(pf_rank[idx], 6),
            "drawdown_percentile": round(dd_rank[idx], 6),
            "win_rate_percentile": None,
            "selection_policy": "expectancy_pf_drawdown_no_winrate",
        }
        scored.append(out)
    return scored


def score_topn_validation_periods(
    top_n_validation: Mapping[str, Any],
    *,
    general_years: Iterable[int] = GENERAL_YEARS,
    stress_labels: Iterable[str] = STRESS_LABELS,
) -> dict[str, list[dict[str, Any]]]:
    """Score Top-N OOS candidates and separate general vs stress periods."""
    general_year_set = {int(y) for y in general_years}
    stress_label_set = {str(x) for x in stress_labels}
    general: list[dict[str, Any]] = []
    stress: list[dict[str, Any]] = []

    for period in list(top_n_validation.get("periods", []) or []):
        if not isinstance(period, Mapping):
            continue
        label = _period_label(period)
        year = _period_year(period)
        scored_candidates = _score_period_candidates(period.get("candidates", []) or [])
        row = dict(period)
        row["label"] = label
        row["candidates"] = scored_candidates
        if label in stress_label_set:
            stress.append(row)
        elif year in general_year_set:
            general.append(row)
    return {"general_periods": general, "stress_periods": stress}


def _candidate_group_rows(periods: Iterable[Mapping[str, Any]], *, group_by: str = "ticker") -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for period in periods or []:
        label = _period_label(period)
        year = _period_year(period)
        period_ticker = str(period.get("ticker") or "")
        for candidate in list(period.get("candidates", []) or []):
            if not isinstance(candidate, Mapping):
                continue
            if group_by == "rulebook_hash":
                key = str(candidate.get("rulebook_hash") or "")
            else:
                key = str(candidate.get("ticker") or period_ticker or "")
            if not key:
                continue
            row = dict(candidate)
            row["ticker"] = str(candidate.get("ticker") or period_ticker or key)
            row["period_label"] = label
            row["period_year"] = year
            grouped.setdefault(key, []).append(row)
    return grouped


def _is_candidate_eligible(row: Mapping[str, Any], *, min_trades: int, min_member_score: float) -> bool:
    metrics = row.get("oos_metrics", {}) if isinstance(row.get("oos_metrics"), Mapping) else {}
    return (
        _safe_int(metrics.get("trade_count"), 0) >= int(min_trades)
        and _safe_float(row.get("oos_member_score"), 0.0) >= float(min_member_score)
    )


def _best_by_expectancy(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(
        rows,
        key=lambda row: (
            _safe_float(row.get("oos_metrics", {}).get("expectancy_pct"), 0.0),
            _safe_float(row.get("oos_metrics", {}).get("profit_factor"), 0.0),
            _safe_float(row.get("oos_member_score"), 0.0),
            -_safe_int(row.get("rank_is"), 9999),
        ),
    )


def _row_expectancy(row: Mapping[str, Any]) -> float:
    return _safe_float((row.get("oos_metrics") or {}).get("expectancy_pct"), 0.0)


def _row_profit_factor(row: Mapping[str, Any]) -> float:
    return _safe_float((row.get("oos_metrics") or {}).get("profit_factor"), 0.0)


def _row_drawdown(row: Mapping[str, Any]) -> float:
    return _safe_float((row.get("oos_metrics") or {}).get("max_drawdown_pct"), 0.0)


def _row_win_rate(row: Mapping[str, Any]) -> float:
    return _safe_float((row.get("oos_metrics") or {}).get("win_rate"), 0.0)


def _row_trades(row: Mapping[str, Any]) -> int:
    return _safe_int((row.get("oos_metrics") or {}).get("trade_count"), 0)


def evaluate_survivors(
    scored_periods: Mapping[str, list[dict[str, Any]]],
    *,
    survivor_k: int,
    min_trades: int,
    min_member_score: float,
    min_expectancy_pct: float = DEFAULT_MIN_EXPECTANCY_PCT,
    min_stress_expectancy_pct: float = DEFAULT_MIN_STRESS_EXPECTANCY_PCT,
) -> list[dict[str, Any]]:
    """Return ticker-level survivors.

    A ticker survives when it repeatedly produces positive-expectancy OOS
    candidates across general years. The selected executable rulebook is the
    best stress-period rulebook when stress OOS is positive; otherwise the best
    general-period rulebook is used.
    """
    general_groups = _candidate_group_rows(scored_periods.get("general_periods", []), group_by="ticker")
    stress_groups = _candidate_group_rows(scored_periods.get("stress_periods", []), group_by="ticker")
    survivors: list[dict[str, Any]] = []

    for ticker, rows in general_groups.items():
        eligible = [
            row
            for row in rows
            if _is_candidate_eligible(row, min_trades=min_trades, min_member_score=min_member_score)
            and _row_expectancy(row) >= float(min_expectancy_pct)
        ]
        # For each year, keep only the strongest expectancy candidate.
        best_by_year: dict[int, dict[str, Any]] = {}
        for row in eligible:
            year = row.get("period_year")
            if year is None:
                continue
            y = int(year)
            current = best_by_year.get(y)
            if current is None or _row_expectancy(row) > _row_expectancy(current):
                best_by_year[y] = row
        eligible_years = sorted(best_by_year)
        if len(eligible_years) < int(survivor_k):
            continue

        year_rows = [best_by_year[y] for y in eligible_years]
        exps = [_row_expectancy(row) for row in year_rows]
        pfs = [_row_profit_factor(row) for row in year_rows]
        wrs = [_row_win_rate(row) for row in year_rows]
        mdds = [_row_drawdown(row) for row in year_rows]
        scores = [_safe_float(row.get("oos_member_score"), 0.0) for row in year_rows]
        trades = [_row_trades(row) for row in year_rows]
        ranks = [_safe_int(row.get("rank_is"), 0) for row in year_rows if row.get("rank_is") is not None]

        stress_rows_all = stress_groups.get(ticker, [])
        stress_rows = [
            row
            for row in stress_rows_all
            if _is_candidate_eligible(row, min_trades=min_trades, min_member_score=min_member_score)
        ]
        stress_positive = [row for row in stress_rows if _row_expectancy(row) >= float(min_stress_expectancy_pct)]
        stress_exps = [_row_expectancy(row) for row in stress_rows]
        stress_scores = [_safe_float(row.get("oos_member_score"), 0.0) for row in stress_rows]

        if stress_rows_all and not stress_positive:
            continue

        selected = _best_by_expectancy(stress_positive) or _best_by_expectancy(year_rows)
        if selected is None:
            continue

        selected_hash = str(selected.get("rulebook_hash") or "")
        selected_label = str(selected.get("period_label") or "")
        selected_year = selected.get("period_year")
        selected_metrics = selected.get("oos_metrics", {}) if isinstance(selected.get("oos_metrics"), Mapping) else {}

        survivors.append(
            {
                "ticker": ticker,
                "rulebook_hash": selected_hash,
                "selected_rulebook_hash": selected_hash,
                "selected_rulebook_source_label": selected_label,
                "selected_rulebook_source_year": selected_year,
                "selected_rulebook_expectancy_pct": round(_safe_float(selected_metrics.get("expectancy_pct"), 0.0), 6),
                "selected_rulebook_profit_factor": round(_safe_float(selected_metrics.get("profit_factor"), 0.0), 6),
                "selected_rulebook_win_rate": round(_safe_float(selected_metrics.get("win_rate"), 0.0), 6),
                "selected_rulebook_trade_count": _safe_int(selected_metrics.get("trade_count"), 0),
                "appearance_count": len({int(row["period_year"]) for row in rows if row.get("period_year") is not None}),
                "eligible_year_count": len(eligible_years),
                "eligible_years": eligible_years,
                "avg_rank_is": round(mean(ranks), 6) if ranks else 0.0,
                "avg_expectancy_pct": round(mean(exps), 6) if exps else 0.0,
                "min_expectancy_pct": round(min(exps), 6) if exps else 0.0,
                "avg_profit_factor": round(mean(pfs), 6) if pfs else 0.0,
                "avg_win_rate": round(mean(wrs), 6) if wrs else 0.0,
                "worst_drawdown_pct": round(min(mdds), 6) if mdds else 0.0,
                "worst_year_member_score": round(min(scores), 6) if scores else 0.0,
                "avg_member_score": round(mean(scores), 6) if scores else 0.0,
                "min_trades": min(trades) if trades else 0,
                "avg_trades": round(mean(trades), 6) if trades else 0.0,
                "stress_appearance_count": len(stress_rows),
                "stress_avg_expectancy_pct": round(mean(stress_exps), 6) if stress_exps else None,
                "stress_worst_expectancy_pct": round(min(stress_exps), 6) if stress_exps else None,
                "stress_avg_member_score": round(mean(stress_scores), 6) if stress_scores else None,
                "stress_worst_member_score": round(min(stress_scores), 6) if stress_scores else None,
                "thresholds": {
                    "survivor_k": int(survivor_k),
                    "min_trades": int(min_trades),
                    "min_member_score": float(min_member_score),
                    "min_expectancy_pct": float(min_expectancy_pct),
                    "min_stress_expectancy_pct": float(min_stress_expectancy_pct),
                    "group_by": "ticker",
                },
            }
        )

    survivors.sort(
        key=lambda row: (
            int(row["eligible_year_count"]),
            float(row["min_expectancy_pct"]),
            float(row["avg_expectancy_pct"]),
            float(row.get("stress_avg_expectancy_pct") or -999.0),
            float(row["avg_profit_factor"]),
            -float(row["avg_rank_is"]),
            str(row["ticker"]),
        ),
        reverse=True,
    )
    return survivors


def sweep_survivor_thresholds(
    top_n_validation: Mapping[str, Any],
    *,
    min_trades_values: Iterable[int] | None = None,
    member_score_thresholds: Iterable[float] = DEFAULT_MEMBER_SCORE_THRESHOLDS,
    survivor_k_values: Iterable[int] = DEFAULT_SURVIVOR_K_VALUES,
    general_years: Iterable[int] = GENERAL_YEARS,
    stress_labels: Iterable[str] = STRESS_LABELS,
) -> dict[str, Any]:
    """Sweep survivor thresholds over scored Top-N OOS periods."""
    scored = score_topn_validation_periods(
        top_n_validation,
        general_years=general_years,
        stress_labels=stress_labels,
    )
    if min_trades_values is None:
        min_trades_values = (OOS_MIN_TRADES, max(0, OOS_MIN_TRADES - 1), max(0, OOS_MIN_TRADES - 2))

    sweep_rows: list[dict[str, Any]] = []
    for min_trades in min_trades_values:
        for min_score in member_score_thresholds:
            for survivor_k in survivor_k_values:
                survivors = evaluate_survivors(
                    scored,
                    survivor_k=int(survivor_k),
                    min_trades=int(min_trades),
                    min_member_score=float(min_score),
                )
                worst_exp = [float(row["min_expectancy_pct"]) for row in survivors]
                stress_exp = [
                    float(row["stress_avg_expectancy_pct"])
                    for row in survivors
                    if row.get("stress_avg_expectancy_pct") is not None
                ]
                sweep_rows.append(
                    {
                        "min_trades": int(min_trades),
                        "min_member_score": float(min_score),
                        "survivor_k": int(survivor_k),
                        "survivor_count": len(survivors),
                        "avg_min_expectancy_pct": round(mean(worst_exp), 6) if worst_exp else 0.0,
                        "avg_stress_expectancy_pct": round(mean(stress_exp), 6) if stress_exp else None,
                    }
                )
    return {"scored_periods": scored, "sweep": sweep_rows}


def recommend_sweep_configs(sweep_rows: Iterable[Mapping[str, Any]], limit: int = 2) -> list[dict[str, Any]]:
    """Pick balanced non-empty sweep rows for review, not automatic promotion."""
    rows = [dict(row) for row in sweep_rows or [] if _safe_int(row.get("survivor_count"), 0) > 0]
    if not rows:
        return []
    positive_counts = sorted({_safe_int(row.get("survivor_count"), 0) for row in rows})
    target = positive_counts[len(positive_counts) // 2]
    rows.sort(
        key=lambda row: (
            abs(_safe_int(row.get("survivor_count"), 0) - target),
            -_safe_float(row.get("avg_min_expectancy_pct"), 0.0),
            -_safe_int(row.get("survivor_k"), 0),
            -_safe_float(row.get("min_member_score"), 0.0),
            -_safe_int(row.get("min_trades"), 0),
        )
    )
    return rows[: max(0, int(limit))]
