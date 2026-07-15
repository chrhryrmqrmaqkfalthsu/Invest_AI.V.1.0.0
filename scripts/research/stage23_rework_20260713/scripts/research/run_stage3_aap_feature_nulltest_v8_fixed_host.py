#!/usr/bin/env python3
"""Fixed entrypoint for AAP feature null-test v8.

The implementation module is run_stage3_aap_feature_nulltest_v8_host.py.  This
entrypoint corrects repository-root discovery before calling the implementation,
so peer OHLCV cache paths work on both the VM checkout and a notebook-local
staging bundle that may not contain a .git directory.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
IMPL = HERE.with_name("run_stage3_aap_feature_nulltest_v8_host.py")


def _repo_root() -> Path:
    for parent in HERE.parents:
        if (parent / "data/_system").exists() and (parent / "scripts/research/stage23_rework_20260713").exists():
            return parent
    for parent in HERE.parents:
        if (parent / ".git").exists() and (parent / "data/_system").exists():
            return parent
    raise RuntimeError("cannot resolve kingmaker repository root")


def _load_impl():
    spec = importlib.util.spec_from_file_location("_feature_nulltest_v8_impl_fixed", IMPL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load implementation: {IMPL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    root = _repo_root()
    module.REPO_ROOT = root
    module.WORK_ROOT = root / "scripts/research/stage23_rework_20260713"
    module.PEER_CACHE = root / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
    return module


if __name__ == "__main__":
    impl = _load_impl()
    impl.v5.base.mp.freeze_support()
    raise SystemExit(impl.main())
