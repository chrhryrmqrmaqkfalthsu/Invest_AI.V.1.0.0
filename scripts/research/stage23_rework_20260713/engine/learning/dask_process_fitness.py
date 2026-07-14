"""Thread-safe Dask entry-fitness evaluator.

The current Dask workers are daemon processes and cannot create child process
pools. This module keeps the public API used by the official probe but executes
fitness directly in the worker's configured threads. Thread safety comes from
``entry_fitness_threadsafe.run_entry_backtest_threadsafe``, which passes the
provisional-exit arguments explicitly and never patches shared module globals.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import struct
import sys
import threading
import time
from typing import Any, Mapping

_TASK_INVOCATIONS: dict[str, int] = {}
_TASK_LOCK = threading.Lock()
_ACTIVE_TASKS = 0
_PEAK_ACTIVE_TASKS = 0
_ACTIVE_LOCK = threading.Lock()


def _current_worker() -> Any:
    """Resolve the worker in task and ``Client.run`` execution contexts."""
    try:
        from dask.distributed import get_worker

        return get_worker()
    except ValueError:
        from distributed.worker import _global_workers

        workers = list(_global_workers)
        if len(workers) != 1:
            raise RuntimeError(f"cannot resolve current Dask worker: {len(workers)} candidates")
        return workers[0]


def _worker_nthreads(worker: Any) -> int:
    """Return configured Dask worker concurrency across Dask API versions."""
    state_value = getattr(getattr(worker, "state", None), "nthreads", None)
    if state_value is not None:
        return int(state_value)
    executor_value = getattr(getattr(worker, "executor", None), "_max_workers", None)
    if executor_value is not None:
        return int(executor_value)
    return 1


def _scheduler_worker_address(worker: Any) -> str:
    """Normalize localhost aliases to the scheduler's registered worker key."""
    reported = str(getattr(worker, "address", ""))
    if reported.startswith("tcp://") and ":" in reported:
        port = reported.rsplit(":", 1)[-1]
        if port.isdigit():
            return f"tcp://127.0.0.1:{port}"
    return reported


def _float_hex(value: float) -> str:
    return struct.pack(">d", float(value)).hex()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


