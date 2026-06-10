"""Run or dry-plan the lot-level cap-binding capital rebalance probe.

Research-only. Does not import or call live brokers, order code, or PositionManager.
Use --dry-run while stage2 is still running.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.portfolio.capital_lot_rebalance_probe import (  # noqa: E402
    DEFAULT_DAILY_SIGNAL_JSONL,
    DEFAULT_OHLCV_CACHE,
    DEFAULT_SELECTED_JSONL,
    DEFAULT_TRADES_JSONL,
    OUT_DIR,
    dry_run_plan,
    run_capital_lot_rebalance_probe,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Lot-level real-signal cap-binding rebalance probe")
    parser.add_argument("--trades-jsonl", default=str(DEFAULT_TRADES_JSONL))
    parser.add_argument("--selected-jsonl", default=str(DEFAULT_SELECTED_JSONL))
    parser.add_argument("--daily-signal-jsonl", default=str(DEFAULT_DAILY_SIGNAL_JSONL))
    parser.add_argument("--ohlcv-cache", default=str(DEFAULT_OHLCV_CACHE))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit for smoke/debug only.")
    parser.add_argument("--dry-run", action="store_true", help="Validate stage2 artifacts and print the execution plan; no full simulation.")
    args = parser.parse_args()

    limit = int(args.limit or 0) or None
    if args.dry_run:
        summary = dry_run_plan(
            Path(args.trades_jsonl),
            Path(args.ohlcv_cache),
            selected_jsonl=Path(args.selected_jsonl),
            daily_signal_jsonl=Path(args.daily_signal_jsonl),
            limit=limit,
        )
    else:
        summary = run_capital_lot_rebalance_probe(
            trades_jsonl=Path(args.trades_jsonl),
            selected_jsonl=Path(args.selected_jsonl),
            daily_signal_jsonl=Path(args.daily_signal_jsonl),
            ohlcv_cache=Path(args.ohlcv_cache),
            out_dir=Path(args.out_dir),
            limit=limit,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
