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
- local command server lets the dashboard wake the runner immediately after a
  manual sell intent is written, without letting api_server place broker orders
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
COMMAND_STATE_PATH = Path("data/_system/runner_command_lr8d16.json")
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
    parser.add_argument("--no-command-server", action="store_true", help="대시보드 즉시 청산 wake용 localhost command server 비활성")
    parser.add_argument("--command-port", type=int, default=8765, help="localhost runner command server 포트")
    parser.add_argument("--summary-hour", type=int, default=6)
    parser.add_argument("--summary-minute", type=int, default=15)
    parser.add_argument("--weekly-summary-hour", type=int, default=6)
    parser.add_argument("--weekly-summary-minute", type=int, default=30)
    parser.add_argument("--monthly-summary-hour", type=int, default=6)
    parser.add_argument("--monthly-summary-minute", type=int, default=45)
    return parser.parse_args()


def _utc_now() -> str:
    return datetime.now(ZoneInfo("UTC")).isoformat()


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _active_positions(runner: Runner) -> list[str]:
    try:
        return sorted(str(t).upper() for t in runner.position_manager.list_tickers())
    except Exception:
        try:
            return sorted(str(t).upper() for t in getattr(runner.position_manager, "positions", {}).keys())
        except Exception:
            return []


def _pending_snapshot(runner: Runner) -> list[dict]:
    pending_mgr = getattr(runner, "pending_order_manager", None)
    if pending_mgr is None:
        return []
    out: list[dict] = []
    try:
        for record in pending_mgr.all():
            out.append(
                {
                    "order_id": str(getattr(record, "order_id", "") or ""),
                    "ticker": str(getattr(record, "ticker", "") or ""),
                    "side": str(getattr(record, "side", "") or ""),
                    "purpose": str(getattr(record, "purpose", "") or ""),
                    "state": str(getattr(record, "state", "") or ""),
                    "filled_shares": float(getattr(record, "filled_shares", 0.0) or 0.0),
                }
            )
    except Exception:
        return []
    return out


def run_manual_sell_wake_cycle(runner: Runner, *, context: str) -> dict:
    """Run the existing manual-sell path immediately inside the live runner.

    api_server only writes an intent.  This function keeps all broker order
    placement, pending tracking, positions cleanup, realized PnL, and notifier
    behavior inside Runner, then returns a small status snapshot for the API.
    """
    lock = getattr(runner, "manual_exit_lock", None)
    acquired = False
    if lock is not None:
        acquired = bool(lock.acquire(timeout=5.0))
        if not acquired:
            return {"ok": False, "reason": "manual_exit_cycle_busy", "context": context}
    try:
        before_pending = _pending_snapshot(runner)
        before_positions = _active_positions(runner)
        runner._poll_pending_orders(context=f"{context}.pre_manual")
        runner._process_manual_sell_intents()
        runner._poll_pending_orders(context=f"{context}.post_manual")
        after_pending = _pending_snapshot(runner)
        after_positions = _active_positions(runner)
        return {
            "ok": True,
            "context": context,
            "before_positions": before_positions,
            "after_positions": after_positions,
            "before_pending_count": len(before_pending),
            "after_pending_count": len(after_pending),
            "after_pending": after_pending[:8],
            "processed_at": _utc_now(),
        }
    except Exception as exc:
        runner._handle_error(context, exc)
        return {"ok": False, "reason": type(exc).__name__, "message": str(exc), "context": context}
    finally:
        if lock is not None and acquired:
            try:
                lock.release()
            except Exception:
                pass


