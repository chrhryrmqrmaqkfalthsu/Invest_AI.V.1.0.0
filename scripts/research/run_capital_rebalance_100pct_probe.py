"""Run or dry-plan the 100%-exposure capital rebalance probe.

This script is research-only. It writes only to data/_system/research when run
without --dry-run, and it does not touch live trading code or brokers.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.portfolio.capital_rebalance_probe import (  # noqa: E402
    BASELINE_CSV,
    OUT_DIR,
    RebalanceConfig,
    dry_run_plan,
    run_capital_rebalance_100pct_probe,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="100%-exposure / cap-binding capital rebalance probe")
    parser.add_argument("--baseline-csv", default=str(BASELINE_CSV))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--dry-run", action="store_true", help="Only validate inputs and print the execution plan; no history load, no outputs.")
    parser.add_argument("--target-exposure-pct", type=float, default=100.0)
    parser.add_argument("--cash-buffer-pct", type=float, default=0.0)
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    parser.add_argument("--deadzone-weight-pct", type=float, default=3.0)
    parser.add_argument("--min-rebalance-days", type=int, default=5)
    parser.add_argument("--max-ticker-gross-share-pct", type=float, default=20.0)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--history-end-date", default="2026-06-09")
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()

    config = RebalanceConfig(
        target_exposure_pct=float(args.target_exposure_pct),
        cash_buffer_pct=float(args.cash_buffer_pct),
        transaction_cost_bps=float(args.transaction_cost_bps),
        deadzone_weight_pct=float(args.deadzone_weight_pct),
        min_rebalance_days=int(args.min_rebalance_days),
        max_ticker_gross_share_pct=float(args.max_ticker_gross_share_pct),
    )
    baseline_csv = Path(args.baseline_csv)
    if args.dry_run:
        summary = dry_run_plan(baseline_csv=baseline_csv, config=config)
    else:
        summary = run_capital_rebalance_100pct_probe(
            baseline_csv=baseline_csv,
            out_dir=Path(args.out_dir),
            config=config,
            start_date=str(args.start_date),
            end_date=str(args.end_date),
            history_end_date=str(args.history_end_date),
            years=int(args.years),
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
