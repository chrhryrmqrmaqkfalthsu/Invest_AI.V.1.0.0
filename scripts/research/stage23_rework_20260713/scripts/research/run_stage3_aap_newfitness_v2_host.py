#!/usr/bin/env python3
"""Host-local AAP new-fitness v2 official runner.

The same file runs as an independent parent on VM or Windows notebook.  It
keeps all GA RNG in that local parent, evaluates fitness in local processes,
merges by candidate input index, and never distributes individual candidates
between machines.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import inspect
import json
import math
import multiprocessing as mp
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import cloudpickle

HERE = Path(__file__).resolve()
RUNNER_PATH = HERE.with_name("run_stage3_aap_newfitness_official.py")


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_newfitness_v2_official", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()
from engine.learning import execution_mode_backtest as execution_bt  # noqa: E402
from engine.learning import genetic  # noqa: E402
from engine.learning import genetic_parallel_portable as portable  # noqa: E402

_CROSS_CTX: dict[str, Any] | None = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _cross_worker_init(ctx_payload: bytes) -> None:
    global _CROSS_CTX
    _CROSS_CTX = cloudpickle.loads(ctx_payload)


def _cross_worker(task: tuple[int, str, dict[str, Any], dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    index, candidate_hash, rulebook_payload, split = task
    if _CROSS_CTX is None:
        raise RuntimeError("portable cross-fold context is not initialized")
    rb = runner.Rulebook.from_dict(rulebook_payload)
    result, diagnostics = runner._entry_scope_result(rb, _CROSS_CTX, split)
    metrics = dict(runner.mod._base.result_metrics(result))
    trades = list(getattr(result, "trades", []) or [])
    return index, {
        "candidate_hash": candidate_hash,
        "period_label": str(split["label"]),
        "metrics": metrics,
        "entry_fitness_diagnostics": diagnostics,
        "entry_dates": sorted(runner.mod._base.entry_dates_from_trades(trades)),
    }


def _parallel_cross_evaluate(
    candidates: list[tuple[str, Any]],
    splits: list[dict[str, Any]],
    ctx: dict[str, Any],
    workers: int,
) -> list[dict[str, Any]]:
    tasks: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
    index = 0
    for candidate_hash, rb in candidates:
        payload = rb.to_dict()
        for split in splits:
            tasks.append((index, candidate_hash, payload, dict(split)))
            index += 1
    start_method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    ctx_payload = cloudpickle.dumps(ctx)
    with ProcessPoolExecutor(
        max_workers=max(1, int(workers)),
        mp_context=mp.get_context(start_method),
        initializer=_cross_worker_init,
        initargs=(ctx_payload,),
    ) as pool:
        results = list(pool.map(_cross_worker, tasks, chunksize=1))
    results.sort(key=lambda row: row[0])
    return [row[1] for row in results]


def _qualify_fail_metrics(metrics: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    config = runner.mod._base.DEFAULT_STAGE3_QUALIFY
    failures: list[str] = []
    trade_count = int(_safe_float(metrics.get("trade_count"), 0.0))
    win_rate = _safe_float(diagnostics.get("win_rate_pct"), _safe_float(metrics.get("win_rate")))
    if trade_count < execution_bt.ENTRY_FITNESS_MIN_TRADES:
        failures.append("entry_trade_count_below_12")
    elif win_rate < execution_bt.ENTRY_FITNESS_MIN_WIN_RATE_PCT:
        failures.append("entry_win_rate_below_60")
    if _safe_float(metrics.get("member_score")) < float(config.min_member_score):
        failures.append("member_score")
    if _safe_float(metrics.get("expectancy_pct")) < float(config.qualify_min_expectancy_pct):
        failures.append("expectancy_pct")
    return failures


def _build_cross_matrix(
    scored_inputs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    by_fold: dict[str, list[dict[str, Any]]] = defaultdict(list)
    diag_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entry_dates_by_key: dict[tuple[str, str], list[str]] = {}
    for row in scored_inputs:
        fold = str(row["period_label"])
        candidate_hash = str(row["candidate_hash"])
        by_fold[fold].append(
            {
                "ticker": runner.TICKER,
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
    for split in runner.mod._base.TRAIN_SPLITS:
        fold = str(split["label"])
        scored = runner.mod._base._score_period_candidates(by_fold[fold])
        fold_rows: list[dict[str, Any]] = []
        for row in scored:
            candidate_hash = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            metrics["member_score"] = _safe_float(row.get("oos_member_score"))
            metrics["fitness"] = _safe_float(row.get("fitness"))
            diagnostics = diag_by_key[(candidate_hash, fold)]
            fail_metrics = _qualify_fail_metrics(metrics, diagnostics)
            trade_count = int(_safe_float(diagnostics.get("trade_count"), metrics.get("trade_count", 0)))
            win_rate = _safe_float(diagnostics.get("win_rate_pct"), metrics.get("win_rate", 0.0))
            output = {
                "ticker": runner.TICKER,
                "candidate_hash": candidate_hash,
                "period_label": fold,
                "pass": not fail_metrics,
                "fail_metrics": fail_metrics,
                "trade_count": trade_count,
                "win_rate_pct": win_rate,
                "win_threshold_pct": _safe_float(diagnostics.get("win_threshold_pct"), 0.5),
                "win_count": int(_safe_float(diagnostics.get("win_count"), 0.0)),
                "loss_count": int(_safe_float(diagnostics.get("loss_count"), 0.0)),
                "trade_count_gate_pass": bool(diagnostics.get("trade_count_gate_pass")),
                "win_rate_threshold_pass": bool(diagnostics.get("win_rate_threshold_pass")),
                "entry_gate_pass": bool(diagnostics.get("entry_gate_pass")),
                "expectancy_pct": _safe_float(metrics.get("expectancy_pct")),
                "profit_factor": _safe_float(metrics.get("profit_factor")),
                "max_drawdown_pct": _safe_float(metrics.get("max_drawdown_pct")),
                "member_score": _safe_float(metrics.get("member_score")),
                "primary_objective_pct_per_day": _safe_float(diagnostics.get("primary_objective_pct_per_day")),
                "mae_penalty": _safe_float(diagnostics.get("mae_penalty")),
                "mae_breach_trade_count": int(_safe_float(diagnostics.get("mae_breach_trade_count"), 0.0)),
                "realized_loss_penalty": _safe_float(diagnostics.get("realized_loss_penalty")),
                "realized_loss_breach_trade_count": int(
                    _safe_float(diagnostics.get("realized_loss_breach_trade_count"), 0.0)
                ),
                "total_risk_penalty": _safe_float(diagnostics.get("total_risk_penalty")),
                "fitness_before_entry_gate": _safe_float(diagnostics.get("fitness_before_entry_gate")),
                "final_fitness": _safe_float(
                    diagnostics.get("final_fitness"),
                    _safe_float(metrics.get("fitness")),
                ),
                "disqualified": bool(diagnostics.get("disqualified")),
                "disqualification_reasons": list(diagnostics.get("disqualification_reasons") or []),
                "mdd_risk": dict(diagnostics.get("mdd_risk") or {}),
                "entry_exit_mutation_hint": dict(diagnostics.get("exit_mutation_hint") or {}),
                "entry_dates": entry_dates_by_key[(candidate_hash, fold)],
            }
            matrix.append(output)
            fold_rows.append(output)

        trade_counts = [row["trade_count"] for row in fold_rows]
        realized_all = [max(row["realized_loss_penalty"], 0.0) for row in fold_rows]
        realized_positive = [value for value in realized_all if value > 0.0]
        mae_all = [max(row["mae_penalty"], 0.0) for row in fold_rows]
        mae_positive = [value for value in mae_all if value > 0.0]
        support_fail = [row for row in fold_rows if row["trade_count"] < 12]
        win_fail_after_support = [
            row
            for row in fold_rows
            if row["trade_count"] >= 12 and row["win_rate_pct"] < 60.0
        ]
        both_pass = [
            row
            for row in fold_rows
            if row["trade_count"] >= 12 and row["win_rate_pct"] >= 60.0
        ]
        count = len(fold_rows)
        gate_summary[fold] = {
            "candidate_count": count,
            "trade_count_below_12_count": len(support_fail),
            "trade_count_below_12_rate": len(support_fail) / count if count else 0.0,
            "trade_count_met_but_win_rate_below_60_count": len(win_fail_after_support),
            "trade_count_met_but_win_rate_below_60_rate": (
                len(win_fail_after_support) / count if count else 0.0
            ),
            "both_entry_gates_pass_count": len(both_pass),
            "both_entry_gates_pass_rate": len(both_pass) / count if count else 0.0,
            "realized_loss_penalized_count": len(realized_positive),
            "realized_loss_penalized_rate": len(realized_positive) / count if count else 0.0,
            "mean_realized_loss_penalty_among_penalized": (
                statistics.mean(realized_positive) if realized_positive else 0.0
            ),
            "mean_realized_loss_penalty_all": statistics.mean(realized_all) if realized_all else 0.0,
            "mae_penalized_count": len(mae_positive),
            "mae_penalized_rate": len(mae_positive) / count if count else 0.0,
            "mean_mae_penalty_among_penalized": statistics.mean(mae_positive) if mae_positive else 0.0,
            "mean_mae_penalty_all": statistics.mean(mae_all) if mae_all else 0.0,
            "trade_count_distribution": {
                "min": min(trade_counts) if trade_counts else None,
                "median": statistics.median(trade_counts) if trade_counts else None,
                "max": max(trade_counts) if trade_counts else None,
                "count_12_13": sum(value in {12, 13} for value in trade_counts),
                "rate_12_13": (
                    sum(value in {12, 13} for value in trade_counts) / count if count else 0.0
                ),
            },
            "mean_primary_objective_pct_per_day": (
                statistics.mean(row["primary_objective_pct_per_day"] for row in fold_rows)
                if fold_rows
                else None
            ),
            "mean_fitness_before_entry_gate": (
                statistics.mean(row["fitness_before_entry_gate"] for row in fold_rows)
                if fold_rows
                else None
            ),
            "qualify_pass_count": sum(1 for row in fold_rows if row["pass"]),
            "fail_metric_counts": dict(
                Counter(metric for row in fold_rows for metric in row["fail_metrics"])
            ),
            "hard_gate_partition_exclusive": True,
            "note": "MAE and realized-loss are score penalties; support and win-rate are hard gates.",
        }

    by_candidate: dict[str, dict[str, bool]] = defaultdict(dict)
    for row in matrix:
        by_candidate[row["candidate_hash"]][row["period_label"]] = bool(row["pass"])
    vectors: list[dict[str, Any]] = []
    distribution = Counter()
    for candidate_hash in sorted(by_candidate):
        vector = {
            str(split["label"]): bool(
                by_candidate[candidate_hash].get(str(split["label"]), False)
            )
            for split in runner.mod._base.TRAIN_SPLITS
        }
        pass_count = sum(vector.values())
        distribution[pass_count] += 1
        vectors.append(
            {
                "ticker": runner.TICKER,
                "candidate_hash": candidate_hash,
                "pass_count": pass_count,
                "pass_vector": vector,
            }
        )
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
    rulebook: Any,
    result: Any,
    diagnostics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidate_hash = runner.mod._base.compute_rulebook_hash(rulebook)
    for index, trade in enumerate(list(getattr(result, "trades", []) or []), 1):
        tape = trade.get("entry_signal_tape") if isinstance(trade.get("entry_signal_tape"), Mapping) else {}
        pnl = _safe_float(trade.get("pnl_pct"))
        holding = max(int(_safe_float(trade.get("holding_days"), 0.0)), 1)
        rows.append(
            {
                "ticker": runner.TICKER,
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
                "win_threshold_pct": execution_bt.ENTRY_FITNESS_WIN_THRESHOLD_PCT,
                "win_after_cost": bool(pnl > execution_bt.ENTRY_FITNESS_WIN_THRESHOLD_PCT),
                "fitness_win_after_cost_logged": bool(
                    trade.get("fitness_win_after_cost", pnl > execution_bt.ENTRY_FITNESS_WIN_THRESHOLD_PCT)
                ),
                "realized_loss_breach_pct_point": _safe_float(
                    trade.get("realized_loss_breach_pct_point")
                ),
                "entry_features": dict(tape.get("entry_features") or {}),
                "interval_checks": dict(tape.get("interval_checks") or {}),
                "strict_interval_pass": tape.get("strict_interval_pass"),
                "entry_exit_local_search": dict(trade.get("entry_exit_local_search") or {}),
                "fold_best_fitness_diagnostics": dict(diagnostics),
            }
        )
    return rows


def _generation_callback(out_dir: Path, *, stage: str, fold: str, call_index: int) -> Any:
    path = out_dir / "generation_best_fitness.jsonl"

    def callback(generation: int, best: Any, average: float) -> None:
        diagnostics = dict(getattr(best, "_entry_fitness_diagnostics", {}) or {})
        row = {
            "event": "stage3_aap_newfitness_v2_ga_generation",
            "stage": stage,
            "fold": fold,
            "call_index": call_index,
            "gene_scope": "entry",
            "evaluation_workers": runner.WORKERS,
            "parallel_axis": "population_fitness_evaluation",
            "merge_order": "input_index_order",
            "generation": int(generation),
            "best_hash": runner.mod._base.compute_rulebook_hash(best),
            "best_fitness": _safe_float(getattr(best, "fitness", 0.0)),
            "mean_fitness": float(average),
            "best_primary_objective_pct_per_day": diagnostics.get("primary_objective_pct_per_day"),
            "best_mae_penalty": diagnostics.get("mae_penalty"),
            "best_realized_loss_penalty": diagnostics.get("realized_loss_penalty"),
            "best_total_risk_penalty": diagnostics.get("total_risk_penalty"),
            "best_fitness_before_entry_gate": diagnostics.get("fitness_before_entry_gate"),
            "best_final_fitness": diagnostics.get("final_fitness"),
            "best_trade_count": diagnostics.get("trade_count"),
            "best_trade_count_gate_pass": diagnostics.get("trade_count_gate_pass"),
            "best_win_rate_pct": diagnostics.get("win_rate_pct"),
            "best_win_rate_threshold_pass": diagnostics.get("win_rate_threshold_pass"),
            "best_entry_gate_pass": diagnostics.get("entry_gate_pass"),
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        runner._append_jsonl(path, row)
        print(json.dumps(row, ensure_ascii=False), flush=True)

    return callback


def _new_fitness_activation_probe(ctx: dict[str, Any]) -> dict[str, Any]:
    split = dict(runner.mod._base.TRAIN_SPLITS[0])
    domain = runner.mod.build_entry_feature_domain(ctx, start=split["start"], end=split["end"])
    rb = genetic.random_rulebook(
        copy.deepcopy(ctx["base_rulebook"]),
        gene_scope="entry",
        entry_feature_domain=domain,
    )
    result, diagnostics = runner._entry_scope_result(rb, ctx, split)
    defaults = {
        name: inspect.signature(getattr(genetic, name)).parameters["gene_scope"].default
        for name in ("random_rulebook", "mutate", "crossover")
    }
    defaults["run_ga"] = inspect.signature(portable.run_ga).parameters["gene_scope"].default
    checks = {
        "gene_scope_marker": diagnostics.get("scope") == "entry",
        "primary_formula": diagnostics.get("primary_objective")
        == "mean(net_realized_pnl_pct / max(holding_days, 1))",
        "mae_threshold": _safe_float(diagnostics.get("mae_threshold_pct")) == -2.0,
        "realized_loss_threshold": _safe_float(diagnostics.get("realized_loss_threshold_pct")) == -1.0,
        "realized_loss_penalty_present": "realized_loss_penalty" in diagnostics,
        "win_threshold": _safe_float(diagnostics.get("win_threshold_pct")) == 0.5,
        "minimum_trade_count": int(_safe_float(diagnostics.get("min_trade_count"), 0.0)) == 12,
        "win_rate_gate": _safe_float(diagnostics.get("win_rate_gate_pct")) == 60.0,
        "entry_gate_rule": diagnostics.get("entry_gate_rule")
        == "trade_count >= 12 AND win_rate_pct >= 60.0",
        "mutation_hint_only": dict(diagnostics.get("exit_mutation_hint") or {}).get("fitness_input") is False,
        "stage2_legacy_defaults": set(defaults.values()) == {"legacy"},
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "gene_scope_defaults": defaults,
        "sample_trade_count": int(getattr(result, "trade_count", 0) or 0),
        "sample_final_fitness": _safe_float(getattr(result, "fitness", 0.0)),
    }


def _reproducibility_probe() -> dict[str, Any]:
    base_rb = runner.Rulebook(ticker="AAP_REPRO", asset_type="us_stock", direction="long")

    def evaluate(rulebook: Any) -> float:
        return (
            -abs(_safe_float(getattr(rulebook, "signal_threshold", 0.0)) - 2.5) * 10.0
            - abs(_safe_float(getattr(rulebook, "base_position_ratio", 0.0)) - 0.7) * 20.0
            + 50.0
        )

    cfg = runner.mod._base.make_ga_config(population=12, generations=3, seed=2026071499)
    return portable.reproducibility_probe(
        base_rb,
        evaluate,
        cfg,
        parallel_workers=min(int(runner.WORKERS), 12),
    )


def _build_readout(
    final: Mapping[str, Any],
    qualify: Mapping[str, Any],
    entry: Mapping[str, Any] | None,
    mutation: Mapping[str, Any],
) -> str:
    pdist = dict(qualify.get("pass_count_distribution") or {})
    lines = [
        "# AAP 새 fitness v2 정식 독립 실행 readout",
        "",
        f"- host role: {os.environ.get('KINGMAKER_HOST_ROLE', 'unknown')}",
        f"- workers: {runner.WORKERS}",
        "- 규모: qualify 100/40 × 3 fold; all3 발생 시 entry/exit/validate 연속 실행",
        "- RNG: 장비별 독립 parent에서만 소비, fitness만 로컬 process 분산",
        "- 병합: candidate input index 순서",
        "- 시장 기준: 사용 가능한 root snapshot 마지막 거래일로 고정",
        f"- qualify 통과: {bool(qualify.get('qualified'))}",
        f"- all3/all2/all1/all0: {pdist.get('all3', 0)}/{pdist.get('all2', 0)}/{pdist.get('all1', 0)}/{pdist.get('all0', 0)}",
        f"- entry survivor: {int((entry or {}).get('selected_count', 0) or 0)}",
        f"- validate survivor: {int(final.get('validate_survivor_count', 0) or 0)}",
        f"- CE/BOIL zero: {bool((final.get('ce_boil_audit') or {}).get('ce_boil_zero'))}",
        "",
        "## Fold별 hard gate와 penalty",
        "",
        "| fold | 후보 | 거래<12 | 거래충족·승률<60 | 두 gate 통과 | 실현손실 감점 | MAE 감점 | 거래 min/med/max | 12~13 비율 | qualify pass |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for fold in ("train_1", "train_2", "train_3"):
        row = dict((qualify.get("gate_bottleneck") or {}).get(fold) or {})
        dist = dict(row.get("trade_count_distribution") or {})
        lines.append(
            f"| {fold} | {row.get('candidate_count', 0)} | "
            f"{row.get('trade_count_below_12_count', 0)} ({float(row.get('trade_count_below_12_rate', 0.0)):.2%}) | "
            f"{row.get('trade_count_met_but_win_rate_below_60_count', 0)} ({float(row.get('trade_count_met_but_win_rate_below_60_rate', 0.0)):.2%}) | "
            f"{row.get('both_entry_gates_pass_count', 0)} ({float(row.get('both_entry_gates_pass_rate', 0.0)):.2%}) | "
            f"{row.get('realized_loss_penalized_count', 0)} ({float(row.get('realized_loss_penalized_rate', 0.0)):.2%}), 평균 {float(row.get('mean_realized_loss_penalty_among_penalized', 0.0)):.6f} | "
            f"{row.get('mae_penalized_count', 0)} ({float(row.get('mae_penalized_rate', 0.0)):.2%}), 평균 {float(row.get('mean_mae_penalty_among_penalized', 0.0)):.6f} | "
            f"{dist.get('min')}/{dist.get('median')}/{dist.get('max')} | "
            f"{float(dist.get('rate_12_13', 0.0)):.2%} | {row.get('qualify_pass_count', 0)} |"
        )
    lines.extend(
        [
            "",
            "## 새 fitness 활성",
            "",
            "- 주목표: 평균 비용차감 실현수익 / 보유일",
            "- 실현손실 벌점: avg(max(0, -1.0 - pnl_pct))",
            "- 승: 비용차감 실현수익 > 0.5%",
            "- hard gate: 거래수 >= 12 AND 승률 >= 60%",
            "- MAE 벌점과 실현손실 벌점은 독립 차감",
            "- mutation bias는 fitness·gate가 아니라 interval width mutation에만 사용",
            "",
            "## 안전성",
            "",
            f"- manifest gate: {bool(final.get('manifest_gate_passed'))}",
            f"- 보호 SHA 불변: {bool(final.get('protected_unchanged'))}",
            f"- daemon proxy/starttime 불변: {bool(final.get('daemon_unchanged'))}",
            f"- 병렬 재현성 probe: {bool((final.get('parallel_reproducibility_probe') or {}).get('passed'))}",
            f"- 새 fitness activation probe: {bool((final.get('new_fitness_activation_probe') or {}).get('passed'))}",
        ]
    )
    return "\n".join(lines) + "\n"


def _patch_market_cutoff(cutoff_date: date) -> None:
    original = runner.mod._primary_freshness

    def available_cutoff_freshness(last_date: date, *, as_of_date: date | None = None) -> dict[str, Any]:
        if last_date != cutoff_date:
            raise RuntimeError(
                f"available-data cutoff mismatch: expected snapshot last_date={cutoff_date}, actual={last_date}"
            )
        result = original(last_date, as_of_date=cutoff_date + timedelta(days=1))
        result.update(
            {
                "basis": "user_approved_available_snapshot_last_session",
                "user_approved_available_data_only": True,
                "available_data_cutoff_date": cutoff_date.isoformat(),
                "wall_clock_new_york_date": datetime.now().astimezone().date().isoformat(),
            }
        )
        return result

    runner.mod._RESEARCH_MARKET_SNAPSHOT_CACHE.clear()
    runner.mod._primary_freshness = available_cutoff_freshness


def _configure(args: argparse.Namespace) -> None:
    runner.WORKERS = int(args.workers)
    os.environ["KINGMAKER_HOST_ROLE"] = str(args.host_role)
    _patch_market_cutoff(date.fromisoformat(args.market_cutoff_date))
    runner.genetic_parallel.run_ga = portable.run_ga
    runner.genetic_parallel.reproducibility_probe = portable.reproducibility_probe
    runner._parallel_cross_evaluate = _parallel_cross_evaluate
    runner._qualify_fail_metrics = _qualify_fail_metrics
    runner._build_cross_matrix = _build_cross_matrix
    runner._fold_best_trade_rows = _fold_best_trade_rows
    runner._generation_callback = _generation_callback
    runner._new_fitness_activation_probe = _new_fitness_activation_probe
    runner._reproducibility_probe = _reproducibility_probe
    runner._build_readout = _build_readout

    if args.host_role == "notebook":
        protected = json.loads(args.protected_snapshot_json)
        daemon = json.loads(args.daemon_snapshot_json)
        runner._protected_snapshot = lambda: copy.deepcopy(protected)
        runner._daemon_snapshot = lambda: copy.deepcopy(daemon)
        original_exit = runner.parallel_resume.run_parallel_exit
        original_validate = runner.parallel_resume.run_parallel_validate
        runner.parallel_resume.run_parallel_exit = (
            lambda *, ticker, out_dir, seed_base, max_workers: original_exit(
                ticker=ticker,
                out_dir=out_dir,
                seed_base=seed_base,
                max_workers=1,
            )
        )
        runner.parallel_resume.run_parallel_validate = (
            lambda *, ticker, out_dir, seed_base, max_workers: original_validate(
                ticker=ticker,
                out_dir=out_dir,
                seed_base=seed_base,
                max_workers=1,
            )
        )

    original_run = runner.run

    def run_with_host_metadata(out_dir: Path, seed_base: int) -> dict[str, Any]:
        result = original_run(out_dir, seed_base)
        for name in ("manifest.json", "official_final_summary.json"):
            path = out_dir / name
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.update(
                {
                    "independent_host_role": args.host_role,
                    "host_name": os.environ.get("COMPUTERNAME") or os.uname().nodename,
                    "requested_local_workers": int(args.workers),
                    "market_available_cutoff_date": args.market_cutoff_date,
                    "inter_machine_candidate_communication": False,
                    "notebook_exit_validate_workers": 1 if args.host_role == "notebook" else int(args.workers),
                    "source_git_commit": args.source_git_commit,
                }
            )
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        runner._write_sha_manifest(out_dir)
        return result

    runner.run = run_with_host_metadata


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Host-local AAP new-fitness v2 official run")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", type=int, default=2026071401)
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--host-role", choices=("vm", "notebook"), required=True)
    parser.add_argument("--market-cutoff-date", required=True)
    parser.add_argument("--protected-snapshot-json", default="{}")
    parser.add_argument("--daemon-snapshot-json", default="{}")
    parser.add_argument("--source-git-commit", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _configure(args)
    return int(
        runner.main(
            [
                "--out-dir",
                args.out_dir,
                "--seed-base",
                str(args.seed_base),
            ]
        )
    )


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
