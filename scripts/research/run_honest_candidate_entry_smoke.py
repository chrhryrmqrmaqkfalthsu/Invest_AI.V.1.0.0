"""Honest candidate-entry smoke for LR8D-style universe expansion.

Purpose:
    Verify a clean entry point that uses the historical 85-ticker universe only
    as a compute-saving ticker filter. It must not use stock_score, rolling OOS
    pass records, promoted rulebooks, or candidate OOS metrics for selection.

Scope:
    Smoke only: EME / MCK / MELI.
    Full-training is run on train data only, then rulebooks are selected by
    train-internal stability rather than highest member_score.

This script intentionally writes only to a separated research directory.
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.learning.backtest import run_backtest
from engine.learning.genetic import GAConfig
from engine.pipeline.context import prepare_ticker_context
from engine.pipeline.full_training import (
    member_score_distribution,
    run_full_training,
    save_full_training_artifacts,
)
from engine.strategies.rulebook import Rulebook

RUN_ID = "honest_candidate_entry_smoke_20260610"
OUT_DIR = Path("data/_system/research") / RUN_ID
SUMMARY_PATH = OUT_DIR / "honest_candidate_entry_smoke_summary.json"
SELECTED_PATH = OUT_DIR / "honest_candidate_entry_smoke_selected_rulebooks.jsonl"

CANDIDATE_FILTER_SOURCE = Path("data/_system/research/lr8d_abcd_20260608/lr8d_abcd_topn.jsonl")
SMOKE_TICKERS = ("EME", "MCK", "MELI")
TRAIN_END = "2023-12-31"
FITNESS_MODE = "swing"
MAX_MEMBERS = 10
GA_CONFIG = GAConfig(
    population=10,
    generations=5,
    elite_ratio=0.2,
    mutation_rate=0.15,
    mutation_strength=0.2,
    tournament_size=3,
    seed_pattern_ratio=0.33,
    early_stop_no_improve=5,
    random_seed=20260610,
)

# Train-internal stability windows only. No OOS/future windows are allowed.
STABILITY_WINDOWS = (
    ("2021", "2021-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
)

SELECTION_RULE_ID = "train_internal_stability_v0"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def load_candidate_filter_tickers(path: Path = CANDIDATE_FILTER_SOURCE) -> list[str]:
    """Load only ticker symbols from the 85 universe source.

    The source rows contain OOS metrics, but this function intentionally reads
    only the top-level ticker key and ignores every score/pass/metric field.
    """
    tickers: set[str] = set()
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            ticker = str(row.get("ticker") or "").upper().strip()
            if ticker:
                tickers.add(ticker)
    return sorted(tickers)


def rulebook_from_member(member: Mapping[str, Any]) -> Rulebook:
    rb = member.get("rulebook")
    if not isinstance(rb, Mapping):
        raise ValueError(f"member has no rulebook dict: rank={member.get('rank')}")
    return Rulebook.from_dict(dict(rb))


def result_metrics(result: Any) -> dict[str, Any]:
    return {
        "trade_count": safe_int(getattr(result, "trade_count", 0)),
        "win_rate": safe_float(getattr(result, "win_rate", 0.0)),
        "expectancy_pct": safe_float(getattr(result, "expectancy_pct", 0.0)),
        "profit_factor": safe_float(getattr(result, "profit_factor", 0.0)),
        "max_drawdown_pct": safe_float(getattr(result, "max_drawdown_pct", 0.0)),
        "fitness": safe_float(getattr(result, "fitness", 0.0)),
    }


def train_start_for_context(ctx: Mapping[str, Any]) -> str:
    return str(ctx.get("data_start") or ctx.get("data_min") or "2020-01-01")


def evaluate_member_stability(member: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, Any]:
    rb = rulebook_from_member(member)
    df = ctx["df"]
    kwargs = dict(
        position_limit_krw=120_000.0,
        market_history_df=ctx.get("market_history_df"),
        sector_name=ctx.get("sector_name", "tech"),
        ticker_sentiment=ctx.get("ticker_sentiment"),
        fitness_mode=FITNESS_MODE,
    )
    windows: list[dict[str, Any]] = []
    positive_expectancy_count = 0
    positive_pf_count = 0
    total_trades = 0
    expectancies: list[float] = []
    pfs: list[float] = []
    drawdowns: list[float] = []

    for label, start, end in STABILITY_WINDOWS:
        result = run_backtest(rb, df, start_date=start, end_date=end, **kwargs)
        metrics = result_metrics(result)
        metrics["label"] = label
        metrics["start_date"] = start
        metrics["end_date"] = end
        windows.append(metrics)
        total_trades += safe_int(metrics.get("trade_count"))
        exp = safe_float(metrics.get("expectancy_pct"))
        pf = safe_float(metrics.get("profit_factor"))
        dd = safe_float(metrics.get("max_drawdown_pct"))
        expectancies.append(exp)
        pfs.append(pf)
        drawdowns.append(dd)
        if safe_int(metrics.get("trade_count")) > 0 and exp > 0:
            positive_expectancy_count += 1
        if safe_int(metrics.get("trade_count")) > 0 and pf > 1.0:
            positive_pf_count += 1

    min_expectancy = min(expectancies) if expectancies else 0.0
    min_profit_factor = min(pfs) if pfs else 0.0
    worst_drawdown_pct = min(drawdowns) if drawdowns else 0.0
    avg_expectancy = sum(expectancies) / len(expectancies) if expectancies else 0.0
    avg_pf = sum(pfs) / len(pfs) if pfs else 0.0
    return {
        "rank": member.get("rank"),
        "member_hash": member.get("member_hash"),
        "rulebook_hash": member.get("rulebook_hash"),
        "member_score_present_but_not_used": member.get("member_score"),
        "fitness": member.get("fitness"),
        "train_metrics_full": member.get("train_metrics") or {},
        "windows": windows,
        "positive_expectancy_window_count": positive_expectancy_count,
        "positive_pf_window_count": positive_pf_count,
        "total_window_trades": total_trades,
        "min_expectancy_pct": round(min_expectancy, 6),
        "avg_expectancy_pct": round(avg_expectancy, 6),
        "min_profit_factor": round(min_profit_factor, 6),
        "avg_profit_factor": round(avg_pf, 6),
        "worst_drawdown_pct": round(worst_drawdown_pct, 6),
    }


def stability_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    full = row.get("train_metrics_full") or {}
    return (
        safe_int(row.get("positive_expectancy_window_count")),
        safe_int(row.get("positive_pf_window_count")),
        safe_int(row.get("total_window_trades")),
        safe_float(row.get("min_expectancy_pct")),
        safe_float(row.get("min_profit_factor")),
        safe_float(row.get("worst_drawdown_pct")),
        safe_float(full.get("profit_factor")),
        safe_float(full.get("expectancy_pct")),
        -safe_int(row.get("rank"), 999999),
    )


def select_stable_member(members: list[dict[str, Any]], ctx: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    stability_rows = [evaluate_member_stability(member, ctx) for member in members]
    if not stability_rows:
        return None, []
    ordered = sorted(stability_rows, key=stability_sort_key, reverse=True)
    selected = dict(ordered[0])
    selected["selection_rule_id"] = SELECTION_RULE_ID
    selected["selection_note"] = "selected_by_train_internal_stability_not_member_score"
    return selected, ordered


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def run_ticker(ticker: str, candidate_filter: set[str]) -> dict[str, Any]:
    started = time.time()
    if ticker not in candidate_filter:
        return {
            "ticker": ticker,
            "status": "SKIPPED_NOT_IN_CANDIDATE_FILTER",
            "elapsed_sec": time.time() - started,
        }
    ctx = prepare_ticker_context(ticker)
    train_start = train_start_for_context(ctx)
    ticker_run_id = f"{RUN_ID}_{ticker}"
    result = run_full_training(
        ticker,
        context=ctx,
        ga_config=GA_CONFIG,
        fitness_mode=FITNESS_MODE,
        train_start=train_start,
        train_end=TRAIN_END,
        max_members=MAX_MEMBERS,
        run_id=ticker_run_id,
    )
    ticker_dir = OUT_DIR / ticker
    paths = save_full_training_artifacts(result, ticker_dir)
    members = list(result.get("members", []) or [])
    selected, stability_ranked = select_stable_member(members, ctx)
    if selected:
        selected_path = ticker_dir / "selected_rulebook_train_stability.json"
        selected_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        paths["selected_rulebook_train_stability"] = str(selected_path)
    stability_path = ticker_dir / "train_internal_stability_ranked.jsonl"
    write_jsonl(stability_path, stability_ranked)
    paths["train_internal_stability_ranked"] = str(stability_path)
    return {
        "ticker": ticker,
        "status": "DONE",
        "candidate_filter_only": True,
        "candidate_filter_source": str(CANDIDATE_FILTER_SOURCE),
        "train_period": [train_start, TRAIN_END],
        "data_start": result.get("data_start"),
        "data_end": result.get("data_end"),
        "ga_config": result.get("ga_config"),
        "ga": result.get("ga"),
        "member_count": result.get("member_count"),
        "qualified_count": result.get("qualified_count"),
        "member_score_distribution": member_score_distribution(members),
        "selection_rule_id": SELECTION_RULE_ID,
        "selected_member": selected,
        "outputs": paths,
        "elapsed_sec": time.time() - started,
    }


def build_summary(rows: list[dict[str, Any]], candidate_filter: list[str]) -> dict[str, Any]:
    status_counts = Counter(row.get("status", "UNKNOWN") for row in rows)
    selected_count = sum(1 for row in rows if row.get("selected_member"))
    member_score_used_for_selection = False
    # Guard: selected rows explicitly carry member_score only as diagnostic.
    for row in rows:
        selected = row.get("selected_member") or {}
        if selected.get("selection_note") != "selected_by_train_internal_stability_not_member_score" and selected:
            member_score_used_for_selection = True
    return {
        "run_id": RUN_ID,
        "purpose": "honest_candidate_entry_smoke_train_only_no_stock_score_no_oos_selection",
        "candidate_filter": {
            "source": str(CANDIDATE_FILTER_SOURCE),
            "ticker_count": len(candidate_filter),
            "tickers_used_for_smoke": list(SMOKE_TICKERS),
            "ticker_only_read": True,
            "scores_oos_pass_records_read": False,
            "note": "85-universe source is used only as a compute-saving ticker filter.",
        },
        "forbidden_inputs": {
            "stock_score_gate_used": False,
            "stock_score_cutoff_used": False,
            "rolling_oos_score_used": False,
            "promoted_rulebook_used": False,
            "parameters_json_rulebook_used": False,
        },
        "training": {
            "engine": "engine.pipeline.full_training.run_full_training",
            "train_end": TRAIN_END,
            "fitness_mode": FITNESS_MODE,
            "ga_population": GA_CONFIG.population,
            "ga_generations": GA_CONFIG.generations,
            "max_members": MAX_MEMBERS,
        },
        "selection": {
            "rule_id": SELECTION_RULE_ID,
            "uses_member_score": member_score_used_for_selection,
            "uses_train_internal_windows_only": True,
            "stability_windows": [list(x) for x in STABILITY_WINDOWS],
            "sort_order": [
                "positive_expectancy_window_count desc",
                "positive_pf_window_count desc",
                "total_window_trades desc",
                "min_expectancy_pct desc",
                "min_profit_factor desc",
                "worst_drawdown_pct desc",
                "full_train_profit_factor desc",
                "full_train_expectancy_pct desc",
                "rank asc",
            ],
        },
        "status_counts": dict(status_counts),
        "selected_count": selected_count,
        "rows": rows,
        "passed_smoke": status_counts.get("DONE", 0) == len(SMOKE_TICKERS) and selected_count == len(SMOKE_TICKERS) and not member_score_used_for_selection,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidate_filter = load_candidate_filter_tickers()
    candidate_set = set(candidate_filter)
    rows = [run_ticker(ticker, candidate_set) for ticker in SMOKE_TICKERS]
    selected_rows = [dict(row.get("selected_member") or {}, ticker=row.get("ticker")) for row in rows if row.get("selected_member")]
    write_jsonl(SELECTED_PATH, selected_rows)
    summary = build_summary(rows, candidate_filter)
    summary["outputs"] = {
        "summary": str(SUMMARY_PATH),
        "selected_rulebooks": str(SELECTED_PATH),
        "out_dir": str(OUT_DIR),
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0 if summary.get("passed_smoke") else 1


if __name__ == "__main__":
    raise SystemExit(main())
