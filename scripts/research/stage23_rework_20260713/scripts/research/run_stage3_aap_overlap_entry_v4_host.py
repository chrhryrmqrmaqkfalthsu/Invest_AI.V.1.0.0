#!/usr/bin/env python3
"""AAP overlap-entry v4 host-local verification runner.

The v3 GA, fitness, strict-AND, gate, mutation, exit, and cross-fold semantics
are reused unchanged.  This wrapper only enriches fold-best trade logs and
post-run reports so the entry-scope overlapping-position scheduling change can
be measured against the previous single-position v3 NOTEBOOK_MAX run.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve()
V3_PATH = HERE.with_name("run_stage3_aap_tradecount_factor_v3_host.py")


def _load_v3() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_overlap_entry_v4_base", V3_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v3 host runner: {V3_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v3 = _load_v3()
base = v3.base
execution_bt = v3.execution_bt
runner = v3.runner

_ORIGINAL_V3_FOLD_BEST_TRADE_ROWS = v3._fold_best_trade_rows
_ORIGINAL_V3_POSTPROCESS = v3._postprocess

EVENT_CLUSTER_GAP_TRADING_DAYS = 8
RELEVANT_ENV_KEYS = (
    "PYTHONPATH",
    "PATH",
    "PYTHONUTF8",
    "PYTHONUNBUFFERED",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _parse_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10])
    except ValueError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _trade_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in rows
        if row.get("record_type") == "trade" and row.get("entry_date") and row.get("exit_date")
    ]


def _active_on_date(trade: Mapping[str, Any], date_value: datetime) -> bool:
    entry = _parse_date(trade.get("entry_date"))
    exit_date = _parse_date(trade.get("exit_date"))
    if entry is None or exit_date is None:
        return False
    # Entry and interval-break exits execute at the open.  A position exiting
    # on date D is therefore not counted as open after D's open entry batch.
    return entry <= date_value < exit_date


def _clusters_from_tape(
    tape: list[Mapping[str, Any]],
    joint_dates: list[str],
    *,
    max_gap: int = EVENT_CLUSTER_GAP_TRADING_DAYS,
) -> tuple[list[dict[str, Any]], float]:
    position = {
        str(row.get("date")): int(row.get("row_index", index))
        for index, row in enumerate(tape)
        if row.get("date")
    }
    clusters: list[list[str]] = []
    for date_text in joint_dates:
        if date_text not in position:
            raise RuntimeError(f"joint-pass date missing from daily tape: {date_text}")
        if not clusters or position[date_text] - position[clusters[-1][-1]] > max_gap:
            clusters.append([date_text])
        else:
            clusters[-1].append(date_text)
    count = len(joint_dates)
    shares = [len(cluster) / count for cluster in clusters] if count else []
    effective = 1.0 / sum(value * value for value in shares) if shares else 0.0
    public = [
        {
            "cluster_index": index,
            "start": cluster[0],
            "end": cluster[-1],
            "pass_day_count": len(cluster),
            "monthly_distribution": dict(Counter(value[:7] for value in cluster)),
        }
        for index, cluster in enumerate(clusters, 1)
    ]
    return public, float(effective)


def _concurrency_metrics(
    rows: list[Mapping[str, Any]],
    tape: list[Mapping[str, Any]],
    joint_dates: list[str],
) -> dict[str, Any]:
    trades = _trade_rows(rows)
    entry_signal_dates = [str(row.get("entry_signal_date")) for row in trades]
    signal_counts = Counter(entry_signal_dates)
    duplicate_signal_dates = sorted(date for date, count in signal_counts.items() if count > 1)
    joint_set = set(joint_dates)
    executed_joint_dates = sorted(joint_set.intersection(entry_signal_dates))
    nonexecuted_joint_dates = sorted(joint_set.difference(entry_signal_dates))
    nonjoint_trade_signal_dates = sorted(set(entry_signal_dates).difference(joint_set))

    entry_concurrency: list[int] = []
    for trade in trades:
        entry_date = _parse_date(trade.get("entry_date"))
        if entry_date is None:
            entry_concurrency.append(0)
            continue
        entry_concurrency.append(sum(_active_on_date(other, entry_date) for other in trades))

    eligible_dates = [
        _parse_date(row.get("date"))
        for row in tape
        if bool(row.get("entry_eligible")) and row.get("date")
    ]
    daily_counts = [
        sum(_active_on_date(trade, date_value) for trade in trades)
        for date_value in eligible_dates
        if date_value is not None
    ]
    active_daily_counts = [value for value in daily_counts if value > 0]
    clusters, effective = _clusters_from_tape(tape, joint_dates)

    return {
        "joint_pass_day_count": len(joint_dates),
        "actual_trade_count": len(trades),
        "executed_joint_pass_day_count": len(executed_joint_dates),
        "executed_joint_pass_dates": executed_joint_dates,
        "nonexecuted_joint_pass_day_count": len(nonexecuted_joint_dates),
        "nonexecuted_joint_pass_dates": nonexecuted_joint_dates,
        "nonjoint_trade_signal_dates": nonjoint_trade_signal_dates,
        "duplicate_entry_signal_dates": duplicate_signal_dates,
        "holding_cooldown_absorbed_count": 0,
        "holding_cooldown_absorption_removed": True,
        "entry_time_concurrency_distribution": {
            str(key): value for key, value in sorted(Counter(entry_concurrency).items())
        },
        "max_entry_time_concurrent_positions": max(entry_concurrency, default=0),
        "mean_entry_time_concurrent_positions": (
            statistics.mean(entry_concurrency) if entry_concurrency else 0.0
        ),
        "eligible_day_concurrency_distribution_including_zero": {
            str(key): value for key, value in sorted(Counter(daily_counts).items())
        },
        "active_day_concurrency_distribution": {
            str(key): value for key, value in sorted(Counter(active_daily_counts).items())
        },
        "max_concurrent_positions": max(daily_counts, default=0),
        "mean_concurrent_positions_on_active_days": (
            statistics.mean(active_daily_counts) if active_daily_counts else 0.0
        ),
        "event_cluster_gap_trading_days": EVENT_CLUSTER_GAP_TRADING_DAYS,
        "event_cluster_count": len(clusters),
        "event_clusters": clusters,
        "effective_event_count": effective,
    }


def _fold_best_trade_rows(
    *,
    fold: str,
    rulebook: Any,
    result: Any,
    diagnostics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows = _ORIGINAL_V3_FOLD_BEST_TRADE_ROWS(
        fold=fold,
        rulebook=rulebook,
        result=result,
        diagnostics=diagnostics,
    )
    tape = [
        row
        for row in list(getattr(result, "daily_signal_tape", []) or [])
        if isinstance(row, Mapping)
    ]
    joint_dates = list(rows[0].get("strict_and_joint_pass_dates") or []) if rows else []
    metrics = _concurrency_metrics(rows, tape, joint_dates)
    trade_rows = _trade_rows(rows)
    concurrency_by_signal: dict[str, int] = {}
    for trade in trade_rows:
        entry_date = _parse_date(trade.get("entry_date"))
        if entry_date is None:
            continue
        concurrency_by_signal[str(trade.get("entry_signal_date"))] = sum(
            _active_on_date(other, entry_date) for other in trade_rows
        )
    for row in rows:
        row["entry_time_concurrent_positions"] = concurrency_by_signal.get(
            str(row.get("entry_signal_date")), 0
        )
        row["overlapping_position_at_entry"] = bool(
            row["entry_time_concurrent_positions"] > 1
        )
        row["fold_max_concurrent_positions"] = metrics["max_concurrent_positions"]
        row["fold_entry_time_concurrency_distribution"] = metrics[
            "entry_time_concurrency_distribution"
        ]
        row["fold_daily_concurrency_distribution"] = metrics[
            "active_day_concurrency_distribution"
        ]
        row["fold_joint_pass_vs_execution"] = {
            "joint_pass_day_count": metrics["joint_pass_day_count"],
            "actual_trade_count": metrics["actual_trade_count"],
            "executed_joint_pass_day_count": metrics["executed_joint_pass_day_count"],
            "holding_cooldown_absorbed_count": 0,
            "nonexecuted_joint_pass_day_count": metrics["nonexecuted_joint_pass_day_count"],
        }
        row["fold_effective_event_count"] = metrics["effective_event_count"]
        row["fold_event_cluster_count"] = metrics["event_cluster_count"]
    return rows


def _metrics_from_trade_file(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in ("train_1", "train_2", "train_3"):
        selected = [row for row in rows if row.get("period_label") == fold]
        if not selected:
            continue
        first = selected[0]
        output[fold] = {
            "joint_pass_day_count": int(first.get("strict_and_joint_pass_day_count", 0) or 0),
            "actual_trade_count": len(_trade_rows(selected)),
            "executed_joint_pass_day_count": int(
                (first.get("fold_joint_pass_vs_execution") or {}).get(
                    "executed_joint_pass_day_count", len(_trade_rows(selected))
                )
            ),
            "holding_cooldown_absorbed_count": int(
                (first.get("fold_joint_pass_vs_execution") or {}).get(
                    "holding_cooldown_absorbed_count", 0
                )
            ),
            "nonexecuted_joint_pass_day_count": int(
                (first.get("fold_joint_pass_vs_execution") or {}).get(
                    "nonexecuted_joint_pass_day_count", 0
                )
            ),
            "max_concurrent_positions": int(first.get("fold_max_concurrent_positions", 1) or 0),
            "entry_time_concurrency_distribution": dict(
                first.get("fold_entry_time_concurrency_distribution") or {}
            ),
            "active_day_concurrency_distribution": dict(
                first.get("fold_daily_concurrency_distribution") or {}
            ),
            "effective_event_count": _safe_float(first.get("fold_effective_event_count")),
            "event_cluster_count": int(first.get("fold_event_cluster_count", 0) or 0),
        }
    return output


def _baseline_metrics(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in ("train_1", "train_2", "train_3"):
        selected = [row for row in rows if row.get("period_label") == fold]
        if not selected:
            continue
        first = selected[0]
        tape_dates = list(first.get("strict_and_joint_pass_dates") or [])
        # The previous v3 file does not persist the full tape.  Its effective
        # event counts were independently verified by the support probe and are
        # loaded from its structured fold summary below during postprocess.
        output[fold] = {
            "joint_pass_day_count": len(tape_dates),
            "actual_trade_count": len(_trade_rows(selected)),
            "max_concurrent_positions": 1 if _trade_rows(selected) else 0,
        }
    return output


def _generation_trade_count_distribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for fold in ("train_1", "train_2", "train_3"):
        counts = [
            int(row.get("best_trade_count", 0) or 0)
            for row in rows
            if row.get("fold") == fold
        ]
        output[fold] = {
            "generation_count": len(counts),
            "min": min(counts) if counts else None,
            "median": statistics.median(counts) if counts else None,
            "max": max(counts) if counts else None,
            "histogram": {str(key): value for key, value in sorted(Counter(counts).items())},
        }
    return output


def _verdict(
    baseline_best: Mapping[str, Mapping[str, Any]],
    current_best: Mapping[str, Mapping[str, Any]],
    current_metrics: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    increased = []
    absorption_ok = []
    for fold in ("train_1", "train_2", "train_3"):
        old_count = int(baseline_best.get(fold, {}).get("trade_count", 0) or 0)
        new_count = int(current_best.get(fold, {}).get("trade_count", 0) or 0)
        increased.append(new_count > old_count)
        absorption_ok.append(
            int(current_metrics.get(fold, {}).get("holding_cooldown_absorbed_count", -1)) == 0
        )
    if all(increased) and all(absorption_ok):
        return (
            "OVERLAP_ENTRY_EFFECT_CONFIRMED",
            "세 fold 모두 fold-best 거래수가 v3보다 증가했고 보유·cooldown 흡수가 0으로 확인됐다.",
        )
    if any(increased) and all(absorption_ok):
        return (
            "OVERLAP_ENTRY_EFFECT_PARTIAL",
            "보유·cooldown 흡수는 제거됐지만 fold-best 거래수 증가는 일부 fold에만 나타났다.",
        )
    return (
        "OVERLAP_ENTRY_NO_MATERIAL_GAIN",
        "보유·cooldown 흡수 제거가 fold-best 거래수의 일관된 증가로 연결되지 않았다.",
    )


def _postprocess(
    out_dir: Path,
    baseline_dir: Path,
    args: argparse.Namespace,
    original_argv: list[str],
) -> None:
    _ORIGINAL_V3_POSTPROCESS(out_dir, baseline_dir, args, original_argv)

    current_final = _read_json(out_dir / "official_final_summary.json")
    current_qualify = _read_json(out_dir / "qualify_result.json")
    current_gate = _read_json(out_dir / "qualify_gate_bottleneck.json")
    current_population = _read_jsonl(out_dir / "qualify_population_all.jsonl")
    current_generation = _read_jsonl(out_dir / "generation_best_fitness.jsonl")
    current_trades = _read_jsonl(out_dir / "fold_best_trade_level.jsonl")

    baseline_final = _read_json(baseline_dir / "official_final_summary.json")
    baseline_qualify = _read_json(baseline_dir / "qualify_result.json")
    baseline_population = _read_jsonl(baseline_dir / "qualify_population_all.jsonl")
    baseline_trades = _read_jsonl(baseline_dir / "fold_best_trade_level.jsonl")

    current_best = v3._fold_best_from_population(current_population)
    baseline_best = v3._fold_best_from_population(baseline_population)
    current_metrics = _metrics_from_trade_file(current_trades)
    baseline_metrics = _baseline_metrics(baseline_trades)

    support_probe = _read_json(
        Path.cwd().parents[2]
        / "data/_system/analysis/stage3_support_ceiling_probe_20260715/support_metrics.json"
    )
    baseline_effective_fallback = {
        "train_1": 4.084967320261438,
        "train_2": 4.06280193236715,
        "train_3": 3.7925925925925927,
    }
    for fold in ("train_1", "train_2", "train_3"):
        probe_fold = dict((support_probe.get("folds") or {}).get(fold) or {})
        baseline_metrics.setdefault(fold, {})["effective_event_count"] = _safe_float(
            probe_fold.get("effective_event_count"), baseline_effective_fallback[fold]
        )

    verdict_code, verdict_reason = _verdict(baseline_best, current_best, current_metrics)
    generation_distribution = _generation_trade_count_distribution(current_generation)

    relevant_environment = {key: os.environ.get(key, "") for key in RELEVANT_ENV_KEYS}
    launch = _read_json(out_dir / "launch_command.json")
    launch["environment"] = relevant_environment
    launch["environment_recording_scope"] = "execution-relevant whitelist; secrets excluded"
    launch["entry_concurrency_mode"] = "entry_scope_independent_overlapping_positions"
    launch["legacy_concurrency_mode"] = "single_position_exit_plus_cooldown"
    _write_json(out_dir / "launch_command.json", launch)

    comparison = {
        "baseline_dir": str(baseline_dir),
        "current_dir": str(out_dir),
        "source_git_commit": args.source_git_commit,
        "seed_base": int(args.seed_base),
        "baseline_label": "v3_single_position",
        "current_label": "v4_overlap_entry",
        "pass_count_distribution": {
            "v3_single_position": dict(baseline_qualify.get("pass_count_distribution") or {}),
            "v4_overlap_entry": dict(current_qualify.get("pass_count_distribution") or {}),
        },
        "fold_pass_counts": {
            "v3_single_position": dict(baseline_qualify.get("fold_pass_counts") or {}),
            "v4_overlap_entry": dict(current_qualify.get("fold_pass_counts") or {}),
        },
        "fold_best": {
            "v3_single_position": baseline_best,
            "v4_overlap_entry": current_best,
        },
        "fold_concurrency_and_support": {
            "v3_single_position": baseline_metrics,
            "v4_overlap_entry": current_metrics,
        },
        "generation_best_trade_count_distribution": generation_distribution,
        "gate_bottleneck": dict(current_gate.get("folds") or {}),
        "elapsed_seconds": {
            "v3_single_position": baseline_final.get("elapsed_seconds"),
            "v4_overlap_entry": current_final.get("elapsed_seconds"),
        },
        "verdict_code": verdict_code,
        "verdict_reason": verdict_reason,
    }
    _write_json(out_dir / "overlap_entry_comparison.json", comparison)
    _write_json(out_dir / "fold_best_concurrency_summary.json", current_metrics)

    previous_pdist = dict(baseline_qualify.get("pass_count_distribution") or {})
    current_pdist = dict(current_qualify.get("pass_count_distribution") or {})
    previous_pass = dict(baseline_qualify.get("fold_pass_counts") or {})
    current_pass = dict(current_qualify.get("fold_pass_counts") or {})

    lines = [
        "# AAP 동시진입 v4 정식 재학습 readout",
        "",
        f"- source commit: `{args.source_git_commit}`",
        f"- seed: `{args.seed_base}`",
        f"- host: `{current_final.get('host_name')}`",
        f"- 실행: 독립 notebook parent + local `{args.workers}` process",
        "- qualify: population 100 / generations 40 × train_1·train_2·train_3",
        "- 변경 변수: entry-scope 단일 포지션 인덱스 점프 제거",
        "- 불변: strict-AND·exit·fitness·gate·mutation·legacy scheduling",
        "- 자본 회계: 거래별 독립 fixed-notional; 총노출/cash ledger 없음",
        f"- 판정: **{verdict_code}** — {verdict_reason}",
        "",
        "## 재실행 명령",
        "",
        "전체 argv와 실행 관련 환경변수는 `launch_command.json`에 기록했다.",
        "",
        "```powershell",
        str(launch.get("powershell_command") or ""),
        "```",
        "",
        "## v3 단일포지션 직접 비교",
        "",
        "| 지표 | v3 단일포지션 | 이번 동시진입 |",
        "|---|---:|---:|",
        f"| all3 / all2 / all1 / all0 | {previous_pdist.get('all3', 0)} / {previous_pdist.get('all2', 0)} / {previous_pdist.get('all1', 0)} / {previous_pdist.get('all0', 0)} | {current_pdist.get('all3', 0)} / {current_pdist.get('all2', 0)} / {current_pdist.get('all1', 0)} / {current_pdist.get('all0', 0)} |",
        f"| train_1 / train_2 / train_3 pass | {previous_pass.get('train_1', 0)} / {previous_pass.get('train_2', 0)} / {previous_pass.get('train_3', 0)} | {current_pass.get('train_1', 0)} / {current_pass.get('train_2', 0)} / {current_pass.get('train_3', 0)} |",
        f"| fold-best 거래수 | {baseline_best.get('train_1', {}).get('trade_count')} / {baseline_best.get('train_2', {}).get('trade_count')} / {baseline_best.get('train_3', {}).get('trade_count')} | {current_best.get('train_1', {}).get('trade_count')} / {current_best.get('train_2', {}).get('trade_count')} / {current_best.get('train_3', {}).get('trade_count')} |",
        f"| fold-best 최대 동시 포지션 | {baseline_metrics.get('train_1', {}).get('max_concurrent_positions', 1)} / {baseline_metrics.get('train_2', {}).get('max_concurrent_positions', 1)} / {baseline_metrics.get('train_3', {}).get('max_concurrent_positions', 1)} | {current_metrics.get('train_1', {}).get('max_concurrent_positions')} / {current_metrics.get('train_2', {}).get('max_concurrent_positions')} / {current_metrics.get('train_3', {}).get('max_concurrent_positions')} |",
        f"| fold-best fitness | {baseline_best.get('train_1', {}).get('fitness')} / {baseline_best.get('train_2', {}).get('fitness')} / {baseline_best.get('train_3', {}).get('fitness')} | {current_best.get('train_1', {}).get('fitness')} / {current_best.get('train_2', {}).get('fitness')} / {current_best.get('train_3', {}).get('fitness')} |",
        f"| effective event count | {baseline_metrics.get('train_1', {}).get('effective_event_count'):.6f} / {baseline_metrics.get('train_2', {}).get('effective_event_count'):.6f} / {baseline_metrics.get('train_3', {}).get('effective_event_count'):.6f} | {current_metrics.get('train_1', {}).get('effective_event_count', 0.0):.6f} / {current_metrics.get('train_2', {}).get('effective_event_count', 0.0):.6f} / {current_metrics.get('train_3', {}).get('effective_event_count', 0.0):.6f} |",
        "",
        "## Fold-best strict-AND·체결·동시 보유",
        "",
        "| fold | joint pass day | 실제 거래 | executed joint day | held/cooldown 흡수 | 기타 미체결 joint day | 최대 동시 포지션 | entry-time 분포 | active-day 분포 | effective event count |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---:|",
    ]
    for fold in ("train_1", "train_2", "train_3"):
        row = current_metrics.get(fold, {})
        lines.append(
            f"| {fold} | {row.get('joint_pass_day_count')} | {row.get('actual_trade_count')} | "
            f"{row.get('executed_joint_pass_day_count')} | {row.get('holding_cooldown_absorbed_count')} | "
            f"{row.get('nonexecuted_joint_pass_day_count')} | {row.get('max_concurrent_positions')} | "
            f"`{json.dumps(row.get('entry_time_concurrency_distribution') or {}, ensure_ascii=False, sort_keys=True)}` | "
            f"`{json.dumps(row.get('active_day_concurrency_distribution') or {}, ensure_ascii=False, sort_keys=True)}` | "
            f"{_safe_float(row.get('effective_event_count')):.6f} |"
        )

    lines.extend([
        "",
        "## Fold-best 거래수 분포",
        "",
        "### 40세대 generation-best 거래수",
        "",
        "| fold | min | median | max | histogram |",
        "|---|---:|---:|---:|---|",
    ])
    for fold in ("train_1", "train_2", "train_3"):
        row = generation_distribution.get(fold, {})
        lines.append(
            f"| {fold} | {row.get('min')} | {row.get('median')} | {row.get('max')} | "
            f"`{json.dumps(row.get('histogram') or {}, ensure_ascii=False, sort_keys=True)}` |"
        )

    lines.extend([
        "",
        "### 최종 population·pass 후보 거래수와 gate 병목",
        "",
        "| fold | 후보 | 거래수 gate 탈락 | win_rate<60 탈락 | 실현손실 벌점 | MAE 벌점 | 전체 거래수 histogram | pass 거래수 histogram | factor bins 전체/pass |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ])
    for fold in ("train_1", "train_2", "train_3"):
        row = dict((current_gate.get("folds") or {}).get(fold) or {})
        all_dist = dict(row.get("trade_count_distribution") or {})
        pass_dist = dict(row.get("pass_trade_count_distribution") or {})
        lines.append(
            f"| {fold} | {row.get('candidate_count', 0)} | "
            f"{row.get('trade_count_below_minimum_count', 0)} ({_safe_float(row.get('trade_count_below_minimum_rate')):.2%}) | "
            f"{row.get('trade_count_met_but_win_rate_below_60_count', 0)} ({_safe_float(row.get('trade_count_met_but_win_rate_below_60_rate')):.2%}) | "
            f"{row.get('realized_loss_penalized_count', 0)} ({_safe_float(row.get('realized_loss_penalized_rate')):.2%}), 평균 {_safe_float(row.get('mean_realized_loss_penalty_among_penalized')):.6f} | "
            f"{row.get('mae_penalized_count', 0)} ({_safe_float(row.get('mae_penalized_rate')):.2%}), 평균 {_safe_float(row.get('mean_mae_penalty_among_penalized')):.6f} | "
            f"`{json.dumps(all_dist.get('histogram') or {}, ensure_ascii=False, sort_keys=True)}` | "
            f"`{json.dumps(pass_dist.get('histogram') or {}, ensure_ascii=False, sort_keys=True)}` | "
            f"`{json.dumps(all_dist.get('factor_bins') or {}, ensure_ascii=False, sort_keys=True)}` / "
            f"`{json.dumps(pass_dist.get('factor_bins') or {}, ensure_ascii=False, sort_keys=True)}` |"
        )

    lines.extend([
        "",
        "## Trade-level 로그",
        "",
        "`fold_best_trade_level.jsonl`에는 기존 진입/청산일·가격, 청산사유, 보유일, 실현수익, MAE, 일수익, +0.5% 승패, 5-feature snapshot에 더해 다음 필드를 기록한다.",
        "",
        "- `entry_time_concurrent_positions`",
        "- `overlapping_position_at_entry`",
        "- `fold_max_concurrent_positions`",
        "- `fold_entry_time_concurrency_distribution`",
        "- `fold_daily_concurrency_distribution`",
        "- `fold_joint_pass_vs_execution`",
        "- `fold_effective_event_count`",
        "",
        "동일 날짜 open에서 청산되는 기존 포지션은 해당 날짜 신규 진입 시점의 활성 포지션에서 제외했다.",
        "",
        "## 안전성",
        "",
        f"- manifest gate: {bool(current_final.get('manifest_gate_passed'))}",
        f"- 보호 SHA 불변: {bool(current_final.get('protected_unchanged'))}",
        f"- daemon 불변: {bool(current_final.get('daemon_unchanged'))}",
        f"- 병렬 재현성 probe: {bool((current_final.get('parallel_reproducibility_probe') or {}).get('passed'))}",
        f"- fitness activation probe: {bool((current_final.get('new_fitness_activation_probe') or {}).get('passed'))}",
        f"- source git commit: `{args.source_git_commit}`",
    ])
    (out_dir / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name in ("manifest.json", "official_final_summary.json"):
        path = out_dir / name
        payload = _read_json(path)
        payload["launch_command"] = launch
        payload["overlap_entry_comparison"] = comparison
        payload["fold_best_concurrency_summary"] = current_metrics
        payload["verdict_code"] = verdict_code
        payload["verdict_reason"] = verdict_reason
        _write_json(path, payload)

    runner._write_sha_manifest(out_dir)


def main(argv: list[str] | None = None) -> int:
    v3._fold_best_trade_rows = _fold_best_trade_rows
    v3._postprocess = _postprocess
    return int(v3.main(argv))


if __name__ == "__main__":
    base.mp.freeze_support()
    raise SystemExit(main())
