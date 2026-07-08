#!/usr/bin/env python3
"""S2 auto live runner.

Default config is fail-closed. Commands in this script do not place live orders
unless all real-order gates are explicitly enabled and ``tick`` reaches a submit
path. The default validation commands are dry-run only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.s2_auto_trader import S2AutoTrader


def dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="S2 auto live runner")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print config/state status")
    probe = sub.add_parser("probe-capital", help="read available cash and fix session capital")
    probe.add_argument("--force-refresh", action="store_true")
    probe.add_argument("--simulate-balance-failure", action="store_true")
    plan = sub.add_parser("plan-dry-run", help="create a dry-run next-open order plan without submitting orders")
    plan.add_argument("--ignore-switches", action="store_true", help="diagnostic only; ignores master/auto_buy off but still never submits orders")
    plan.add_argument("--force-capital-refresh", action="store_true")
    plan.add_argument("--simulate-balance-failure", action="store_true")
    plan.add_argument("--candidate-id", default="")
    tick = sub.add_parser("tick", help="normal tick; respects all switches")
    tick.add_argument("--ignore-switches-for-dry-run", action="store_true")
    tick.add_argument("--simulate-balance-failure", action="store_true")
    daemon = sub.add_parser("daemon", help="loop tick")
    daemon.add_argument("--interval", type=int, default=60)
    sub.add_parser("exit-selftest", help="verify no-TP exit trigger behavior")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    trader = S2AutoTrader()
    if args.cmd == "status":
        dump(trader.status())
    elif args.cmd == "probe-capital":
        ok, capital, reason, details = trader.ensure_session_capital(simulate_failure=args.simulate_balance_failure, force_refresh=args.force_refresh)
        dump({"ok": ok, "capital_usd": capital, "reason": reason, "details": details, "state_path": str(trader.state_path), "orders_submitted": 0})
    elif args.cmd == "plan-dry-run":
        plan = trader.compute_order_plan(ignore_switches=args.ignore_switches, simulate_balance_failure=args.simulate_balance_failure, force_capital_refresh=args.force_capital_refresh, candidate_id=args.candidate_id)
        dump({"ok": plan.ok, "plan": plan.__dict__, "orders_submitted": 0, "real_order_attempted": False})
    elif args.cmd == "tick":
        dump(trader.tick(ignore_switches_for_dry_run=args.ignore_switches_for_dry_run, simulate_balance_failure=args.simulate_balance_failure))
    elif args.cmd == "daemon":
        while True:
            dump(trader.tick())
            time.sleep(max(1, int(args.interval)))
    elif args.cmd == "exit-selftest":
        dump(trader.exit_trigger_selftest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
