#!/usr/bin/env python3
"""AAP·POWI 정식 규모 Stage 3 실행 전용 runner.

정식 파라미터를 강제하고, stage checkpoint·세대 로그·신호 병목·거래 상세·
CE/BOIL·interval-break 진단을 남긴다. Root SHA-pinned 시장 snapshot과 기존
AAP/POWI OHLCV snapshot만 사용하며 외부 fetch와 자동 재생성은 금지한다.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import importlib.util
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve()
LIGHT_RUNNER = HERE.with_name("run_stage3_baseline_light.py")

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
    "validate_min_expectancy_pct": 1.0,
}


def _load_light_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_stage3_official_support", LIGHT_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Stage3 support runner: {LIGHT_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


support = _load_light_runner()
mod = support.mod
Rulebook = support.Rulebook


def _apply_official_config() -> None:
    base = mod._base
    base.QUALIFY_POPULATION = OFFICIAL_CONFIG["qualify_population"]
    base.QUALIFY_GENERATIONS = OFFICIAL_CONFIG["qualify_generations"]
    base.ENTRY_POPULATION = OFFICIAL_CONFIG["entry_population"]
    base.ENTRY_GENERATIONS = OFFICIAL_CONFIG["entry_generations"]
    base.EXIT_POPULATION = OFFICIAL_CONFIG["exit_population"]
    base.EXIT_GENERATIONS = OFFICIAL_CONFIG["exit_generations"]
    base.TOP_N_QUALIFY = OFFICIAL_CONFIG["top_n_qualify"]
    base.TOP_N_ENTRY_POOL = OFFICIAL_CONFIG["top_n_entry_pool"]
    base.TOP_N_EXIT_PER_ENTRY = OFFICIAL_CONFIG["top_n_exit_per_entry"]
    base.DEFAULT_STAGE3_ENTRY_SELECTION = dataclasses.replace(
        base.DEFAULT_STAGE3_ENTRY_SELECTION,
        entry_min_expectancy_pct=OFFICIAL_CONFIG["entry_min_expectancy_pct"],
        entry_overlap_threshold=OFFICIAL_CONFIG["entry_overlap_threshold"],
        max_entry_candidates=OFFICIAL_CONFIG["max_entry_candidates"],
    )


def _write_json(path: Path, value: Any) -> None:
    mod._base.write_json(path, value)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def _append_jsonl_row(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")
        handle.flush()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return support._read_jsonl(path)


def _remove_paths(out_dir: Path, names: Iterable[str]) -> None:
    for name in names:
        path = out_dir / name
        if path.exists() and path.is_file():
            path.unlink()


def _update_progress(out_dir: Path, *, stage: str, status: str, detail: Mapping[str, Any] | None = None) -> None:
    current_path = out_dir / "progress_state.json"
    current = _read_json(current_path) if current_path.exists() else {}
    current.update(
        {
            "ticker": current.get("ticker"),
            "execution_scale": "OFFICIAL_FULL_STAGE3",
            "current_stage": stage,
            "status": status,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "detail": dict(detail or {}),
        }
    )
    _write_json(current_path, current)


def _next_call_index(path: Path) -> int:
    rows = _read_jsonl(path)
    return max((int(row.get("call_index", 0) or 0) for row in rows), default=0) + 1


def _install_generation_trace(
    *,
    out_dir: Path,
    stage_ref: dict[str, str],
) -> tuple[list[dict[str, Any]], Any]:
    original = mod._base.run_ga
    calls: list[dict[str, Any]] = []
    generation_path = out_dir / "generation_best_fitness.jsonl"
    call_counter = _next_call_index(generation_path)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def traced_run_ga(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_counter
        call_index = call_counter
        call_counter += 1
        gene_scope = str(kwargs.get("gene_scope", "all"))
        stage = str(stage_ref.get("stage", "unknown"))
        history: list[dict[str, Any]] = []
        original_callback = kwargs.get("on_generation")

        def callback(generation: int, best: Rulebook, average: float) -> None:
            row = {
                "event": "stage3_official_ga_generation",
                "run_id": run_id,
                "stage": stage,
                "call_index": call_index,
                "gene_scope": gene_scope,
                "generation": int(generation),
                "best_fitness": float(getattr(best, "fitness", 0.0) or 0.0),
                "average_fitness": float(average),
                "logged_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            history.append(row)
            _append_jsonl_row(generation_path, row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if original_callback is not None:
                original_callback(generation, best, average)

        kwargs["on_generation"] = callback
        result = original(*args, **kwargs)
        calls.append(
            {
                "stage": stage,
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


def _persist_qualify_best(
    out_dir: Path,
    calls: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    qualify_calls = [call for call in calls if str(call.get("stage")) == "qualify"]
    rows: list[dict[str, Any]] = []
    for split, call in zip(mod._base.TRAIN_SPLITS, qualify_calls):
        rb = call.get("best_rulebook")
        if rb is None:
            continue
        rows.append(
            {
                "ticker": getattr(rb, "ticker", None),
                "split": dict(split),
                "call_index": int(call.get("call_index", 0) or 0),
                "rulebook_hash": mod._base.compute_rulebook_hash(rb),
                "fitness": float(getattr(rb, "fitness", 0.0) or 0.0),
                "rulebook": rb.to_dict(),
                "policy": "diagnostic_only_then_discard_from_survivor_pool",
            }
        )
    _write_jsonl(out_dir / "qualify_best_rulebooks.jsonl", rows)
    return rows


def _manifest_preflight(
    *,
    out_dir: Path,
    ticker: str,
    seed_base: int,
    market_metadata: Mapping[str, Any],
    ohlcv_metadata: Mapping[str, Any],
    exit_priority: Mapping[str, Any],
) -> None:
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        mod.ensure_research_experiment_header(out_dir, ticker=ticker, seed_base=seed_base, stage="all")
    manifest = _read_json(manifest_path)
    manifest.update(
        {
            "runner": "scripts/research/run_stage3_official_2sym.py",
            "execution_scale": "OFFICIAL_FULL_STAGE3",
            "official_config": OFFICIAL_CONFIG,
            "market_snapshot_preflight": dict(market_metadata),
            "ohlcv_snapshot": dict(ohlcv_metadata),
            "entry_phase_exit_priority_gate": dict(exit_priority),
            "stage2_executed": False,
            "resume_policy": "completed stage checkpoint skip; interrupted stage reruns from stage start",
            "qualify_individual_policy": "best-per-fold diagnostics persisted; qualify population discarded",
            "external_fetch_enabled": False,
        }
    )
    _write_json(manifest_path, manifest)


def _diagnostic_periods(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    return support._audit_periods(ctx)


def _audit_all(
    *,
    ticker: str,
    out_dir: Path,
    ctx: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    signal_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    qualify_rows = _read_jsonl(out_dir / "qualify_best_rulebooks.jsonl")
    for row in qualify_rows:
        rb = Rulebook.from_dict(dict(row["rulebook"]))
        split = dict(row["split"])
        stats, trades = support._audit_rulebook(
            ticker=ticker,
            stage="qualify_best_discarded",
            candidate_hash=str(row.get("rulebook_hash")),
            rb=rb,
            ctx=ctx,
            periods=[split],
            entry_phase=True,
        )
        signal_rows.extend(stats)
        trade_rows.extend(trades)

    entry_rows = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
    for row in entry_rows:
        rb = Rulebook.from_dict(dict(row["rulebook"]))
        stats, trades = support._audit_rulebook(
            ticker=ticker,
            stage="entry_survivor",
            candidate_hash=str(row.get("rulebook_hash")),
            rb=rb,
            ctx=ctx,
            periods=_diagnostic_periods(ctx),
            entry_phase=True,
        )
        signal_rows.extend(stats)
        trade_rows.extend(trades)

    final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
    catalog_rows = _read_jsonl(out_dir / "stage3_profile_catalog.jsonl")
    validated_hashes = {
        str(row.get("rulebook_hash"))
        for row in catalog_rows
        if row.get("rulebook_hash") is not None
    }
    if validated_hashes:
        final_audit_rows = [row for row in final_rows if str(row.get("rulebook_hash")) in validated_hashes]
    else:
        final_audit_rows = final_rows[:3]

    for row in final_audit_rows:
        rb = Rulebook.from_dict(dict(row["rulebook"]))
        stats, trades = support._audit_rulebook(
            ticker=ticker,
            stage="validated_survivor" if validated_hashes else "final_top3_no_validate_survivor",
            candidate_hash=str(row.get("rulebook_hash")),
            rb=rb,
            ctx=ctx,
            periods=_diagnostic_periods(ctx),
            entry_phase=False,
        )
        signal_rows.extend(stats)
        trade_rows.extend(trades)

    _write_jsonl(out_dir / "signal_statistics.jsonl", signal_rows)
    _write_jsonl(out_dir / "trade_level_details.jsonl", trade_rows)

    quality_override_count = sum(int(row.get("quality_override_count", 0) or 0) for row in signal_rows)
    schema_audit = support._schema_audit([*entry_rows, *final_rows], quality_override_count)
    reason_counts = Counter(str(row.get("exit_reason")) for row in trade_rows)
    total_exits = sum(reason_counts.values())
    interval_break_count = int(reason_counts.get("entry_interval_break", 0))
    interval_break_summary = {
        "trade_count": len(trade_rows),
        "exit_reason_counts": dict(sorted(reason_counts.items())),
        "entry_interval_break_count": interval_break_count,
        "entry_interval_break_share": float(interval_break_count / total_exits) if total_exits else 0.0,
    }
    return signal_rows, trade_rows, {
        "ce_boil_audit": schema_audit,
        "interval_break": interval_break_summary,
        "qualify_best_count": len(qualify_rows),
        "entry_audit_candidate_count": len(entry_rows),
        "final_audit_candidate_count": len(final_audit_rows),
    }


def _cleanup_stage_outputs(out_dir: Path, stage: str) -> None:
    stage_files = {
        "qualify": ["qualify_result.json", "qualify_best_rulebooks.jsonl"],
        "entry": ["entry_result.json", "entry_rulebooks.jsonl", "entry_rejected_overlap.json"],
        "exit": ["exit_result.json", "final_rulebooks.jsonl"],
        "validate": ["validate_result.json", "stage3_profile_catalog.jsonl"],
    }
    _remove_paths(out_dir, stage_files[stage])


def run_official_ticker(ticker: str, out_dir: Path, seed_base: int) -> dict[str, Any]:
    started = time.time()
    ticker = ticker.upper().strip()
    out_dir.mkdir(parents=True, exist_ok=True)

    market_frame, market_metadata = support._preflight_market_snapshot()
    exit_priority = support._exit_priority_gate()
    ctx, ohlcv_metadata = support._load_snapshot_context(ticker, market_frame)
    _manifest_preflight(
        out_dir=out_dir,
        ticker=ticker,
        seed_base=seed_base,
        market_metadata=market_metadata,
        ohlcv_metadata=ohlcv_metadata,
        exit_priority=exit_priority,
    )
    _write_json(
        out_dir / "progress_state.json",
        {
            "ticker": ticker,
            "execution_scale": "OFFICIAL_FULL_STAGE3",
            "current_stage": "preflight",
            "status": "passed",
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            "detail": {
                "market_fresh": True,
                "auto_fetch": False,
                "stage2_executed": False,
            },
        },
    )

    stage_ref = {"stage": "preflight"}
    ga_calls, original_run_ga = _install_generation_trace(out_dir=out_dir, stage_ref=stage_ref)
    summaries: list[dict[str, Any]] = []
    stop_reason: str | None = None

    try:
        code_commit = mod._base.resolve_code_commit(mod._base.PROJECT_ROOT)

        qualify_path = out_dir / "qualify_result.json"
        qualify_best_path = out_dir / "qualify_best_rulebooks.jsonl"
        if qualify_path.exists() and qualify_best_path.exists():
            qualify = _read_json(qualify_path)
            print(json.dumps({"event": "stage3_official_resume_skip", "stage": "qualify"}, ensure_ascii=False), flush=True)
        else:
            _cleanup_stage_outputs(out_dir, "qualify")
            stage_ref["stage"] = "qualify"
            _update_progress(out_dir, stage="qualify", status="running")
            qualify = mod.run_qualify(
                ticker,
                out_dir,
                seed_base=seed_base,
                use_fitness_cache=False,
                code_commit=code_commit,
                context=ctx,
            )
            _persist_qualify_best(out_dir, ga_calls)
            _update_progress(
                out_dir,
                stage="qualify",
                status="completed",
                detail={
                    "qualified": bool(qualify.get("qualified")),
                    "all3_pass_count": int(qualify.get("all3_pass_count", 0) or 0),
                },
            )
        summaries.append(qualify)

        if not bool(qualify.get("qualified")):
            stop_reason = "qualify_failed"
        else:
            entry_path = out_dir / "entry_result.json"
            if entry_path.exists():
                entry = _read_json(entry_path)
                print(json.dumps({"event": "stage3_official_resume_skip", "stage": "entry"}, ensure_ascii=False), flush=True)
            else:
                _cleanup_stage_outputs(out_dir, "entry")
                stage_ref["stage"] = "entry"
                _update_progress(out_dir, stage="entry", status="running")
                entry = mod.run_entry_ga(
                    ticker,
                    out_dir,
                    seed_base=seed_base,
                    use_fitness_cache=False,
                    code_commit=code_commit,
                    context=ctx,
                )
                _update_progress(
                    out_dir,
                    stage="entry",
                    status="completed",
                    detail={"selected_count": int(entry.get("selected_count", 0) or 0)},
                )
            summaries.append(entry)

            entry_rows = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
            if not entry_rows:
                stop_reason = "no_entry_survivor"
            else:
                exit_path = out_dir / "exit_result.json"
                if exit_path.exists():
                    exit_summary = _read_json(exit_path)
                    print(json.dumps({"event": "stage3_official_resume_skip", "stage": "exit"}, ensure_ascii=False), flush=True)
                else:
                    _cleanup_stage_outputs(out_dir, "exit")
                    stage_ref["stage"] = "exit"
                    _update_progress(out_dir, stage="exit", status="running")
                    exit_summary = mod._base.run_exit_ga(
                        ticker,
                        out_dir,
                        seed_base=seed_base,
                        weights=mod._base.DEFAULT_EXIT_FITNESS_WEIGHTS,
                        context=ctx,
                    )
                    _update_progress(
                        out_dir,
                        stage="exit",
                        status="completed",
                        detail={"final_candidate_count": int(exit_summary.get("final_count", 0) or 0)},
                    )
                summaries.append(exit_summary)

                final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
                if not final_rows:
                    stop_reason = "no_exit_candidate"
                else:
                    validate_path = out_dir / "validate_result.json"
                    if validate_path.exists():
                        validate = _read_json(validate_path)
                        print(json.dumps({"event": "stage3_official_resume_skip", "stage": "validate"}, ensure_ascii=False), flush=True)
                    else:
                        _cleanup_stage_outputs(out_dir, "validate")
                        stage_ref["stage"] = "validate"
                        _update_progress(out_dir, stage="validate", status="running")
                        validate = mod._base.run_validate(ticker, out_dir, seed_base=seed_base, context=ctx)
                        _update_progress(
                            out_dir,
                            stage="validate",
                            status="completed",
                            detail={"survivor_count": int(validate.get("selected_count", validate.get("catalog_count", 0)) or 0)},
                        )
                    summaries.append(validate)

        stage_ref["stage"] = "diagnostics"
        _update_progress(out_dir, stage="diagnostics", status="running")
        signal_rows, trade_rows, diagnostics = _audit_all(ticker=ticker, out_dir=out_dir, ctx=ctx)

        entry_rows = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
        final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
        catalog_rows = _read_jsonl(out_dir / "stage3_profile_catalog.jsonl")
        validate_result = _read_json(out_dir / "validate_result.json") if (out_dir / "validate_result.json").exists() else {}
        qualify_result = summaries[0] if summaries else {}
        final = {
            "ticker": ticker,
            "execution_scale": "OFFICIAL_FULL_STAGE3",
            "official_config": OFFICIAL_CONFIG,
            "qualified": bool(qualify_result.get("qualified")),
            "qualify_all3_pass_count": int(qualify_result.get("all3_pass_count", 0) or 0),
            "entry_survivor_count": len(entry_rows),
            "exit_candidate_count": len(final_rows),
            "validate_survivor_count": len(catalog_rows),
            "validate_result": validate_result,
            "ce_boil_audit": diagnostics["ce_boil_audit"],
            "interval_break": diagnostics["interval_break"],
            "stop_reason": stop_reason,
            "signal_statistics_rows": len(signal_rows),
            "trade_level_rows": len(trade_rows),
            "generation_rows": len(_read_jsonl(out_dir / "generation_best_fitness.jsonl")),
            "diagnostic_candidate_counts": {
                "qualify_best": diagnostics["qualify_best_count"],
                "entry": diagnostics["entry_audit_candidate_count"],
                "final": diagnostics["final_audit_candidate_count"],
            },
            "summaries": summaries,
            "elapsed_seconds": time.time() - started,
        }
        _write_json(out_dir / "official_final_summary.json", final)
        _write_json(out_dir / "last_run_summary.json", {"ticker": ticker, "stage": "all", "summaries": summaries})

        manifest = _read_json(out_dir / "manifest.json")
        manifest.update(
            {
                "official_run_completed": True,
                "official_run_stop_reason": stop_reason,
                "official_final_counts": {
                    "qualified": final["qualified"],
                    "entry_survivor_count": final["entry_survivor_count"],
                    "exit_candidate_count": final["exit_candidate_count"],
                    "validate_survivor_count": final["validate_survivor_count"],
                },
                "ce_boil_audit": diagnostics["ce_boil_audit"],
                "interval_break": diagnostics["interval_break"],
            }
        )
        _write_json(out_dir / "manifest.json", manifest)
        _update_progress(out_dir, stage="all", status="completed", detail=manifest["official_final_counts"])
        print(json.dumps({"event": "stage3_official_done", **final}, ensure_ascii=False, default=str), flush=True)
        return final
    except Exception as exc:
        _update_progress(
            out_dir,
            stage=str(stage_ref.get("stage", "unknown")),
            status="failed",
            detail={"error_type": type(exc).__name__, "error": str(exc)},
        )
        raise
    finally:
        mod._base.run_ga = original_run_ga


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-ticker official full Stage3")
    parser.add_argument("--ticker", required=True, choices=["AAP", "POWI"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", required=True, type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _apply_official_config()
    try:
        run_official_ticker(args.ticker, Path(args.out_dir).resolve(), int(args.seed_base))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "stage3_official_failed",
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
