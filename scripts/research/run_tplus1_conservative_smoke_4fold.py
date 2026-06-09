"""LR8D T+1/conservative_core four-fold smoke runner.

Purpose:
    Extend the Phase-2a single-fold smoke to EME/MCK/MELI across the LR8D
    2022/2023/2024/2025H2 folds using real ticker data.

This is a wiring / fold-boundary / stress-tagging / artifact smoke only. It is
not a performance evaluation and does not change production outputs.
"""
from __future__ import annotations

import copy
import fcntl
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core.metadata import compute_rulebook_hash
from engine.learning.execution_mode_backtest import run_backtest_execution_mode
from engine.learning.genetic import GAConfig, collect_top_rulebooks, run_ga
from engine.pipeline.context import prepare_ticker_context
from scripts.research.rulebook_persist import collect_rulebook_rows
from scripts.research.trade_persist import collect_trade_rows

RUN_ID = "lr8d_tplus1_conservative_smoke_4fold_20260610"
OUT_DIR = Path("data/_system/research") / RUN_ID
TOPN_PATH = OUT_DIR / "lr8d_tplus1_conservative_smoke_4fold_topn.jsonl"
RULEBOOKS_PATH = OUT_DIR / "lr8d_tplus1_conservative_smoke_4fold_topn_rulebooks.jsonl"
TRADES_PATH = OUT_DIR / "lr8d_tplus1_conservative_smoke_4fold_trades.jsonl"
SUMMARY_PATH = OUT_DIR / "lr8d_tplus1_conservative_smoke_4fold_summary.json"

TICKERS = ("EME", "MCK", "MELI")
STRESS_LABEL = "2025H2"
STRESS_RUN_KEY_SUFFIX = "2025H2_STRESS"
POPULATION = 10
GENERATIONS = 5
CANDIDATE_COLLECT_N = 5
POSITION_LIMIT_KRW = 120_000.0
COMMISSION_RATE = 0.0005
WARMUP = 200
FITNESS_MODE = "swing"
ENTRY_EXECUTION_MODE = "t_plus_1_open"
EXIT_EXECUTION_MODE = "conservative_core"
FOLD_EXIT_POLICY = "fold_end_mark_to_market"

