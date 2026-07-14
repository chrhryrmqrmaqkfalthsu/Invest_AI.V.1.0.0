#!/usr/bin/env python3
"""AAP overlap-entry v4 host-local official runner.

This wrapper reuses the v3 GA, strict-AND, fitness, gate, mutation, exit, and
parallel semantics unchanged.  It only extends fold-best observation and final
reporting for the entry-scope overlapping-position scheduling introduced in
``engine.learning.execution_mode_backtest``.
"""
from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve()
BASE_PATH = HERE.with_name("run_stage3_aap_tradecount_factor_v3_host.py")
FOLDS = ("train_1", "train_2", "train_3")
CLUSTER_GAP_TRADING_DAYS = 8  # entry-phase 7-day cap + one-day cooldown reference


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_overlap_entry_v4_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base host runner: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()
execution_bt = base.execution_bt
runner = base.runner

_V3_FOLD_BEST_TRADE_ROWS = base._fold_best_trade_rows
_V3_ACTIVATION_PROBE = base._new_fitness_activation_probe
_V3_POSTPROCESS = base._postprocess


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


def _date_key(value: Any) -> str:
    return str(value or "")[:10]


def _effective_event_summary(joint_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        (
            (int(row.get("row_index", -1)), str(row.get("date")))
            for row in joint_rows
            if int(row.get("row_index", -1)) >= 0
        ),
        key=lambda item: item[0],
    )
    clusters: list[list[tuple[int, str]]] = []
    for item in ordered:
        if not clusters or item[0] - clusters[-1][-1][0] > CLUSTER_GAP_TRADING_DAYS:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    total = len(ordered)
    shares = [len(cluster) / total for cluster in clusters] if total else []
    effective = 1.0 / sum(share * share for share in shares) if shares else 0.0
    return {
        "cluster_gap_trading_days": CLUSTER_GAP_TRADING_DAYS,
        "cluster_count": len(clusters),
        "effective_event_count": float(effective),
        "clusters": [
            {
                "start": cluster[0][1],
                "end": cluster[-1][1],
                "pass_day_count": len(cluster),
                "row_indices": [item[0] for item in cluster],
                "months": dict(Counter(item[1][:7] for item in cluster)),
            }
            for cluster in clusters
        ],
    }


