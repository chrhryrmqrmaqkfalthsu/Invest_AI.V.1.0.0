"""
run_live.py - 라이브 트레이딩 봇 엔트리포인트.

구성:
  Scheduler (시계)
    ├─ once         → Runner.startup_check    (가동 직후 1회)
    ├─ market_hours → Runner.tick_market      (장중 60초)
    ├─ interval     → Runner.tick_offmarket   (24h 60분, 헬스체크)
    └─ cron         → Runner.daily_summary    (평일 16:00)

  Runner (뇌)
    ├─ Broker (PaperBroker | KisBroker | AlpacaBroker)
    ├─ SafetyLayer
    ├─ TelegramNotifier
    ├─ MarketClock
    └─ RuleBook

현재 live Runner는 단일 시장 universe만 지원한다. startup과 hot-reload는
동일한 promotion/market 정책을 공유하고, 최종 clock 선택에서도 혼재 시장을
fail-fast한다.
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from engine.live.broker.factory import make_broker
from engine.live.market_clock import select_market_clock, validate_broker_market_compatibility
from engine.live.runner import Runner
from engine.live.safety.layer import SafetyLayer
from engine.live.scheduler import Scheduler
from engine.live.telegram.notifier import TelegramNotifier
from engine.live.telegram.bot import TelegramBot
from engine.live.universe import (
    DEFAULT_LIVE_PROMOTION_ID,
    LiveUniverseConfig,
    load_live_universe,
)
from engine.strategies.demo_rulebook import DemoRuleBook
from engine.strategies.learned_rulebook import LearnedRuleBook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("run_live")


def main():
    parser = argparse.ArgumentParser(description="Kingmaker live trading bot")
    parser.add_argument("--mode", choices=["paper", "real", "vts", "live", "alpaca", "alpaca_paper"], default=None,
                        help="브로커 모드 강제 지정 (기본: .env의 BROKER_MODE/KIS_MODE)")
    parser.add_argument("--dry-run", action="store_true",
                        help="KIS 실모드에서도 주문은 mock으로 처리")
    parser.add_argument("--market", choices=["US", "KRX"], default="US",
                        help="단일 라이브 시장. 기본 US")
    parser.add_argument("--universe", choices=["promoted", "parameters"], default="promoted",
                        help="promoted=정확한 promotion-id 승인 종목만, parameters=시장 내 모든 parameters 종목")
    parser.add_argument("--promotion-id", default=DEFAULT_LIVE_PROMOTION_ID,
                        help="--universe promoted에서 반드시 정확히 일치해야 하는 promotion id")
    parser.add_argument("--market-tick", type=int, default=60,
                        help="장중 tick 주기(초). 기본 60")
    parser.add_argument("--offmarket-tick", type=int, default=3600,
                        help="장외 헬스체크 주기(초). 기본 3600")
    parser.add_argument("--sma-window", type=int, default=20,
                        help="DemoRuleBook SMA 윈도우. 기본 20")
    parser.add_argument("--stop-loss", type=float, default=0.03,
                        help="DemoRuleBook 손절률. 기본 0.03")
    parser.add_argument("--summary-hour", type=int, default=16,
                        help="일일 요약 시각(시). 기본 16 (시장별 timezone 정합은 후속)")
    parser.add_argument("--rulebook", choices=["learned", "demo"], default="learned",
                        help="룰북 선택. learned=학습된 룰북(기본), demo=SMA20 데모")
    parser.add_argument("--summary-minute", type=int, default=0,
                        help="일일 요약 시각(분). 기본 0")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Kingmaker live trading bot 시작")
    logger.info("=" * 60)

    universe_config = LiveUniverseConfig(
        market=args.market,
        universe_mode=args.universe,
        promotion_id=args.promotion_id,
    )
    try:
        universe = load_live_universe(universe_config)
        symbols = list(universe.symbols)
        if not symbols:
            raise RuntimeError(f"필터 통과 종목이 없음: {universe.summary()}")
        # 최종 안전망: helper 필터 후에도 단일-market clock 검증은 유지한다.
        clock = select_market_clock(symbols)
    except Exception as exc:
        logger.error(f"라이브 universe/clock 검증 실패: {exc}")
        sys.exit(1)

    logger.info(f"LiveUniverse: {universe.summary()}")
    logger.info(f"종목 {len(symbols)}개: {symbols}")
    source = getattr(clock, "calendar_source", "built-in")
    logger.info(f"MarketClock: market={clock.name} source={source}")

    try:
        broker = make_broker(force_mode=args.mode, dry_run=args.dry_run)
        validate_broker_market_compatibility(broker, clock)
    except Exception as exc:
        logger.error(f"라이브 시장/브로커 정합성 실패: {exc}")
        sys.exit(2)
    logger.info(f"Broker: mode={broker.mode} dry_run={args.dry_run}")

    notifier = TelegramNotifier()
    safety = SafetyLayer(broker=broker)
    if args.rulebook == "learned":
        rulebook = LearnedRuleBook()
    else:
        rulebook = DemoRuleBook(window=args.sma_window, stop_loss_pct=args.stop_loss)
    logger.info(f"RuleBook: {rulebook.name()}")

    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=clock,
        rulebook=rulebook,
        symbols=symbols,
        order_shares=1,
        universe_config=universe.config,
    )

    try:
        bot = TelegramBot(broker=broker, safety=safety, notifier=notifier)
        runner.attach_bot(bot)
        bot.start_polling(blocking=False)
        logger.info("TelegramBot 폴링 시작")
    except Exception as e:
        logger.error(f"TelegramBot 시작 실패 (계속 진행): {e}")
        bot = None

    scheduler = Scheduler(default_timezone="Asia/Seoul")
    scheduler.add_once_job(func=runner.startup_check, delay_sec=2, job_id="startup_check")
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
        try:
            if bot is not None:
                bot.stop()
                logger.info("TelegramBot 폴링 종료")
        except Exception as e:
            logger.warning(f"TelegramBot 종료 예외: {e}")
        scheduler.shutdown(wait=True)
        logger.info("Scheduler shutdown 완료")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    scheduler.start()
    logger.info(f"Scheduler 가동. 등록된 잡 {len(scheduler.list_jobs())}개")
    for job in scheduler.list_jobs():
        logger.info(f"  - {job}")
    logger.info("Ctrl+C로 종료")

    try:
        while not stop_flag["stop"]:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler(signal.SIGINT, None)


if __name__ == "__main__":
    main()
