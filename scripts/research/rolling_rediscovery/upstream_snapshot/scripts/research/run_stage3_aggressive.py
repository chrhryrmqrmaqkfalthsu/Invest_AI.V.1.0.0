#!/usr/bin/env python3
"""Stage3 rolling rediscovery working-copy entry point.

The copied Stage3 orchestration uses the same directly modified bilateral
interval-gene, train-only, stress/OOS double-gate and daily rolling score
pipeline as the copied Stage2 path-filter runner. Legacy exit genes and holding
caps are not invoked because this rolling design removes artificial holding-day
exits.
"""
from __future__ import annotations

import multiprocessing as mp
import sys
from pathlib import Path

ISOLATED_ROOT = Path(__file__).resolve().parents[2]
if str(ISOLATED_ROOT) in sys.path:
    sys.path.remove(str(ISOLATED_ROOT))
sys.path.insert(0, str(ISOLATED_ROOT))

from scripts.research.run_stage2_path_filter import run_stage2


def main(argv: list[str] | None = None) -> int:
    return run_stage2.main(argv)


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