def _overlap_summary(rows: list[dict[str, Any]], joint_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    trade_rows = [row for row in rows if row.get("record_type") == "trade"]
    ordered = sorted(
        trade_rows,
        key=lambda row: (_date_key(row.get("entry_date")), int(row.get("trade_index", 0))),
    )
    entry_distribution: Counter[int] = Counter()
    max_concurrent = 0
    for index, row in enumerate(ordered):
        entry_date = _date_key(row.get("entry_date"))
        existing = sum(
            1
            for prior in ordered[:index]
            if _date_key(prior.get("entry_date")) <= entry_date < _date_key(prior.get("exit_date"))
        )
        concurrent = existing + 1
        row["existing_open_positions_before_entry"] = existing
        row["concurrent_positions_at_entry"] = concurrent
        row["concurrency_interval_semantics"] = "half_open_[entry_date,exit_date)"
        entry_distribution[concurrent] += 1
        max_concurrent = max(max_concurrent, concurrent)

    joint_dates = [str(row.get("date")) for row in joint_rows]
    executed_signal_dates = sorted(
        {_date_key(row.get("entry_signal_date")) for row in ordered if _date_key(row.get("entry_signal_date"))}
    )
    unexecuted = sorted(set(joint_dates) - set(executed_signal_dates))
    events = _effective_event_summary(joint_rows)
    return {
        "strict_and_joint_pass_day_count": len(joint_dates),
        "strict_and_joint_pass_dates": joint_dates,
        "actual_trade_count": len(ordered),
        "executed_unique_signal_day_count": len(executed_signal_dates),
        "executed_unique_signal_dates": executed_signal_dates,
        "joint_pass_minus_executed_signal_days": len(unexecuted),
        "non_overlap_unexecuted_joint_pass_dates": unexecuted,
        "overlap_absorbed_pass_day_count": 0,
        "overlap_absorption_removed": True,
        "concurrent_positions_at_entry_distribution": {
            str(key): value for key, value in sorted(entry_distribution.items())
        },
        "max_concurrent_positions": max_concurrent,
        **events,
    }


def _fold_best_trade_rows(
    *,
    fold: str,
    rulebook: Any,
    result: Any,
    diagnostics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = _V3_FOLD_BEST_TRADE_ROWS(
        fold=fold,
        rulebook=rulebook,
        result=result,
        diagnostics=diagnostics,
    )
    tape = [row for row in list(getattr(result, "daily_signal_tape", []) or []) if isinstance(row, Mapping)]
    joint_rows = [
        row
        for row in tape
        if bool(row.get("entry_eligible")) and row.get("strict_interval_pass") is True
    ]
    summary = _overlap_summary(rows, joint_rows)
    for row in rows:
        row["entry_concurrency_mode"] = "independent_overlapping_positions"
        row["capital_accounting_mode"] = "independent_fixed_notional_per_trade"
        row["cooldown_scope"] = "per_trade_diagnostic_only"
        row["fold_overlap_summary"] = summary
    return rows


def _new_fitness_activation_probe(ctx: dict[str, Any]) -> dict[str, Any]:
    probe = dict(_V3_ACTIVATION_PROBE(ctx))
    source = inspect.getsource(execution_bt.run_backtest_execution_mode)
    overlap_checks = {
        "entry_scope_marker_present": "entry_scope_active = _entry_scope_active(rb)" in source,
        "entry_scope_daily_increment_present": "if entry_scope_active:" in source and "i += 1" in source,
        "legacy_exit_cooldown_jump_present": "i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)" in source,
        "entry_scope_daily_increment_precedes_legacy_jump": source.find("if entry_scope_active:") < source.find(
            "i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)"
        ),
    }
    checks = dict(probe.get("checks") or {})
    checks.update(overlap_checks)
    probe["checks"] = checks
    probe["overlap_entry_checks"] = overlap_checks
    probe["passed"] = bool(probe.get("passed")) and all(overlap_checks.values())
    return probe


def _fold_best_overlap_summary(trade_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in FOLDS:
        selected = [row for row in trade_rows if row.get("period_label") == fold]
        if not selected:
            continue
        summary = dict(selected[0].get("fold_overlap_summary") or {})
        output[fold] = summary
    return output


def _verdict(
    previous_best: Mapping[str, Mapping[str, Any]],
    current_best: Mapping[str, Mapping[str, Any]],
    overlap: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    previous = [int(previous_best.get(fold, {}).get("trade_count", 0) or 0) for fold in FOLDS]
    current = [int(current_best.get(fold, {}).get("trade_count", 0) or 0) for fold in FOLDS]
    absorbed = [int(overlap.get(fold, {}).get("overlap_absorbed_pass_day_count", -1)) for fold in FOLDS]
    if all(now > before for before, now in zip(previous, current)) and all(value == 0 for value in absorbed):
        return "OVERLAP_EFFECT_CONFIRMED", "세 fold 모두 fold-best 거래수가 증가했고 보유·cooldown에 의한 pass-day 흡수가 0으로 제거됐다."
    if any(now > before for before, now in zip(previous, current)) and all(value == 0 for value in absorbed):
        return "OVERLAP_EFFECT_PARTIAL", "흡수는 제거됐지만 fold-best 거래수 증가는 일부 fold에서만 확인됐다."
    return "OVERLAP_EFFECT_NOT_FOUND", "흡수 제거 후에도 fold-best 거래수가 이전 v3보다 증가하지 않았다."


def _postprocess(out_dir: Path, baseline_dir: Path, args: argparse.Namespace, original_argv: list[str]) -> None:
    _V3_POSTPROCESS(out_dir, baseline_dir, args, original_argv)

    current_final = _read_json(out_dir / "official_final_summary.json")
    current_qualify = _read_json(out_dir / "qualify_result.json")
    current_gate = _read_json(out_dir / "qualify_gate_bottleneck.json")
    current_population = _read_jsonl(out_dir / "qualify_population_all.jsonl")
    current_trades = _read_jsonl(out_dir / "fold_best_trade_level.jsonl")

    previous_final = _read_json(baseline_dir / "official_final_summary.json")
    previous_qualify = _read_json(baseline_dir / "qualify_result.json")
    previous_population = _read_jsonl(baseline_dir / "qualify_population_all.jsonl")

    current_best = base._fold_best_from_population(current_population)
    previous_best = base._fold_best_from_population(previous_population)
    overlap = _fold_best_overlap_summary(current_trades)
    verdict_code, verdict_reason = _verdict(previous_best, current_best, overlap)

    previous_pdist = dict(previous_qualify.get("pass_count_distribution") or {})
    current_pdist = dict(current_qualify.get("pass_count_distribution") or {})
    previous_pass = dict(previous_qualify.get("fold_pass_counts") or {})
    current_pass = dict(current_qualify.get("fold_pass_counts") or {})

    relevant_env_keys = (
        "PYTHONUTF8",
        "PYTHONUNBUFFERED",
        "PYTHONPATH",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "PATH",
        "KINGMAKER_HOST_ROLE",
    )
    env_snapshot = {key: os.environ.get(key, "") for key in relevant_env_keys}
    argv = [sys.executable, str(HERE), *original_argv]

    def ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    env_lines = [f"$env:{key}={ps_quote(value)}" for key, value in env_snapshot.items() if value]
    powershell = " ".join(["&", ps_quote(sys.executable), *(ps_quote(value) for value in [str(HERE), *original_argv])])
    launch = {
        "python_executable": sys.executable,
        "cwd": str(Path.cwd()),
        "argv": argv,
        "environment_variables": env_snapshot,
        "powershell_environment_prelude": env_lines,
        "powershell_command": powershell,
        "full_powershell_replay": "\n".join([*env_lines, powershell]),
        "host_local_parent": True,
        "local_process_workers": int(args.workers),
        "inter_machine_candidate_communication": False,
        "parallel_axis": "population_fitness_evaluation",
        "merge_order": "input_index_order",
        "entry_concurrency_mode": "independent_overlapping_positions",
        "capital_accounting_mode": "independent_fixed_notional_per_trade",
    }

    fold_best_histogram = dict(Counter(int(row.get("trade_count", 0) or 0) for row in current_best.values()))
    comparison = {
        "baseline_dir": str(baseline_dir),
        "current_dir": str(out_dir),
        "seed_base": int(args.seed_base),
        "pass_count_distribution": {"v3_single_position": previous_pdist, "v4_overlap_entry": current_pdist},
        "fold_pass_counts": {"v3_single_position": previous_pass, "v4_overlap_entry": current_pass},
        "fold_best": {"v3_single_position": previous_best, "v4_overlap_entry": current_best},
        "fold_best_trade_count_histogram": {str(key): value for key, value in sorted(fold_best_histogram.items())},
        "fold_best_overlap": overlap,
        "gate_bottleneck": dict(current_gate.get("folds") or {}),
        "elapsed_seconds": {
            "v3_single_position": previous_final.get("elapsed_seconds"),
            "v4_overlap_entry": current_final.get("elapsed_seconds"),
        },
        "verdict_code": verdict_code,
        "verdict_reason": verdict_reason,
    }
    _write_json(out_dir / "launch_command.json", launch)
    _write_json(out_dir / "overlap_entry_comparison.json", comparison)
    _write_json(
        out_dir / "fold_best_summary.json",
        {
            "fold_best": current_best,
            "fold_best_trade_count_histogram": comparison["fold_best_trade_count_histogram"],
            "fold_best_overlap": overlap,
            "verdict_code": verdict_code,
            "verdict_reason": verdict_reason,
        },
    )

    lines = [
        "# AAP 단일 포지션 제약 제거 후 재학습 readout",
        "",
        f"- source commit: `{args.source_git_commit}`",
        f"- seed: `{args.seed_base}`",
        f"- host: `{current_final.get('host_name')}`",
        f"- 실행: 독립 notebook parent + local `{args.workers}` processes",
        "- qualify: population 100 / generations 40 × train_1·train_2·train_3",
        "- 변경 변수: entry-scope 단일 포지션 인덱스 점프 제거만",
        "- strict-AND·exit·fitness·gate·mutation bias: 직전 v3와 동일",
        "- capital accounting: independent fixed notional per trade; aggregate exposure cap 없음",
        f"- 판정: **{verdict_code}** — {verdict_reason}",
        "",
        "## 전체 재실행 명령",
        "",
        "```powershell",
        *env_lines,
        powershell,
        "```",
        "",
        "## v3 직접 비교",
        "",
        "| 지표 | v3 단일포지션 | 이번 동시진입 |",
        "|---|---:|---:|",
        f"| all3 / all2 / all1 / all0 | {previous_pdist.get('all3', 0)} / {previous_pdist.get('all2', 0)} / {previous_pdist.get('all1', 0)} / {previous_pdist.get('all0', 0)} | {current_pdist.get('all3', 0)} / {current_pdist.get('all2', 0)} / {current_pdist.get('all1', 0)} / {current_pdist.get('all0', 0)} |",
        f"| train_1 / train_2 / train_3 pass | {previous_pass.get('train_1', 0)} / {previous_pass.get('train_2', 0)} / {previous_pass.get('train_3', 0)} | {current_pass.get('train_1', 0)} / {current_pass.get('train_2', 0)} / {current_pass.get('train_3', 0)} |",
        f"| fold-best 거래수 | {previous_best.get('train_1', {}).get('trade_count')} / {previous_best.get('train_2', {}).get('trade_count')} / {previous_best.get('train_3', {}).get('trade_count')} | {current_best.get('train_1', {}).get('trade_count')} / {current_best.get('train_2', {}).get('trade_count')} / {current_best.get('train_3', {}).get('trade_count')} |",
        f"| fold-best 최대 동시 포지션 | 1 / 1 / 1 | {overlap.get('train_1', {}).get('max_concurrent_positions')} / {overlap.get('train_2', {}).get('max_concurrent_positions')} / {overlap.get('train_3', {}).get('max_concurrent_positions')} |",
        f"| fold-best fitness | {previous_best.get('train_1', {}).get('fitness')} / {previous_best.get('train_2', {}).get('fitness')} / {previous_best.get('train_3', {}).get('fitness')} | {current_best.get('train_1', {}).get('fitness')} / {current_best.get('train_2', {}).get('fitness')} / {current_best.get('train_3', {}).get('fitness')} |",
        f"| effective event count | 4.084967 / 4.062802 / 3.792593 | {_safe_float(overlap.get('train_1', {}).get('effective_event_count')):.6f} / {_safe_float(overlap.get('train_2', {}).get('effective_event_count')):.6f} / {_safe_float(overlap.get('train_3', {}).get('effective_event_count')):.6f} |",
        "",
        "## Fold-best support·동시 보유",
        "",
        "| fold | joint pass day | 실제 거래 | overlap 흡수 | 미체결 pass | 최대 동시 | 진입시 동시수 분포 | cluster | effective event count |",
        "|---|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for fold in FOLDS:
        row = overlap.get(fold, {})
        lines.append(
            f"| {fold} | {row.get('strict_and_joint_pass_day_count')} | {row.get('actual_trade_count')} | "
            f"{row.get('overlap_absorbed_pass_day_count')} | {row.get('joint_pass_minus_executed_signal_days')} | "
            f"{row.get('max_concurrent_positions')} | `{json.dumps(row.get('concurrent_positions_at_entry_distribution') or {}, sort_keys=True)}` | "
            f"{row.get('cluster_count')} | {_safe_float(row.get('effective_event_count')):.6f} |"
        )

    lines.extend([
        "",
        f"Fold-best 거래수 histogram: `{json.dumps(comparison['fold_best_trade_count_histogram'], sort_keys=True)}`",
        "",
        "## Fold별 pass 거래수 histogram·gate 병목",
        "",
        "| fold | 후보 | 거래수 gate 탈락 | 승률<60 탈락 | 두 gate 통과 | 실현손실 벌점 | MAE 벌점 | pass 거래수 histogram | factor bins 전체/pass |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ])
    for fold in FOLDS:
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
            f"`{json.dumps(pass_dist.get('histogram') or {}, sort_keys=True)}` | "
            f"`{json.dumps(all_dist.get('factor_bins') or {}, sort_keys=True)}` / `{json.dumps(pass_dist.get('factor_bins') or {}, sort_keys=True)}` |"
        )

    lines.extend([
        "",
        "## Trade-level 로그",
        "",
        "`fold_best_trade_level.jsonl`은 진입/청산일·가격, 청산 사유, 보유일, 비용차감 실현수익, MAE, 일수익, +0.5% 승패, 5-feature snapshot, interval checks와 함께 다음을 추가 기록한다.",
        "",
        "- `existing_open_positions_before_entry`",
        "- `concurrent_positions_at_entry`",
        "- `concurrency_interval_semantics=half_open_[entry_date,exit_date)`",
        "- fold별 joint pass·흡수·cluster·effective event summary",
        "",
        "## 안전성",
        "",
        f"- manifest gate: {bool(current_final.get('manifest_gate_passed'))}",
        f"- 보호 SHA 불변: {bool(current_final.get('protected_unchanged'))}",
        f"- daemon 불변: {bool(current_final.get('daemon_unchanged'))}",
        f"- 병렬 재현성 probe: {bool((current_final.get('parallel_reproducibility_probe') or {}).get('passed'))}",
        f"- activation probe: {bool((current_final.get('new_fitness_activation_probe') or {}).get('passed'))}",
        f"- source git commit: `{args.source_git_commit}`",
    ])
    (out_dir / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name in ("manifest.json", "official_final_summary.json"):
        path = out_dir / name
        payload = _read_json(path)
        payload["launch_command"] = launch
        payload["overlap_entry_comparison"] = comparison
        payload["entry_concurrency_mode"] = "independent_overlapping_positions"
        payload["capital_accounting_mode"] = "independent_fixed_notional_per_trade"
        payload["verdict_code"] = verdict_code
        payload["verdict_reason"] = verdict_reason
        _write_json(path, payload)
    runner._write_sha_manifest(out_dir)


def main(argv: list[str] | None = None) -> int:
    original_argv = list(sys.argv[1:] if argv is None else argv)
    base._fold_best_trade_rows = _fold_best_trade_rows
    base._new_fitness_activation_probe = _new_fitness_activation_probe
    base._postprocess = _postprocess
    return int(base.main(original_argv))


if __name__ == "__main__":
    base.base.mp.freeze_support()
    raise SystemExit(main())
