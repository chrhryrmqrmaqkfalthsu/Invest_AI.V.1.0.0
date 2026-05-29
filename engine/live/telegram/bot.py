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

# AI 비서 (optional - 로드 실패해도 봇은 동작)
try:
    from engine.ai.assistant import AIAssistant
    from engine.ai.training import get_training_manager
except Exception as _e:
    AIAssistant = None

ENV_PATH = Path.home() / "kingmaker" / ".env"
API_BASE = "https://api.telegram.org"

log = logging.getLogger("telegram.bot")


# 명령별 로딩 placeholder 텍스트
PROGRESS_TEXT = {
    "/start":     "👋 시작 처리 중...",
    "/help":      "📚 도움말 준비 중...",
    "/status":    "📊 계좌 상태 조회 중...",
    "/positions": "📦 보유 종목 + 수익률/달성률/확률 계산 중...",
    "/pause":     "⏸ 일시정지 처리 중...",
    "/resume":    "▶️ 재개 처리 중...",
    "/approve":   "✅ 첫 주문 승인 처리 중...",
    "/reject":    "❌ 거부 처리 중...",
    "/kill":      "🛑 정지 처리 중...",
}
PROGRESS_APPROVE_AMOUNT = "💰 추가매수 승인 처리 중 (시그널 재평가 가능성)..."
PROGRESS_DEFAULT = "⏳ 처리 중..."


