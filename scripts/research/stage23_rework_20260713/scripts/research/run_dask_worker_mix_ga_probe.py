#!/usr/bin/env python3
"""Compare one deterministic AAP GA across two Dask worker configurations.

A: Linux/VM Dask worker only.
B: Linux/VM + Windows/notebook workers, assigned round-robin.

Candidate generation, selection, crossover, mutation and every RNG draw happen
in the client process.  Only entry-fitness backtests are submitted to workers,
and results are merged by candidate input index rather than completion order.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import json
import math
import os
import random
import struct
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
from dask.distributed import Client, get_worker

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
REPOSITORY_ROOT = WORKSPACE_ROOT.parents[2]
STAGE3_RUNNER = HERE.with_name("run_stage3_aggressive.py")
MARKET_HISTORY_PATH = REPOSITORY_ROOT / "data/_system/market_history.csv"
MARKET_HISTORY_EXPECTED_SHA256 = "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38"
TICKER = "AAP"
FOLD_LABEL = "train_3"
POPULATION = 8
GENERATIONS = 3
SEED = 2026071401
POSITION_LIMIT_KRW = 120_000.0
ENTRY_PHASE_MAX_HOLDING_DAYS = 7
ENTRY_EXECUTION_MODE = "t_plus_1_open"
EXIT_EXECUTION_MODE = "conservative_core"
FOLD_EXIT_POLICY = "fold_end_mark_to_market"
LIVE_HARD_STOP_GUARD = True


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_value(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _float_hex(value: float) -> str:
    return struct.pack(">d", float(value)).hex()


def _ordered_float_int(value: float) -> int:
    bits = struct.unpack(">Q", struct.pack(">d", float(value)))[0]
    return (~bits & ((1 << 64) - 1)) if bits & (1 << 63) else bits | (1 << 63)


def _ulp_distance(left: float, right: float) -> int | None:
    if not (math.isfinite(left) and math.isfinite(right)):
        return None
    return abs(_ordered_float_int(left) - _ordered_float_int(right))


def _load_stage3_module() -> Any:
    spec = importlib.util.spec_from_file_location("_dask_mix_probe_stage3", STAGE3_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Stage3 runner: {STAGE3_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _entry_phase_execution_context() -> Iterable[None]:
    from engine.learning import execution_mode_backtest as execution_bt

    original_builder = execution_bt._build_daily_signal_tape
    original_simulate_exit = execution_bt.simulate_exit
    state: dict[str, Any] = {}

    def build_tape(*args: Any, **kwargs: Any) -> Any:
        tape = original_builder(*args, **kwargs)
        state["signal_tape"] = tape
        return tape

    def simulate_entry_exit(*args: Any, **kwargs: Any) -> Any:
        tape = state.get("signal_tape")
        if tape is None:
            raise RuntimeError("entry-phase signal tape missing before simulate_exit")
        kwargs["entry_phase_exit"] = True
        kwargs["entry_phase_signal_tape"] = tape
        kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
        return original_simulate_exit(*args, **kwargs)

    execution_bt._build_daily_signal_tape = build_tape
    execution_bt.simulate_exit = simulate_entry_exit
    try:
        yield
    finally:
        execution_bt._build_daily_signal_tape = original_builder
        execution_bt.simulate_exit = original_simulate_exit


def _run_entry_backtest(rulebook: Any, payload: Mapping[str, Any]) -> Any:
    from engine.learning import execution_mode_backtest as execution_bt

    split = payload["split"]
    with _entry_phase_execution_context():
        return execution_bt.run_backtest_execution_mode(
            rulebook,
            payload["df"],
            start_date=str(split["start"]),
            end_date=str(split["end"]),
            position_limit_krw=POSITION_LIMIT_KRW,
            market_history_df=payload["market_history_df"],
            sector_name=str(payload.get("sector_name") or "tech"),
            ticker_sentiment=payload.get("ticker_sentiment"),
            fitness_mode="swing",
            use_llm_events=False,
            entry_execution_mode=ENTRY_EXECUTION_MODE,
            exit_execution_mode=EXIT_EXECUTION_MODE,
            fold_exit_policy=FOLD_EXIT_POLICY,
            live_hard_stop_guard=LIVE_HARD_STOP_GUARD,
        )


def _worker_evaluate(
    index: int,
    rulebook: Any,
    stage: str,
    payload: Mapping[str, Any],
    entry_ctx: Any,
) -> dict[str, Any]:
    from engine.core.metadata import compute_rulebook_hash
    from engine.learning import genetic as base
    from engine.learning import execution_mode_backtest as execution_bt

    candidate = copy.deepcopy(rulebook)

    def evaluate_fn(item: Any) -> float:
        result = _run_entry_backtest(item, payload)
        return float(getattr(result, "fitness", -1_000_000_000.0))

    fitness = base._evaluate_candidate(
        candidate,
        evaluate_fn,
        gene_scope="entry",
        entry_ctx=entry_ctx,
        stage=stage,
    )
    candidate.fitness = float(fitness)
    diagnostics = dict(getattr(candidate, execution_bt.ENTRY_FITNESS_DIAGNOSTICS_ATTR, {}) or {})
    worker = get_worker()
    return {
        "index": int(index),
        "candidate": candidate,
        "chromosome_hash": compute_rulebook_hash(candidate),
        "fitness": float(fitness),
        "fitness_hex": _float_hex(float(fitness)),
        "trade_count": diagnostics.get("trade_count"),
        "win_rate_pct": diagnostics.get("win_rate_pct"),
        "entry_gate_pass": diagnostics.get("entry_gate_pass"),
        "worker_address": worker.address,
        "worker_os_name": os.name,
        "worker_python": sys.version,
        "worker_numpy": np.__version__,
        "worker_engine_file": str(__import__("engine").__file__),
        "market_snapshot_sha256": payload["market_snapshot_sha256"],
        "worker_local_market_file_read": False,
    }


def _evaluate_batch(
    client: Client,
    population: list[Any],
    *,
    stages: list[str],
    worker_addresses: list[str],
    payload_futures: Mapping[str, Any],
    entry_ctx: Any,
) -> tuple[list[Any], list[dict[str, Any]]]:
    if len(population) != len(stages):
        raise ValueError("population/stages length mismatch")
    if not worker_addresses:
        raise ValueError("worker_addresses must not be empty")

    results: list[dict[str, Any]] = []
    # One active evaluation per worker.  The entry provisional-exit path uses a
    # short-lived module patch, so this avoids thread races inside one worker.
    for wave_start in range(0, len(population), len(worker_addresses)):
        futures = []
        for offset, address in enumerate(worker_addresses):
            index = wave_start + offset
            if index >= len(population):
                break
            futures.append(
                client.submit(
                    _worker_evaluate,
                    index,
                    population[index],
                    stages[index],
                    payload_futures[address],
                    entry_ctx,
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                )
            )
        results.extend(client.gather(futures, direct=False))

    results.sort(key=lambda row: int(row["index"]))
    evaluated = [row.pop("candidate") for row in results]
    return evaluated, results


def _run_ga(
    client: Client,
    *,
    base_rulebook: Any,
    entry_feature_domain: Mapping[str, Any],
    worker_addresses: list[str],
    payload_futures: Mapping[str, Any],
    run_label: str,
) -> dict[str, Any]:
    from engine.core.metadata import canonical_rulebook_dict, compute_rulebook_hash
    from engine.learning import genetic as base
    from engine.learning import genetic_parallel

    cfg = base.GAConfig(
        population=POPULATION,
        generations=GENERATIONS,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=999,
        random_seed=SEED,
    )
    random.seed(SEED)
    np.random.seed(SEED)
    entry_ctx = base._normalize_entry_feature_domain(entry_feature_domain)

    population = [
        base.random_rulebook(
            base_rulebook,
            gene_scope="entry",
            entry_feature_domain=entry_ctx,
        )
        for _ in range(cfg.population)
    ]

    evaluation_batches: list[dict[str, Any]] = []
    population, initial_rows = _evaluate_batch(
        client,
        population,
        stages=[f"initial-evaluate-{index}" for index in range(len(population))],
        worker_addresses=worker_addresses,
        payload_futures=payload_futures,
        entry_ctx=entry_ctx,
    )
    evaluation_batches.append({"batch": "initial", "rows": initial_rows})
    best_overall = copy.deepcopy(max(population, key=lambda item: item.fitness))
    fitness_history: list[tuple[int, float, float]] = []
    generation_populations: list[dict[str, Any]] = []

    for generation in range(1, cfg.generations + 1):
        population.sort(key=lambda item: item.fitness, reverse=True)
        best = population[0]
        average = float(np.mean([float(rulebook.fitness) for rulebook in population]))
        fitness_history.append((generation, float(best.fitness), average))
        generation_populations.append(
            {
                "generation": generation,
                "ordered": [
                    {
                        "index": index,
                        "chromosome_hash": compute_rulebook_hash(rulebook),
                        "fitness": float(rulebook.fitness),
                        "fitness_hex": _float_hex(float(rulebook.fitness)),
                    }
                    for index, rulebook in enumerate(population)
                ],
            }
        )
        if best.fitness > best_overall.fitness:
            best_overall = copy.deepcopy(best)

        elite_count = max(1, int(cfg.population * cfg.elite_ratio))
        elites = [copy.deepcopy(item) for item in population[:elite_count]]
        children: list[Any] = []
        while len(elites) + len(children) < cfg.population:
            parent_1 = base.tournament_select(population, cfg.tournament_size)
            parent_2 = base.tournament_select(population, cfg.tournament_size)
            child = base.crossover(
                parent_1,
                parent_2,
                gene_scope="entry",
                entry_feature_domain=entry_ctx,
            )
            child = genetic_parallel._mutate_with_trace(
                child,
                cfg.mutation_rate,
                cfg.mutation_strength,
                gene_scope="entry",
                entry_ctx=entry_ctx,
            )
            children.append(child)

        children, child_rows = _evaluate_batch(
            client,
            children,
            stages=[
                f"offspring-evaluate-{generation}-{elite_count + index}"
                for index in range(len(children))
            ],
            worker_addresses=worker_addresses,
            payload_futures=payload_futures,
            entry_ctx=entry_ctx,
        )
        evaluation_batches.append({"batch": f"offspring-{generation}", "rows": child_rows})
        population = elites + children

    base._require_valid_entry_candidate(best_overall, entry_ctx, stage=f"{run_label}-best")
    for index, rulebook in enumerate(population):
        base._require_valid_entry_candidate(rulebook, entry_ctx, stage=f"{run_label}-final-{index}")

    best_params = canonical_rulebook_dict(best_overall)
    final_population = [
        {
            "index": index,
            "chromosome_hash": compute_rulebook_hash(rulebook),
            "parameter_sha256": _sha256_value(canonical_rulebook_dict(rulebook)),
            "fitness": float(rulebook.fitness),
            "fitness_hex": _float_hex(float(rulebook.fitness)),
        }
        for index, rulebook in enumerate(population)
    ]
    history = [
        {
            "generation": generation,
            "best_fitness": best,
            "best_fitness_hex": _float_hex(best),
            "mean_fitness": mean,
            "mean_fitness_hex": _float_hex(mean),
        }
        for generation, best, mean in fitness_history
    ]
    worker_assignment_counts: dict[str, int] = {}
    for batch in evaluation_batches:
        for row in batch["rows"]:
            address = str(row["worker_address"])
            worker_assignment_counts[address] = worker_assignment_counts.get(address, 0) + 1

    result = {
        "run_label": run_label,
        "worker_addresses": worker_addresses,
        "worker_assignment_counts": worker_assignment_counts,
        "best_fitness": float(best_overall.fitness),
        "best_fitness_hex": _float_hex(float(best_overall.fitness)),
        "best_chromosome_hash": compute_rulebook_hash(best_overall),
        "best_parameters": best_params,
        "best_parameters_sha256": _sha256_value(best_params),
        "best_result_sha256": _sha256_value(
            {
                "fitness_hex": _float_hex(float(best_overall.fitness)),
                "parameters": best_params,
            }
        ),
        "fitness_history": history,
        "fitness_history_sha256": _sha256_value(history),
        "generation_populations": generation_populations,
        "final_population": final_population,
        "final_population_sha256": _sha256_value(final_population),
        "final_population_multiset_sha256": _sha256_value(
            sorted(
                (
                    row["chromosome_hash"],
                    row["fitness_hex"],
                )
                for row in final_population
            )
        ),
        "evaluation_batches": evaluation_batches,
    }
    result["overall_sha256"] = _sha256_value(
        {
            "best_result_sha256": result["best_result_sha256"],
            "fitness_history_sha256": result["fitness_history_sha256"],
            "final_population_sha256": result["final_population_sha256"],
        }
    )
    return result


def _deep_first_difference(left: Any, right: Any, path: str = "$") -> dict[str, Any] | None:
    if type(left) is not type(right):
        return {"path": path, "left": left, "right": right, "kind": "type"}
    if isinstance(left, dict):
        left_keys = sorted(left)
        right_keys = sorted(right)
        if left_keys != right_keys:
            return {"path": path, "left_keys": left_keys, "right_keys": right_keys, "kind": "keys"}
        for key in left_keys:
            difference = _deep_first_difference(left[key], right[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return {"path": path, "left_len": len(left), "right_len": len(right), "kind": "length"}
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            difference = _deep_first_difference(left_item, right_item, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if isinstance(left, float):
        if _float_hex(left) != _float_hex(right):
            return {
                "path": path,
                "left": left,
                "right": right,
                "left_hex": _float_hex(left),
                "right_hex": _float_hex(right),
                "absolute_difference": abs(left - right),
                "ulp_distance": _ulp_distance(left, right),
                "kind": "float",
            }
        return None
    if left != right:
        return {"path": path, "left": left, "right": right, "kind": "value"}
    return None


def _diagnose(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    exact = left["overall_sha256"] == right["overall_sha256"]
    diagnosis: dict[str, Any] = {
        "verdict": "IDENTICAL" if exact else "DIVERGENT",
        "overall_sha_equal": exact,
        "best_result_equal": left["best_result_sha256"] == right["best_result_sha256"],
        "best_chromosome_equal": left["best_chromosome_hash"] == right["best_chromosome_hash"],
        "best_fitness_bitwise_equal": left["best_fitness_hex"] == right["best_fitness_hex"],
        "best_parameters_equal": left["best_parameters_sha256"] == right["best_parameters_sha256"],
        "fitness_history_equal": left["fitness_history_sha256"] == right["fitness_history_sha256"],
        "final_population_ordered_equal": left["final_population_sha256"] == right["final_population_sha256"],
        "final_population_multiset_equal": left["final_population_multiset_sha256"] == right["final_population_multiset_sha256"],
        "first_difference": None,
        "classification": "exact_match" if exact else "undetermined",
    }
    if exact:
        return diagnosis

    for left_batch, right_batch in zip(left["evaluation_batches"], right["evaluation_batches"]):
        if left_batch["batch"] != right_batch["batch"]:
            diagnosis["first_difference"] = {
                "scope": "evaluation_batch_name",
                "left": left_batch["batch"],
                "right": right_batch["batch"],
            }
            diagnosis["classification"] = "control_flow"
            return diagnosis
        for left_row, right_row in zip(left_batch["rows"], right_batch["rows"]):
            if left_row["chromosome_hash"] != right_row["chromosome_hash"]:
                diagnosis["first_difference"] = {
                    "scope": "candidate_chromosome",
                    "batch": left_batch["batch"],
                    "index": left_row["index"],
                    "left_hash": left_row["chromosome_hash"],
                    "right_hash": right_row["chromosome_hash"],
                }
                diagnosis["classification"] = "selection_or_rng_amplification"
                return diagnosis
            if left_row["fitness_hex"] != right_row["fitness_hex"]:
                left_fitness = float(left_row["fitness"])
                right_fitness = float(right_row["fitness"])
                diagnosis["first_difference"] = {
                    "scope": "candidate_fitness",
                    "batch": left_batch["batch"],
                    "index": left_row["index"],
                    "chromosome_hash": left_row["chromosome_hash"],
                    "left_worker": left_row["worker_address"],
                    "right_worker": right_row["worker_address"],
                    "left_fitness": left_fitness,
                    "right_fitness": right_fitness,
                    "left_hex": left_row["fitness_hex"],
                    "right_hex": right_row["fitness_hex"],
                    "absolute_difference": abs(left_fitness - right_fitness),
                    "ulp_distance": _ulp_distance(left_fitness, right_fitness),
                    "left_trade_count": left_row.get("trade_count"),
                    "right_trade_count": right_row.get("trade_count"),
                    "left_win_rate_pct": left_row.get("win_rate_pct"),
                    "right_win_rate_pct": right_row.get("win_rate_pct"),
                }
                diagnosis["classification"] = "fitness_float_or_backtest_difference"
                return diagnosis

    if diagnosis["final_population_multiset_equal"] and not diagnosis["final_population_ordered_equal"]:
        diagnosis["classification"] = "population_order_only"
        diagnosis["first_difference"] = _deep_first_difference(
            left["final_population"], right["final_population"], "$.final_population"
        )
        return diagnosis

    diagnosis["first_difference"] = (
        _deep_first_difference(left["fitness_history"], right["fitness_history"], "$.fitness_history")
        or _deep_first_difference(left["final_population"], right["final_population"], "$.final_population")
        or _deep_first_difference(left["best_parameters"], right["best_parameters"], "$.best_parameters")
    )
    if diagnosis["first_difference"] and diagnosis["first_difference"].get("kind") == "float":
        diagnosis["classification"] = "floating_point_terminal_difference"
    else:
        diagnosis["classification"] = "state_difference"
    return diagnosis


def run(scheduler: str) -> dict[str, Any]:
    stage3 = _load_stage3_module()
    market_frame, market_metadata = stage3._load_research_market_snapshot_bundle()
    market_sha = str(market_metadata["primary"]["sha256"])
    if market_sha != MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError(f"market snapshot SHA mismatch: {market_sha}")
    if _sha256_file(MARKET_HISTORY_PATH) != MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError("market_history.csv root SHA mismatch")

    context = stage3.prepare_research_ticker_context(TICKER)
    split = next(item for item in stage3._base.TRAIN_SPLITS if item["label"] == FOLD_LABEL)
    entry_feature_domain = stage3.build_entry_feature_domain(
        context,
        start=split["start"],
        end=split["end"],
    )
    payload = {
        "df": context["df"],
        "market_history_df": context["market_history_df"],
        "sector_name": context.get("sector_name", "tech"),
        "ticker_sentiment": context.get("ticker_sentiment"),
        "split": dict(split),
        "market_snapshot_sha256": market_sha,
    }

    client = Client(scheduler, direct_to_workers=False)
    try:
        worker_os = client.run(lambda: __import__("os").name)
        worker_env = client.run(
            lambda: {
                "os_name": __import__("os").name,
                "python": __import__("sys").version,
                "numpy": __import__("numpy").__version__,
                "pandas": __import__("pandas").__version__,
                "engine_file": __import__("engine").__file__,
            }
        )
        vm_workers = sorted(address for address, name in worker_os.items() if name == "posix")
        notebook_workers = sorted(address for address, name in worker_os.items() if name == "nt")
        if not vm_workers:
            raise RuntimeError(f"no VM/posix worker found: {worker_os}")
        if not notebook_workers:
            raise RuntimeError(f"no notebook/Windows worker found: {worker_os}")
        worker_a = [vm_workers[0]]
        worker_b = [vm_workers[0], notebook_workers[0]]

        payload_futures: dict[str, Any] = {}
        for address in worker_b:
            payload_futures[address] = client.scatter(
                payload,
                workers=[address],
                broadcast=False,
                direct=False,
                hash=False,
            )

        run_a = _run_ga(
            client,
            base_rulebook=context["base_rulebook"],
            entry_feature_domain=entry_feature_domain,
            worker_addresses=worker_a,
            payload_futures=payload_futures,
            run_label="A_VM_ONLY",
        )
        run_b = _run_ga(
            client,
            base_rulebook=context["base_rulebook"],
            entry_feature_domain=entry_feature_domain,
            worker_addresses=worker_b,
            payload_futures=payload_futures,
            run_label="B_VM_PLUS_NOTEBOOK",
        )
        comparison = _diagnose(run_a, run_b)
        return {
            "probe": "AAP_ENTRY_GA_DASK_VM_VS_VM_PLUS_NOTEBOOK",
            "config": {
                "ticker": TICKER,
                "fold": dict(split),
                "population": POPULATION,
                "generations": GENERATIONS,
                "random_seed": SEED,
                "early_stop_no_improve": 999,
                "gene_scope": "entry",
                "fitness_gate": "trade_count >= 10 AND win_rate_pct >= 60.0",
                "parent_rng_only": True,
                "merge_order": "candidate_input_index",
                "max_concurrent_tasks_per_worker": 1,
            },
            "manifest": {
                "market_history_sha256": market_sha,
                "market_history_rows": len(market_frame),
                "ticker_df_rows": len(context["df"]),
                "auto_fetch": False,
                "worker_local_market_file_read": False,
                "delivery": "Client.scatter then function argument",
            },
            "workers": {
                "all": worker_env,
                "run_a": worker_a,
                "run_b": worker_b,
            },
            "run_a": run_a,
            "run_b": run_b,
            "comparison": comparison,
        }
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare AAP GA on VM-only vs mixed Dask workers")
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run(args.scheduler)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
