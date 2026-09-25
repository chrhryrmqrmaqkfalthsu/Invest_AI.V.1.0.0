#!/usr/bin/env python3
"""AAP trade-count-factor v3 host-local verification runner.

This wrapper keeps the official new-fitness v2 GA/backtest implementation and
only replaces observation/reporting hooks so the restored continuous
trade-count factor can be measured without changing GA order, mutation,
strict-AND evaluation, or cross-fold qualification semantics.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import inspect
import json
import math
import os
import shlex
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve()
BASE_PATH = HERE.with_name("run_stage3_aap_newfitness_v2_host.py")


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_tradecount_factor_v3_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base host runner: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()
execution_bt = base.execution_bt
genetic = base.genetic
portable = base.portable
runner = base.runner

_ORIGINAL_BUILD_CROSS_MATRIX = base._build_cross_matrix
_ORIGINAL_FOLD_BEST_TRADE_ROWS = base._fold_best_trade_rows


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _factor_bin(trade_count: int) -> str:
    count = int(trade_count)
    if count < 8:
        return "lt_8"
    if count < 12:
        return "8_11"
    if count < 20:
        return "12_19"
    if count <= 80:
        return "20_80"
    return "gt_80"


def _qualify_fail_metrics(metrics: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    config = runner.mod._base.DEFAULT_STAGE3_QUALIFY
    failures: list[str] = []
    trade_count = int(_safe_float(metrics.get("trade_count"), 0.0))
    win_rate = _safe_float(diagnostics.get("win_rate_pct"), _safe_float(metrics.get("win_rate")))
    minimum = int(execution_bt.ENTRY_FITNESS_MIN_TRADES)
    if trade_count < minimum:
        failures.append(f"entry_trade_count_below_{minimum}")
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
    matrix, original_summary, vectors = _ORIGINAL_BUILD_CROSS_MATRIX(scored_inputs)
    minimum = int(execution_bt.ENTRY_FITNESS_MIN_TRADES)
    gate_summary: dict[str, Any] = {}

    for row in matrix:
        trade_count = int(row.get("trade_count", 0) or 0)
        row["trade_count_factor"] = float(execution_bt._entry_fitness_trade_factor(trade_count))
        row["trade_count_factor_bin"] = _factor_bin(trade_count)

    for fold in ("train_1", "train_2", "train_3"):
        fold_rows = [row for row in matrix if row.get("period_label") == fold]
        count = len(fold_rows)
        support_fail = [row for row in fold_rows if int(row["trade_count"]) < minimum]
        win_fail_after_support = [
            row
            for row in fold_rows
            if int(row["trade_count"]) >= minimum
            and _safe_float(row.get("win_rate_pct")) < execution_bt.ENTRY_FITNESS_MIN_WIN_RATE_PCT
        ]
        both_pass = [
            row
            for row in fold_rows
            if int(row["trade_count"]) >= minimum
            and _safe_float(row.get("win_rate_pct")) >= execution_bt.ENTRY_FITNESS_MIN_WIN_RATE_PCT
        ]
        pass_rows = [row for row in fold_rows if bool(row.get("pass"))]
        trade_counts = [int(row["trade_count"]) for row in fold_rows]
        pass_trade_counts = [int(row["trade_count"]) for row in pass_rows]
        realized_all = [max(_safe_float(row.get("realized_loss_penalty")), 0.0) for row in fold_rows]
        realized_positive = [value for value in realized_all if value > 0.0]
        mae_all = [max(_safe_float(row.get("mae_penalty")), 0.0) for row in fold_rows]
        mae_positive = [value for value in mae_all if value > 0.0]
        factor_bins_all = Counter(_factor_bin(value) for value in trade_counts)
        factor_bins_pass = Counter(_factor_bin(value) for value in pass_trade_counts)
        gate_summary[fold] = {
            "candidate_count": count,
            "minimum_trade_count": minimum,
            "trade_count_below_minimum_count": len(support_fail),
            "trade_count_below_minimum_rate": len(support_fail) / count if count else 0.0,
            "trade_count_met_but_win_rate_below_60_count": len(win_fail_after_support),
            "trade_count_met_but_win_rate_below_60_rate": len(win_fail_after_support) / count if count else 0.0,
            "both_entry_gates_pass_count": len(both_pass),
            "both_entry_gates_pass_rate": len(both_pass) / count if count else 0.0,
            "realized_loss_penalized_count": len(realized_positive),
            "realized_loss_penalized_rate": len(realized_positive) / count if count else 0.0,
            "mean_realized_loss_penalty_among_penalized": statistics.mean(realized_positive) if realized_positive else 0.0,
            "mean_realized_loss_penalty_all": statistics.mean(realized_all) if realized_all else 0.0,
            "mae_penalized_count": len(mae_positive),
            "mae_penalized_rate": len(mae_positive) / count if count else 0.0,
            "mean_mae_penalty_among_penalized": statistics.mean(mae_positive) if mae_positive else 0.0,
            "mean_mae_penalty_all": statistics.mean(mae_all) if mae_all else 0.0,
            "trade_count_distribution": {
                "min": min(trade_counts) if trade_counts else None,
                "median": statistics.median(trade_counts) if trade_counts else None,
                "max": max(trade_counts) if trade_counts else None,
                "histogram": {str(key): value for key, value in sorted(Counter(trade_counts).items())},
                "factor_bins": dict(factor_bins_all),
            },
            "pass_trade_count_distribution": {
                "min": min(pass_trade_counts) if pass_trade_counts else None,
                "median": statistics.median(pass_trade_counts) if pass_trade_counts else None,
                "max": max(pass_trade_counts) if pass_trade_counts else None,
                "histogram": {str(key): value for key, value in sorted(Counter(pass_trade_counts).items())},
                "factor_bins": dict(factor_bins_pass),
            },
            "mean_primary_objective_pct_per_day": statistics.mean(
                _safe_float(row.get("primary_objective_pct_per_day")) for row in fold_rows
            ) if fold_rows else None,
            "mean_fitness_before_entry_gate": statistics.mean(
                _safe_float(row.get("fitness_before_entry_gate")) for row in fold_rows
            ) if fold_rows else None,
            "qualify_pass_count": len(pass_rows),
            "fail_metric_counts": dict(Counter(metric for row in fold_rows for metric in row.get("fail_metrics", []))),
            "hard_gate_partition_exclusive": True,
            "note": "MAE and realized-loss remain score penalties; support and win-rate remain hard gates.",
        }

    return matrix, {
        "folds": gate_summary,
        "pass_count_distribution": dict(original_summary.get("pass_count_distribution") or {}),
    }, vectors


def _fold_best_trade_rows(
    *,
    fold: str,
    rulebook: Any,
    result: Any,
    diagnostics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = _ORIGINAL_FOLD_BEST_TRADE_ROWS(
        fold=fold,
        rulebook=rulebook,
        result=result,
        diagnostics=diagnostics,
    )
    tape = [row for row in list(getattr(result, "daily_signal_tape", []) or []) if isinstance(row, Mapping)]
    eligible = [row for row in tape if bool(row.get("entry_eligible"))]
    joint = [row for row in eligible if row.get("strict_interval_pass") is True]
    joint_dates = [str(row.get("date")) for row in joint]
    trade_count = int(_safe_float(diagnostics.get("trade_count"), len(rows)))
    factor = _safe_float(
        diagnostics.get("trade_count_factor"),
        execution_bt._entry_fitness_trade_factor(trade_count),
    )
    shared = {
        "record_type": "trade" if rows else "fold_summary",
        "fold_best_trade_count": trade_count,
        "fold_best_trade_count_factor": factor,
        "fold_best_factor_expected": float(execution_bt._entry_fitness_trade_factor(trade_count)),
        "strict_and_entry_eligible_day_count": len(eligible),
        "strict_and_joint_pass_day_count": len(joint),
        "strict_and_joint_pass_dates": joint_dates,
    }
    if not rows:
        return [{
            "ticker": runner.TICKER,
            "stage": "qualify_fold_best",
            "period_label": fold,
            "candidate_hash": runner.mod._base.compute_rulebook_hash(rulebook),
            "fold_best_fitness_diagnostics": dict(diagnostics),
            **shared,
        }]
    for row in rows:
        row.update(shared)
    return rows


def _generation_callback(out_dir: Path, *, stage: str, fold: str, call_index: int) -> Any:
    path = out_dir / "generation_best_fitness.jsonl"

    def callback(generation: int, best: Any, average: float) -> None:
        diagnostics = dict(getattr(best, "_entry_fitness_diagnostics", {}) or {})
        trade_count = int(_safe_float(diagnostics.get("trade_count"), 0.0))
        factor = _safe_float(
            diagnostics.get("trade_count_factor"),
            execution_bt._entry_fitness_trade_factor(trade_count),
        )
        row = {
            "event": "stage3_aap_tradecount_factor_v3_ga_generation",
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
            "best_trade_count_factor": factor,
            "best_trade_count_factor_bin": _factor_bin(trade_count),
            "best_primary_after_trade_count_factor": diagnostics.get("primary_after_trade_count_factor"),
            "best_mae_penalty": diagnostics.get("mae_penalty"),
            "best_realized_loss_penalty": diagnostics.get("realized_loss_penalty"),
            "best_total_risk_penalty": diagnostics.get("total_risk_penalty"),
            "best_fitness_before_entry_gate": diagnostics.get("fitness_before_entry_gate"),
            "best_final_fitness": diagnostics.get("final_fitness"),
            "best_trade_count": trade_count,
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
    minimum = int(execution_bt.ENTRY_FITNESS_MIN_TRADES)
    factor_checks = {
        "7": execution_bt._entry_fitness_trade_factor(7) == 0.0,
        "8": math.isclose(execution_bt._entry_fitness_trade_factor(8), 0.35),
        "10": math.isclose(execution_bt._entry_fitness_trade_factor(10), 0.525),
        "12": math.isclose(execution_bt._entry_fitness_trade_factor(12), 0.70),
        "15": math.isclose(execution_bt._entry_fitness_trade_factor(15), 0.8125),
        "20": math.isclose(execution_bt._entry_fitness_trade_factor(20), 1.00),
        "81": math.isclose(execution_bt._entry_fitness_trade_factor(81), 0.996),
    }
    checks = {
        "gene_scope_marker": diagnostics.get("scope") == "entry",
        "primary_formula": diagnostics.get("primary_objective")
        == "mean(net_realized_pnl_pct / max(holding_days, 1))",
        "mae_threshold": _safe_float(diagnostics.get("mae_threshold_pct")) == -2.0,
        "realized_loss_threshold": _safe_float(diagnostics.get("realized_loss_threshold_pct")) == -1.0,
        "realized_loss_penalty_present": "realized_loss_penalty" in diagnostics,
        "profit_concentration_penalty_absent": "profit_concentration_penalty" not in diagnostics,
        "win_threshold": _safe_float(diagnostics.get("win_threshold_pct")) == 0.5,
        "minimum_trade_count": int(_safe_float(diagnostics.get("min_trade_count"), 0.0)) == minimum == 8,
        "win_rate_gate": _safe_float(diagnostics.get("win_rate_gate_pct")) == 60.0,
        "entry_gate_rule": diagnostics.get("entry_gate_rule")
        == "trade_count >= 8 AND win_rate_pct >= 60.0",
        "trade_count_factor_active": diagnostics.get("entry_fitness_trade_count_neutral") is False,
        "factor_anchors": all(factor_checks.values()),
        "mutation_hint_only": dict(diagnostics.get("exit_mutation_hint") or {}).get("fitness_input") is False,
        "stage2_legacy_defaults": set(defaults.values()) == {"legacy"},
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "factor_checks": factor_checks,
        "gene_scope_defaults": defaults,
        "sample_trade_count": int(getattr(result, "trade_count", 0) or 0),
        "sample_final_fitness": _safe_float(getattr(result, "fitness", 0.0)),
    }


def _fold_best_from_population(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in ("train_1", "train_2", "train_3"):
        candidates = [row for row in rows if row.get("fold") == fold]
        if not candidates:
            continue
        row = min(candidates, key=lambda item: int(item.get("population_rank", 10**9)))
        diagnostics = dict(row.get("entry_fitness_diagnostics") or {})
        count = int(_safe_float(diagnostics.get("trade_count"), 0.0))
        output[fold] = {
            "fitness": _safe_float(row.get("fitness")),
            "trade_count": count,
            "trade_count_factor": _safe_float(
                diagnostics.get("trade_count_factor"),
                execution_bt._entry_fitness_trade_factor(count),
            ),
            "primary_objective_pct_per_day": diagnostics.get("primary_objective_pct_per_day"),
            "mae_penalty": diagnostics.get("mae_penalty"),
            "realized_loss_penalty": diagnostics.get("realized_loss_penalty"),
            "win_rate_pct": diagnostics.get("win_rate_pct"),
        }
    return output


def _fold_best_joint_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in ("train_1", "train_2", "train_3"):
        selected = [row for row in rows if row.get("period_label") == fold]
        if not selected:
            continue
        first = selected[0]
        output[fold] = {
            "strict_and_entry_eligible_day_count": int(first.get("strict_and_entry_eligible_day_count", 0) or 0),
            "strict_and_joint_pass_day_count": int(first.get("strict_and_joint_pass_day_count", 0) or 0),
            "strict_and_joint_pass_dates": list(first.get("strict_and_joint_pass_dates") or []),
        }
    return output


def _verdict(fold_best: Mapping[str, Mapping[str, Any]], joint: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    counts = [int(fold_best.get(fold, {}).get("trade_count", 0) or 0) for fold in ("train_1", "train_2", "train_3")]
    joint_counts = [int(joint.get(fold, {}).get("strict_and_joint_pass_day_count", 0) or 0) for fold in ("train_1", "train_2", "train_3")]
    if counts and all(count > 12 for count in counts) and max(counts) >= 15:
        return "FACTOR_RESTORE_SUCCESS", "세 fold fold-best가 모두 12건을 벗어났고 최소 한 fold가 15건 이상으로 이동했다."
    stuck = [index for index, count in enumerate(counts) if count == 12]
    if stuck and all(joint_counts[index] <= 12 for index in stuck):
        return "STRICT_AND_SUPPORT_LIMIT", "12건에 남은 fold의 strict-AND joint pass day 자체가 12일 이하라 support 상한 가능성이 높다."
    if stuck:
        return "FACTOR_RESTORE_INSUFFICIENT", "strict-AND joint pass day 여유가 있는데도 fold-best가 12건에 남아 factor 압력만으로는 부족하다."
    return "FACTOR_RESTORE_PARTIAL", "12건 고정은 해소됐지만 세 fold가 일관되게 15~20 구간으로 이동하지는 않았다."


def _quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _postprocess(out_dir: Path, baseline_dir: Path, args: argparse.Namespace, original_argv: list[str]) -> None:
    current_final = _read_json(out_dir / "official_final_summary.json")
    current_qualify = _read_json(out_dir / "qualify_result.json")
    current_gate = _read_json(out_dir / "qualify_gate_bottleneck.json")
    current_population = _read_jsonl(out_dir / "qualify_population_all.jsonl")
    current_generation = _read_jsonl(out_dir / "generation_best_fitness.jsonl")
    current_trades = _read_jsonl(out_dir / "fold_best_trade_level.jsonl")

    baseline_final = _read_json(baseline_dir / "official_final_summary.json")
    baseline_qualify = _read_json(baseline_dir / "qualify_result.json")
    baseline_population = _read_jsonl(baseline_dir / "qualify_population_all.jsonl")
    baseline_generation = _read_jsonl(baseline_dir / "generation_best_fitness.jsonl")

    current_best = _fold_best_from_population(current_population)
    baseline_best = _fold_best_from_population(baseline_population)
    joint = _fold_best_joint_summary(current_trades)
    verdict_code, verdict_reason = _verdict(current_best, joint)

    current_final_generation = {
        fold: next(
            (
                row for row in current_generation
                if row.get("fold") == fold and int(row.get("generation", 0)) == 40
            ),
            {},
        )
        for fold in ("train_1", "train_2", "train_3")
    }
    baseline_final_generation = {
        fold: next(
            (
                row for row in baseline_generation
                if row.get("fold") == fold and int(row.get("generation", 0)) == 40
            ),
            {},
        )
        for fold in ("train_1", "train_2", "train_3")
    }

    env_snapshot = {
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        "PATH_prefix": os.environ.get("PATH", "").split(os.pathsep)[:4],
    }
    argv = [sys.executable, str(HERE), *original_argv]
    powershell = " ".join(["&", _quote_powershell(sys.executable), *(_quote_powershell(value) for value in [str(HERE), *original_argv])])
    launch = {
        "python_executable": sys.executable,
        "cwd": str(Path.cwd()),
        "argv": argv,
        "environment": env_snapshot,
        "powershell_command": powershell,
        "host_local_parent": True,
        "local_process_workers": int(args.workers),
        "inter_machine_candidate_communication": False,
    }

    comparison = {
        "baseline_dir": str(baseline_dir),
        "current_dir": str(out_dir),
        "seed_base": int(args.seed_base),
        "pass_count_distribution": {
            "previous_v2": dict(baseline_qualify.get("pass_count_distribution") or {}),
            "factor_restore": dict(current_qualify.get("pass_count_distribution") or {}),
        },
        "fold_pass_counts": {
            "previous_v2": dict(baseline_qualify.get("fold_pass_counts") or {}),
            "factor_restore": dict(current_qualify.get("fold_pass_counts") or {}),
        },
        "fold_best": {
            "previous_v2": baseline_best,
            "factor_restore": current_best,
        },
        "final_generation": {
            "previous_v2": baseline_final_generation,
            "factor_restore": current_final_generation,
        },
        "strict_and_joint_pass": joint,
        "gate_bottleneck": dict(current_gate.get("folds") or {}),
        "elapsed_seconds": {
            "previous_v2": baseline_final.get("elapsed_seconds"),
            "factor_restore": current_final.get("elapsed_seconds"),
        },
        "verdict_code": verdict_code,
        "verdict_reason": verdict_reason,
    }
    _write_json(out_dir / "launch_command.json", launch)
    _write_json(out_dir / "trade_count_factor_comparison.json", comparison)
    _write_json(out_dir / "fold_best_summary.json", {
        "fold_best": current_best,
        "strict_and_joint_pass": joint,
        "verdict_code": verdict_code,
        "verdict_reason": verdict_reason,
    })

    previous_pdist = dict(baseline_qualify.get("pass_count_distribution") or {})
    current_pdist = dict(current_qualify.get("pass_count_distribution") or {})
    previous_pass = dict(baseline_qualify.get("fold_pass_counts") or {})
    current_pass = dict(current_qualify.get("fold_pass_counts") or {})

    lines = [
        "# AAP 거래수 factor 복원 재학습 검증 readout",
        "",
        f"- source commit: `{args.source_git_commit}`",
        f"- seed: `{args.seed_base}`",
        f"- host: `{current_final.get('host_name')}`",
        f"- 실행: 독립 notebook parent + local `{args.workers}` process",
        "- inter-machine candidate communication: false",
        "- qualify: population 100 / generations 40 × train_1·train_2·train_3",
        "- auto-fetch/regenerate: disabled",
        f"- 판정: **{verdict_code}** — {verdict_reason}",
        "",
        "## 정확한 실행 진입",
        "",
        "`launch_command.json`의 `argv`와 환경을 원본 기록으로 사용한다.",
        "",
        "```powershell",
        f"$env:PYTHONPATH={_quote_powershell(env_snapshot['PYTHONPATH'])}",
        powershell,
        "```",
        "",
        "## 이전 v2 직접 비교",
        "",
        "| 지표 | 이전 v2 | 이번 factor 복원 |",
        "|---|---:|---:|",
        f"| all3 / all2 / all1 / all0 | {previous_pdist.get('all3', 0)} / {previous_pdist.get('all2', 0)} / {previous_pdist.get('all1', 0)} / {previous_pdist.get('all0', 0)} | {current_pdist.get('all3', 0)} / {current_pdist.get('all2', 0)} / {current_pdist.get('all1', 0)} / {current_pdist.get('all0', 0)} |",
        f"| train_1 / train_2 / train_3 pass | {previous_pass.get('train_1', 0)} / {previous_pass.get('train_2', 0)} / {previous_pass.get('train_3', 0)} | {current_pass.get('train_1', 0)} / {current_pass.get('train_2', 0)} / {current_pass.get('train_3', 0)} |",
        f"| fold-best 거래수 | {baseline_best.get('train_1', {}).get('trade_count')} / {baseline_best.get('train_2', {}).get('trade_count')} / {baseline_best.get('train_3', {}).get('trade_count')} | {current_best.get('train_1', {}).get('trade_count')} / {current_best.get('train_2', {}).get('trade_count')} / {current_best.get('train_3', {}).get('trade_count')} |",
        f"| fold-best fitness | {baseline_best.get('train_1', {}).get('fitness')} / {baseline_best.get('train_2', {}).get('fitness')} / {baseline_best.get('train_3', {}).get('fitness')} | {current_best.get('train_1', {}).get('fitness')} / {current_best.get('train_2', {}).get('fitness')} / {current_best.get('train_3', {}).get('fitness')} |",
        f"| 전체 소요시간(초) | {baseline_final.get('elapsed_seconds')} | {current_final.get('elapsed_seconds')} |",
        "",
        "## Fold-best 거래수·factor·strict-AND support",
        "",
        "| fold | 거래수 | 실제 factor | 기대 factor | fitness | strict-AND joint pass day |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for fold in ("train_1", "train_2", "train_3"):
        best = current_best.get(fold, {})
        count = int(best.get("trade_count", 0) or 0)
        lines.append(
            f"| {fold} | {count} | {best.get('trade_count_factor')} | "
            f"{execution_bt._entry_fitness_trade_factor(count)} | {best.get('fitness')} | "
            f"{joint.get(fold, {}).get('strict_and_joint_pass_day_count')} |"
        )

    lines.extend([
        "",
        "## Fold별 gate 병목·pass 거래수 분포",
        "",
        "| fold | 후보 | 거래<8 | support 충족·승률<60 | 두 entry gate 통과 | 실현손실 감점 | MAE 감점 | pass 거래수 histogram | factor bins(전체/pass) |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for fold in ("train_1", "train_2", "train_3"):
        row = dict((current_gate.get("folds") or {}).get(fold) or {})
        pass_dist = dict(row.get("pass_trade_count_distribution") or {})
        all_dist = dict(row.get("trade_count_distribution") or {})
        lines.append(
            f"| {fold} | {row.get('candidate_count', 0)} | "
            f"{row.get('trade_count_below_minimum_count', 0)} ({_safe_float(row.get('trade_count_below_minimum_rate')):.2%}) | "
            f"{row.get('trade_count_met_but_win_rate_below_60_count', 0)} ({_safe_float(row.get('trade_count_met_but_win_rate_below_60_rate')):.2%}) | "
            f"{row.get('both_entry_gates_pass_count', 0)} ({_safe_float(row.get('both_entry_gates_pass_rate')):.2%}) | "
            f"{row.get('realized_loss_penalized_count', 0)} ({_safe_float(row.get('realized_loss_penalized_rate')):.2%}), 평균 {_safe_float(row.get('mean_realized_loss_penalty_among_penalized')):.6f} | "
            f"{row.get('mae_penalized_count', 0)} ({_safe_float(row.get('mae_penalized_rate')):.2%}), 평균 {_safe_float(row.get('mean_mae_penalty_among_penalized')):.6f} | "
            f"`{json.dumps(pass_dist.get('histogram') or {}, ensure_ascii=False, sort_keys=True)}` | "
            f"`{json.dumps(all_dist.get('factor_bins') or {}, ensure_ascii=False, sort_keys=True)}` / `{json.dumps(pass_dist.get('factor_bins') or {}, ensure_ascii=False, sort_keys=True)}` |"
        )

    lines.extend([
        "",
        "## 산출 로그",
        "",
        "- `generation_best_fitness.jsonl`: 세대별 best/mean fitness, 거래수, 실제 factor",
        "- `qualify_population_all.jsonl`: 최종 fold population 전체 diagnostics",
        "- `qualify_cross_fold_matrix.jsonl`: 후보×fold pass, 거래수, factor, gate 실패",
        "- `fold_best_trade_level.jsonl`: 진입·청산 가격/일자, 청산사유, 보유일, 실현수익, MAE, 일수익, +0.5% 승패, 5-feature snapshot, joint-pass day",
        "- `qualify_gate_bottleneck.json`: hard gate·penalty·factor-bin·pass histogram",
        "",
        "## 안전성",
        "",
        f"- manifest gate: {bool(current_final.get('manifest_gate_passed'))}",
        f"- 보호 SHA 불변: {bool(current_final.get('protected_unchanged'))}",
        f"- daemon 불변: {bool(current_final.get('daemon_unchanged'))}",
        f"- 병렬 재현성 probe: {bool((current_final.get('parallel_reproducibility_probe') or {}).get('passed'))}",
        f"- factor activation probe: {bool((current_final.get('new_fitness_activation_probe') or {}).get('passed'))}",
        f"- source git commit: `{args.source_git_commit}`",
    ])
    (out_dir / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name in ("manifest.json", "official_final_summary.json"):
        path = out_dir / name
        payload = _read_json(path)
        payload["launch_command"] = launch
        payload["trade_count_factor_comparison"] = comparison
        payload["verdict_code"] = verdict_code
        payload["verdict_reason"] = verdict_reason
        _write_json(path, payload)
    runner._write_sha_manifest(out_dir)


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--baseline-dir", required=True)
    known, base_argv = parser.parse_known_args(raw)
    base_args = base.parse_args(base_argv)
    base_args.baseline_dir = known.baseline_dir
    return base_args, base_argv


def main(argv: list[str] | None = None) -> int:
    original_argv = list(sys.argv[1:] if argv is None else argv)
    args, base_argv = parse_args(original_argv)

    base._qualify_fail_metrics = _qualify_fail_metrics
    base._build_cross_matrix = _build_cross_matrix
    base._fold_best_trade_rows = _fold_best_trade_rows
    base._generation_callback = _generation_callback
    base._new_fitness_activation_probe = _new_fitness_activation_probe

    result = int(base.main(base_argv))
    if result == 0:
        _postprocess(
            Path(args.out_dir),
            Path(args.baseline_dir),
            args,
            original_argv,
        )
    return result


if __name__ == "__main__":
    base.mp.freeze_support()
    raise SystemExit(main())
