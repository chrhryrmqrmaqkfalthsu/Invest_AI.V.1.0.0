"""Central portfolio no-op gate 실행 래퍼."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.portfolio.capital_allocation_probe import run_capital_allocation_reweight_probe
from engine.portfolio.conservative_core_exit_gate import run_conservative_core_exit_gate
from engine.portfolio.noop_gate import (
    run_comparison_infra_gate,
    run_engine_noop_gate_v1,
    run_fractional_gate_v2,
    run_live_current_proxy_baseline,
    run_tplus1_entry_gate,
)
from engine.portfolio.pit_universe_bias_probe import run_pit_universe_manifest_builder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=[
            "comparison_infra_v0",
            "engine_noop_v1",
            "fractional_v2",
            "live_current_proxy",
            "tplus1_entry",
            "conservative_core_exit",
            "capital_reweight_probe",
            "pit_universe_manifest",
        ],
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
    elif args.mode == "fractional_v2":
        summary = run_fractional_gate_v2(
            start_date=args.start_date,
            end_date=args.end_date,
            history_end_date=args.history_end_date,
            position_limit_krw=args.position_limit,
            commission_rate=args.commission_rate,
            warmup=args.warmup,
            years=args.years,
        )
    elif args.mode == "live_current_proxy":
        summary = run_live_current_proxy_baseline(
            start_date=args.start_date,
            end_date=args.end_date,
            history_end_date=args.history_end_date,
            position_limit_krw=args.position_limit,
            commission_rate=args.commission_rate,
            warmup=args.warmup,
            years=args.years,
        )
    elif args.mode == "tplus1_entry":
        summary = run_tplus1_entry_gate(
            start_date=args.start_date,
            end_date=args.end_date,
            history_end_date=args.history_end_date,
            position_limit_krw=args.position_limit,
            commission_rate=args.commission_rate,
            warmup=args.warmup,
            years=args.years,
        )
    elif args.mode == "conservative_core_exit":
        summary = run_conservative_core_exit_gate(
            start_date=args.start_date,
            end_date=args.end_date,
            history_end_date=args.history_end_date,
            position_limit_krw=args.position_limit,
            commission_rate=args.commission_rate,
            warmup=args.warmup,
            years=args.years,
        )
    elif args.mode == "capital_reweight_probe":
        summary = run_capital_allocation_reweight_probe()
    elif args.mode == "pit_universe_manifest":
        summary = run_pit_universe_manifest_builder()
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
            f"\n[FAIL] {summary['gate']} mismatch_count={summary.get('mismatch_count', 'n/a')} "
            "— v2 진행 전 원인 확인 필요.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"\n[PASS] {summary['gate']} 통과. "
        f"ref_trades={summary.get('ref_trade_count', summary.get('trade_count', 'n/a'))}, "
        f"candidate_trades={summary.get('candidate_trade_count', summary.get('trade_count', 'n/a'))}."
    )


if __name__ == "__main__":
    main()
