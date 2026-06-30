#!/usr/bin/env python3
"""Elite strategy simulation daemon.

무엇을 하는 파일인가:
- final_gate / pullback_only 두 개의 매수 게이트 전략을 broker 주문 없이 모의 운용한다.
- 실제 Alpaca/KIS 주문, live positions.json, parameters.json은 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.elite_strategy_sim import run_strategy_sim_tick, strategy_sim_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
log = logging.getLogger("run_elite_strategy_sim")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kingmaker elite gate strategy simulator")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--max-candidates", type=int, default=93)
    parser.add_argument("--notional", type=float, default=5000.0)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop = {"value": False}

    def on_signal(signum, frame):
        stop["value"] = True
        log.info("signal %s 수신 — 종료 대기", signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    log.warning("Elite strategy sim 시작: interval=%ss max_candidates=%s notional=$%s 실제주문=OFF", args.interval, args.max_candidates, args.notional)
    while not stop["value"]:
        try:
            result = run_strategy_sim_tick(max_candidates=args.max_candidates, notional=args.notional)
            payload = strategy_sim_payload(recent_trade_limit=20)
            chunks = []
            for name, sim in (payload.get("strategies") or {}).items():
                s = sim.get("summary") or {}
                chunks.append(f"{name}: open={s.get('open_count')} closed={s.get('closed_count')} pnl=${float(s.get('total_pnl_usd') or 0):.2f} roi={float(s.get('total_roi_pct') or 0):+.2f}%")
            log.info("tick ok=%s elapsed=%s | %s", result.get("ok"), result.get("elapsed_sec"), " | ".join(chunks))
        except Exception as exc:
            log.exception("elite strategy sim tick 실패: %s", exc)
        if args.once:
            break
        for _ in range(max(1, args.interval)):
            if stop["value"]:
                break
            time.sleep(1)
    log.warning("Elite strategy sim 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
