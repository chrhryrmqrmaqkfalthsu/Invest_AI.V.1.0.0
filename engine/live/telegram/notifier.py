"""
TelegramNotifier - 단방향 알림 전송 (봇 → 사용자)
- 외부 라이브러리 없이 requests만 사용
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 필수
- 토큰 없으면 silent fail (운영 중 알림 실패로 봇이 죽지 않게)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from dotenv import dotenv_values

import requests

ENV_PATH = Path.home() / "kingmaker" / ".env"
API_BASE = "https://api.telegram.org"

log = logging.getLogger("telegram.notifier")


class TelegramNotifier:

    def __init__(self, env_path: Optional[str] = None, silent_on_error: bool = True):
        env = dotenv_values(env_path or str(ENV_PATH))
        self.token   = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        self.silent_on_error = silent_on_error

        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            log.warning("Telegram 미설정 (TOKEN/CHAT_ID 없음) — 알림은 무시됨")

    def send(self, text: str, parse_mode: str = "") -> bool:
        """일반 메시지. 반환: 성공 여부"""
        if not self.enabled:
            return False
        url = f"{API_BASE}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code != 200:
                log.warning(f"Telegram send 실패 {res.status_code}: {res.text[:200]}")
                if not self.silent_on_error:
                    raise RuntimeError(f"Telegram send 실패: {res.text[:200]}")
                return False
            return True
        except requests.RequestException as e:
            log.warning(f"Telegram 네트워크 오류: {e}")
            if not self.silent_on_error:
                raise
            return False

    # ---------- 포맷된 알림 ----------
    def send_order(self, order) -> bool:
        """주문 체결/접수 알림"""
        side_kr = "🟢 매수" if str(order.side).lower().endswith("buy") else "🔴 매도"
        status_emoji = {
            "pending":   "⏳",
            "filled":    "✅",
            "partial":   "🟡",
            "cancelled": "⚪",
            "rejected":  "❌",
            "failed":    "❌",
        }.get(str(order.status).lower().split(".")[-1], "❓")

        text = (
            f"{side_kr} *{order.ticker}*\n"
            f"수량: {order.shares}주 @ {order.price:,.0f}원\n"
            f"상태: {status_emoji} `{order.status}`\n"
        )
        if order.filled_shares > 0:
            text += f"체결: {order.filled_shares}주 @ {order.filled_avg_price:,.0f}원\n"
        if order.message:
            text += f"_{order.message[:120]}_\n"
        return self.send(text)

    def send_error(self, message: str) -> bool:
        return self.send(f"⚠️ *오류*\n```\n{message[:500]}\n```")

    def send_info(self, message: str) -> bool:
        return self.send(f"ℹ️ {message}")

    def send_safety_block(self, code: str, reason: str) -> bool:
        return self.send(f"🛑 *주문 차단* `{code}`\n{reason}")

    def send_daily_summary(self, summary: dict) -> bool:
        text = (
            "📊 *일일 요약*\n"
            f"가용 현금: {summary.get('cash_krw', 0):,.0f}원\n"
            f"총 평가금: {summary.get('total_value_krw', 0):,.0f}원\n"
            f"오늘 손익: {summary.get('realized_pnl_today', 0):+,.0f}원\n"
            f"오늘 주문: {summary.get('orders_today', 0)}건\n"
            f"보유 종목: {summary.get('holdings_count', 0)}개"
        )
        return self.send(text)


if __name__ == "__main__":
    print("=" * 50)
    print("TelegramNotifier 검증")
    print("=" * 50)

    n = TelegramNotifier()
    print(f"enabled: {n.enabled}, chat_id 길이: {len(n.chat_id)}")

    if not n.enabled:
        print("❌ TELEGRAM_BOT_TOKEN/CHAT_ID 설정 안 됨")
        raise SystemExit(1)

    print("\n[1] 기본 메시지 전송...")
    ok = n.send("🤖 *Kingmaker* 봇 연결 테스트\n_TelegramNotifier 검증 중_")
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n[2] 정보 메시지...")
    ok = n.send_info("이건 info 알림입니다")
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n[3] 차단 알림...")
    ok = n.send_safety_block("LIMIT_KRW", "주문금액 15,000원 > 한도 10,000원")
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n[4] 일일 요약...")
    ok = n.send_daily_summary({
        "cash_krw": 100000, "total_value_krw": 125615,
        "realized_pnl_today": -3500, "orders_today": 2, "holdings_count": 1,
    })
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n" + "=" * 50)
    print("✅ 검증 완료 — 휴대폰 텔레그램에서 4개 메시지 확인")
    print("=" * 50)
