"""Elite Shadow 신규 진입 품질 필터.

목적:
- entry_score가 높더라도 가격이 따라오지 않는 이벤트/BB/RSI 가짜 신호를 줄인다.
- 저가/고변동/과열추격 종목은 차단하거나 가상 notional을 줄인다.
- 실제 broker 주문에는 관여하지 않는다. elite shadow 가상 ledger의 신규 OPEN 전에만 사용한다.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

FILTER_VERSION = "shadow_entry_quality_v1"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return default if math.isnan(out) else out
    except Exception:
        return default


def _pct(a: float, b: float) -> float | None:
    if a > 0 and b > 0:
        return (a / b - 1.0) * 100.0
    return None


def _quality_label(score: float) -> str:
    if score >= 75.0:
        return "STRONG_FOLLOW_THROUGH"
    if score >= 60.0:
        return "HEALTHY_FOLLOW_THROUGH"
    if score >= 45.0:
        return "WEAK_FOLLOW_THROUGH"
    return "FAILED_FOLLOW_THROUGH"


def _reason_score(reasons: list[str], keyword: str) -> float:
    total = 0.0
    for reason in reasons:
        if keyword not in str(reason):
            continue
        for match in re.findall(r"\(([+-]?\d+(?:\.\d+)?)\)", str(reason)):
            total += _num(match, 0.0)
        for match in re.findall(r"\(([+]\d+(?:\.\d+)?)", str(reason)):
            total += _num(match, 0.0)
    return total


def _has_reason(reasons: list[str], *keywords: str) -> bool:
    text = " | ".join(str(r) for r in reasons)
    return any(k in text for k in keywords)


def _last_list(df: pd.DataFrame, col: str) -> list[float]:
    try:
        return [_num(x, 0.0) for x in df[col].dropna().tolist()]
    except Exception:
        return []


def _technical_snapshot(df: pd.DataFrame, price: float) -> dict[str, Any]:
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
    recent_low5 = min(lows[-5:])
    recent_high5 = max(highs[-5:])
    c1, c2, c3 = closes[-1], closes[-2], closes[-3]
    l1, l2, l3 = lows[-1], lows[-2], lows[-3]
    h1, h2 = highs[-1], highs[-2]
    o1 = opens[-1] if opens else c1
    prior_low3 = min(lows[-5:-2])
    vol_base = [v for v in volumes[-21:-1] if v > 0]
    volume_ratio20 = volumes[-1] / (sum(vol_base) / len(vol_base)) if volumes and volumes[-1] > 0 and vol_base else None
    close_position = (c1 - l1) / max(h1 - l1, 0.0001) if h1 > 0 and l1 > 0 else 0.0
    return {
        "ok": True,
        "price": current,
        "last_close": c1,
        "ret_1d_pct": _pct(current, closes[-2]),
        "ret_3d_pct": _pct(current, closes[-4]),
        "ret_5d_pct": _pct(current, closes[-6]),
        "ret_10d_pct": _pct(current, closes[-11]) if len(closes) > 11 else None,
        "dist_ma3_pct": _pct(current, ma3),
        "dist_ma5_pct": _pct(current, ma5),
        "dist_ma20_pct": _pct(current, ma20),
        "bounce_low5_pct": _pct(current, recent_low5),
        "dist_high5_pct": _pct(current, recent_high5),
        "above_ma3": current >= ma3,
        "above_ma5": current >= ma5,
        "above_ma20": current >= ma20,
        "above_prev_high": current > h2,
        "higher_close": bool(c1 > c2 >= c3 or current > c1 >= c2),
        "higher_low": bool(l1 > l2 >= l3 or min(lows[-2:]) > prior_low3 * 1.005),
        "up_day": c1 >= o1,
        "close_position": close_position,
        "volume_ratio20": volume_ratio20,
    }


def _follow_through_score(m: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    bounce = _num(m.get("bounce_low5_pct"), 0.0)
    ret1 = _num(m.get("ret_1d_pct"), 0.0)
    ret3 = _num(m.get("ret_3d_pct"), 0.0)
    dist_ma5 = _num(m.get("dist_ma5_pct"), 0.0)
    dist_high5 = _num(m.get("dist_high5_pct"), 0.0)
    vol = m.get("volume_ratio20")
    vol_f = _num(vol, 0.0) if vol is not None else 0.0

    if bounce >= 15.0:
        score += 22.0
        reasons.append(f"5일 저점 대비 강한 회복 {bounce:.2f}%")
    elif bounce >= 8.0:
        score += 16.0
        reasons.append(f"5일 저점 대비 회복 {bounce:.2f}%")
    elif bounce >= 4.0:
        score += 10.0
        reasons.append(f"5일 저점 대비 약한 회복 {bounce:.2f}%")

    if ret1 > 3.0:
        score += 14.0
        reasons.append(f"1일 follow-through 강함 {ret1:.2f}%")
    elif ret1 > 0.0:
        score += 8.0
        reasons.append(f"1일 follow-through 양수 {ret1:.2f}%")

    if ret3 > 5.0:
        score += 10.0
        reasons.append(f"3일 추세 양호 {ret3:.2f}%")
    elif ret3 > 0.0:
        score += 5.0

    if m.get("above_ma5"):
        score += 14.0
        reasons.append("MA5 회복")
    if m.get("above_ma20"):
        score += 8.0
        reasons.append("MA20 위")
    if m.get("higher_close"):
        score += 10.0
        reasons.append("higher close")
    if m.get("higher_low"):
        score += 10.0
        reasons.append("higher low")
    if m.get("above_prev_high"):
        score += 10.0
        reasons.append("전일 고점 돌파")
    if _num(m.get("close_position"), 0.0) >= 0.70:
        score += 8.0
        reasons.append("당일 범위 상단 위치")
    if vol_f >= 1.5 and ret1 > 0:
        score += 10.0
        reasons.append(f"거래량 동반 {vol_f:.2f}x")
    elif vol_f >= 1.1 and ret1 > 0:
        score += 6.0
        reasons.append(f"거래량 보통 이상 {vol_f:.2f}x")

    if ret1 < -2.0:
        score -= 12.0
        reasons.append(f"1일 하락 follow-through {ret1:.2f}%")
    if dist_ma5 < -3.0:
        score -= 10.0
        reasons.append(f"MA5 아래 {dist_ma5:.2f}%")
    if dist_high5 < -12.0:
        score -= 8.0
        reasons.append(f"5일 고점 회복 실패 {dist_high5:.2f}%")

    return round(max(0.0, min(100.0, score)), 2), reasons


def assess_shadow_entry_quality(
    *,
    candidate: dict[str, Any],
    df: pd.DataFrame,
    price: float,
    score: float,
    threshold: float,
    ratio: float,
    reasons: list[str],
    components: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """신규 Shadow OPEN 허용 여부와 size factor를 반환한다."""
    metrics = _technical_snapshot(df, price)
    if not metrics.get("ok"):
        return {
            "version": FILTER_VERSION,
            "allow": True,
            "size_factor": 1.0,
            "label": "QUALITY_UNKNOWN",
            "score": None,
            "primary_reason": "quality_unknown",
            "reasons": [metrics.get("reason") or "metrics_missing"],
            "metrics": metrics,
        }

    q_score, q_reasons = _follow_through_score(metrics)
    label = _quality_label(q_score)
    ticker = str(candidate.get("ticker") or "").upper()
    bucket = str(candidate.get("bucket") or "")
    stage = str(candidate.get("stage") or "")
    event_score = _reason_score(reasons, "이벤트반응")
    bb_reason = _has_reason(reasons, "BB근접")
    rsi_reason = _has_reason(reasons, "RSI")
    event_heavy = event_score >= 5.0 or (ratio >= 4.0 and _has_reason(reasons, "이벤트반응"))
    bottom_fishing = (bb_reason or rsi_reason) and _num(metrics.get("dist_ma5_pct"), 0.0) < 0.0
    atr = _num(df["ATR"].iloc[-1], 0.0) if "ATR" in df.columns else 0.0
    atr_pct = atr / price * 100.0 if price > 0 and atr > 0 else None
    high_vol = (atr_pct is not None and atr_pct >= 8.0) or _num(metrics.get("bounce_low5_pct"), 0.0) >= 25.0
    low_price = price < 5.0
    overheat = _num(metrics.get("ret_5d_pct"), 0.0) >= 20.0 and _num(metrics.get("dist_ma20_pct"), 0.0) >= 18.0
    no_follow = not (
        metrics.get("above_ma5")
        or _num(metrics.get("ret_1d_pct"), 0.0) > 0.0
        or (_num(metrics.get("bounce_low5_pct"), 0.0) >= 8.0 and _num(metrics.get("volume_ratio20"), 0.0) >= 1.1)
    )

    allow = True
    size_factor = 1.0
    block_reasons: list[str] = []
    reduce_reasons: list[str] = []

    if q_score < 45.0:
        allow = False
        block_reasons.append("failed_follow_through_q_lt_45")
    if allow and no_follow and q_score < 60.0:
        allow = False
        block_reasons.append("no_price_follow_through")
    if allow and event_heavy and q_score < 60.0:
        allow = False
        block_reasons.append("event_heavy_without_follow_through")
    if allow and event_heavy and not metrics.get("above_ma5") and q_score < 75.0:
        allow = False
        block_reasons.append("event_heavy_below_ma5")
    if allow and bottom_fishing and q_score < 60.0:
        allow = False
        block_reasons.append("bottom_fishing_failed")
    if allow and overheat and _num(metrics.get("ret_1d_pct"), 0.0) <= 0.0:
        allow = False
        block_reasons.append("overheat_reversal")
    if allow and (low_price or high_vol) and q_score < 60.0:
        allow = False
        block_reasons.append("high_risk_weak_quality")

    if allow:
        if low_price or high_vol:
            size_factor = min(size_factor, 0.7 if q_score >= 75.0 else 0.5)
            reduce_reasons.append("high_vol_or_low_price_size_cap")
        if event_heavy and q_score < 75.0:
            size_factor = min(size_factor, 0.6)
            reduce_reasons.append("event_heavy_size_cap")
        if overheat:
            size_factor = min(size_factor, 0.5)
            reduce_reasons.append("overheat_size_cap")
        if bucket == "A_core" and q_score < 60.0:
            size_factor = min(size_factor, 0.5)
            reduce_reasons.append("a_core_weak_quality_cap")

    primary = block_reasons[0] if block_reasons else reduce_reasons[0] if reduce_reasons else "passed"
    metrics = dict(metrics)
    metrics.update(
        {
            "atr_pct": atr_pct,
            "event_score": event_score,
            "event_heavy": event_heavy,
            "bottom_fishing": bottom_fishing,
            "overheat": overheat,
            "high_vol": high_vol,
            "low_price": low_price,
            "no_follow": no_follow,
        }
    )
    return {
        "version": FILTER_VERSION,
        "allow": allow,
        "size_factor": round(size_factor, 3),
        "label": label,
        "score": q_score,
        "primary_reason": primary,
        "block_reasons": block_reasons,
        "reduce_reasons": reduce_reasons,
        "reasons": q_reasons[:10],
        "metrics": metrics,
        "input": {
            "ticker": ticker,
            "stage": stage,
            "bucket": bucket,
            "entry_score": score,
            "entry_threshold": threshold,
            "entry_ratio": ratio,
            "entry_reasons": reasons[:8],
            "components": components or {},
        },
    }
