"""Daily/weekly/monthly live report builder/sender for Telegram.

장마감 보고는 기존 TelegramNotifier transport를 그대로 쓰고,
시장 요약·계좌 현황·거래·보유 종목·리스크 상태를 한 메시지로 묶는다.
주간/월간 성과·MDD 계산을 위해 일일 보고 발송 시 equity snapshot을 적립한다.
"""
from __future__ import annotations

import csv
import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from engine.live.position_manager import TRADE_LOG_PATH
from engine.live.safety import state as safety_state_mod
from engine.market.context import get_market_context

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAPER_AUDIT_PATH = PROJECT_ROOT / "data" / "_system" / "paper_trade_audit.jsonl"
EQUITY_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "_system" / "equity_snapshots.csv"

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


def _fmt_pct(value: Any, signed: bool = True, digits: int = 2) -> str:
    v = _float(value)
    sign = "+" if signed else ""
    return f"{v:{sign}.{digits}f}%"


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


def _session_date_from_dt(dt: datetime, market_name: str = "US") -> date:
    if str(market_name or "").upper() == "US":
        return dt.astimezone(NY).date()
    return dt.astimezone(KST).date()


def _session_date_from_value(value: Any, market_name: str = "US") -> date | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    return _session_date_from_dt(dt, market_name)


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


def _period_trade_log_rows(start: date, end: date, market_name: str = "US", trade_log_path: Path = TRADE_LOG_PATH) -> list[dict[str, str]]:
    if not trade_log_path.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        with trade_log_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                session_date = _session_date_from_value(row.get("exited_at") or row.get("date") or row.get("timestamp"), market_name)
                if session_date and start <= session_date <= end:
                    rows.append(dict(row))
    except Exception as exc:
        log.warning("period trade_log 읽기 실패: %s", exc)
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


def _period_paper_audit_orders(start: date, end: date, market_name: str = "US", audit_path: Path = PAPER_AUDIT_PATH) -> list[dict[str, Any]]:
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
                session_date = _session_date_from_value(ts, market_name)
                if session_date and start <= session_date <= end:
                    orders.append(row)
    except Exception as exc:
        log.warning("period paper audit log 읽기 실패: %s", exc)
    return orders


def _market_name(runner) -> str:
    return str(getattr(getattr(runner, "clock", None), "name", "US") or "US").upper()


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
        pnl = _trade_pnl_usd(r)
        reason = str(r.get("exit_reason") or "청산")
        sell_labels.append(f"{ticker} {_fmt_usd(pnl, signed=True)} ({reason})")

    return {
        "buy_count": len(buy_orders),
        "sell_count": len(trade_rows),
        "buy_labels": buy_labels,
        "sell_labels": sell_labels,
        "trade_rows": trade_rows,
    }


def _trade_pnl_usd(row: dict[str, Any]) -> float:
    return _float(row.get("pnl_usd", row.get("pnl_notional", row.get("pnl_krw", 0.0))))


def _trade_pnl_pct(row: dict[str, Any]) -> float:
    value = row.get("pnl_pct", row.get("return_pct", row.get("realized_pnl_pct", "")))
    if value not in (None, ""):
        return _float(value)
    entry = _float(row.get("entry_price"))
    exit_price = _float(row.get("exit_price"))
    if entry > 0 and exit_price > 0:
        return (exit_price / entry - 1.0) * 100.0
    return 0.0


def _exit_reason(row: dict[str, Any]) -> str:
    return str(row.get("exit_reason") or row.get("reason") or "unknown")


def _reason_label(reason: str) -> str:
    key = str(reason or "").lower()
    return {
        "take_profit": "익절",
        "trailing": "트레일링",
        "stop_loss": "손절",
        "sell_omen": "sell_omen",
        "time_out": "시간초과",
        "breakeven": "본전보호",
        "breakeven_stop": "본전보호",
        "manual": "수동",
        "pending_sell": "대기체결",
    }.get(key, key or "기타")


