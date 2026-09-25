#!/usr/bin/env python3
"""Check whether Dask submission changes the parent's GA RNG state."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pickle
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve()
DIAG_PATH = HERE.with_name("diagnose_official_initial_state_divergence.py")
SPEC = importlib.util.spec_from_file_location("_initial_state_diag_for_rng", DIAG_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {DIAG_PATH}")
DIAG = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DIAG
SPEC.loader.exec_module(DIAG)
OFFICIAL = DIAG.OFFICIAL


def state_sha(value: Any) -> str:
    return hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()


def rng_snapshot() -> dict[str, str]:
    return {
        "python_random_sha256": state_sha(random.getstate()),
        "numpy_random_sha256": state_sha(np.random.get_state()),
    }


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
    baseline_python = random.getstate()
    baseline_numpy = np.random.get_state()
    baseline_hash = rng_snapshot()

    payload = {
        "df": context["df"],
        "market_history_df": context["market_history_df"],
        "sector_name": context.get("sector_name", "tech"),
        "ticker_sentiment": context.get("ticker_sentiment"),
        "split": dict(split),
        "market_snapshot_sha256": metadata["primary"]["sha256"],
    }
    payload_key = f"AAP:{split['label']}:{metadata['primary']['sha256']}:rng-diagnosis-v1"

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

        client.run(DIAG.worker_shutdown, workers=[vm, notebook])
        client.gather(
            client.submit(
                DIAG.worker_warmup,
                payload_futures[vm],
                payload_key,
                capacities[vm],
                workers=[vm],
                allow_other_workers=False,
                pure=False,
                key="rng-diagnosis-warm-a",
            ),
            direct=False,
        )
        random.setstate(baseline_python)
        np.random.set_state(baseline_numpy)
        before_a = rng_snapshot()
        _, timing_a = DIAG.evaluate_population(
            client,
            label="RNG_A_VM_ONLY",
            population=population,
            entry_ctx=entry_ctx,
            addresses=[vm],
            capacities=capacities,
            payload_futures=payload_futures,
            payload_key=payload_key,
        )
        after_a = rng_snapshot()

        client.run(DIAG.worker_shutdown, workers=[vm])
        client.gather(
            [
                client.submit(
                    DIAG.worker_warmup,
                    payload_futures[address],
                    payload_key,
                    capacities[address],
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                    key=f"rng-diagnosis-warm-b-{address}",
                )
                for address in (vm, notebook)
            ],
            direct=False,
        )
        random.setstate(baseline_python)
        np.random.set_state(baseline_numpy)
        before_b = rng_snapshot()
        _, timing_b = DIAG.evaluate_population(
            client,
            label="RNG_B_MIXED",
            population=population,
            entry_ctx=entry_ctx,
            addresses=[vm, notebook],
            capacities=capacities,
            payload_futures=payload_futures,
            payload_key=payload_key,
        )
        after_b = rng_snapshot()

        result = {
            "status": "PASS",
            "population": len(population),
            "seed": OFFICIAL.SEED,
            "baseline": baseline_hash,
            "before_a": before_a,
            "after_a": after_a,
            "before_b": before_b,
            "after_b": after_b,
            "before_states_equal": before_a == before_b == baseline_hash,
            "python_random_after_equal": (
                after_a["python_random_sha256"]
                == after_b["python_random_sha256"]
            ),
            "numpy_random_after_equal": (
                after_a["numpy_random_sha256"]
                == after_b["numpy_random_sha256"]
            ),
            "python_random_changed_in_a": (
                after_a["python_random_sha256"]
                != before_a["python_random_sha256"]
            ),
            "python_random_changed_in_b": (
                after_b["python_random_sha256"]
                != before_b["python_random_sha256"]
            ),
            "numpy_random_changed_in_a": (
                after_a["numpy_random_sha256"]
                != before_a["numpy_random_sha256"]
            ),
            "numpy_random_changed_in_b": (
                after_b["numpy_random_sha256"]
                != before_b["numpy_random_sha256"]
            ),
            "timing_a": timing_a,
            "timing_b": timing_b,
            "market_snapshot_sha256": metadata["primary"]["sha256"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        try:
            client.run(DIAG.worker_shutdown)
        except Exception:
            pass
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
