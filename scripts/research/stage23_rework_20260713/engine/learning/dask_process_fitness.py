"""Dask worker bridge to a local external fitness process service.

Dask worker processes are daemonic and cannot create multiprocessing children.
Each worker therefore launches ``engine.learning.dask_fitness_service`` as a
standalone non-daemon subprocess. Dask task threads communicate with that
service through a loopback TCP socket; the service owns a persistent ``spawn``
process pool sized to the worker's configured thread count.
"""
from __future__ import annotations

import os
import pickle
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

_TASK_INVOCATIONS: dict[str, int] = {}
_TASK_LOCK = threading.Lock()
_SERVICE_LOCK = threading.Lock()
_SERVICE_PROCESS: subprocess.Popen[Any] | None = None
_SERVICE_PORT = 0
_SERVICE_CONFIG_KEY = ""
_SERVICE_MAX_WORKERS = 0
_SERVICE_PROJECT_ROOT = ""


def _current_worker() -> Any:
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
    state_value = getattr(getattr(worker, "state", None), "nthreads", None)
    if state_value is not None:
        return int(state_value)
    executor_value = getattr(getattr(worker, "executor", None), "_max_workers", None)
    if executor_value is not None:
        return int(executor_value)
    return 1


def _scheduler_worker_address(worker: Any) -> str:
    reported = str(getattr(worker, "address", ""))
    if reported.startswith("tcp://") and ":" in reported:
        port = reported.rsplit(":", 1)[-1]
        if port.isdigit():
            return f"tcp://127.0.0.1:{port}"
    return reported


def _service_port() -> int:
    return 49328 if os.name == "nt" else 49308


def _recv_exact(stream: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = int(size)
    while remaining:
        chunk = stream.recv(remaining)
        if not chunk:
            raise EOFError("fitness service socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _service_request(
    request: Mapping[str, Any],
    *,
    port: int,
    timeout: float = 900.0,
) -> dict[str, Any]:
    payload = pickle.dumps(dict(request), protocol=5)
    with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout) as stream:
        stream.settimeout(timeout)
        stream.sendall(struct.pack(">Q", len(payload)) + payload)
        length = struct.unpack(">Q", _recv_exact(stream, 8))[0]
        response = pickle.loads(_recv_exact(stream, length))
    if not response.get("ok"):
        raise RuntimeError(
            f"fitness service {response.get('operation')} failed: "
            f"{response.get('error_type')}: {response.get('error')}\n"
            f"{response.get('traceback')}"
        )
    return dict(response["result"])


def _service_is_alive(port: int) -> bool:
    try:
        status = _service_request({"op": "status"}, port=port, timeout=2.0)
        return bool(status.get("service_pid"))
    except Exception:
        return False


def _wait_for_service(port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if _service_is_alive(port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"fitness service did not start on 127.0.0.1:{port}")


def _launch_service(project_root: str, port: int) -> subprocess.Popen[Any]:
    root = str(project_root)
    vendor = str(Path(root) / "vendor")
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    preferred = os.pathsep.join([root, vendor])
    environment["PYTHONPATH"] = preferred if not existing else os.pathsep.join([preferred, existing])
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["OMP_NUM_THREADS"] = "1"
    environment["OPENBLAS_NUM_THREADS"] = "1"
    environment["MKL_NUM_THREADS"] = "1"
    environment["NUMEXPR_NUM_THREADS"] = "1"
    args = [
        sys.executable,
        "-m",
        "engine.learning.dask_fitness_service",
        "--host",
        "127.0.0.1",
        "--port",
        str(int(port)),
    ]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": environment,
    }
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def _clear_service_state() -> None:
    global _SERVICE_PROCESS, _SERVICE_PORT, _SERVICE_CONFIG_KEY
    global _SERVICE_MAX_WORKERS, _SERVICE_PROJECT_ROOT

    _SERVICE_PROCESS = None
    _SERVICE_PORT = 0
    _SERVICE_CONFIG_KEY = ""
    _SERVICE_MAX_WORKERS = 0
    _SERVICE_PROJECT_ROOT = ""


def _stop_service_locked() -> dict[str, Any]:
    process = _SERVICE_PROCESS
    port = int(_SERVICE_PORT or _service_port())
    status_before: dict[str, Any] | None = None
    try:
        if _service_is_alive(port):
            status_before = _service_request({"op": "shutdown"}, port=port, timeout=30.0)
    except Exception:
        status_before = None

    if process is not None:
        try:
            process.wait(timeout=45.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=15.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15.0)
    _clear_service_state()
    return {
        "service_port": port,
        "status_before": status_before,
        "process_returncode": None if process is None else process.returncode,
    }


