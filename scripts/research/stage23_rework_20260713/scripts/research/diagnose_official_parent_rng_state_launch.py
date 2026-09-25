#!/usr/bin/env python3
"""Launch parent-RNG diagnosis with scheduler-safe worker helper functions."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve()
TARGET = HERE.with_name("diagnose_official_parent_rng_state.py")
SPEC = importlib.util.spec_from_file_location("_parent_rng_diagnosis_base", TARGET)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {TARGET}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


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


BASE.DIAG.worker_task = worker_task
BASE.DIAG.worker_warmup = worker_warmup
BASE.DIAG.worker_shutdown = worker_shutdown


if __name__ == "__main__":
    raise SystemExit(BASE.main())