def run_fast_exit_cycle(runner: Runner, *, context: str) -> dict:
    wake = run_manual_sell_wake_cycle(runner, context=context)
    if not wake.get("ok"):
        return wake
    try:
        pending_mgr = getattr(runner, "pending_order_manager", None)
        if pending_mgr is not None and pending_mgr.all():
            wake["auto_exit_checked"] = False
            wake["auto_exit_skip_reason"] = "pending_order_exists"
            return wake
        exited = runner.position_manager.check_exits(
            runner.broker,
            runner.notifier,
            pending_manager=pending_mgr,
        )
        if exited:
            for record in exited:
                runner._record_realized_pnl_from_trade(record)
            logger.info("[FAST-EXIT] 자동 청산 %d건 완료", len(exited))
        wake["auto_exit_checked"] = True
        wake["auto_exit_count"] = len(exited or [])
        return wake
    except Exception as exc:
        runner._handle_error(context + ".auto_exit", exc)
        wake["auto_exit_checked"] = False
        wake["auto_exit_error"] = f"{type(exc).__name__}: {exc}"
        return wake


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
            run_fast_exit_cycle(runner, context="fast_exit")
        finally:
            state["busy"] = False

    fast_exit_tick.__name__ = "fast_exit_tick"
    return fast_exit_tick


def start_runner_command_server(runner: Runner, *, port: int) -> ThreadingHTTPServer:
    """Start localhost-only wake server used by api_server after manual sell click."""
    token = secrets.token_urlsafe(32)
    bind_port = int(port or 8765)

    class Handler(BaseHTTPRequestHandler):
        server_version = "KingmakerRunnerCommand/1.0"

        def log_message(self, fmt, *args):  # noqa: N802
            logger.info("[RUNNER-COMMAND] " + fmt, *args)

        def _send_json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            return str(self.headers.get("X-Kingmaker-Token") or "") == token

        def do_GET(self):  # noqa: N802
            if self.path != "/health":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            if not self._authorized():
                self._send_json(403, {"ok": False, "error": "forbidden"})
                return
            self._send_json(200, {"ok": True, "run_id": "lr8d16", "pid": os.getpid(), "time": _utc_now()})

        def do_POST(self):  # noqa: N802
            if self.path != "/manual_sell/wake":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            if not self._authorized():
                self._send_json(403, {"ok": False, "error": "forbidden"})
                return
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 8192)
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {}
            result = run_manual_sell_wake_cycle(runner, context="rpc_manual_sell")
            result["request"] = {
                "ticker": str(payload.get("ticker") or "").upper(),
                "intent_id": str(payload.get("intent_id") or ""),
                "source": str(payload.get("source") or "api_server"),
            }
            self._send_json(200 if result.get("ok") else 409, result)

    httpd = ThreadingHTTPServer(("127.0.0.1", bind_port), Handler)
    actual_port = int(httpd.server_address[1])
    state = {
        "_comment": "Local-only live runner command endpoint. api_server uses this to wake Runner after manual sell intent creation; broker orders still happen only inside the runner process.",
        "run_id": "lr8d16_legacy",
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": actual_port,
        "url": f"http://127.0.0.1:{actual_port}",
        "token": token,
        "created_at": _utc_now(),
        "manual_sell_wake_path": "/manual_sell/wake",
    }
    _atomic_write_json(COMMAND_STATE_PATH, state)
    thread = threading.Thread(target=httpd.serve_forever, name="runner-command-server", daemon=True)
    thread.start()
    logger.warning("[RUNNER-COMMAND] ON: url=%s state=%s", state["url"], COMMAND_STATE_PATH)
    return httpd


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
    runner.manual_exit_lock = threading.Lock()
    install_position_dashboard(runner)

    command_server = None
    if not args.no_command_server:
        try:
            command_server = start_runner_command_server(runner, port=int(args.command_port))
        except Exception as exc:
            logger.error("[RUNNER-COMMAND] 시작 실패 — intent+fast-exit fallback만 사용: %s", exc)
    else:
        logger.warning("[RUNNER-COMMAND] OFF")

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
            if command_server is not None:
                command_server.shutdown()
                command_server.server_close()
                logger.info("Runner command server 종료")
        except Exception as exc:
            logger.warning("Runner command server 종료 예외: %s", exc)
        try:
            if COMMAND_STATE_PATH.exists():
                COMMAND_STATE_PATH.unlink()
        except Exception:
            pass
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
