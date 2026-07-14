#!/usr/bin/env python3
"""Official AAP GA reproducibility/performance probe across Dask worker mixes.

Run A uses only the Linux/VM worker. Run B uses the Linux/VM worker and the
Windows notebook worker with capacity proportional to each worker's configured
thread count. Candidate creation, RNG, selection, crossover and mutation remain
in the client. Only fitness evaluation runs on workers.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from dask.distributed import Client as DaskClient

HERE = Path(__file__).resolve()
BASE_PATH = HERE.with_name("run_dask_worker_mix_ga_probe.py")
SPEC = importlib.util.spec_from_file_location("_official_dask_mix_base", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load base probe: {BASE_PATH}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

POPULATION = 100
GENERATIONS = 40
SEED = 2026071401
EXPECTED_EVALUATIONS = POPULATION + int(POPULATION * (1.0 - 0.2)) * GENERATIONS
ACTIVE_RUN_LABEL = "UNSET"
WORKER_CAPACITIES: dict[str, int] = {}
PAYLOAD_KEY = ""


class AtomicDictClient(DaskClient):
    """Scatter dictionaries as one opaque dependency."""

    def scatter(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(data, dict):
            return super().scatter([data], *args, **kwargs)[0]
        return super().scatter(data, *args, **kwargs)


def _runtime_attrs(rulebook: Any) -> dict[str, Any]:
    names = (
        "_entry_fitness_diagnostics",
        "_entry_exit_mutation_hint",
        "_entry_exit_mutation_applied",
    )
    return {
        name: copy.deepcopy(getattr(rulebook, name))
        for name in names
        if hasattr(rulebook, name)
    }


def _entry_domain_payload(entry_ctx: Any) -> dict[str, dict[str, Any]]:
    return {
        feature: {
            **dict(entry_ctx.metadata[feature]),
            "values": entry_ctx.values[feature].tolist(),
        }
        for feature in entry_ctx.metadata
    }


def _worker_pool_task(
    task_id: str,
    envelope: Mapping[str, Any],
    payload: Mapping[str, Any],
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    from engine.learning.dask_process_fitness import evaluate_via_worker_pool

    return evaluate_via_worker_pool(
        task_id,
        envelope,
        payload,
        payload_key=payload_key,
        max_workers=max_workers,
    )


def _worker_pool_warmup(
    payload: Mapping[str, Any],
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    from engine.learning.dask_process_fitness import warmup_worker_pool

    return warmup_worker_pool(
        payload,
        payload_key=payload_key,
        max_workers=max_workers,
    )


def _worker_pool_shutdown() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import shutdown_worker_pool

    return shutdown_worker_pool()


def _worker_pool_status() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import worker_pool_status

    return worker_pool_status()


def _weighted_slots(addresses: list[str]) -> list[str]:
    weights = {address: max(1, int(WORKER_CAPACITIES[address])) for address in addresses}
    total = sum(weights.values())
    current = {address: 0 for address in addresses}
    slots: list[str] = []
    for _ in range(total):
        for address in addresses:
            current[address] += weights[address]
        selected = max(addresses, key=lambda address: (current[address], address))
        current[selected] -= total
        slots.append(selected)
    return slots


def _evaluate_batch_pool(
    client: Any,
    population: list[Any],
    *,
    stages: list[str],
    worker_addresses: list[str],
    payload_futures: Mapping[str, Any],
    entry_ctx: Any,
) -> tuple[list[Any], list[dict[str, Any]]]:
    from engine.strategies.rulebook import Rulebook

    if len(population) != len(stages):
        raise ValueError("population/stages length mismatch")
    slots = _weighted_slots(worker_addresses)
    domain_payload = _entry_domain_payload(entry_ctx)
    results: list[dict[str, Any]] = []

    for wave_start in range(0, len(population), len(slots)):
        futures = []
        envelope_futures = []
        for offset, address in enumerate(slots):
            index = wave_start + offset
            if index >= len(population):
                break
            rulebook = population[index]
            task_id = f"{ACTIVE_RUN_LABEL}:{stages[index]}:{index}"
            envelope = {
                "index": int(index),
                "rulebook_payload": rulebook.to_dict(),
                "runtime_attrs": _runtime_attrs(rulebook),
                "stage": str(stages[index]),
                "entry_domain": domain_payload,
            }
            envelope_future = client.scatter(
                envelope,
                workers=[address],
                broadcast=False,
                direct=False,
                hash=False,
            )
            envelope_futures.append(envelope_future)
            futures.append(
                client.submit(
                    _worker_pool_task,
                    task_id,
                    envelope_future,
                    payload_futures[address],
                    PAYLOAD_KEY,
                    int(WORKER_CAPACITIES[address]),
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                    retries=1,
                    key=f"official-mix-{ACTIVE_RUN_LABEL}-{stages[index]}-{index}",
                )
            )
        results.extend(client.gather(futures, direct=False))
        client.cancel(envelope_futures, force=False)

    results.sort(key=lambda row: int(row["index"]))
    evaluated: list[Any] = []
    public_rows: list[dict[str, Any]] = []
    for row in results:
        candidate = Rulebook.from_dict(dict(row["candidate_payload"]))
        for name, value in dict(row.get("candidate_runtime_attrs") or {}).items():
            setattr(candidate, str(name), copy.deepcopy(value))
        evaluated.append(candidate)
        public = dict(row)
        public.pop("candidate_payload", None)
        public.pop("candidate_runtime_attrs", None)
        public_rows.append(public)
    return evaluated, public_rows


def _worker_metrics(client: DaskClient, addresses: list[str]) -> dict[str, Any]:
    info = client.scheduler_info()["workers"]
    result: dict[str, Any] = {}
    for address in addresses:
        row = info[address]
        metrics = dict(row.get("metrics") or {})
        result[address] = {
            "nthreads": int(row.get("nthreads", 0)),
            "memory_limit": int(row.get("memory_limit", 0)),
            "status": row.get("status"),
            "managed_bytes": int(metrics.get("managed_bytes", 0) or 0),
            "spilled_bytes": dict(metrics.get("spilled_bytes") or {}),
            "process_memory": int(metrics.get("memory", 0) or 0),
            "cpu": float(metrics.get("cpu", 0.0) or 0.0),
            "task_counts": dict(metrics.get("task_counts") or {}),
            "incoming_count_total": int(
                (metrics.get("transfer") or {}).get("incoming_count_total", 0) or 0
            ),
            "outgoing_count_total": int(
                (metrics.get("transfer") or {}).get("outgoing_count_total", 0) or 0
            ),
        }
    return result


def _new_scheduler_warnings(
    before: list[tuple[str, str]],
    after: list[tuple[str, str]],
    run_label: str,
) -> list[dict[str, str]]:
    before_counts: dict[tuple[str, str], int] = {}
    for item in before:
        before_counts[item] = before_counts.get(item, 0) + 1
    rows: list[dict[str, str]] = []
    for level, message in after:
        key = (level, message)
        remaining = before_counts.get(key, 0)
        if remaining:
            before_counts[key] = remaining - 1
            continue
        if level in {"ERROR", "WARNING"}:
            rows.append({"level": level, "message": message, "run_label": run_label})
    return rows


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction)


def _aggregate_evaluations(run_result: Mapping[str, Any]) -> dict[str, Any]:
    rows = [
        row
        for batch in run_result["evaluation_batches"]
        for row in batch["rows"]
    ]
    assignment: dict[str, int] = {}
    child_pids: dict[str, set[int]] = {}
    retries: list[dict[str, Any]] = []
    eval_seconds: list[float] = []
    task_seconds: list[float] = []
    gate_pass = 0
    for row in rows:
        address = str(row["worker_address"])
        assignment[address] = assignment.get(address, 0) + 1
        child_pids.setdefault(address, set()).add(int(row["child_pid"]))
        invocation = int(row.get("worker_task_invocation_count", 1))
        if invocation > 1:
            retries.append(
                {
                    "task_id": row.get("task_id"),
                    "worker_address": address,
                    "invocation_count": invocation,
                }
            )
        eval_seconds.append(float(row.get("evaluation_seconds", 0.0)))
        task_seconds.append(float(row.get("worker_task_seconds", 0.0)))
        gate_pass += int(bool(row.get("entry_gate_pass")))
    return {
        "evaluation_count": len(rows),
        "worker_assignment_counts": assignment,
        "worker_assignment_rates": {
            address: count / len(rows) if rows else 0.0
            for address, count in assignment.items()
        },
        "child_pids": {
            address: sorted(pids) for address, pids in child_pids.items()
        },
        "child_pid_counts": {
            address: len(pids) for address, pids in child_pids.items()
        },
        "task_reexecution_count": len(retries),
        "task_reexecutions": retries,
        "entry_gate_pass_count": gate_pass,
        "evaluation_seconds": {
            "min": min(eval_seconds) if eval_seconds else None,
            "mean": statistics.mean(eval_seconds) if eval_seconds else None,
            "p50": _percentile(eval_seconds, 0.50),
            "p95": _percentile(eval_seconds, 0.95),
            "max": max(eval_seconds) if eval_seconds else None,
            "sum": sum(eval_seconds),
        },
        "worker_task_seconds": {
            "min": min(task_seconds) if task_seconds else None,
            "mean": statistics.mean(task_seconds) if task_seconds else None,
            "p50": _percentile(task_seconds, 0.50),
            "p95": _percentile(task_seconds, 0.95),
            "max": max(task_seconds) if task_seconds else None,
            "sum": sum(task_seconds),
        },
    }


def _compact_run(
    run_result: Mapping[str, Any],
    *,
    wall_seconds: float,
    warmup: Mapping[str, Any],
    metrics_before: Mapping[str, Any],
    metrics_after: Mapping[str, Any],
    pool_status: Mapping[str, Any],
    scheduler_warnings: list[dict[str, str]],
) -> dict[str, Any]:
    telemetry = _aggregate_evaluations(run_result)
    if telemetry["evaluation_count"] != EXPECTED_EVALUATIONS:
        raise RuntimeError(
            f"evaluation count mismatch: {telemetry['evaluation_count']} != {EXPECTED_EVALUATIONS}"
        )
    return {
        "worker_addresses": list(run_result["worker_addresses"]),
        "best_fitness": run_result["best_fitness"],
        "best_fitness_hex": run_result["best_fitness_hex"],
        "best_chromosome_hash": run_result["best_chromosome_hash"],
        "best_parameters": run_result["best_parameters"],
        "best_parameters_sha256": run_result["best_parameters_sha256"],
        "best_result_sha256": run_result["best_result_sha256"],
        "fitness_history": run_result["fitness_history"],
        "fitness_history_sha256": run_result["fitness_history_sha256"],
        "final_population": run_result["final_population"],
        "final_population_sha256": run_result["final_population_sha256"],
        "final_population_multiset_sha256": run_result[
            "final_population_multiset_sha256"
        ],
        "overall_sha256": run_result["overall_sha256"],
        "wall_clock_seconds": float(wall_seconds),
        "pool_warmup": dict(warmup),
        "telemetry": telemetry,
        "worker_metrics_before": dict(metrics_before),
        "worker_metrics_after": dict(metrics_after),
        "pool_status_after": dict(pool_status),
        "scheduler_error_warning_events": scheduler_warnings,
    }


def _run_one(
    client: AtomicDictClient,
    *,
    run_label: str,
    worker_addresses: list[str],
    base_rulebook: Any,
    entry_feature_domain: Mapping[str, Any],
    payload_futures: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    global ACTIVE_RUN_LABEL

    ACTIVE_RUN_LABEL = run_label
    client.run(_worker_pool_shutdown, workers=worker_addresses)
    warm_started = time.perf_counter()
    warm_futures = [
        client.submit(
            _worker_pool_warmup,
            payload_futures[address],
            PAYLOAD_KEY,
            int(WORKER_CAPACITIES[address]),
            workers=[address],
            allow_other_workers=False,
            pure=False,
            key=f"official-warmup-{run_label}-{address}",
        )
        for address in worker_addresses
    ]
    warm_rows = client.gather(warm_futures, direct=False)
    warmup = {
        "wall_clock_seconds": time.perf_counter() - warm_started,
        "workers": {row["worker_address"]: row for row in warm_rows},
    }

    metrics_before = _worker_metrics(client, worker_addresses)
    logs_before = client.get_scheduler_logs()
    started = time.perf_counter()
    raw = BASE._run_ga(
        client,
        base_rulebook=base_rulebook,
        entry_feature_domain=entry_feature_domain,
        worker_addresses=worker_addresses,
        payload_futures=payload_futures,
        run_label=run_label,
    )
    wall_seconds = time.perf_counter() - started
    logs_after = client.get_scheduler_logs()
    metrics_after = _worker_metrics(client, worker_addresses)
    pool_status = client.run(_worker_pool_status, workers=worker_addresses)
    warnings = _new_scheduler_warnings(logs_before, logs_after, run_label)
    compact = _compact_run(
        raw,
        wall_seconds=wall_seconds,
        warmup=warmup,
        metrics_before=metrics_before,
        metrics_after=metrics_after,
        pool_status=pool_status,
        scheduler_warnings=warnings,
    )
    return raw, compact


def run(scheduler: str) -> dict[str, Any]:
    global WORKER_CAPACITIES, PAYLOAD_KEY

    BASE.POPULATION = POPULATION
    BASE.GENERATIONS = GENERATIONS
    BASE.SEED = SEED
    BASE._evaluate_batch = _evaluate_batch_pool

    stage3 = BASE._load_stage3_module()
    market_frame, market_metadata = stage3._load_research_market_snapshot_bundle()
    market_sha = str(market_metadata["primary"]["sha256"])
    if market_sha != BASE.MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError(f"market snapshot SHA mismatch: {market_sha}")
    if BASE._sha256_file(BASE.MARKET_HISTORY_PATH) != BASE.MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError("market_history.csv root SHA mismatch")

    context = stage3.prepare_research_ticker_context(BASE.TICKER)
    split = next(
        item for item in stage3._base.TRAIN_SPLITS if item["label"] == BASE.FOLD_LABEL
    )
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
    PAYLOAD_KEY = f"AAP:{BASE.FOLD_LABEL}:{market_sha}:entry-v1"

    client = AtomicDictClient(scheduler, direct_to_workers=False)
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
        scheduler_workers = client.scheduler_info()["workers"]
        vm_workers = sorted(address for address, value in worker_os.items() if value == "posix")
        notebook_workers = sorted(address for address, value in worker_os.items() if value == "nt")
        if not vm_workers or not notebook_workers:
            raise RuntimeError(f"required VM and notebook workers not found: {worker_os}")
        vm = vm_workers[0]
        notebook = notebook_workers[0]
        WORKER_CAPACITIES = {
            vm: int(scheduler_workers[vm]["nthreads"]),
            notebook: int(scheduler_workers[notebook]["nthreads"]),
        }
        if WORKER_CAPACITIES[notebook] != 28:
            raise RuntimeError(
                f"notebook worker must expose nthreads=28, got {WORKER_CAPACITIES[notebook]}"
            )

        payload_futures = {
            address: client.scatter(
                payload,
                workers=[address],
                broadcast=False,
                direct=False,
                hash=False,
            )
            for address in (vm, notebook)
        }

        raw_a, compact_a = _run_one(
            client,
            run_label="A_VM_ONLY",
            worker_addresses=[vm],
            base_rulebook=context["base_rulebook"],
            entry_feature_domain=entry_feature_domain,
            payload_futures=payload_futures,
        )
        client.run(_worker_pool_shutdown, workers=[vm])

        raw_b, compact_b = _run_one(
            client,
            run_label="B_VM_PLUS_NOTEBOOK",
            worker_addresses=[vm, notebook],
            base_rulebook=context["base_rulebook"],
            entry_feature_domain=entry_feature_domain,
            payload_futures=payload_futures,
        )
        comparison = BASE._diagnose(raw_a, raw_b)

        a_seconds = float(compact_a["wall_clock_seconds"])
        b_seconds = float(compact_b["wall_clock_seconds"])
        speedup = a_seconds / b_seconds if b_seconds > 0 else None
        reduction = (a_seconds - b_seconds) / a_seconds * 100.0 if a_seconds > 0 else None
        if b_seconds < a_seconds * 0.99:
            speed_class = "FASTER"
        elif b_seconds > a_seconds * 1.01:
            speed_class = "SLOWER"
        else:
            speed_class = "SAME_WITHIN_1_PERCENT"

        return {
            "probe": "AAP_OFFICIAL_ENTRY_GA_DASK_VM_VS_VM_PLUS_NOTEBOOK",
            "status": "PASS",
            "config": {
                "ticker": BASE.TICKER,
                "fold": dict(split),
                "population": POPULATION,
                "generations": GENERATIONS,
                "random_seed": SEED,
                "expected_evaluations_per_run": EXPECTED_EVALUATIONS,
                "gene_scope": "entry",
                "fitness_gate": "trade_count >= 10 AND win_rate_pct >= 60.0",
                "parent_rng_only": True,
                "merge_order": "candidate_input_index",
                "dask_task_retries": 1,
                "process_start_method": "spawn",
            },
            "manifest": {
                "market_history_sha256": market_sha,
                "market_history_rows": len(market_frame),
                "ticker_df_rows": len(context["df"]),
                "auto_fetch": False,
                "worker_local_market_file_read": False,
                "delivery": "atomic Client.scatter then function argument",
            },
            "workers": {
                "vm_address": vm,
                "notebook_address": notebook,
                "capacity": WORKER_CAPACITIES,
                "scheduler_metadata": {
                    address: {
                        "nthreads": int(scheduler_workers[address]["nthreads"]),
                        "memory_limit": int(scheduler_workers[address]["memory_limit"]),
                        "status": scheduler_workers[address].get("status"),
                    }
                    for address in (vm, notebook)
                },
                "environment": worker_env,
            },
            "run_a": compact_a,
            "run_b": compact_b,
            "comparison": comparison,
            "performance_comparison": {
                "run_a_wall_clock_seconds": a_seconds,
                "run_b_wall_clock_seconds": b_seconds,
                "speedup_a_over_b": speedup,
                "wall_clock_reduction_percent": reduction,
                "classification": speed_class,
                "notebook_evaluation_count": compact_b["telemetry"][
                    "worker_assignment_counts"
                ].get(notebook, 0),
                "notebook_evaluation_share": compact_b["telemetry"][
                    "worker_assignment_rates"
                ].get(notebook, 0.0),
            },
        }
    finally:
        try:
            client.run(_worker_pool_shutdown)
        except Exception:
            pass
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Official VM-only vs mixed Dask GA probe")
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args.scheduler)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
