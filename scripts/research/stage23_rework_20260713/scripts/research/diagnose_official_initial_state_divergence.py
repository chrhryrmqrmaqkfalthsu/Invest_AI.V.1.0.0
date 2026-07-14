#!/usr/bin/env python3
"""Diagnose initial-population runtime state divergence across VM/mixed workers."""
from __future__ import annotations

import copy
import importlib.util
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np

HERE = Path(__file__).resolve()
OFFICIAL_PATH = HERE.with_name("run_dask_worker_mix_ga_official.py")
SPEC = importlib.util.spec_from_file_location("_initial_state_official", OFFICIAL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {OFFICIAL_PATH}")
OFFICIAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = OFFICIAL
SPEC.loader.exec_module(OFFICIAL)


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


def canonical_sha(value: Any) -> str:
    return OFFICIAL.BASE._sha256_value(value)


def runtime_hashes(row: Mapping[str, Any]) -> dict[str, Any]:
    attrs = dict(row.get("candidate_runtime_attrs") or {})
    diagnostics = attrs.get("_entry_fitness_diagnostics")
    hint = attrs.get("_entry_exit_mutation_hint")
    applied = attrs.get("_entry_exit_mutation_applied")
    return {
        "fitness_hex": row["fitness_hex"],
        "chromosome_hash": row["chromosome_hash"],
        "parameter_sha256": row["parameter_sha256"],
        "candidate_payload_sha256": canonical_sha(row["candidate_payload"]),
        "diagnostics_sha256": canonical_sha(diagnostics),
        "mutation_hint_sha256": canonical_sha(hint),
        "mutation_applied_sha256": canonical_sha(applied),
        "diagnostics": diagnostics,
        "mutation_hint": hint,
        "mutation_applied": applied,
        "worker_address": row["worker_address"],
        "child_pid": row["child_pid"],
    }


def evaluate_population(
    client: Any,
    *,
    label: str,
    population: list[Any],
    entry_ctx: Any,
    addresses: list[str],
    capacities: Mapping[str, int],
    payload_futures: Mapping[str, Any],
    payload_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    slots: list[str] = []
    total = sum(capacities[address] for address in addresses)
    current = {address: 0 for address in addresses}
    for _ in range(total):
        for address in addresses:
            current[address] += capacities[address]
        selected = max(addresses, key=lambda address: (current[address], address))
        current[selected] -= total
        slots.append(selected)

    domain_payload = OFFICIAL._entry_domain_payload(entry_ctx)
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for wave_start in range(0, len(population), len(slots)):
        futures = []
        envelope_futures = []
        for offset, address in enumerate(slots):
            index = wave_start + offset
            if index >= len(population):
                break
            candidate = population[index]
            envelope = {
                "index": index,
                "rulebook_payload": candidate.to_dict(),
                "runtime_attrs": {},
                "stage": f"{label}-initial-{index}",
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
                    worker_task,
                    f"{label}:initial:{index}",
                    envelope_future,
                    payload_futures[address],
                    payload_key,
                    capacities[address],
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                    retries=0,
                    key=f"initial-state-{label}-{index}",
                )
            )
        rows.extend(client.gather(futures, direct=False))
        client.cancel(envelope_futures, force=False)
    rows.sort(key=lambda row: int(row["index"]))
    return rows, {
        "wall_clock_seconds": time.perf_counter() - started,
        "worker_counts": {
            address: sum(row["worker_address"] == address for row in rows)
            for address in addresses
        },
    }


def first_runtime_difference(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for left, right in zip(left_rows, right_rows):
        left_hashes = runtime_hashes(left)
        right_hashes = runtime_hashes(right)
        for field in (
            "chromosome_hash",
            "fitness_hex",
            "candidate_payload_sha256",
            "diagnostics_sha256",
            "mutation_hint_sha256",
            "mutation_applied_sha256",
        ):
            if left_hashes[field] != right_hashes[field]:
                value_field = {
                    "diagnostics_sha256": "diagnostics",
                    "mutation_hint_sha256": "mutation_hint",
                    "mutation_applied_sha256": "mutation_applied",
                }.get(field)
                detail = None
                if value_field is not None:
                    detail = OFFICIAL.BASE._deep_first_difference(
                        left_hashes[value_field],
                        right_hashes[value_field],
                        f"$.{value_field}",
                    )
                return {
                    "index": int(left["index"]),
                    "field": field,
                    "left": left_hashes[field],
                    "right": right_hashes[field],
                    "left_worker": left_hashes["worker_address"],
                    "right_worker": right_hashes["worker_address"],
                    "left_child_pid": left_hashes["child_pid"],
                    "right_child_pid": right_hashes["child_pid"],
                    "detail": detail,
                    "left_runtime": left_hashes,
                    "right_runtime": right_hashes,
                }
    return None


def main() -> int:
    stage3 = OFFICIAL.BASE._load_stage3_module()
    _, metadata = stage3._load_research_market_snapshot_bundle()
    context = stage3.prepare_research_ticker_context(OFFICIAL.BASE.TICKER)
    split = next(
        item
        for item in stage3._base.TRAIN_SPLITS
        if item["label"] == OFFICIAL.BASE.FOLD_LABEL
    )
    domain = stage3.build_entry_feature_domain(
        context,
        start=split["start"],
        end=split["end"],
    )
    from engine.learning import genetic

    entry_ctx = genetic._normalize_entry_feature_domain(domain)
    random.seed(OFFICIAL.SEED)
    np.random.seed(OFFICIAL.SEED)
    population = [
        genetic.random_rulebook(
            context["base_rulebook"],
            gene_scope="entry",
            entry_feature_domain=entry_ctx,
        )
        for _ in range(OFFICIAL.POPULATION)
    ]
    payload = {
        "df": context["df"],
        "market_history_df": context["market_history_df"],
        "sector_name": context.get("sector_name", "tech"),
        "ticker_sentiment": context.get("ticker_sentiment"),
        "split": dict(split),
        "market_snapshot_sha256": metadata["primary"]["sha256"],
    }
    payload_key = f"AAP:{split['label']}:{metadata['primary']['sha256']}:initial-diagnosis-v1"

    client = OFFICIAL.AtomicDictClient("tcp://localhost:8786", direct_to_workers=False)
    try:
        worker_os = client.run(lambda: __import__("os").name)
        vm = sorted(address for address, value in worker_os.items() if value == "posix")[0]
        notebook = sorted(address for address, value in worker_os.items() if value == "nt")[0]
        info = client.scheduler_info()["workers"]
        capacities = {
            vm: int(info[vm]["nthreads"]),
            notebook: int(info[notebook]["nthreads"]),
        }
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

        client.run(worker_shutdown, workers=[vm, notebook])
        warm_a = client.gather(
            client.submit(
                worker_warmup,
                payload_futures[vm],
                payload_key,
                capacities[vm],
                workers=[vm],
                allow_other_workers=False,
                pure=False,
                key="initial-diagnosis-warm-a",
            ),
            direct=False,
        )
        rows_a, timing_a = evaluate_population(
            client,
            label="A_VM_ONLY",
            population=population,
            entry_ctx=entry_ctx,
            addresses=[vm],
            capacities=capacities,
            payload_futures=payload_futures,
            payload_key=payload_key,
        )

        client.run(worker_shutdown, workers=[vm])
        warm_b = client.gather(
            [
                client.submit(
                    worker_warmup,
                    payload_futures[address],
                    payload_key,
                    capacities[address],
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                    key=f"initial-diagnosis-warm-b-{address}",
                )
                for address in (vm, notebook)
            ],
            direct=False,
        )
        rows_b, timing_b = evaluate_population(
            client,
            label="B_MIXED",
            population=population,
            entry_ctx=entry_ctx,
            addresses=[vm, notebook],
            capacities=capacities,
            payload_futures=payload_futures,
            payload_key=payload_key,
        )

        difference = first_runtime_difference(rows_a, rows_b)
        summary = {
            "status": "PASS",
            "population": len(population),
            "seed": OFFICIAL.SEED,
            "fitness_rows_bitwise_equal": all(
                left["fitness_hex"] == right["fitness_hex"]
                for left, right in zip(rows_a, rows_b)
            ),
            "chromosomes_equal": all(
                left["chromosome_hash"] == right["chromosome_hash"]
                for left, right in zip(rows_a, rows_b)
            ),
            "runtime_state_equal": difference is None,
            "first_runtime_difference": difference,
            "timing_a": timing_a,
            "timing_b": timing_b,
            "warmup_a": warm_a,
            "warmup_b": warm_b,
            "market_snapshot_sha256": metadata["primary"]["sha256"],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        try:
            client.run(worker_shutdown)
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
