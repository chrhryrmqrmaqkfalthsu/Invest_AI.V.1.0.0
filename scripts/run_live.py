"""
run_live.py - 라이브 트레이딩 봇 엔트리포인트.

구성:
  Scheduler (시계)
    ├─ once         → Runner.startup_check
    ├─ market_hours → Runner.tick_market → holding-news refresh
    ├─ interval     → Runner.tick_offmarket
    ├─ interval     → next_open pre-open selection / open execution checks
    ├─ cron         → 장마감 일일 보고
    ├─ cron         → 주간 보고
    └─ cron         → 월간 보고
"""
from __future__ import annotations

import argparse
import logging
import os
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
from engine.live.central_control import (
    DEFAULT_STAGE3_LIVE_POOL_PATH,
    LiveCentralControlConfig,
    LiveCentralController,
    order_notional_safety_buffer_from_policy,
)
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
from engine.live.scheduled_open_buy_queue import (
    DEFAULT_SCHEDULED_OPEN_BUY_QUEUE_PATH,
    NextOpenBuyCoordinator,
)
from engine.live.scheduler import Scheduler
from engine.live.telegram.dashboard import install_position_dashboard
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
# 구 개별 ticker BUY 경로는 install_legacy_buy_guard()에서 fail-safe 차단된다.
# 이 기본값은 central-control이 decision.notional로 덮어쓰기 전 runner sizing 초기값으로만 남긴다.
DEFAULT_ORDER_NOTIONAL_USD = 30.0
CENTRAL_CONTROL_REASON_PREFIX = "central_control "
LEGACY_BUY_DISABLED_CODE = "LEGACY_BUY_DISABLED"


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
    bot = bot_factory(broker=broker, safety=safety, notifier=notifier, polling_owner="run_live", runner=runner)
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except Exception:
        return default


def make_holding_news_tick_market_job(runner: Runner):
    """Wrap market tick with hourly holding-news refresh.

    The wrapper does not change sell/exit authority. It runs the existing market
    tick first, then refreshes the cache read by lookup_live_sell_omen_score() so
    API latency cannot delay the current tick's existing exit checks.
    """
    state = {"last_refresh_at": None}

    def _refresh_if_due() -> None:
        if not _env_bool("HOLDING_NEWS_QUEUE_ENABLED", True):
            return
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        interval_min = max(1, _env_int("HOLDING_NEWS_REFRESH_MIN", 60))
        last = state.get("last_refresh_at")
        due = last is None or (now - last).total_seconds() >= interval_min * 60
        if not due:
            return
        try:
            from engine.live.holding_news_queue import (
                DEFAULT_INDIVIDUAL_CALL_BUDGET,
                recent_no_ticker_covered_tickers,
                refresh_holding_news_for_positions,
            )

            budget = min(max(0, _env_int("HOLDING_NEWS_INDIVIDUAL_BUDGET", DEFAULT_INDIVIDUAL_CALL_BUDGET)), DEFAULT_INDIVIDUAL_CALL_BUDGET)
            market_covered = recent_no_ticker_covered_tickers(
                max_age_minutes=max(1, _env_int("HOLDING_NEWS_MARKET_COVERED_MAX_MIN", 90)),
                now=now,
            )
            result = refresh_holding_news_for_positions(
                runner.position_manager.all(),
                broker=runner.broker,
                notifier=runner.notifier,
                asof=now,
                budget=budget,
                dry_run=_env_bool("HOLDING_NEWS_DRY_RUN", False),
                exclude_tickers=market_covered,
            )
            state["last_refresh_at"] = now
            logger.info(
                "[HOLDING-NEWS] refresh result held=%s selected=%s budget=%s market_covered=%s dry_run=%s errors=%s",
                result.get("held_count"),
                result.get("selected_count"),
                result.get("budget"),
                result.get("market_covered_count"),
                result.get("dry_run"),
                len(result.get("errors") or {}),
            )
        except Exception as exc:
            logger.warning("[HOLDING-NEWS] refresh failed; tick_market already completed: %s", exc)
            state["last_refresh_at"] = now

    def tick_market_with_holding_news():
        result = runner.tick_market()
        _refresh_if_due()
        return result

    tick_market_with_holding_news.__name__ = "tick_market_with_holding_news"
    return tick_market_with_holding_news


def _central_control_enabled(value: str) -> bool:
    return str(value or "off").strip().lower() == "on"


def _is_central_control_buy_reason(reason: str) -> bool:
    return str(reason or "").strip().startswith(CENTRAL_CONTROL_REASON_PREFIX)


