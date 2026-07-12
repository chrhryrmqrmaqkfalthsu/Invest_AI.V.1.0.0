#!/usr/bin/env python3
"""Path-resolution corrected launcher for run_entry_filter_2d3pct.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("entry_filter_2d3pct_core", HERE / "run_entry_filter_2d3pct.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load core runner")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def resolve_rl_replay(candidate):
    source = Path(str(candidate.get("source_file") or ""))
    if source.is_absolute():
        resolved = source
    else:
        direct = mod.PROJECT_ROOT / source
        batch_relative = mod.BATCH_ROOT / source
        resolved = direct if direct.exists() else batch_relative
    return resolved.parent / "rl_replay_trades.jsonl"


mod.resolve_rl_replay = resolve_rl_replay
raise SystemExit(mod.main())
