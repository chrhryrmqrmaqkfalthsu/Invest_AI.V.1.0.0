"""Local process-pool service for Dask entry-fitness evaluation.

Dask worker processes are daemonic and cannot spawn multiprocessing children.
A Dask task can, however, launch this standalone non-daemon Python service with
``subprocess.Popen``.  The service owns a persistent ``spawn`` process pool and
accepts concurrent requests over a loopback TCP socket.  This preserves the
single forwarded Dask worker port while using all local CPU cores.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import multiprocessing as mp
import os
import pickle
import socketserver
import struct
import sys
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Mapping

_CHILD_PAYLOAD: Mapping[str, Any] | None = None
_EXECUTOR: ProcessPoolExecutor | None = None
_EXECUTOR_LOCK = threading.Lock()
_CONFIG_KEY = ""
_MAX_WORKERS = 0
_PROJECT_ROOT = ""
_EVALUATION_COUNT = 0
_FAILURE_COUNT = 0
_ACTIVE_COUNT = 0
_PEAK_ACTIVE_COUNT = 0
_STATE_LOCK = threading.Lock()
_SERVER: "FitnessTCPServer | None" = None


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


def _child_initializer(payload: Mapping[str, Any], project_root: str) -> None:
    global _CHILD_PAYLOAD

    root = str(project_root)
    vendor = str(Path(root) / "vendor")
    sys.path[:] = [root, vendor] + [item for item in sys.path if item not in {root, vendor}]
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"
    _CHILD_PAYLOAD = payload


def _warmup_job(index: int) -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    time.sleep(0.15)
    return {
        "index": int(index),
        "pid": os.getpid(),
        "python": sys.version,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "engine_file": str(__import__("engine").__file__),
    }


def _evaluate_candidate_child(task_envelope: Mapping[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    if _CHILD_PAYLOAD is None:
        raise RuntimeError("fitness service child payload is not initialized")

    import numpy as np
    import pandas as pd

    from engine.core.metadata import compute_rulebook_hash
    from engine.learning import execution_mode_backtest as execution_bt
    from engine.learning import genetic
    from engine.learning.entry_fitness_threadsafe import run_entry_backtest_threadsafe
    from engine.strategies.rulebook import Rulebook

    candidate = Rulebook.from_dict(dict(task_envelope["rulebook_payload"]))
    for name, value in dict(task_envelope.get("runtime_attrs") or {}).items():
        setattr(candidate, str(name), copy.deepcopy(value))
    entry_ctx = genetic._normalize_entry_feature_domain(task_envelope["entry_domain"])
    stage = str(task_envelope["stage"])
    payload = _CHILD_PAYLOAD

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
        "child_pid": os.getpid(),
        "child_python": sys.version,
        "child_numpy": np.__version__,
        "child_pandas": pd.__version__,
        "child_engine_file": str(__import__("engine").__file__),
        "evaluation_seconds": time.perf_counter() - started,
        "market_snapshot_sha256": payload["market_snapshot_sha256"],
        "worker_local_market_file_read": False,
    }


def _shutdown_executor() -> None:
    global _EXECUTOR, _CONFIG_KEY, _MAX_WORKERS, _PROJECT_ROOT

    executor = _EXECUTOR
    _EXECUTOR = None
    _CONFIG_KEY = ""
    _MAX_WORKERS = 0
    _PROJECT_ROOT = ""
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)


def _configure(request: Mapping[str, Any]) -> dict[str, Any]:
    global _EXECUTOR, _CONFIG_KEY, _MAX_WORKERS, _PROJECT_ROOT
    global _EVALUATION_COUNT, _FAILURE_COUNT, _ACTIVE_COUNT, _PEAK_ACTIVE_COUNT

    payload = request["payload"]
    config_key = str(request["payload_key"])
    max_workers = max(1, int(request["max_workers"]))
    project_root = str(request["project_root"])
    started = time.perf_counter()

    with _EXECUTOR_LOCK:
        reused = (
            _EXECUTOR is not None
            and _CONFIG_KEY == config_key
            and _MAX_WORKERS == max_workers
            and _PROJECT_ROOT == project_root
        )
        if not reused:
            _shutdown_executor()
            context = mp.get_context("spawn")
            _EXECUTOR = ProcessPoolExecutor(
                max_workers=max_workers,
                mp_context=context,
                initializer=_child_initializer,
                initargs=(payload, project_root),
            )
            _CONFIG_KEY = config_key
            _MAX_WORKERS = max_workers
            _PROJECT_ROOT = project_root
            with _STATE_LOCK:
                _EVALUATION_COUNT = 0
                _FAILURE_COUNT = 0
                _ACTIVE_COUNT = 0
                _PEAK_ACTIVE_COUNT = 0

        assert _EXECUTOR is not None
        warmup_futures = [
            _EXECUTOR.submit(_warmup_job, index)
            for index in range(max_workers * 2)
        ]
        warmup_rows = [future.result() for future in warmup_futures]

    child_pids = sorted({int(row["pid"]) for row in warmup_rows})
    return {
        "configured": True,
        "reused": reused,
        "payload_key": config_key,
        "max_workers": max_workers,
        "project_root": project_root,
        "service_pid": os.getpid(),
        "child_pids": child_pids,
        "child_pid_count": len(child_pids),
        "warmup_seconds": time.perf_counter() - started,
        "child_environment": {
            "python_versions": sorted({row["python"] for row in warmup_rows}),
            "numpy_versions": sorted({row["numpy"] for row in warmup_rows}),
            "pandas_versions": sorted({row["pandas"] for row in warmup_rows}),
            "engine_files": sorted({row["engine_file"] for row in warmup_rows}),
        },
    }


def _evaluate(request: Mapping[str, Any]) -> dict[str, Any]:
    global _EVALUATION_COUNT, _FAILURE_COUNT, _ACTIVE_COUNT, _PEAK_ACTIVE_COUNT

    with _EXECUTOR_LOCK:
        executor = _EXECUTOR
    if executor is None:
        raise RuntimeError("fitness service is not configured")

    with _STATE_LOCK:
        _ACTIVE_COUNT += 1
        _PEAK_ACTIVE_COUNT = max(_PEAK_ACTIVE_COUNT, _ACTIVE_COUNT)
        active_at_start = _ACTIVE_COUNT
        peak_at_start = _PEAK_ACTIVE_COUNT
    try:
        result = executor.submit(
            _evaluate_candidate_child,
            request["task_envelope"],
        ).result()
        with _STATE_LOCK:
            _EVALUATION_COUNT += 1
        result["service_active_at_start"] = int(active_at_start)
        result["service_peak_active_at_start"] = int(peak_at_start)
        return result
    except Exception:
        with _STATE_LOCK:
            _FAILURE_COUNT += 1
        raise
    finally:
        with _STATE_LOCK:
            _ACTIVE_COUNT -= 1


def _status() -> dict[str, Any]:
    with _STATE_LOCK:
        state = {
            "evaluation_count": int(_EVALUATION_COUNT),
            "failure_count": int(_FAILURE_COUNT),
            "active_count": int(_ACTIVE_COUNT),
            "peak_active_count": int(_PEAK_ACTIVE_COUNT),
        }
    state.update(
        {
            "configured": _EXECUTOR is not None,
            "payload_key": _CONFIG_KEY,
            "max_workers": int(_MAX_WORKERS),
            "project_root": _PROJECT_ROOT,
            "service_pid": os.getpid(),
        }
    )
    return state


def _recv_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise EOFError("fitness service socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_message(stream: Any) -> Any:
    length = struct.unpack(">Q", _recv_exact(stream, 8))[0]
    return pickle.loads(_recv_exact(stream, length))


def _send_message(stream: Any, value: Any) -> None:
    payload = pickle.dumps(value, protocol=5)
    stream.sendall(struct.pack(">Q", len(payload)) + payload)


def _request_shutdown() -> None:
    time.sleep(0.05)
    server = _SERVER
    if server is not None:
        server.shutdown()


class FitnessRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request: Mapping[str, Any] | None = None
        try:
            request = _recv_message(self.request)
            operation = str(request.get("op"))
            if operation == "configure":
                result = _configure(request)
            elif operation == "evaluate":
                result = _evaluate(request)
            elif operation == "status":
                result = _status()
            elif operation == "shutdown":
                result = {"shutdown": True, **_status()}
                threading.Thread(target=_request_shutdown, daemon=True).start()
            else:
                raise ValueError(f"unsupported operation: {operation}")
            _send_message(self.request, {"ok": True, "result": result})
        except Exception as exc:
            _send_message(
                self.request,
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                    "operation": None if request is None else request.get("op"),
                },
            )


class FitnessTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kingmaker local fitness process service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _SERVER

    args = parse_args(argv)
    server = FitnessTCPServer((args.host, int(args.port)), FitnessRequestHandler)
    _SERVER = server
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        with _EXECUTOR_LOCK:
            _shutdown_executor()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
