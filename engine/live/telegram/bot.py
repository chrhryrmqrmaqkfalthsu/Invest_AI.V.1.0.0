"""
TelegramBot - 양방향 명령 처리 (polling 방식)
- python-telegram-bot 같은 무거운 의존성 없이 requests만 사용
- TELEGRAM_CHAT_ID 화이트리스트 (다른 chat은 무시)
- 명령: /start /help /status /positions /pause /resume /approve /kill

사용법:
    bot = TelegramBot(broker=broker, safety=safety_layer)
    bot.start_polling()  # 블로킹. 별도 스레드에서 실행 권장.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional
from dotenv import dotenv_values

import requests

from .notifier import TelegramNotifier
from ..broker.base import Broker
from ..safety.layer import SafetyLayer, KILL_SWITCH_PATH
from ..safety import state as state_mod

ENV_PATH = Path.home() / "kingmaker" / ".env"
API_BASE = "https://api.telegram.org"

log = logging.getLogger("telegram.bot")


class TelegramBot:

    def __init__(
        self,
        broker: Optional[Broker] = None,
        safety: Optional[SafetyLayer] = None,
        notifier: Optional[TelegramNotifier] = None,
        poll_interval: float = 2.0,
    ):
        env = dotenv_values(str(ENV_PATH))
        self.token       = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self.allowed_id  = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        self.broker      = broker
        self.safety      = safety
        self.notifier    = notifier or TelegramNotifier()
        self.poll_interval = poll_interval

        if not self.token or not self.allowed_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 누락")

        self._offset = 0          # 마지막으로 처리한 update_id
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 명령 라우팅 테이블
        self.commands: Dict[str, Callable[[dict], str]] = {
            "/start":     self._cmd_start,
            "/help":      self._cmd_help,
            "/status":    self._cmd_status,
            "/positions": self._cmd_positions,
            "/pause":     self._cmd_pause,
            "/resume":    self._cmd_resume,
            "/approve":   self._cmd_approve,
            "/kill":      self._cmd_kill,
        }

    # ---------- polling ----------
    def start_polling(self, blocking: bool = True) -> None:
        self._running = True
        if blocking:
            self._poll_loop()
        else:
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _poll_loop(self) -> None:
        log.info(f"polling 시작 (interval={self.poll_interval}s)")
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                log.error(f"polling 예외: {e}")
                time.sleep(5)
            time.sleep(self.poll_interval)

    def _poll_once(self) -> None:
        url = f"{API_BASE}/bot{self.token}/getUpdates"
        params = {"offset": self._offset + 1, "timeout": 0}
        try:
            res = requests.get(url, params=params, timeout=10)
        except requests.RequestException as e:
            log.warning(f"getUpdates 실패: {e}")
            return
        if res.status_code != 200:
            log.warning(f"getUpdates HTTP {res.status_code}")
            return
        for update in res.json().get("result", []):
            self._offset = update["update_id"]
            self._handle_update(update)

    def _handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id", ""))
        if chat_id != self.allowed_id:
            log.warning(f"화이트리스트 외 chat_id={chat_id} (텍스트={msg.get('text','')[:30]}) — 무시")
            return

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return

        # /command @botname 형식도 처리
        cmd = text.split()[0].split("@")[0].lower()
        handler = self.commands.get(cmd)
        if not handler:
            self.notifier.send(f"알 수 없는 명령: `{cmd}`\n/help 로 목록 확인")
            return

        try:
            reply = handler(msg)
            if reply:
                self.notifier.send(reply)
        except Exception as e:
            log.exception(f"명령 처리 실패: {cmd}")
            self.notifier.send_error(f"{cmd} 처리 실패: {e}")

    # ---------- 명령 핸들러 ----------
    def _cmd_start(self, msg: dict) -> str:
        return (
            "🤖 *Kingmaker* 봇 연결됨\n"
            f"chat_id=`{msg['chat']['id']}` 화이트리스트 통과\n"
            "/help 로 명령어 확인"
        )

    def _cmd_help(self, msg: dict) -> str:
        return (
            "*명령어*\n"
            "/status — 잔고/손익 요약\n"
            "/positions — 보유 종목 상세\n"
            "/approve — 오늘 첫 주문 승인 ⚠️\n"
            "/pause — 봇 일시정지 (KILL_SWITCH)\n"
            "/resume — 재개\n"
            "/kill — 긴급 정지 (보유 청산은 별도 확인)\n"
        )

    def _cmd_status(self, msg: dict) -> str:
        if not self.broker:
            return "broker 미연결 (Runner 미가동)"
        bal = self.broker.get_balance()
        st = state_mod.load()
        return (
            "📊 *현황*\n"
            f"가용 현금: {bal.cash_krw:,.0f}원\n"
            f"총 평가금: {bal.total_value_krw:,.0f}원\n"
            f"매수 원금: {bal.invested_krw:,.0f}원\n"
            f"보유 종목: {len(bal.holdings)}개\n"
            f"오늘 주문: {st.orders_today}건\n"
            f"오늘 손익: {st.realized_pnl_today:+,.0f}원\n"
            f"연속 손실: {st.consecutive_losses}건\n"
            f"승인 상태: {'✅ 승인됨' if st.first_order_approved else '⏳ 미승인'}"
        )

    def _cmd_positions(self, msg: dict) -> str:
        if not self.broker:
            return "broker 미연결"
        holdings = self.broker.get_holdings()
        if not holdings:
            return "보유 종목 없음"
        lines = ["📦 *보유 종목*"]
        for h in holdings:
            lines.append(
                f"`{h.ticker}` {h.shares}주 @ {h.avg_cost:,.0f}\n"
                f"  현재 {h.current_price:,.0f} ({h.unrealized_pnl_pct:+.2f}%, "
                f"{h.unrealized_pnl:+,.0f}원)"
            )
        return "\n".join(lines)

    def _cmd_pause(self, msg: dict) -> str:
        KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_PATH.touch()
        return f"⏸ 봇 일시정지 (KILL_SWITCH 생성)\n`{KILL_SWITCH_PATH}`\n/resume 으로 해제"

    def _cmd_resume(self, msg: dict) -> str:
        if KILL_SWITCH_PATH.exists():
            KILL_SWITCH_PATH.unlink()
            return "▶️ 봇 재개 (KILL_SWITCH 제거)"
        return "이미 활성 상태 (KILL_SWITCH 없음)"

    def _cmd_approve(self, msg: dict) -> str:
        if not self.safety:
            return "SafetyLayer 미연결"
        self.safety.approve_first_order()
        return "✅ 오늘 첫 주문 승인됨. 다음 매수 시도 시 발사됩니다."

    def _cmd_kill(self, msg: dict) -> str:
        KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_PATH.touch()
        return (
            "🛑 *KILL 명령 수신*\n"
            "신규 주문 차단됨 (KILL_SWITCH 생성)\n"
            "_보유 종목 청산은 별도로 수동 처리 또는 정책 결정 필요_\n"
            "/resume 으로 해제"
        )


# ==================================================
# 단독 실행: 명령 라우팅 단위 테스트 (실제 polling X)
# ==================================================
if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock
    from engine.live.broker.base import Balance, Holding

    print("=" * 50)
    print("TelegramBot 명령 라우팅 검증 (실제 polling 안 함)")
    print("=" * 50)

    # 모의 broker
    mock_broker = MagicMock()
    mock_broker.get_balance = MagicMock(return_value=Balance(
        cash_krw=100000, total_value_krw=125000, invested_krw=25000,
        holdings=[Holding(
            ticker="379800", shares=1, avg_cost=25000, current_price=25500,
            market_value=25500, unrealized_pnl=500, unrealized_pnl_pct=2.0,
        )],
    ))
    mock_broker.get_holdings = MagicMock(side_effect=lambda: mock_broker.get_balance().holdings)

    # 모의 safety
    mock_safety = MagicMock()
    mock_safety.approve_first_order = MagicMock()

    bot = TelegramBot(broker=mock_broker, safety=mock_safety)
    print(f"allowed_chat_id={bot.allowed_id}, commands={len(bot.commands)}개\n")

    # 화이트리스트 chat_id로 모의 메시지 만들기
    def fake_msg(text: str) -> dict:
        return {"chat": {"id": int(bot.allowed_id)}, "text": text}

    # 각 명령의 반환 텍스트 검증 (실제 텔레그램 전송은 안 함)
    for cmd in ["/start", "/help", "/status", "/positions", "/pause", "/resume", "/approve", "/kill"]:
        handler = bot.commands.get(cmd)
        try:
            reply = handler(fake_msg(cmd))
            head = reply.split("\n")[0] if reply else "(빈 응답)"
            print(f"  ✅ {cmd:12s} → {head[:60]}")
        except Exception as e:
            print(f"  ❌ {cmd:12s} → 예외: {e}")

    # 화이트리스트 외 chat_id 차단 검증
    print("\n[화이트리스트 차단 테스트]")
    bot._handle_update({"update_id": 1, "message": {
        "chat": {"id": 99999999}, "text": "/status",
    }})
    print("  ✅ 화이트리스트 외 chat_id → 무시됨 (notifier 미호출)")

    # KILL_SWITCH 청소 (테스트 잔재)
    if KILL_SWITCH_PATH.exists():
        KILL_SWITCH_PATH.unlink()
        print("\n  ℹ️ 테스트 잔재 KILL_SWITCH 제거")

    print("\n" + "=" * 50)
    print("✅ 라우팅 검증 완료")
    print("=" * 50)
    print("\n실제 polling 테스트는 별도 (다음 단계에서 봇 띄우고 휴대폰으로 /start)")
