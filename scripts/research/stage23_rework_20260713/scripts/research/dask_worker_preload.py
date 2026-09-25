"""Dask worker preload for the Stage 2/3 research engine bundle.

Windows worker example::

    dask worker tcp://<scheduler>:8786 \
        --preload C:\\kingmaker\\worker_preload.py

The current deployment tool also activates the same paths in already-running
workers with ``Client.run``.  This preload keeps the setup after a worker
restart without requiring a local market-data copy.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from typing import Any

WINDOWS_PROJECT_ROOT = Path(r"C:\kingmaker")
LINUX_PROJECT_ROOT = Path("/tmp/kingmaker_stage23_rework_20260713")


def resolve_project_root() -> Path:
    return WINDOWS_PROJECT_ROOT if os.name == "nt" else LINUX_PROJECT_ROOT


def activate_project_root(
    project_root: str | Path | None = None,
    *,
    purge_engine: bool = False,
) -> dict[str, Any]:
    """Put the deployed project and pure-Python vendor directory first."""
    root = Path(project_root) if project_root is not None else resolve_project_root()
    vendor = root / "vendor"
    if not (root / "engine" / "__init__.py").is_file():
        raise FileNotFoundError(f"deployed engine package missing: {root / 'engine'}")
    if not (root / "config" / "policy.yaml").is_file():
        raise FileNotFoundError(f"deployed policy missing: {root / 'config/policy.yaml'}")

    preferred = [str(root), str(vendor)]
    sys.path[:] = preferred + [item for item in sys.path if item not in preferred]
    if purge_engine:
        for name in tuple(sys.modules):
            if name == "engine" or name.startswith("engine."):
                sys.modules.pop(name, None)
    importlib.invalidate_caches()

    engine = importlib.import_module("engine")
    return {
        "project_root": str(root),
        "vendor_root": str(vendor),
        "engine_file": str(getattr(engine, "__file__", "")),
        "sys_path_head": list(sys.path[:4]),
    }


def dask_setup(worker: Any) -> None:
    """Dask ``--preload`` hook."""
    result = activate_project_root(purge_engine=True)
    worker.kingmaker_project_root = result["project_root"]
    worker.kingmaker_engine_file = result["engine_file"]