def warmup_worker_pool(
    payload: Mapping[str, Any],
    *,
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    worker = _current_worker()
    from engine.learning.entry_fitness_threadsafe import run_entry_backtest_threadsafe

    if payload.get("market_snapshot_sha256") is None:
        raise RuntimeError("market snapshot SHA missing from worker payload")
    configured = _worker_nthreads(worker)
    return {
        "worker_address": _scheduler_worker_address(worker),
        "worker_reported_address": str(worker.address),
        "worker_nthreads": configured,
        "pool_max_workers": int(max_workers),
        "execution_model": "dask_worker_threads_threadsafe_backtest",
        "process_pid": os.getpid(),
        "child_pids": [os.getpid()],
        "child_pid_count": 1,
        "threadsafe_function": f"{run_entry_backtest_threadsafe.__module__}.{run_entry_backtest_threadsafe.__name__}",
        "payload_key": str(payload_key),
        "warmup_seconds": time.perf_counter() - started,
    }


def evaluate_via_worker_pool(
    task_id: str,
    task_envelope: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    global _ACTIVE_TASKS, _PEAK_ACTIVE_TASKS

    import numpy as np
    import pandas as pd

    from engine.core.metadata import compute_rulebook_hash
    from engine.learning import execution_mode_backtest as execution_bt
    from engine.learning import genetic as genetic
    from engine.learning.entry_fitness_threadsafe import run_entry_backtest_threadsafe
    from engine.strategies.rulebook import Rulebook

    worker = _current_worker()
    with _TASK_LOCK:
        invocation = _TASK_INVOCATIONS.get(str(task_id), 0) + 1
        _TASK_INVOCATIONS[str(task_id)] = invocation
    with _ACTIVE_LOCK:
        _ACTIVE_TASKS += 1
        _PEAK_ACTIVE_TASKS = max(_PEAK_ACTIVE_TASKS, _ACTIVE_TASKS)
        active_at_start = _ACTIVE_TASKS
        peak_at_start = _PEAK_ACTIVE_TASKS

    started = time.perf_counter()
    try:
        candidate = Rulebook.from_dict(dict(task_envelope["rulebook_payload"]))
        for name, value in dict(task_envelope.get("runtime_attrs") or {}).items():
            setattr(candidate, str(name), copy.deepcopy(value))
        entry_ctx = genetic._normalize_entry_feature_domain(task_envelope["entry_domain"])
        stage = str(task_envelope["stage"])

        def evaluate_fn(item: Any) -> float:
            split = payload["split"]
            result = run_entry_backtest_threadsafe(
                item,
                payload["df"],
                start_date=str(split["start"]),
                end_date=str(split["end"]),
                position_limit_krw=120_000.0,
                market_history_df=payload["market_history_df"],
                sector_name=str(payload.get("sector_name") or "tech"),
                ticker_sentiment=payload.get("ticker_sentiment"),
                complexity_penalty_per_mask=0.0,
                use_llm_events=False,
                entry_execution_mode="t_plus_1_open",
                exit_execution_mode="conservative_core",
                fold_exit_policy="fold_end_mark_to_market",
                live_hard_stop_guard=True,
                entry_phase_max_holding_days=7,
            )
            return float(getattr(result, "fitness", -1_000_000_000.0))

        fitness = genetic._evaluate_candidate(
            candidate,
            evaluate_fn,
            gene_scope="entry",
            entry_ctx=entry_ctx,
            stage=stage,
        )
        candidate.fitness = float(fitness)
        diagnostics = dict(
            getattr(candidate, execution_bt.ENTRY_FITNESS_DIAGNOSTICS_ATTR, {}) or {}
        )
        elapsed = time.perf_counter() - started
        return {
            "index": int(task_envelope["index"]),
            "candidate_payload": candidate.to_dict(),
            "candidate_runtime_attrs": _runtime_attrs(candidate),
            "chromosome_hash": compute_rulebook_hash(candidate),
            "parameter_sha256": _canonical_sha256(candidate.to_dict()),
            "fitness": float(fitness),
            "fitness_hex": _float_hex(float(fitness)),
            "trade_count": diagnostics.get("trade_count"),
            "win_rate_pct": diagnostics.get("win_rate_pct"),
            "entry_gate_pass": diagnostics.get("entry_gate_pass"),
            "task_id": str(task_id),
            "worker_task_invocation_count": int(invocation),
            "worker_address": _scheduler_worker_address(worker),
            "worker_reported_address": str(worker.address),
            "worker_os_name": os.name,
            "worker_python": sys.version,
            "worker_numpy": np.__version__,
            "worker_pandas": pd.__version__,
            "worker_nthreads": _worker_nthreads(worker),
            "worker_pool_max_workers": int(max_workers),
            "worker_engine_file": str(__import__("engine").__file__),
            "worker_process_pid": os.getpid(),
            "child_pid": os.getpid(),
            "worker_thread_id": threading.get_ident(),
            "active_tasks_at_start": int(active_at_start),
            "peak_active_tasks_at_start": int(peak_at_start),
            "evaluation_seconds": elapsed,
            "worker_task_seconds": elapsed,
            "market_snapshot_sha256": payload["market_snapshot_sha256"],
            "worker_local_market_file_read": False,
            "execution_model": "dask_worker_threads_threadsafe_backtest",
            "payload_key": str(payload_key),
        }
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_TASKS -= 1


def worker_pool_status() -> dict[str, Any]:
    worker = _current_worker()
    configured = _worker_nthreads(worker)
    with _ACTIVE_LOCK:
        active = int(_ACTIVE_TASKS)
        peak = int(_PEAK_ACTIVE_TASKS)
    return {
        "worker_address": _scheduler_worker_address(worker),
        "worker_reported_address": str(worker.address),
        "executor_active": True,
        "executor_max_workers": configured,
        "executor_payload_key": "threadsafe-direct",
        "task_invocation_key_count": len(_TASK_INVOCATIONS),
        "worker_nthreads": configured,
        "active_tasks": active,
        "peak_active_tasks": peak,
        "execution_model": "dask_worker_threads_threadsafe_backtest",
    }


def shutdown_worker_pool() -> dict[str, Any]:
    global _PEAK_ACTIVE_TASKS

    worker = _current_worker()
    with _TASK_LOCK:
        invocation_count = len(_TASK_INVOCATIONS)
        _TASK_INVOCATIONS.clear()
    with _ACTIVE_LOCK:
        peak = int(_PEAK_ACTIVE_TASKS)
        _PEAK_ACTIVE_TASKS = 0
    return {
        "worker_address": _scheduler_worker_address(worker),
        "worker_reported_address": str(worker.address),
        "was_active": bool(invocation_count),
        "task_invocation_key_count": invocation_count,
        "peak_active_tasks": peak,
        "shutdown_seconds": 0.0,
        "execution_model": "dask_worker_threads_threadsafe_backtest",
    }