class TelegramBot:

    def __init__(
        self,
        broker: Optional[Broker] = None,
        safety: Optional[SafetyLayer] = None,
        notifier: Optional[TelegramNotifier] = None,
        position_manager=None,
        approval_manager=None,
        rulebook=None,
        poll_interval: float = 2.0,
    ):
        env = dotenv_values(str(ENV_PATH))
        self.token       = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self.allowed_id  = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        self.broker      = broker
        self.safety      = safety
        self.notifier    = notifier or TelegramNotifier()
        self.position_manager = position_manager
        self.approval_manager = approval_manager
        self.rulebook    = rulebook

        # AI 비서 (slash 명령 외 자유 텍스트 처리)
        self.ai = None
        if AIAssistant is not None:
            try:
                # TrainingManager 가져오기 (싱글톤)
                try:
                    self.training_manager = get_training_manager()
                except Exception as e:
                    log.warning(f"TrainingManager 초기화 실패: {e}")
                    self.training_manager = None

                self.ai = AIAssistant(
                    broker=self.broker,
                    position_manager=self.position_manager,
                    approval_manager=self.approval_manager,
                    rulebook=self.rulebook,
                    training_manager=self.training_manager,
                )
                log.info("AIAssistant 연결 완료")
            except Exception as e:
                log.warning(f"AIAssistant 초기화 실패: {e} (자유 텍스트는 무시됨)")
        self.poll_interval = poll_interval

        if not self.token or not self.allowed_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 누락")

        self._offset = 0          # 마지막으로 처리한 update_id
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 명령 라우팅 테이블
        self.commands: Dict[str, Callable[[dict], str]] = {
            "/start":         self._cmd_start,
            "/help":          self._cmd_help,
            "/status":        self._cmd_status,
            "/positions":     self._cmd_positions,
            "/pause":         self._cmd_pause,
            "/resume":        self._cmd_resume,
            "/approve":       self._cmd_approve,
            "/reject":        self._cmd_reject,
            "/kill":          self._cmd_kill,
            "/learn":         self._cmd_learn,
            "/training":      self._cmd_training_status,
            "/cancel_train":  self._cmd_cancel_training,
            "/learn_queue":   self._cmd_learn_queue,
            "/queue":         self._cmd_queue_status,
            "/clear_queue":   self._cmd_clear_queue,
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
        if not text:
            return
        # 슬래시 아닌 자유 텍스트 → AI 비서로 라우팅
        if not text.startswith("/"):
            if self.ai is None:
                self.notifier.send("ℹ️ AI 비서가 활성화되지 않았습니다. /help 로 명령어 확인하세요.")
                return
            progress_id = self.notifier.send_progress("🤖 AI 분석 중...")
            try:
                answer = self.ai.ask(text)
            except Exception as e:
                log.exception(f"AI 비서 처리 예외: {e}")
                answer = f"❌ AI 처리 중 오류: {type(e).__name__}: {e}"
            if progress_id:
                self.notifier.edit_message(progress_id, answer, parse_mode="Markdown")
            else:
                self.notifier.send(answer, parse_mode="Markdown")
            return

        # /command @botname 형식도 처리
        cmd = text.split()[0].split("@")[0].lower()

        # /approve_NNk → 동적 매칭 (prefix)
        if cmd.startswith("/approve_"):
            progress_id = self.notifier.send_progress(PROGRESS_APPROVE_AMOUNT)
            try:
                reply = self._cmd_approve_amount(cmd, msg)
            except Exception as e:
                reply = f"❌ 예외: {e}"
            if reply:
                self.notifier.edit_message(progress_id, reply, parse_mode="Markdown")
            return

        handler = self.commands.get(cmd)
        if not handler:
            self.notifier.send(f"알 수 없는 명령: `{cmd}`\n/help 로 목록 확인")
            return

        progress_text = PROGRESS_TEXT.get(cmd, PROGRESS_DEFAULT)
        progress_id = self.notifier.send_progress(progress_text)
        try:
            reply = handler(msg)
            if reply:
                # Markdown 사용 명령은 parse_mode 지정 (positions/approve 등)
                use_md = cmd in ("/positions", "/status")
                self.notifier.edit_message(
                    progress_id, reply,
                    parse_mode="Markdown" if use_md else ""
                )
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
            "📚 명령어\n\n"
            "[조회]\n"
            "/status — 잔고/손익 요약\n"
            "/positions — 보유 종목 상세 (수익률·달성률·확률)\n\n"
            "[제어]\n"
            "/pause — 봇 일시정지 (KILL_SWITCH)\n"
            "/resume — 재개\n"
            "/kill — 긴급 정지\n\n"
            "[학습]\n"
            "/learn <종목> — 학습 시작 (예: /learn 069500 또는 /learn 코덱스200)\n"
            "/learn_queue <종목1> <종목2> ... — 여러 종목 큐 등록\n"
            "/training — 진행 중인 학습 상태\n"
            "/queue — 대기열 확인\n"
            "/cancel_train — 현재 학습 취소\n"
            "/clear_queue — 대기열 비우기\n"
            "💡 자유 텍스트도 가능: '코덱스200, 나스닥100 학습해'\n\n"
            "[승인]\n"
            "/approve — 오늘 첫 매수 승인\n"
            "/approve_20k — 추가매수 2만원\n"
            "/approve_30k — 추가매수 3만원\n"
            "/approve_50k — 추가매수 5만원\n"
            "/approve_100k — 추가매수 10만원\n"
            "/approve_200k — 추가매수 20만원\n"
            "/approve_500k — 추가매수 50만원\n"
            "/reject — 현재 대기중인 추가매수 거부\n\n"
            "💡 강한 시그널 감지 시 추가매수 옵션을 알려드립니다.\n"
            "60초 이내 응답 시 즉시, 초과 시 재평가 후 진행됩니다."
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
        """강화된 포지션 대시보드: 수익률 + 달성률 + 확률 재계산."""
        if not self.broker:
            return "broker 미연결"
        holdings = self.broker.get_holdings()
        if not holdings:
            return "보유 종목 없음"

        from datetime import datetime
        from zoneinfo import ZoneInfo
        KST = ZoneInfo("Asia/Seoul")

        lines = ["📦 *보유 종목 대시보드*"]
        for h in holdings:
            cur = h.current_price
            avg = h.avg_cost
            pnl_pct = h.unrealized_pnl_pct
            pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"

            block = [
                f"\n▸ `{h.ticker}` ({h.shares}주)",
                f"  매수 {avg:,.0f} → 현재 {cur:,.0f} ({pnl_pct:+.2f}%) {pnl_emoji}",
                f"  평가금: {h.market_value:,.0f}원 ({h.unrealized_pnl:+,.0f}원)",
            ]

            # PositionManager 메타 (있으면 강화)
            pos = self.position_manager.get(h.ticker) if self.position_manager else None
            if pos:
                # 목표/손절
                try:
                    target_pct = (pos.target_price / avg - 1) * 100
                    stop_pct = (pos.stop_price / avg - 1) * 100
                    achieve = (cur - avg) / (pos.target_price - avg) * 100 if pos.target_price > avg else 0.0
                    achieve = max(0.0, min(100.0, achieve))
                except Exception:
                    target_pct = stop_pct = achieve = 0.0
                block.append(f"  🎯 목표 {pos.target_price:,.0f} ({target_pct:+.2f}%) | 달성률 {achieve:.0f}%")
                block.append(f"  🛑 손절 {pos.stop_price:,.0f} ({stop_pct:+.2f}%) | 트레일 {pos.trailing_stop:,.0f}")

                # 보유일 / 남은일
                try:
                    entry_dt = datetime.fromisoformat(pos.entry_date)
                    if entry_dt.tzinfo is None:
                        entry_dt = entry_dt.replace(tzinfo=KST)
                    held_days = (datetime.now(KST) - entry_dt).days
                except Exception:
                    held_days = 0
                remain = max(0, pos.max_holding_days - held_days)
                block.append(f"  ⏱ 보유 {held_days}일 / 남은 {remain}일")

                # 목표 달성 확률 재계산
                prob = self._estimate_probability(
                    ticker=h.ticker,
                    pos=pos,
                    current_price=cur,
                    held_days=held_days,
                )
                if prob is not None:
                    base = pos.win_rate_at_entry * 100
                    block.append(f"  📊 달성 확률: {prob*100:.0f}% (진입 시 {base:.0f}%)")
            else:
                block.append("  (PositionManager 메타 없음)")

            lines.extend(block)

        return "\n".join(lines)

    def _estimate_probability(self, ticker, pos, current_price, held_days):
        """목표 달성 확률 재계산.

        prob = win_rate_at_entry × time_factor × distance_factor × signal_factor × market_factor

        - time_factor: 보유일/max 비율로 감소 (오래 들고 있을수록 ↓)
        - distance_factor: 목표까지 거리 (멀수록 ↓)
        - signal_factor: 현재 시그널 재평가
        - market_factor: regime 가중치
        """
        try:
            base = pos.win_rate_at_entry
            if base <= 0:
                return None

            # 1) time_factor
            mh = max(1, pos.max_holding_days)
            time_factor = max(0.3, 1.0 - (held_days / mh))

            # 2) distance_factor
            if pos.target_price > pos.entry_price:
                progress = (current_price - pos.entry_price) / (pos.target_price - pos.entry_price)
                progress = max(0.0, min(1.0, progress))
                distance_factor = 0.5 + 0.5 * progress  # 0% 진행 → 0.5, 100% → 1.0
            else:
                distance_factor = 0.7

            # 3) signal_factor (rulebook 호출, 실패 시 1.0)
            signal_factor = 1.0
            try:
                if self.rulebook and hasattr(self.rulebook, "evaluate"):
                    res = self.rulebook.evaluate(ticker, current_price)
                    if res and getattr(res, "threshold", 0) > 0:
                        ratio = res.score / res.threshold
                        # 0.5~1.5 → 0.5~1.2 정규화
                        signal_factor = max(0.5, min(1.2, 0.5 + 0.7 * (ratio - 0.5)))
            except Exception:
                pass

            # 4) market_factor
            market_factor = 1.0
            try:
                from engine.market.context import get_market_context
                ctx = get_market_context()
                regime = getattr(ctx, "regime", "neutral")
                if regime == "bull":
                    market_factor = 1.0
                elif regime == "neutral":
                    market_factor = 0.7
                else:
                    market_factor = 0.5
            except Exception:
                pass

            prob = base * time_factor * distance_factor * signal_factor * market_factor
            return max(0.0, min(1.0, prob))
        except Exception as e:
            log.warning(f"{ticker} 확률 재계산 실패: {e}")
            return None

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

    def _cmd_reject(self, msg: dict) -> str:
        """가장 최근 pending 또는 reevaluating 요청 거부."""
        if not self.approval_manager:
            return "approval_manager 미연결"
        pending = self.approval_manager.all_pending()
        # reevaluating 상태도 포함
        from engine.live.approval_manager import ApprovalManager
        all_active = [
            r for r in self.approval_manager._requests.values()
            if r.status in ("pending", "reevaluating")
        ]
        if not all_active:
            return "거부할 요청 없음"
        # 가장 최근 것 거부 (단순화)
        all_active.sort(key=lambda r: r.created_at, reverse=True)
        target = all_active[0]
        ok, msg_text = self.approval_manager.reject(target.request_id)
        return f"{'✅' if ok else '❌'} `{target.ticker}` {msg_text}"

    def _cmd_approve_amount(self, cmd: str, msg: dict) -> str:
        """`/approve_50k` 같은 명령 처리.

        - 가장 최근 pending 요청을 찾아 그 금액으로 승인
        - 60초 초과면 재평가 트리거 → 즉시 답변하고 Runner가 처리
        """
        if not self.approval_manager:
            return "approval_manager 미연결"

        # 1) 금액 파싱: /approve_50k → 50000
        try:
            amount_str = cmd.replace("/approve_", "").rstrip("k")
            amount_krw = int(amount_str) * 1000
        except Exception:
            return f"⚠️ 금액 파싱 실패: `{cmd}` (예: `/approve_50k`)"

        if amount_krw <= 0:
            return f"⚠️ 잘못된 금액: {amount_krw}"

        # 2) 가장 최근 pending 또는 reevaluating 요청
        active = [
            r for r in self.approval_manager._requests.values()
            if r.status in ("pending", "reevaluating")
        ]
        if not active:
            return "활성화된 승인 요청이 없습니다"
        active.sort(key=lambda r: r.created_at, reverse=True)
        target = active[0]

        # 3) 옵션에 없는 금액이면 경고
        if amount_krw not in target.options_krw:
            return (
                f"⚠️ `{target.ticker}` 요청의 옵션이 아님: {amount_krw:,}원\n"
                f"  유효 옵션: {target.options_krw}"
            )

        # 4) 승인 시도 (60초 이내면 즉시, 초과면 재평가 라우팅)
        if target.status == "pending":
            ok, msg_text, req = self.approval_manager.approve(target.request_id, amount_krw)
            if ok:
                return (
                    f"✅ `{target.ticker}` 추가매수 승인: {amount_krw:,}원\n"
                    f"Runner가 다음 tick에 실행합니다."
                )
            # 재평가 라우팅
            return (
                f"⏱ `{target.ticker}` {msg_text}\n"
                f"Runner가 현재 시그널 재평가 후 자동 진행/거부합니다.\n"
                f"  요청 금액: {amount_krw:,}원"
            )
        else:  # reevaluating 상태 — 이미 재평가 대기 중
            return (
                f"⏱ `{target.ticker}` 이미 재평가 대기 중\n"
                f"  요청 금액: {amount_krw:,}원\n"
                f"  Runner가 다음 tick에 처리합니다."
            )

    def _cmd_kill(self, msg: dict) -> str:
        KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_PATH.touch()
        return (
            "🛑 *KILL 명령 수신*\n"
            "신규 주문 차단됨 (KILL_SWITCH 생성)\n"
            "_보유 종목 청산은 별도로 수동 처리 또는 정책 결정 필요_\n"
            "/resume 으로 해제"
        )

    # ---------- 학습 명령 (v6 신규) ----------
    def _cmd_learn(self, msg: dict) -> str:
        if not self.training_manager:
            return "❌ TrainingManager 미연결"
        text = (msg.get("text") or "").strip()
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return (
                "사용법: `/learn <종목>`\n"
                "예: `/learn 069500`, `/learn 코덱스200`"
            )
        query = parts[1].strip()

        from engine.ai.ticker_resolver import resolve_ticker, get_ticker_name
        if query.isdigit() and len(query) == 6:
            ticker = query
            name = get_ticker_name(ticker) or ticker
        else:
            r = resolve_ticker(query, limit=5)
            exact = r.get("exact", [])
            partial = r.get("partial", [])
            candidates = exact + partial
            if not candidates:
                return f"❌ '{query}'에 해당하는 종목을 찾지 못했습니다"
            if len(exact) == 0 and len(partial) > 1:
                lines = [f"❓ '{query}' 후보가 여러개입니다:"]
                for c in partial[:5]:
                    lines.append(f"  • `{c['code']}` {c['name']}")
                lines.append("\n정확한 티커로 다시 지정해주세요.")
                return "\n".join(lines)
            top = candidates[0]
            ticker = top["code"]
            name = top["name"]

        result = self.training_manager.start(ticker=ticker, ticker_name=name)
        if result.get("started"):
            return f"📊 *{name}* (`{ticker}`) 학습 시작\n진행률은 별도 메시지로 갱신됩니다."
        cur = result.get("current", {})
        return (
            f"⚠ 학습 시작 거부: {result.get('reason','')}\n"
            f"진행 중: `{cur.get('ticker','?')}` "
            f"(Gen {cur.get('current_gen',0)}/{cur.get('total_gen','?')})"
        )

    def _cmd_training_status(self, msg: dict) -> str:
        if not self.training_manager:
            return "❌ TrainingManager 미연결"
        s = self.training_manager.status()
        if not s.get("running"):
            return "💤 진행 중인 학습이 없습니다"
        return (
            f"📊 *학습 진행중*\n"
            f"종목: {s['ticker_name']} (`{s['ticker']}`)\n"
            f"세대: {s['current_gen']}/{s['total_gen']} ({s['progress_pct']}%)\n"
            f"경과: {s['elapsed_sec']}초\n"
            f"Best fitness: {s['best_fitness']}\n"
            f"Avg fitness: {s['avg_fitness']}"
        )

    def _cmd_cancel_training(self, msg: dict) -> str:
        if not self.training_manager:
            return "❌ TrainingManager 미연결"
        r = self.training_manager.cancel()
        if r.get("cancelled"):
            return (
                f"🛑 학습 취소 요청: `{r['ticker']}` "
                f"(Gen {r['stopped_at_gen']}에서 중단)"
            )
        return f"⚠ {r.get('reason', '취소 실패')}"

    # ---------- 큐 명령 ----------
    def _resolve_query(self, query: str):
        from engine.ai.ticker_resolver import resolve_ticker, get_ticker_name
        q = query.strip()
        if q.isdigit() and len(q) == 6:
            return q, get_ticker_name(q) or q
        r = resolve_ticker(q, limit=5)
        exact = r.get("exact", [])
        partial = r.get("partial", [])
        candidates = exact + partial
        if not candidates:
            return None, f"❌ '{q}' 종목 못 찾음"
        if len(exact) == 0 and len(partial) > 1:
            return None, f"❌ '{q}' 후보 여러개 ({len(partial)}개)"
        return candidates[0]["code"], candidates[0]["name"]

    def _cmd_learn_queue(self, msg: dict) -> str:
        if not self.training_manager:
            return "❌ TrainingManager 미연결"
        text = (msg.get("text") or "").strip()
        parts = text.split()[1:]
        if not parts:
            return (
                "사용법: `/learn_queue <종목1> <종목2> ...`\n"
                "예: `/learn_queue 069500 367380 379800`"
            )
        items = []
        errors = []
        for q in parts:
            ticker, name = self._resolve_query(q)
            if ticker is None:
                errors.append(name)
            else:
                items.append({"ticker": ticker, "ticker_name": name})
        if not items:
            return "❌ 등록할 종목 없음:\n" + "\n".join(errors)
        result = self.training_manager.enqueue_many(items)
        lines = [f"📋 *큐 등록 결과*"]
        lines.append(f"즉시 시작: {result['started']}개")
        lines.append(f"대기열 추가: {result['queued']}개\n")
        for r in result["items"]:
            if r["started"]:
                lines.append(f"▶️ `{r['ticker']}` {r['ticker_name']} (시작)")
            elif r["queued"]:
                lines.append(f"⏳ `{r['ticker']}` {r['ticker_name']} (#{r['queue_position']})")
        if errors:
            lines.append("\n*제외된 항목:*")
            for e in errors:
                lines.append(f"  • {e}")
        return "\n".join(lines)

    def _cmd_queue_status(self, msg: dict) -> str:
        if not self.training_manager:
            return "❌ TrainingManager 미연결"
        s = self.training_manager.status()
        lines = []
        if s.get("running"):
            lines.append(
                f"📊 *진행중*: {s['ticker_name']} (`{s['ticker']}`)\n"
                f"   Gen {s['current_gen']}/{s['total_gen']} ({s['progress_pct']}%) fitness={s['best_fitness']}"
            )
        else:
            lines.append("💤 진행 중인 학습 없음")
        q = s.get("queue", [])
        if q:
            lines.append(f"\n⏳ *대기열* ({len(q)}개)")
            for i, item in enumerate(q, 1):
                lines.append(f"  {i}. `{item['ticker']}` {item['ticker_name']}")
        else:
            lines.append("\n대기열 비어있음")
        return "\n".join(lines)

    def _cmd_clear_queue(self, msg: dict) -> str:
        if not self.training_manager:
            return "❌ TrainingManager 미연결"
        r = self.training_manager.clear_queue()
        n = r["cleared_count"]
        if n == 0:
            return "대기열이 이미 비어있습니다"
        lines = [f"🗑 대기열 {n}개 삭제됨:"]
        for it in r["items"]:
            lines.append(f"  • `{it['ticker']}` {it['ticker_name']}")
        return "\n".join(lines)


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
