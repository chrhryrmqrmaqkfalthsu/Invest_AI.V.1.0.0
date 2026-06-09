"""Live news/risk alert helpers.

이 모듈은 거래 실행 로직을 바꾸지 않고, 보유 종목과 시장 국면에 대한
텔레그램 사전 경고만 담당한다. 특히 sell_omen 사전경고는 실제 청산
임계값보다 낮은 구간에 들어왔을 때 1회만 알린다.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from engine.live.market_clock import market_region_for_ticker

log = logging.getLogger("live.news_alerts")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SELL_OMEN_SCORE_TABLE = PROJECT_ROOT / "data" / "_system" / "ml_sell_omen" / "sell_omen_scores.csv"
NEWS_ALERT_STATE_PATH = PROJECT_ROOT / "data" / "_system" / "news_alert_state.json"
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")

_SCORE_CACHE: dict[str, Any] = {"mtime": None, "rows_by_ticker": {}}


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
    return f"{_float(value):+.1f}%"


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            dt = datetime.now(KST)
        else:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def _score_asof_date(ticker: str, asof: Any = None) -> str:
    dt = _parse_dt(asof)
    region = market_region_for_ticker(ticker)
    local = dt.astimezone(NY if region == "US" else KST)
    return local.date().isoformat()


def _load_score_rows(path: Path = SELL_OMEN_SCORE_TABLE) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _SCORE_CACHE.get("mtime") == mtime:
        return dict(_SCORE_CACHE.get("rows_by_ticker") or {})

    rows_by_ticker: dict[str, list[dict[str, Any]]] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = str(row.get("ticker") or "").upper().strip()
                date = str(row.get("Date") or row.get("date") or "").strip()[:10]
                score = _float(row.get("sell_omen_score"), -1.0)
                if not ticker or not date or not (0.0 <= score <= 1.0):
                    continue
                rows_by_ticker.setdefault(ticker, []).append({
                    "ticker": ticker,
                    "date": date,
                    "score": score,
                    "model_train_end": row.get("model_train_end", ""),
                    "score_year": row.get("score_year", ""),
                })
        for ticker in rows_by_ticker:
            rows_by_ticker[ticker].sort(key=lambda r: str(r.get("date") or ""))
        _SCORE_CACHE["mtime"] = mtime
        _SCORE_CACHE["rows_by_ticker"] = rows_by_ticker
    except Exception as exc:
        log.warning("sell_omen score table load failed: %s", exc)
        return {}
    return rows_by_ticker


def lookup_live_sell_omen_score(ticker: str, *, asof: Any = None, path: Path = SELL_OMEN_SCORE_TABLE) -> Optional[dict[str, Any]]:
    """Return latest sell_omen score row with Date <= asof session date."""
    t = str(ticker or "").upper().strip()
    if not t:
        return None
    asof_date = _score_asof_date(t, asof)
    rows = _load_score_rows(path).get(t, [])
    latest = None
    for row in rows:
        if str(row.get("date") or "") <= asof_date:
            latest = row
        else:
            break
    return dict(latest) if latest else None


def _load_state(path: Path = NEWS_ALERT_STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"sell_omen_prealerts": {}, "market_regime_alerts": {}}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("sell_omen_prealerts", {})
            data.setdefault("market_regime_alerts", {})
            return data
    except Exception as exc:
        log.warning("news alert state load failed: %s", exc)
    return {"sell_omen_prealerts": {}, "market_regime_alerts": {}}


def _save_state(state: dict[str, Any], path: Path = NEWS_ALERT_STATE_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(path)
    except Exception as exc:
        log.warning("news alert state save failed: %s", exc)


def _should_send_prealert(ticker: str, score_date: str, score: float, warning_threshold: float, threshold: float) -> bool:
    if score < warning_threshold or score >= threshold:
        return False
    state = _load_state()
    key = f"{ticker.upper()}:{score_date}:{warning_threshold:.2f}:{threshold:.2f}"
    sent = state.setdefault("sell_omen_prealerts", {})
    if key in sent:
        return False
    sent[key] = {
        "sent_at": datetime.now(KST).isoformat(),
        "ticker": ticker.upper(),
        "score_date": score_date,
        "score": round(score, 4),
        "warning_threshold": round(warning_threshold, 4),
        "threshold": round(threshold, 4),
    }
    _save_state(state)
    return True


def send_sell_omen_prealert(
    notifier,
    *,
    ticker: str,
    score: float,
    threshold: float,
    warning_threshold: float,
    current_pnl_pct: float,
    holding_days: int,
    score_date: str = "",
    model_train_end: str = "",
    live_exit_wired: bool = False,
) -> bool:
    cause = "부정 뉴스/시장 위험 피처 상승"
    action = "→ 다음 청산 판정에서 매도될 수 있음" if live_exit_wired else "→ sell_omen 청산 후보권 진입, 보유 종목 주의"
    lines = [
        f"⚠️ 위험 신호 — {ticker} (보유 중)",
        f"sell_omen 점수: {score:.2f} (사전경고 {warning_threshold:.2f}↑ / 청산임계 {threshold:.2f})",
        f"원인: {cause}",
        f"현재 수익: {_fmt_pct(current_pnl_pct)} ({holding_days}일째)",
    ]
    if score_date:
        extra = f"score date: {score_date}"
        if model_train_end:
            extra += f" / train≤{model_train_end}"
        lines.append(extra)
    lines.append(action)
    return bool(notifier.send("\n".join(lines)[:3900]))


def maybe_send_sell_omen_prealert(
    *,
    ticker: str,
    pos: Any,
    current_price: float,
    notifier,
    asof: Any = None,
    state_path: Path = NEWS_ALERT_STATE_PATH,
) -> bool:
    if notifier is None or pos is None:
        return False
    try:
        from engine.live.exit_policy_adapter import resolve_position_rulebook
        rulebook, _ = resolve_position_rulebook(pos)
    except Exception as exc:
        log.debug("%s sell_omen prealert rulebook resolve failed: %s", ticker, exc)
        return False
    if rulebook is None or not bool(getattr(rulebook, "sell_omen_enabled", False)):
        return False
    threshold = _float(getattr(rulebook, "sell_omen_threshold", 1.0), 1.0)
    if threshold <= 0 or threshold > 1.0:
        return False
    warning_threshold = max(0.0, threshold - 0.10)
    score_row = lookup_live_sell_omen_score(ticker, asof=asof)
    if not score_row:
        return False
    score = _float(score_row.get("score"), -1.0)
    score_date = str(score_row.get("date") or "")
    if not _should_send_prealert(ticker, score_date, score, warning_threshold, threshold):
        return False
    entry_price = _float(getattr(pos, "entry_price", 0.0))
    current_pnl_pct = (float(current_price) / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0
    try:
        entry_dt = _parse_dt(getattr(pos, "entry_date", ""))
        holding_days = max(0, (datetime.now(KST) - entry_dt.astimezone(KST)).days)
    except Exception:
        holding_days = 0
    return send_sell_omen_prealert(
        notifier,
        ticker=ticker,
        score=score,
        threshold=threshold,
        warning_threshold=warning_threshold,
        current_pnl_pct=current_pnl_pct,
        holding_days=holding_days,
        score_date=score_date,
        model_train_end=str(score_row.get("model_train_end") or ""),
        live_exit_wired=False,
    )


def send_market_regime_warning(
    notifier,
    *,
    prev_regime: str,
    new_regime: str,
    prev_vix: Any = None,
    vix: Any = None,
    prev_score: Any = None,
    score: Any = None,
    holdings_count: int = 0,
    exposure_usd: float = 0.0,
) -> bool:
    prev_score_f = _float(prev_score, 0.0)
    score_f = _float(score, 0.0)
    prev_vix_text = "?" if prev_vix in (None, "") else f"{_float(prev_vix):.1f}"
    vix_text = "?" if vix in (None, "") else f"{_float(vix):.1f}"
    if score_f < 40 or str(new_regime).lower() == "bear":
        mood = "위험 회피"
    elif score_f >= 70 or str(new_regime).lower() == "bull":
        mood = "위험 선호"
    else:
        mood = "중립"
    lines = [
        "🚨 시장 경고 — 위험 국면 전환",
        f"VIX: {prev_vix_text} → {vix_text}",
        f"시장 분위기: {prev_regime or '?'} → {new_regime or '?'} ({mood})",
        f"market_score: {prev_score_f:.0f} → {score_f:.0f}",
        f"보유 {holdings_count}종목 / 노출 {_fmt_usd(exposure_usd)}",
        "→ 신규 매수 보수적, 보유 종목 주의",
    ]
    return bool(notifier.send("\n".join(lines)[:3900]))
