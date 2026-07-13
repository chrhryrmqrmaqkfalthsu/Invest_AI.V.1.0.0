#!/usr/bin/env python3
"""AAP 새 fitness 정식 Stage 3 runner — deterministic 6-worker population evaluation.

정식 규모:
- qualify: population 100 / generations 40 / 3 folds
- entry: population 100 / generations 50 / expectancy gate 2%
- exit: 기존 Stage 3 14-field, entry candidate 단위 최대 6 process
- validate: final candidate 단위 최대 6 process

qualify/entry GA는 후보 생성과 모든 RNG 소비를 부모 프로세스에서 기존 순서로
수행하고 fitness backtest만 6 fork worker로 분산한다. 결과는 input index
순서대로 병합한다. 전체 final population, cross-fold matrix, 신규 fitness
진단, mutation 방향/interval width 이동, fold-best trade-level을 보존한다.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
REPOSITORY_ROOT = WORKSPACE_ROOT.parents[2]
OFFICIAL_RUNNER = HERE.with_name("run_stage3_official_2sym.py")
DETAIL_RUNNER = HERE.with_name("run_stage3_aap_detail.py")
PARALLEL_RESUME_RUNNER = HERE.with_name("run_stage3_parallel_resume.py")

TICKER = "AAP"
WORKERS = 6
SEED_BASE_DEFAULT = 2026071401
DAEMON_PID = 494330
OFFICIAL_CONFIG = {
    "qualify_population": 100,
    "qualify_generations": 40,
    "entry_population": 100,
    "entry_generations": 50,
    "exit_population": 60,
    "exit_generations": 25,
    "top_n_qualify": 100,
    "top_n_entry_pool": 100,
    "max_entry_candidates": 20,
    "top_n_exit_per_entry": 3,
    "entry_min_expectancy_pct": 2.0,
    "entry_overlap_threshold": 0.7,
}
PROTECTED_PATHS = (
    REPOSITORY_ROOT / ".env",
    REPOSITORY_ROOT / "data/_system/market_history.csv",
    REPOSITORY_ROOT / "data/_system/market_history_v2.csv",
)


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


official = _load_module("_aap_newfitness_official_support", OFFICIAL_RUNNER)
detail = _load_module("_aap_newfitness_detail_support", DETAIL_RUNNER)
parallel_resume = _load_module("_aap_newfitness_parallel_resume", PARALLEL_RESUME_RUNNER)
official._apply_official_config()
support = official.support
mod = official.mod
Rulebook = official.Rulebook

from engine.learning import execution_mode_backtest as execution_bt  # noqa: E402
from engine.learning import genetic_parallel  # noqa: E402
from engine.strategies.rulebook import ENTRY_INTERVAL_SPECS  # noqa: E402

_CROSS_CTX: dict[str, Any] | None = None


class _Tee:
    def __init__(self, *streams: Any) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
        handle.flush()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if np.isfinite(number) else float(default)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_snapshot() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in PROTECTED_PATHS:
        if not path.is_file():
            raise FileNotFoundError(f"protected file missing: {path}")
        snapshot[str(path.relative_to(REPOSITORY_ROOT))] = _sha256(path)
    return snapshot


def _daemon_snapshot() -> dict[str, Any]:
    proc = Path(f"/proc/{DAEMON_PID}")
    if not proc.is_dir():
        raise RuntimeError(f"required daemon PID is not alive: {DAEMON_PID}")
    stat_fields = (proc / "stat").read_text(encoding="utf-8").split()
    cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    return {
        "pid": DAEMON_PID,
        "state": stat_fields[2] if len(stat_fields) > 2 else None,
        "starttime_ticks": stat_fields[21] if len(stat_fields) > 21 else None,
        "cmdline": cmdline,
    }


def _manifest_gate(market_metadata: Mapping[str, Any]) -> dict[str, Any]:
    return detail._validate_manifest_gate(market_metadata)


def _entry_scope_result(rulebook: Rulebook, ctx: dict[str, Any], split: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    marker = execution_bt.ENTRY_GA_SCOPE_MARKER
    present = hasattr(rulebook, marker)
    previous = getattr(rulebook, marker, None)
    setattr(rulebook, marker, execution_bt.ENTRY_GA_SCOPE_VALUE)
    try:
        result = mod.run_entry_backtest_period(
            rulebook,
            ctx,
            start=str(split["start"]),
            end=str(split["end"]),
        )
        diagnostics = dict(getattr(result, "entry_fitness_diagnostics", {}) or {})
        if not diagnostics:
            raise RuntimeError("entry-scope new fitness diagnostics missing")
        return result, diagnostics
    finally:
        if present:
            setattr(rulebook, marker, previous)
        else:
            try:
                delattr(rulebook, marker)
            except AttributeError:
                pass


def _cross_worker(task: tuple[int, str, dict[str, Any], dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    index, candidate_hash, rulebook_payload, split = task
    if _CROSS_CTX is None:
        raise RuntimeError("cross-fold context not initialized")
    rb = Rulebook.from_dict(rulebook_payload)
    result, diagnostics = _entry_scope_result(rb, _CROSS_CTX, split)
    metrics = dict(mod._base.result_metrics(result))
    trades = list(getattr(result, "trades", []) or [])
    return index, {
        "candidate_hash": candidate_hash,
        "period_label": str(split["label"]),
        "metrics": metrics,
        "entry_fitness_diagnostics": diagnostics,
        "entry_dates": sorted(mod._base.entry_dates_from_trades(trades)),
    }


def _parallel_cross_evaluate(
    candidates: list[tuple[str, Rulebook]],
    splits: list[dict[str, Any]],
    ctx: dict[str, Any],
    workers: int,
) -> list[dict[str, Any]]:
    global _CROSS_CTX
    if "fork" not in mp.get_all_start_methods():
        raise RuntimeError("cross-fold deterministic parallel evaluation requires fork")
    _CROSS_CTX = ctx
    tasks: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
    index = 0
    for candidate_hash, rb in candidates:
        payload = rb.to_dict()
        for split in splits:
            tasks.append((index, candidate_hash, payload, dict(split)))
            index += 1
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork")) as pool:
            results = list(pool.map(_cross_worker, tasks, chunksize=1))
    finally:
        _CROSS_CTX = None
    results.sort(key=lambda row: row[0])
    return [row[1] for row in results]


def _generation_callback(out_dir: Path, *, stage: str, fold: str, call_index: int) -> Any:
    path = out_dir / "generation_best_fitness.jsonl"

    def callback(generation: int, best: Rulebook, average: float) -> None:
        diagnostics = dict(getattr(best, "_entry_fitness_diagnostics", {}) or {})
        row = {
            "event": "stage3_aap_newfitness_ga_generation",
            "stage": stage,
            "fold": fold,
            "call_index": call_index,
            "gene_scope": "entry",
            "evaluation_workers": WORKERS,
            "parallel_axis": "population_fitness_evaluation",
            "generation": int(generation),
            "best_hash": mod._base.compute_rulebook_hash(best),
            "best_fitness": _safe_float(getattr(best, "fitness", 0.0)),
            "mean_fitness": float(average),
            "best_primary_objective_pct_per_day": diagnostics.get("primary_objective_pct_per_day"),
            "best_mae_penalty": diagnostics.get("mae_penalty"),
            "best_win_rate_pct": diagnostics.get("win_rate_pct"),
            "best_win_gate_pass": diagnostics.get("win_rate_gate_pass"),
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _append_jsonl(path, row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    return callback


def _population_rows(
    *,
    ticker: str,
    stage: str,
    fold: str,
    population: list[Rulebook],
) -> list[dict[str, Any]]:
    ranked = sorted(population, key=lambda rb: _safe_float(getattr(rb, "fitness", float("-inf"))), reverse=True)
    rows: list[dict[str, Any]] = []
    for rank, rb in enumerate(ranked, 1):
        rows.append(
            {
                "ticker": ticker,
                "stage": stage,
                "fold": fold,
                "population_rank": rank,
                "rulebook_hash": mod._base.compute_rulebook_hash(rb),
                "fitness": _safe_float(getattr(rb, "fitness", 0.0)),
                "entry_fitness_diagnostics": dict(getattr(rb, "_entry_fitness_diagnostics", {}) or {}),
                "entry_exit_mutation_hint": dict(getattr(rb, "_entry_exit_mutation_hint", {}) or {}),
                "entry_exit_mutation_applied": dict(getattr(rb, "_entry_exit_mutation_applied", {}) or {}),
                "rulebook": rb.to_dict(),
            }
        )
    return rows


def _history_rows(result: Any, *, stage: str, fold: str, call_index: int) -> list[dict[str, Any]]:
    rows = []
    for item in list(getattr(result, "population_diagnostics_history", []) or []):
        rows.append(
            {
                "stage": stage,
                "fold": fold,
                "call_index": call_index,
                "gene_scope": "entry",
                "evaluation_workers": int(getattr(result, "evaluation_workers", 1) or 1),
                "parallel_axis": getattr(result, "parallel_axis", None),
                **dict(item),
            }
        )
    return rows


def _qualify_fail_metrics(metrics: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    config = mod._base.DEFAULT_STAGE3_QUALIFY
    failures: list[str] = []
    if int(_safe_float(metrics.get("trade_count"), 0.0)) < int(config.min_trades):
        failures.append("trade_count")
    if _safe_float(metrics.get("member_score")) < float(config.min_member_score):
        failures.append("member_score")
    if _safe_float(metrics.get("expectancy_pct")) < float(config.qualify_min_expectancy_pct):
        failures.append("expectancy_pct")
    if not bool(diagnostics.get("win_rate_gate_pass")):
        failures.append("win_rate_gate")
    return failures


def _build_cross_matrix(scored_inputs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    by_fold: dict[str, list[dict[str, Any]]] = defaultdict(list)
    diag_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entry_dates_by_key: dict[tuple[str, str], list[str]] = {}
    for row in scored_inputs:
        fold = str(row["period_label"])
        candidate_hash = str(row["candidate_hash"])
        by_fold[fold].append(
            {
                "ticker": TICKER,
                "label": fold,
                "period_label": fold,
                "rulebook_hash": candidate_hash,
                "rank_is": len(by_fold[fold]) + 1,
                "oos": dict(row["metrics"]),
            }
        )
        diag_by_key[(candidate_hash, fold)] = dict(row["entry_fitness_diagnostics"])
        entry_dates_by_key[(candidate_hash, fold)] = list(row.get("entry_dates") or [])

    matrix: list[dict[str, Any]] = []
    gate_summary: dict[str, Any] = {}
    for split in mod._base.TRAIN_SPLITS:
        fold = str(split["label"])
        scored = mod._base._score_period_candidates(by_fold[fold])
        fold_rows: list[dict[str, Any]] = []
        for row in scored:
            candidate_hash = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            metrics["member_score"] = _safe_float(row.get("oos_member_score"))
            metrics["fitness"] = _safe_float(row.get("fitness"))
            diagnostics = diag_by_key[(candidate_hash, fold)]
            fail_metrics = _qualify_fail_metrics(metrics, diagnostics)
            output = {
                "ticker": TICKER,
                "candidate_hash": candidate_hash,
                "period_label": fold,
                "pass": not fail_metrics,
                "fail_metrics": fail_metrics,
                "trade_count": int(_safe_float(metrics.get("trade_count"), 0.0)),
                "win_rate_pct": _safe_float(metrics.get("win_rate")),
                "expectancy_pct": _safe_float(metrics.get("expectancy_pct")),
                "profit_factor": _safe_float(metrics.get("profit_factor")),
                "max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
                "member_score": _safe_float(metrics.get("member_score")),
                "primary_objective_pct_per_day": _safe_float(diagnostics.get("primary_objective_pct_per_day")),
                "mae_penalty": _safe_float(diagnostics.get("mae_penalty")),
                "mae_breach_trade_count": int(_safe_float(diagnostics.get("mae_breach_trade_count"), 0.0)),
                "win_rate_gate_pct": diagnostics.get("win_rate_gate_pct"),
                "win_rate_gate_pass": bool(diagnostics.get("win_rate_gate_pass")),
                "disqualified": bool(diagnostics.get("disqualified")),
                "final_fitness": _safe_float(diagnostics.get("final_fitness"), _safe_float(metrics.get("fitness"))),
                "mdd_risk": dict(diagnostics.get("mdd_risk") or {}),
                "entry_exit_mutation_hint": dict(diagnostics.get("exit_mutation_hint") or {}),
                "entry_dates": entry_dates_by_key[(candidate_hash, fold)],
            }
            matrix.append(output)
            fold_rows.append(output)
        penalized = [row["mae_penalty"] for row in fold_rows if row["mae_penalty"] > 0.0]
        gate_summary[fold] = {
            "candidate_count": len(fold_rows),
            "win_rate_gate_disqualified_count": sum(1 for row in fold_rows if not row["win_rate_gate_pass"]),
            "win_rate_gate_disqualified_rate": (
                sum(1 for row in fold_rows if not row["win_rate_gate_pass"]) / len(fold_rows)
                if fold_rows else 0.0
            ),
            "mae_penalized_count": len(penalized),
            "mae_penalized_rate": len(penalized) / len(fold_rows) if fold_rows else 0.0,
            "mean_mae_penalty_among_penalized": statistics.mean(penalized) if penalized else 0.0,
            "mean_mae_penalty_all": statistics.mean([row["mae_penalty"] for row in fold_rows]) if fold_rows else 0.0,
            "after_win_gate_count": sum(1 for row in fold_rows if row["win_rate_gate_pass"]),
            "qualify_pass_count": sum(1 for row in fold_rows if row["pass"]),
            "fail_metric_counts": dict(Counter(metric for row in fold_rows for metric in row["fail_metrics"])),
            "largest_hard_filter": "win_rate_gate",
            "note": "MAE is a score penalty, not a hard removal gate",
        }

    by_candidate: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in matrix:
        by_candidate[row["candidate_hash"]][row["period_label"]] = bool(row["pass"])
    vectors: list[dict[str, Any]] = []
    distribution = Counter()
    for candidate_hash in sorted(by_candidate):
        vector = {str(split["label"]): bool(by_candidate[candidate_hash].get(str(split["label"]), False)) for split in mod._base.TRAIN_SPLITS}
        count = sum(vector.values())
        distribution[count] += 1
        vectors.append({"ticker": TICKER, "candidate_hash": candidate_hash, "pass_count": count, "pass_vector": vector})
    pass_distribution = {
        "all3": int(distribution[3]),
        "all2": int(distribution[2]),
        "all1": int(distribution[1]),
        "all0": int(distribution[0]),
    }
    return matrix, {"folds": gate_summary, "pass_count_distribution": pass_distribution}, vectors


def _fold_best_trade_rows(
    *,
    fold: str,
    rulebook: Rulebook,
    result: Any,
    diagnostics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidate_hash = mod._base.compute_rulebook_hash(rulebook)
    for index, trade in enumerate(list(getattr(result, "trades", []) or []), 1):
        tape = trade.get("entry_signal_tape") if isinstance(trade.get("entry_signal_tape"), Mapping) else {}
        pnl = _safe_float(trade.get("pnl_pct"))
        holding = max(int(_safe_float(trade.get("holding_days"), 0.0)), 1)
        rows.append(
            {
                "ticker": TICKER,
                "stage": "qualify_fold_best",
                "period_label": fold,
                "candidate_hash": candidate_hash,
                "trade_index": index,
                "entry_signal_date": trade.get("entry_signal_date"),
                "entry_date": trade.get("entry_fill_date", trade.get("entry_date")),
                "entry_price": trade.get("entry_price"),
                "exit_date": trade.get("exit_date"),
                "exit_price": trade.get("exit_price"),
                "exit_reason": trade.get("exit_reason"),
                "holding_days": trade.get("holding_days"),
                "realized_pnl_pct_after_cost": pnl,
                "mae_pct": _safe_float(trade.get("max_loss_during_hold")),
                "daily_return_pct": pnl / holding,
                "win_after_cost": bool(pnl > 0.0),
                "entry_features": dict(tape.get("entry_features") or {}),
                "interval_checks": dict(tape.get("interval_checks") or {}),
                "strict_interval_pass": tape.get("strict_interval_pass"),
                "entry_exit_local_search": dict(trade.get("entry_exit_local_search") or {}),
                "fold_best_fitness_diagnostics": dict(diagnostics),
            }
        )
    return rows


def _run_qualify(out_dir: Path, ctx: dict[str, Any], seed_base: int) -> tuple[dict[str, Any], dict[str, Rulebook], list[dict[str, Any]]]:
    started = time.time()
    candidates_by_hash: dict[str, Rulebook] = {}
    population_rows: list[dict[str, Any]] = []
    population_history_rows: list[dict[str, Any]] = []
    fold_best_trade_rows: list[dict[str, Any]] = []
    ga_summaries: list[dict[str, Any]] = []

    for call_index, split in enumerate(mod._base.TRAIN_SPLITS, 1):
        fold = str(split["label"])
        seed = seed_base + call_index
        domain = mod.build_entry_feature_domain(ctx, start=split["start"], end=split["end"])

        def evaluate_fn(rulebook: Rulebook, s: dict[str, Any] = dict(split)) -> float:
            result = mod.run_entry_backtest_period(rulebook, ctx, start=s["start"], end=s["end"])
            return _safe_float(getattr(result, "fitness", execution_bt.ENTRY_FITNESS_DISQUALIFIED), execution_bt.ENTRY_FITNESS_DISQUALIFIED)

        ga = genetic_parallel.run_ga(
            base_rulebook=ctx["base_rulebook"],
            evaluate_fn=evaluate_fn,
            ga_config=mod._base.make_ga_config(
                population=OFFICIAL_CONFIG["qualify_population"],
                generations=OFFICIAL_CONFIG["qualify_generations"],
                seed=seed,
            ),
            on_generation=_generation_callback(out_dir, stage="qualify", fold=fold, call_index=call_index),
            gene_scope="entry",
            entry_feature_domain=domain,
            evaluation_workers=WORKERS,
        )
        fold_population = _population_rows(ticker=TICKER, stage="qualify", fold=fold, population=list(ga.final_population))
        population_rows.extend(fold_population)
        population_history_rows.extend(_history_rows(ga, stage="qualify", fold=fold, call_index=call_index))
        for rb in [ga.best, *list(ga.final_population)]:
            candidate_hash = mod._base.compute_rulebook_hash(rb)
            current = candidates_by_hash.get(candidate_hash)
            if current is None or _safe_float(getattr(rb, "fitness", 0.0)) > _safe_float(getattr(current, "fitness", 0.0)):
                candidates_by_hash[candidate_hash] = copy.deepcopy(rb)

        best_result, best_diag = _entry_scope_result(copy.deepcopy(ga.best), ctx, split)
        fold_best_trade_rows.extend(
            _fold_best_trade_rows(fold=fold, rulebook=ga.best, result=best_result, diagnostics=best_diag)
        )
        ga_summaries.append(
            {
                "fold": fold,
                "seed": seed,
                "population": OFFICIAL_CONFIG["qualify_population"],
                "generation_limit": OFFICIAL_CONFIG["qualify_generations"],
                "generations_run": int(ga.generations_run),
                "evaluation_workers": int(getattr(ga, "evaluation_workers", 1)),
                "parallel_axis": getattr(ga, "parallel_axis", None),
                "best_hash": mod._base.compute_rulebook_hash(ga.best),
                "best_fitness": _safe_float(getattr(ga.best, "fitness", 0.0)),
                "final_population_count": len(ga.final_population),
            }
        )

    _write_jsonl(out_dir / "qualify_population_all.jsonl", population_rows)
    _write_jsonl(out_dir / "ga_population_history.jsonl", population_history_rows)
    _write_jsonl(out_dir / "fold_best_trade_level.jsonl", fold_best_trade_rows)
    candidate_rows = [
        {
            "ticker": TICKER,
            "candidate_hash": candidate_hash,
            "rulebook": candidates_by_hash[candidate_hash].to_dict(),
        }
        for candidate_hash in sorted(candidates_by_hash)
    ]
    _write_jsonl(out_dir / "qualify_candidate_rulebooks.jsonl", candidate_rows)

    candidates = [(row["candidate_hash"], candidates_by_hash[row["candidate_hash"]]) for row in candidate_rows]
    cross_raw = _parallel_cross_evaluate(candidates, [dict(split) for split in mod._base.TRAIN_SPLITS], ctx, WORKERS)
    matrix, gate_summary, vectors = _build_cross_matrix(cross_raw)
    _write_jsonl(out_dir / "qualify_cross_fold_matrix.jsonl", matrix)
    _write_jsonl(out_dir / "qualify_candidate_pass_vectors.jsonl", vectors)
    _write_json(out_dir / "qualify_gate_bottleneck.json", gate_summary)

    pass_distribution = dict(gate_summary["pass_count_distribution"])
    all3_hashes = [row["candidate_hash"] for row in vectors if row["pass_count"] == 3]
    result = {
        "ticker": TICKER,
        "stage": "qualify",
        "execution_scale": "OFFICIAL_FULL_STAGE3_NEW_FITNESS",
        "qualified": bool(all3_hashes),
        "all3_pass_count": len(all3_hashes),
        "all3_pass_hash_samples": all3_hashes[:20],
        "pass_count_distribution": pass_distribution,
        "fold_pass_counts": {
            fold: sum(1 for row in matrix if row["period_label"] == fold and row["pass"])
            for fold in ("train_1", "train_2", "train_3")
        },
        "unique_candidate_count": len(candidates_by_hash),
        "population_rows_preserved": len(population_rows),
        "cross_fold_rows": len(matrix),
        "ga_summaries": ga_summaries,
        "gate_bottleneck": gate_summary["folds"],
        "new_fitness": {
            "primary": "mean(net realized pnl_pct / max(holding_days, 1))",
            "mae_threshold_pct": execution_bt.ENTRY_FITNESS_MAE_THRESHOLD_PCT,
            "mae_penalty_weight": execution_bt.ENTRY_FITNESS_MAE_PENALTY_WEIGHT,
            "win_rate_gate_pct": execution_bt.ENTRY_FITNESS_MIN_WIN_RATE_PCT,
            "disqualified_fitness": execution_bt.ENTRY_FITNESS_DISQUALIFIED,
            "mutation_hint_only": True,
        },
        "parallel": {
            "workers": WORKERS,
            "axis": "population_fitness_evaluation",
            "merge_order": "input_index_order",
            "rng_location": "parent_process_only",
            "cross_fold_workers": WORKERS,
        },
        "early_stopped_cross_fold": False,
        "elapsed_seconds": time.time() - started,
    }
    _write_json(out_dir / "qualify_result.json", result)
    return result, candidates_by_hash, population_history_rows


def _run_entry(out_dir: Path, ctx: dict[str, Any], seed_base: int, call_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = time.time()
    train_3 = next(dict(split) for split in mod._base.TRAIN_SPLITS if str(split["label"]) == "train_3")
    domain = mod.build_entry_feature_domain(ctx, start=train_3["start"], end=train_3["end"])
    seed = seed_base + 100

    def evaluate_fn(rulebook: Rulebook) -> float:
        result = mod.run_entry_backtest_period(rulebook, ctx, start=train_3["start"], end=train_3["end"])
        return _safe_float(getattr(result, "fitness", execution_bt.ENTRY_FITNESS_DISQUALIFIED), execution_bt.ENTRY_FITNESS_DISQUALIFIED)

    ga = genetic_parallel.run_ga(
        base_rulebook=ctx["base_rulebook"],
        evaluate_fn=evaluate_fn,
        ga_config=mod._base.make_ga_config(
            population=OFFICIAL_CONFIG["entry_population"],
            generations=OFFICIAL_CONFIG["entry_generations"],
            seed=seed,
        ),
        on_generation=_generation_callback(out_dir, stage="entry", fold="train_3", call_index=call_index),
        gene_scope="entry",
        entry_feature_domain=domain,
        evaluation_workers=WORKERS,
    )
    population_rows = _population_rows(ticker=TICKER, stage="entry", fold="train_3", population=list(ga.final_population))
    _write_jsonl(out_dir / "entry_population_all.jsonl", population_rows)
    history_rows = _history_rows(ga, stage="entry", fold="train_3", call_index=call_index)
    with (out_dir / "ga_population_history.jsonl").open("a", encoding="utf-8") as handle:
        for row in history_rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    top = mod._base.collect_top_rulebooks(ga, OFFICIAL_CONFIG["top_n_entry_pool"])
    unique: dict[str, Rulebook] = {}
    for rb in top:
        unique[mod._base.compute_rulebook_hash(rb)] = copy.deepcopy(rb)
    evaluated = _parallel_cross_evaluate(sorted(unique.items()), [train_3], ctx, WORKERS)
    by_hash = {row["candidate_hash"]: row for row in evaluated}
    evaluated_rows: list[dict[str, Any]] = []
    for pool_rank, (candidate_hash, rb) in enumerate(sorted(unique.items(), key=lambda item: _safe_float(by_hash[item[0]]["metrics"].get("fitness")), reverse=True), 1):
        raw = by_hash[candidate_hash]
        metrics = dict(raw["metrics"])
        diagnostics = dict(raw["entry_fitness_diagnostics"])
        evaluated_rows.append(
            {
                "ticker": TICKER,
                "pool_rank": pool_rank,
                "rulebook_hash": candidate_hash,
                "train_period": train_3,
                "gene_scope": "entry",
                "train_fitness": _safe_float(metrics.get("fitness")),
                "primary_objective_pct_per_day": _safe_float(diagnostics.get("primary_objective_pct_per_day")),
                "mae_penalty": _safe_float(diagnostics.get("mae_penalty")),
                "win_rate_gate_pass": bool(diagnostics.get("win_rate_gate_pass")),
                "disqualified": bool(diagnostics.get("disqualified")),
                "expectancy_pct": _safe_float(metrics.get("expectancy_pct")),
                "trade_count": int(_safe_float(metrics.get("trade_count"), 0.0)),
                "win_rate": _safe_float(metrics.get("win_rate")),
                "profit_factor": _safe_float(metrics.get("profit_factor")),
                "max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
                "entry_date_count": len(raw.get("entry_dates") or []),
                "entry_dates": list(raw.get("entry_dates") or []),
                "rulebook": rb.to_dict(),
            }
        )
    _write_jsonl(out_dir / "entry_pool_evaluated.jsonl", evaluated_rows)
    gate_eligible = [row for row in evaluated_rows if row["win_rate_gate_pass"] and not row["disqualified"]]
    selected, rejected = mod._base._select_diverse_entry_rows(gate_eligible, mod._base.DEFAULT_STAGE3_ENTRY_SELECTION)
    output_rows = []
    for rank, row in enumerate(selected, 1):
        output = dict(row)
        output["rank"] = rank
        output_rows.append(output)
    _write_jsonl(out_dir / "entry_rulebooks.jsonl", output_rows)
    _write_json(out_dir / "entry_rejected_overlap.json", rejected)
    summary = {
        "ticker": TICKER,
        "stage": "entry",
        "seed": seed,
        "population": OFFICIAL_CONFIG["entry_population"],
        "generation_limit": OFFICIAL_CONFIG["entry_generations"],
        "generations_run": int(ga.generations_run),
        "evaluation_workers": int(getattr(ga, "evaluation_workers", 1)),
        "parallel_axis": getattr(ga, "parallel_axis", None),
        "pool_count": len(evaluated_rows),
        "win_gate_pass_pool_count": len(gate_eligible),
        "expectancy_absolute_pass_count": sum(1 for row in gate_eligible if row["expectancy_pct"] >= OFFICIAL_CONFIG["entry_min_expectancy_pct"]),
        "selected_count": len(output_rows),
        "overlap_rejected_count": len(rejected),
        "best_fitness": output_rows[0]["train_fitness"] if output_rows else None,
        "best_hash": output_rows[0]["rulebook_hash"] if output_rows else None,
        "elapsed_seconds": time.time() - started,
    }
    _write_json(out_dir / "entry_result.json", summary)
    return summary, history_rows


def _mutation_summary(history_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_stage_fold: dict[str, Any] = {}
    for key, rows in _group_rows(history_rows, lambda row: f"{row.get('stage')}:{row.get('fold')}").items():
        ordered = sorted(rows, key=lambda row: int(row.get("generation", 0)))
        hint_counts = Counter()
        applied_counts = Counter()
        alignment_totals = Counter()
        for row in ordered:
            hint_counts.update(dict(row.get("mutation_hint_direction_counts") or {}))
            applied_counts.update(dict(row.get("mutation_applied_direction_counts") or {}))
            align = dict(row.get("mutation_width_alignment") or {})
            for name in (
                "earlier_total_feature_moves",
                "earlier_aligned_feature_moves",
                "later_total_feature_moves",
                "later_aligned_feature_moves",
            ):
                alignment_totals[name] += int(align.get(name, 0) or 0)
        first_widths = dict(ordered[0].get("mean_interval_widths") or {}) if ordered else {}
        last_widths = dict(ordered[-1].get("mean_interval_widths") or {}) if ordered else {}
        by_stage_fold[key] = {
            "history_rows": len(ordered),
            "hint_direction_counts": dict(hint_counts),
            "applied_direction_counts": dict(applied_counts),
            "width_alignment": {
                **dict(alignment_totals),
                "earlier_alignment_rate": (
                    alignment_totals["earlier_aligned_feature_moves"] / alignment_totals["earlier_total_feature_moves"]
                    if alignment_totals["earlier_total_feature_moves"] else None
                ),
                "later_alignment_rate": (
                    alignment_totals["later_aligned_feature_moves"] / alignment_totals["later_total_feature_moves"]
                    if alignment_totals["later_total_feature_moves"] else None
                ),
            },
            "mean_interval_width_first": first_widths,
            "mean_interval_width_last": last_widths,
            "mean_interval_width_change": {
                feature: _safe_float(last_widths.get(feature)) - _safe_float(first_widths.get(feature))
                for feature in ENTRY_INTERVAL_SPECS
            },
        }
    return {
        "policy": "mutation hint only; excluded from fitness, win gate and qualify pass",
        "local_search_cap_trading_days": execution_bt.ENTRY_EXIT_LOCAL_SEARCH_MAX_HOLDING_DAYS,
        "by_stage_fold": by_stage_fold,
    }


def _group_rows(rows: Iterable[dict[str, Any]], key_fn: Any) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row))].append(row)
    return grouped


def _reproducibility_probe() -> dict[str, Any]:
    base_rb = copy.deepcopy(mod._base.default_rulebook("AAP_REPRO", "us_stock", "long"))

    def evaluate(rulebook: Rulebook) -> float:
        return (
            -abs(_safe_float(getattr(rulebook, "signal_threshold", 0.0)) - 2.5) * 10.0
            -abs(_safe_float(getattr(rulebook, "base_position_ratio", 0.0)) - 0.7) * 20.0
            + 50.0
        )

    cfg = mod._base.make_ga_config(population=12, generations=3, seed=2026071499)
    return genetic_parallel.reproducibility_probe(base_rb, evaluate, cfg)


def _new_fitness_activation_probe(ctx: dict[str, Any]) -> dict[str, Any]:
    split = dict(mod._base.TRAIN_SPLITS[0])
    rb = copy.deepcopy(ctx["base_rulebook"])
    domain = mod.build_entry_feature_domain(ctx, start=split["start"], end=split["end"])
    rb = mod._base.random_rulebook(rb, gene_scope="entry", entry_feature_domain=domain)
    result, diagnostics = _entry_scope_result(rb, ctx, split)
    checks = {
        "gene_scope_marker": diagnostics.get("scope") == "entry",
        "primary_formula": diagnostics.get("primary_objective") == "mean(net_realized_pnl_pct / max(holding_days, 1))",
        "mae_threshold": _safe_float(diagnostics.get("mae_threshold_pct")) == -2.0,
        "win_gate": _safe_float(diagnostics.get("win_rate_gate_pct")) == 60.0,
        "mutation_hint_only": dict(diagnostics.get("exit_mutation_hint") or {}).get("fitness_input") is False,
        "stage2_legacy_default": genetic_parallel.run_ga.__kwdefaults__.get("gene_scope") == "legacy",
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "sample_trade_count": int(getattr(result, "trade_count", 0) or 0),
        "sample_final_fitness": _safe_float(getattr(result, "fitness", 0.0)),
    }


def _ce_boil_audit(out_dir: Path) -> dict[str, Any]:
    entry_rows = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
    final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
    return support._schema_audit([*entry_rows, *final_rows], 0)


def _write_sha_manifest(out_dir: Path) -> None:
    rows = []
    for path in sorted(out_dir.iterdir(), key=lambda item: item.name):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        rows.append(f"{_sha256(path)}  {path.name}")
    (out_dir / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _build_readout(final: Mapping[str, Any], qualify: Mapping[str, Any], entry: Mapping[str, Any] | None, mutation: Mapping[str, Any]) -> str:
    pdist = dict(qualify.get("pass_count_distribution") or {})
    lines = [
        "# AAP 새 fitness 정식 Stage 3 재학습 readout",
        "",
        "- 규모: qualify 100/40, entry 100/50, exit 14-field, validate",
        "- 병렬 축: GA population fitness evaluation 6 process",
        "- RNG: 부모 프로세스에서만 소비, input index 순서 병합",
        f"- qualify 통과: {bool(qualify.get('qualified'))}",
        f"- all3/all2/all1/all0: {pdist.get('all3', 0)}/{pdist.get('all2', 0)}/{pdist.get('all1', 0)}/{pdist.get('all0', 0)}",
        f"- entry survivor: {int((entry or {}).get('selected_count', 0) or 0)}",
        f"- validate survivor: {int(final.get('validate_survivor_count', 0) or 0)}",
        f"- CE/BOIL zero: {bool((final.get('ce_boil_audit') or {}).get('ce_boil_zero'))}",
        "",
        "## Fold gate 병목",
        "",
        "| fold | 후보 | 승률 gate 실격 | MAE 감점 | gate 후 잔존 | qualify pass |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for fold in ("train_1", "train_2", "train_3"):
        row = dict((qualify.get("gate_bottleneck") or {}).get(fold) or {})
        lines.append(
            f"| {fold} | {row.get('candidate_count', 0)} | {row.get('win_rate_gate_disqualified_count', 0)} ({float(row.get('win_rate_gate_disqualified_rate', 0.0)):.2%}) | {row.get('mae_penalized_count', 0)} ({float(row.get('mae_penalized_rate', 0.0)):.2%}), 평균 {float(row.get('mean_mae_penalty_among_penalized', 0.0)):.6f} | {row.get('after_win_gate_count', 0)} | {row.get('qualify_pass_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Mutation 편향",
            "",
            f"- 7거래일 cap: {mutation.get('local_search_cap_trading_days')}",
            "- earlier/later 정보는 fitness·승패·실격·qualify pass에서 제외되고 interval width mutation에만 사용됨.",
            "",
            "## 실행 안전성",
            "",
            f"- manifest gate: {bool(final.get('manifest_gate_passed'))}",
            f"- 보호 SHA 불변: {bool(final.get('protected_unchanged'))}",
            f"- daemon PID/starttime 불변: {bool(final.get('daemon_unchanged'))}",
            f"- 병렬 재현성 probe: {bool((final.get('parallel_reproducibility_probe') or {}).get('passed'))}",
            f"- 신규 fitness activation probe: {bool((final.get('new_fitness_activation_probe') or {}).get('passed'))}",
        ]
    )
    return "\n".join(lines) + "\n"


def run(out_dir: Path, seed_base: int) -> dict[str, Any]:
    started = time.time()
    protected_start = _protected_snapshot()
    daemon_start = _daemon_snapshot()
    market_frame, market_metadata = support._preflight_market_snapshot()
    manifest_gate = _manifest_gate(market_metadata)
    exit_priority = support._exit_priority_gate()
    ctx, ohlcv_metadata = support._load_snapshot_context(TICKER, market_frame)
    repro_probe = _reproducibility_probe()
    if not repro_probe.get("passed"):
        raise RuntimeError(f"parallel reproducibility probe failed: {repro_probe}")
    fitness_probe = _new_fitness_activation_probe(ctx)
    if not fitness_probe.get("passed"):
        raise RuntimeError(f"new fitness activation probe failed: {fitness_probe}")

    mod.ensure_research_experiment_header(out_dir, ticker=TICKER, seed_base=seed_base, stage="all")
    manifest = _read_json(out_dir / "manifest.json")
    manifest.update(
        {
            "runner": "scripts/research/run_stage3_aap_newfitness_official.py",
            "execution_scale": "OFFICIAL_FULL_STAGE3_NEW_FITNESS",
            "official_config": OFFICIAL_CONFIG,
            "parallel": {
                "workers": WORKERS,
                "qualify_entry_axis": "population_fitness_evaluation",
                "cross_fold_axis": "candidate_fold_backtest",
                "exit_axis": "entry_candidate",
                "validate_axis": "final_candidate",
                "rng_location": "parent_process_only",
                "merge_order": "input_index_order",
            },
            "parallel_reproducibility_probe": repro_probe,
            "new_fitness_activation_probe": fitness_probe,
            "market_snapshot_manifest_gate": manifest_gate,
            "market_snapshot_preflight": market_metadata,
            "ohlcv_snapshot": ohlcv_metadata,
            "entry_phase_exit_priority_gate": exit_priority,
            "protected_sha_start": protected_start,
            "daemon_start": daemon_start,
            "external_fetch_enabled": False,
            "auto_regenerate_enabled": False,
            "qualify_individual_policy": "preserve_all_final_populations_and_full_cross_fold_matrix",
            "stage2_executed": False,
        }
    )
    _write_json(out_dir / "manifest.json", manifest)

    qualify, _, history_rows = _run_qualify(out_dir, ctx, seed_base)
    entry_summary: dict[str, Any] | None = None
    exit_summary: dict[str, Any] | None = None
    validate_summary: dict[str, Any] | None = None
    stop_reason: str | None = None
    if not qualify["qualified"]:
        stop_reason = "qualify_failed"
    else:
        entry_summary, entry_history = _run_entry(out_dir, ctx, seed_base, call_index=4)
        history_rows.extend(entry_history)
        if not _read_jsonl(out_dir / "entry_rulebooks.jsonl"):
            stop_reason = "no_entry_survivor"
        else:
            exit_summary = parallel_resume.run_parallel_exit(
                ticker=TICKER,
                out_dir=out_dir,
                seed_base=seed_base,
                max_workers=WORKERS,
            )
            if not _read_jsonl(out_dir / "final_rulebooks.jsonl"):
                stop_reason = "no_exit_candidate"
            else:
                validate_summary = parallel_resume.run_parallel_validate(
                    ticker=TICKER,
                    out_dir=out_dir,
                    seed_base=seed_base,
                    max_workers=WORKERS,
                )

    mutation_summary = _mutation_summary(_read_jsonl(out_dir / "ga_population_history.jsonl"))
    _write_json(out_dir / "mutation_bias_summary.json", mutation_summary)
    ce_boil = _ce_boil_audit(out_dir)
    catalog_rows = _read_jsonl(out_dir / "stage3_profile_catalog.jsonl")

    protected_end = _protected_snapshot()
    daemon_end = _daemon_snapshot()
    if protected_start != protected_end:
        raise RuntimeError("protected file SHA changed during run")
    if daemon_start.get("starttime_ticks") != daemon_end.get("starttime_ticks"):
        raise RuntimeError("daemon PID was restarted or replaced during run")

    final = {
        "ticker": TICKER,
        "execution_scale": "OFFICIAL_FULL_STAGE3_NEW_FITNESS",
        "official_config": OFFICIAL_CONFIG,
        "workers": WORKERS,
        "parallel_axis": "population_fitness_evaluation",
        "parallel_reproducibility_probe": repro_probe,
        "new_fitness_activation_probe": fitness_probe,
        "manifest_gate_passed": bool(manifest_gate.get("passed")),
        "qualified": bool(qualify.get("qualified")),
        "all3_pass_count": int(qualify.get("all3_pass_count", 0) or 0),
        "pass_count_distribution": qualify.get("pass_count_distribution"),
        "entry_survivor_count": len(_read_jsonl(out_dir / "entry_rulebooks.jsonl")),
        "exit_candidate_count": len(_read_jsonl(out_dir / "final_rulebooks.jsonl")),
        "validate_survivor_count": len(catalog_rows),
        "entry_result": entry_summary,
        "exit_result": exit_summary,
        "validate_result": validate_summary,
        "ce_boil_audit": ce_boil,
        "stop_reason": stop_reason,
        "protected_sha_start": protected_start,
        "protected_sha_end": protected_end,
        "protected_unchanged": protected_start == protected_end,
        "daemon_start": daemon_start,
        "daemon_end": daemon_end,
        "daemon_unchanged": daemon_start.get("starttime_ticks") == daemon_end.get("starttime_ticks"),
        "elapsed_seconds": time.time() - started,
    }
    _write_json(out_dir / "official_final_summary.json", final)
    (out_dir / "readout.md").write_text(_build_readout(final, qualify, entry_summary, mutation_summary), encoding="utf-8")
    manifest.update(
        {
            "run_completed": True,
            "stop_reason": stop_reason,
            "protected_sha_end": protected_end,
            "protected_unchanged": True,
            "daemon_end": daemon_end,
            "daemon_unchanged": True,
            "final_counts": {
                "all3": final["all3_pass_count"],
                "entry_survivor": final["entry_survivor_count"],
                "validate_survivor": final["validate_survivor_count"],
            },
            "ce_boil_audit": ce_boil,
        }
    )
    _write_json(out_dir / "manifest.json", manifest)
    _write_sha_manifest(out_dir)
    print(json.dumps({"event": "stage3_aap_newfitness_official_done", **final}, ensure_ascii=False, default=str), flush=True)
    return final


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AAP official Stage3 with new fitness and 6 workers")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", type=int, default=SEED_BASE_DEFAULT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"output directory must be new or empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with log_path.open("w", encoding="utf-8") as log_handle:
        sys.stdout = _Tee(original_stdout, log_handle)
        sys.stderr = _Tee(original_stderr, log_handle)
        try:
            run(out_dir, int(args.seed_base))
            return 0
        except Exception as exc:
            failure = {
                "event": "stage3_aap_newfitness_official_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(json.dumps(failure, ensure_ascii=False), flush=True)
            _write_json(out_dir / "failure.json", failure)
            return 2
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if __name__ == "__main__":
    raise SystemExit(main())