def _append_equity_snapshot(runner, *, now: datetime | None = None) -> None:
    now = (now or datetime.now(KST)).astimezone(KST)
    try:
        balance = runner.broker.get_balance()
        safety_state = safety_state_mod.load()
        holdings = list(getattr(balance, "holdings", []) or [])
        total_value = _float(getattr(balance, "total_value_usd", getattr(balance, "total_value_krw", 0.0)))
        cash = _float(getattr(balance, "cash_usd", getattr(balance, "cash_krw", 0.0)))
        invested = _float(getattr(balance, "invested_usd", getattr(balance, "invested_krw", 0.0)))
        unrealized_pnl = sum(_float(getattr(h, "unrealized_pnl", 0.0)) for h in holdings)
        market_name = _market_name(runner)
        row = {
            "timestamp": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "session_date": _session_date_from_dt(now, market_name).isoformat(),
            "market": market_name,
            "cash": f"{cash:.6f}",
            "invested": f"{invested:.6f}",
            "total_value": f"{total_value:.6f}",
            "unrealized_pnl": f"{unrealized_pnl:.6f}",
            "realized_pnl_today": f"{_float(getattr(safety_state, 'realized_pnl_today', 0.0)):.6f}",
            "holdings_count": str(len(holdings)),
            "orders_today": str(int(getattr(safety_state, "orders_today", 0) or 0)),
            "daily_loss_limit": f"{_float(getattr(runner.safety, 'daily_loss_limit_usd', 0.0)):.6f}",
        }
        EQUITY_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        file_exists = EQUITY_SNAPSHOT_PATH.exists()
        with EQUITY_SNAPSHOT_PATH.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as exc:
        log.warning("equity snapshot 기록 실패: %s", exc)


