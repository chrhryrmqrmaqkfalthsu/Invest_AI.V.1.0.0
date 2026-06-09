"""Central portfolio no-op gate 실행 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.portfolio.noop_gate import run_comparison_infra_gate, run_engine_noop_gate_v1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["comparison_infra_v0", "engine_noop_v1"],
        default="comparison_infra_v0",
    )
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--history-end-date", default="2026-06-09")
    parser.add_argument("--position-limit", type=float, default=30.0)
    parser.add_argument("--commission-rate", type=float, default=0.0005)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()

    if args.mode == "engine_noop_v1":
        summary = run_engine_noop_gate_v1(
            start_date=args.start_date,
            end_date=args.end_date,
            history_end_date=args.history_end_date,
            position_limit_krw=args.position_limit,
            commission_rate=args.commission_rate,
            warmup=args.warmup,
            years=args.years,
        )
    else:
        summary = run_comparison_infra_gate(
            start_date=args.start_date,
            end_date=args.end_date,
            history_end_date=args.history_end_date,
            position_limit_krw=args.position_limit,
            commission_rate=args.commission_rate,
            warmup=args.warmup,
            years=args.years,
        )

    print(json.dumps(summary, indent=2, default=str))
    if not summary["passed"]:
        print(
            f"\n[FAIL] {summary['gate']} mismatch_count={summary['mismatch_count']} "
            "— v2 진행 전 원인 확인 필요.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"\n[PASS] {summary['gate']} 통과. "
        f"trades={summary['ref_trade_count']}, mismatches=0."
    )


if __name__ == "__main__":
    main()
