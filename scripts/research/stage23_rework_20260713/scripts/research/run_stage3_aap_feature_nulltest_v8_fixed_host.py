#!/usr/bin/env python3
"""Fixed entrypoint for AAP feature null-test v8.

The implementation module is run_stage3_aap_feature_nulltest_v8_host.py.  This
entrypoint corrects repository-root discovery before calling the implementation,
so peer OHLCV cache paths work on both the VM checkout and a notebook-local
staging bundle that may not contain a .git directory.

For this null-test, the requested training scope is qualify-only
(population 100 / generation 40 × 3 folds).  If a feature produces all3, this
entrypoint records that result and then stubs the downstream entry stage so
entry/exit/validate are not run by accident.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

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


def _load_impl() -> Any:
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


def _install_qualify_only_stub(impl: Any) -> None:
    def _run_entry_qualify_only(out_dir: Path, ctx: dict[str, Any], seed_base: int, call_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        started = time.time()
        summary = {
            "ticker": "AAP",
            "stage": "entry",
            "skipped_by_feature_nulltest_v8": True,
            "skip_reason": "qualify_only_nulltest_pop100_gen40_x3fold",
            "selected_count": 0,
            "pool_count": 0,
            "seed_base": int(seed_base),
            "call_index": int(call_index),
            "elapsed_seconds": time.time() - started,
        }
        (out_dir / "entry_rulebooks.jsonl").write_text("", encoding="utf-8")
        (out_dir / "entry_result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return summary, []

    impl.runner._run_entry = _run_entry_qualify_only
    impl.runner.FEATURE_NULLTEST_V8_QUALIFY_ONLY = True


if __name__ == "__main__":
    impl = _load_impl()
    _install_qualify_only_stub(impl)
    impl.v5.base.mp.freeze_support()
    raise SystemExit(impl.main())
