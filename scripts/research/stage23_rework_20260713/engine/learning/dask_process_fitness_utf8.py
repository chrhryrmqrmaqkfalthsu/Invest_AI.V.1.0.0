"""UTF-8 launch bridge for the external Dask fitness service.

Windows ``spawn`` children inherit the service environment.  Forcing UTF-8
prevents harmless Unicode diagnostics (for example the missing-.env warning)
from failing before the fitness modules finish importing under a CP949 console.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from engine.learning import dask_process_fitness as base


def _launch_service_utf8(project_root: str, port: int) -> subprocess.Popen[Any]:
    root = str(project_root)
    vendor = str(Path(root) / "vendor")
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    preferred = os.pathsep.join([root, vendor])
    environment["PYTHONPATH"] = (
        preferred if not existing else os.pathsep.join([preferred, existing])
    )
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
        kwargs["creationflags"] = int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


base._launch_service = _launch_service_utf8

warmup_worker_pool = base.warmup_worker_pool
evaluate_via_worker_pool = base.evaluate_via_worker_pool
worker_pool_status = base.worker_pool_status
shutdown_worker_pool = base.shutdown_worker_pool
