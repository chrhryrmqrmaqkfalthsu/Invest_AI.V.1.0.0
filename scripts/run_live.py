"""
run_live.py - 라이브 트레이딩 봇 엔트리포인트.

구성:
  Scheduler (시계)
    ├─ once         → Runner.startup_check
    ├─ market_hours → Runner.tick_market
    ├─ interval     → Runner.tick_offmarket
    └─ cron         → Runner.daily_summary
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

from engine.live.broker.factory import make_broker
from engine.live.market_clock import select_market_clock, validate_broker_market_compatibility
from engine.live.runner import Runner
from engine.live.safety.layer import SafetyLayer
from engine.live.scheduler import Scheduler
from engine.live.telegram.notifier import TelegramNotifier
from engine.live.telegram.locked_bot import TelegramBot, is_process_alive
from engine.live.universe import DEFAULT_LIVE_PROMOTION_ID, LiveUniverseConfig, load_live_universe
from engine.strategies.demo_rulebook import DemoRuleBook
from engine.strategies.learned_rulebook import LearnedRuleBook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("run_live")

RUN_BOT_PID_PATH = Path("data/_system/run_bot.pid")


def assert_no_legacy_run_bot(pid_path: Path | str = RUN_BOT_PID_PATH) -> None:
    path = Path(pid_path)
    if not path.exists():
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except Exception as exc:
        raise RuntimeError(f"legacy run_bot PID file invalid; manual inspection required: {path}") from exc
    if pid == os.getpid():
        return
    if is_process_alive(pid):
        raise RuntimeError(
            f"legacy run_bot.py is still polling Telegram without the new lock: pid={pid}. "
            "Gracefully stop it before starting run_live.py."
        )
    logger.warning("stale legacy run_bot PID file ignored: pid=%s path=%s", pid, path)


def start_telegram_control(
    *,
    no_telegram_bot: bool,
    broker,
    safety,
    notifier,
    runner,
    bot_factory=TelegramBot,
    legacy_run_bot_pid_path: Path | str = RUN_BOT_PID_PATH,
):
    if no_telegram_bot:
        logger.warning("Telegram command polling disabled by explicit --no-telegram-bot (test/headless only)")
        return None

    assert_no_legacy_run_bot(legacy_run_bot_pid_path)
    bot = bot_factory(broker=broker, safety=safety, notifier=notifier, polling_owner="run_live")
    try:
        runner.attach_bot(bot)
        bot.start_polling(blocking=False)
    except Exception:
        try:
            bot.stop()
        except Exception:
            pass
        raise
    logger.info("TelegramBot 단독 폴링 시작")
    return bot


def main():
    parser = argparse.ArgumentParser(description="Kingmaker live trading bot")
    parser.add_argument("--mode", choices=["paper", "real", "vts", "live", "alpaca", "alpaca_paper"], default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-telegram-bot", action="store_true", help="명시적 테스트/headless 모드. 실제 Paper/Live E2E에서는 사용 금지")
    parser.add_argument("--market", choices=["US", "KRX"], default="US")
    parser.add_argument("--universe", choices=["promoted", "parameters"], default="promoted")
    parser.add_argument("--promotion-id", default=DEFAULT_LIVE_PROMOTION_ID)
    parser.add_argument("--market-tick", type=int, default=60)
    parser.add_argument("--offmarket-tick", type=int, default=3600)
    parser.add_argument("--sma-window", type=int, default=20)
    parser.add_argument("--stop-loss", type=float, default=0.03)
    parser.add_argument("--summary-hour", type=int, default=16)
    parser.add_argument("--rulebook", choices=["learned", "demo"], default="learned")
    parser.add_argument("--summary-minute", type=int, default=0)
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Kingmaker live trading bot 시작")
    logger.info("=" * 60)

    universe_config = LiveUniverseConfig(market=args.market, universe_mode=args.universe, promotion_id=args.promotion_id)
    try:
        universe = load_live_universe(universe_config)
        symbols = list(universe.symbols)
        if not symbols:
            raise RuntimeError(f"필터 통과 종목이 없음: {universe.summary()}")
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
    rulebook = LearnedRuleBook() if args.rulebook == "learned" else DemoRuleBook(window=args.sma_window, stop_loss_pct=args.stop_loss)
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
        bot = start_telegram_control(
            no_telegram_bot=args.no_telegram_bot,
            broker=broker,
            safety=safety,
            notifier=notifier,
            runner=runner,
        )
    except Exception as e:
        logger.error(f"TelegramBot 시작 실패 — Scheduler 시작 전 종료: {e}")
        sys.exit(3)

    scheduler = Scheduler(default_timezone="Asia/Seoul")
    scheduler.add_once_job(func=runner.startup_check, delay_sec=2, job_id="startup_check")
    scheduler.add_market_hours_job(func=runner.tick_market, interval_sec=args.market_tick, market=clock, job_id="tick_market")
    scheduler.add_interval_job(func=runner.tick_offmarket, interval_sec=args.offmarket_tick, job_id="tick_offmarket", name="tick_offmarket")
    scheduler.add_cron_job(func=runner.daily_summary, hour=args.summary_hour, minute=args.summary_minute, market=clock, weekdays_only=True, job_id="daily_summary")

    stop_flag = {"stop": False}

    def shutdown_handler(signum, frame):
        if stop_flag["stop"]:
            return
        stop_flag["stop"] = True
        logger.info(f"signal {signum} 수신 — graceful shutdown...")
        try:
            if bot is not None:
                bot.stop()
                logger.info("TelegramBot 폴링 종료")
        except Exception as e:
            logger.warning(f"TelegramBot 종료 예외: {e}")
        try:
            scheduler.shutdown(wait=True)
            logger.info("Scheduler shutdown 완료")
        except Exception as e:
            logger.warning(f"Scheduler shutdown 예외: {e}")
        try:
            notifier.send("🛑 Kingmaker 종료됨")
        except Exception:
            pass
        for handler in logging.getLogger().handlers:
            try:
                handler.flush()
            except Exception:
                pass
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