SMOKE_SPLITS = (
    {
        "label": "2022",
        "run_key_label": "2022",
        "year": 2022,
        "train_start": None,
        "train_end": "2021-12-31",
        "test_start": "2022-01-01",
        "test_end": "2022-12-31",
        "is_stress": False,
    },
    {
        "label": "2023",
        "run_key_label": "2023",
        "year": 2023,
        "train_start": None,
        "train_end": "2022-12-31",
        "test_start": "2023-01-01",
        "test_end": "2023-12-31",
        "is_stress": False,
    },
    {
        "label": "2024",
        "run_key_label": "2024",
        "year": 2024,
        "train_start": None,
        "train_end": "2023-12-31",
        "test_start": "2024-01-01",
        "test_end": "2024-12-31",
        "is_stress": False,
    },
    {
        "label": STRESS_LABEL,
        "run_key_label": STRESS_RUN_KEY_SUFFIX,
        "year": STRESS_LABEL,
        "train_start": None,
        "train_end": "2025-05-31",
        "test_start": "2025-06-01",
        "test_end": None,  # filled from ticker context data_end
        "is_stress": True,
    },
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            lines = f.read().splitlines()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    for line in lines:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def completed_keys(path: Path) -> set[str]:
    return {str(row.get("run_key")) for row in read_jsonl(path) if row.get("run_key")}


def float0(value: Any) -> float:
    try:
        number = float(value or 0.0)
        if not math.isfinite(number):
            return 0.0
        return number
    except Exception:
        return 0.0


def finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def run_key_for(ticker: str, split: Mapping[str, Any]) -> str:
    return f"{ticker}|{split['run_key_label']}"


def expected_run_keys() -> list[str]:
    return [run_key_for(ticker, split) for ticker in TICKERS for split in SMOKE_SPLITS]


def make_ga_config(ticker: str, split: Mapping[str, Any]) -> GAConfig:
    seed = 20260610 + sum(ord(ch) for ch in f"{ticker}|{split['run_key_label']}")
    return GAConfig(
        population=POPULATION,
        generations=GENERATIONS,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=GENERATIONS,
        random_seed=seed,
    )


def base_kwargs(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_limit_krw": POSITION_LIMIT_KRW,
        "commission_rate": COMMISSION_RATE,
        "warmup": WARMUP,
        "market_history_df": ctx["market_history_df"],
        "sector_name": ctx["sector_name"],
        "ticker_sentiment": ctx["ticker_sentiment"],
        "fitness_mode": FITNESS_MODE,
        "use_llm_events": False,
        "entry_execution_mode": ENTRY_EXECUTION_MODE,
        "exit_execution_mode": EXIT_EXECUTION_MODE,
        "fold_exit_policy": FOLD_EXIT_POLICY,
    }


def result_metrics(result: Any) -> dict[str, Any]:
    return {
        "trade_count": int(getattr(result, "trade_count", 0) or 0),
        "win_rate": float0(getattr(result, "win_rate", 0.0)),
        "expectancy_pct": float0(getattr(result, "expectancy_pct", 0.0)),
        "profit_factor": float0(getattr(result, "profit_factor", 0.0)),
        "max_drawdown_pct": float0(getattr(result, "max_drawdown_pct", 0.0)),
        "fitness": float0(getattr(result, "fitness", 0.0)),
    }


def candidate_row(ticker: str, split: Mapping[str, Any], rank_is: int, rb: Any, result: Any) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "year": split["year"],
        "label": split["label"],
        "fold_label": split["label"],
        "run_key_label": split["run_key_label"],
        "is_stress": bool(split.get("is_stress")),
        "rank_is": int(rank_is),
        "rulebook_hash": compute_rulebook_hash(rb),
        "train_fitness": float0(getattr(rb, "fitness", 0.0)),
        "train_period": [split["train_start"], split["train_end"]],
        "test_period": [split["test_start"], split["test_end"]],
        "fold_end": split["test_end"],
        "oos": result_metrics(result),
        "oos_metrics": result_metrics(result),
        "fitness": float0(getattr(result, "fitness", 0.0)),
        "entry_execution_mode": ENTRY_EXECUTION_MODE,
        "exit_execution_mode": EXIT_EXECUTION_MODE,
        "fold_exit_policy": FOLD_EXIT_POLICY,
    }


def summarize_trades(trades: list[dict[str, Any]], *, fold_end: str) -> dict[str, Any]:
    total = len(trades)
    entry_mode_ok = sum(1 for row in trades if row.get("entry_execution_mode") == ENTRY_EXECUTION_MODE)
    exit_mode_ok = sum(1 for row in trades if row.get("exit_execution_mode") == EXIT_EXECUTION_MODE)
    fill_gt_signal = 0
    exit_le_fold_end = 0
    exit_gt_fold_end = 0
    fold_mtm = 0
    missing_dates = 0
    wrong_fold_label = 0
    for row in trades:
        signal = str(row.get("entry_signal_date") or "")
        fill = str(row.get("entry_fill_date") or "")
        exit_date = str(row.get("exit_date") or "")
        if signal and fill and fill > signal:
            fill_gt_signal += 1
        elif not signal or not fill:
            missing_dates += 1
        if exit_date and exit_date <= fold_end:
            exit_le_fold_end += 1
        elif exit_date and exit_date > fold_end:
            exit_gt_fold_end += 1
        else:
            missing_dates += 1
        if row.get("exit_reason") == "fold_end_mark_to_market":
            fold_mtm += 1
        if row.get("fold_label") and row.get("label") and row.get("fold_label") != row.get("label"):
            wrong_fold_label += 1
    return {
        "trade_count": total,
        "entry_mode_ok_count": entry_mode_ok,
        "exit_mode_ok_count": exit_mode_ok,
        "all_entry_execution_mode_tplus1": total == entry_mode_ok,
        "all_exit_execution_mode_conservative_core": total == exit_mode_ok,
        "entry_fill_date_gt_entry_signal_date_count": fill_gt_signal,
        "entry_fill_date_gt_entry_signal_date_ratio": (fill_gt_signal / total) if total else 0.0,
        "exit_date_lte_fold_end_count": exit_le_fold_end,
        "exit_date_gt_fold_end_count": exit_gt_fold_end,
        "fold_end_mark_to_market_count": fold_mtm,
        "missing_date_count": missing_dates,
        "wrong_fold_label_count": wrong_fold_label,
    }


