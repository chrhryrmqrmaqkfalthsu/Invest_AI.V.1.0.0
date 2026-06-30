#!/usr/bin/env python3
"""Run LR8D stage-1 16-symbol legacy per-ticker live loop.

This runner is intentionally separate from scripts/run_live.py:
- central-control is not used
- next-open scheduled queue is not used
- old per-ticker BUY path is allowed, but only for the exact
  lr8d_stage1_20260609 promoted universe
- SELL/exit, pending reconciliation, safety layer, Telegram control, and
  dashboard hooks stay the same as the normal live runner
- fast-exit tick runs a lightweight exit/manual-sell/pending loop more often
  than the full BUY signal scan so manual exits finalize quickly
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.broker.factory import make_broker
from engine.live.daily_report import (
    send_daily_report_from_runner,
    send_monthly_report_from_runner,
    send_weekly_report_from_runner,
    should_send_monthly_report,
    should_send_weekly_report,
)
from engine.live.exit_policy_guard import validate_startup_exit_policy
from engine.live.market_clock import select_market_clock, validate_broker_market_compatibility
from engine.live.runner import Runner
from engine.live.safety.layer import SafetyLayer
from engine.live.scheduler import Scheduler
from engine.live.telegram.dashboard import install_position_dashboard
from engine.live.telegram.notifier import TelegramNotifier
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.strategies.learned_rulebook import LearnedRuleBook
from scripts.run_live import make_holding_news_tick_market_job, start_telegram_control

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("run_live_lr8d16_legacy")

PROMOTION_ID = "lr8d_stage1_20260609"
EXPECTED_SYMBOLS = (
    "CAKE",
    "CRWD",
    "CW",
    "EME",
    "ETR",
    "HSBC",
    "ITT",
    "KT",
    "LASR",
    "MPC",
    "MPLX",
    "MTB",
    "NBIX",
    "WAB",
    "WELL",
    "WPM",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kingmaker LR8D16 legacy per-ticker runner")
    parser.add_argument("--mode", choices=["paper", "real", "vts", "live", "alpaca", "alpaca_paper"], default="alpaca_paper")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-telegram-bot", action="store_true", help="명시적 테스트/headless 모드")
    parser.add_argument("--market", choices=["US"], default="US")
    parser.add_argument("--promotion-id", default=PROMOTION_ID)
    parser.add_argument("--order-notional", type=float, default=5000.0, help="개별 ticker legacy BUY 1회 주문금액(USD)")
    parser.add_argument("--order-shares", type=float, default=1.0, help="--order-notional<=0일 때만 쓰는 fallback 수량")
    parser.add_argument("--market-tick", type=int, default=60)
    parser.add_argument("--fast-exit-tick", type=int, default=10, help="장중 청산/수동매도/pending 정산 전용 빠른 tick 주기(초). 0 이하이면 비활성")
    parser.add_argument("--offmarket-tick", type=int, default=3600)
    parser.add_argument("--summary-hour", type=int, default=6)
    parser.add_argument("--summary-minute", type=int, default=15)
    parser.add_argument("--weekly-summary-hour", type=int, default=6)
    parser.add_argument("--weekly-summary-minute", type=int, default=30)
    parser.add_argument("--monthly-summary-hour", type=int, default=6)
    parser.add_argument("--monthly-summary-minute", type=int, default=45)
    return parser.parse_args()


def make_fast_exit_tick_job(runner: Runner):
    """Return a lightweight market-hours job for exits only.

    Full tick_market still scans BUY signals every 60s.  This job only handles:
    - pending order polling/finalization
    - dashboard/Telegram manual SELL intents
    - automatic exit checks for already-held positions

    That keeps manual exits and filled-order reconciliation from waiting for the
    next full BUY-scan tick, without increasing 16-symbol entry-scan load.
    """
    state = {"busy": False}

    def fast_exit_tick() -> None:
        if state["busy"]:
            logger.debug("[FAST-EXIT] previous tick still running — skip")
            return
        state["busy"] = True
        try:
            runner._poll_pending_orders(context="fast_exit.pre_manual")
            runner._process_manual_sell_intents()
            runner._poll_pending_orders(context="fast_exit.post_manual")

            pending_mgr = getattr(runner, "pending_order_manager", None)
            if pending_mgr is not None and pending_mgr.all():
                logger.debug("[FAST-EXIT] pending 주문 존재 → 자동청산 체크 보류")
                return

            exited = runner.position_manager.check_exits(
                runner.broker,
                runner.notifier,
                pending_manager=pending_mgr,
            )
            if exited:
                for record in exited:
                    runner._record_realized_pnl_from_trade(record)
                logger.info("[FAST-EXIT] 자동 청산 %d건 완료", len(exited))
        except Exception as exc:
            runner._handle_error("fast_exit_tick", exc)
        finally:
            state["busy"] = False

    fast_exit_tick.__name__ = "fast_exit_tick"
    return fast_exit_tick


def main() -> int:
    args = parse_args()
    logger.info("=" * 70)
    logger.info("Kingmaker LR8D16 legacy runner 시작 — central-control OFF / per-ticker BUY ON")
    logger.info("=" * 70)

    if args.promotion_id != PROMOTION_ID:
        logger.error("LR8D16 runner는 promotion_id=%s만 허용합니다: requested=%s", PROMOTION_ID, args.promotion_id)
        return 2

    universe_config = LiveUniverseConfig(market=args.market, universe_mode="promoted", promotion_id=args.promotion_id)
    try:
        universe = load_live_universe(universe_config)
        symbols = tuple(universe.symbols)
        if symbols != EXPECTED_SYMBOLS:
            logger.error("LR8D16 universe 불일치: expected=%s actual=%s", EXPECTED_SYMBOLS, symbols)
            return 3
        clock = select_market_clock(symbols)
    except Exception as exc:
        logger.error("LR8D16 universe/clock 검증 실패: %s", exc)
        return 4

    logger.info("LiveUniverse: %s", universe.summary())
    logger.info("거래 허용 종목 %d개: %s", len(symbols), symbols)
    logger.warning("[CENTRAL-CONTROL] OFF: central selector/next-open queue 미사용")
    logger.warning("[LEGACY-BUY] ON: 이 runner에서는 LR8D16 promoted universe에 한해 개별 ticker BUY 허용")

    try:
        broker = make_broker(force_mode=args.mode, dry_run=args.dry_run)
        validate_broker_market_compatibility(broker, clock)
        validate_startup_exit_policy(broker)
    except Exception as exc:
        logger.error("라이브 시장/브로커 정합성 실패: %s", exc)
        return 5
    logger.info("Broker: mode=%s dry_run=%s", broker.mode, args.dry_run)

    notifier = TelegramNotifier()
    safety = SafetyLayer(broker=broker)
    rulebook = LearnedRuleBook()
    logger.info("RuleBook: %s", rulebook.name())

    order_notional = float(args.order_notional or 0.0)
    order_shares = float(args.order_shares or 1.0)
    if order_notional > 0.0:
        logger.info("Order sizing: legacy per-ticker notional=%g USD", order_notional)
    else:
        logger.warning("Order sizing: shares fallback=%g (--order-notional<=0)", order_shares)

    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=clock,
        rulebook=rulebook,
        symbols=list(symbols),
        order_shares=order_shares,
        order_notional=order_notional if order_notional > 0.0 else None,
        universe_config=universe.config,
    )
    install_position_dashboard(runner)

    try:
        bot = start_telegram_control(
            no_telegram_bot=bool(args.no_telegram_bot),
            broker=broker,
            safety=safety,
            notifier=notifier,
            runner=runner,
        )
    except Exception as exc:
        logger.error("TelegramBot 시작 실패 — Scheduler 시작 전 종료: %s", exc)
        return 6

    scheduler = Scheduler(default_timezone="Asia/Seoul")
    scheduler.add_once_job(func=runner.startup_check, delay_sec=2, job_id="startup_check")
    fast_exit_interval = int(args.fast_exit_tick or 0)
    if fast_exit_interval > 0:
        scheduler.add_market_hours_job(
            func=make_fast_exit_tick_job(runner),
            interval_sec=fast_exit_interval,
            market=clock,
            job_id="fast_exit_tick",
        )
        logger.warning("[FAST-EXIT] ON: 청산/수동매도/pending 정산 전용 tick every %ss", fast_exit_interval)
    else:
        logger.warning("[FAST-EXIT] OFF")
    scheduler.add_market_hours_job(
        func=make_holding_news_tick_market_job(runner),
        interval_sec=int(args.market_tick),
        market=clock,
        job_id="tick_market",
    )
    scheduler.add_interval_job(
        func=runner.tick_offmarket,
        interval_sec=int(args.offmarket_tick),
        job_id="tick_offmarket",
        name="tick_offmarket",
    )

    def daily_report_job():
        return send_daily_report_from_runner(runner)

    def weekly_report_job():
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        if should_send_weekly_report(clock, now=now):
            return send_weekly_report_from_runner(runner, now=now)
        return False

    def monthly_report_job():
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        if should_send_monthly_report(clock, now=now):
            return send_monthly_report_from_runner(runner, now=now)
        return False

    daily_report_job.__name__ = "daily_report"
    weekly_report_job.__name__ = "weekly_report"
    monthly_report_job.__name__ = "monthly_report"

    scheduler.add_cron_job(
        func=daily_report_job,
        hour=int(args.summary_hour),
        minute=int(args.summary_minute),
        market=clock,
        weekdays_only=False,
        job_id="daily_summary",
    )
    scheduler.add_cron_job(
        func=weekly_report_job,
        hour=int(args.weekly_summary_hour),
        minute=int(args.weekly_summary_minute),
        market=clock,
        weekdays_only=False,
        job_id="weekly_summary",
    )
    scheduler.add_cron_job(
        func=monthly_report_job,
        hour=int(args.monthly_summary_hour),
        minute=int(args.monthly_summary_minute),
        market=clock,
        weekdays_only=False,
        job_id="monthly_summary",
    )

    stop_flag = {"stop": False}

    def shutdown_handler(signum, frame):
        if stop_flag["stop"]:
            return
        stop_flag["stop"] = True
        logger.info("signal %s 수신 — graceful shutdown...", signum)
        try:
            if bot is not None:
                bot.stop()
                logger.info("TelegramBot 폴링 종료")
        except Exception as exc:
            logger.warning("TelegramBot 종료 예외: %s", exc)
        try:
            scheduler.shutdown(wait=True)
            logger.info("Scheduler shutdown 완료")
        except Exception as exc:
            logger.warning("Scheduler shutdown 예외: %s", exc)
        try:
            notifier.send("🛑 Kingmaker LR8D16 legacy runner 종료됨")
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
    logger.info("Scheduler 가동. 등록된 잡 %d개", len(scheduler.list_jobs()))
    for job in scheduler.list_jobs():
        logger.info("  - %s", job)
    logger.info("Ctrl+C로 종료")

    try:
        while not stop_flag["stop"]:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_handler(signal.SIGINT, None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
