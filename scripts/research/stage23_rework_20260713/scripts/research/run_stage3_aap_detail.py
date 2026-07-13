#!/usr/bin/env python3
"""AAP 단일 종목 상세 Stage 3 qualify 재실행 runner.

목적
- qualify GA 40 population / 15 generations 축소 규모를 고정한다.
- 각 fold final top-40의 unique chromosome을 모두 보존한다.
- 후보 전체를 train_1/2/3에 early-stop 없이 cross-fold 재평가한다.
- pass 여부, 거래 수, 승률, 기대값, MDD와 MDD episode 근거를 남긴다.
- 각 fold GA best의 signal 통계와 trade-level 상세를 남긴다.
- qualify 통과 시에만 entry 이후 단계를 자동 진행한다.

시장 데이터는 repository-root SHA-pinned snapshot만 읽고 auto-fetch/재생성을
허용하지 않는다. 보호 파일과 daemon은 시작/종료 시 대조한다.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import importlib.util
import json
import math
import os
import statistics
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
REPOSITORY_ROOT = WORKSPACE_ROOT.parents[2]
BASELINE_LIGHT_RUNNER = WORKSPACE_ROOT / "scripts/research/run_stage3_baseline_light.py"

DETAIL_CONFIG = {
    "qualify_population": 40,
    "qualify_generations": 15,
    "top_n_qualify": 40,
    "entry_population": 40,
    "entry_generations": 15,
    "top_n_entry_pool": 40,
    "max_entry_candidates": 20,
    "exit_population": 40,
    "exit_generations": 15,
    "top_n_exit_per_entry": 3,
}
DEFAULT_SEED_BASE = 2026071301
DAEMON_PID = 494330
PROTECTED_PATHS = (
    REPOSITORY_ROOT / ".env",
    REPOSITORY_ROOT / "data/_system/market_history.csv",
    REPOSITORY_ROOT / "data/_system/market_history_v2.csv",
)
MDD_CLASSIFICATION_RULE = {
    "TYPE1_ACCIDENT": "MDD episode에 손실 거래가 1개이고 그 거래 보유일이 7일 미만",
    "TYPE2_NEGLECT": "MDD episode에 손실 거래가 2개 이상이거나 손실 거래 중 보유일 7일 이상",
    "NO_DRAWDOWN": "기존 backtest 요약식 기준 MDD가 0 이상",
}


def _load_light_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_stage3_aap_detail_light", BASELINE_LIGHT_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load baseline light runner: {BASELINE_LIGHT_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


light = _load_light_runner()
mod = light.mod
Rulebook = light.Rulebook


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


def _apply_detail_config() -> None:
    base = mod._base
    base.QUALIFY_POPULATION = DETAIL_CONFIG["qualify_population"]
    base.QUALIFY_GENERATIONS = DETAIL_CONFIG["qualify_generations"]
    base.TOP_N_QUALIFY = DETAIL_CONFIG["top_n_qualify"]
    base.ENTRY_POPULATION = DETAIL_CONFIG["entry_population"]
    base.ENTRY_GENERATIONS = DETAIL_CONFIG["entry_generations"]
    base.TOP_N_ENTRY_POOL = DETAIL_CONFIG["top_n_entry_pool"]
    base.EXIT_POPULATION = DETAIL_CONFIG["exit_population"]
    base.EXIT_GENERATIONS = DETAIL_CONFIG["exit_generations"]
    base.TOP_N_EXIT_PER_ENTRY = DETAIL_CONFIG["top_n_exit_per_entry"]
    base.DEFAULT_STAGE3_ENTRY_SELECTION = dataclasses.replace(
        base.DEFAULT_STAGE3_ENTRY_SELECTION,
        max_entry_candidates=DETAIL_CONFIG["max_entry_candidates"],
    )


def _validate_manifest_gate(market_metadata: Mapping[str, Any]) -> dict[str, Any]:
    expected_primary = mod.RESEARCH_MARKET_HISTORY_SOURCE.resolve()
    expected_v2 = mod.RESEARCH_MARKET_HISTORY_V2_SOURCE.resolve()
    primary = dict(market_metadata.get("primary") or {})
    v2 = dict(market_metadata.get("v2") or {})
    checks = {
        "root_single_source": market_metadata.get("source_mode") == "repository_root_sha_pinned_single_source",
        "auto_fetch_blocked": market_metadata.get("auto_fetch_enabled") is False,
        "auto_regenerate_blocked": market_metadata.get("auto_regenerate_enabled") is False,
        "fail_closed": market_metadata.get("fail_closed") is True,
        "fresh": bool((market_metadata.get("primary_freshness") or {}).get("fresh")),
        "primary_root_path": Path(str(primary.get("path", ""))).resolve() == expected_primary,
        "v2_root_path": Path(str(v2.get("path", ""))).resolve() == expected_v2,
        "primary_sha_fixed": primary.get("sha256") == mod.RESEARCH_MARKET_HISTORY_EXPECTED_SHA256,
        "v2_sha_fixed": v2.get("sha256") == mod.RESEARCH_MARKET_HISTORY_V2_EXPECTED_SHA256,
        "primary_required_columns": set(mod.RESEARCH_MARKET_PRIMARY_REQUIRED_COLUMNS).issubset(set(primary.get("required_columns") or [])),
        "v2_required_columns": set(mod.RESEARCH_MARKET_V2_REQUIRED_COLUMNS).issubset(set(v2.get("required_columns") or [])),
    }
    if not all(checks.values()):
        raise RuntimeError(f"manifest snapshot gate failed: {json.dumps(checks, ensure_ascii=False, sort_keys=True)}")
    return {"passed": True, "checks": checks, "metadata": dict(market_metadata)}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _trade_value(trade: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    return _finite(trade.get(key), default)


def _mdd_episode(trades_raw: Iterable[Mapping[str, Any]], reported_mdd: Any) -> dict[str, Any]:
    trades = [dict(row) for row in trades_raw if isinstance(row, Mapping)]
    pnl = np.asarray([_trade_value(row, "pnl_pct") for row in trades], dtype=float)
    mdd = _finite(reported_mdd)
    if len(pnl) == 0 or mdd >= -1e-12:
        return {
            "mdd_pct": mdd,
            "mdd_type": "NO_DRAWDOWN",
            "peak_trade_index": None,
            "trough_trade_index": None,
            "episode_trade_count": 0,
            "episode_loss_count": 0,
            "long_loss_count": 0,
            "dominant_loss_share": 0.0,
            "episode_trades": [],
        }

    cumulative = np.cumsum(pnl)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    trough = int(np.argmin(drawdown))
    peak_value = float(running_max[trough])
    peak_candidates = np.flatnonzero(np.isclose(cumulative[: trough + 1], peak_value, rtol=0.0, atol=1e-12))
    peak = int(peak_candidates[-1]) if len(peak_candidates) else 0
    start = min(peak + 1, trough)
    episode_indices = list(range(start, trough + 1))
    episode_rows = [trades[idx] for idx in episode_indices]
    loss_rows = [(idx, row) for idx, row in zip(episode_indices, episode_rows) if _trade_value(row, "pnl_pct") <= 0.0]
    long_loss_count = sum(1 for _, row in loss_rows if int(_finite(row.get("holding_days"), 0.0)) >= 7)
    worst_loss = min((_trade_value(row, "pnl_pct") for _, row in loss_rows), default=0.0)
    dominant_share = min(1.0, abs(worst_loss) / abs(mdd)) if abs(mdd) > 1e-12 else 0.0
    mdd_type = "TYPE2_NEGLECT" if len(loss_rows) >= 2 or long_loss_count > 0 else "TYPE1_ACCIDENT"

    compact = []
    for idx, row in zip(episode_indices, episode_rows):
        compact.append(
            {
                "trade_index": idx + 1,
                "entry_date": row.get("entry_fill_date", row.get("entry_date")),
                "exit_date": row.get("exit_date"),
                "exit_reason": row.get("exit_reason"),
                "holding_days": int(_finite(row.get("holding_days"), 0.0)),
                "pnl_pct": _trade_value(row, "pnl_pct"),
            }
        )
    return {
        "mdd_pct": mdd,
        "mdd_type": mdd_type,
        "peak_trade_index": peak + 1,
        "trough_trade_index": trough + 1,
        "episode_trade_count": len(episode_rows),
        "episode_loss_count": len(loss_rows),
        "long_loss_count": int(long_loss_count),
        "dominant_loss_share": float(dominant_share),
        "episode_trades": compact,
    }


def _percentile(values: list[int], q: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=float), q))


def _trade_count_distribution(rows: list[Mapping[str, Any]], min_trades: int) -> dict[str, Any]:
    values = [int(_finite(row.get("trade_count"), 0.0)) for row in rows]
    below = sum(1 for value in values if value < min_trades)
    return {
        "candidate_count": len(values),
        "min": min(values) if values else None,
        "p25": _percentile(values, 0.25),
        "median": float(statistics.median(values)) if values else None,
        "p75": _percentile(values, 0.75),
        "max": max(values) if values else None,
        "mean": float(statistics.mean(values)) if values else None,
        "support_min_trades": int(min_trades),
        "below_support_count": int(below),
        "below_support_rate": float(below / len(values)) if values else None,
    }


def _candidate_trade_rows(
    *,
    ticker: str,
    candidate_hash: str,
    period_label: str,
    result: Any,
    mdd_episode: Mapping[str, Any],
) -> list[dict[str, Any]]:
    trades = [dict(row) for row in list(getattr(result, "trades", []) or []) if isinstance(row, Mapping)]
    pnl = np.asarray([_trade_value(row, "pnl_pct") for row in trades], dtype=float)
    cumulative = np.cumsum(pnl) if len(pnl) else np.asarray([], dtype=float)
    running_max = np.maximum.accumulate(cumulative) if len(cumulative) else np.asarray([], dtype=float)
    drawdown = cumulative - running_max if len(cumulative) else np.asarray([], dtype=float)
    episode_indices = {
        int(row["trade_index"]) - 1
        for row in list(mdd_episode.get("episode_trades") or [])
        if row.get("trade_index") is not None
    }
    rows: list[dict[str, Any]] = []
    for idx, trade in enumerate(trades):
        signal_tape = trade.get("entry_signal_tape") if isinstance(trade.get("entry_signal_tape"), Mapping) else {}
        interval_checks = dict(signal_tape.get("interval_checks") or {})
        boolean_checks = [bool(value) for value in interval_checks.values() if isinstance(value, (bool, np.bool_))]
        rows.append(
            {
                "ticker": ticker,
                "stage": "qualify_fold_best",
                "candidate_hash": candidate_hash,
                "period_label": period_label,
                "trade_index": idx + 1,
                "entry_signal_date": trade.get("entry_signal_date"),
                "entry_fill_date": trade.get("entry_fill_date", trade.get("entry_date")),
                "entry_date": trade.get("entry_date"),
                "entry_price": trade.get("entry_price"),
                "exit_date": trade.get("exit_date"),
                "exit_price": trade.get("exit_price"),
                "exit_reason": trade.get("exit_reason"),
                "holding_days": trade.get("holding_days"),
                "pnl_pct": trade.get("pnl_pct"),
                "pnl_krw": trade.get("pnl_krw"),
                "cumulative_pnl_pct": float(cumulative[idx]),
                "running_peak_pnl_pct": float(running_max[idx]),
                "drawdown_pct": float(drawdown[idx]),
                "in_mdd_episode": idx in episode_indices,
                "entry_features": dict(signal_tape.get("entry_features") or {}),
                "interval_checks": interval_checks,
                "strict_interval_pass": signal_tape.get("strict_interval_pass"),
                "all_boolean_interval_checks_pass": bool(boolean_checks) and all(boolean_checks),
                "quality_score": signal_tape.get("quality_score"),
                "quality_threshold": signal_tape.get("threshold"),
                "market_score": signal_tape.get("market_score"),
                "sector_score": signal_tape.get("sector_score"),
                "vix_level": signal_tape.get("vix_level"),
                "entry_execution_mode": trade.get("entry_execution_mode"),
                "exit_execution_mode": trade.get("exit_execution_mode"),
            }
        )
    return rows


def _generation_summary(ga_calls: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call in ga_calls:
        history = list(call.get("history") or [])
        bests = [_finite(row.get("best_fitness")) for row in history]
        rows.append(
            {
                "call_index": call.get("call_index"),
                "gene_scope": call.get("gene_scope"),
                "generations_recorded": len(history),
                "first_best_fitness": bests[0] if bests else None,
                "final_best_fitness": bests[-1] if bests else None,
                "best_fitness_improvement": (bests[-1] - bests[0]) if bests else None,
                "monotonic_non_decreasing": all(bests[idx] >= bests[idx - 1] - 1e-12 for idx in range(1, len(bests))),
                "generations_run": call.get("generations_run"),
                "final_population_count": call.get("final_population_count"),
            }
        )
    return rows


def run_detailed_qualify(
    ticker: str,
    out_dir: Path,
    *,
    seed_base: int,
    code_commit: str,
    ctx: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Rulebook], dict[str, Rulebook], list[dict[str, Any]]]:
    started = time.time()
    config = mod._base.DEFAULT_STAGE3_QUALIFY
    candidates_by_hash: dict[str, Rulebook] = {}
    candidate_sources: dict[str, list[dict[str, Any]]] = {}
    ga_summaries: list[dict[str, Any]] = []
    best_by_split: dict[str, Rulebook] = {}

    for idx, split in enumerate(mod._base.TRAIN_SPLITS, 1):
        split_seed = seed_base + idx
        domain = mod.build_entry_feature_domain(ctx, start=split["start"], end=split["end"])

        def evaluate_fn(rulebook: Any, s: dict[str, str] = split) -> float:
            result = mod.run_entry_backtest_period(rulebook, ctx, start=s["start"], end=s["end"])
            return mod._base.safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

        ga = mod._base.run_ga(
            base_rulebook=ctx["base_rulebook"],
            evaluate_fn=evaluate_fn,
            ga_config=mod._base.make_ga_config(
                population=DETAIL_CONFIG["qualify_population"],
                generations=DETAIL_CONFIG["qualify_generations"],
                seed=split_seed,
            ),
            gene_scope="entry",
            entry_feature_domain=domain,
        )
        best_by_split[str(split["label"])] = copy.deepcopy(ga.best)
        top_rulebooks = mod._base.collect_top_rulebooks(ga, DETAIL_CONFIG["top_n_qualify"])
        for rank, rb in enumerate(top_rulebooks, 1):
            candidate_hash = mod._base.compute_rulebook_hash(rb)
            candidate_sources.setdefault(candidate_hash, []).append(
                {
                    "source_split": split["label"],
                    "source_rank": rank,
                    "source_fitness": _finite(getattr(rb, "fitness", 0.0)),
                }
            )
            current = candidates_by_hash.get(candidate_hash)
            if current is None or _finite(getattr(rb, "fitness", 0.0)) > _finite(getattr(current, "fitness", 0.0)):
                candidates_by_hash[candidate_hash] = copy.deepcopy(rb)
        ga_summaries.append(
            {
                "split": dict(split),
                "seed": split_seed,
                "gene_scope": "entry",
                "entry_domain_sample_count": min(int(value["sample_count"]) for value in domain.values()),
                "generations_run": int(getattr(ga, "generations_run", 0) or 0),
                "final_population_count": len(ga.final_population),
                "top_count": len(top_rulebooks),
                "best_fitness": _finite(getattr(ga.best, "fitness", 0.0)),
                "best_hash": mod._base.compute_rulebook_hash(ga.best),
            }
        )

    candidate_hashes = sorted(candidates_by_hash)
    candidate_rows = [
        {
            "ticker": ticker,
            "rulebook_hash": candidate_hash,
            "sources": candidate_sources.get(candidate_hash, []),
            "rulebook": candidates_by_hash[candidate_hash].to_dict(),
        }
        for candidate_hash in candidate_hashes
    ]
    light._append_jsonl(out_dir / "qualify_candidate_rulebooks.jsonl", candidate_rows)

    matrix_rows: list[dict[str, Any]] = []
    mdd_rows: list[dict[str, Any]] = []
    metrics_by_hash: dict[str, dict[str, dict[str, Any]]] = {candidate_hash: {} for candidate_hash in candidate_hashes}
    pass_by_hash: dict[str, dict[str, bool]] = {candidate_hash: {} for candidate_hash in candidate_hashes}
    year_pass_counts: dict[str, int] = {}
    member_score_stats: dict[str, dict[str, Any]] = {}
    trade_distributions: dict[str, dict[str, Any]] = {}
    fail_reason_counter: Counter[str] = Counter()
    result_maps: dict[str, dict[str, Any]] = {}

    for split in mod._base.TRAIN_SPLITS:
        label = str(split["label"])
        raw_rows: list[dict[str, Any]] = []
        result_by_hash: dict[str, Any] = {}
        for rank, candidate_hash in enumerate(candidate_hashes, 1):
            result = mod.run_entry_backtest_period(
                candidates_by_hash[candidate_hash],
                ctx,
                start=split["start"],
                end=split["end"],
            )
            result_by_hash[candidate_hash] = result
            raw_rows.append(
                {
                    "ticker": ticker,
                    "label": label,
                    "period_label": label,
                    "rulebook_hash": candidate_hash,
                    "rank_is": rank,
                    "oos": mod._base.result_metrics(result),
                }
            )
        result_maps[label] = result_by_hash
        scored = mod._base._score_period_candidates(raw_rows)
        split_rows: list[dict[str, Any]] = []
        scores: list[float] = []
        pass_count = 0
        for row in scored:
            candidate_hash = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            member_score = _finite(row.get("oos_member_score"))
            metrics["member_score"] = member_score
            metrics["fitness"] = _finite(row.get("fitness"))
            trade_count = int(_finite(metrics.get("trade_count"), 0.0))
            expectancy = _finite(metrics.get("expectancy_pct"))
            pass_trade = trade_count >= int(config.min_trades)
            pass_member = member_score >= float(config.min_member_score)
            pass_expectancy = expectancy >= float(config.qualify_min_expectancy_pct)
            passed = bool(pass_trade and pass_member and pass_expectancy)
            if passed:
                pass_count += 1
            fail_metrics = []
            if not pass_trade:
                fail_metrics.append("trade_count")
                fail_reason_counter["trade_count"] += 1
            if not pass_member:
                fail_metrics.append("member_score")
                fail_reason_counter["member_score"] += 1
            if not pass_expectancy:
                fail_metrics.append("expectancy_pct")
                fail_reason_counter["expectancy_pct"] += 1
            result = result_by_hash[candidate_hash]
            episode = _mdd_episode(getattr(result, "trades", []) or [], metrics.get("max_drawdown_pct"))
            matrix = {
                "ticker": ticker,
                "candidate_hash": candidate_hash,
                "period_label": label,
                "pass": passed,
                "pass_trade_count": pass_trade,
                "pass_member_score": pass_member,
                "pass_expectancy": pass_expectancy,
                "fail_metrics": fail_metrics,
                "trade_count": trade_count,
                "win_rate": _finite(metrics.get("win_rate")),
                "expectancy_pct": expectancy,
                "profit_factor": _finite(metrics.get("profit_factor")),
                "max_drawdown_pct": _finite(metrics.get("max_drawdown_pct")),
                "member_score": member_score,
                "fitness": _finite(metrics.get("fitness")),
                "member_score_components": dict(row.get("oos_member_score_components") or {}),
                "mdd_type": episode["mdd_type"],
            }
            matrix_rows.append(matrix)
            split_rows.append(matrix)
            mdd_rows.append(
                {
                    "ticker": ticker,
                    "candidate_hash": candidate_hash,
                    "period_label": label,
                    **episode,
                }
            )
            metrics_by_hash[candidate_hash][label] = dict(metrics)
            pass_by_hash[candidate_hash][label] = passed
            scores.append(member_score)
        year_pass_counts[label] = pass_count
        member_score_stats[label] = {
            "count": len(scores),
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "mean": statistics.mean(scores) if scores else None,
            "median": statistics.median(scores) if scores else None,
        }
        trade_distributions[label] = _trade_count_distribution(split_rows, int(config.min_trades))

    pass_count_distribution: Counter[int] = Counter()
    pass_hash_samples: dict[str, list[str]] = {"all3": [], "all2": [], "all1": [], "all0": []}
    candidate_pass_rows: list[dict[str, Any]] = []
    for candidate_hash in candidate_hashes:
        vector = {str(split["label"]): bool(pass_by_hash[candidate_hash].get(str(split["label"]), False)) for split in mod._base.TRAIN_SPLITS}
        count = sum(vector.values())
        pass_count_distribution[count] += 1
        key = {3: "all3", 2: "all2", 1: "all1", 0: "all0"}[count]
        if len(pass_hash_samples[key]) < 20:
            pass_hash_samples[key].append(candidate_hash)
        candidate_pass_rows.append(
            {
                "ticker": ticker,
                "candidate_hash": candidate_hash,
                "pass_count": count,
                "pass_vector": vector,
            }
        )
    light._append_jsonl(out_dir / "qualify_cross_fold_matrix.jsonl", matrix_rows)
    light._append_jsonl(out_dir / "qualify_candidate_pass_vectors.jsonl", candidate_pass_rows)
    light._append_jsonl(out_dir / "qualify_mdd_episodes.jsonl", mdd_rows)
    light._write_json(out_dir / "qualify_trade_count_distribution.json", trade_distributions)

    mdd_type_counts = Counter(row["mdd_type"] for row in mdd_rows)
    typed_total = mdd_type_counts["TYPE1_ACCIDENT"] + mdd_type_counts["TYPE2_NEGLECT"]
    mdd_summary = {
        "classification_rule": MDD_CLASSIFICATION_RULE,
        "candidate_fold_row_count": len(mdd_rows),
        "type_counts": dict(mdd_type_counts),
        "type1_ratio_among_drawdowns": float(mdd_type_counts["TYPE1_ACCIDENT"] / typed_total) if typed_total else None,
        "type2_ratio_among_drawdowns": float(mdd_type_counts["TYPE2_NEGLECT"] / typed_total) if typed_total else None,
    }
    light._write_json(out_dir / "qualify_mdd_type_summary.json", mdd_summary)

    all3 = int(pass_count_distribution[3])
    result = {
        "ticker": ticker,
        "stage": "qualify",
        "qualified": all3 > 0,
        "execution_scale": "REDUCED_AAP_DETAIL_QUALIFY_40X15",
        "detail_config": DETAIL_CONFIG,
        "config": dataclasses.asdict(config),
        "periods": list(mod._base.TRAIN_SPLITS),
        "seed_base": seed_base,
        "code_commit": code_commit,
        "entry_execution_semantics": mod.ENTRY_PHASE_CACHE_MODE,
        "technical_feature_lag_mode": mod.TECHNICAL_FEATURE_LAG_MODE,
        "technical_feature_lag_trading_days": mod.TECHNICAL_FEATURE_LAG_TRADING_DAYS,
        "market_context_lag_days": int(getattr(mod._base, "FEATURE_LAG_DAYS", 1)),
        "data_start": ctx.get("data_start"),
        "data_end": ctx.get("data_end"),
        "ga_summaries": ga_summaries,
        "unique_candidate_count": len(candidate_hashes),
        "candidate_source_row_count": sum(len(value) for value in candidate_sources.values()),
        "year_pass_counts": year_pass_counts,
        "member_score_stats": member_score_stats,
        "trade_count_distributions": trade_distributions,
        "pass_count_distribution": {
            "all3": int(pass_count_distribution[3]),
            "all2": int(pass_count_distribution[2]),
            "all1": int(pass_count_distribution[1]),
            "all0": int(pass_count_distribution[0]),
        },
        "pass_hash_samples": pass_hash_samples,
        "all3_pass_count": all3,
        "all3_pass_hash_samples": pass_hash_samples["all3"],
        "fail_reason_metric_counts": dict(sorted(fail_reason_counter.items())),
        "early_stopped": False,
        "early_stop_reason": None,
        "mdd_type_summary": mdd_summary,
        "elapsed_seconds": time.time() - started,
        "note": "all unique final top-40 candidates from three fold GAs were fully evaluated on all three folds",
    }
    light._write_json(out_dir / "qualify_result.json", result)
    return result, candidates_by_hash, best_by_split, matrix_rows


def _build_readout(
    *,
    out_dir: Path,
    qualify: Mapping[str, Any],
    fold_best_rows: list[Mapping[str, Any]],
    generation_summary: list[Mapping[str, Any]],
    protected_start: Mapping[str, str],
    protected_end: Mapping[str, str],
    daemon_start: Mapping[str, Any],
    daemon_end: Mapping[str, Any],
) -> str:
    distributions = dict(qualify.get("trade_count_distributions") or {})
    pass_dist = dict(qualify.get("pass_count_distribution") or {})
    below_rates = [float(row.get("below_support_rate") or 0.0) for row in distributions.values()]
    medians = [float(row.get("median") or 0.0) for row in distributions.values()]
    fold_best_trade_count = sum(int(row.get("trade_count", 0) or 0) for row in fold_best_rows)
    interval_break_count = sum(int(row.get("interval_break_count", 0) or 0) for row in fold_best_rows)
    interval_break_rate = interval_break_count / fold_best_trade_count if fold_best_trade_count else 0.0
    broadly_low_support = sum(1 for median in medians if median < 5.0) >= 2 or sum(1 for rate in below_rates if rate >= 0.5) >= 2
    sufficient_support = all(median >= 5.0 for median in medians) and statistics.mean(below_rates) < 0.5 if below_rates else False
    all2 = int(pass_dist.get("all2", 0) or 0)
    interval_dominant = interval_break_rate >= 0.70
    if broadly_low_support and interval_dominant and all2 > 0:
        verdict = "CAUSE_A_TOO_TIGHT"
        reason = "거래 수 분포가 전반적으로 support 바닥이고 fold-best interval-break가 지배적이며 all2 후보가 존재"
    elif sufficient_support and all2 == 0:
        verdict = "CAUSE_B_INFO_LACK"
        reason = "거래 수 분포는 support를 충족하지만 2개 fold까지 동시에 통과한 후보가 없음"
    else:
        verdict = "MIXED / INCONCLUSIVE"
        reason = "지시된 A/B 필요조건 중 어느 한쪽도 완전히 충족하지 않음"
    mdd = dict(qualify.get("mdd_type_summary") or {})
    lines = [
        "# AAP 상세 qualify 재실행 readout",
        "",
        f"- 실행 규모: **축소 qualify population {DETAIL_CONFIG['qualify_population']} / generations {DETAIL_CONFIG['qualify_generations']}**",
        f"- seed base: `{qualify.get('seed_base')}`",
        f"- unique 후보: {qualify.get('unique_candidate_count')}",
        f"- 최종 판정: **`{verdict}`**",
        f"- 판정 근거: {reason}",
        f"- qualify 통과: {bool(qualify.get('qualified'))}",
        "",
        "## Cross-fold 통과 분포",
        "",
        f"- all3: {pass_dist.get('all3', 0)}",
        f"- all2: {pass_dist.get('all2', 0)}",
        f"- all1: {pass_dist.get('all1', 0)}",
        f"- all0: {pass_dist.get('all0', 0)}",
        "",
        "## 거래 수 분포",
        "",
        "| fold | min | median | max | 5건 미달 | 미달 비율 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label in ("train_1", "train_2", "train_3"):
        row = distributions.get(label, {})
        lines.append(
            f"| {label} | {row.get('min')} | {row.get('median')} | {row.get('max')} | {row.get('below_support_count')} | {float(row.get('below_support_rate') or 0.0):.2%} |"
        )
    lines.extend(
        [
            "",
            "## Fold-best 성과와 청산",
            "",
            "| fold | 거래 | 승률 | 기대값 | MDD | MDD 유형 | interval-break |",
            "|---|---:|---:|---:|---:|---|---:|",
        ]
    )
    for row in fold_best_rows:
        lines.append(
            f"| {row.get('period_label')} | {row.get('trade_count')} | {float(row.get('win_rate') or 0.0):.2f}% | {float(row.get('expectancy_pct') or 0.0):.6f}% | {float(row.get('max_drawdown_pct') or 0.0):.6f}% | {row.get('mdd_type')} | {row.get('interval_break_count')} |"
        )
    lines.extend(
        [
            "",
            f"- fold-best interval-break 비율: {interval_break_count}/{fold_best_trade_count} = {interval_break_rate:.2%}",
            "",
            "## MDD 유형 분포 — 전체 후보×fold",
            "",
            f"- 유형1 사고: {(mdd.get('type_counts') or {}).get('TYPE1_ACCIDENT', 0)}",
            f"- 유형2 방치: {(mdd.get('type_counts') or {}).get('TYPE2_NEGLECT', 0)}",
            f"- 무낙폭: {(mdd.get('type_counts') or {}).get('NO_DRAWDOWN', 0)}",
            f"- 유형1 비율(낙폭 발생분): {float(mdd.get('type1_ratio_among_drawdowns') or 0.0):.2%}",
            f"- 유형2 비율(낙폭 발생분): {float(mdd.get('type2_ratio_among_drawdowns') or 0.0):.2%}",
            "- 유형2 비중이 높을수록 청산 로직 완화는 손실 누적·장기 방치 위험을 키울 수 있음.",
            "",
            "## 수렴/무한루프 점검",
            "",
        ]
    )
    for row in generation_summary:
        lines.append(
            f"- GA call {row.get('call_index')}: {row.get('generations_recorded')}세대 기록, best {row.get('first_best_fitness')} → {row.get('final_best_fitness')}, monotonic={row.get('monotonic_non_decreasing')}"
        )
    lines.extend(
        [
            "",
            "## 보호 게이트",
            "",
            f"- 보호 SHA 시작/종료 동일: {dict(protected_start) == dict(protected_end)}",
            f"- daemon PID/starttime 동일: {daemon_start.get('pid') == daemon_end.get('pid') and daemon_start.get('starttime_ticks') == daemon_end.get('starttime_ticks')}",
            "- auto-fetch/auto-regenerate: 차단 유지",
            "- root SHA-pinned 단일 market snapshot과 필수 컬럼/freshness gate 통과 후 GA 시작",
            "",
            "## 산출물",
            "",
            "- `qualify_cross_fold_matrix.jsonl`: 모든 후보×3 folds pass/metric",
            "- `qualify_candidate_pass_vectors.jsonl`: all3/all2/all1/all0 판정 근거",
            "- `qualify_trade_count_distribution.json`: fold별 거래 수 분포",
            "- `qualify_mdd_episodes.jsonl`: 후보별 fold MDD episode와 관련 거래/청산 사유",
            "- `fold_best_trade_level_details.jsonl`: fold-best 진입·청산·5-feature·interval 체크",
            "- `generation_best_fitness.jsonl`: 세대별 best/average fitness",
        ]
    )
    return "\n".join(lines) + "\n"


def run(ticker: str, out_dir: Path, seed_base: int) -> dict[str, Any]:
    if ticker != "AAP":
        raise ValueError("this runner is restricted to AAP")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"output directory must be new or empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    protected_start = _protected_snapshot()
    daemon_start = _daemon_snapshot()
    market_frame, market_metadata = light._preflight_market_snapshot()
    manifest_gate = _validate_manifest_gate(market_metadata)
    exit_priority = light._exit_priority_gate()
    ctx, ohlcv_metadata = light._load_snapshot_context(ticker, market_frame)
    started = time.time()

    mod.ensure_research_experiment_header(out_dir, ticker=ticker, seed_base=seed_base, stage="all")
    light._update_manifest(
        out_dir,
        {
            "runner": "scripts/research/run_stage3_aap_detail.py",
            "execution_scale": "REDUCED_AAP_DETAIL_QUALIFY_40X15",
            "detail_config": DETAIL_CONFIG,
            "scope": "AAP_ONLY",
            "market_snapshot_manifest_gate": manifest_gate,
            "market_snapshot_preflight": market_metadata,
            "ohlcv_snapshot": ohlcv_metadata,
            "entry_phase_exit_priority_gate": exit_priority,
            "qualify_individual_policy": "preserve_all_top40_unique_cross_fold_full_matrix",
            "early_stop_disabled_for_diagnostics": True,
            "protected_sha_start": protected_start,
            "daemon_start": daemon_start,
            "external_fetch_enabled": False,
        },
    )

    ga_calls, original_run_ga = light._install_ga_trace()
    downstream_summaries: list[dict[str, Any]] = []
    stop_reason: str | None = None
    try:
        code_commit = mod._base.resolve_code_commit(mod._base.PROJECT_ROOT)
        qualify, candidates_by_hash, best_by_split, _ = run_detailed_qualify(
            ticker,
            out_dir,
            seed_base=seed_base,
            code_commit=code_commit,
            ctx=ctx,
        )

        fold_best_signal_rows: list[dict[str, Any]] = []
        fold_best_trade_rows: list[dict[str, Any]] = []
        fold_best_summary_rows: list[dict[str, Any]] = []
        for split in mod._base.TRAIN_SPLITS:
            label = str(split["label"])
            rb = best_by_split[label]
            candidate_hash = mod._base.compute_rulebook_hash(rb)
            result = mod.run_entry_backtest_period(rb, ctx, start=split["start"], end=split["end"])
            metrics = mod._base.result_metrics(result)
            episode = _mdd_episode(getattr(result, "trades", []) or [], metrics.get("max_drawdown_pct"))
            fold_best_signal_rows.append(
                light._signal_stats(
                    ticker=ticker,
                    stage="qualify_fold_best",
                    candidate_hash=candidate_hash,
                    period_label=label,
                    rb=rb,
                    ctx=ctx,
                    result=result,
                )
            )
            fold_best_trade_rows.extend(
                _candidate_trade_rows(
                    ticker=ticker,
                    candidate_hash=candidate_hash,
                    period_label=label,
                    result=result,
                    mdd_episode=episode,
                )
            )
            exit_counts = Counter(str(row.get("exit_reason") or "") for row in list(getattr(result, "trades", []) or []))
            fold_best_summary_rows.append(
                {
                    "ticker": ticker,
                    "period_label": label,
                    "candidate_hash": candidate_hash,
                    "trade_count": int(_finite(metrics.get("trade_count"), 0.0)),
                    "win_rate": _finite(metrics.get("win_rate")),
                    "expectancy_pct": _finite(metrics.get("expectancy_pct")),
                    "profit_factor": _finite(metrics.get("profit_factor")),
                    "max_drawdown_pct": _finite(metrics.get("max_drawdown_pct")),
                    "mdd_type": episode["mdd_type"],
                    "mdd_episode": episode,
                    "exit_reason_counts": dict(exit_counts),
                    "interval_break_count": int(exit_counts.get("entry_interval_break", 0)),
                }
            )
        light._append_jsonl(out_dir / "fold_best_signal_statistics.jsonl", fold_best_signal_rows)
        light._append_jsonl(out_dir / "fold_best_trade_level_details.jsonl", fold_best_trade_rows)
        light._write_json(out_dir / "fold_best_summary.json", fold_best_summary_rows)

        if not bool(qualify.get("qualified")):
            stop_reason = "qualify_failed"
        else:
            entry = mod.run_entry_ga(
                ticker,
                out_dir,
                seed_base=seed_base,
                use_fitness_cache=False,
                code_commit=code_commit,
                context=ctx,
            )
            downstream_summaries.append(entry)
            entry_rows = light._read_jsonl(out_dir / "entry_rulebooks.jsonl")
            if not entry_rows:
                stop_reason = "no_entry_survivor"
            else:
                exit_summary = mod._base.run_exit_ga(
                    ticker,
                    out_dir,
                    seed_base=seed_base,
                    weights=mod._base.DEFAULT_EXIT_FITNESS_WEIGHTS,
                    context=ctx,
                )
                downstream_summaries.append(exit_summary)
                final_rows = light._read_jsonl(out_dir / "final_rulebooks.jsonl")
                if not final_rows:
                    stop_reason = "no_exit_candidate"
                else:
                    validate = mod._base.run_validate(ticker, out_dir, seed_base=seed_base, context=ctx)
                    downstream_summaries.append(validate)

        generation_rows = [row for call in ga_calls for row in list(call.get("history") or [])]
        generation_summary = _generation_summary(ga_calls)
        light._append_jsonl(out_dir / "generation_best_fitness.jsonl", generation_rows)
        light._write_json(out_dir / "generation_convergence_summary.json", generation_summary)

        protected_end = _protected_snapshot()
        daemon_end = _daemon_snapshot()
        if protected_start != protected_end:
            raise RuntimeError("protected file SHA changed during run")
        if daemon_start.get("starttime_ticks") != daemon_end.get("starttime_ticks"):
            raise RuntimeError("daemon PID was restarted or replaced during run")

        readout = _build_readout(
            out_dir=out_dir,
            qualify=qualify,
            fold_best_rows=fold_best_summary_rows,
            generation_summary=generation_summary,
            protected_start=protected_start,
            protected_end=protected_end,
            daemon_start=daemon_start,
            daemon_end=daemon_end,
        )
        (out_dir / "readout.md").write_text(readout, encoding="utf-8")

        final = {
            "ticker": ticker,
            "execution_scale": "REDUCED_AAP_DETAIL_QUALIFY_40X15",
            "detail_config": DETAIL_CONFIG,
            "qualified": bool(qualify.get("qualified")),
            "qualify_pass_count_distribution": qualify.get("pass_count_distribution"),
            "stop_reason": stop_reason,
            "downstream_summaries": downstream_summaries,
            "protected_sha_start": protected_start,
            "protected_sha_end": protected_end,
            "protected_unchanged": protected_start == protected_end,
            "daemon_start": daemon_start,
            "daemon_end": daemon_end,
            "daemon_unchanged": daemon_start.get("starttime_ticks") == daemon_end.get("starttime_ticks"),
            "elapsed_seconds": time.time() - started,
        }
        light._write_json(out_dir / "detail_final_summary.json", final)
        light._write_json(out_dir / "last_run_summary.json", {"ticker": ticker, "stage": "all", "summaries": [qualify, *downstream_summaries]})
        light._update_manifest(
            out_dir,
            {
                "detail_run_completed": True,
                "detail_run_stop_reason": stop_reason,
                "protected_sha_end": protected_end,
                "protected_unchanged": True,
                "daemon_end": daemon_end,
                "daemon_unchanged": True,
                "qualify_pass_count_distribution": qualify.get("pass_count_distribution"),
                "elapsed_seconds": final["elapsed_seconds"],
            },
        )
        print(json.dumps({"event": "stage3_aap_detail_done", **final}, ensure_ascii=False, default=str), flush=True)
        return final
    finally:
        mod._base.run_ga = original_run_ga


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AAP detailed qualify 40x15")
    parser.add_argument("--ticker", default="AAP", choices=["AAP"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", type=int, default=DEFAULT_SEED_BASE)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _apply_detail_config()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with log_path.open("w", encoding="utf-8") as log_handle:
        sys.stdout = _Tee(original_stdout, log_handle)
        sys.stderr = _Tee(original_stderr, log_handle)
        try:
            run(args.ticker, out_dir, int(args.seed_base))
            return 0
        except Exception as exc:
            failure = {
                "event": "stage3_aap_detail_failed",
                "ticker": args.ticker,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            print(json.dumps(failure, ensure_ascii=False), flush=True)
            try:
                light._write_json(out_dir / "failure.json", failure)
            except Exception:
                pass
            return 2
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if __name__ == "__main__":
    raise SystemExit(main())
