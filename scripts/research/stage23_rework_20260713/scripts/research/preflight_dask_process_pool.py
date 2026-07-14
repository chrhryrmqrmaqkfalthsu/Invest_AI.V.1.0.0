#!/usr/bin/env python3
"""Preflight the external process-pool fitness services on both Dask workers."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
import time
from typing import Any

from dask.distributed import Client

EXPECTED_MODEL = "external_loopback_spawn_process_service"
EXPECTED_FITNESS_HEX = "3ff4cb6cc6ec4670"


class AtomicClient(Client):
    def scatter(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(data, dict):
            return super().scatter([data], *args, **kwargs)[0]
        return super().scatter(data, *args, **kwargs)


def warm(payload: dict[str, Any], key: str, workers: int) -> dict[str, Any]:
    from engine.learning.dask_process_fitness import warmup_worker_pool

    return warmup_worker_pool(payload, payload_key=key, max_workers=workers)


def evaluate(
    task_id: str,
    envelope: dict[str, Any],
    payload: dict[str, Any],
    key: str,
    workers: int,
) -> dict[str, Any]:
    from engine.learning.dask_process_fitness import evaluate_via_worker_pool

    return evaluate_via_worker_pool(
        task_id,
        envelope,
        payload,
        payload_key=key,
        max_workers=workers,
    )


def status() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import worker_pool_status

    return worker_pool_status()


def stop() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import shutdown_worker_pool

    return shutdown_worker_pool()


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    scripts_dir = pathlib.Path(__file__).resolve().parent
    stage3 = load_module("_service_preflight_stage3", scripts_dir / "run_stage3_aggressive.py")

    from engine.learning import genetic
    from engine.strategies.rulebook import Rulebook

    _, metadata = stage3._load_research_market_snapshot_bundle()
    context = stage3.prepare_research_ticker_context("AAP")
    split = next(item for item in stage3._base.TRAIN_SPLITS if item["label"] == "train_3")
    entry_domain = stage3.build_entry_feature_domain(
        context,
        start=split["start"],
        end=split["end"],
    )
    entry_ctx = genetic._normalize_entry_feature_domain(entry_domain)
    entry_domain_payload = {
        feature: {
            **dict(entry_ctx.metadata[feature]),
            "values": entry_ctx.values[feature].tolist(),
        }
        for feature in entry_ctx.metadata
    }
    best_path = (
        scripts_dir.parents[4]
        / "data/_system/analysis/stage3_aap_dask_worker_mix_probe_20260714/best_parameters_full.json"
    )
    rulebook = Rulebook.from_dict(json.loads(best_path.read_text(encoding="utf-8")))
    payload = {
        "df": context["df"],
        "market_history_df": context["market_history_df"],
        "sector_name": context.get("sector_name", "tech"),
        "ticker_sentiment": context.get("ticker_sentiment"),
        "split": dict(split),
        "market_snapshot_sha256": metadata["primary"]["sha256"],
    }
    key = "AAP:train_3:external-service-preflight"

    client = AtomicClient("tcp://localhost:8786", direct_to_workers=False)
    try:
        worker_info = client.scheduler_info()["workers"]
        original_workers = {
            address: row
            for address, row in worker_info.items()
            if not str(row.get("name", "")).startswith(("official-vm", "official-notebook"))
        }
        if len(original_workers) != 2:
            raise RuntimeError(f"expected two original workers, got {list(original_workers)}")

        payload_futures: dict[str, Any] = {}
        warm_futures = []
        capacities: dict[str, int] = {}
        for address, info in original_workers.items():
            capacity = int(info["nthreads"])
            capacities[address] = capacity
            payload_future = client.scatter(
                payload,
                workers=[address],
                broadcast=False,
                direct=False,
                hash=False,
            )
            payload_futures[address] = payload_future
            warm_futures.append(
                client.submit(
                    warm,
                    payload_future,
                    key,
                    capacity,
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                    key=f"service-preflight-warm-{capacity}",
                )
            )
        warm_rows = client.gather(warm_futures, direct=False)
        warm_by_address = {row["worker_address"]: row for row in warm_rows}
        for address, capacity in capacities.items():
            row = warm_by_address[address]
            if row["execution_model"] != EXPECTED_MODEL:
                raise RuntimeError(f"wrong execution model: {row}")
            if int(row["pool_max_workers"]) != capacity:
                raise RuntimeError(f"service capacity mismatch: {row}")
            if int(row["child_pid_count"]) < max(1, capacity // 2):
                raise RuntimeError(f"insufficient process fan-out: {row}")

        futures = []
        started = time.perf_counter()
        for address, capacity in capacities.items():
            for index in range(capacity):
                envelope = {
                    "index": index,
                    "rulebook_payload": rulebook.to_dict(),
                    "runtime_attrs": {},
                    "stage": f"service-preflight-{address}-{index}",
                    "entry_domain": copy.deepcopy(entry_domain_payload),
                }
                envelope_future = client.scatter(
                    envelope,
                    workers=[address],
                    broadcast=False,
                    direct=False,
                    hash=False,
                )
                futures.append(
                    client.submit(
                        evaluate,
                        f"service-preflight:{address}:{index}",
                        envelope_future,
                        payload_futures[address],
                        key,
                        capacity,
                        workers=[address],
                        allow_other_workers=False,
                        pure=False,
                        key=f"service-preflight-eval-{capacity}-{index}",
                    )
                )
        rows = client.gather(futures, direct=False)
        elapsed = time.perf_counter() - started
        fitness_hexes = {row["fitness_hex"] for row in rows}
        if fitness_hexes != {EXPECTED_FITNESS_HEX}:
            raise RuntimeError(f"process service fitness mismatch: {fitness_hexes}")

        status_rows = client.run(status, workers=list(original_workers))
        for address, capacity in capacities.items():
            row = status_rows[address]
            if row["execution_model"] != EXPECTED_MODEL:
                raise RuntimeError(f"wrong service status: {row}")
            if int(row["executor_max_workers"]) != capacity:
                raise RuntimeError(f"wrong service process count: {row}")
            if int(row["failure_count"]) != 0:
                raise RuntimeError(f"service failures detected: {row}")
            if int(row["evaluation_count"]) != capacity:
                raise RuntimeError(f"service evaluation count mismatch: {row}")
            if int(row["peak_active_tasks"]) < max(2, capacity // 2):
                raise RuntimeError(f"insufficient concurrent service requests: {row}")

        worker_counts = {
            address: sum(row["worker_address"] == address for row in rows)
            for address in original_workers
        }
        child_pids = {
            address: sorted(
                {
                    int(row["child_pid"])
                    for row in rows
                    if row["worker_address"] == address
                }
            )
            for address in original_workers
        }
        for address, capacity in capacities.items():
            if worker_counts[address] != capacity:
                raise RuntimeError(f"worker assignment mismatch: {worker_counts}")
            if len(child_pids[address]) < max(1, capacity // 2):
                raise RuntimeError(f"child PID usage too low: {child_pids}")

        print(
            json.dumps(
                {
                    "status": "PASS",
                    "execution_model": EXPECTED_MODEL,
                    "warmup": warm_rows,
                    "evaluation_count": len(rows),
                    "fitness_hex": EXPECTED_FITNESS_HEX,
                    "wall_clock_seconds": elapsed,
                    "worker_capacities": capacities,
                    "worker_counts": worker_counts,
                    "worker_status": status_rows,
                    "child_pids": child_pids,
                    "child_pid_counts": {
                        address: len(pids) for address, pids in child_pids.items()
                    },
                    "market_snapshot_sha256": payload["market_snapshot_sha256"],
                    "worker_local_market_file_read": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        try:
            client.run(stop, workers=list(client.scheduler_info()["workers"]))
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
