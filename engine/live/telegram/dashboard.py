"""Telegram live position dashboard.

Tick마다 기존 대시보드 메시지는 editMessageText로 갱신하고,
거래/차단/오류 등 별도 이벤트 알림이 실제 전송된 뒤에는 delete→send로
대시보드를 다시 맨 아래로 배치한다.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from engine.live.exit_policy_adapter import count_holding_trading_days
from engine.live.safety import state as state_mod
from engine.live.telegram.notifier import API_BASE

log = logging.getLogger("telegram.dashboard")

ROOT = Path(__file__).resolve().parents[3]
STATE_PATH = ROOT / "data" / "_system" / "telegram_position_dashboard.json"
KST = ZoneInfo("Asia/Seoul")
MAX_TEXT_LEN = 3900
MIN_EDIT_INTERVAL_SEC = 20
TRAILING_ACTIVATION_BARS = 2


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _fmt_usd(value: Any, signed: bool = False) -> str:
    amount = _float(value)
    if signed and amount > 0:
        return f"+${amount:,.2f}"
    if amount < 0:
        return f"-${abs(amount):,.2f}"
    return f"${amount:,.2f}"


def _fmt_pct(value: Any) -> str:
    return f"{_float(value):+.2f}%"


def _load_json(path: Path, default: dict) -> dict:
    try:
        if not path.exists():
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class PositionDashboardController:
    def __init__(self, runner, state_path: Path = STATE_PATH):
        self.runner = runner
        self.notifier = runner.notifier
        self.state_path = state_path
        self._repost_requested = True
        self._installed_hooks = False
        self._last_update_attempt_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.notifier, "enabled", False))

    def request_repost(self) -> None:
        self._repost_requested = True

    def consume_repost_flag(self) -> bool:
        value = bool(self._repost_requested)
        self._repost_requested = False
        return value

    def _api_post(self, method: str, payload: dict, timeout: float = 5.0):
        token = str(getattr(self.notifier, "token", "") or "").strip()
        if not token:
            return None
        url = f"{API_BASE}/bot{token}/{method}"
        return requests.post(url, json=payload, timeout=timeout)

    def _send_message(self, text: str) -> int:
        if not self.enabled:
            return 0
        payload = {
            "chat_id": str(getattr(self.notifier, "chat_id", "") or ""),
            "text": text[:MAX_TEXT_LEN],
            "disable_web_page_preview": True,
        }
        try:
            res = self._api_post("sendMessage", payload)
            if res is None or res.status_code != 200:
                if res is not None:
                    log.warning("dashboard sendMessage 실패 %s: %s", res.status_code, res.text[:200])
                return 0
            return int(res.json().get("result", {}).get("message_id", 0) or 0)
        except Exception as exc:
            log.warning("dashboard sendMessage 예외: %s", exc)
            return 0

    def _edit_message(self, message_id: int, text: str) -> bool:
        if not self.enabled or not message_id:
            return False
        payload = {
            "chat_id": str(getattr(self.notifier, "chat_id", "") or ""),
            "message_id": int(message_id),
            "text": text[:MAX_TEXT_LEN],
            "disable_web_page_preview": True,
        }
        try:
            res = self._api_post("editMessageText", payload)
            if res is None:
                return False
            if res.status_code == 200:
                return True
            body = str(res.text or "")
            if "message is not modified" in body.lower():
                return True
            log.debug("dashboard editMessageText 실패 %s: %s", res.status_code, body[:200])
            return False
        except Exception as exc:
            log.warning("dashboard editMessageText 예외: %s", exc)
            return False

    def _delete_message(self, message_id: int) -> bool:
        if not self.enabled or not message_id:
            return False
        payload = {
            "chat_id": str(getattr(self.notifier, "chat_id", "") or ""),
            "message_id": int(message_id),
        }
        try:
            res = self._api_post("deleteMessage", payload)
            return bool(res is not None and res.status_code == 200)
        except Exception as exc:
            log.debug("dashboard deleteMessage 예외: %s", exc)
            return False

    def _load_state(self) -> dict:
        return _load_json(self.state_path, {})

    def _save_state(self, message_id: int, text: str) -> None:
        _save_json(
            self.state_path,
            {
                "message_id": int(message_id or 0),
                "last_text": text[:MAX_TEXT_LEN],
                "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
            },
        )

    def _wrap_event_method(self, name: str) -> None:
        original = getattr(self.notifier, name, None)
        if not callable(original) or getattr(original, "_dashboard_wrapped", False):
            return

        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            try:
                if result:
                    self.request_repost()
            except Exception:
                pass
            return result

        setattr(wrapped, "_dashboard_wrapped", True)
        setattr(self.notifier, name, wrapped)

    def install_event_hooks(self) -> None:
        if self._installed_hooks:
            return
        for name in (
            "send",
            "send_order",
            "send_trade_entry",
            "send_trade_exit",
            "send_safety_block",
            "send_order_rejected",
            "send_error",
            "send_info",
            "send_risk_alert",
            "send_system_alert",
            "send_approval_request",
        ):
            self._wrap_event_method(name)
        self._installed_hooks = True

    def _position_by_ticker(self) -> dict[str, Any]:
        try:
            return {str(pos.ticker): pos for pos in self.runner.position_manager.all()}
        except Exception:
            return {}

    def _holding_trading_days(self, pos: Any, ticker: str) -> int:
        try:
            return int(count_holding_trading_days(str(getattr(pos, "entry_date", "")), ticker=ticker))
        except Exception:
            return 0

    def _trailing_status_line(
        self,
        *,
        ticker: str,
        pos: Any,
        entry: float,
        stop: float,
        target: float,
        trail: float,
    ) -> str:
        rb = getattr(pos, "rulebook_snapshot", {}) or {}
        strategy = str(rb.get("exit_strategy") or getattr(pos, "exit_strategy", "") or "").lower()
        stop_pct = (stop / entry - 1.0) * 100.0 if entry > 0 and stop > 0 else 0.0
        target_pct = (target / entry - 1.0) * 100.0 if entry > 0 and target > 0 else 0.0
        base = f"진입 {entry:,.2f} / 손절 {stop:,.2f}({stop_pct:+.1f}%) / 익절 {target:,.2f}({target_pct:+.1f}%)"

        if strategy == "fixed":
            return f"  {base} / 실손절 {stop:,.2f} / trail 비활성(고정)"
        if trail <= 0 or entry <= 0:
            return f"  {base} / 실손절 {stop:,.2f} / trail 없음"

        highest = _float(getattr(pos, "highest_price", entry), entry)
        highest_profit_pct = (highest / entry - 1.0) * 100.0 if entry > 0 and highest > 0 else 0.0
        activation_pct = _float(rb.get("trailing_activation_profit_pct"), 0.0)
        holding_days = self._holding_trading_days(pos, ticker)
        bars_ok = holding_days > TRAILING_ACTIVATION_BARS
        price_ok = highest_profit_pct >= activation_pct
        active = bars_ok and price_ok

        if active:
            effective_stop = max(stop, trail)
            return f"  {base} / 실손절 {effective_stop:,.2f} / trail {trail:,.2f} 활성✓"

        notes: list[str] = []
        if not price_ok:
            notes.append(f"최고 {highest_profit_pct:+.1f}%<{activation_pct:+.1f}%")
        if not bars_ok:
            notes.append(f"보유 {holding_days}d≤{TRAILING_ACTIVATION_BARS}d")
        reason = ", ".join(notes) if notes else "대기"
        return f"  {base} / 실손절 {stop:,.2f} / trail {trail:,.2f} 비활성({reason})"

    def _holding_lines(self, holdings: list[Any], positions: dict[str, Any]) -> list[str]:
        if not holdings:
            return ["보유: 없음"]
        lines: list[str] = []
        for holding in sorted(holdings, key=lambda h: str(getattr(h, "ticker", ""))):
            ticker = str(getattr(holding, "ticker", "") or "?")
            shares = _float(getattr(holding, "shares", 0.0))
            cur = _float(getattr(holding, "current_price", 0.0))
            entry = _float(getattr(holding, "avg_cost", 0.0))
            market_value = _float(getattr(holding, "market_value", 0.0))
            pnl = _float(getattr(holding, "unrealized_pnl", 0.0))
            pnl_pct = _float(getattr(holding, "unrealized_pnl_pct", 0.0))
            pos = positions.get(ticker)
            if pos is not None:
                entry = _float(getattr(pos, "entry_price", entry), entry)
            if cur <= 0:
                try:
                    cur = _float(self.runner.broker.get_current_price(ticker), cur)
                except Exception:
                    pass
            if market_value <= 0 and shares > 0 and cur > 0:
                market_value = shares * cur
            if pnl == 0.0 and entry > 0 and cur > 0 and shares > 0:
                pnl = (cur - entry) * shares
                pnl_pct = (cur / entry - 1.0) * 100.0

            icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{icon} {ticker} {cur:,.2f} | {market_value:,.2f} USD | "
                f"{_fmt_usd(pnl, signed=True)} ({_fmt_pct(pnl_pct)})"
            )
            if pos is not None:
                stop = _float(getattr(pos, "stop_price", 0.0))
                target = _float(getattr(pos, "target_price", 0.0))
                trail = _float(getattr(pos, "trailing_stop", 0.0))
                lines.append(
                    self._trailing_status_line(
                        ticker=ticker,
                        pos=pos,
                        entry=entry,
                        stop=stop,
                        target=target,
                        trail=trail,
                    )
                )
        return lines

    def build_text(self) -> str:
        now = datetime.now(KST)
        try:
            balance = self.runner.broker.get_balance()
            holdings = list(getattr(balance, "holdings", []) or [])
            cash = _float(getattr(balance, "cash_usd", getattr(balance, "cash_krw", 0.0)))
            total_value = _float(getattr(balance, "total_value_usd", getattr(balance, "total_value_krw", 0.0)))
        except Exception as exc:
            holdings = []
            cash = total_value = 0.0
            log.warning("dashboard balance 조회 실패: %s", exc)

        positions = self._position_by_ticker()
        invested = sum(max(0.0, _float(getattr(h, "shares", 0.0)) * _float(getattr(h, "avg_cost", 0.0))) for h in holdings)
        unrealized = sum(_float(getattr(h, "unrealized_pnl", 0.0)) for h in holdings)
        unrealized_pct = (unrealized / invested * 100.0) if invested > 0 else 0.0
        try:
            st = state_mod.load()
            orders_today = int(getattr(st, "orders_today", 0) or 0)
            bought_today = _float(getattr(st, "invested_krw_today", 0.0))
        except Exception:
            orders_today = 0
            bought_today = 0.0

        pending_n = 0
        try:
            pending_n = len(self.runner.pending_order_manager.all())
        except Exception:
            pending_n = 0

        stats = getattr(self.runner, "stats", None)
        tick_no = int(getattr(stats, "market_ticks", 0) or 0)
        lines = [
            "📊 Kingmaker Live Dashboard",
            f"업데이트: {now:%Y-%m-%d %H:%M:%S KST}",
            f"모드: {getattr(self.runner.broker, 'mode', '?')} | tick #{tick_no} | pending {pending_n}",
            f"총평가: {_fmt_usd(total_value)} | 현금: {_fmt_usd(cash)}",
            f"보유: {len(holdings)}개 | 평가손익: {_fmt_usd(unrealized, signed=True)} ({_fmt_pct(unrealized_pct)})",
            f"오늘 주문: {orders_today}건 | 오늘 매수: {_fmt_usd(bought_today)}",
            "",
            "📌 Positions",
        ]
        lines.extend(self._holding_lines(holdings, positions))
        text = "\n".join(lines)
        return text[:MAX_TEXT_LEN]

    def update(self, force_repost: bool = False) -> bool:
        if not self.enabled:
            return False
        now = time.time()
        if not force_repost and now - self._last_update_attempt_at < MIN_EDIT_INTERVAL_SEC:
            return False
        self._last_update_attempt_at = now

        text = self.build_text()
        state = self._load_state()
        message_id = int(state.get("message_id") or 0)
        if not force_repost and message_id and state.get("last_text") == text:
            return True

        if force_repost and message_id:
            self._delete_message(message_id)
            message_id = 0

        if message_id:
            if self._edit_message(message_id, text):
                self._save_state(message_id, text)
                return True
            log.info("dashboard edit 실패 → 새 메시지로 재생성")

        new_id = self._send_message(text)
        if new_id:
            self._save_state(new_id, text)
            return True
        return False


def install_position_dashboard(runner) -> PositionDashboardController:
    existing = getattr(runner, "_position_dashboard_controller", None)
    if existing is not None:
        return existing

    controller = PositionDashboardController(runner)
    controller.install_event_hooks()
    original_tick_market: Callable[[], Any] = runner.tick_market

    def tick_market_with_dashboard():
        try:
            return original_tick_market()
        finally:
            try:
                controller.update(force_repost=controller.consume_repost_flag())
            except Exception as exc:
                log.warning("position dashboard update 실패: %s", exc)

    runner.tick_market = tick_market_with_dashboard
    runner._position_dashboard_controller = controller
    log.info("Telegram position dashboard installed")
    return controller
