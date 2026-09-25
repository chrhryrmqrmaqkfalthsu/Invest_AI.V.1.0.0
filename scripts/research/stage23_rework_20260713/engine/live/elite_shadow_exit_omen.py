"""Elite Shadow 전용 정밀 청산 오멘.

목적:
- 개별 룰북의 stop/take/trailing과 별도로, Elite Shadow 가상 포지션에만 적용되는
  수익잠금·수익반납·추세붕괴·매도압력 전조를 점수화한다.
- v2는 장중 1분봉 기반 VWAP/EMA/고점갱신 실패/상대약세/하락 거래량까지 함께 본다.
- 실제 broker 주문에는 관여하지 않는다. data/_system/elite_shadow_state.json 과
  data/_system/elite_shadow_trades.jsonl 의 가상 장부만 갱신한다.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from typing import Any

import pandas as pd
import yfinance as yf

from engine.live.elite_shadow_entry_quality import assess_shadow_entry_quality
from engine.live.regular_hours_gate import regular_hours_snapshot
from engine.live.elite_shadow_trader import (
    _event,
    _holding_days,
    _latest_price,
    _load_ohlcv,
    _safe_float,
    _safe_int,
    _summarize_state,
    _acquire_lock,
    _release_lock,
    ShadowStateCorruptionError,
    append_trade,
    load_state,
    save_state,
    utc_now,
)

EXIT_OMEN_VERSION = "shadow_exit_omen_v2"
MARKET_PROXY = "QQQ"
INTRADAY_CACHE_TTL_SEC = 60.0

_intraday_cache: dict[tuple[str, str, str], tuple[pd.DataFrame, float]] = {}


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


def _last_list(df: pd.DataFrame, col: str) -> list[float]:
    try:
        return [_num(x, 0.0) for x in df[col].dropna().tolist()]
    except Exception:
        return []


def _load_intraday(ticker: str, *, period: str = "1d", interval: str = "1m") -> pd.DataFrame | None:
    """청산 오멘 전용 장중 데이터 로더.

    yfinance 1분봉은 종종 빈 값/지연 값이 생기므로, 실패해도 청산 로직 전체를 막지 않고
    daily/live price 기반 v1 신호만 계속 사용한다.
    """
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return None
    key = (ticker, period, interval)
    now = time.time()
    cached = _intraday_cache.get(key)
    if cached is not None:
        df, ts = cached
        if now - ts < INTRADAY_CACHE_TTL_SEC:
            return df
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, prepost=True, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty or len(df) < 15:
        return None
    try:
        df = df.copy()
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in df.columns:
                return None
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if df.empty or len(df) < 15:
            return None
    except Exception:
        return None
    _intraday_cache[key] = (df, now)
    return df


def _technical_snapshot(df: pd.DataFrame | None, price: float) -> dict[str, Any]:
    if df is None or df.empty:
        return {"ok": False, "reason": "ohlcv_missing"}
    closes = _last_list(df, "Close")
    highs = _last_list(df, "High")
    lows = _last_list(df, "Low")
    opens = _last_list(df, "Open")
    volumes = _last_list(df, "Volume") if "Volume" in df.columns else []
    if len(closes) < 25 or len(highs) < 8 or len(lows) < 8:
        return {"ok": False, "reason": "ohlcv_window_insufficient"}

    current = price if price > 0 else closes[-1]
    ma3 = sum(closes[-3:]) / 3.0
    ma5 = sum(closes[-5:]) / 5.0
    ma20 = sum(closes[-20:]) / 20.0
    recent_high5 = max(highs[-5:])
    recent_low5 = min(lows[-5:])
    prev_low = lows[-2]
    prev_high = highs[-2]
    prev_close = closes[-2]
    last_high = max(highs[-1], current)
    last_low = min(lows[-1], current)
    last_open = opens[-1] if opens else closes[-1]
    close_position = (current - last_low) / max(last_high - last_low, 0.0001)
    vol_base = [v for v in volumes[-21:-1] if v > 0]
    volume_ratio20 = volumes[-1] / (sum(vol_base) / len(vol_base)) if volumes and volumes[-1] > 0 and vol_base else None

    return {
        "ok": True,
        "price": current,
        "ma3": ma3,
        "ma5": ma5,
        "ma20": ma20,
        "above_ma3": current >= ma3,
        "above_ma5": current >= ma5,
        "above_ma20": current >= ma20,
        "below_ma3": current < ma3,
        "below_ma5": current < ma5,
        "below_ma20": current < ma20,
        "ret_1d_pct": _pct(current, prev_close),
        "ret_3d_pct": _pct(current, closes[-4]),
        "ret_5d_pct": _pct(current, closes[-6]),
        "dist_ma3_pct": _pct(current, ma3),
        "dist_ma5_pct": _pct(current, ma5),
        "dist_ma20_pct": _pct(current, ma20),
        "dist_high5_pct": _pct(current, recent_high5),
        "bounce_low5_pct": _pct(current, recent_low5),
        "prev_low": prev_low,
        "prev_high": prev_high,
        "below_prev_low": current < prev_low,
        "below_prev_high": current < prev_high,
        "down_day": current < last_open,
        "close_position": max(0.0, min(1.0, close_position)),
        "volume_ratio20": volume_ratio20,
    }


def _intraday_snapshot(df: pd.DataFrame | None, price: float, *, market_df: pd.DataFrame | None = None) -> dict[str, Any]:
    if df is None or df.empty or len(df) < 15:
        return {"ok": False, "reason": "intraday_missing"}
    try:
        work = df.copy().dropna(subset=["Open", "High", "Low", "Close"])
        if work.empty or len(work) < 15:
            return {"ok": False, "reason": "intraday_window_insufficient"}
        close = work["Close"].astype(float)
        high = work["High"].astype(float)
        low = work["Low"].astype(float)
        open_ = work["Open"].astype(float)
        volume = work["Volume"].fillna(0).astype(float) if "Volume" in work.columns else pd.Series([0.0] * len(work), index=work.index)
        current = price if price > 0 else float(close.iloc[-1])
        typical = (high + low + close) / 3.0
        vol_sum = float(volume.sum())
        vwap = float((typical * volume).sum() / vol_sum) if vol_sum > 0 else float(close.expanding().mean().iloc[-1])
        ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        day_high = float(max(high.max(), current))
        day_low = float(min(low.min(), current))
        high_idx = high.idxmax()
        try:
            high_pos = int(work.index.get_loc(high_idx))
            bars_since_high = max(0, len(work) - high_pos - 1)
        except Exception:
            bars_since_high = None
        high_giveback_pct = _pct(current, day_high) if day_high > 0 else None
        day_range_position = (current - day_low) / max(day_high - day_low, 0.0001)
        ret_15m = _pct(current, float(close.iloc[-16])) if len(close) > 16 else None
        ret_30m = _pct(current, float(close.iloc[-31])) if len(close) > 31 else None
        ret_60m = _pct(current, float(close.iloc[-61])) if len(close) > 61 else None

        recent_high = float(high.tail(10).max()) if len(high) >= 10 else float(high.max())
        prior_high = float(high.iloc[-20:-10].max()) if len(high) >= 20 else recent_high
        recent_low = float(low.tail(10).min()) if len(low) >= 10 else float(low.min())
        prior_low = float(low.iloc[-20:-10].min()) if len(low) >= 20 else recent_low
        lower_high_recent = bool(recent_high < prior_high * 0.998) if prior_high > 0 else False
        lower_low_recent = bool(recent_low < prior_low * 0.998) if prior_low > 0 else False

        tail = work.tail(30).copy()
        up_vol = tail.loc[tail["Close"] >= tail["Open"], "Volume"].fillna(0).astype(float)
        down_vol = tail.loc[tail["Close"] < tail["Open"], "Volume"].fillna(0).astype(float)
        up_avg = float(up_vol.mean()) if len(up_vol) else 0.0
        down_avg = float(down_vol.mean()) if len(down_vol) else 0.0
        red_bar_ratio = float((tail["Close"] < tail["Open"]).sum() / len(tail)) if len(tail) else 0.0
        down_volume_dominant = bool(down_avg > 0 and down_avg >= max(up_avg, 1.0) * 1.25 and red_bar_ratio >= 0.45)

        market_ret_30m = None
        relative_ret_30m = None
        if market_df is not None and not market_df.empty and len(market_df) > 31:
            try:
                mclose = market_df["Close"].dropna().astype(float)
                if len(mclose) > 31:
                    mcur = float(mclose.iloc[-1])
                    market_ret_30m = _pct(mcur, float(mclose.iloc[-31]))
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
            "dist_vwap_pct": _pct(current, vwap),
            "dist_ema9_pct": _pct(current, ema9),
            "dist_ema20_pct": _pct(current, ema20),
            "intraday_high": day_high,
            "intraday_low": day_low,
            "intraday_high_giveback_pct": high_giveback_pct,
            "intraday_range_position": max(0.0, min(1.0, day_range_position)),
            "bars_since_intraday_high": bars_since_high,
            "ret_15m_pct": ret_15m,
            "ret_30m_pct": ret_30m,
            "ret_60m_pct": ret_60m,
            "lower_high_recent": lower_high_recent,
            "lower_low_recent": lower_low_recent,
            "recent_high_10m": recent_high,
            "prior_high_10m": prior_high,
            "recent_low_10m": recent_low,
            "prior_low_10m": prior_low,
            "up_volume_avg_30m": up_avg,
            "down_volume_avg_30m": down_avg,
            "down_volume_dominant": down_volume_dominant,
            "red_bar_ratio_30m": red_bar_ratio,
            "market_ret_30m_pct": market_ret_30m,
            "relative_ret_30m_pct": relative_ret_30m,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"intraday_error:{type(exc).__name__}"}


def _current_quality(pos: dict[str, Any], df: pd.DataFrame | None, price: float) -> dict[str, Any]:
    if df is None or df.empty:
        return {"score": None, "label": "QUALITY_UNKNOWN", "primary_reason": "ohlcv_missing", "metrics": {}}
    candidate = {
        "ticker": pos.get("ticker"),
        "stage": pos.get("stage"),
        "bucket": pos.get("bucket"),
    }
    try:
        return assess_shadow_entry_quality(
            candidate=candidate,
            df=df,
            price=price,
            score=_num(pos.get("entry_score"), 0.0),
            threshold=_num(pos.get("entry_threshold"), 0.0),
            ratio=_num(pos.get("entry_ratio"), 0.0),
            reasons=list(pos.get("entry_reasons") or []),
            components={},
        )
    except Exception as exc:
        return {"score": None, "label": "QUALITY_ERROR", "primary_reason": type(exc).__name__, "metrics": {}}


def _entry_label(pos: dict[str, Any]) -> str:
    return str(pos.get("entry_quality_label") or "").upper()


def _add_score(parts: list[dict[str, Any]], points: float, reason: str, **detail: Any) -> float:
    if points <= 0:
        return 0.0
    item = {"points": round(points, 2), "reason": reason}
    item.update(detail)
    parts.append(item)
    return points


def evaluate_shadow_exit_omen(
    *,
    pos: dict[str, Any],
    df: pd.DataFrame | None,
    price: float,
    market_price: float | None = None,
    intraday_df: pd.DataFrame | None = None,
    market_intraday_df: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """열린 Shadow 포지션의 전용 청산 오멘을 계산한다.

    반환값의 close=True이면 기존 룰북 청산과 별개로 Shadow 가상 청산 대상이다.
    """
    entry = _num(pos.get("entry_price"), 0.0)
    if entry <= 0.0 or price <= 0.0:
        return {
            "version": EXIT_OMEN_VERSION,
            "close": False,
            "reason": "invalid_price",
            "score": 0.0,
            "reasons": [],
            "metrics": {"entry": entry, "price": price},
        }

    pnl_pct = (price / entry - 1.0) * 100.0
    highest = max(_num(pos.get("highest_price"), entry), price)
    lowest = min(_num(pos.get("lowest_price"), entry), price)
    max_profit_pct = max(_num(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0)
    max_loss_pct = min(_num(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0)
    giveback_pct = max(0.0, max_profit_pct - pnl_pct)
    giveback_ratio = giveback_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0
    label = _entry_label(pos)
    is_healthy = "HEALTHY" in label
    entry_q = _num(pos.get("entry_quality_score"), -1.0)

    metrics = _technical_snapshot(df, price)
    intraday = _intraday_snapshot(intraday_df, price, market_df=market_intraday_df)
    quality = _current_quality(pos, df, price)
    current_q = quality.get("score")
    current_q_f = _num(current_q, -1.0) if current_q is not None else -1.0

    market_entry = _num(pos.get("market_entry_price"), 0.0)
    market_ret_since_entry = None
    relative_ret_since_entry = None
    if market_price and market_entry > 0.0:
        market_ret_since_entry = (market_price / market_entry - 1.0) * 100.0
        relative_ret_since_entry = pnl_pct - market_ret_since_entry

    parts: list[dict[str, Any]] = []
    score = 0.0

    if giveback_ratio >= 0.70 and max_profit_pct >= 2.0:
        score += _add_score(parts, 32.0, "profit_giveback_extreme", giveback_ratio=round(giveback_ratio, 3))
    elif giveback_ratio >= 0.55 and max_profit_pct >= 2.0:
        score += _add_score(parts, 24.0, "profit_giveback_large", giveback_ratio=round(giveback_ratio, 3))
    elif giveback_ratio >= 0.40 and max_profit_pct >= 2.0:
        score += _add_score(parts, 15.0, "profit_giveback_warning", giveback_ratio=round(giveback_ratio, 3))

    if pnl_pct < 0.0 and max_profit_pct >= 2.0:
        score += _add_score(parts, 18.0, "winner_turned_negative")
    if max_profit_pct < 2.0 and pnl_pct <= -2.0:
        score += _add_score(parts, 22.0, "failed_followthrough_loss")
    if is_healthy and pnl_pct <= -1.5:
        score += _add_score(parts, 18.0, "healthy_weak_loss")

    if metrics.get("ok"):
        ret1 = _num(metrics.get("ret_1d_pct"), 0.0)
        vol = _num(metrics.get("volume_ratio20"), 0.0)
        close_pos = _num(metrics.get("close_position"), 0.5)
        dist_ma5 = _num(metrics.get("dist_ma5_pct"), 0.0)
        dist_ma20 = _num(metrics.get("dist_ma20_pct"), 0.0)
        if metrics.get("below_ma5"):
            score += _add_score(parts, 10.0, "below_ma5", dist_ma5=round(dist_ma5, 3))
        if metrics.get("below_ma20") and max_profit_pct >= 2.0:
            score += _add_score(parts, 8.0, "below_ma20", dist_ma20=round(dist_ma20, 3))
        if metrics.get("below_prev_low") and max_profit_pct >= 2.0:
            score += _add_score(parts, 18.0, "previous_low_break")
        if ret1 <= -1.5:
            score += _add_score(parts, 9.0, "daily_down_move", ret_1d_pct=round(ret1, 3))
        if ret1 <= -1.5 and vol >= 1.3 and close_pos <= 0.35:
            score += _add_score(parts, 18.0, "high_volume_sell_pressure", volume_ratio20=round(vol, 3), close_position=round(close_pos, 3))
        elif giveback_ratio >= 0.45 and vol >= 1.2 and close_pos <= 0.45 and max_profit_pct >= 3.0:
            score += _add_score(parts, 14.0, "high_volume_giveback", volume_ratio20=round(vol, 3), close_position=round(close_pos, 3))
        elif close_pos <= 0.25 and max_profit_pct >= 3.0:
            score += _add_score(parts, 8.0, "candle_lower_range", close_position=round(close_pos, 3))

    if intraday.get("ok"):
        high_gb = _num(intraday.get("intraday_high_giveback_pct"), 0.0)
        ret15 = _num(intraday.get("ret_15m_pct"), 0.0)
        ret30 = _num(intraday.get("ret_30m_pct"), 0.0)
        rel30 = _num(intraday.get("relative_ret_30m_pct"), 0.0)
        red_ratio = _num(intraday.get("red_bar_ratio_30m"), 0.0)
        if intraday.get("below_vwap") and (max_profit_pct >= 1.0 or pnl_pct < 0.0):
            score += _add_score(parts, 12.0, "intraday_below_vwap", dist_vwap_pct=round(_num(intraday.get("dist_vwap_pct"), 0.0), 3))
        if intraday.get("below_ema9") and (max_profit_pct >= 1.0 or pnl_pct < 0.0):
            score += _add_score(parts, 8.0, "intraday_below_ema9", dist_ema9_pct=round(_num(intraday.get("dist_ema9_pct"), 0.0), 3))
        if intraday.get("below_ema20") and (max_profit_pct >= 1.0 or pnl_pct < 0.0):
            score += _add_score(parts, 10.0, "intraday_below_ema20", dist_ema20_pct=round(_num(intraday.get("dist_ema20_pct"), 0.0), 3))
        if intraday.get("ema9_below_ema20") and max_profit_pct >= 1.5:
            score += _add_score(parts, 10.0, "intraday_ema9_below_ema20")
        if high_gb <= -3.0 and max_profit_pct >= 2.0:
            score += _add_score(parts, 20.0, "intraday_peak_giveback_large", intraday_high_giveback_pct=round(high_gb, 3))
        elif high_gb <= -1.5 and max_profit_pct >= 2.0:
            score += _add_score(parts, 12.0, "intraday_peak_giveback", intraday_high_giveback_pct=round(high_gb, 3))
        if ret30 <= -2.0:
            score += _add_score(parts, 14.0, "intraday_30m_drop_large", ret_30m_pct=round(ret30, 3))
        elif ret30 <= -1.0:
            score += _add_score(parts, 8.0, "intraday_30m_drop", ret_30m_pct=round(ret30, 3))
        elif ret15 <= -1.0:
            score += _add_score(parts, 6.0, "intraday_15m_drop", ret_15m_pct=round(ret15, 3))
        if intraday.get("lower_high_recent") and max_profit_pct >= 2.0:
            score += _add_score(parts, 10.0, "intraday_lower_high")
        if intraday.get("lower_low_recent") and (max_profit_pct >= 1.5 or pnl_pct < 0.0):
            score += _add_score(parts, 12.0, "intraday_lower_low")
        if intraday.get("down_volume_dominant"):
            score += _add_score(parts, 10.0, "intraday_down_volume_dominant", red_bar_ratio_30m=round(red_ratio, 3))
        if red_ratio >= 0.65 and (max_profit_pct >= 1.5 or pnl_pct < 0.0):
            score += _add_score(parts, 8.0, "intraday_red_bar_cluster", red_bar_ratio_30m=round(red_ratio, 3))
        if rel30 <= -1.5:
            score += _add_score(parts, 14.0, "intraday_market_relative_weakness_large", relative_ret_30m_pct=round(rel30, 3))
        elif rel30 <= -0.8:
            score += _add_score(parts, 8.0, "intraday_market_relative_weakness", relative_ret_30m_pct=round(rel30, 3))

    if current_q is not None:
        if current_q_f < 45.0:
            score += _add_score(parts, 24.0, "current_quality_failed", current_q=round(current_q_f, 2), entry_q=round(entry_q, 2))
        elif current_q_f < 60.0:
            score += _add_score(parts, 14.0, "current_quality_weakened", current_q=round(current_q_f, 2), entry_q=round(entry_q, 2))
        if entry_q >= 90.0 and current_q_f >= 0.0 and current_q_f <= entry_q - 35.0:
            score += _add_score(parts, 12.0, "q90_quality_collapse", current_q=round(current_q_f, 2), entry_q=round(entry_q, 2))

    if relative_ret_since_entry is not None:
        if relative_ret_since_entry <= -3.0:
            score += _add_score(parts, 18.0, "market_relative_weakness_large", relative_ret=round(relative_ret_since_entry, 3))
        elif relative_ret_since_entry <= -1.5:
            score += _add_score(parts, 10.0, "market_relative_weakness", relative_ret=round(relative_ret_since_entry, 3))

    reason: str | None = None

    # 하드 청산: 수익을 크게 냈던 포지션은 본전/음전 전에 잠근다.
    if max_profit_pct >= 8.0 and pnl_pct <= max(4.0, max_profit_pct * 0.55):
        reason = "shadow_profit_giveback_8"
    elif max_profit_pct >= 5.0 and pnl_pct <= max(2.0, max_profit_pct * 0.50):
        reason = "shadow_profit_giveback_5"
    elif max_profit_pct >= 3.0 and pnl_pct <= max(1.0, max_profit_pct * 0.45):
        reason = "shadow_profit_giveback_3"
    elif max_profit_pct >= 2.0 and pnl_pct <= 0.3:
        reason = "shadow_profit_lock_2"

    # 진입 실패와 HEALTHY 약세는 수익잠금보다 뒤에 두되, 손실 확대는 빠르게 차단한다.
    if reason is None and max_profit_pct < 2.0 and pnl_pct <= -2.0:
        reason = "shadow_failed_followthrough"
    if reason is None and is_healthy and pnl_pct <= -1.5:
        reason = "shadow_healthy_weak_cut"
    if reason is None and is_healthy and max_profit_pct >= 2.0 and pnl_pct <= 0.8:
        reason = "shadow_healthy_profit_lock"

    if reason is None and metrics.get("ok"):
        if max_profit_pct >= 3.0 and metrics.get("below_ma5") and pnl_pct <= max_profit_pct * 0.65:
            reason = "shadow_ma5_break_after_profit"
        elif max_profit_pct >= 3.0 and metrics.get("below_prev_low"):
            reason = "shadow_prev_low_break_after_profit"
        elif _num(metrics.get("ret_1d_pct"), 0.0) <= -1.5 and _num(metrics.get("volume_ratio20"), 0.0) >= 1.3 and _num(metrics.get("close_position"), 0.5) <= 0.35 and (max_profit_pct >= 1.5 or pnl_pct <= -1.0):
            reason = "shadow_volume_sell_pressure"
        elif max_profit_pct >= 3.0 and giveback_ratio >= 0.45 and _num(metrics.get("volume_ratio20"), 0.0) >= 1.2 and _num(metrics.get("close_position"), 0.5) <= 0.45:
            reason = "shadow_high_volume_giveback"

    if reason is None and intraday.get("ok"):
        high_gb = _num(intraday.get("intraday_high_giveback_pct"), 0.0)
        ret30 = _num(intraday.get("ret_30m_pct"), 0.0)
        rel30 = _num(intraday.get("relative_ret_30m_pct"), 0.0)
        if max_profit_pct >= 2.0 and giveback_ratio >= 0.35 and intraday.get("below_vwap") and intraday.get("below_ema9"):
            reason = "shadow_intraday_vwap_ema_break"
        elif max_profit_pct >= 3.0 and high_gb <= -2.0 and intraday.get("lower_high_recent"):
            reason = "shadow_intraday_peak_failure"
        elif pnl_pct <= -1.2 and intraday.get("below_vwap") and intraday.get("below_ema20"):
            reason = "shadow_intraday_failed_below_vwap"
        elif ret30 <= -1.0 and intraday.get("below_vwap") and intraday.get("down_volume_dominant") and (max_profit_pct >= 1.0 or pnl_pct <= -0.8):
            reason = "shadow_intraday_sell_pressure"
        elif rel30 <= -1.5 and intraday.get("below_vwap") and (max_profit_pct >= 1.5 or pnl_pct <= -0.8):
            reason = "shadow_intraday_relative_break"

    if reason is None and current_q is not None:
        if entry_q >= 75.0 and current_q_f < 45.0 and (pnl_pct <= 0.8 or giveback_ratio >= 0.45):
            reason = "shadow_current_q_failed"
        elif entry_q >= 90.0 and current_q_f < 60.0 and max_profit_pct >= 3.0 and giveback_ratio >= 0.35:
            reason = "shadow_q90_quality_weakened"

    if reason is None and score >= 75.0 and (pnl_pct < 0.0 or max_profit_pct >= 2.0):
        reason = "shadow_exit_omen_score"

    out_metrics = {
        "entry_price": entry,
        "price": price,
        "pnl_pct": pnl_pct,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "giveback_pct": giveback_pct,
        "giveback_ratio": giveback_ratio,
        "entry_q": entry_q,
        "current_q": current_q,
        "current_q_label": quality.get("label"),
        "market_proxy": MARKET_PROXY,
        "market_entry_price": market_entry or None,
        "market_price": market_price,
        "market_ret_since_entry": market_ret_since_entry,
        "relative_ret_since_entry": relative_ret_since_entry,
    }
    if metrics.get("ok"):
        out_metrics.update({
            k: metrics.get(k)
            for k in [
                "ret_1d_pct",
                "ret_3d_pct",
                "ret_5d_pct",
                "dist_ma5_pct",
                "dist_ma20_pct",
                "dist_high5_pct",
                "bounce_low5_pct",
                "below_ma5",
                "below_ma20",
                "below_prev_low",
                "close_position",
                "volume_ratio20",
            ]
        })
    if intraday.get("ok"):
        out_metrics["intraday"] = {
            k: intraday.get(k)
            for k in [
                "below_vwap",
                "below_ema9",
                "below_ema20",
                "ema9_below_ema20",
                "dist_vwap_pct",
                "dist_ema9_pct",
                "dist_ema20_pct",
                "intraday_high_giveback_pct",
                "intraday_range_position",
                "bars_since_intraday_high",
                "ret_15m_pct",
                "ret_30m_pct",
                "ret_60m_pct",
                "lower_high_recent",
                "lower_low_recent",
                "down_volume_dominant",
                "red_bar_ratio_30m",
                "market_ret_30m_pct",
                "relative_ret_30m_pct",
            ]
        }
    else:
        out_metrics["intraday"] = {"ok": False, "reason": intraday.get("reason")}

    return {
        "version": EXIT_OMEN_VERSION,
        "close": reason is not None,
        "reason": reason or "hold",
        "score": round(max(0.0, min(100.0, score)), 2),
        "reasons": parts[:16],
        "metrics": out_metrics,
        "current_quality": {
            "score": quality.get("score"),
            "label": quality.get("label"),
            "primary_reason": quality.get("primary_reason"),
            "reasons": list(quality.get("reasons") or [])[:8],
        },
    }


def _close_by_shadow_exit_omen(
    *,
    pos_key: str,
    pos: dict[str, Any],
    price: float,
    state: dict[str, Any],
    omen: dict[str, Any],
) -> dict[str, Any]:
    entry = _safe_float(pos.get("entry_price"), 0.0)
    shares = _safe_float(pos.get("shares"), 0.0)
    pnl_pct = (price / entry - 1.0) * 100.0 if entry > 0.0 and price > 0.0 else 0.0
    pnl_usd = shares * (price - entry)
    metrics = omen.get("metrics") or {}
    trade = {
        "_comment": "Elite shadow virtual closed trade. No broker order was placed.",
        "position_id": pos.get("position_id"),
        "candidate_id": pos_key,
        "ticker": pos.get("ticker"),
        "stage": pos.get("stage"),
        "bucket": pos.get("bucket"),
        "rulebook_hash_short": pos.get("rulebook_hash_short"),
        "opened_at": pos.get("opened_at"),
        "closed_at": utc_now(),
        "entry_price": entry,
        "exit_price": price,
        "shares": shares,
        "notional": _safe_float(pos.get("notional"), 0.0),
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "exit_reason": str(omen.get("reason") or "shadow_exit_omen"),
        "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        "max_profit_pct": _safe_float(metrics.get("max_profit_pct"), _safe_float(pos.get("max_profit_pct"), 0.0)),
        "max_loss_pct": _safe_float(metrics.get("max_loss_pct"), _safe_float(pos.get("max_loss_pct"), 0.0)),
        "entry_score": pos.get("entry_score"),
        "entry_threshold": pos.get("entry_threshold"),
        "entry_ratio": pos.get("entry_ratio"),
        "entry_quality_score": pos.get("entry_quality_score"),
        "entry_quality_label": pos.get("entry_quality_label"),
        "entry_quality_primary_reason": (pos.get("entry_quality") or {}).get("primary_reason"),
        "shadow_exit_omen_version": omen.get("version"),
        "shadow_exit_omen_score": omen.get("score"),
        "shadow_exit_omen_reasons": omen.get("reasons"),
        "shadow_exit_omen_metrics": metrics,
    }
    append_trade(trade)
    state["open_positions"].pop(pos_key, None)
    state["closed_count"] = _safe_int(state.get("closed_count"), 0) + 1
    _event(
        state,
        "CLOSE",
        str(pos.get("ticker") or ""),
        f"{trade['exit_reason']} pnl={pnl_pct:+.2f}% omen={omen.get('score')} price={price:.2f}",
        trade,
    )
    return trade


def run_shadow_exit_omen_tick(*, max_positions: int | None = None) -> dict[str, Any]:
    """열린 Elite Shadow 포지션에 전용 청산 오멘을 overlay로 적용한다."""
    if not _acquire_lock():
        return {"ok": False, "reason": "shadow_state_lock_busy"}
    started = time.time()
    try:
        state = load_state()
    except ShadowStateCorruptionError as exc:
        _release_lock()
        return {"ok": False, "reason": "state_corrupt", "error": str(exc)}
    try:
        decision_gate = regular_hours_snapshot()
        if not bool(decision_gate.get("allow_decision")):
            state["last_shadow_exit_omen_tick"] = {
                "time": utc_now(),
                "elapsed_sec": round(time.time() - started, 3),
                "evaluated": 0,
                "closed": 0,
                "close_counts": {},
                "closed_samples": [],
                "errors": [],
                "open_count_after": len(state.get("open_positions") or {}),
                "version": EXIT_OMEN_VERSION,
                "decision_gate": decision_gate,
                "skipped_reason": "outside_regular_hours_exit_omen_blocked",
            }
            state["summary"] = _summarize_state(state)
            save_state(state)
            return {"ok": True, **state["last_shadow_exit_omen_tick"], "state": state}

        open_items = list((state.get("open_positions") or {}).items())
        if max_positions is not None:
            open_items = open_items[: max(0, int(max_positions))]
    
        market_df = _load_ohlcv(MARKET_PROXY)
        market_price = _latest_price(MARKET_PROXY, market_df) if market_df is not None else None
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
            if market_price and not _safe_float(pos.get("market_entry_price"), 0.0):
                pos["market_proxy"] = MARKET_PROXY
                pos["market_entry_price"] = market_price
    
            intraday_df = _load_intraday(ticker)
            omen = evaluate_shadow_exit_omen(
                pos=pos,
                df=df,
                price=price,
                market_price=market_price,
                intraday_df=intraday_df,
                market_intraday_df=market_intraday_df,
            )
            pos["shadow_exit_omen"] = omen
            pos["shadow_exit_omen_score"] = omen.get("score")
            pos["shadow_exit_omen_reason"] = omen.get("reason")
            evaluated += 1
            if bool(omen.get("close")):
                trade = _close_by_shadow_exit_omen(pos_key=pos_key, pos=pos, price=price, state=state, omen=omen)
                closed += 1
                close_counts[str(trade.get("exit_reason") or "shadow_exit_omen")] += 1
                if len(closed_samples) < 20:
                    closed_samples.append({
                        "ticker": ticker,
                        "reason": trade.get("exit_reason"),
                        "pnl_pct": trade.get("pnl_pct"),
                        "pnl_usd": trade.get("pnl_usd"),
                        "max_profit_pct": trade.get("max_profit_pct"),
                        "max_loss_pct": trade.get("max_loss_pct"),
                        "omen_score": trade.get("shadow_exit_omen_score"),
                    })
    
        state["last_shadow_exit_omen_tick"] = {
            "time": utc_now(),
            "elapsed_sec": round(time.time() - started, 3),
            "evaluated": evaluated,
            "closed": closed,
            "close_counts": dict(close_counts),
            "closed_samples": closed_samples,
            "errors": errors[-20:],
            "open_count_after": len(state.get("open_positions") or {}),
            "version": EXIT_OMEN_VERSION,
            "decision_gate": decision_gate,
        }
        state["summary"] = _summarize_state(state)
        save_state(state)
        return {"ok": True, **state["last_shadow_exit_omen_tick"], "state": state}
    finally:
        _release_lock()
