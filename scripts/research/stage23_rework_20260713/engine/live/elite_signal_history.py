"""Elite shadow signal history analyzer.

무엇을 하는 파일인가:
- elite-shadow 후보 룰북의 최근 N거래일 신호 흐름을 재생한다.
- 특정 후보가 오늘 BUY라면 며칠 전에도 BUY였는지, 그때 가격이 얼마였는지,
  지금 진입이 추격매수인지 판단할 근거를 만든다.
- broker 주문, live runner, positions.json, parameters.json은 절대 수정하지 않는다.

주의:
- 시장/섹터 context는 현재 context를 고정해서 daily OHLCV slice에 적용한다.
  과거 날짜의 정확한 macro context replay가 아니므로 "signal-shape replay"로 해석한다.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from engine.live.elite_shadow_report import build_elite_shadow_report
from engine.live.elite_shadow_trader import (
    _event_flags,
    _latest_price,
    _load_ohlcv,
    _load_rulebook_for_candidate,
    _news_context,
    _safe_float,
    load_state,
)
from engine.market.context import get_market_context
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or f"{candidate.get('stage')}:{candidate.get('ticker')}:{candidate.get('rulebook_hash_short')}")


def _date_text(value: Any) -> str:
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d")
        text = str(value)
        return text[:10]
    except Exception:
        return ""


def _find_candidate(*, candidate_id: str | None = None, ticker: str | None = None, stage: str | None = None, rulebook_hash_short: str | None = None) -> dict[str, Any] | None:
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    candidates = report.get("candidates") or []
    ticker_u = str(ticker or "").upper().strip()
    stage_s = str(stage or "").lower().strip()
    hash_s = str(rulebook_hash_short or "").strip()
    for candidate in candidates:
        key = _candidate_key(candidate)
        if candidate_id and key == candidate_id:
            return candidate
        if ticker_u and str(candidate.get("ticker") or "").upper() != ticker_u:
            continue
        if stage_s and str(candidate.get("stage") or "").lower() != stage_s:
            continue
        if hash_s and not str(candidate.get("rulebook_hash_short") or "").startswith(hash_s):
            continue
        if ticker_u:
            return candidate
    return None


def _context_values(ctx: Any, rb: Rulebook) -> tuple[float, float, float]:
    if ctx is None:
        return 50.0, 50.0, 18.0
    try:
        market_score = float(getattr(ctx, "score", 50.0))
        sector_strength = getattr(ctx, "sector_strength", {}) or {}
        sector_score = float(sector_strength.get(rb.sector_name, 50.0))
        vix_level = float(getattr(ctx, "vix_level", 18.0))
        return market_score, sector_score, vix_level
    except Exception:
        return 50.0, 50.0, 18.0


def _slice_price(df: pd.DataFrame) -> float:
    try:
        return _safe_float(df["Close"].iloc[-1], 0.0)
    except Exception:
        return 0.0


def build_signal_history(
    *,
    candidate_id: str | None = None,
    ticker: str | None = None,
    stage: str | None = None,
    rulebook_hash_short: str | None = None,
    days: int = 12,
) -> dict[str, Any]:
    """Return recent daily signal replay for one elite-shadow candidate."""
    days = max(3, min(int(days or 12), 40))
    candidate = _find_candidate(
        candidate_id=candidate_id,
        ticker=ticker,
        stage=stage,
        rulebook_hash_short=rulebook_hash_short,
    )
    if not candidate:
        return {"ok": False, "reason": "candidate_not_found", "query": {"candidate_id": candidate_id, "ticker": ticker, "stage": stage, "rulebook_hash_short": rulebook_hash_short}}
    ticker_u = str(candidate.get("ticker") or "").upper().strip()
    rb_dict = _load_rulebook_for_candidate(candidate)
    if not rb_dict:
        return {"ok": False, "reason": "rulebook_missing", "candidate": candidate}
    rb_dict = dict(rb_dict)
    rb_dict["ticker"] = ticker_u
    rb = Rulebook.from_dict(rb_dict)
    df = _load_ohlcv(ticker_u)
    if df is None or len(df) < 60:
        return {"ok": False, "reason": "ohlcv_missing", "candidate": candidate}

    try:
        ctx = get_market_context()
    except Exception:
        ctx = None
    market_score, sector_score, vix_level = _context_values(ctx, rb)
    rows: list[dict[str, Any]] = []
    tail_start = max(0, len(df) - days)
    event_flags = _event_flags(ctx)
    for idx in range(tail_start, len(df)):
        sliced = df.iloc[: idx + 1].copy()
        signal_date = sliced["Date"].iloc[-1] if "Date" in sliced.columns else sliced.index[-1]
        price = _slice_price(sliced)
        if price <= 0.0:
            continue
        news_sentiment, topic = _news_context(ticker_u, rb, signal_date)
        try:
            res = evaluate_signal(
                rb=rb,
                df=sliced,
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=news_sentiment,
                event_flags=event_flags,
                topic_features=topic,
            )
            score = float(res.score)
            threshold = float(res.threshold)
            should_buy = bool(res.should_buy)
            reasons = list(res.reasons)[:8]
            raw_score = float(res.raw_score)
        except Exception as exc:
            rows.append({"date": _date_text(signal_date), "close": price, "ok": False, "reason": f"evaluate_failed:{type(exc).__name__}"})
            continue
        rows.append(
            {
                "date": _date_text(signal_date),
                "close": price,
                "ok": True,
                "should_buy": should_buy,
                "score": score,
                "raw_score": raw_score,
                "threshold": threshold,
                "ratio": score / max(threshold, 0.0001),
                "reasons": reasons,
                "news_sentiment": news_sentiment,
                "topic_count": len(topic),
            }
        )

    current_price = _latest_price(ticker_u, df) or (rows[-1]["close"] if rows else 0.0)
    buy_rows = [row for row in rows if row.get("ok") and row.get("should_buy")]
    first_buy = buy_rows[0] if buy_rows else None
    last = rows[-1] if rows else None
    state = load_state()
    open_pos = None
    key = _candidate_key(candidate)
    open_positions = state.get("open_positions") or {}
    if key in open_positions:
        open_pos = open_positions.get(key)
    else:
        for pos in open_positions.values():
            if str(pos.get("ticker") or "").upper() == ticker_u:
                open_pos = pos
                break

    first_buy_price = _safe_float(first_buy.get("close"), 0.0) if first_buy else 0.0
    chase_from_first_buy_pct = ((current_price / first_buy_price - 1.0) * 100.0) if first_buy_price > 0 else 0.0
    entry_price = _safe_float((open_pos or {}).get("entry_price"), 0.0)
    entry_vs_first_buy_pct = ((entry_price / first_buy_price - 1.0) * 100.0) if entry_price > 0 and first_buy_price > 0 else 0.0
    consecutive_buy_days = 0
    for row in reversed(rows):
        if row.get("ok") and row.get("should_buy"):
            consecutive_buy_days += 1
        else:
            break
    stale_chase = bool(first_buy and (chase_from_first_buy_pct >= 3.0 or consecutive_buy_days >= 3))
    severe_chase = bool(first_buy and (chase_from_first_buy_pct >= 6.0 or consecutive_buy_days >= 5))
    verdict = "no_recent_buy"
    if first_buy:
        if severe_chase:
            verdict = "severe_chase"
        elif stale_chase:
            verdict = "chase_risk"
        elif consecutive_buy_days <= 2 and chase_from_first_buy_pct < 3.0:
            verdict = "fresh_signal"
        else:
            verdict = "normal_signal"

    return {
        "ok": True,
        "_comment": "Recent daily signal replay. Market/sector context is current-context replay, not historical macro replay.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate": {
            "candidate_id": key,
            "ticker": ticker_u,
            "stage": candidate.get("stage"),
            "bucket": candidate.get("bucket"),
            "rulebook_hash_short": candidate.get("rulebook_hash_short"),
        },
        "context_mode": "current_market_context_replay",
        "current_price": current_price,
        "open_position": open_pos,
        "summary": {
            "days": days,
            "row_count": len(rows),
            "buy_day_count": len(buy_rows),
            "consecutive_buy_days": consecutive_buy_days,
            "first_buy_date": first_buy.get("date") if first_buy else None,
            "first_buy_price": first_buy_price if first_buy else None,
            "current_price": current_price,
            "chase_from_first_buy_pct": chase_from_first_buy_pct if first_buy else None,
            "entry_price": entry_price if entry_price > 0 else None,
            "entry_vs_first_buy_pct": entry_vs_first_buy_pct if entry_price > 0 and first_buy else None,
            "last_score": last.get("score") if last and last.get("ok") else None,
            "last_threshold": last.get("threshold") if last and last.get("ok") else None,
            "last_ratio": last.get("ratio") if last and last.get("ok") else None,
            "last_should_buy": bool(last.get("should_buy")) if last and last.get("ok") else False,
            "verdict": verdict,
        },
        "rows": rows,
    }
