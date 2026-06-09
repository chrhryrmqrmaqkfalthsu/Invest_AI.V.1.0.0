"""Daily live report builder/sender for Telegram.

장마감 일일 보고는 기존 TelegramNotifier transport를 그대로 쓰고,
시장 요약·계좌 현황·오늘 거래·보유 종목·리스크 상태를 한 메시지로 묶는다.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from engine.live.position_manager import TRADE_LOG_PATH
from engine.live.safety import state as safety_state_mod
from engine.market.context import get_market_context

KST = ZoneInfo("Asia/Seoul")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPER_AUDIT_PATH = PROJECT_ROOT / "data" / "_system" / "paper_trade_audit.jsonl"

log = logging.getLogger("daily_report")


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


def _fmt_pct(value: Any, signed: bool = True) -> str:
    v = _float(value)
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _is_today_kst(value: Any, today: str) -> bool:
    dt = _parse_dt(value)
    return bool(dt and dt.strftime("%Y-%m-%d") == today)


def _today_trade_log_rows(today: str, trade_log_path: Path = TRADE_LOG_PATH) -> list[dict[str, str]]:
    if not trade_log_path.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        with trade_log_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if _is_today_kst(row.get("exited_at") or row.get("date") or row.get("timestamp"), today):
                    rows.append(dict(row))
    except Exception as exc:
        log.warning("trade_log 읽기 실패: %s", exc)
    return rows


def _today_paper_audit_orders(today: str, audit_path: Path = PAPER_AUDIT_PATH) -> list[dict[str, Any]]:
    if not audit_path.exists():
        return []
    orders: list[dict[str, Any]] = []
    try:
        with audit_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = row.get("filled_at") or row.get("submitted_at") or row.get("audit_logged_at")
                if _is_today_kst(ts, today):
                    orders.append(row)
    except Exception as exc:
        log.warning("paper audit log 읽기 실패: %s", exc)
    return orders


def _market_mood(score: float, regime: str) -> str:
    reg = str(regime or "").lower()
    if score >= 70 or reg == "bull":
        return "양호 (위험 선호)"
    if score >= 40 or reg == "neutral":
        return "보통 (중립)"
    return "주의 (방어 우선)"


def _vix_label(vix: float) -> str:
    if vix < 15:
        return "안정"
    if vix < 25:
        return "보통"
    return "불안"


def _holding_days(position) -> str:
    entry_date = getattr(position, "entry_date", "") if position is not None else ""
    dt = _parse_dt(entry_date)
    if not dt:
        return "기록 없음"
    days = max(0, (datetime.now(KST).date() - dt.date()).days)
    return "오늘 진입" if days == 0 else f"{days}일째"


def _compact_trade_list(items: Iterable[str], limit: int = 4) -> str:
    rows = [x for x in items if x]
    if not rows:
        return "없음"
    if len(rows) <= limit:
        return ", ".join(rows)
    return ", ".join(rows[:limit]) + f" 외 {len(rows) - limit}건"


def _build_trade_summary(today: str) -> dict[str, Any]:
    orders = _today_paper_audit_orders(today)
    buy_orders = [o for o in orders if str(o.get("side", "")).lower() == "buy" and str(o.get("status", "")).lower() == "filled"]
    trade_rows = _today_trade_log_rows(today)

    buy_labels = []
    for o in buy_orders:
        ticker = str(o.get("ticker") or "?")
        notional = _float(o.get("notional"))
        if notional <= 0:
            notional = _float(o.get("filled_shares")) * _float(o.get("filled_avg_price") or o.get("fill_price"))
        buy_labels.append(f"{ticker} {_fmt_usd(notional)}")

    sell_labels = []
    for r in trade_rows:
        ticker = str(r.get("ticker") or "?")
        pnl = _float(r.get("pnl_usd", r.get("pnl_notional", r.get("pnl_krw", 0.0))))
        reason = str(r.get("exit_reason") or "청산")
        sell_labels.append(f"{ticker} {_fmt_usd(pnl, signed=True)} ({reason})")

    return {
        "buy_count": len(buy_orders),
        "sell_count": len(trade_rows),
        "buy_labels": buy_labels,
        "sell_labels": sell_labels,
        "trade_rows": trade_rows,
    }


def build_daily_report_text(runner, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(KST)).astimezone(KST)
    today = now.strftime("%Y-%m-%d")

    balance = runner.broker.get_balance()
    holdings = list(getattr(balance, "holdings", []) or [])
    position_map = {getattr(p, "ticker", ""): p for p in runner.position_manager.all()}
    safety_state = safety_state_mod.load()

    try:
        ctx = get_market_context()
        market_score = _float(getattr(ctx, "score", 50.0), 50.0)
        market_regime = str(getattr(ctx, "regime", "neutral") or "neutral")
        sp500_trend = _float(getattr(ctx, "sp500_trend_pct", 0.0), 0.0)
        vix = _float(getattr(ctx, "vix_level", 18.0), 18.0)
    except Exception as exc:
        log.warning("market context 조회 실패: %s", exc)
        market_score, market_regime, sp500_trend, vix = 50.0, "neutral", 0.0, 18.0

    total_value = _float(getattr(balance, "total_value_usd", getattr(balance, "total_value_krw", 0.0)))
    cash = _float(getattr(balance, "cash_usd", getattr(balance, "cash_krw", 0.0)))
    invested = _float(getattr(balance, "invested_usd", getattr(balance, "invested_krw", 0.0)))
    unrealized_pnl = sum(_float(getattr(h, "unrealized_pnl", 0.0)) for h in holdings)
    today_pnl = _float(getattr(safety_state, "realized_pnl_today", 0.0)) + unrealized_pnl
    today_pnl_pct = (today_pnl / total_value * 100.0) if total_value > 0 else 0.0
    account_pnl = unrealized_pnl + _float(getattr(safety_state, "realized_pnl_today", 0.0))
    account_pnl_pct = (account_pnl / total_value * 100.0) if total_value > 0 else 0.0

    trade_summary = _build_trade_summary(today)
    trade_count = int(trade_summary["buy_count"]) + int(trade_summary["sell_count"])
    buy_text = _compact_trade_list(trade_summary["buy_labels"])
    sell_text = _compact_trade_list(trade_summary["sell_labels"])

    holding_lines = []
    for h in holdings:
        ticker = str(getattr(h, "ticker", ""))
        pos = position_map.get(ticker)
        holding_lines.append(
            f"{ticker:<6} {_fmt_pct(getattr(h, 'unrealized_pnl_pct', 0.0), signed=True)}  ({_holding_days(pos)})"
        )
    if not holding_lines:
        holding_lines = ["없음"]

    loss_today = max(0.0, -_float(getattr(safety_state, "realized_pnl_today", 0.0)))
    daily_loss_limit = _float(getattr(runner.safety, "daily_loss_limit_usd", 0.0))
    loss_status = "여유" if daily_loss_limit <= 0 or loss_today < daily_loss_limit else "한도 도달"
    max_orders = int(getattr(runner.safety, "max_orders_per_day", 0) or 0)
    orders_today = int(getattr(safety_state, "orders_today", 0) or 0)
    order_status = f"{orders_today} / {max_orders} 한도 내" if max_orders > 0 and orders_today < max_orders else f"{orders_today} / {max_orders or '?'} 확인 필요"

    lines = [
        f"📊 일일 보고 — {today} (장 마감)",
        "",
        "🏛️ 시장 요약",
        f"S&P500 60일: {sp500_trend:+.2f}% / VIX: {vix:.1f} ({_vix_label(vix)})",
        f"시장 분위기: {_market_mood(market_score, market_regime)} / score {market_score:.0f}",
        "",
        "💰 계좌 현황",
        f"총 평가금: {_fmt_usd(total_value)}",
        f"현금: {_fmt_usd(cash)} / 투자: {_fmt_usd(invested)}",
        f"누적 손익: {_fmt_usd(account_pnl, signed=True)} ({account_pnl_pct:+.2f}%)",
        f"오늘 손익: {_fmt_usd(today_pnl, signed=True)} ({today_pnl_pct:+.2f}%)",
        "",
        f"📈 오늘 거래 ({trade_count}건)",
        f"매수 {trade_summary['buy_count']}건: {buy_text}",
        f"매도 {trade_summary['sell_count']}건: {sell_text}",
        "",
        f"📋 보유 종목 ({len(holdings)})",
        *holding_lines,
        "",
        "🛡️ 리스크 상태",
        f"일일 손실 한도: {_fmt_usd(loss_today)} / {_fmt_usd(daily_loss_limit)} ({loss_status})",
        f"연속 손실: {int(getattr(safety_state, 'consecutive_losses', 0) or 0)}회",
        f"주문: {order_status}",
    ]
    return "\n".join(lines)[:3900]


def send_daily_report_from_runner(runner, *, now: datetime | None = None) -> bool:
    text = build_daily_report_text(runner, now=now)
    ok = runner.notifier.send(text)
    try:
        runner.stats.reset_daily()
    except Exception:
        pass
    return ok