def _ensure_service(
    payload: Mapping[str, Any],
    *,
    payload_key: str,
    max_workers: int,
    project_root: str,
) -> dict[str, Any]:
    global _SERVICE_PROCESS, _SERVICE_PORT, _SERVICE_CONFIG_KEY
    global _SERVICE_MAX_WORKERS, _SERVICE_PROJECT_ROOT

    workers = max(1, int(max_workers))
    root = str(project_root)
    port = _service_port()
    with _SERVICE_LOCK:
        same_config = (
            _SERVICE_CONFIG_KEY == str(payload_key)
            and _SERVICE_MAX_WORKERS == workers
            and _SERVICE_PROJECT_ROOT == root
            and _service_is_alive(port)
        )
        if same_config:
            status = _service_request({"op": "status"}, port=port, timeout=10.0)
            return {
                "configured": bool(status.get("configured")),
                "reused": True,
                "payload_key": status.get("payload_key", str(payload_key)),
                "max_workers": int(status.get("max_workers", workers) or workers),
                "project_root": status.get("project_root", root),
                "service_pid": status.get("service_pid"),
                "child_pids": [],
                "child_pid_count": 0,
                "warmup_seconds": 0.0,
                "child_environment": {},
            }

        _stop_service_locked()
        if _service_is_alive(port):
            try:
                _service_request({"op": "shutdown"}, port=port, timeout=15.0)
                time.sleep(0.5)
            except Exception:
                pass
        process = _launch_service(root, port)
        _SERVICE_PROCESS = process
        _SERVICE_PORT = port
        _wait_for_service(port, timeout=90.0)
        configure = _service_request(
            {
                "op": "configure",
                "payload": payload,
                "payload_key": str(payload_key),
                "max_workers": workers,
                "project_root": root,
            },
            port=port,
            timeout=900.0,
        )
        _SERVICE_CONFIG_KEY = str(payload_key)
        _SERVICE_MAX_WORKERS = workers
        _SERVICE_PROJECT_ROOT = root
        return configure


def warmup_worker_pool(
    payload: Mapping[str, Any],
    *,
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    worker = _current_worker()
    project_root = str(Path(__import__("engine").__file__).resolve().parent.parent)
    started = time.perf_counter()
    configured = _ensure_service(
        payload,
        payload_key=payload_key,
        max_workers=max_workers,
        project_root=project_root,
    )
    return {
        "worker_address": _scheduler_worker_address(worker),
        "worker_reported_address": str(worker.address),
        "worker_nthreads": _worker_nthreads(worker),
        "pool_max_workers": int(max_workers),
        "execution_model": "external_loopback_spawn_process_service",
        "project_root": project_root,
        "payload_key": str(payload_key),
        "service_port": _service_port(),
        "service_pid": configured.get("service_pid"),
        "child_pids": configured.get("child_pids", []),
        "child_pid_count": configured.get("child_pid_count", 0),
        "child_environment": configured.get("child_environment", {}),
        "service_warmup_seconds": configured.get("warmup_seconds"),
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
    worker = _current_worker()
    with _TASK_LOCK:
        invocation = _TASK_INVOCATIONS.get(str(task_id), 0) + 1
        _TASK_INVOCATIONS[str(task_id)] = invocation

    project_root = str(Path(__import__("engine").__file__).resolve().parent.parent)
    _ensure_service(
        payload,
        payload_key=payload_key,
        max_workers=max_workers,
        project_root=project_root,
    )
    started = time.perf_counter()
    result = _service_request(
        {"op": "evaluate", "task_envelope": dict(task_envelope)},
        port=_service_port(),
        timeout=1800.0,
    )
    result.update(
        {
            "task_id": str(task_id),
            "worker_task_invocation_count": int(invocation),
            "worker_address": _scheduler_worker_address(worker),
            "worker_reported_address": str(worker.address),
            "worker_os_name": os.name,
            "worker_python": sys.version,
            "worker_nthreads": _worker_nthreads(worker),
            "worker_pool_max_workers": int(max_workers),
            "worker_engine_file": str(__import__("engine").__file__),
            "worker_process_pid": os.getpid(),
            "worker_thread_id": threading.get_ident(),
            "worker_task_seconds": time.perf_counter() - started,
            "execution_model": "external_loopback_spawn_process_service",
            "payload_key": str(payload_key),
            "service_port": _service_port(),
        }
    )
    return result


def worker_pool_status() -> dict[str, Any]:
    worker = _current_worker()
    port = _service_port()
    status = (
        _service_request({"op": "status"}, port=port, timeout=10.0)
        if _service_is_alive(port)
        else {
            "configured": False,
            "evaluation_count": 0,
            "failure_count": 0,
            "active_count": 0,
            "peak_active_count": 0,
            "max_workers": 0,
            "service_pid": None,
        }
    )
    return {
        "worker_address": _scheduler_worker_address(worker),
        "worker_reported_address": str(worker.address),
        "executor_active": bool(status.get("configured")),
        "executor_max_workers": int(status.get("max_workers", 0) or 0),
        "executor_payload_key": status.get("payload_key", ""),
        "task_invocation_key_count": len(_TASK_INVOCATIONS),
        "worker_nthreads": _worker_nthreads(worker),
        "active_tasks": int(status.get("active_count", 0) or 0),
        "peak_active_tasks": int(status.get("peak_active_count", 0) or 0),
        "evaluation_count": int(status.get("evaluation_count", 0) or 0),
        "failure_count": int(status.get("failure_count", 0) or 0),
        "service_pid": status.get("service_pid"),
        "service_port": port,
        "execution_model": "external_loopback_spawn_process_service",
    }


def shutdown_worker_pool() -> dict[str, Any]:
    worker = _current_worker()
    with _TASK_LOCK:
        invocation_count = len(_TASK_INVOCATIONS)
        _TASK_INVOCATIONS.clear()
    with _SERVICE_LOCK:
        stopped = _stop_service_locked()
    return {
        "worker_address": _scheduler_worker_address(worker),
        "worker_reported_address": str(worker.address),
        "was_active": bool(invocation_count or stopped.get("status_before")),
        "task_invocation_key_count": invocation_count,
        "peak_active_tasks": (
            (stopped.get("status_before") or {}).get("peak_active_count", 0)
        ),
        "shutdown_seconds": 0.0,
        "execution_model": "external_loopback_spawn_process_service",
        "service_stop": stopped,
    }