def flatten_candidate_trades(candidate_trade_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidate_trade_rows:
        for trade in candidate.get("trades", []) or []:
            row = dict(trade)
            row["run_key"] = candidate.get("run_key")
            row["ticker"] = candidate.get("ticker")
            row["label"] = candidate.get("label")
            row["fold_label"] = candidate.get("fold_label", candidate.get("label"))
            row["run_key_label"] = candidate.get("run_key_label")
            row["is_stress"] = bool(candidate.get("is_stress"))
            row["fold_end"] = candidate.get("fold_end")
            row["rank_is"] = candidate.get("rank_is")
            row["rulebook_hash"] = candidate.get("rulebook_hash")
            out.append(row)
    return out


def existing_summary_for_run(ticker: str, split: Mapping[str, Any]) -> dict[str, Any]:
    run_key = run_key_for(ticker, split)
    topn_rows = [row for row in read_jsonl(TOPN_PATH) if row.get("run_key") == run_key]
    trade_rows = [row for row in read_jsonl(TRADES_PATH) if row.get("run_key") == run_key]
    topn = topn_rows[-1] if topn_rows else {}
    flat_trades = flatten_candidate_trades(trade_rows)
    trade_summary = summarize_trades(flat_trades, fold_end=str(split["test_end"]))
    candidates = topn.get("candidates") or []
    representative_trade_count = 0
    if candidates:
        representative_trade_count = int((candidates[0].get("oos") or {}).get("trade_count", 0) or 0)
    best_fitness = finite_or_none((topn.get("ga") or {}).get("best_fitness"))
    timing = topn.get("timing") if isinstance(topn.get("timing"), dict) else {}
    return {
        "ticker": ticker,
        "run_key": run_key,
        "fold_label": split["label"],
        "run_key_label": split["run_key_label"],
        "is_stress": bool(split.get("is_stress")),
        "fold_end": split["test_end"],
        "completed": bool(topn_rows),
        "skipped_existing": True,
        "candidate_count": len(candidates),
        "best_fitness": best_fitness,
        "best_fitness_is_finite": best_fitness is not None,
        "train_nan_inf_fitness_count": 0,
        "oos_nan_inf_fitness_count": 0,
        "evaluate_fn_call_count": (topn.get("ga") or {}).get("evaluate_fn_call_count"),
        "generations_run": (topn.get("ga") or {}).get("generations_run"),
        "trade_summary": trade_summary,
        "representative_trade_count": representative_trade_count,
        "total_candidate_trade_rows": sum(int(row.get("trade_count", 0) or 0) for row in trade_rows),
        "elapsed_seconds": timing.get("elapsed_seconds", 0.0),
        "ga_seconds": timing.get("ga_seconds", 0.0),
        "oos_candidates_seconds": timing.get("oos_candidates_seconds", 0.0),
    }


def run_ticker_fold_smoke(ticker: str, split_in: Mapping[str, Any], ctx: dict[str, Any], completed: set[str]) -> dict[str, Any]:
    split = dict(split_in)
    split["train_start"] = ctx.get("data_start") or ctx.get("data_min") or "2020-01-01"
    if split["is_stress"]:
        split["test_end"] = ctx.get("data_end") or ctx.get("data_max") or "2026-06-09"
    run_key = run_key_for(ticker, split)
    if run_key in completed:
        return existing_summary_for_run(ticker, split)

    start = time.perf_counter()
    df = ctx["df"]
    kwargs = base_kwargs(ctx)
    ga_cfg = make_ga_config(ticker, split)
    evaluate_call_count = 0
    train_nan_inf_count = 0

    def evaluate_fn(rb: Any) -> float:
        nonlocal evaluate_call_count, train_nan_inf_count
        evaluate_call_count += 1
        result = run_backtest_execution_mode(
            rb,
            df,
            start_date=split["train_start"],
            end_date=split["train_end"],
            **kwargs,
        )
        fitness = finite_or_none(getattr(result, "fitness", None))
        if fitness is None:
            train_nan_inf_count += 1
            return -1_000_000.0
        return fitness

    ga_start = time.perf_counter()
    base = copy.deepcopy(ctx["base_rulebook"])
    ga_result = run_ga(base_rulebook=base, evaluate_fn=evaluate_fn, ga_config=ga_cfg)
    ga_seconds = time.perf_counter() - ga_start

    candidates = collect_top_rulebooks(ga_result, CANDIDATE_COLLECT_N)
    candidate_rows: list[dict[str, Any]] = []
    candidate_trades_by_hash: dict[str, dict[str, Any]] = {}
    oos_nan_inf_count = 0
    oos_start = time.perf_counter()
    for rank_is, rb in enumerate(candidates, 1):
        result = run_backtest_execution_mode(
            rb,
            df,
            start_date=split["test_start"],
            end_date=split["test_end"],
            **kwargs,
        )
        fitness = finite_or_none(getattr(result, "fitness", None))
        if fitness is None:
            oos_nan_inf_count += 1
        row = candidate_row(ticker, split, rank_is, rb, result)
        candidate_rows.append(row)
        h = str(row.get("rulebook_hash") or "")
        if h:
            candidate_trades_by_hash[h] = {
                "run_key": run_key,
                "ticker": ticker,
                "year": split["year"],
                "label": split["label"],
                "fold_label": split["label"],
                "run_key_label": split["run_key_label"],
                "is_stress": bool(split.get("is_stress")),
                "fold_end": split["test_end"],
                "rank_is": int(rank_is),
                "rulebook_hash": h,
                "trade_count": int(getattr(result, "trade_count", 0) or 0),
                "trades": list(getattr(result, "trades", []) or []),
            }
    oos_seconds = time.perf_counter() - oos_start

    selected_rows = candidate_rows
    rulebook_rows = collect_rulebook_rows(run_key, ticker, split["year"], candidates, selected_rows)
    trade_rows = collect_trade_rows(candidate_trades_by_hash, selected_rows)
    flat_trades = flatten_candidate_trades(trade_rows)
    trade_summary = summarize_trades(flat_trades, fold_end=split["test_end"])

    topn_row = {
        "run_key": run_key,
        "created_at": utc_now(),
        "ticker": ticker,
        "year": split["year"],
        "label": split["label"],
        "fold_label": split["label"],
        "run_key_label": split["run_key_label"],
        "is_stress": bool(split.get("is_stress")),
        "split": split,
        "config": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "candidate_collect_n": CANDIDATE_COLLECT_N,
            "fitness_mode": FITNESS_MODE,
            "entry_execution_mode": ENTRY_EXECUTION_MODE,
            "exit_execution_mode": EXIT_EXECUTION_MODE,
            "fold_exit_policy": FOLD_EXIT_POLICY,
            "use_llm_events": False,
            "purpose": "four_fold_smoke_only_not_performance_evaluation",
        },
        "timing": {
            "ga_seconds": round(ga_seconds, 6),
            "oos_candidates_seconds": round(oos_seconds, 6),
            "elapsed_seconds": round(time.perf_counter() - start, 6),
        },
        "ga": {
            "generations_run": getattr(ga_result, "generations_run", None),
            "best_fitness": float0(getattr(getattr(ga_result, "best", None), "fitness", 0.0)),
            "evaluate_fn_call_count": evaluate_call_count,
            "final_population_size": len(getattr(ga_result, "final_population", []) or []),
            "candidate_pool_count": len(candidates),
        },
        "candidate_pool_count": len(candidates),
        "qualified_count": len(selected_rows),
        "candidates": selected_rows,
    }
    append_jsonl(TOPN_PATH, topn_row)
    for row in rulebook_rows:
        append_jsonl(RULEBOOKS_PATH, row)
    for row in trade_rows:
        append_jsonl(TRADES_PATH, row)

    best_fitness = finite_or_none(getattr(getattr(ga_result, "best", None), "fitness", None))
    return {
        "ticker": ticker,
        "run_key": run_key,
        "fold_label": split["label"],
        "run_key_label": split["run_key_label"],
        "is_stress": bool(split.get("is_stress")),
        "fold_end": split["test_end"],
        "completed": True,
        "skipped_existing": False,
        "candidate_count": len(selected_rows),
        "best_fitness": best_fitness,
        "best_fitness_is_finite": best_fitness is not None,
        "train_nan_inf_fitness_count": train_nan_inf_count,
        "oos_nan_inf_fitness_count": oos_nan_inf_count,
        "evaluate_fn_call_count": evaluate_call_count,
        "generations_run": getattr(ga_result, "generations_run", None),
        "trade_summary": trade_summary,
        "representative_trade_count": int(selected_rows[0].get("oos", {}).get("trade_count", 0) if selected_rows else 0),
        "total_candidate_trade_rows": sum(int(row.get("trade_count", 0) or 0) for row in trade_rows),
        "elapsed_seconds": round(time.perf_counter() - start, 6),
        "ga_seconds": round(ga_seconds, 6),
        "oos_candidates_seconds": round(oos_seconds, 6),
    }


