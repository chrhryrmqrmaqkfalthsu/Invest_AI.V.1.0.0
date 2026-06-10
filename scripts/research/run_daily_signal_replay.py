"""Run or dry-plan daily signal replay for stage2 lot probes.

Research-only. No live brokers, no order code, no modification of stage2 rows.
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

from engine.portfolio.daily_signal_replay import (  # noqa: E402
    DEFAULT_OHLCV_CACHE,
    DEFAULT_RULEBOOKS_JSONL,
    DEFAULT_TRADES_JSONL,
    OUT_DIR,
    ReplayConfig,
    dry_run_plan,
    run_daily_signal_replay,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage2 daily signal replay for lot-level capital probes")
    parser.add_argument("--trades-jsonl", default=str(DEFAULT_TRADES_JSONL))
    parser.add_argument("--rulebooks-jsonl", default=str(DEFAULT_RULEBOOKS_JSONL))
    parser.add_argument("--ohlcv-cache", default=str(DEFAULT_OHLCV_CACHE))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-daily-rows-per-lot", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--full-run", action="store_true")
    parser.add_argument("--no-fail-on-entry-mismatch", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        payload = dry_run_plan(
            trades_jsonl=Path(args.trades_jsonl),
            rulebooks_jsonl=Path(args.rulebooks_jsonl),
            ohlcv_cache=Path(args.ohlcv_cache),
            limit=int(args.limit or 20),
        )
    else:
        cfg = ReplayConfig(
            max_lots=int(args.limit or 20),
            max_daily_rows_per_lot=int(args.max_daily_rows_per_lot or 0),
            full_run=bool(args.full_run),
            fail_on_entry_mismatch=not bool(args.no_fail_on_entry_mismatch),
        )
        payload = run_daily_signal_replay(
            trades_jsonl=Path(args.trades_jsonl),
            rulebooks_jsonl=Path(args.rulebooks_jsonl),
            ohlcv_cache=Path(args.ohlcv_cache),
            out_dir=Path(args.out_dir),
            config=cfg,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
