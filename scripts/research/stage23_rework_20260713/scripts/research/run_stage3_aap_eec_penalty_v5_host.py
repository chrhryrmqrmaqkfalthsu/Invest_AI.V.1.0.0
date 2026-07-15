#!/usr/bin/env python3
"""AAP entry-scope EEC-penalty v5 host-local runner.

Only the entry-scope fitness evaluation is changed: the existing
trade-count-adjusted primary objective is multiplied by
clamp(EEC / target, floor, 1.0).  Signal logic, should_buy, strict interval,
entry/exit execution, mutation, legacy scheduling, and fixed-notional trade
accounting are inherited from v4 unchanged.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve()
V4_PATH = HERE.with_name("run_stage3_aap_overlap_entry_v4_host.py")


def _load_v4() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_eec_penalty_v5_base", V4_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v4 host runner: {V4_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v4 = _load_v4()
v3 = v4.v3
base = v4.base
execution_bt = v4.execution_bt
runner = v4.runner

from engine.learning import execution_mode_backtest_eec_v5 as eec_v5  # noqa: E402

eec_v5.install(execution_bt)

_ORIGINAL_V3_BUILD_CROSS_MATRIX = v3._build_cross_matrix
_ORIGINAL_V3_FOLD_BEST_FROM_POPULATION = v3._fold_best_from_population
_ORIGINAL_V4_FOLD_BEST_TRADE_ROWS = v4._fold_best_trade_rows
_ORIGINAL_V4_POSTPROCESS = v4._postprocess

V4_REFERENCE = {
    "pass_count_distribution": {"all3": 0, "all2": 2, "all1": 192, "all0": 106},
    "fold_best_trade_count": {"train_1": 20, "train_2": 15, "train_3": 13},
    "fold_best_eec": {"train_1": 2.30, "train_2": 2.53, "train_3": 3.70},
    "fold_best_max_cluster_share": {"train_1": 0.60, "train_2": 0.60, "train_3": 0.37},
}


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


def _eec_fields_from_diag(diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "effective_event_count": _safe_float(diagnostics.get("effective_event_count")),
        "eec_multiplier": _safe_float(diagnostics.get("entry_fitness_eec_multiplier"), 1.0),
        "eec_target": _safe_float(diagnostics.get("entry_fitness_eec_target"), eec_v5.ENTRY_FITNESS_EEC_TARGET),
        "eec_floor": _safe_float(diagnostics.get("entry_fitness_eec_floor"), eec_v5.ENTRY_FITNESS_EEC_FLOOR),
        "eec_nonduplicate_event_count": int(_safe_float(diagnostics.get("eec_nonduplicate_event_count"), 0.0)),
        "eec_event_cluster_count": int(_safe_float(diagnostics.get("eec_event_cluster_count"), 0.0)),
        "eec_max_cluster_trade_share": _safe_float(diagnostics.get("eec_max_cluster_trade_share")),
        "primary_after_trade_count_factor_before_eec": _safe_float(diagnostics.get("primary_after_trade_count_factor_before_eec")),
        "primary_after_eec_penalty": _safe_float(diagnostics.get("primary_after_eec_penalty")),
        "final_fitness_before_eec": _safe_float(diagnostics.get("final_fitness_before_eec")),
    }


def _build_cross_matrix(scored_inputs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    matrix, summary, vectors = _ORIGINAL_V3_BUILD_CROSS_MATRIX(scored_inputs)
    diagnostics_by_key = {
        (str(row.get("candidate_hash")), str(row.get("period_label"))): dict(row.get("entry_fitness_diagnostics") or {})
        for row in scored_inputs
    }
    for row in matrix:
        diag = diagnostics_by_key.get((str(row.get("candidate_hash")), str(row.get("period_label"))), {})
        row.update(_eec_fields_from_diag(diag))
    folds = dict(summary.get("folds") or {})
    for fold, fold_summary in folds.items():
        fold_rows = [row for row in matrix if row.get("period_label") == fold]
        multipliers = [_safe_float(row.get("eec_multiplier"), 1.0) for row in fold_rows]
        eecs = [_safe_float(row.get("effective_event_count")) for row in fold_rows]
        max_shares = [_safe_float(row.get("eec_max_cluster_trade_share")) for row in fold_rows]
        fold_summary.update(
            {
                "eec_penalty_enabled": True,
                "eec_target": eec_v5.ENTRY_FITNESS_EEC_TARGET,
                "eec_floor": eec_v5.ENTRY_FITNESS_EEC_FLOOR,
                "mean_effective_event_count": statistics.mean(eecs) if eecs else 0.0,
                "median_effective_event_count": statistics.median(eecs) if eecs else 0.0,
                "mean_eec_multiplier": statistics.mean(multipliers) if multipliers else 1.0,
                "eec_multiplier_histogram": {str(key): value for key, value in sorted(Counter(round(value, 2) for value in multipliers).items())},
                "max_cluster_share_mean": statistics.mean(max_shares) if max_shares else 0.0,
                "eec_penalized_count": sum(value < 0.999999 for value in multipliers),
                "eec_penalized_rate": (sum(value < 0.999999 for value in multipliers) / len(multipliers)) if multipliers else 0.0,
            }
        )
    summary["folds"] = folds
    summary["eec_penalty"] = {
        "enabled": True,
        "target": eec_v5.ENTRY_FITNESS_EEC_TARGET,
        "floor": eec_v5.ENTRY_FITNESS_EEC_FLOOR,
        "cluster_gap_trading_days": eec_v5.ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS,
    }
    return matrix, summary, vectors


def _fold_best_from_population(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output = _ORIGINAL_V3_FOLD_BEST_FROM_POPULATION(rows)
    for fold in ("train_1", "train_2", "train_3"):
        candidates = [row for row in rows if row.get("fold") == fold]
        if not candidates:
            continue
        row = min(candidates, key=lambda item: int(item.get("population_rank", 10**9)))
        diagnostics = dict(row.get("entry_fitness_diagnostics") or {})
        output.setdefault(fold, {}).update(_eec_fields_from_diag(diagnostics))
    return output


def _fold_best_trade_rows(*, fold: str, rulebook: Any, result: Any, diagnostics: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _ORIGINAL_V4_FOLD_BEST_TRADE_ROWS(
        fold=fold,
        rulebook=rulebook,
        result=result,
        diagnostics=diagnostics,
    )
    eec_fields = _eec_fields_from_diag(diagnostics)
    clusters = list(dict(diagnostics).get("eec_event_clusters") or [])
    for row in rows:
        row.update(eec_fields)
        row["eec_event_clusters"] = clusters
        row["entry_fitness_eec_target"] = eec_fields["eec_target"]
        row["entry_fitness_eec_floor"] = eec_fields["eec_floor"]
    return rows


def _fold_best_metrics_from_trades(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for fold in ("train_1", "train_2", "train_3"):
        selected = [row for row in rows if row.get("period_label") == fold]
        trade_rows = [row for row in selected if row.get("record_type") == "trade"]
        if not selected:
            continue
        first = selected[0]
        output[fold] = {
            "trade_count": len(trade_rows),
            "effective_event_count": _safe_float(first.get("effective_event_count"), first.get("fold_effective_event_count")),
            "eec_multiplier": _safe_float(first.get("eec_multiplier"), 1.0),
            "max_cluster_trade_share": _safe_float(first.get("eec_max_cluster_trade_share")),
            "event_clusters": list(first.get("eec_event_clusters") or []),
            "entry_time_concurrency_distribution": dict(first.get("fold_entry_time_concurrency_distribution") or {}),
            "active_day_concurrency_distribution": dict(first.get("fold_daily_concurrency_distribution") or {}),
        }
    return output


def _rank_shift(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for fold in ("train_1", "train_2", "train_3"):
        selected = [row for row in rows if row.get("fold") == fold]
        enriched = []
        for row in selected:
            diag = dict(row.get("entry_fitness_diagnostics") or {})
            before = _safe_float(diag.get("final_fitness_before_eec"), row.get("fitness"))
            after = _safe_float(row.get("fitness"))
            enriched.append({"hash": row.get("candidate_hash"), "before": before, "after": after, "eec": _safe_float(diag.get("effective_event_count")), "multiplier": _safe_float(diag.get("entry_fitness_eec_multiplier"), 1.0)})
        before_rank = {item["hash"]: idx + 1 for idx, item in enumerate(sorted(enriched, key=lambda item: item["before"], reverse=True))}
        after_rank = {item["hash"]: idx + 1 for idx, item in enumerate(sorted(enriched, key=lambda item: item["after"], reverse=True))}
        moved = sorted(
            [
                {**item, "rank_before_eec": before_rank.get(item["hash"]), "rank_after_eec": after_rank.get(item["hash"]), "rank_delta_after_minus_before": (after_rank.get(item["hash"], 0) - before_rank.get(item["hash"], 0))}
                for item in enriched
            ],
            key=lambda item: abs(int(item.get("rank_delta_after_minus_before") or 0)),
            reverse=True,
        )
        output[fold] = {"largest_rank_shifts": moved[:10]}
    return output


def _verdict(current_best: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    eec_up = []
    share_down = []
    for fold in ("train_1", "train_2", "train_3"):
        current = current_best.get(fold, {})
        eec_up.append(_safe_float(current.get("effective_event_count")) > _safe_float(V4_REFERENCE["fold_best_eec"].get(fold)))
        current_share = _safe_float(current.get("eec_max_cluster_trade_share"), current.get("max_cluster_trade_share"))
        share_down.append(current_share < _safe_float(V4_REFERENCE["fold_best_max_cluster_share"].get(fold)))
    if all(eec_up) and all(share_down):
        return "EEC_PENALTY_EFFECTIVE", "fold-best EEC가 모든 fold에서 상승했고 최대 클러스터 비중도 모두 하락했다."
    if any(eec_up) and any(share_down):
        return "EEC_PENALTY_PARTIAL", "EEC/클러스터 집중 완화가 일부 fold에서만 확인됐다."
    return "EEC_PENALTY_INEFFECTIVE", "fold-best 기준으로 몰빵 집중이 충분히 깨지지 않았다."


def _postprocess(out_dir: Path, baseline_dir: Path, args: argparse.Namespace, original_argv: list[str]) -> None:
    _ORIGINAL_V4_POSTPROCESS(out_dir, baseline_dir, args, original_argv)

    current_final = _read_json(out_dir / "official_final_summary.json")
    current_qualify = _read_json(out_dir / "qualify_result.json")
    current_gate = _read_json(out_dir / "qualify_gate_bottleneck.json")
    current_population = _read_jsonl(out_dir / "qualify_population_all.jsonl")
    current_generation = _read_jsonl(out_dir / "generation_best_fitness.jsonl")
    current_trades = _read_jsonl(out_dir / "fold_best_trade_level.jsonl")

    current_best = _fold_best_from_population(current_population)
    current_trade_metrics = _fold_best_metrics_from_trades(current_trades)
    rank_shift = _rank_shift(current_population)
    verdict_code, verdict_reason = _verdict({fold: {**current_best.get(fold, {}), **current_trade_metrics.get(fold, {})} for fold in ("train_1", "train_2", "train_3")})

    launch = _read_json(out_dir / "launch_command.json")
    launch.update(
        {
            "eec_penalty_patch_token": eec_v5.PATCH_TOKEN,
            "eec_target": eec_v5.ENTRY_FITNESS_EEC_TARGET,
            "eec_floor": eec_v5.ENTRY_FITNESS_EEC_FLOOR,
            "eec_cluster_gap_trading_days": eec_v5.ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS,
            "remote_fetch_regenerate_disabled": True,
            "logged_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    _write_json(out_dir / "launch_command.json", launch)

    comparison = {
        "baseline_dir": str(baseline_dir),
        "current_dir": str(out_dir),
        "seed_base": int(args.seed_base),
        "v4_reference": V4_REFERENCE,
        "pass_count_distribution": {
            "v4": V4_REFERENCE["pass_count_distribution"],
            "this_run": dict(current_qualify.get("pass_count_distribution") or {}),
        },
        "fold_best": current_best,
        "fold_best_trade_metrics": current_trade_metrics,
        "rank_shift": rank_shift,
        "gate_bottleneck": dict(current_gate.get("folds") or {}),
        "verdict_code": verdict_code,
        "verdict_reason": verdict_reason,
    }
    _write_json(out_dir / "eec_penalty_comparison.json", comparison)
    _write_json(out_dir / "fold_best_eec_summary.json", current_trade_metrics)
    _write_json(out_dir / "eec_rank_shift.json", rank_shift)

    pdist = dict(current_qualify.get("pass_count_distribution") or {})
    pass_counts = dict(current_qualify.get("fold_pass_counts") or {})
    lines = [
        "# AAP EEC 집중도 벌점 v5 재학습 readout",
        "",
        f"- source commit: `{args.source_git_commit}`",
        f"- seed: `{args.seed_base}`",
        f"- host: `{current_final.get('host_name')}`",
        f"- 실행: notebook host-local `{args.workers}` process",
        "- qualify: population 100 / generations 40 × train_1·train_2·train_3",
        "- 변경 변수: entry-scope fitness에 EEC concentration multiplier 추가",
        "- 불변: 진입/청산·should_buy·strict interval·legacy scheduling·mutation·fixed-notional accounting",
        f"- EEC: target `{eec_v5.ENTRY_FITNESS_EEC_TARGET}`, floor `{eec_v5.ENTRY_FITNESS_EEC_FLOOR}`, cluster gap `{eec_v5.ENTRY_FITNESS_EEC_CLUSTER_GAP_TRADING_DAYS}` trading days",
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
        "## v4 대비 비교표",
        "",
        "| Metric | v4 | This run |",
        "|---|---:|---:|",
        f"| all3/all2/all1/all0 | 0/2/192/106 | {pdist.get('all3', 0)}/{pdist.get('all2', 0)}/{pdist.get('all1', 0)}/{pdist.get('all0', 0)} |",
        f"| fold별 pass 수 | - | {pass_counts.get('train_1', 0)}/{pass_counts.get('train_2', 0)}/{pass_counts.get('train_3', 0)} |",
        f"| fold-best 거래수 | 20/15/13(±) | {current_best.get('train_1', {}).get('trade_count')}/{current_best.get('train_2', {}).get('trade_count')}/{current_best.get('train_3', {}).get('trade_count')} |",
        f"| fold-best EEC | 2.30/2.53/3.70(±) | {current_trade_metrics.get('train_1', {}).get('effective_event_count', 0.0):.6f}/{current_trade_metrics.get('train_2', {}).get('effective_event_count', 0.0):.6f}/{current_trade_metrics.get('train_3', {}).get('effective_event_count', 0.0):.6f} |",
        f"| fold-best 최대 클러스터 비중 | 60%/60%/37% | {current_trade_metrics.get('train_1', {}).get('max_cluster_trade_share', 0.0):.2%}/{current_trade_metrics.get('train_2', {}).get('max_cluster_trade_share', 0.0):.2%}/{current_trade_metrics.get('train_3', {}).get('max_cluster_trade_share', 0.0):.2%} |",
        f"| fold-best fitness | ? | {current_best.get('train_1', {}).get('fitness')}/{current_best.get('train_2', {}).get('fitness')}/{current_best.get('train_3', {}).get('fitness')} |",
        "",
        "## Fold-best EEC·클러스터",
        "",
        "| fold | 거래수 | 비중복 체결 | EEC | EEC multiplier | 최대 클러스터 비중 | 클러스터 기간·거래수 | entry-time 분포 | active-day 분포 |",
        "|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for fold in ("train_1", "train_2", "train_3"):
        best = current_best.get(fold, {})
        metric = current_trade_metrics.get(fold, {})
        clusters = [f"{row.get('start')}~{row.get('end')}:{row.get('event_count')}({float(row.get('trade_share', 0.0)):.1%})" for row in list(metric.get("event_clusters") or [])]
        lines.append(
            f"| {fold} | {best.get('trade_count')} | {best.get('eec_nonduplicate_event_count')} | "
            f"{metric.get('effective_event_count', 0.0):.6f} | {best.get('eec_multiplier')} | "
            f"{metric.get('max_cluster_trade_share', 0.0):.2%} | `{' ; '.join(clusters)}` | "
            f"`{json.dumps(metric.get('entry_time_concurrency_distribution') or {}, ensure_ascii=False, sort_keys=True)}` | "
            f"`{json.dumps(metric.get('active_day_concurrency_distribution') or {}, ensure_ascii=False, sort_keys=True)}` |"
        )

    lines.extend([
        "",
        "## 몰빵 개체 vs 분산 개체 순위 변화",
        "",
        "`eec_rank_shift.json`에 fold별 EEC 적용 전/후 fitness 순위 변화를 기록했다. 아래는 절대 순위 변화 상위 5개다.",
        "",
        "| fold | candidate | EEC | multiplier | rank before | rank after | delta |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for fold in ("train_1", "train_2", "train_3"):
        for row in list((rank_shift.get(fold) or {}).get("largest_rank_shifts") or [])[:5]:
            lines.append(
                f"| {fold} | `{row.get('hash')}` | {_safe_float(row.get('eec')):.6f} | {_safe_float(row.get('multiplier'), 1.0):.6f} | {row.get('rank_before_eec')} | {row.get('rank_after_eec')} | {row.get('rank_delta_after_minus_before')} |"
            )

    lines.extend([
        "",
        "## Gate·factor 병목",
        "",
        "| fold | 후보 | pass | win_rate gate 병목 | EEC penalized | mean EEC | mean multiplier | trade-count factor bins 전체/pass |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for fold in ("train_1", "train_2", "train_3"):
        row = dict((current_gate.get("folds") or {}).get(fold) or {})
        all_dist = dict(row.get("trade_count_distribution") or {})
        pass_dist = dict(row.get("pass_trade_count_distribution") or {})
        lines.append(
            f"| {fold} | {row.get('candidate_count', 0)} | {row.get('qualify_pass_count', 0)} | "
            f"{row.get('trade_count_met_but_win_rate_below_60_count', 0)} ({_safe_float(row.get('trade_count_met_but_win_rate_below_60_rate')):.2%}) | "
            f"{row.get('eec_penalized_count', 0)} ({_safe_float(row.get('eec_penalized_rate')):.2%}) | "
            f"{_safe_float(row.get('mean_effective_event_count')):.6f} | {_safe_float(row.get('mean_eec_multiplier'), 1.0):.6f} | "
            f"`{json.dumps(all_dist.get('factor_bins') or {}, ensure_ascii=False, sort_keys=True)}` / `{json.dumps(pass_dist.get('factor_bins') or {}, ensure_ascii=False, sort_keys=True)}` |"
        )

    lines.extend([
        "",
        "## Trade-level 로그",
        "",
        "`fold_best_trade_level.jsonl`에는 진입/청산일·가격, 청산 사유, 보유일, 실현손익, MAE, +0.5% 승/패, entry-time 동시 포지션 수에 더해 다음 EEC 필드를 기록한다.",
        "",
        "- `entry_fitness_effective_event_count`",
        "- `entry_fitness_eec_multiplier`",
        "- `entry_fitness_eec_cluster_index`",
        "- `entry_fitness_eec_cluster_trade_share`",
        "- `entry_fitness_eec_cluster_share_squared`",
        "- `entry_fitness_eec_trade_share`",
        "",
        "## 안전성",
        "",
        f"- manifest gate: {bool(current_final.get('manifest_gate_passed'))}",
        f"- 보호 SHA 불변: {bool(current_final.get('protected_unchanged'))}",
        f"- daemon 불변: {bool(current_final.get('daemon_unchanged'))}",
        f"- 병렬 재현성 probe: {bool((current_final.get('parallel_reproducibility_probe') or {}).get('passed'))}",
        f"- EEC activation patch: `{eec_v5.PATCH_TOKEN}`",
        f"- source git commit: `{args.source_git_commit}`",
    ])
    (out_dir / "readout.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for name in ("manifest.json", "official_final_summary.json"):
        path = out_dir / name
        payload = _read_json(path)
        payload["launch_command"] = launch
        payload["eec_penalty_comparison"] = comparison
        payload["fold_best_eec_summary"] = current_trade_metrics
        payload["eec_rank_shift"] = rank_shift
        payload["verdict_code"] = verdict_code
        payload["verdict_reason"] = verdict_reason
        _write_json(path, payload)
    runner._write_sha_manifest(out_dir)


def main(argv: list[str] | None = None) -> int:
    v3._build_cross_matrix = _build_cross_matrix
    v3._fold_best_from_population = _fold_best_from_population
    v4._fold_best_trade_rows = _fold_best_trade_rows
    v4._postprocess = _postprocess
    return int(v4.main(argv))


if __name__ == "__main__":
    base.mp.freeze_support()
    raise SystemExit(main())
