"""v0 comparison_infra_gate 실행 래퍼.
self-vs-self 게이트 — 반드시 0 mismatch로 통과해야 정상.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.portfolio.noop_gate import run_comparison_infra_gate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--history-end-date", default="2026-06-09")
    parser.add_argument("--position-limit", type=float, default=30.0)
    parser.add_argument("--commission-rate", type=float, default=0.0005)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--years", type=int, default=3)
    args = parser.parse_args()

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
            f"\n[FAIL] mismatch_count={summary['mismatch_count']} "
            "— data nondeterminism or comparator bug. v1 시작 불가.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "\n[PASS] comparison_infra_gate v0 통과. "
        f"trades={summary['ref_trade_count']}, mismatches=0. v1(daily loop) 진행 가능."
    )


if __name__ == "__main__":
    main()
