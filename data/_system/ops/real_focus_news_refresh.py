#!/usr/bin/env python3
"""Real-focus individual ticker news refresh runner.

Refreshes individual news only for real-trading candidate slots and Alpaca live
holdings. Does not touch paper candidate selection.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.real_focus_news_refresh import refresh_real_focus_news, utc_now_iso


def dump(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), flush=True)


def daemon(interval: int, budget: int, dry_run: bool, cache_max_minutes: int) -> int:
    stop = {"value": False}

    def on_signal(signum, frame):
        stop["value"] = True
        print(f"signal {signum} received; stopping", file=sys.stderr, flush=True)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    while not stop["value"]:
        started = time.time()
        try:
            result = refresh_real_focus_news(budget=budget, dry_run=dry_run, cache_max_minutes=cache_max_minutes)
            print(f"{utc_now_iso()} ok={result.get('ok')} dry_run={result.get('dry_run')} selected={result.get('selected_tickers')} fetched={result.get('fetched_count')} errors={len(result.get('errors') or {})}", flush=True)
        except Exception as exc:
            print(f"{utc_now_iso()} ERROR {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        elapsed = time.time() - started
        sleep_for = max(1, int(interval - elapsed))
        for _ in range(sleep_for):
            if stop["value"]:
                break
            time.sleep(1)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Refresh real candidate/holding individual news")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("--budget", type=int, default=12)
    r.add_argument("--cache-max-minutes", type=int, default=180)
    r.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("daemon")
    d.add_argument("--interval", type=int, default=1800)
    d.add_argument("--budget", type=int, default=12)
    d.add_argument("--cache-max-minutes", type=int, default=180)
    d.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.cmd == "refresh":
        dump(refresh_real_focus_news(budget=args.budget, dry_run=args.dry_run, cache_max_minutes=args.cache_max_minutes))
    elif args.cmd == "daemon":
        return daemon(max(60, int(args.interval)), int(args.budget), bool(args.dry_run), int(args.cache_max_minutes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
