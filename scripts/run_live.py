"""
run_live.py - 라이브 트레이딩 봇 엔트리포인트.

구성:
  Scheduler (시계)
    ├─ once         → Runner.startup_check    (가동 직후 1회)
    ├─ market_hours → Runner.tick_market      (장중 60초)
    ├─ interval     → Runner.tick_offmarket   (24h 60분, 헬스체크)
    └─ cron         → Runner.daily_summary    (평일 16:00)

  Runner (뇌)
    ├─ Broker (PaperBroker | KisBroker)
    ├─ SafetyLayer
    ├─ TelegramNotifier
    ├─ MarketClock
    └─ RuleBook (DemoRuleBook)

사용법:
  PYTHONPATH=. python scripts/run_live.py                  # .env의 KIS_MODE 사용
  PYTHONPATH=. python scripts/run_live.py --mode paper     # 강제 paper
  PYTHONPATH=. python scripts/run_live.py --dry-run        # KIS 실모드여도 주문 mock
  Ctrl+C로 graceful 종료.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from engine.live.broker.factory import make_broker
from engine.live.market_clock import KrxMarketClock
from engine.live.runner import Runner
from engine.live.safety.layer import SafetyLayer
from engine.live.scheduler import Scheduler
from engine.live.telegram.notifier import TelegramNotifier
from engine.strategies.demo_rulebook import DemoRuleBook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("run_live")


# ----------------- 종목 로드 -----------------
def load_symbols(symbols_dir: Path = Path("data/symbols")) -> list[str]:
    """
    data/symbols/ 하위 디렉토리 이름을 종목코드로 사용.
    SafetyLayer 화이트리스트와 동일한 소스.
    """
    if not symbols_dir.exists():
        logger.warning(f"종목 디렉토리 없음: {symbols_dir}")
        return []
    syms = sorted([d.name for d in symbols_dir.iterdir() if d.is_dir()])
    return syms


# ----------------- 메인 -----------------
def main():
    parser = argparse.ArgumentParser(description="Kingmaker live trading bot")
    parser.add_argument("--mode", choices=["paper", "real", "vts", "live"], default=None,
                        help="브로커 모드 강제 지정 (기본: .env의 KIS_MODE)")
    parser.add_argument("--dry-run", action="store_true",
                        help="KIS 실모드에서도 주문은 mock으로 처리")
    parser.add_argument("--market-tick", type=int, default=60,
                        help="장중 tick 주기(초). 기본 60")
    parser.add_argument("--offmarket-tick", type=int, default=3600,
                        help="장외 헬스체크 주기(초). 기본 3600")
    parser.add_argument("--sma-window", type=int, default=20,
                        help="DemoRuleBook SMA 윈도우. 기본 20")
    parser.add_argument("--stop-loss", type=float, default=0.03,
                        help="DemoRuleBook 손절률. 기본 0.03")
    parser.add_argument("--summary-hour", type=int, default=16,
                        help="일일 요약 시각(시). 기본 16")
    parser.add_argument("--summary-minute", type=int, default=0,
                        help="일일 요약 시각(분). 기본 0")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Kingmaker live trading bot 시작")
    logger.info("=" * 60)

    # 1) 브로커
    broker = make_broker(force_mode=args.mode, dry_run=args.dry_run)
    logger.info(f"Broker: mode={broker.mode} dry_run={args.dry_run}")

    # 2) 종목 로드
    symbols = load_symbols()
    if not symbols:
        logger.error("종목이 비어있음. data/symbols/ 아래 종목 디렉토리를 만들어주세요.")
        sys.exit(1)
    logger.info(f"종목 {len(symbols)}개: {symbols}")

    # 3) 의존성
    notifier = TelegramNotifier()
    safety = SafetyLayer(broker=broker)
    clock = KrxMarketClock()
    rulebook = DemoRuleBook(window=args.sma_window, stop_loss_pct=args.stop_loss)

    # 4) Runner
    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=clock,
        rulebook=rulebook,
        symbols=symbols,
        order_shares=1,
    )

    # 5) Scheduler에 잡 등록
    scheduler = Scheduler(default_timezone="Asia/Seoul")

    scheduler.add_once_job(
        func=runner.startup_check,
        delay_sec=2,
        job_id="startup_check",
    )
    scheduler.add_market_hours_job(
        func=runner.tick_market,
        interval_sec=args.market_tick,
        market=clock,
        job_id="tick_market",
    )
    scheduler.add_interval_job(
        func=runner.tick_offmarket,
        interval_sec=args.offmarket_tick,
        name="tick_offmarket",
    )
    scheduler.add_cron_job(
        func=runner.daily_summary,
        hour=args.summary_hour,
        minute=args.summary_minute,
        market=clock,
        weekdays_only=True,
        job_id="daily_summary",
    )

    # 6) graceful shutdown
    stop_flag = {"stop": False}

    def shutdown_handler(signum, frame):
        if stop_flag["stop"]:
            return
        stop_flag["stop"] = True
        logger.info(f"signal {signum} 수신 — graceful shutdown...")
        try:
            notifier.send("🛑 Kingmaker 종료 중...")
        except Exception:
            pass
        scheduler.shutdown(wait=True)
        logger.info("Scheduler shutdown 완료")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # 7) 시작
    scheduler.start()
    logger.info(f"Scheduler 가동. 등록된 잡 {len(scheduler.list_jobs())}개")
    for j in scheduler.list_jobs():
        logger.info(f"  - {j}")
    logger.info("Ctrl+C로 종료")

    # 메인 스레드는 잠자기 (Scheduler는 백그라운드)
    try:
        while not stop_flag["stop"]:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler(signal.SIGINT, None)


if __name__ == "__main__":
    main()
