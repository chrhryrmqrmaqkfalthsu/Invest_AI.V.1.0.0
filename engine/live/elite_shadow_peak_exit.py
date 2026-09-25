"""Elite Shadow Peak Exhaustion Exit v3.

목적:
- v1/v2가 수익반납·구조붕괴를 확인하고 방어하는 로직이라면,
  v3는 수익권 포지션에서 장중 고점권 소진 신호를 감지해 더 이른 익절을 시도한다.
- 실제 주문은 내지 않는다. Elite Shadow 가상 장부에만 청산을 기록한다.

설계 원칙:
- 손실권/본전권 포지션에는 고점 예측 로직을 남발하지 않는다.
- max_profit >= 2.5% 또는 현재 pnl >= 1.8% 이상인 수익권에서만 peak exhaustion을 강하게 본다.
- 단일 분봉 신호가 아니라 고점 반납, VWAP/EMA 약화, lower high, 하락거래량, 상대약세가
  여러 개 겹칠 때만 청산한다.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any

import pandas as pd

from engine.live.elite_shadow_exit_omen import _close_by_shadow_exit_omen, _load_intraday
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

PEAK_EXIT_VERSION = "shadow_peak_exhaustion_v3"
MARKET_PROXY = "QQQ"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return default if math.isnan(out) else out
    except Exception:
        return default


def _pct(a: float, b: float) -> float | None:
    if a > 0.0 and b > 0.0:
        return (a / b - 1.0) * 100.0
    return None


def _add(parts: list[dict[str, Any]], points: float, reason: str, **detail: Any) -> float:
    if points <= 0.0:
        return 0.0
    row = {"points": round(points, 2), "reason": reason}
    row.update(detail)
    parts.append(row)
    return points


def _intraday_peak_snapshot(df: pd.DataFrame | None, price: float, market_df: pd.DataFrame | None = None) -> dict[str, Any]:
    if df is None or df.empty or len(df) < 20:
        return {"ok": False, "reason": "intraday_missing"}
    try:
        work = df.copy().dropna(subset=["Open", "High", "Low", "Close"])
        if work.empty or len(work) < 20:
            return {"ok": False, "reason": "intraday_window_insufficient"}
        close = work["Close"].astype(float)
        high = work["High"].astype(float)
        low = work["Low"].astype(float)
        open_ = work["Open"].astype(float)
        volume = work["Volume"].fillna(0).astype(float) if "Volume" in work.columns else pd.Series([0.0] * len(work), index=work.index)
        current = price if price > 0.0 else float(close.iloc[-1])
        typical = (high + low + close) / 3.0
        vol_sum = float(volume.sum())
        vwap = float((typical * volume).sum() / vol_sum) if vol_sum > 0.0 else float(close.expanding().mean().iloc[-1])
        ema9_series = close.ewm(span=9, adjust=False).mean()
        ema20_series = close.ewm(span=20, adjust=False).mean()
        ema9 = float(ema9_series.iloc[-1])
        ema20 = float(ema20_series.iloc[-1])
        ema9_prev = float(ema9_series.iloc[-6]) if len(ema9_series) >= 6 else ema9
        ema20_prev = float(ema20_series.iloc[-6]) if len(ema20_series) >= 6 else ema20
        ema9_slope_5m_pct = _pct(ema9, ema9_prev)
        ema20_slope_5m_pct = _pct(ema20, ema20_prev)
        day_high = float(max(high.max(), current))
        day_low = float(min(low.min(), current))
        high_idx = high.idxmax()
        try:
            high_pos = int(work.index.get_loc(high_idx))
            bars_since_high = max(0, len(work) - high_pos - 1)
        except Exception:
            bars_since_high = None
        high_giveback_pct = _pct(current, day_high) if day_high > 0.0 else None
        range_position = (current - day_low) / max(day_high - day_low, 0.0001)
        ret_10m = _pct(current, float(close.iloc[-11])) if len(close) > 11 else None
        ret_15m = _pct(current, float(close.iloc[-16])) if len(close) > 16 else None
        ret_30m = _pct(current, float(close.iloc[-31])) if len(close) > 31 else None

        recent_high_10 = float(high.tail(10).max()) if len(high) >= 10 else float(high.max())
        prior_high_10 = float(high.iloc[-20:-10].max()) if len(high) >= 20 else recent_high_10
        recent_low_10 = float(low.tail(10).min()) if len(low) >= 10 else float(low.min())
        prior_low_10 = float(low.iloc[-20:-10].min()) if len(low) >= 20 else recent_low_10
        lower_high_recent = bool(recent_high_10 < prior_high_10 * 0.998) if prior_high_10 > 0.0 else False
        lower_low_recent = bool(recent_low_10 < prior_low_10 * 0.998) if prior_low_10 > 0.0 else False

        tail = work.tail(30).copy()
        up_vol = tail.loc[tail["Close"] >= tail["Open"], "Volume"].fillna(0).astype(float)
        down_vol = tail.loc[tail["Close"] < tail["Open"], "Volume"].fillna(0).astype(float)
        up_avg = float(up_vol.mean()) if len(up_vol) else 0.0
        down_avg = float(down_vol.mean()) if len(down_vol) else 0.0
        red_bar_ratio = float((tail["Close"] < tail["Open"]).sum() / len(tail)) if len(tail) else 0.0
        down_volume_dominant = bool(down_avg > 0.0 and down_avg >= max(up_avg, 1.0) * 1.2 and red_bar_ratio >= 0.45)
        last_range = max(float(high.iloc[-1] - low.iloc[-1]), 0.0001)
        last_close_position = max(0.0, min(1.0, float((current - low.iloc[-1]) / last_range)))
        upper_wick_ratio = max(0.0, min(1.0, float((high.iloc[-1] - max(current, open_.iloc[-1])) / last_range)))

        market_ret_30m = None
        relative_ret_30m = None
        if market_df is not None and not market_df.empty and len(market_df) > 31:
            try:
                mclose = market_df["Close"].dropna().astype(float)
                if len(mclose) > 31:
                    market_ret_30m = _pct(float(mclose.iloc[-1]), float(mclose.iloc[-31]))
                    if market_ret_30m is not None and ret_30m is not None:
                        relative_ret_30m = ret_30m - market_ret_30m
            except Exception:
                market_ret_30m = None
                relative_ret_30m = None

        return {
            "ok": True,
            "price": current,
            "vwap": vwap,
            "ema9": ema9,
            "ema20": ema20,
            "below_vwap": current < vwap,
            "below_ema9": current < ema9,
            "below_ema20": current < ema20,
            "ema9_below_ema20": ema9 < ema20,
            "ema9_slope_5m_pct": ema9_slope_5m_pct,
            "ema20_slope_5m_pct": ema20_slope_5m_pct,
            "intraday_high": day_high,
            "intraday_low": day_low,
            "intraday_high_giveback_pct": high_giveback_pct,
            "intraday_range_position": max(0.0, min(1.0, range_position)),
            "bars_since_intraday_high": bars_since_high,
            "ret_10m_pct": ret_10m,
            "ret_15m_pct": ret_15m,
            "ret_30m_pct": ret_30m,
            "lower_high_recent": lower_high_recent,
            "lower_low_recent": lower_low_recent,
            "recent_high_10m": recent_high_10,
            "prior_high_10m": prior_high_10,
            "recent_low_10m": recent_low_10,
            "prior_low_10m": prior_low_10,
            "up_volume_avg_30m": up_avg,
            "down_volume_avg_30m": down_avg,
            "down_volume_dominant": down_volume_dominant,
            "red_bar_ratio_30m": red_bar_ratio,
            "last_close_position": last_close_position,
            "upper_wick_ratio": upper_wick_ratio,
            "market_ret_30m_pct": market_ret_30m,
            "relative_ret_30m_pct": relative_ret_30m,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"intraday_error:{type(exc).__name__}"}


def evaluate_peak_exhaustion(
    *,
    pos: dict[str, Any],
    price: float,
    intraday_df: pd.DataFrame | None,
    market_intraday_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    entry = _num(pos.get("entry_price"), 0.0)
    if entry <= 0.0 or price <= 0.0:
        return {"version": PEAK_EXIT_VERSION, "close": False, "reason": "invalid_price", "score": 0.0, "reasons": [], "metrics": {}}

    pnl_pct = (price / entry - 1.0) * 100.0
    highest = max(_num(pos.get("highest_price"), entry), price)
    lowest = min(_num(pos.get("lowest_price"), entry), price)
    max_profit_pct = max(_num(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0)
    max_loss_pct = min(_num(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0)
    giveback_pct = max(0.0, max_profit_pct - pnl_pct)
    giveback_ratio = giveback_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0
    capture_ratio = pnl_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0

    # v3는 수익권 고점 소진 감지 전용이다. 본전/손실권은 v1/v2 실패 컷이 담당한다.
    active = max_profit_pct >= 2.5 or pnl_pct >= 1.8
    if not active:
        return {
            "version": PEAK_EXIT_VERSION,
            "close": False,
            "reason": "hold",
            "score": 0.0,
            "reasons": [],
            "metrics": {
                "active": False,
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

    if bars_since_high >= 45:
        score += _add(parts, 12.0, "stale_intraday_high", bars_since_high=int(bars_since_high))
    elif bars_since_high >= 20:
        score += _add(parts, 8.0, "no_new_high_20m", bars_since_high=int(bars_since_high))

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

    # 목표 회수율: v3는 늦은 손절이 아니라 고점권 익절이어야 하므로 현재 수익이 충분히 남아 있을 때만 발동한다.
    reason: str | None = None
    if max_profit_pct >= 8.0 and pnl_pct >= max(5.0, max_profit_pct * 0.62) and score >= 58.0:
        reason = "shadow_peak_exhaustion_8p"
    elif max_profit_pct >= 5.0 and pnl_pct >= max(3.0, max_profit_pct * 0.58) and score >= 56.0:
        reason = "shadow_peak_exhaustion_5p"
    elif max_profit_pct >= 3.0 and pnl_pct >= max(1.8, max_profit_pct * 0.55) and score >= 60.0:
        reason = "shadow_peak_exhaustion_3p"
    elif max_profit_pct >= 2.5 and pnl_pct >= 1.4 and score >= 72.0:
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
        "intraday": snap,
    }
    return {
        "version": PEAK_EXIT_VERSION,
        "close": reason is not None,
        "reason": reason or "hold",
        "score": round(max(0.0, min(100.0, score)), 2),
        "reasons": parts[:18],
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
        peak = evaluate_peak_exhaustion(pos=pos, price=price, intraday_df=intraday_df, market_intraday_df=market_intraday_df)
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
    }
    state["summary"] = _summarize_state(state)
    save_state(state)
    return {"ok": True, **state["last_shadow_peak_exit_tick"], "state": state}