def file_status() -> dict[str, Any]:
    return {
        str(TOPN_PATH): TOPN_PATH.exists(),
        str(RULEBOOKS_PATH): RULEBOOKS_PATH.exists(),
        str(TRADES_PATH): TRADES_PATH.exists(),
        str(SUMMARY_PATH): SUMMARY_PATH.exists(),
    }


def label_artifact_summary() -> dict[str, Any]:
    topn_rows = read_jsonl(TOPN_PATH)
    trade_rows = read_jsonl(TRADES_PATH)
    labels = sorted({str(row.get("fold_label") or row.get("label")) for row in topn_rows})
    trade_labels = sorted({str(row.get("fold_label") or row.get("label")) for row in trade_rows})
    run_keys = sorted({str(row.get("run_key")) for row in topn_rows})
    stress_bad = [row.get("run_key") for row in topn_rows if (row.get("fold_label") == STRESS_LABEL) != bool(row.get("is_stress"))]
    general_bad = [row.get("run_key") for row in topn_rows if row.get("fold_label") in {"2022", "2023", "2024"} and bool(row.get("is_stress"))]
    return {
        "topn_label_count": len(labels),
        "topn_labels": labels,
        "trade_labels": trade_labels,
        "run_key_count": len(run_keys),
        "stress_tag_mismatch_count": len(stress_bad),
        "stress_tag_mismatches": stress_bad[:20],
        "general_stress_mismatch_count": len(general_bad),
        "general_stress_mismatches": general_bad[:20],
        "labels_separable": set(labels) == {"2022", "2023", "2024", STRESS_LABEL},
    }


