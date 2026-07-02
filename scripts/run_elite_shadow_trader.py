#!/usr/bin/env python3
"""Elite shadow trader daemon.

무엇을 하는 파일인가:
- broker 주문 없이 정예 후보 룰북을 주기적으로 평가한다.
- BUY 신호가 뜨면 가상으로 매수했다고 기록한다.
- 룰북의 손절/목표/트레일링/본전보호/최대보유일/sell_omen 조건이 맞으면 팔았다고 거래내역을 남긴다.
- Exit Policy Lab이 동일 진입을 여러 청산 정책에 복제해 독립 비교한다.
- Elite Shadow 전용 청산 오멘을 추가 overlay로 적용해 수익반납/추세붕괴 전조를 가상 청산한다.
- Peak Exhaustion v3.1로 수익권 포지션의 고점권 소진 신호를 감지해 더 이른 익절을 시도한다.
- output:
  - data/_system/elite_shadow_state.json
  - data/_system/elite_shadow_trades.jsonl
  - data/_system/elite_exit_policy_lab_state.json
  - data/_system/elite_exit_policy_lab_trades.jsonl

주의:
- 실제 Alpaca/KIS 주문은 절대 제출하지 않는다.
- live runner, positions.json, parameters.json을 수정하지 않는다.
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

from engine.live.elite_exit_policy_lab import run_exit_policy_lab_tick
from engine.live.elite_shadow_exit_omen import run_shadow_exit_omen_tick
from engine.live.elite_shadow_mark_to_market import persist_shadow_mark_to_market
from engine.live.elite_shadow_peak_exit_v4 import run_shadow_peak_exit_tick
from engine.live.elite_shadow_trader import run_shadow_tick, shadow_dashboard_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
log = logging.getLogger("run_elite_shadow_trader")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kingmaker elite shadow trader daemon")
    parser.add_argument("--interval", type=int, default=300, help="tick 주기(초)")
    parser.add_argument("--max-candidates", type=int, default=93, help="평가할 정예 후보 최대 수")
    parser.add_argument("--notional", type=float, default=5000.0, help="가상 1회 진입 금액(USD)")
    parser.add_argument("--once", action="store_true", help="한 번만 tick 실행 후 종료")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop = {"value": False}

    def on_signal(signum, frame):
        stop["value"] = True
        log.info("signal %s 수신 — 종료 대기", signum)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    log.warning(
        "Elite shadow trader 시작: interval=%ss max_candidates=%s notional=$%s 실제주문=OFF exit_policy_lab=ON shadow_exit_omen=ON peak_exit_v3_1=ON mark_to_market_1m_high_low=ON",
        args.interval,
        args.max_candidates,
        args.notional,
    )
    while not stop["value"]:
        try:
            # 직전 tick 이후 1분봉 중간 고가/저가를 먼저 state에 저장한다.
            # 그래야 run_shadow_tick/exit_omen/peak_exit이 tick 순간 현재가가 아니라
            # entry 이후 실제 intraday High/Low 기준 MFE/MAE를 보고 청산 판단한다.
            pre_mtm = persist_shadow_mark_to_market(source="shadow_daemon_pre_tick_1m_high_low")
            result = run_shadow_tick(max_candidates=int(args.max_candidates), notional=float(args.notional))
            # run_shadow_tick에서 새로 열린 포지션도 Lab/오멘/Peak 평가 전에 high/low snapshot을 저장한다.
            post_mtm = persist_shadow_mark_to_market(source="shadow_daemon_post_open_1m_high_low")
            # Lab은 active Shadow 청산 overlay 전에 실행한다.
            # 이렇게 해야 새 진입이 v2/v3.1에서 바로 닫히더라도 동일 진입을 정책별로 먼저 복제할 수 있다.
            lab_result = run_exit_policy_lab_tick()
            omen_result = run_shadow_exit_omen_tick()
            peak_result = run_shadow_peak_exit_tick()
            payload = shadow_dashboard_payload(recent_trade_limit=20)
            summary = payload.get("summary") or {}
            closed_total_tick = int(result.get("closed") or 0) + int(omen_result.get("closed") or 0) + int(peak_result.get("closed") or 0)
            log.info(
                "tick ok=%s evaluated=%s opened=%s closed=%s rulebook_closed=%s shadow_omen_closed=%s peak_closed=%s lab_closed=%s lab_open=%s open=%s closed_total=%s pnl=$%.2f unreal=$%.2f elapsed=%s mtm_pre=%s/%s mtm_post=%s/%s omen=%s peak=%s lab=%s",
                result.get("ok"),
                result.get("evaluated"),
                result.get("opened"),
                closed_total_tick,
                result.get("closed"),
                omen_result.get("closed"),
                peak_result.get("closed"),
                lab_result.get("closed"),
                lab_result.get("open_policy_positions"),
                summary.get("open_count"),
                summary.get("closed_count"),
                float(summary.get("total_pnl_usd") or 0.0),
                float(summary.get("open_unrealized_usd") or 0.0),
                result.get("elapsed_sec"),
                pre_mtm.get("updated"),
                pre_mtm.get("evaluated"),
                post_mtm.get("updated"),
                post_mtm.get("evaluated"),
                omen_result.get("close_counts"),
                peak_result.get("close_counts"),
                lab_result.get("close_counts"),
            )
        except Exception as exc:
            log.exception("elite shadow tick 실패: %s", exc)
        if args.once:
            break
        for _ in range(max(1, int(args.interval))):
            if stop["value"]:
                break
            time.sleep(1)
    log.warning("Elite shadow trader 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