def install_legacy_buy_guard(runner: Runner) -> None:
    """Fail-safe guard that disables the old per-ticker signal BUY path.

    Runner._try_order is shared by the legacy signal path and central-control.
    The live central adapter tags its BUY reason with ``central_control`` and
    temporarily overwrites runner.order_notional with decision.notional. This
    guard allows that explicit central path, but blocks every other BUY before
    preflight/safety/broker submission. SELL, pending reconciliation, and exit
    handling are not affected.
    """
    if getattr(runner, "_legacy_buy_guard_installed", False):
        return
    if not hasattr(runner, "_try_order"):
        logger.warning("[%s] runner has no _try_order; legacy BUY guard 설치 스킵(test double/headless)", LEGACY_BUY_DISABLED_CODE)
        return
    original_try_order = runner._try_order

    def guarded_try_order(side: str, ticker: str, price: float, reason: str, signal_result=None, rulebook_override=None) -> None:
        side_u = str(side or "").upper()
        if side_u == "BUY" and not _is_central_control_buy_reason(reason):
            try:
                runner.stats.orders_attempted += 1
                runner.stats.orders_blocked += 1
            except Exception:
                pass
            logger.warning(
                "[%s] %s BUY 차단: 구 개별 ticker 신호 BUY 경로 폐기. central-control BUY만 허용. reason=%s",
                LEGACY_BUY_DISABLED_CODE,
                ticker,
                reason,
            )
            try:
                runner.notifier.send_safety_block(
                    LEGACY_BUY_DISABLED_CODE,
                    f"{ticker} BUY: 구 신호 BUY 경로는 비활성화됨. central-control BUY만 허용됩니다.",
                )
            except Exception as exc:
                logger.warning("[%s] 차단 알림 실패: %s", LEGACY_BUY_DISABLED_CODE, exc)
            return None
        try:
            return original_try_order(side, ticker, price, reason, signal_result=signal_result, rulebook_override=rulebook_override)
        except TypeError as exc:
            if "rulebook_override" not in str(exc):
                raise
            return original_try_order(side, ticker, price, reason, signal_result=signal_result)

    runner._try_order = guarded_try_order
    runner._legacy_buy_guard_installed = True
    logger.warning("[%s] 구 개별 ticker BUY 경로 fail-safe 차단 설치 완료; SELL/청산/central BUY는 유지", LEGACY_BUY_DISABLED_CODE)