def build_pass_flags(run_summaries: list[dict[str, Any]], nan_inf_count: int) -> dict[str, Any]:
    completed = {row["run_key"]: bool(row.get("completed")) for row in run_summaries}
    fold_mtm_total = sum(int(row.get("trade_summary", {}).get("fold_end_mark_to_market_count", 0) or 0) for row in run_summaries)
    exit_over_total = sum(int(row.get("trade_summary", {}).get("exit_date_gt_fold_end_count", 0) or 0) for row in run_summaries)
    entry_bad_total = sum(
        int(row.get("trade_summary", {}).get("trade_count", 0) or 0)
        - int(row.get("trade_summary", {}).get("entry_fill_date_gt_entry_signal_date_count", 0) or 0)
        for row in run_summaries
    )
    entry_mode_bad_total = sum(
        int(row.get("trade_summary", {}).get("trade_count", 0) or 0)
        - int(row.get("trade_summary", {}).get("entry_mode_ok_count", 0) or 0)
        for row in run_summaries
    )
    exit_mode_bad_total = sum(
        int(row.get("trade_summary", {}).get("trade_count", 0) or 0)
        - int(row.get("trade_summary", {}).get("exit_mode_ok_count", 0) or 0)
        for row in run_summaries
    )
    wrong_fold_label_total = sum(int(row.get("trade_summary", {}).get("wrong_fold_label_count", 0) or 0) for row in run_summaries)
    files = file_status()
    artifact = label_artifact_summary()
    general_count = sum(1 for row in run_summaries if not row.get("is_stress"))
    stress_count = sum(1 for row in run_summaries if row.get("is_stress"))
    return {
        "expected_run_key_count": len(expected_run_keys()),
        "completed_run_key_count": sum(1 for key in expected_run_keys() if completed.get(key, False)),
        "all_run_keys_completed": all(completed.get(key, False) for key in expected_run_keys()),
        "all_output_files_created": all(files.values()),
        "entry_mode_bad_total": entry_mode_bad_total,
        "exit_mode_bad_total": exit_mode_bad_total,
        "entry_fill_not_after_signal_total": entry_bad_total,
        "exit_after_fold_end_total": exit_over_total,
        "fold_end_mark_to_market_total": fold_mtm_total,
        "wrong_fold_label_total": wrong_fold_label_total,
        "nan_inf_fitness_total": nan_inf_count,
        "general_run_summary_count": general_count,
        "stress_run_summary_count": stress_count,
        "label_artifact_summary": artifact,
        "pass_candidate": (
            all(completed.get(key, False) for key in expected_run_keys())
            and all(files.values())
            and entry_mode_bad_total == 0
            and exit_mode_bad_total == 0
            and entry_bad_total == 0
            and exit_over_total == 0
            and wrong_fold_label_total == 0
            and nan_inf_count == 0
            and general_count == 9
            and stress_count == 3
            and artifact["labels_separable"]
            and artifact["stress_tag_mismatch_count"] == 0
            and artifact["general_stress_mismatch_count"] == 0
        ),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    completed = completed_keys(TOPN_PATH)
    run_summaries: list[dict[str, Any]] = []
    contexts = {ticker: prepare_ticker_context(ticker) for ticker in TICKERS}
    for ticker in TICKERS:
        for split in SMOKE_SPLITS:
            run_summaries.append(run_ticker_fold_smoke(ticker, split, contexts[ticker], completed))
            completed = completed_keys(TOPN_PATH)

    nan_inf_count = sum(
        int(row.get("train_nan_inf_fitness_count", 0) or 0) + int(row.get("oos_nan_inf_fitness_count", 0) or 0)
        for row in run_summaries
    )
    summary = {
        "run_id": RUN_ID,
        "purpose": "four_fold_real_data_smoke_only_not_performance_evaluation",
        "created_at": utc_now(),
        "tickers": list(TICKERS),
        "folds": [dict(split, train_start="per_ticker_data_start", test_end=("per_ticker_data_end" if split["is_stress"] else split["test_end"])) for split in SMOKE_SPLITS],
        "expected_run_keys": expected_run_keys(),
        "config": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "candidate_collect_n": CANDIDATE_COLLECT_N,
            "fitness_mode": FITNESS_MODE,
            "entry_execution_mode": ENTRY_EXECUTION_MODE,
            "exit_execution_mode": EXIT_EXECUTION_MODE,
            "fold_exit_policy": FOLD_EXIT_POLICY,
            "position_limit_krw": POSITION_LIMIT_KRW,
            "commission_rate": COMMISSION_RATE,
            "warmup": WARMUP,
        },
        "output_files": {
            "topn": str(TOPN_PATH),
            "rulebooks": str(RULEBOOKS_PATH),
            "trades": str(TRADES_PATH),
            "summary": str(SUMMARY_PATH),
        },
        "run_summaries": run_summaries,
        "nan_inf_fitness_total": nan_inf_count,
        "pass_flags": {},
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    summary["pass_flags"] = build_pass_flags(run_summaries, nan_inf_count)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
