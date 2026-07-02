"""Elite Shadow Peak Exhaustion Exit v3.1.

v3의 고점권 소진 익절 로직에 아래 안전장치를 추가한다.
- 고점 이후 20~45분 새 고점 실패 신호는 정규장(09:30~16:00 ET)에서만 점수화한다.
- 당일 진입 포지션은 정규장 수익권에서 당일 수익 회수 쪽으로 약간 더 민감하게 본다.
- 장 후반(15:35~16:00 ET)에는 당일 수익 포지션을 다음날까지 끌고 가지 않도록 수익잠금 점수를 추가한다.

실제 broker 주문은 내지 않고 Elite Shadow 가상 장부만 갱신한다.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from datetime import datetime, time as dtime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from engine.live.elite_shadow_exit_omen import _close_by_shadow_exit_omen, _load_intraday
from engine.live.elite_shadow_peak_exit import _add, _intraday_peak_snapshot, _num
from engine.live.elite_shadow_trader import (
    _holding_days,
    _latest_price,
    _load_ohlcv,
    _safe_float,
    _summarize_state,
    load_state,
    save_state,
    utc_now,
)

PEAK_EXIT_VERSION = "shadow_peak_exhaustion_v3_1_regular_same_day"
MARKET_PROXY = "QQQ"
NY_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN_ET = dtime(9, 30)
REGULAR_CLOSE_ET = dtime(16, 0)
LATE_DAY_PROFIT_LOCK_ET = dtime(15, 35)


def _now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(NY_TZ)


def _market_session_context(now_et: datetime | None = None) -> dict[str, Any]:
    now_et = now_et or _now_et()
    tod = now_et.time()
    is_weekday = now_et.weekday() < 5
    is_regular = bool(is_weekday and REGULAR_OPEN_ET <= tod <= REGULAR_CLOSE_ET)
    is_late_day = bool(is_regular and tod >= LATE_DAY_PROFIT_LOCK_ET)
    return {
        "now_et": now_et,
        "date_et": str(now_et.date()),
        "time_et": now_et.strftime("%H:%M:%S"),
        "is_weekday": is_weekday,
        "is_regular": is_regular,
        "is_late_day": is_late_day,
        "regular_open_et": REGULAR_OPEN_ET.strftime("%H:%M"),
        "regular_close_et": REGULAR_CLOSE_ET.strftime("%H:%M"),
        "late_day_profit_lock_et": LATE_DAY_PROFIT_LOCK_ET.strftime("%H:%M"),
    }


def _parse_dt(value: Any) -> datetime | None:
    try:
        if not value:
            return None
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _position_time_context(pos: dict[str, Any], now_et: datetime) -> dict[str, Any]:
    opened = _parse_dt(pos.get("opened_at"))
    if opened is None:
        return {
            "opened_at_et": None,
            "holding_minutes": None,
            "same_market_day": False,
        }
    opened_et = opened.astimezone(NY_TZ)
    holding_minutes = max(0.0, (now_et - opened_et).total_seconds() / 60.0)
    return {
        "opened_at_et": opened_et.isoformat(),
        "holding_minutes": round(holding_minutes, 2),
        "same_market_day": opened_et.date() == now_et.date(),
    }


def evaluate_peak_exhaustion(
    *,
    pos: dict[str, Any],
    price: float,
    intraday_df: pd.DataFrame | None,
    market_intraday_df: pd.DataFrame | None = None,
    now_et: datetime | None = None,
) -> dict[str, Any]:
    """수익권 포지션의 고점권 소진 여부를 평가한다.

    v3.1 변경점:
    - stale/no-new-high 신호는 정규장일 때만 점수에 반영한다.
    - 당일 진입 포지션은 수익권이면 당일 회수 점수를 일부 더한다.
    """
    entry = _num(pos.get("entry_price"), 0.0)
    session = _market_session_context(now_et)
    pos_time = _position_time_context(pos, session["now_et"])
    is_regular = bool(session["is_regular"])
    is_late_day = bool(session["is_late_day"])
    same_day = bool(pos_time.get("same_market_day"))
    holding_minutes = _num(pos_time.get("holding_minutes"), 0.0)

    if entry <= 0.0 or price <= 0.0:
        return {
            "version": PEAK_EXIT_VERSION,
            "close": False,
            "reason": "invalid_price",
            "score": 0.0,
            "reasons": [],
            "metrics": {"session": {k: v for k, v in session.items() if k != "now_et"}, "position_time": pos_time},
        }

    pnl_pct = (price / entry - 1.0) * 100.0
    highest = max(_num(pos.get("highest_price"), entry), price)
    lowest = min(_num(pos.get("lowest_price"), entry), price)
    max_profit_pct = max(_num(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0)
    max_loss_pct = min(_num(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0)
    giveback_pct = max(0.0, max_profit_pct - pnl_pct)
    giveback_ratio = giveback_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0
    capture_ratio = pnl_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0

    # 기본 v3보다 당일 진입 수익권은 조금 더 빨리 감시를 시작한다.
    active = (
        max_profit_pct >= 2.5
        or pnl_pct >= 1.8
        or (same_day and is_regular and (max_profit_pct >= 2.0 or pnl_pct >= 1.2))
    )
    if not active:
        return {
            "version": PEAK_EXIT_VERSION,
            "close": False,
            "reason": "hold",
            "score": 0.0,
            "reasons": [],
            "metrics": {
                "active": False,
                "session": {k: v for k, v in session.items() if k != "now_et"},
                "position_time": pos_time,
                "pnl_pct": pnl_pct,
                "max_profit_pct": max_profit_pct,
                "capture_ratio": capture_ratio,
                "giveback_ratio": giveback_ratio,
            },
        }

    snap = _intraday_peak_snapshot(intraday_df, price, market_df=market_intraday_df)
    parts: list[dict[str, Any]] = []
    score = 0.0
    if not snap.get("ok"):
        return {
            "version": PEAK_EXIT_VERSION,
            "close": False,
            "reason": "hold",
            "score": 0.0,
            "reasons": [],
            "metrics": {
                "active": True,
                "session": {k: v for k, v in session.items() if k != "now_et"},
                "position_time": pos_time,
                "intraday": snap,
                "pnl_pct": pnl_pct,
                "max_profit_pct": max_profit_pct,
                "capture_ratio": capture_ratio,
                "giveback_ratio": giveback_ratio,
            },
        }

    high_gb = _num(snap.get("intraday_high_giveback_pct"), 0.0)
    ret10 = _num(snap.get("ret_10m_pct"), 0.0)
    ret15 = _num(snap.get("ret_15m_pct"), 0.0)
    ret30 = _num(snap.get("ret_30m_pct"), 0.0)
    rel30 = _num(snap.get("relative_ret_30m_pct"), 0.0)
    range_pos = _num(snap.get("intraday_range_position"), 0.5)
    last_close_pos = _num(snap.get("last_close_position"), 0.5)
    upper_wick = _num(snap.get("upper_wick_ratio"), 0.0)
    bars_since_high = _num(snap.get("bars_since_intraday_high"), 0.0)
    ema9_slope = _num(snap.get("ema9_slope_5m_pct"), 0.0)

    if high_gb <= -2.5:
        score += _add(parts, 26.0, "peak_giveback_from_intraday_high_large", high_giveback_pct=round(high_gb, 3))
    elif high_gb <= -1.5:
        score += _add(parts, 18.0, "peak_giveback_from_intraday_high", high_giveback_pct=round(high_gb, 3))
    elif high_gb <= -0.8:
        score += _add(parts, 10.0, "peak_giveback_early", high_giveback_pct=round(high_gb, 3))

    if giveback_ratio >= 0.35:
        score += _add(parts, 16.0, "overall_peak_giveback_large", giveback_ratio=round(giveback_ratio, 3))
    elif giveback_ratio >= 0.22:
        score += _add(parts, 9.0, "overall_peak_giveback_warning", giveback_ratio=round(giveback_ratio, 3))

    # 핵심 요청: 새 고점 실패 시간 조건은 정규장에만 적용한다.
    if is_regular:
        if bars_since_high >= 45:
            score += _add(parts, 12.0, "stale_intraday_high_regular_hours", bars_since_high=int(bars_since_high))
        elif bars_since_high >= 20:
            score += _add(parts, 8.0, "no_new_high_20m_regular_hours", bars_since_high=int(bars_since_high))
    elif bars_since_high >= 20:
        parts.append({
            "points": 0.0,
            "reason": "no_new_high_ignored_outside_regular_hours",
            "bars_since_high": int(bars_since_high),
            "time_et": session["time_et"],
        })

    if snap.get("lower_high_recent"):
        score += _add(parts, 12.0, "lower_high_after_peak")
    if snap.get("lower_low_recent"):
        score += _add(parts, 8.0, "lower_low_after_peak")

    if snap.get("below_vwap"):
        score += _add(parts, 12.0, "below_vwap_near_peak")
    if snap.get("below_ema9"):
        score += _add(parts, 8.0, "below_ema9_near_peak")
    if snap.get("below_ema20"):
        score += _add(parts, 10.0, "below_ema20_near_peak")
    if snap.get("ema9_below_ema20"):
        score += _add(parts, 10.0, "ema9_below_ema20_near_peak")
    if ema9_slope < -0.05:
        score += _add(parts, 7.0, "ema9_slope_down", ema9_slope_5m_pct=round(ema9_slope, 3))

    if ret30 <= -1.2:
        score += _add(parts, 14.0, "thirty_minute_pullback", ret_30m_pct=round(ret30, 3))
    elif ret15 <= -0.8:
        score += _add(parts, 8.0, "fifteen_minute_pullback", ret_15m_pct=round(ret15, 3))
    elif ret10 <= -0.6:
        score += _add(parts, 5.0, "ten_minute_pullback", ret_10m_pct=round(ret10, 3))

    if rel30 <= -1.5:
        score += _add(parts, 16.0, "relative_weakness_after_peak_large", relative_ret_30m_pct=round(rel30, 3))
    elif rel30 <= -0.8:
        score += _add(parts, 10.0, "relative_weakness_after_peak", relative_ret_30m_pct=round(rel30, 3))

    if snap.get("down_volume_dominant"):
        score += _add(parts, 12.0, "down_volume_dominant_after_peak")
    if _num(snap.get("red_bar_ratio_30m"), 0.0) >= 0.6:
        score += _add(parts, 8.0, "red_bar_cluster_after_peak", red_bar_ratio_30m=round(_num(snap.get("red_bar_ratio_30m"), 0.0), 3))
    if upper_wick >= 0.35:
        score += _add(parts, 8.0, "upper_wick_rejection", upper_wick_ratio=round(upper_wick, 3))
    if range_pos <= 0.45 and max_profit_pct >= 3.0:
        score += _add(parts, 8.0, "intraday_range_fade", range_position=round(range_pos, 3))
    if last_close_pos <= 0.35:
        score += _add(parts, 6.0, "last_bar_lower_close", last_close_position=round(last_close_pos, 3))

    if same_day and is_regular and pnl_pct >= 1.2:
        score += _add(parts, 5.0, "same_day_profit_capture_bias", holding_minutes=round(holding_minutes, 1))
    if same_day and is_late_day and pnl_pct >= 1.0:
        score += _add(parts, 16.0, "same_day_late_session_profit_lock_bias", time_et=session["time_et"])
    if same_day and is_regular and holding_minutes >= 60.0 and max_profit_pct >= 2.0 and capture_ratio >= 0.55:
        score += _add(parts, 7.0, "same_day_capture_window", capture_ratio=round(capture_ratio, 3), holding_minutes=round(holding_minutes, 1))

    # 당일 진입 수익 포지션은 기준을 소폭 낮추고, 장 후반에는 더 낮춘다.
    same_day_adj = 6.0 if same_day and is_regular else 0.0
    late_day_adj = 10.0 if same_day and is_late_day else 0.0
    reason: str | None = None
    if same_day and is_late_day and max_profit_pct >= 2.0 and pnl_pct >= max(1.2, max_profit_pct * 0.52) and score >= 48.0:
        reason = "shadow_same_day_late_profit_lock"
    elif max_profit_pct >= 8.0 and pnl_pct >= max(5.0, max_profit_pct * 0.62) and score >= 58.0 - same_day_adj - late_day_adj:
        reason = "shadow_peak_exhaustion_8p"
    elif max_profit_pct >= 5.0 and pnl_pct >= max(3.0, max_profit_pct * 0.58) and score >= 56.0 - same_day_adj - late_day_adj:
        reason = "shadow_peak_exhaustion_5p"
    elif max_profit_pct >= 3.0 and pnl_pct >= max(1.8, max_profit_pct * 0.55) and score >= 60.0 - same_day_adj - late_day_adj:
        reason = "shadow_peak_exhaustion_3p"
    elif same_day and is_regular and max_profit_pct >= 2.0 and pnl_pct >= max(1.2, max_profit_pct * 0.55) and score >= 60.0:
        reason = "shadow_same_day_peak_exhaustion"
    elif max_profit_pct >= 2.5 and pnl_pct >= 1.4 and score >= 72.0 - same_day_adj - late_day_adj:
        reason = "shadow_peak_exhaustion_score"

    metrics = {
        "active": True,
        "entry_price": entry,
        "price": price,
        "pnl_pct": pnl_pct,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "giveback_pct": giveback_pct,
        "giveback_ratio": giveback_ratio,
        "capture_ratio": capture_ratio,
        "target_capture_ratio": 0.60,
        "session": {k: v for k, v in session.items() if k != "now_et"},
        "position_time": pos_time,
        "same_day_adjustment": same_day_adj,
        "late_day_adjustment": late_day_adj,
        "intraday": snap,
    }
    return {
        "version": PEAK_EXIT_VERSION,
        "close": reason is not None,
        "reason": reason or "hold",
        "score": round(max(0.0, min(100.0, score)), 2),
        "reasons": parts[:20],
        "metrics": metrics,
    }


def run_shadow_peak_exit_tick(*, max_positions: int | None = None) -> dict[str, Any]:
    """열린 Elite Shadow 포지션에 고점권 소진 익절 로직을 적용한다."""
    started = time.time()
    state = load_state()
    open_items = list((state.get("open_positions") or {}).items())
    if max_positions is not None:
        open_items = open_items[: max(0, int(max_positions))]

    market_intraday_df = _load_intraday(MARKET_PROXY)
    evaluated = 0
    closed = 0
    errors: list[dict[str, Any]] = []
    close_counts: Counter[str] = Counter()
    closed_samples: list[dict[str, Any]] = []
    now_et = _now_et()

    for pos_key, pos in open_items:
        ticker = str(pos.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        df = _load_ohlcv(ticker)
        price = _latest_price(ticker, df)
        if not price:
            errors.append({"ticker": ticker, "candidate_id": pos_key, "reason": "price_missing"})
            continue
        entry = _safe_float(pos.get("entry_price"), 0.0)
        if entry <= 0.0:
            errors.append({"ticker": ticker, "candidate_id": pos_key, "reason": "entry_price_missing"})
            continue

        highest = max(_safe_float(pos.get("highest_price"), entry), price)
        lowest = min(_safe_float(pos.get("lowest_price"), entry), price)
        pnl_pct = (price / entry - 1.0) * 100.0
        max_profit_pct = max(_safe_float(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0)
        max_loss_pct = min(_safe_float(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0)
        pos.update({
            "highest_price": highest,
            "lowest_price": lowest,
            "max_profit_pct": max_profit_pct,
            "max_loss_pct": max_loss_pct,
            "last_price": price,
            "last_seen_at": utc_now(),
            "unrealized_pnl_pct": pnl_pct,
            "unrealized_pnl_usd": (_safe_float(pos.get("shares")) * (price - entry)),
            "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        })

        intraday_df = _load_intraday(ticker)
        peak = evaluate_peak_exhaustion(
            pos=pos,
            price=price,
            intraday_df=intraday_df,
            market_intraday_df=market_intraday_df,
            now_et=now_et,
        )
        pos["shadow_peak_exhaustion"] = peak
        pos["shadow_peak_exhaustion_score"] = peak.get("score")
        pos["shadow_peak_exhaustion_reason"] = peak.get("reason")
        evaluated += 1
        if bool(peak.get("close")):
            trade = _close_by_shadow_exit_omen(pos_key=pos_key, pos=pos, price=price, state=state, omen=peak)
            closed += 1
            close_counts[str(trade.get("exit_reason") or "shadow_peak_exhaustion")] += 1
            if len(closed_samples) < 20:
                closed_samples.append({
                    "ticker": ticker,
                    "reason": trade.get("exit_reason"),
                    "pnl_pct": trade.get("pnl_pct"),
                    "pnl_usd": trade.get("pnl_usd"),
                    "max_profit_pct": trade.get("max_profit_pct"),
                    "max_loss_pct": trade.get("max_loss_pct"),
                    "peak_score": trade.get("shadow_exit_omen_score"),
                    "capture_ratio": ((trade.get("shadow_exit_omen_metrics") or {}).get("capture_ratio")),
                })

    state["last_shadow_peak_exit_tick"] = {
        "time": utc_now(),
        "elapsed_sec": round(time.time() - started, 3),
        "evaluated": evaluated,
        "closed": closed,
        "close_counts": dict(close_counts),
        "closed_samples": closed_samples,
        "errors": errors[-20:],
        "open_count_after": len(state.get("open_positions") or {}),
        "version": PEAK_EXIT_VERSION,
        "regular_hours_only_high_failure": True,
        "same_day_exit_bias": True,
    }
    state["summary"] = _summarize_state(state)
    save_state(state)
    return {"ok": True, **state["last_shadow_peak_exit_tick"], "state": state}
