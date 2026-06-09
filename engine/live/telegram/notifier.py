"""
TelegramNotifier - 단방향 알림 전송 (봇 → 사용자)
- 외부 라이브러리 없이 requests만 사용
- TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 필수
- 토큰 없으면 silent fail (운영 중 알림 실패로 봇이 죽지 않게)
- 실거래 알림은 USD notional 기준으로 표준 이벤트/레벨/rate-limit를 적용
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Optional
from dotenv import dotenv_values

import requests

ENV_PATH = Path.home() / "kingmaker" / ".env"
API_BASE = "https://api.telegram.org"

log = logging.getLogger("telegram.notifier")


class TelegramNotifier:

    def __init__(
        self,
        env_path: Optional[str] = None,
        silent_on_error: bool = True,
        default_rate_limit_seconds: int = 600,
    ):
        env = dotenv_values(env_path or str(ENV_PATH))
        self.token   = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        self.chat_id = (env.get("TELEGRAM_CHAT_ID") or "").strip()
        self.silent_on_error = silent_on_error
        self.default_rate_limit_seconds = max(0, int(default_rate_limit_seconds or 0))
        self._last_event_sent_at: dict[str, float] = {}

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
                log.debug(f"edit_message {res.status_code}: {res.text[:100]}")
                return False
            return True
        except Exception as e:
            log.warning(f"edit_message 실패: {e}")
            return False

    # ---------- 표준 이벤트 알림 공통 헬퍼 ----------
    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value) or "")

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return default

    @staticmethod
    def _fmt_usd(value: Any, signed: bool = False) -> str:
        amount = TelegramNotifier._float(value)
        if signed and amount > 0:
            return f"+${amount:,.2f}"
        if amount < 0:
            return f"-${abs(amount):,.2f}"
        return f"${amount:,.2f}"

    @staticmethod
    def _fmt_pct(value: Any) -> str:
        return f"{TelegramNotifier._float(value):.1f}%"

    @staticmethod
    def _fmt_signed_pct(value: Any) -> str:
        return f"{TelegramNotifier._float(value):+.1f}%"

    @staticmethod
    def _fmt_shares(value: Any) -> str:
        shares = TelegramNotifier._float(value)
        if abs(shares - round(shares)) < 1e-6:
            return f"{int(round(shares))}주"
        return f"{shares:.6f}".rstrip("0").rstrip(".") + "주"

    @staticmethod
    def _level_prefix(level: str) -> str:
        lvl = str(level or "INFO").upper()
        return {
            "INFO": "ℹ️ INFO",
            "WARN": "⚠️ WARN",
            "WARNING": "⚠️ WARN",
            "CRITICAL": "🚨 CRITICAL",
            "ERROR": "🚨 CRITICAL",
        }.get(lvl, f"ℹ️ {lvl}")

    @staticmethod
    def _pct_value(value: Any) -> float:
        """Accept either 0.8 or 80.0 and return percent scale."""
        v = TelegramNotifier._float(value)
        if 0 < v <= 1.0:
            return v * 100.0
        return v

    @staticmethod
    def _get_path_value(source: Any, name: str) -> Any:
        cur = source
        for part in str(name).split("."):
            if cur is None:
                return None
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = getattr(cur, part, None)
        return cur

    def _first_value(self, source: Any, names: tuple[str, ...], default: Any = None) -> Any:
        for name in names:
            value = self._get_path_value(source, name)
            if value not in (None, ""):
                return value
        return default

    def _metric_value(self, explicit: Any, rulebook: Any, names: tuple[str, ...], default: Any = None) -> Any:
        if explicit not in (None, ""):
            return explicit
        return self._first_value(rulebook, names, default)

    def _rate_limited(self, event_key: str, rate_limit_seconds: Optional[int], bypass: bool = False) -> bool:
        if bypass or not event_key:
            return False
        limit = self.default_rate_limit_seconds if rate_limit_seconds is None else int(rate_limit_seconds or 0)
        if limit <= 0:
            return False
        now = time.time()
        last = self._last_event_sent_at.get(event_key, 0.0)
        if now - last < limit:
            return True
        self._last_event_sent_at[event_key] = now
        return False

    def _send_event(
        self,
        *,
        title: str,
        lines: list[str],
        level: str = "INFO",
        event_key: str = "",
        rate_limit_seconds: Optional[int] = None,
        parse_mode: str = "",
    ) -> bool:
        lvl = str(level or "INFO").upper()
        if self._rate_limited(event_key, rate_limit_seconds, bypass=lvl in {"CRITICAL", "ERROR"}):
            log.info("Telegram event rate-limited: %s", event_key)
            return False
        body = "\n".join(str(x) for x in lines if str(x).strip())
        text = f"{self._level_prefix(lvl)} | {title}"
        if body:
            text += f"\n{body}"
        return self.send(text[:3900], parse_mode=parse_mode)

    # ---------- 진입 사유/통계/청산 계획 포맷 ----------
    @staticmethod
    def _clean_reason_token(reason: Any) -> str:
        text = str(reason or "").strip()
        text = re.sub(r"\([^)]*\)", "", text).strip()
        return text

    def _extract_signal_reasons(self, signal_result: Any = None, raw_reason: str = "") -> list[str]:
        reasons = list(getattr(signal_result, "reasons", []) or []) if signal_result is not None else []
        if reasons:
            return [str(r) for r in reasons if str(r).strip()]
        text = str(raw_reason or getattr(signal_result, "reason", "") or "")
        matched = re.search(r"reasons=\[([^\]]*)\]", text)
        if matched:
            return [x.strip() for x in matched.group(1).split(",") if x.strip()]
        return [text] if text.strip() else []

    def _humanize_entry_reasons(self, signal_result: Any = None, raw_reason: str = "", max_items: int = 3) -> str:
        raw_reasons = self._extract_signal_reasons(signal_result=signal_result, raw_reason=raw_reason)
        mapped: list[str] = []
        for raw in raw_reasons:
            token = self._clean_reason_token(raw)
            human = ""
            if "정배열" in token:
                human = "상승 추세"
            elif "MACD" in token or "크로스" in token:
                human = "상승 전환 신호"
            elif "RSI" in token:
                human = "아직 과열 이전"
            elif "BB" in token or "볼린저" in token:
                human = "눌림목 매수 구간"
            elif "거래량" in token:
                human = "거래량 증가"
            elif "전체톤" in token:
                human = "개별 뉴스 톤 보조"
            elif "토픽뉴스" in token:
                human = "토픽 뉴스 보조"
            elif "이벤트반응" in token:
                human = "이벤트 반응 보조"
            elif "폭락매수" in token:
                human = "급락 후 반등 후보"
            elif "시장보정" in token:
                human = "시장 환경 보정"
            if human and human not in mapped:
                mapped.append(human)
        if not mapped:
            return "진입 조건 충족"
        return " + ".join(mapped[:max_items])

    def _win_rate_value(self, rulebook: Any = None, recent_win_rate: Any = None) -> float:
        value = self._metric_value(
            recent_win_rate,
            rulebook,
            ("recent5_win_rate", "recent_win_rate", "last5_win_rate", "win_rate", "oos_metrics.win_rate"),
            0.0,
        )
        return self._pct_value(value)

    def _recent_trade_returns_line(
        self,
        rulebook: Any = None,
        recent_trade_returns_pct: Any = None,
        recent_win_rate: Any = None,
    ) -> str:
        returns = self._metric_value(
            recent_trade_returns_pct,
            rulebook,
            (
                "recent5_returns_pct",
                "recent5_trade_returns_pct",
                "last5_returns_pct",
                "last5_trade_returns_pct",
                "recent_trade_returns_pct",
                "recent_returns_pct",
                "pnl_history_pct",
            ),
            None,
        )
        win_rate = self._win_rate_value(rulebook=rulebook, recent_win_rate=recent_win_rate)
        suffix = f" (승률 {win_rate:.0f}%)" if win_rate > 0 else ""
        if isinstance(returns, str):
            parts = [x.strip() for x in re.split(r"[,\s]+", returns) if x.strip()]
        elif isinstance(returns, (list, tuple)):
            parts = list(returns)[-5:]
        else:
            parts = []
        if parts:
            values = " ".join(self._fmt_signed_pct(x) for x in parts)
            return f"최근 5거래: {values}{suffix}"
        if suffix:
            return f"최근 5거래: 수익률 기록 없음{suffix}"
        return "최근 5거래: 수익률 기록 없음"

    def _trailing_line(self, rulebook: Any = None, trailing_activation_profit_pct: Any = None) -> str:
        activation = self._metric_value(
            trailing_activation_profit_pct,
            rulebook,
            ("trailing_activation_profit_pct",),
            0.0,
        )
        pct = self._float(activation)
        if pct <= 0:
            return "트레일링: 비활성"
        return f"트레일링: +{pct:g}% 도달 시 활성"

    def _sell_omen_line(self, rulebook: Any = None, sell_omen_threshold: Any = None) -> str:
        enabled = True
        threshold = sell_omen_threshold
        if rulebook is not None:
            enabled = bool(self._first_value(rulebook, ("sell_omen_enabled",), True))
            if threshold in (None, ""):
                threshold = self._first_value(rulebook, ("sell_omen_threshold",), None)
        if not enabled:
            return "sell_omen: 비활성"
        th = self._float(threshold)
        if th <= 0:
            return "sell_omen: 기준값 없음"
        return f"sell_omen: 위험점수 {th:g}↑ 시 청산"

    def _price_plan_line(
        self,
        label: str,
        entry_price: float,
        price_value: Any = None,
        pct_value: Any = None,
        fallback_text: str = "",
    ) -> str:
        target_price = self._float(price_value)
        pct = self._float(pct_value)
        if target_price <= 0 and entry_price > 0 and pct:
            target_price = entry_price * (1.0 + pct / 100.0)
        if target_price > 0:
            if entry_price > 0:
                pct = (target_price / entry_price - 1.0) * 100.0
                return f"{label}: {self._fmt_usd(target_price)} ({pct:+.1f}%)"
            return f"{label}: {self._fmt_usd(target_price)}"
        return f"{label}: {fallback_text}" if fallback_text else ""

    def _stat_line(self, label: str, value: Any, *, pct: bool = False, signed: bool = False, suffix: str = "") -> str:
        if value in (None, ""):
            return f"{label}: 데이터 없음"
        if pct:
            v = self._pct_value(value)
            text = f"{v:+.1f}%" if signed else f"{v:.0f}%"
        else:
            text = f"{self._float(value):.2f}".rstrip("0").rstrip(".")
        return f"{label}: {text}{suffix}"

    # ---------- 포맷된 알림 ----------
    def send_order(self, order) -> bool:
        """주문 체결/접수 알림. 기존 호출 호환 유지, USD notional 표기."""
        side_raw = self._enum_value(getattr(order, "side", "")).lower()
        side_kr = "🟢 BUY" if side_raw.endswith("buy") else "🔴 SELL"
        status_raw = self._enum_value(getattr(order, "status", "")).lower().split(".")[-1]
        status_emoji = {
            "pending":   "⏳",
            "filled":    "✅",
            "partial":   "🟡",
            "cancelled": "⚪",
            "rejected":  "❌",
            "failed":    "❌",
        }.get(status_raw, "❓")

        requested_shares = self._float(getattr(order, "shares", 0.0))
        requested_price = self._float(getattr(order, "price", 0.0))
        filled_shares = self._float(getattr(order, "filled_shares", 0.0))
        filled_avg_price = self._float(getattr(order, "filled_avg_price", 0.0))
        requested_notional = requested_shares * requested_price if requested_price > 0 else 0.0
        filled_notional = filled_shares * filled_avg_price if filled_shares > 0 and filled_avg_price > 0 else 0.0
        effective_price = filled_avg_price or requested_price
        effective_notional = filled_notional or requested_notional

        lines = [
            f"종목: {getattr(order, 'ticker', '')}",
            f"수량: {self._fmt_shares(requested_shares)}",
            f"요청가/기준가: {self._fmt_usd(effective_price)}",
            f"예상/체결 금액: {self._fmt_usd(effective_notional)}",
            f"상태: {status_emoji} {self._enum_value(getattr(order, 'status', ''))}",
        ]
        if filled_shares > 0:
            lines.append(f"체결: {self._fmt_shares(filled_shares)} @ {self._fmt_usd(filled_avg_price)}")
        commission = self._float(getattr(order, "commission", 0.0))
        if commission:
            lines.append(f"수수료: {self._fmt_usd(commission)}")
        message = str(getattr(order, "message", "") or "").strip()
        if message:
            lines.append(f"메시지: {message[:180]}")
        level = "WARN" if status_raw in {"pending", "partial"} else "CRITICAL" if status_raw in {"rejected", "failed"} else "INFO"
        return self._send_event(
            title=f"{side_kr} 주문",
            lines=lines,
            level=level,
            event_key=f"order:{getattr(order, 'ticker', '')}:{side_raw}:{status_raw}",
            rate_limit_seconds=0 if status_raw in {"filled", "rejected", "failed"} else 300,
        )

    def send_trade_entry(
        self,
        order=None,
        *,
        ticker: str = "",
        shares: float = 0.0,
        price: float = 0.0,
        notional: float = 0.0,
        signal_result: Any = None,
        raw_reason: str = "",
        rulebook: Any = None,
        recent_win_rate: Any = None,
        recent_trade_returns_pct: Any = None,
        trailing_activation_profit_pct: Any = None,
        sell_omen_threshold: Any = None,
        stop_price: Any = None,
        target_price: Any = None,
        stop_pct: Any = None,
        target_pct: Any = None,
        expected_holding_days: Any = None,
        expectancy_pct: Any = None,
        profit_factor: Any = None,
    ) -> bool:
        """실거래 매수 체결 알림. INFO prefix 없이 합의한 정본 포맷으로 전송한다."""
        ticker = ticker or str(getattr(order, "ticker", "") or getattr(signal_result, "ticker", "") or "")
        shares = self._float(shares) or self._float(getattr(order, "filled_shares", 0.0)) or self._float(getattr(order, "shares", 0.0))
        price = self._float(price) or self._float(getattr(order, "filled_avg_price", 0.0)) or self._float(getattr(order, "price", 0.0)) or self._float(getattr(signal_result, "price", 0.0))
        notional = self._float(notional) or (shares * price if shares and price else 0.0)
        reason_text = self._humanize_entry_reasons(signal_result=signal_result, raw_reason=raw_reason)

        exp_value = self._metric_value(expectancy_pct, rulebook, ("expectancy_pct", "oos_metrics.expectancy_pct"), None)
        wr_value = self._metric_value(recent_win_rate, rulebook, ("win_rate", "oos_metrics.win_rate"), None)
        pf_value = self._metric_value(profit_factor, rulebook, ("profit_factor", "pf", "oos_metrics.profit_factor"), None)
        recent_line = self._recent_trade_returns_line(
            rulebook=rulebook,
            recent_trade_returns_pct=recent_trade_returns_pct,
            recent_win_rate=recent_win_rate,
        )

        rb_stop_pct = self._first_value(rulebook, ("stop_loss_pct", "stop_pct"), None)
        rb_target_pct = self._first_value(rulebook, ("take_profit_pct", "target_profit_pct", "target_pct"), None)
        stop_line = self._price_plan_line(
            "손절",
            price,
            price_value=stop_price,
            pct_value=stop_pct if stop_pct not in (None, "") else rb_stop_pct,
            fallback_text=(
                f"ATR×{self._float(self._first_value(rulebook, ('stop_loss_atr',), 0.0)):g} 기준"
                if self._float(self._first_value(rulebook, ("stop_loss_atr",), 0.0)) > 0 else "데이터 없음"
            ),
        )
        target_line = self._price_plan_line(
            "익절",
            price,
            price_value=target_price,
            pct_value=target_pct if target_pct not in (None, "") else rb_target_pct,
            fallback_text=(
                f"ATR×{self._float(self._first_value(rulebook, ('take_profit_atr',), 0.0)):g} 기준"
                if self._float(self._first_value(rulebook, ("take_profit_atr",), 0.0)) > 0 else "데이터 없음"
            ),
        )
        holding_days = self._metric_value(
            expected_holding_days,
            rulebook,
            ("expected_holding_days", "avg_holding_days", "max_holding_days"),
            None,
        )
        holding_line = ""
        if holding_days not in (None, ""):
            holding_line = f"예상 보유: ~{self._float(holding_days):g}일"

        lines = [
            f"🟢 매수 체결 — {ticker}",
            f"수량/금액: {self._fmt_shares(shares)} / {self._fmt_usd(notional)}",
            f"진입가: {self._fmt_usd(price)}",
            f"진입사유: {reason_text}",
            "",
            "📋 해당 룰북 통계",
            self._stat_line("기대수익", exp_value, pct=True, signed=True),
            self._stat_line("승률", wr_value, pct=True, suffix=" (OOS 2024~)"),
            self._stat_line("손익비(PF)", pf_value),
            recent_line,
            "",
            "🎯 청산 계획",
            stop_line,
            target_line,
            self._trailing_line(rulebook=rulebook, trailing_activation_profit_pct=trailing_activation_profit_pct),
            self._sell_omen_line(rulebook=rulebook, sell_omen_threshold=sell_omen_threshold),
            holding_line,
        ]
        return self.send("\n".join(str(x) for x in lines if str(x).strip())[:3900])

    def send_error(self, message: str) -> bool:
        return self._send_event(
            title="오류",
            lines=[str(message)[:700]],
            level="CRITICAL",
            event_key=f"error:{str(message)[:120]}",
            rate_limit_seconds=300,
        )

    def send_info(self, message: str) -> bool:
        return self._send_event(title="정보", lines=[str(message)], level="INFO", event_key=f"info:{str(message)[:120]}")

    def send_safety_block(self, code: str, reason: str) -> bool:
        return self.send_order_rejected(
            code=code,
            reason=reason,
            level="WARN" if str(code).upper() not in {"DAILY_LOSS", "KILL_SWITCH", "COOLDOWN"} else "CRITICAL",
        )

    def send_order_rejected(
        self,
        *,
        code: str,
        reason: str,
        ticker: str = "",
        side: str = "",
        shares: float = 0.0,
        price: float = 0.0,
        purpose: str = "",
        level: str = "WARN",
        event_key: str = "",
    ) -> bool:
        notional = self._float(shares) * self._float(price)
        lines = [
            f"코드: {code or 'UNKNOWN'}",
            f"사유: {reason}",
        ]
        if ticker or side or purpose:
            lines.insert(0, f"주문: {side or '?'} {ticker or '?'} ({purpose or 'unknown'})")
        if shares or price:
            lines.append(f"요청: {self._fmt_shares(shares)} @ {self._fmt_usd(price)} = {self._fmt_usd(notional)}")
        return self._send_event(
            title="주문 차단/거부",
            lines=lines,
            level=level,
            event_key=event_key or f"order_rejected:{ticker}:{side}:{code}",
            rate_limit_seconds=300,
        )

    def send_risk_alert(
        self,
        *,
        realized_pnl_today: float,
        daily_loss_limit_usd: float,
        daily_loss_limit_pct: float = 0.0,
        total_value_usd: float = 0.0,
        consecutive_losses: int = 0,
        consecutive_loss_limit: int = 0,
        orders_today: int = 0,
        kill_until: str = "",
        cooldown_until: str = "",
        last_trade_pnl_usd: float = 0.0,
        ticker: str = "",
        exit_reason: str = "",
        event_key: str = "",
    ) -> bool:
        loss_today = max(0.0, -self._float(realized_pnl_today))
        abs_limit = max(0.0, self._float(daily_loss_limit_usd))
        abs_ratio = loss_today / abs_limit if abs_limit > 0 else 0.0
        pct_ratio = 0.0
        if total_value_usd > 0 and daily_loss_limit_pct > 0:
            pct_ratio = (loss_today / total_value_usd * 100.0) / daily_loss_limit_pct
        ratio = max(abs_ratio, pct_ratio)
        if kill_until or ratio >= 1.0:
            level, title = "CRITICAL", "일일 손실 한도 도달"
        elif cooldown_until or (consecutive_loss_limit > 0 and consecutive_losses >= consecutive_loss_limit):
            level, title = "CRITICAL", "연속 손실 쿨다운"
        elif ratio >= 0.9:
            level, title = "WARN", "일일 손실 한도 90% 근접"
        elif ratio >= 0.7:
            level, title = "WARN", "일일 손실 한도 70% 근접"
        else:
            level, title = "INFO", "실현손익 업데이트"

        pct_line = ""
        if total_value_usd > 0 and daily_loss_limit_pct > 0:
            cur_pct = loss_today / total_value_usd * 100.0
            pct_line = f"손실률: {cur_pct:.2f}% / 한도 {daily_loss_limit_pct:.2f}%"
        lines = [
            f"누적 손익: {self._fmt_usd(realized_pnl_today, signed=True)}",
            f"누적 손실: -{self._fmt_usd(loss_today)} / -{self._fmt_usd(abs_limit)} ({ratio * 100.0:.0f}%)",
            pct_line,
            f"연속 손실: {consecutive_losses}회 / 한도 {consecutive_loss_limit}회",
            f"오늘 주문: {orders_today}건",
        ]
        if last_trade_pnl_usd:
            trade = f"최근 청산 손익: {self._fmt_usd(last_trade_pnl_usd, signed=True)}"
            if ticker:
                trade += f" ({ticker})"
            if exit_reason:
                trade += f" / {exit_reason}"
            lines.append(trade)
        if kill_until:
            lines.append(f"신규 주문 차단 해제 예정: {kill_until}")
        if cooldown_until:
            lines.append(f"쿨다운 해제 예정: {cooldown_until}")
        if level == "CRITICAL":
            lines.append("→ 신규 매수 차단 상태를 반드시 확인하세요.")
        return self._send_event(
            title=title,
            lines=lines,
            level=level,
            event_key=event_key or f"risk:{title}",
            rate_limit_seconds=900,
        )

    def send_daily_summary(self, summary: dict) -> bool:
        cash = summary.get("cash_usd", summary.get("cash_notional", summary.get("cash_krw", 0)))
        total_value = summary.get("total_value_usd", summary.get("total_value", summary.get("total_value_krw", 0)))
        pnl = summary.get("realized_pnl_today_usd", summary.get("realized_pnl_today", 0))
        lines = [
            f"가용 현금: {self._fmt_usd(cash)}",
            f"총 평가금: {self._fmt_usd(total_value)}",
            f"오늘 손익: {self._fmt_usd(pnl, signed=True)}",
            f"오늘 주문: {summary.get('orders_today', 0)}건",
            f"보유 종목: {summary.get('holdings_count', 0)}개",
        ]
        return self._send_event(title="일일 요약", lines=lines, level="INFO", event_key="daily_summary", rate_limit_seconds=0)

    def send_system_alert(self, title: str, message: str, level: str = "WARN", event_key: str = "") -> bool:
        return self._send_event(title=title, lines=[message], level=level, event_key=event_key or f"system:{title}")

    # ---------- Phase E: 추가 매수 승인 요청 ----------
    def send_approval_request(self, req) -> bool:
        """ApprovalRequest 객체를 받아 강도별 차등 알림 발송."""
        emoji_map = {"weak": "🟡", "medium": "🟠", "strong": "🔴"}
        level_kr = {"weak": "약한", "medium": "중간", "strong": "강한"}
        emoji = emoji_map.get(req.strength, "🟡")
        level = level_kr.get(req.strength, req.strength)

        try:
            target_pct = (req.target_price / req.current_price - 1) * 100
            stop_pct = (req.stop_price / req.current_price - 1) * 100
        except Exception:
            target_pct = stop_pct = 0.0

        reasons = req.signal_reasons[:3] if req.signal_reasons else []
        reasons_str = "\n".join(f"    • {r}" for r in reasons) if reasons else "    • (근거 없음)"

        opt_lines = []
        for amount in req.options_krw:
            shares = amount / req.current_price if req.current_price > 0 else 0.0
            label = f"{int(amount // 1000)}k"
            opt_lines.append(f"/approve_{label} — {self._fmt_usd(amount)} (≈{self._fmt_shares(shares)})")
        opt_str = "\n".join(opt_lines)

        text = (
            f"{emoji} {level} BUY 시그널: {req.ticker}\n"
            f"현재가: {self._fmt_usd(req.current_price)}\n"
            f"\n"
            f"📊 분석 근거\n"
            f"  점수: {req.signal_score:.2f} / 임계 {req.signal_threshold:.2f} "
            f"(×{req.signal_score/req.signal_threshold:.2f})\n"
            f"{reasons_str}\n"
            f"  룰북: 승률 {req.win_rate*100:.0f}%, fitness {req.fitness:.1f}\n"
            f"  시장: {req.market_regime} (score {req.market_score:.0f}, "
            f"buy_mult ×{req.buy_multiplier:.2f})\n"
            f"  섹터 강도: {req.sector_score:.0f}/100\n"
            f"\n"
            f"🎯 목표/손절\n"
            f"  목표가: {self._fmt_usd(req.target_price)} ({target_pct:+.2f}%)\n"
            f"  손절가: {self._fmt_usd(req.stop_price)} ({stop_pct:+.2f}%)\n"
            f"  최대 보유: {req.max_holding_days}일\n"
            f"\n"
            f"💰 추가 매수 옵션\n"
            f"{opt_str}\n"
            f"/reject — 거부\n"
            f"\n"
            f"⏱ 60초 내 미응답 시 재평가 후 진행\n"
            f"🔖 ID: {req.request_id}"
        )
        return self.send(text)

    # ---------- Phase E: 보유 포지션 대시보드 ----------
    def send_position_dashboard(self, dashboard_text: str) -> bool:
        """이미 만들어진 대시보드 문자열을 그대로 전송 (Bot._cmd_positions와 공유)."""
        return self.send(dashboard_text, parse_mode="Markdown")

    # ---------- Phase E: regime 변경 알림 ----------
    def send_regime_change(self, prev: str, new: str, score: float, buy_mult: float) -> bool:
        lines = [
            f"국면: {prev} → {new}",
            f"score: {score:.1f}",
            f"buy_multiplier: ×{buy_mult:.2f}",
        ]
        return self._send_event(title="시장 국면 변경", lines=lines, level="INFO", event_key="regime_change", rate_limit_seconds=0)


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
    ok = n.send("🤖 Kingmaker 봇 연결 테스트\nTelegramNotifier 검증 중")
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n[2] 정보 메시지...")
    ok = n.send_info("이건 info 알림입니다")
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n[3] 차단 알림...")
    ok = n.send_safety_block("LIMIT_NOTIONAL", "주문금액 $15.00 > 한도 $10.00")
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n[4] 일일 요약...")
    ok = n.send_daily_summary({
        "cash_usd": 100.0, "total_value_usd": 125.61,
        "realized_pnl_today_usd": -3.50, "orders_today": 2, "holdings_count": 1,
    })
    print(f"  결과: {'✅' if ok else '❌'}")

    print("\n" + "=" * 50)
    print("✅ 검증 완료 — 휴대폰 텔레그램에서 4개 메시지 확인")
    print("=" * 50)
