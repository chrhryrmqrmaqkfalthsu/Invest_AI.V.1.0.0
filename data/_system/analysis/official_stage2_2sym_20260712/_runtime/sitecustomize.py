"""Runtime-only protection for official Stage 2 research runs.

The official Stage 2 code is left unchanged. This module is loaded through
PYTHONPATH and replaces only get_market_history() with a read-only loader for
the already regenerated cache. It prevents the stale-cache branch in
engine.market.context.build_market_history() from writing market_history.csv.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pandas as pd

EXPECTED_SHA256 = "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38"
ROOT = Path(os.environ.get("KINGMAKER_PROJECT_ROOT", Path.cwd())).resolve()
MARKET_PATH = ROOT / "data/_system/market_history.csv"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _readonly_get_market_history(years: int = 6):
    actual = _sha256(MARKET_PATH)
    if actual != EXPECTED_SHA256:
        raise RuntimeError(
            f"market_history.csv SHA mismatch: expected={EXPECTED_SHA256} actual={actual}"
        )
    frame = pd.read_csv(MARKET_PATH, index_col=0, parse_dates=True)
    if frame.empty:
        raise RuntimeError("market_history.csv is empty")
    from engine.market import context as market_context

    return market_context._merge_v2_events(frame)


from engine.market import context as _market_context  # noqa: E402

_market_context.get_market_history = _readonly_get_market_history

# prepare_ticker_context imports get_market_history by name, so replace its
# module-global binding as well.
from engine.pipeline import context as _pipeline_context  # noqa: E402

_pipeline_context.get_market_history = _readonly_get_market_history
