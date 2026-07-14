#!/usr/bin/env python3
"""Preflight concurrent thread-safe Dask entry-fitness evaluation."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
import time
from typing import Any

from dask.distributed import Client


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
    stage3 = load_module("_thread_preflight_stage3", scripts_dir / "run_stage3_aggressive.py")

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
    key = "AAP:train_3:thread-preflight"

    client = AtomicClient("tcp://localhost:8786", direct_to_workers=False)
    try:
        worker_info = client.scheduler_info()["workers"]
        payload_futures: dict[str, Any] = {}
        warm_futures = []
        for address, info in worker_info.items():
            nthreads = int(info["nthreads"])
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
                    nthreads,
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                    key=f"thread-preflight-warm-{nthreads}",
                )
            )
        warm_rows = client.gather(warm_futures, direct=False)
        for row in warm_rows:
            if row["execution_model"] != "dask_worker_threads_threadsafe_backtest":
                raise RuntimeError(f"wrong execution model: {row}")
            if int(row["worker_nthreads"]) != int(row["pool_max_workers"]):
                raise RuntimeError(f"thread capacity mismatch: {row}")

        futures = []
        started = time.perf_counter()
        for address, info in worker_info.items():
            nthreads = int(info["nthreads"])
            for index in range(nthreads):
                envelope = {
                    "index": index,
                    "rulebook_payload": rulebook.to_dict(),
                    "runtime_attrs": {},
                    "stage": f"thread-preflight-{address}-{index}",
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
                        f"thread-preflight:{address}:{index}",
                        envelope_future,
                        payload_futures[address],
                        key,
                        nthreads,
                        workers=[address],
                        allow_other_workers=False,
                        pure=False,
                        key=f"thread-preflight-eval-{nthreads}-{index}",
                    )
                )
        rows = client.gather(futures, direct=False)
        elapsed = time.perf_counter() - started
        fitness_hexes = {row["fitness_hex"] for row in rows}
        if len(fitness_hexes) != 1:
            raise RuntimeError(f"concurrent fitness mismatch: {fitness_hexes}")
        status_rows = client.run(status)
        for address, row in status_rows.items():
            if int(row["peak_active_tasks"]) < 2:
                raise RuntimeError(f"worker did not execute concurrent tasks: {address}: {row}")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "warmup": warm_rows,
                    "evaluation_count": len(rows),
                    "fitness_hex": next(iter(fitness_hexes)),
                    "wall_clock_seconds": elapsed,
                    "worker_counts": {
                        address: sum(row["worker_address"] == address for row in rows)
                        for address in worker_info
                    },
                    "worker_status": status_rows,
                    "thread_ids": {
                        address: sorted(
                            {
                                int(row["worker_thread_id"])
                                for row in rows
                                if row["worker_address"] == address
                            }
                        )
                        for address in worker_info
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        try:
            client.run(stop)
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
