"""Entry-time concentration candidate scoring.

이 모듈은 Elite Shadow의 '몰빵 후보' 판단을 진입 시점에 고정하기 위한 점수 계산기다.
중요 원칙:
- 진입 후 현재 손익, 오멘, MFE 반납, VWAP/EMA 상태는 점수에 넣지 않는다.
- 점수는 진입 당시 candidate artifact + entry_quality snapshot만 사용한다.
- hard gate를 통과하지 못하면 점수가 높아도 몰빵 후보가 아니다.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

VERSION = "entry_concentration_v1_static_entry_time"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def clamp(value: Any, low: float, high: float) -> float:
    return max(low, min(high, safe_float(value)))


def score_entry_concentration(candidate: dict[str, Any], entry_quality: dict[str, Any] | None) -> dict[str, Any]:
    """진입 시점 몰빵 후보 점수를 계산한다.

    candidate는 build_elite_shadow_report()가 반환한 해당 후보 row이고,
    entry_quality는 assess_shadow_entry_quality()의 결과다.
    """
    entry_quality = entry_quality or {}
    metrics = candidate.get("metrics") or {}
    trade_summary = candidate.get("trade_summary") or {}
    q_metrics = entry_quality.get("metrics") or {}

    stage = str(candidate.get("stage") or "")
    bucket = str(candidate.get("bucket") or "")
    q = safe_float(entry_quality.get("score"), -1.0)
    label = str(entry_quality.get("label") or "")
    size_factor = safe_float(entry_quality.get("size_factor"), 1.0)

    oos_exp = safe_float(metrics.get("oos_expectancy_pct"))
    oos_win = safe_float(metrics.get("oos_win_rate"))
    fitness = safe_float(metrics.get("oos_fitness"))
    trades = safe_float(metrics.get("oos_trade_count")) or safe_float(trade_summary.get("trade_count"))
    avg_mfe = safe_float(trade_summary.get("avg_mfe_pct"))
    worst_dd = abs(safe_float(metrics.get("worst_drawdown_pct")))

    ret5 = safe_float(q_metrics.get("ret_5d_pct"))
    ret3 = safe_float(q_metrics.get("ret_3d_pct"))
    dist_ma5 = safe_float(q_metrics.get("dist_ma5_pct"))
    dist_high5 = safe_float(q_metrics.get("dist_high5_pct"))
    vol = safe_float(q_metrics.get("volume_ratio20"))
    atr = safe_float(q_metrics.get("atr_pct"))
    close_pos = safe_float(q_metrics.get("close_position"))

    expected = 0.0
    expected += clamp(oos_exp * 1.45, 0, 17)
    expected += clamp((oos_win - 60.0) * 0.45, 0, 10)
    expected += clamp(fitness / 120.0 * 4.0, 0, 4)
    expected += clamp(trades / 25.0 * 3.0, 0, 3)
    expected += clamp(avg_mfe / 12.0 * 4.0, 0, 4)
    expected -= clamp((worst_dd - 12.0) * 0.65, 0, 5)

    quality = clamp(q * 0.30, 0, 30)
    if "STRONG" in label:
        quality += 6
    elif "HEALTHY" in label:
        quality += 2
    else:
        quality -= 10
    if stage == "stage2":
        quality += 4
    elif stage == "stage3":
        quality += 1
    quality += {"A_core": 4, "B_stable": 3, "C_momentum": 2, "watch": 0}.get(bucket, 0)
    if size_factor < 0.75:
        quality -= 6

    base = 8.0
    caps: list[str] = []
    if -2 <= ret5 <= 7:
        base += 5
    elif ret5 > 12:
        base -= 7
        caps.append("5일 과열")
    elif ret5 < -5:
        base -= 5
    if -1 <= ret3 <= 5:
        base += 3
    if 0 <= dist_ma5 <= 5:
        base += 3
    elif dist_ma5 < -2:
        base -= 6
    elif dist_ma5 > 8:
        base -= 4
    if -6 <= dist_high5 <= 0:
        base += 3
    elif dist_high5 < -12:
        base -= 5
    if 0.8 <= vol <= 2.2:
        base += 2
    elif vol > 3:
        base -= 3
    elif 0 < vol < 0.5:
        base -= 2
    if 0 < close_pos < 0.25:
        base -= 2

    risk = 10.0
    if worst_dd > 18:
        risk -= 3
    if worst_dd > 25:
        risk -= 3
    if atr > 8:
        risk -= 3
    if q_metrics.get("event_heavy"):
        risk -= 2
    if q_metrics.get("high_vol") or q_metrics.get("low_price"):
        risk -= 3
    if vol > 3:
        risk -= 1

    blocks: list[str] = []
    if q < 75:
        blocks.append("Q<75")
    if stage == "stage3" and q < 80:
        blocks.append("stage3 Q<80")
    if "WEAK" in label:
        blocks.append("WEAK")
    if size_factor < 0.75:
        blocks.append("축소진입")
    if q_metrics.get("event_heavy") and q < 90:
        blocks.append("이벤트 Q<90")
    if (q_metrics.get("high_vol") or q_metrics.get("low_price")) and q < 90:
        blocks.append("고위험 Q<90")
    if base < 8:
        caps.append("베이스 약함")
    if risk < 5:
        caps.append("리스크 높음")

    expected = clamp(expected, 0, 30)
    quality = clamp(quality, 0, 40)
    base = clamp(base, 0, 20)
    risk = clamp(risk, 0, 10)
    score = clamp(expected + quality + base + risk, 0, 100)

    action = "분산/제외"
    allowed = False
    if blocks:
        action = "몰빵 금지"
    elif score >= 85 and not caps:
        action = "TOP 진입 몰빵"
        allowed = True
    elif score >= 78:
        action = "2순위 집중"
        allowed = True
    elif score >= 70:
        action = "관찰 후보"

    return {
        "version": VERSION,
        "calculated_at": utc_now(),
        "source": "entry_tick_snapshot",
        "rank_scope": "full_buy_entries_at_tick",
        "confidence": "exact",
        "reconstructed": False,
        "score": round(score, 6),
        "action": action,
        "allowed": bool(allowed),
        "blocks": blocks,
        "caps": caps,
        "components": {
            "expected": round(expected, 6),
            "quality": round(quality, 6),
            "base": round(base, 6),
            "risk": round(risk, 6),
        },
        "inputs": {
            "ticker": candidate.get("ticker"),
            "stage": stage,
            "bucket": bucket,
            "q": q,
            "label": label,
            "size_factor": size_factor,
            "oos_expectancy_pct": oos_exp,
            "oos_win_rate": oos_win,
            "oos_fitness": fitness,
            "trades": trades,
            "avg_mfe_pct": avg_mfe,
            "worst_drawdown_pct_abs": worst_dd,
            "ret_5d_pct": ret5,
            "ret_3d_pct": ret3,
            "dist_ma5_pct": dist_ma5,
            "dist_high5_pct": dist_high5,
            "volume_ratio20": vol,
            "atr_pct": atr,
            "close_position": close_pos,
            "event_heavy": bool(q_metrics.get("event_heavy")),
            "high_vol": bool(q_metrics.get("high_vol")),
            "low_price": bool(q_metrics.get("low_price")),
        },
    }
