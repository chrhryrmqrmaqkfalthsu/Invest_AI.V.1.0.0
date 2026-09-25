#!/usr/bin/env python3
"""이번 실행 전용 축소 Stage 3 baseline runner.

- qualify 10x3, entry 10x3, exit 10x3
- qualify top 3, entry pool 3 / survivor 최대 3, exit top 1
- root SHA-pinned market snapshot만 사용
- 기존 AAP/POWI OHLCV snapshot만 사용하며 외부 fetch 없음
- 상세 signal/trade/generation/CE-BOIL 진단을 실행 디렉터리에 기록
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import importlib.util
import inspect
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
REPOSITORY_ROOT = WORKSPACE_ROOT.parents[2]
BASELINE_RUNNER = WORKSPACE_ROOT / "scripts/research/run_stage3_aggressive.py"
OHLCV_SNAPSHOT_ROOT = REPOSITORY_ROOT / "data/_system/analysis/ohlc_snapshot_20260707"
EXPECTED_OHLCV_SHA256 = {
    "AAP": "6a07b754f5ea60983e16ecc91115496495bd41c090fa837f381a62340c3f3717",
    "POWI": "bd683b376899ff9784eb86a807bd154f74502c8f0bb025ceb120b3e16b8fb400",
}
LIGHT_CONFIG = {
    "qualify_population": 10,
    "qualify_generations": 3,
    "entry_population": 10,
    "entry_generations": 3,
    "exit_population": 10,
    "exit_generations": 3,
    "top_n_qualify": 3,
    "top_n_entry_pool": 3,
    "max_entry_candidates": 3,
    "top_n_exit_per_entry": 1,
}


def _load_baseline_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_stage3_baseline_light_core", BASELINE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Stage3 baseline runner: {BASELINE_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_baseline_runner()

from engine.core.indicators import calc_indicators  # noqa: E402
from engine.pipeline.context import attach_sell_omen_scores  # noqa: E402
from engine.strategies.evaluator import extract_entry_features  # noqa: E402
from engine.strategies.rulebook import (  # noqa: E402
    ENTRY_INTERVAL_SPECS,
    Rulebook,
    default_rulebook,
    validate_entry_feature_domains,
    validate_entry_intervals,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    mod._base.write_json(path, value)


def _append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    mod._base.append_jsonl(path, list(rows))


def _apply_light_config() -> None:
    base = mod._base
    base.QUALIFY_POPULATION = LIGHT_CONFIG["qualify_population"]
    base.QUALIFY_GENERATIONS = LIGHT_CONFIG["qualify_generations"]
    base.ENTRY_POPULATION = LIGHT_CONFIG["entry_population"]
    base.ENTRY_GENERATIONS = LIGHT_CONFIG["entry_generations"]
    base.EXIT_POPULATION = LIGHT_CONFIG["exit_population"]
    base.EXIT_GENERATIONS = LIGHT_CONFIG["exit_generations"]
    base.TOP_N_QUALIFY = LIGHT_CONFIG["top_n_qualify"]
    base.TOP_N_ENTRY_POOL = LIGHT_CONFIG["top_n_entry_pool"]
    base.TOP_N_EXIT_PER_ENTRY = LIGHT_CONFIG["top_n_exit_per_entry"]
    base.DEFAULT_STAGE3_ENTRY_SELECTION = dataclasses.replace(
        base.DEFAULT_STAGE3_ENTRY_SELECTION,
        max_entry_candidates=LIGHT_CONFIG["max_entry_candidates"],
    )


def _preflight_market_snapshot() -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, metadata = mod._load_research_market_snapshot_bundle()
    if metadata.get("auto_fetch_enabled") is not False:
        raise RuntimeError("market auto-fetch gate is not disabled")
    if metadata.get("auto_regenerate_enabled") is not False:
        raise RuntimeError("market auto-regenerate gate is not disabled")
    if metadata.get("fail_closed") is not True:
        raise RuntimeError("market snapshot fail-closed gate is not active")
    if not bool((metadata.get("primary_freshness") or {}).get("fresh")):
        raise RuntimeError("market snapshot freshness gate failed")
    return frame, metadata


def _exit_priority_gate() -> dict[str, Any]:
    source = inspect.getsource(mod._execution_backtest.simulate_exit)
    stop_pos = source.index("# 1) intraday ATR hard stop")
    break_pos = source.index("# 2) 종가 기준 strict interval break")
    cap_pos = source.index("# 3) provisional max holding")
    passed = stop_pos < break_pos < cap_pos and mod.ENTRY_PHASE_MAX_HOLDING_DAYS == 7
    if not passed:
        raise RuntimeError("entry-phase exit priority gate failed")
    return {
        "passed": True,
        "priority": ["entry_provisional_atr_stop", "entry_interval_break", "entry_provisional_max_holding"],
        "max_holding_days": mod.ENTRY_PHASE_MAX_HOLDING_DAYS,
    }


def _load_snapshot_context(ticker: str, market_history_df: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]]:
    ticker = ticker.upper().strip()
    path = OHLCV_SNAPSHOT_ROOT / f"{ticker}_ohlcv.csv"
    expected_sha = EXPECTED_OHLCV_SHA256.get(ticker)
    if expected_sha is None:
        raise ValueError(f"unsupported light-run ticker: {ticker}")
    if not path.is_file():
        raise FileNotFoundError(f"OHLCV snapshot missing: {path}")
    actual_sha = _sha256(path)
    if actual_sha != expected_sha:
        raise RuntimeError(f"OHLCV snapshot SHA mismatch: {ticker} expected={expected_sha} actual={actual_sha}")

    raw = pd.read_csv(path)
    required = {"Date", "Open", "High", "Low", "Close", "Volume"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise RuntimeError(f"OHLCV required columns missing for {ticker}: {missing}")
    raw["Date"] = pd.to_datetime(raw["Date"], errors="coerce")
    if raw["Date"].isna().any():
        raise RuntimeError(f"OHLCV invalid date rows: {ticker}")
    raw = raw.set_index("Date").sort_index()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
        if not np.isfinite(raw[column].to_numpy(dtype=float)).all():
            raise RuntimeError(f"OHLCV NaN/Inf: {ticker}:{column}")
    df = calc_indicators(raw)

    sell_omen_path = REPOSITORY_ROOT / "data/_system/ml_sell_omen/sell_omen_scores.csv"
    df, sell_omen_info = attach_sell_omen_scores(df, ticker, score_table_path=sell_omen_path)

    adapter = mod._pipeline_context.get_adapter(ticker)
    meta = adapter.meta
    from engine.learning.learner import _detect_sector_name

    sector_name = _detect_sector_name(meta.name)
    base_rulebook = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rulebook.sector_name = sector_name
    data_start = str(pd.Timestamp(df.index.min()).date())
    data_end = str(pd.Timestamp(df.index.max()).date())
    context = {
        "ticker": ticker,
        "adapter": adapter,
        "meta": meta,
        "df": df,
        "rows": int(len(df)),
        "data_min": data_start,
        "data_max": data_end,
        "data_start": data_start,
        "data_end": data_end,
        "market_history_df": market_history_df.copy(),
        "ticker_sentiment": None,
        "sector_name": sector_name,
        "base_rulebook": base_rulebook,
        "sell_omen_info": sell_omen_info,
    }
    metadata = {
        "path": str(path.resolve()),
        "sha256": actual_sha,
        "rows": int(len(df)),
        "first_date": data_start,
        "last_date": data_end,
        "external_fetch": False,
        "sell_omen_score_table": str(sell_omen_path.resolve()),
        "sell_omen_info": sell_omen_info,
    }
    return context, metadata


def _install_ga_trace() -> tuple[list[dict[str, Any]], Any]:
    original = mod._base.run_ga
    calls: list[dict[str, Any]] = []

    def traced_run_ga(*args: Any, **kwargs: Any) -> Any:
        call_index = len(calls) + 1
        gene_scope = str(kwargs.get("gene_scope", "all"))
        history: list[dict[str, Any]] = []
        original_callback = kwargs.get("on_generation")

        def callback(generation: int, best: Rulebook, average: float) -> None:
            row = {
                "event": "stage3_light_ga_generation",
                "call_index": call_index,
                "gene_scope": gene_scope,
                "generation": int(generation),
                "best_fitness": float(getattr(best, "fitness", 0.0) or 0.0),
                "average_fitness": float(average),
            }
            history.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if original_callback is not None:
                original_callback(generation, best, average)

        kwargs["on_generation"] = callback
        result = original(*args, **kwargs)
        calls.append(
            {
                "call_index": call_index,
                "gene_scope": gene_scope,
                "history": history,
                "best_rulebook": copy.deepcopy(result.best),
                "generations_run": int(result.generations_run),
                "final_population_count": len(result.final_population),
            }
        )
        return result

    mod._base.run_ga = traced_run_ga
    return calls, original


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _standalone_feature_pass(rb: Rulebook, feature: str, value: Any) -> bool:
    if not _finite(value):
        return False
    spec = ENTRY_INTERVAL_SPECS[feature]
    number = float(value)
    hard_min = spec.get("hard_min")
    hard_max = spec.get("hard_max")
    if hard_min is not None and number < float(hard_min):
        return False
    if hard_max is not None and number > float(hard_max):
        return False
    domains = getattr(rb, "entry_feature_domains", {}) or {}
    domain = domains.get(feature)
    if not isinstance(domain, Mapping):
        return False
    q01 = domain.get("q01")
    q99 = domain.get("q99")
    low = getattr(rb, spec["low_field"], None)
    high = getattr(rb, spec["high_field"], None)
    if not all(_finite(item) for item in (q01, q99, low, high)):
        return False
    return float(q01) <= number <= float(q99) and float(low) <= number <= float(high)


def _signal_stats(
    *,
    ticker: str,
    stage: str,
    candidate_hash: str,
    period_label: str,
    rb: Rulebook,
    ctx: Mapping[str, Any],
    result: Any,
) -> dict[str, Any]:
    tape = [row for row in list(getattr(result, "daily_signal_tape", []) or []) if isinstance(row, Mapping)]
    eligible = [row for row in tape if bool(row.get("entry_eligible"))]
    standalone = Counter()
    quality_blocked = 0
    quality_override = 0
    for point in eligible:
        row_index = int(point.get("row_index", -1))
        features = extract_entry_features(ctx["df"].iloc[: row_index + 1]) if row_index >= 0 else {}
        for feature in ENTRY_INTERVAL_SPECS:
            if _standalone_feature_pass(rb, feature, features.get(feature)):
                standalone[feature] += 1
        strict_pass = bool(point.get("strict_interval_pass"))
        should_buy = bool(point.get("should_buy"))
        quality_score = float(point.get("quality_score", 0.0) or 0.0)
        threshold = float(point.get("threshold", 0.0) or 0.0)
        if not strict_pass and quality_score >= threshold:
            quality_blocked += 1
        if should_buy and not strict_pass:
            quality_override += 1
    denominator = len(eligible)
    strict_count = sum(1 for row in eligible if bool(row.get("strict_interval_pass")))
    return {
        "ticker": ticker,
        "stage": stage,
        "candidate_hash": candidate_hash,
        "period_label": period_label,
        "eligible_day_count": denominator,
        "strict_and_pass_count": strict_count,
        "strict_and_pass_rate": float(strict_count / denominator) if denominator else 0.0,
        "feature_standalone_pass_count": {feature: int(standalone[feature]) for feature in ENTRY_INTERVAL_SPECS},
        "feature_standalone_pass_rate": {
            feature: float(standalone[feature] / denominator) if denominator else 0.0
            for feature in ENTRY_INTERVAL_SPECS
        },
        "quality_high_but_strict_blocked_count": int(quality_blocked),
        "quality_override_count": int(quality_override),
        "trade_count": int(getattr(result, "trade_count", 0) or 0),
        "fitness": float(getattr(result, "fitness", 0.0) or 0.0),
    }


def _trade_rows(
    *,
    ticker: str,
    stage: str,
    candidate_hash: str,
    period_label: str,
    result: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in list(getattr(result, "trades", []) or []):
        if not isinstance(trade, Mapping):
            continue
        signal_tape = trade.get("entry_signal_tape") if isinstance(trade.get("entry_signal_tape"), Mapping) else {}
        rows.append(
            {
                "ticker": ticker,
                "stage": stage,
                "candidate_hash": candidate_hash,
                "period_label": period_label,
                "entry_signal_date": trade.get("entry_signal_date"),
                "entry_fill_date": trade.get("entry_fill_date", trade.get("entry_date")),
                "entry_date": trade.get("entry_date"),
                "entry_price": trade.get("entry_price"),
                "exit_date": trade.get("exit_date"),
                "exit_price": trade.get("exit_price"),
                "exit_reason": trade.get("exit_reason"),
                "holding_days": trade.get("holding_days"),
                "pnl_pct": trade.get("pnl_pct"),
                "entry_features": dict(signal_tape.get("entry_features") or {}),
                "interval_checks": dict(signal_tape.get("interval_checks") or {}),
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


def _audit_rulebook(
    *,
    ticker: str,
    stage: str,
    candidate_hash: str,
    rb: Rulebook,
    ctx: dict[str, Any],
    periods: Iterable[Mapping[str, Any]],
    entry_phase: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stats: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for period in periods:
        label = str(period["label"])
        end = period.get("end")
        if label == "recent_1y":
            end = ctx.get("data_end")
        if entry_phase:
            result = mod.run_entry_backtest_period(rb, ctx, start=period.get("start"), end=end)
        else:
            result = mod._base.run_backtest_period(rb, ctx, start=period.get("start"), end=end)
        stats.append(
            _signal_stats(
                ticker=ticker,
                stage=stage,
                candidate_hash=candidate_hash,
                period_label=label,
                rb=rb,
                ctx=ctx,
                result=result,
            )
        )
        trades.extend(
            _trade_rows(
                ticker=ticker,
                stage=stage,
                candidate_hash=candidate_hash,
                period_label=label,
                result=result,
            )
        )
    return stats, trades


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _schema_audit(rulebook_rows: Iterable[Mapping[str, Any]], quality_override_count: int) -> dict[str, Any]:
    one_sided = 0
    missing_domain = 0
    validator_errors: Counter[str] = Counter()
    candidate_count = 0
    for row in rulebook_rows:
        raw = row.get("rulebook") if isinstance(row.get("rulebook"), Mapping) else row
        rb = Rulebook.from_dict(dict(raw))
        candidate_count += 1
        domains = getattr(rb, "entry_feature_domains", {}) or {}
        for feature, spec in ENTRY_INTERVAL_SPECS.items():
            low = getattr(rb, spec["low_field"], None)
            high = getattr(rb, spec["high_field"], None)
            if not _finite(low) or not _finite(high):
                one_sided += 1
            if not isinstance(domains.get(feature), Mapping):
                missing_domain += 1
        for error in validate_entry_intervals(rb) + validate_entry_feature_domains(rb):
            validator_errors[str(error)] += 1
    return {
        "candidate_count": candidate_count,
        "one_sided_count": int(one_sided),
        "missing_domain_count": int(missing_domain),
        "validator_error_count": int(sum(validator_errors.values())),
        "validator_errors": dict(validator_errors),
        "quality_score_override_count": int(quality_override_count),
        "ce_boil_zero": bool(
            one_sided == 0
            and missing_domain == 0
            and not validator_errors
            and quality_override_count == 0
        ),
    }


def _audit_periods(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    periods = [dict(item) for item in mod._base.TRAIN_SPLITS]
    recent = next(
        dict(item)
        for item in mod._base.PURE_OOS_VALIDATION_PERIODS
        if str(item.get("label")) == "recent_1y"
    )
    recent["end"] = ctx.get("data_end")
    periods.append(recent)
    return periods


def _update_manifest(out_dir: Path, updates: Mapping[str, Any]) -> None:
    path = out_dir / "manifest.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current.update(dict(updates))
    _write_json(path, current)


def run_ticker(ticker: str, out_dir: Path, seed_base: int) -> dict[str, Any]:
    started = time.time()
    ticker = ticker.upper().strip()
    market_frame, market_metadata = _preflight_market_snapshot()
    exit_priority = _exit_priority_gate()
    ctx, ohlcv_metadata = _load_snapshot_context(ticker, market_frame)

    out_dir.mkdir(parents=True, exist_ok=True)
    mod.ensure_research_experiment_header(out_dir, ticker=ticker, seed_base=seed_base, stage="all")
    _update_manifest(
        out_dir,
        {
            "runner": "scripts/research/run_stage3_baseline_light.py",
            "execution_scale": "LIGHT_ONLY_NOT_FULL_STAGE3",
            "light_config": LIGHT_CONFIG,
            "market_snapshot_preflight": market_metadata,
            "ohlcv_snapshot": ohlcv_metadata,
            "entry_phase_exit_priority_gate": exit_priority,
            "qualify_individual_policy": "audit_summary_then_discard",
            "external_fetch_enabled": False,
        },
    )

    ga_calls, original_run_ga = _install_ga_trace()
    all_signal_stats: list[dict[str, Any]] = []
    all_trade_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    stop_reason: str | None = None

    try:
        code_commit = mod._base.resolve_code_commit(mod._base.PROJECT_ROOT)
        qualify = mod.run_qualify(
            ticker,
            out_dir,
            seed_base=seed_base,
            use_fitness_cache=False,
            code_commit=code_commit,
            context=ctx,
        )
        summaries.append(qualify)

        qualify_calls = list(ga_calls[: len(mod._base.TRAIN_SPLITS)])
        for split, call in zip(mod._base.TRAIN_SPLITS, qualify_calls):
            rb = call["best_rulebook"]
            candidate_hash = mod._base.compute_rulebook_hash(rb)
            stats, trades = _audit_rulebook(
                ticker=ticker,
                stage="qualify_best_discarded",
                candidate_hash=candidate_hash,
                rb=rb,
                ctx=ctx,
                periods=[split],
                entry_phase=True,
            )
            all_signal_stats.extend(stats)
            all_trade_rows.extend(trades)
        for call in qualify_calls:
            call.pop("best_rulebook", None)

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
            summaries.append(entry)
            entry_rows = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
            for row in entry_rows:
                rb = Rulebook.from_dict(dict(row["rulebook"]))
                stats, trades = _audit_rulebook(
                    ticker=ticker,
                    stage="entry_survivor",
                    candidate_hash=str(row.get("rulebook_hash")),
                    rb=rb,
                    ctx=ctx,
                    periods=_audit_periods(ctx),
                    entry_phase=True,
                )
                all_signal_stats.extend(stats)
                all_trade_rows.extend(trades)

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
                summaries.append(exit_summary)
                final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
                if not final_rows:
                    stop_reason = "no_exit_candidate"
                else:
                    validate = mod._base.run_validate(ticker, out_dir, seed_base=seed_base, context=ctx)
                    summaries.append(validate)
                    for row in final_rows:
                        rb = Rulebook.from_dict(dict(row["rulebook"]))
                        stats, trades = _audit_rulebook(
                            ticker=ticker,
                            stage="final_rulebook",
                            candidate_hash=str(row.get("rulebook_hash")),
                            rb=rb,
                            ctx=ctx,
                            periods=_audit_periods(ctx),
                            entry_phase=False,
                        )
                        all_signal_stats.extend(stats)
                        all_trade_rows.extend(trades)

        _append_jsonl(out_dir / "signal_statistics.jsonl", all_signal_stats)
        _append_jsonl(out_dir / "trade_level_details.jsonl", all_trade_rows)

        generation_rows: list[dict[str, Any]] = []
        for call in ga_calls:
            generation_rows.extend(call.get("history") or [])
        _append_jsonl(out_dir / "generation_best_fitness.jsonl", generation_rows)

        entry_rows = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
        final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
        catalog_rows = _read_jsonl(out_dir / "stage3_profile_catalog.jsonl")
        quality_override_count = sum(int(row.get("quality_override_count", 0) or 0) for row in all_signal_stats)
        schema_rows = [*entry_rows, *final_rows]
        schema_audit = _schema_audit(schema_rows, quality_override_count)

        qualify_result = summaries[0] if summaries else {}
        final = {
            "ticker": ticker,
            "execution_scale": "LIGHT_ONLY_NOT_FULL_STAGE3",
            "light_config": LIGHT_CONFIG,
            "qualified": bool(qualify_result.get("qualified")),
            "qualify_all3_pass_count": int(qualify_result.get("all3_pass_count", 0) or 0),
            "entry_survivor_count": len(entry_rows),
            "exit_candidate_count": len(final_rows),
            "validate_survivor_count": len(catalog_rows),
            "ce_boil_audit": schema_audit,
            "stop_reason": stop_reason,
            "signal_statistics_rows": len(all_signal_stats),
            "trade_level_rows": len(all_trade_rows),
            "generation_rows": len(generation_rows),
            "summaries": summaries,
            "elapsed_seconds": time.time() - started,
        }
        _write_json(out_dir / "light_final_summary.json", final)
        _write_json(out_dir / "last_run_summary.json", {"ticker": ticker, "stage": "all", "summaries": summaries})
        _update_manifest(
            out_dir,
            {
                "light_run_completed": True,
                "light_run_stop_reason": stop_reason,
                "light_final_counts": {
                    "qualified": final["qualified"],
                    "entry_survivor_count": final["entry_survivor_count"],
                    "exit_candidate_count": final["exit_candidate_count"],
                    "validate_survivor_count": final["validate_survivor_count"],
                },
                "ce_boil_audit": schema_audit,
            },
        )
        print(json.dumps({"event": "stage3_light_done", **final}, ensure_ascii=False, default=str), flush=True)
        return final
    finally:
        mod._base.run_ga = original_run_ga


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-ticker light Stage3 baseline")
    parser.add_argument("--ticker", required=True, choices=["AAP", "POWI"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _apply_light_config()
    try:
        run_ticker(args.ticker, Path(args.out_dir).resolve(), int(args.seed_base))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "stage3_light_failed",
                    "ticker": args.ticker,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
