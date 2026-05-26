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

    def send_progress(self, text: str) -> int:
        """진행 상태 placeholder 전송. message_id 반환 (실패 시 0)."""
        if not self.enabled:
            return 0
        url = f"{API_BASE}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code != 200:
                return 0
            data = res.json()
            return int(data.get("result", {}).get("message_id", 0))
        except Exception as e:
            log.warning(f"send_progress 실패: {e}")
            return 0

    def edit_message(self, message_id: int, text: str, parse_mode: str = "") -> bool:
        """기존 메시지 본문 교체. message_id=0이면 send fallback."""
        if not self.enabled or not message_id:
            return self.send(text, parse_mode=parse_mode)
        url = f"{API_BASE}/bot{self.token}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code != 200:
                # 본문이 동일하면 텔레그램이 400 반환 — 무시
                log.debug(f"edit_message {res.status_code}: {res.text[:100]}")
                return False
            return True
        except Exception as e:
            log.warning(f"edit_message 실패: {e}")
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

    # ---------- Phase E: 추가 매수 승인 요청 ----------
    def send_approval_request(self, req) -> bool:
        """ApprovalRequest 객체를 받아 강도별 차등 알림 발송."""
        emoji_map = {"weak": "🟡", "medium": "🟠", "strong": "🔴"}
        level_kr = {"weak": "약한", "medium": "중간", "strong": "강한"}
        emoji = emoji_map.get(req.strength, "🟡")
        level = level_kr.get(req.strength, req.strength)

        # 가격 변화율
        try:
            target_pct = (req.target_price / req.current_price - 1) * 100
            stop_pct = (req.stop_price / req.current_price - 1) * 100
        except Exception:
            target_pct = stop_pct = 0.0

        # 시그널 근거 (최대 3개)
        reasons = req.signal_reasons[:3] if req.signal_reasons else []
        reasons_str = "\n".join(f"    • {r}" for r in reasons) if reasons else "    • (근거 없음)"

        # 한도 옵션 명령어
        opt_lines = []
        for krw in req.options_krw:
            shares = int(krw / req.current_price) if req.current_price > 0 else 0
            label = f"{krw // 1000}k"
            opt_lines.append(f"/approve_{label} — {krw:,}원 (≈{shares}주)")
        opt_str = "\n".join(opt_lines)

        text = (
            f"{emoji} *{level} BUY 시그널*: `{req.ticker}`\n"
            f"현재가: {req.current_price:,.0f}원\n"
            f"\n"
            f"📊 *분석 근거*\n"
            f"  점수: {req.signal_score:.2f} / 임계 {req.signal_threshold:.2f} "
            f"(×{req.signal_score/req.signal_threshold:.2f})\n"
            f"{reasons_str}\n"
            f"  룰북: 승률 {req.win_rate*100:.0f}%, fitness {req.fitness:.1f}\n"
            f"  시장: {req.market_regime} (score {req.market_score:.0f}, "
            f"buy_mult ×{req.buy_multiplier:.2f})\n"
            f"  섹터 강도: {req.sector_score:.0f}/100\n"
            f"\n"
            f"🎯 *목표/손절*\n"
            f"  목표가: {req.target_price:,.0f}원 ({target_pct:+.2f}%)\n"
            f"  손절가: {req.stop_price:,.0f}원 ({stop_pct:+.2f}%)\n"
            f"  최대 보유: {req.max_holding_days}일\n"
            f"\n"
            f"💰 *추가 매수 옵션*\n"
            f"{opt_str}\n"
            f"/reject — 거부\n"
            f"\n"
            f"⏱ 60초 내 미응답 시 재평가 후 진행\n"
            f"🔖 ID: `{req.request_id}`"
        )
        return self.send(text, parse_mode="Markdown")

    # ---------- Phase E: 보유 포지션 대시보드 ----------
    def send_position_dashboard(self, dashboard_text: str) -> bool:
        """이미 만들어진 대시보드 문자열을 그대로 전송 (Bot._cmd_positions와 공유)."""
        return self.send(dashboard_text, parse_mode="Markdown")

    # ---------- Phase E: regime 변경 알림 ----------
    def send_regime_change(self, prev: str, new: str, score: float, buy_mult: float) -> bool:
        text = (
            "📈 *시장 국면 변경*\n"
            f"  {prev} → *{new}*\n"
            f"  score: {score:.1f}\n"
            f"  buy_multiplier: ×{buy_mult:.2f}"
        )
        return self.send(text, parse_mode="Markdown")


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