def _load_equity_snapshots(path: Path = EQUITY_SNAPSHOT_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    latest_by_session: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                session_date = str(row.get("session_date") or row.get("date") or "")
                if session_date:
                    latest_by_session[session_date] = dict(row)
    except Exception as exc:
        log.warning("equity snapshot 읽기 실패: %s", exc)
        return []
    return [latest_by_session[k] for k in sorted(latest_by_session)]


def _snapshots_between(start: date, end: date) -> list[dict[str, Any]]:
    rows = []
    for row in _load_equity_snapshots():
        try:
            d = date.fromisoformat(str(row.get("session_date") or row.get("date"))[:10])
        except Exception:
            continue
        if start <= d <= end:
            rows.append(row)
    return rows


def _mdd_pct(values: list[float]) -> float | None:
    vals = [v for v in values if v > 0]
    if not vals:
        return None
    peak = vals[0]
    mdd = 0.0
    for value in vals:
        peak = max(peak, value)
        if peak > 0:
            mdd = min(mdd, (value / peak - 1.0) * 100.0)
    return mdd


def _period_equity_summary(start: date, end: date, current_total_value: float = 0.0) -> dict[str, Any]:
    snaps = _snapshots_between(start, end)
    if not snaps:
        return {
            "snapshots": [],
            "start_value": None,
            "end_value": current_total_value if current_total_value > 0 else None,
            "pnl": None,
            "return_pct": None,
            "mdd_pct": None,
            "daily_loss_limit_hits": 0,
        }
    start_value = _float(snaps[0].get("total_value"))
    end_value = _float(snaps[-1].get("total_value"))
    pnl = end_value - start_value if start_value > 0 else None
    ret = (pnl / start_value * 100.0) if start_value > 0 and pnl is not None else None
    values = [_float(r.get("total_value")) for r in snaps]
    hits = 0
    for r in snaps:
        realized = _float(r.get("realized_pnl_today"))
        limit = _float(r.get("daily_loss_limit"))
        if limit > 0 and -realized >= limit:
            hits += 1
    return {
        "snapshots": snaps,
        "start_value": start_value,
        "end_value": end_value,
        "pnl": pnl,
        "return_pct": ret,
        "mdd_pct": _mdd_pct(values),
        "daily_loss_limit_hits": hits,
    }


def _period_trade_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [_trade_pnl_usd(r) for r in trades]
    pct_values = [_trade_pnl_pct(r) for r in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_pct = [p for p in pct_values if p > 0]
    loss_pct = [p for p in pct_values if p < 0]
    total = len(trades)
    win_count = len(wins)
    loss_count = len(losses)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else None
    best = max(trades, key=_trade_pnl_pct) if trades else None
    worst = min(trades, key=_trade_pnl_pct) if trades else None
    reason_counts = Counter(_reason_label(_exit_reason(r)) for r in trades)
    reason_pnl = defaultdict(float)
    reason_count = defaultdict(int)
    ticker_pnl = defaultdict(float)
    ticker_count = defaultdict(int)
    for r in trades:
        label = _reason_label(_exit_reason(r))
        pnl = _trade_pnl_usd(r)
        reason_pnl[label] += pnl
        reason_count[label] += 1
        ticker = str(r.get("ticker") or "?")
        ticker_pnl[ticker] += pnl
        ticker_count[ticker] += 1
    best_ticker = max(ticker_pnl, key=ticker_pnl.get) if ticker_pnl else ""
    return {
        "total": total,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": (win_count / total * 100.0) if total else None,
        "avg_win_pct": (sum(win_pct) / len(win_pct)) if win_pct else None,
        "avg_loss_pct": (sum(loss_pct) / len(loss_pct)) if loss_pct else None,
        "profit_factor": pf,
        "best": best,
        "worst": worst,
        "reason_counts": reason_counts,
        "reason_pnl": dict(reason_pnl),
        "reason_count": dict(reason_count),
        "best_ticker": best_ticker,
        "best_ticker_pnl": ticker_pnl.get(best_ticker, 0.0) if best_ticker else 0.0,
        "best_ticker_count": ticker_count.get(best_ticker, 0) if best_ticker else 0,
    }


def _period_orders(start: date, end: date, market_name: str) -> list[dict[str, Any]]:
    return _period_paper_audit_orders(start, end, market_name)


def _weekly_period(now: datetime, market_name: str) -> tuple[date, date]:
    local = now.astimezone(NY if market_name == "US" else KST)
    end = local.date()
    start = end - timedelta(days=end.weekday())
    return start, end


def _monthly_period(now: datetime, market_name: str) -> tuple[date, date]:
    local = now.astimezone(NY if market_name == "US" else KST)
    end = local.date()
    start = end.replace(day=1)
    return start, end


def _period_label(start: date, end: date) -> str:
    if start.year == end.year:
        return f"{start:%Y-%m-%d} ~ {end:%m-%d}"
    return f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"


def _sp500_return_pct(start: date, end: date) -> float | None:
    try:
        from engine.market.context import _fetch_index
        df = _fetch_index("^GSPC", "3mo")
        if df is None or df.empty or "Close" not in df.columns:
            return None
        idx = getattr(df, "index", None)
        if idx is None:
            return None
        df = df.copy()
        df.index = [getattr(x, "date", lambda: x)() if hasattr(x, "date") else x for x in df.index]
        rows = df[(df.index >= start) & (df.index <= end)]
        if len(rows) < 2:
            return None
        first = _float(rows["Close"].iloc[0])
        last = _float(rows["Close"].iloc[-1])
        if first <= 0 or last <= 0:
            return None
        return (last / first - 1.0) * 100.0
    except Exception as exc:
        log.warning("S&P500 benchmark 계산 실패: %s", exc)
        return None


def _trade_line(row: dict[str, Any] | None) -> str:
    if not row:
        return "없음"
    return f"{row.get('ticker', '?')} {_fmt_pct(_trade_pnl_pct(row), signed=True, digits=1)} ({_reason_label(_exit_reason(row))})"


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


def build_weekly_report_text(runner, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(KST)).astimezone(KST)
    market_name = _market_name(runner)
    start, end = _weekly_period(now, market_name)
    current_value = _float(getattr(runner.broker.get_balance(), "total_value_krw", 0.0))
    equity = _period_equity_summary(start, end, current_total_value=current_value)
    trades = _period_trade_log_rows(start, end, market_name)
    stats = _period_trade_stats(trades)

    start_value = equity["start_value"]
    end_value = equity["end_value"]
    return_pct = equity["return_pct"]
    pnl = equity["pnl"]
    if start_value is None or end_value is None or return_pct is None or pnl is None:
        perf_line = "주간 수익률: 데이터 부족 (equity snapshot 적립 필요)"
        value_line = f"주 시작 데이터 없음 → 현재 { _fmt_usd(current_value) }"
    else:
        perf_line = f"주간 수익률: {_fmt_pct(return_pct)} ({_fmt_usd(pnl, signed=True)})"
        value_line = f"주 시작 {_fmt_usd(start_value)} → 주 마감 {_fmt_usd(end_value)}"

    win_rate_line = "승률: 데이터 없음"
    if stats["win_rate"] is not None:
        win_rate_line = f"승률: {stats['win_rate']:.0f}% ({stats['wins']}승 {stats['losses']}패)"
    avg_line = "평균 수익/손실: 데이터 없음"
    if stats["avg_win_pct"] is not None or stats["avg_loss_pct"] is not None:
        avg_win = _fmt_pct(stats["avg_win_pct"] or 0.0, digits=1)
        avg_loss = _fmt_pct(stats["avg_loss_pct"] or 0.0, digits=1)
        avg_line = f"평균 수익: {avg_win} / 평균 손실: {avg_loss}"
    pf_line = f"손익비: {stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "손익비: 데이터 없음"
    reason_counts = stats["reason_counts"]
    reason_line = " / ".join(f"{k} {v}" for k, v in reason_counts.items()) or "데이터 없음"
    mdd_line = f"주간 최대 낙폭(MDD): {_fmt_pct(equity['mdd_pct'])}" if equity["mdd_pct"] is not None else "주간 최대 낙폭(MDD): 데이터 부족"

    lines = [
        f"📅 주간 보고 — {_period_label(start, end)}",
        "",
        "💰 주간 성과",
        perf_line,
        value_line,
        f"누적 수익률: {_fmt_pct((end_value / start_value - 1.0) * 100.0) if start_value and end_value else '데이터 부족'}",
        "",
        f"📊 거래 통계 (이번 주 {stats['total']}건)",
        win_rate_line,
        avg_line,
        pf_line,
        "",
        "🏆 베스트 / 워스트",
        f"베스트: {_trade_line(stats['best'])}",
        f"워스트: {_trade_line(stats['worst'])}",
        "",
        "📋 청산 유형별",
        reason_line,
        "",
        "🛡️ 리스크",
        f"최대 연속 손실: {int(getattr(safety_state_mod.load(), 'consecutive_losses', 0) or 0)}회",
        mdd_line,
    ]
    return "\n".join(lines)[:3900]


def build_monthly_report_text(runner, *, now: datetime | None = None) -> str:
    now = (now or datetime.now(KST)).astimezone(KST)
    market_name = _market_name(runner)
    start, end = _monthly_period(now, market_name)
    current_value = _float(getattr(runner.broker.get_balance(), "total_value_krw", 0.0))
    equity = _period_equity_summary(start, end, current_total_value=current_value)
    trades = _period_trade_log_rows(start, end, market_name)
    orders = _period_orders(start, end, market_name)
    buy_count = sum(1 for o in orders if str(o.get("side", "")).lower() == "buy" and str(o.get("status", "")).lower() == "filled")
    stats = _period_trade_stats(trades)
    benchmark = _sp500_return_pct(start, end)

    start_value = equity["start_value"]
    end_value = equity["end_value"]
    return_pct = equity["return_pct"]
    pnl = equity["pnl"]
    if return_pct is None or pnl is None:
        perf_line = "월 수익률: 데이터 부족 (equity snapshot 적립 필요)"
        excess_line = "시장(S&P500) 대비: 데이터 부족"
    else:
        perf_line = f"월 수익률: {_fmt_pct(return_pct)} ({_fmt_usd(pnl, signed=True)})"
        if benchmark is None:
            excess_line = f"시장(S&P500) 대비: {_fmt_pct(return_pct)} vs 데이터 없음"
        else:
            excess = return_pct - benchmark
            excess_line = f"시장(S&P500) 대비: {_fmt_pct(return_pct)} vs {_fmt_pct(benchmark)} → 초과 {_fmt_pct(excess)}"

    win_pf = "승률/PF: 데이터 없음"
    if stats["win_rate"] is not None or stats["profit_factor"] is not None:
        wr = f"{stats['win_rate']:.0f}%" if stats["win_rate"] is not None else "데이터 없음"
        pf = f"{stats['profit_factor']:.2f}" if stats["profit_factor"] is not None else "데이터 없음"
        win_pf = f"승률: {wr} / 손익비: {pf}"

    reason_lines = []
    for reason, pnl_value in sorted(stats["reason_pnl"].items(), key=lambda kv: kv[0]):
        count = stats["reason_count"].get(reason, 0)
        reason_lines.append(f"{reason}: {_fmt_usd(pnl_value, signed=True)} ({count}건)")
    if not reason_lines:
        reason_lines = ["데이터 없음"]

    best_rulebook_line = "데이터 없음"
    if stats["best_ticker"]:
        best_rulebook_line = f"{stats['best_ticker']} 룰북: {_fmt_usd(stats['best_ticker_pnl'], signed=True)} ({stats['best_ticker_count']}거래)"

    mdd_line = f"월 최대 낙폭(MDD): {_fmt_pct(equity['mdd_pct'])}" if equity["mdd_pct"] is not None else "월 최대 낙폭(MDD): 데이터 부족"
    month_label = f"{end:%Y년 %-m월}" if hasattr(end, "strftime") else str(end)
    cumulative_line = "누적 수익률: 데이터 부족"
    if start_value and end_value:
        cumulative_line = f"누적 수익률: {_fmt_pct((end_value / start_value - 1.0) * 100.0)}"

    lines = [
        f"🗓️ 월간 보고 — {month_label}",
        "",
        "💰 월간 성과",
        perf_line,
        cumulative_line,
        excess_line,
        "",
        f"📊 거래 통계 (이번 달 {stats['total']}건)",
        win_pf,
        f"총 매수 / 매도: {buy_count} / {stats['total']}",
        "",
        "📈 청산 유형별 기여도",
        *reason_lines,
        "",
        "🏆 베스트 룰북",
        best_rulebook_line,
        "",
        "🛡️ 리스크",
        mdd_line,
        f"일일 손실 한도 도달: {equity['daily_loss_limit_hits']}회",
    ]
    return "\n".join(lines)[:3900]


def should_send_weekly_report(clock, *, now: datetime | None = None) -> bool:
    now = (now or datetime.now(KST)).astimezone(KST)
    market_name = str(getattr(clock, "name", "US") or "US").upper()
    return now.weekday() == (5 if market_name == "US" else 4)


def should_send_monthly_report(clock, *, now: datetime | None = None) -> bool:
    now = (now or datetime.now(KST)).astimezone(KST)
    market_name = str(getattr(clock, "name", "US") or "US").upper()
    local = now.astimezone(NY if market_name == "US" else KST)
    try:
        next_open = clock.next_open(local + timedelta(minutes=1)) if hasattr(clock, "next_open") else None
        if next_open is not None:
            next_local = next_open.astimezone(NY if market_name == "US" else KST)
            return next_local.month != local.month
    except Exception as exc:
        log.warning("monthly report next_open 확인 실패: %s", exc)
    tomorrow = local.date() + timedelta(days=1)
    return tomorrow.month != local.month


def send_daily_report_from_runner(runner, *, now: datetime | None = None) -> bool:
    text = build_daily_report_text(runner, now=now)
    _append_equity_snapshot(runner, now=now)
    ok = runner.notifier.send(text)
    try:
        runner.stats.reset_daily()
    except Exception:
        pass
    return ok


def send_weekly_report_from_runner(runner, *, now: datetime | None = None) -> bool:
    return runner.notifier.send(build_weekly_report_text(runner, now=now))


def send_monthly_report_from_runner(runner, *, now: datetime | None = None) -> bool:
    return runner.notifier.send(build_monthly_report_text(runner, now=now))