def main():
    parser = argparse.ArgumentParser(description="Kingmaker live trading bot")
    parser.add_argument("--mode", choices=["paper", "real", "vts", "live", "alpaca", "alpaca_paper"], default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-telegram-bot", action="store_true", help="명시적 테스트/headless 모드. 실제 Paper/Live E2E에서는 사용 금지")
    parser.add_argument("--market", choices=["US", "KRX"], default="US")
    parser.add_argument("--universe", choices=["promoted", "parameters"], default="promoted")
    parser.add_argument("--promotion-id", default=DEFAULT_LIVE_PROMOTION_ID)
    parser.add_argument("--central-control", choices=["on", "off"], default="off", help="신규 BUY만 중앙통제기 선정/배분으로 전환")
    parser.add_argument("--buy-timing-mode", choices=["next_open"], default="next_open", help="BUY timing: D-1 종가 선별 후 D일 open queue 집행만 허용")
    parser.add_argument("--preopen-select-minutes-before-open", type=int, default=10)
    parser.add_argument("--open-buy-delay-sec", type=int, default=5)
    parser.add_argument("--scheduled-buy-queue-path", default=str(DEFAULT_SCHEDULED_OPEN_BUY_QUEUE_PATH))
    parser.add_argument("--buy-mode", choices=["auto", "semi_auto"], default="auto", help="legacy/intraday central BUY mode. next_open에서는 즉시 BUY에 사용하지 않음")
    parser.add_argument("--central-selection-metric", choices=["confidence", "turnover_score"], default="confidence")
    parser.add_argument("--central-confidence-mode", choices=["raw", "adjusted"], default="adjusted", help="central confidence 산식: raw=기존 PF 평균, adjusted=PF cap+min trades neutral guard")
    parser.add_argument("--central-pf-cap", type=float, default=10.0, help="adjusted confidence의 구간별 profit_factor 상한")
    parser.add_argument("--central-min-trades", type=int, default=15, help="adjusted confidence에서 이 값 미만 trade_count 구간은 PF=1.0으로 중립 대체")
    parser.add_argument("--central-max-positions", type=int, default=8)
    parser.add_argument("--central-position-sizing", choices=["score_weighted", "equal"], default="score_weighted")
    parser.add_argument("--central-pool-limit", type=int, default=533)
    parser.add_argument("--central-stage3-mix", choices=["on", "off"], default="off", help="Stage3 filtered live-pool을 기존 central entity pool에 추가")
    parser.add_argument("--central-stage3-pool-path", default=str(DEFAULT_STAGE3_LIVE_POOL_PATH), help="Stage3 filtered live-pool JSONL 경로")
    parser.add_argument("--central-stage3-pool-limit", type=int, default=0, help="Stage3 추가 entity 개수 상한(0=무제한)")
    parser.add_argument("--central-strength-cap", type=float, default=4.0, help="Stage3 mix ON일 때 Stage2 포함 일반 후보 strength 상한(0=비활성)")
    parser.add_argument("--central-stage3-strength-cap", type=float, default=3.0, help="Stage3 mix ON일 때 Stage3 후보 strength 추가 상한(0=비활성)")
    parser.add_argument("--central-stage3-min-confidence", type=float, default=0.0, help="Stage3 mix ON일 때 Stage3 후보 최소 confidence. confidence <= 값은 제외")
    parser.add_argument("--market-tick", type=int, default=60)
    parser.add_argument("--offmarket-tick", type=int, default=3600)
    parser.add_argument("--sma-window", type=int, default=20)
    parser.add_argument("--stop-loss", type=float, default=0.03)
    parser.add_argument("--order-notional", type=float, default=DEFAULT_ORDER_NOTIONAL_USD, help="central-control 주문금액 초기값. 구 개별 BUY는 LEGACY_BUY_DISABLED로 차단되며 central은 decision.notional로 덮어씀")
    parser.add_argument("--order-shares", type=float, default=1.0, help="--order-notional이 0 이하일 때만 쓰는 fallback 수량")
    parser.add_argument("--summary-hour", type=int, default=6)
    parser.add_argument("--summary-minute", type=int, default=15)
    parser.add_argument("--weekly-summary-hour", type=int, default=6)
    parser.add_argument("--weekly-summary-minute", type=int, default=30)
    parser.add_argument("--monthly-summary-hour", type=int, default=6)
    parser.add_argument("--monthly-summary-minute", type=int, default=45)
    parser.add_argument("--rulebook", choices=["learned", "demo"], default="learned")
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
        validate_startup_exit_policy(broker)
    except Exception as exc:
        logger.error(f"라이브 시장/브로커 정합성 실패: {exc}")
        sys.exit(2)
    logger.info(f"Broker: mode={broker.mode} dry_run={args.dry_run}")

    notifier = TelegramNotifier()
    safety = SafetyLayer(broker=broker)
    rulebook = LearnedRuleBook() if args.rulebook == "learned" else DemoRuleBook(window=args.sma_window, stop_loss_pct=args.stop_loss)
    logger.info(f"RuleBook: {rulebook.name()}")

    order_notional = float(args.order_notional or 0.0)
    order_shares = float(args.order_shares or 1.0)
    if order_notional > 0:
        logger.info(f"Order sizing: notional={order_notional:g} USD (legacy BUY disabled; central-control overrides per decision)")
    else:
        logger.warning(f"Order sizing: shares fallback={order_shares:g} (--order-notional<=0; legacy BUY disabled)")

    runner = Runner(
        broker=broker,
        safety=safety,
        notifier=notifier,
        clock=clock,
        rulebook=rulebook,
        symbols=symbols,
        order_shares=order_shares,
        order_notional=order_notional if order_notional > 0 else None,
        universe_config=universe.config,
    )
    install_legacy_buy_guard(runner)
    next_open_coordinator = None
    if _central_control_enabled(args.central_control):
        broker_mode = str(getattr(broker, "mode", "") or "").lower()
        if broker_mode not in {"paper", "alpaca_paper"}:
            logger.error("central-control은 alpaca_paper/paper에서만 허용: broker.mode=%s", broker_mode)
            sys.exit(4)
        sizing_buffer = order_notional_safety_buffer_from_policy(getattr(safety, "policy", {}) or {})
        central_config = LiveCentralControlConfig(
            enabled=True,
            selection_metric=args.central_selection_metric,
            max_positions=int(args.central_max_positions),
            position_sizing=args.central_position_sizing,
            pool_limit=int(args.central_pool_limit),
            confidence_mode=args.central_confidence_mode,
            pf_cap=float(args.central_pf_cap),
            min_trades=int(args.central_min_trades),
            buy_mode=args.buy_mode,
            order_notional_safety_buffer=sizing_buffer,
            stage3_mix_enabled=str(args.central_stage3_mix).lower() == "on",
            stage3_live_pool_path=Path(args.central_stage3_pool_path),
            stage3_pool_limit=int(args.central_stage3_pool_limit),
            central_strength_cap=float(args.central_strength_cap),
            central_stage3_strength_cap=float(args.central_stage3_strength_cap),
            central_stage3_min_confidence=float(args.central_stage3_min_confidence),
        )
        central_controller = LiveCentralController(runner, central_config)
        if args.buy_timing_mode == "next_open":
            next_open_coordinator = NextOpenBuyCoordinator(
                controller=central_controller,
                market_clock=clock,
                queue_path=Path(args.scheduled_buy_queue_path),
                preopen_select_minutes_before_open=int(args.preopen_select_minutes_before_open),
                open_buy_delay_sec=int(args.open_buy_delay_sec),
            )
            logger.warning(
                "[NEXT-OPEN] ON: regular-hours central BUY disabled; preopen D-1 close selection → D open queue execution path=%s preopen_min=%s open_delay_sec=%s",
                args.scheduled_buy_queue_path,
                args.preopen_select_minutes_before_open,
                args.open_buy_delay_sec,
            )
        logger.warning(
            "[CENTRAL-CONTROL] ON: metric=%s confidence_mode=%s pf_cap=%s min_trades=%s max_positions=%s sizing=%s buy_mode=%s buy_timing_mode=%s order_notional_safety_buffer=%.4f universe=promoted∩central pool_limit=%s stage3_mix=%s stage3_pool_path=%s stage3_pool_limit=%s strength_cap=%s stage3_strength_cap=%s stage3_min_confidence=%s exits=unchanged existing_positions=unchanged legacy_buy_guard=central_only",
            args.central_selection_metric,
            args.central_confidence_mode,
            args.central_pf_cap,
            args.central_min_trades,
            args.central_max_positions,
            args.central_position_sizing,
            args.buy_mode,
            args.buy_timing_mode,
            sizing_buffer,
            args.central_pool_limit,
            args.central_stage3_mix,
            args.central_stage3_pool_path,
            args.central_stage3_pool_limit,
            args.central_strength_cap,
            args.central_stage3_strength_cap,
            args.central_stage3_min_confidence,
        )
    else:
        if args.buy_mode != "auto":
            logger.warning("[CENTRAL-CONTROL] OFF: --buy-mode=%s는 central-control ON에서만 적용됨", args.buy_mode)
        logger.warning("[CENTRAL-CONTROL] OFF: 구 live 개별 ticker BUY 경로는 LEGACY_BUY_DISABLED로 차단; SELL/청산/보유관리는 유지")
    if hasattr(runner, "notifier") and hasattr(runner, "tick_market"):
        install_position_dashboard(runner)

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
    scheduler.add_market_hours_job(func=make_holding_news_tick_market_job(runner), interval_sec=args.market_tick, market=clock, job_id="tick_market")
    scheduler.add_interval_job(func=runner.tick_offmarket, interval_sec=args.offmarket_tick, job_id="tick_offmarket", name="tick_offmarket")
    if next_open_coordinator is not None:
        scheduler.add_interval_job(
            func=next_open_coordinator.prepare_if_due,
            interval_sec=60,
            job_id="next_open_prepare_queue",
            name="next_open_prepare_queue",
        )
        scheduler.add_interval_job(
            func=next_open_coordinator.execute_if_due,
            interval_sec=max(5, min(60, int(args.market_tick or 60))),
            job_id="next_open_execute_queue",
            name="next_open_execute_queue",
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
        hour=args.summary_hour,
        minute=args.summary_minute,
        market=clock,
        weekdays_only=False,
        job_id="daily_summary",
    )
    scheduler.add_cron_job(
        func=weekly_report_job,
        hour=args.weekly_summary_hour,
        minute=args.weekly_summary_minute,
        market=clock,
        weekdays_only=False,
        job_id="weekly_summary",
    )
    scheduler.add_cron_job(
        func=monthly_report_job,
        hour=args.monthly_summary_hour,
        minute=args.monthly_summary_minute,
        market=clock,
        weekdays_only=False,
        job_id="monthly_summary",
    )

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
