#!/usr/bin/env python3
"""Run LR8D16 live loop with automatic BUY disabled.

This wrapper keeps the existing LR8D16 legacy runner behavior for exits,
pending reconciliation, manual SELL, Telegram, and dashboard hooks, but patches
Runner._try_order so every BUY signal is published to dashboard candidates only.
No automatic BUY broker order is submitted from this process.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.candidate_only_buy_guard import DEFAULT_MAX_CANDIDATES, install_candidate_only_buy_guard
from engine.live.runner import Runner

log = logging.getLogger("run_live_lr8d16_candidate_only")
_original_runner_init = Runner.__init__


def _candidate_only_max() -> int:
    try:
        return max(1, min(DEFAULT_MAX_CANDIDATES, int(os.getenv("KINGMAKER_LIVE_CANDIDATE_ONLY_MAX", str(DEFAULT_MAX_CANDIDATES)))))
    except Exception:
        return DEFAULT_MAX_CANDIDATES


def _patched_runner_init(self, *args, **kwargs):
    _original_runner_init(self, *args, **kwargs)
    install_candidate_only_buy_guard(self, max_candidates=_candidate_only_max())
    log.warning("[CANDIDATE-ONLY] LR8D16 automatic BUY disabled; dashboard candidates only; max=%s", _candidate_only_max())


def main() -> int:
    if getattr(Runner, "_candidate_only_init_patch_installed", False) is not True:
        Runner.__init__ = _patched_runner_init
        Runner._candidate_only_init_patch_installed = True
    import scripts.run_live_lr8d16_legacy as legacy

    legacy.logger.warning("[CANDIDATE-ONLY] wrapper active: legacy per-ticker BUY orders are disabled and only candidates are published")
    return int(legacy.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
