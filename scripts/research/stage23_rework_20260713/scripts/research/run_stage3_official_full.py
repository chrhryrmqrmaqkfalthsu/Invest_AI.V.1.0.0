#!/usr/bin/env python3
"""AAP·POWI 정식 규모 Stage 3 전용 runner.

기존 경량 runner의 snapshot/fail-closed/상세감사 로직을 재사용하되,
정식 파라미터와 OFFICIAL_FULL_STAGE3 표기를 강제한다.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
LIGHT_RUNNER = HERE.with_name("run_stage3_baseline_light.py")


def _load_light_runner() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_stage3_official_full_shared", LIGHT_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load shared Stage3 runner: {LIGHT_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _load_light_runner()

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
}


def _apply_official_config() -> None:
    base = shared.mod._base
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
        entry_min_expectancy_pct=2.0,
        entry_overlap_threshold=0.7,
        max_entry_candidates=OFFICIAL_CONFIG["max_entry_candidates"],
    )
    shared.LIGHT_CONFIG.clear()
    shared.LIGHT_CONFIG.update(OFFICIAL_CONFIG)


def _officialize(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            new_key = {
                "light_config": "official_config",
                "light_run_completed": "official_run_completed",
                "light_run_stop_reason": "official_run_stop_reason",
                "light_final_counts": "official_final_counts",
            }.get(str(key), str(key))
            result[new_key] = _officialize(item)
        return result
    if isinstance(value, list):
        return [_officialize(item) for item in value]
    if value == "LIGHT_ONLY_NOT_FULL_STAGE3":
        return "OFFICIAL_FULL_STAGE3"
    if value == "scripts/research/run_stage3_baseline_light.py":
        return "scripts/research/run_stage3_official_full.py"
    return value


def _install_output_adapter() -> None:
    original_write_json = shared._write_json
    original_update_manifest = shared._update_manifest

    def write_json(path: Path, value: Any) -> None:
        target = path.with_name("official_final_summary.json") if path.name == "light_final_summary.json" else path
        original_write_json(target, _officialize(value))

    def update_manifest(out_dir: Path, updates: dict[str, Any]) -> None:
        original_update_manifest(out_dir, _officialize(updates))

    shared._write_json = write_json
    shared._update_manifest = update_manifest


def _install_official_ga_trace() -> None:
    def install() -> tuple[list[dict[str, Any]], Any]:
        original = shared.mod._base.run_ga
        calls: list[dict[str, Any]] = []

        def traced_run_ga(*args: Any, **kwargs: Any) -> Any:
            call_index = len(calls) + 1
            gene_scope = str(kwargs.get("gene_scope", "all"))
            history: list[dict[str, Any]] = []
            original_callback = kwargs.get("on_generation")

            def callback(generation: int, best: Any, average: float) -> None:
                row = {
                    "event": "stage3_official_ga_generation",
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

        shared.mod._base.run_ga = traced_run_ga
        return calls, original

    shared._install_ga_trace = install


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one-ticker official full Stage3")
    parser.add_argument("--ticker", required=True, choices=["AAP", "POWI"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _apply_official_config()
    _install_output_adapter()
    _install_official_ga_trace()
    try:
        result = shared.run_ticker(args.ticker, Path(args.out_dir).resolve(), int(args.seed_base))
        print(
            json.dumps(
                {"event": "stage3_official_done", **_officialize(result)},
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
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
