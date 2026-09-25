#!/usr/bin/env python3
"""Run the live-safe Dask worker bundle deployment as ``__main__``.

The live installer verifies a temporary extraction and copies files in place,
which is required on Windows while Loguru keeps files under ``C:\\kingmaker``
open.  Running by path keeps task functions serialized by value, so the remote
scheduler does not need this research module on its own import path.
"""
from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    target = Path(__file__).resolve().with_name("deploy_dask_worker_bundle_live.py")
    runpy.run_path(str(target), run_name="__main__")
