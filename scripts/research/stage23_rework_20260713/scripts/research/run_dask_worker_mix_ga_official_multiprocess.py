#!/usr/bin/env python3
"""Official bit-identical/performance probe on process-based Dask workers.

A uses eight 1-thread VM worker processes. B uses the same eight VM workers plus
twenty-eight 1-thread Windows notebook worker processes.  The existing original
workers remain management-only and are excluded by worker-name prefix.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve()
OFFICIAL_PATH = HERE.with_name("run_dask_worker_mix_ga_official.py")
SPEC = importlib.util.spec_from_file_location("_official_mix_process_base", OFFICIAL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load official base: {OFFICIAL_PATH}")
OFFICIAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = OFFICIAL
SPEC.loader.exec_module(OFFICIAL)

VM_PREFIX = "official-vm"
NOTEBOOK_PREFIX = "official-notebook"
EXPECTED_VM_WORKERS = 8
EXPECTED_NOTEBOOK_WORKERS = 28


def worker_task(
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


def worker_warmup(
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


def worker_shutdown() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import shutdown_worker_pool

    return shutdown_worker_pool()


def worker_status() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import worker_pool_status

    return worker_pool_status()


def _group_workers(client: Any) -> tuple[list[str], list[str], dict[str, Any]]:
    info = client.scheduler_info()["workers"]
    vm = sorted(
        address
        for address, row in info.items()
        if str(row.get("name", "")).startswith(VM_PREFIX)
    )
    notebook = sorted(
        address
        for address, row in info.items()
        if str(row.get("name", "")).startswith(NOTEBOOK_PREFIX)
    )
    if len(vm) != EXPECTED_VM_WORKERS:
        raise RuntimeError(f"expected {EXPECTED_VM_WORKERS} VM workers, got {len(vm)}: {vm}")
    if len(notebook) != EXPECTED_NOTEBOOK_WORKERS:
        raise RuntimeError(
            f"expected {EXPECTED_NOTEBOOK_WORKERS} notebook workers, got {len(notebook)}: {notebook}"
        )
    selected = vm + notebook
    for address in selected:
        if int(info[address].get("nthreads", 0)) != 1:
            raise RuntimeError(f"official process worker must have nthreads=1: {address}: {info[address]}")
    return vm, notebook, info


def _sum_assignment(run_result: Mapping[str, Any], addresses: list[str]) -> int:
    counts = run_result["telemetry"]["worker_assignment_counts"]
    return sum(int(counts.get(address, 0)) for address in addresses)


def _summarize_worker_environment(environment: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "worker_count": len(environment),
        "python_versions": sorted({str(row.get("python")) for row in environment.values()}),
        "numpy_versions": sorted({str(row.get("numpy")) for row in environment.values()}),
        "pandas_versions": sorted({str(row.get("pandas")) for row in environment.values()}),
        "engine_roots": sorted(
            {
                str(Path(str(row.get("engine_file"))).parent.parent)
                for row in environment.values()
            }
        ),
    }


def _performance(a_seconds: float, b_seconds: float, notebook_count: int) -> dict[str, Any]:
    speedup = a_seconds / b_seconds if b_seconds > 0 else None
    reduction = (a_seconds - b_seconds) / a_seconds * 100.0 if a_seconds > 0 else None
    if b_seconds < a_seconds * 0.99:
        classification = "FASTER"
    elif b_seconds > a_seconds * 1.01:
        classification = "SLOWER"
    else:
        classification = "SAME_WITHIN_1_PERCENT"
    return {
        "run_a_wall_clock_seconds": a_seconds,
        "run_b_wall_clock_seconds": b_seconds,
        "speedup_a_over_b": speedup,
        "wall_clock_reduction_percent": reduction,
        "classification": classification,
        "notebook_evaluation_count": notebook_count,
        "notebook_evaluation_share": notebook_count / OFFICIAL.EXPECTED_EVALUATIONS,
    }


def run(scheduler: str) -> dict[str, Any]:
    OFFICIAL.BASE.POPULATION = OFFICIAL.POPULATION
    OFFICIAL.BASE.GENERATIONS = OFFICIAL.GENERATIONS
    OFFICIAL.BASE.SEED = OFFICIAL.SEED
    OFFICIAL.BASE._evaluate_batch = OFFICIAL._evaluate_batch_pool
    OFFICIAL._worker_pool_task = worker_task
    OFFICIAL._worker_pool_warmup = worker_warmup
    OFFICIAL._worker_pool_shutdown = worker_shutdown
    OFFICIAL._worker_pool_status = worker_status

    stage3 = OFFICIAL.BASE._load_stage3_module()
    market_frame, market_metadata = stage3._load_research_market_snapshot_bundle()
    market_sha = str(market_metadata["primary"]["sha256"])
    if market_sha != OFFICIAL.BASE.MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError(f"market snapshot SHA mismatch: {market_sha}")
    if OFFICIAL.BASE._sha256_file(OFFICIAL.BASE.MARKET_HISTORY_PATH) != market_sha:
        raise RuntimeError("market_history.csv root SHA mismatch")

    context = stage3.prepare_research_ticker_context(OFFICIAL.BASE.TICKER)
    split = next(
        item
        for item in stage3._base.TRAIN_SPLITS
        if item["label"] == OFFICIAL.BASE.FOLD_LABEL
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
    OFFICIAL.PAYLOAD_KEY = f"AAP:{OFFICIAL.BASE.FOLD_LABEL}:{market_sha}:official-process-v1"

    client = OFFICIAL.AtomicDictClient(scheduler, direct_to_workers=False)
    try:
        vm_workers, notebook_workers, scheduler_workers = _group_workers(client)
        selected = vm_workers + notebook_workers
        OFFICIAL.WORKER_CAPACITIES = {address: 1 for address in selected}
        environment = client.run(
            lambda: {
                "os_name": __import__("os").name,
                "python": __import__("sys").version,
                "numpy": __import__("numpy").__version__,
                "pandas": __import__("pandas").__version__,
                "engine_file": __import__("engine").__file__,
            },
            workers=selected,
        )
        payload_futures = {
            address: client.scatter(
                payload,
                workers=[address],
                broadcast=False,
                direct=False,
                hash=False,
            )
            for address in selected
        }

        raw_a, compact_a = OFFICIAL._run_one(
            client,
            run_label="A_VM_ONLY_PROCESS_WORKERS",
            worker_addresses=vm_workers,
            base_rulebook=context["base_rulebook"],
            entry_feature_domain=entry_feature_domain,
            payload_futures=payload_futures,
        )
        client.run(worker_shutdown, workers=vm_workers)

        raw_b, compact_b = OFFICIAL._run_one(
            client,
            run_label="B_VM_PLUS_NOTEBOOK_PROCESS_WORKERS",
            worker_addresses=selected,
            base_rulebook=context["base_rulebook"],
            entry_feature_domain=entry_feature_domain,
            payload_futures=payload_futures,
        )
        comparison = OFFICIAL.BASE._diagnose(raw_a, raw_b)
        vm_a_count = _sum_assignment(compact_a, vm_workers)
        vm_b_count = _sum_assignment(compact_b, vm_workers)
        notebook_b_count = _sum_assignment(compact_b, notebook_workers)
        if vm_a_count != OFFICIAL.EXPECTED_EVALUATIONS:
            raise RuntimeError(f"run A assignment mismatch: {vm_a_count}")
        if vm_b_count + notebook_b_count != OFFICIAL.EXPECTED_EVALUATIONS:
            raise RuntimeError(
                f"run B assignment mismatch: vm={vm_b_count}, notebook={notebook_b_count}"
            )

        performance = _performance(
            float(compact_a["wall_clock_seconds"]),
            float(compact_b["wall_clock_seconds"]),
            notebook_b_count,
        )
        return {
            "probe": "AAP_OFFICIAL_ENTRY_GA_PROCESS_WORKERS_VM_VS_MIXED",
            "status": "PASS",
            "config": {
                "ticker": OFFICIAL.BASE.TICKER,
                "fold": dict(split),
                "population": OFFICIAL.POPULATION,
                "generations": OFFICIAL.GENERATIONS,
                "random_seed": OFFICIAL.SEED,
                "expected_evaluations_per_run": OFFICIAL.EXPECTED_EVALUATIONS,
                "gene_scope": "entry",
                "fitness_gate": "trade_count >= 10 AND win_rate_pct >= 60.0",
                "parent_rng_only": True,
                "merge_order": "candidate_input_index",
                "dask_task_retries": 1,
                "execution_model": "independent_dask_worker_processes",
                "threads_per_worker": 1,
                "memory_limit": 0,
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
                "vm_worker_count": len(vm_workers),
                "notebook_worker_count": len(notebook_workers),
                "vm_addresses": vm_workers,
                "notebook_addresses": notebook_workers,
                "scheduler_metadata": {
                    address: {
                        "name": str(scheduler_workers[address].get("name")),
                        "nthreads": int(scheduler_workers[address].get("nthreads", 0)),
                        "memory_limit": int(
                            scheduler_workers[address].get("memory_limit", 0)
                        ),
                        "status": scheduler_workers[address].get("status"),
                    }
                    for address in selected
                },
                "environment_summary": _summarize_worker_environment(environment),
            },
            "run_a": compact_a,
            "run_b": compact_b,
            "comparison": comparison,
            "performance_comparison": performance,
            "assignment_summary": {
                "run_a_vm": vm_a_count,
                "run_b_vm": vm_b_count,
                "run_b_notebook": notebook_b_count,
            },
        }
    finally:
        try:
            client.run(worker_shutdown)
        except Exception:
            pass
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Official process-worker VM-only vs mixed Dask GA probe"
    )
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
