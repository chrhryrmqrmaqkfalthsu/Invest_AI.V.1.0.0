#!/usr/bin/env python3
"""AAP overlap-entry v4 launcher with spawn-safe market-cutoff propagation.

The host runner already patches market freshness in the parent process.  On
Windows, exit/validate workers are created with ``spawn`` and import the
parallel-resume module afresh, so the parent monkey patch is not inherited.
This launcher applies the same user-approved snapshot cutoff inside every
spawned worker without changing GA, fitness, strict-AND, mutation, entry, or
exit semantics.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
V4_PATH = HERE.with_name("run_stage3_aap_overlap_entry_v4_host.py")
CUTOFF_ENV = "KINGMAKER_MARKET_CUTOFF_DATE"


def _load_v4() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_overlap_entry_v4_cutoff_base", V4_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load overlap-entry v4 runner: {V4_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v4 = _load_v4()
parallel_resume = v4.runner.parallel_resume
_ORIGINAL_LOAD_OFFICIAL = parallel_resume._load_official


def _load_official_with_cutoff() -> Any:
    official = _ORIGINAL_LOAD_OFFICIAL()
    cutoff_text = str(os.environ.get(CUTOFF_ENV, "") or "").strip()
    if not cutoff_text:
        raise RuntimeError(f"spawn worker missing required {CUTOFF_ENV}")
    cutoff_date = date.fromisoformat(cutoff_text)
    target = official.shared.mod
    original_freshness = target._primary_freshness

    def available_cutoff_freshness(
        last_date: date,
        *,
        as_of_date: date | None = None,
    ) -> dict[str, Any]:
        if last_date != cutoff_date:
            raise RuntimeError(
                "available-data cutoff mismatch in spawn worker: "
                f"expected snapshot last_date={cutoff_date}, actual={last_date}"
            )
        result = original_freshness(
            last_date,
            as_of_date=cutoff_date + timedelta(days=1),
        )
        result.update(
            {
                "basis": "user_approved_available_snapshot_last_session_spawn_safe",
                "user_approved_available_data_only": True,
                "available_data_cutoff_date": cutoff_date.isoformat(),
                "wall_clock_local_date": datetime.now().astimezone().date().isoformat(),
                "spawn_worker_cutoff_propagated": True,
            }
        )
        return result

    target._RESEARCH_MARKET_SNAPSHOT_CACHE.clear()
    target._primary_freshness = available_cutoff_freshness
    return official


# Executed both in the parent and when multiprocessing imports this script as
# __mp_main__, so the original worker functions resolve the patched loader.
parallel_resume._load_official = _load_official_with_cutoff
v4.RELEVANT_ENV_KEYS = tuple(v4.RELEVANT_ENV_KEYS) + (CUTOFF_ENV,)


def _extract_cutoff(argv: list[str]) -> str:
    try:
        index = argv.index("--market-cutoff-date")
        value = argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError("--market-cutoff-date is required") from exc
    date.fromisoformat(value)
    return value


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    cutoff = _extract_cutoff(raw)
    os.environ[CUTOFF_ENV] = cutoff
    return int(v4.main(raw))


if __name__ == "__main__":
    v4.base.mp.freeze_support()
    raise SystemExit(main())
