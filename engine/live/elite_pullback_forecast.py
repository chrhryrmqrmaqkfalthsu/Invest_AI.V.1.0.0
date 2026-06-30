"""Elite pullback outcome forecaster.

- strategy sim의 눌림 후보/가상 보유 포지션을 과거 동일 룰북 거래와 비교한다.
- 최근 OHLCV 기반 반등 품질 점수를 같이 본다.
- 과거 기록과 현재 반등 품질이 충돌하면 억지로 BASE로 바꾸지 않고 MIXED_CONFLICT(경합)로 표시한다.
- 실제 broker 주문, live positions.json, strategy state 파일은 수정하지 않는다.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path("exp_batch_stage123_2009_20260616_full")
_TRADE_CACHE_TTL_SEC = 900.0
_CANDIDATE_CACHE_TTL_SEC = 900.0
_REBOUND_QUALITY_CACHE_TTL_SEC = 60.0
_trade_cache: dict[tuple[str, str, str], tuple[list[dict[str, Any]], float]] = {}
_candidate_cache: tuple[dict[str, dict[str, Any]], float] | None = None
_rebound_quality_cache: dict[str, tuple[dict[str, Any], float]] = {}

PULLBACK_GATES = {"BUY_PULLBACK_REENTRY", "WAIT_PULLBACK_CONFIRM"}
OUTCOME_KEYS = ("REBOUND_TO_TARGET", "BASE_HOLD", "DROP_MORE")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _percentile(values: list[float], q: float) -> float | None:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    q = _clamp(q, 0.0, 1.0)
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def _load_jsonl(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _candidate_map() -> dict[str, dict[str, Any]]:
    global _candidate_cache
    now = time.time()
    if _candidate_cache is not None:
        data, ts = _candidate_cache
        if now - ts < _CANDIDATE_CACHE_TTL_SEC:
            return data
    try:
        from engine.live.elite_shadow_report import build_elite_shadow_report

        report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
        data = {str(c.get("candidate_id") or ""): c for c in report.get("candidates") or [] if c.get("candidate_id")}
    except Exception:
        data = {}
    _candidate_cache = (data, now)
    return data


def _trade_source_for(position: dict[str, Any]) -> tuple[Path | None, str, str]:
    stage = str(position.get("stage") or "").lower()
    ticker = str(position.get("ticker") or "").upper().strip()
    rulebook_hash = str(position.get("rulebook_hash") or "")
    if stage == "stage3" and ticker and rulebook_hash:
        return ROOT / "tickers" / ticker / "stage3" / "exit_trades.jsonl", "final_rulebook_hash", rulebook_hash
    candidate_id = str(position.get("candidate_id") or "")
    candidate = _candidate_map().get(candidate_id) or {}
    rulebook_hash = rulebook_hash or str(candidate.get("rulebook_hash") or "")
    if stage == "stage2" and candidate.get("trade_file") and rulebook_hash:
        return ROOT / str(candidate.get("trade_file")), "rulebook_hash", rulebook_hash
    return None, "", rulebook_hash


def _load_rulebook_trades(position: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path, hash_key, rulebook_hash = _trade_source_for(position)
    diag = {
        "path": str(path) if path else None,
        "hash_key": hash_key,
        "rulebook_hash_short": str(rulebook_hash or "")[:12],
        "ok": False,
        "reason": None,
    }
    if not path or not hash_key or not rulebook_hash:
        diag["reason"] = "trade_source_missing"
        return [], diag
    if not path.exists():
        diag["reason"] = "trade_file_missing"
        return [], diag
    cache_key = (str(path), hash_key, rulebook_hash)
    now = time.time()
    cached = _trade_cache.get(cache_key)
    if cached is not None:
        rows, ts = cached
        if now - ts < _TRADE_CACHE_TTL_SEC:
            diag.update({"ok": True, "source": "cache", "rows": len(rows)})
            return rows, diag
    rows: list[dict[str, Any]] = []
    for row in _load_jsonl(path) or []:
        if str(row.get(hash_key) or "") == rulebook_hash:
            rows.append(row)
    _trade_cache[cache_key] = (rows, now)
    diag.update({"ok": True, "source": "file", "rows": len(rows)})
    return rows, diag


def _trade_depth(row: dict[str, Any]) -> float:
    mae = _safe_float(row.get("max_loss_during_hold"), 0.0)
    if mae < 0:
        return abs(mae)
    entry = _safe_float(row.get("entry_price"), 0.0)
    stop = _safe_float(row.get("stop_price_at_entry"), 0.0)
    if entry > 0 and stop > 0 and stop < entry:
        return max(0.0, (entry - stop) / entry * 100.0 * 0.25)
    return 0.0


def _target_hit(row: dict[str, Any]) -> bool:
    reason = str(row.get("exit_reason") or "").lower()
    if reason in {"take_profit", "target", "target_hit"}:
        return True
    entry = _safe_float(row.get("entry_price"), 0.0)
    target = _safe_float(row.get("target_price_at_entry"), 0.0)
    exit_price = _safe_float(row.get("exit_price"), 0.0)
    mfe = _safe_float(row.get("max_profit_during_hold"), 0.0)
    if entry > 0 and target > 0:
        target_pct = (target / entry - 1.0) * 100.0
        if exit_price >= target * 0.995:
            return True
        if target_pct > 0 and mfe >= target_pct * 0.90:
            return True
    return False


def _summarize_trade_paths(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [r for r in rows if _safe_float(r.get("entry_price"), 0.0) > 0]
    wins = [r for r in usable if _safe_float(r.get("pnl_pct"), 0.0) > 0]
    losses = [r for r in usable if _safe_float(r.get("pnl_pct"), 0.0) <= 0]
    win_depth = [_trade_depth(r) for r in wins]
    loss_depth = [_trade_depth(r) for r in losses]
    all_depth = [_trade_depth(r) for r in usable]
    target_hits = [r for r in usable if _target_hit(r)]
    pnl = [_safe_float(r.get("pnl_pct"), 0.0) for r in usable]
    return {
        "trade_count": len(usable),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": len(wins) / len(usable) * 100.0 if usable else 0.0,
        "avg_pnl_pct": sum(pnl) / len(pnl) if pnl else 0.0,
        "target_hit_count": len(target_hits),
        "target_hit_rate": len(target_hits) / len(usable) * 100.0 if usable else 0.0,
        "win_mae_p50": _percentile(win_depth, 0.50),
        "win_mae_p75": _percentile(win_depth, 0.75),
        "win_mae_p90": _percentile(win_depth, 0.90),
        "loss_mae_p50": _percentile(loss_depth, 0.50),
        "loss_mae_p75": _percentile(loss_depth, 0.75),
        "all_mae_p75": _percentile(all_depth, 0.75),
        "all_mae_p90": _percentile(all_depth, 0.90),
    }


def _quality_label(score: float) -> str:
    if score >= 75.0:
        return "STRONG_REBOUND"
    if score >= 60.0:
        return "HEALTHY_REBOUND"
    if score >= 45.0:
        return "WEAK_REBOUND"
    return "FAILED_REBOUND"


def _build_rebound_quality(position: dict[str, Any], *, current_price: float) -> dict[str, Any]:
    ticker = str(position.get("ticker") or "").upper().strip()
    if not ticker:
        return {"ok": False, "score": None, "label": "NO_TICKER", "reason": "ticker_missing"}
    cache_key = f"{ticker}:{round(float(current_price or 0.0), 4)}"
    now = time.time()
    cached = _rebound_quality_cache.get(cache_key)
    if cached is not None:
        payload, ts = cached
        if now - ts < _REBOUND_QUALITY_CACHE_TTL_SEC:
            return payload
    try:
        from engine.live.elite_shadow_trader import _load_ohlcv

        df = _load_ohlcv(ticker)
    except Exception as exc:
        return {"ok": False, "score": None, "label": "OHLCV_ERROR", "reason": f"{type(exc).__name__}: {exc}"}
    if df is None or len(df) < 8:
        return {"ok": False, "score": None, "label": "INSUFFICIENT_OHLCV", "reason": "need at least 8 daily rows"}

    tail = df.tail(8)
    opens = [_safe_float(x, 0.0) for x in tail.get("Open", []).tolist()]
    highs = [_safe_float(x, 0.0) for x in tail.get("High", []).tolist()]
    lows = [_safe_float(x, 0.0) for x in tail.get("Low", []).tolist()]
    closes = [_safe_float(x, 0.0) for x in tail.get("Close", []).tolist()]
    volumes = [_safe_float(x, 0.0) for x in tail.get("Volume", []).tolist()]
    if len(closes) < 8 or min(closes[-5:]) <= 0 or min(lows[-5:]) <= 0:
        return {"ok": False, "score": None, "label": "BAD_OHLCV", "reason": "invalid close/low data"}

    current = current_price if current_price > 0 else closes[-1]
    c1, c2, c3 = closes[-1], closes[-2], closes[-3]
    l1, l2, l3 = lows[-1], lows[-2], lows[-3]
    h1, h2 = highs[-1], highs[-2]
    o1 = opens[-1] if opens else c1
    recent_low_5 = min(lows[-5:])
    prior_low_3 = min(lows[-5:-2])
    ma3 = sum(closes[-3:]) / 3.0
    ma5 = sum(closes[-5:]) / 5.0
    bounce_from_low_pct = (current / recent_low_5 - 1.0) * 100.0 if recent_low_5 > 0 else 0.0
    close_position = (c1 - l1) / max(h1 - l1, 0.0001) if h1 > 0 and l1 > 0 else 0.0
    vol_base = [v for v in volumes[-6:-1] if v > 0]
    volume_ratio = volumes[-1] / (sum(vol_base) / len(vol_base)) if volumes and volumes[-1] > 0 and vol_base else None
    up_day = c1 >= o1
    higher_close = bool(c1 > c2 >= c3 or current > c1 >= c2)
    higher_low = bool(l1 > l2 >= l3 or min(lows[-2:]) > prior_low_3 * 1.005)
    reclaim_ma3 = bool(current >= ma3)
    reclaim_ma5 = bool(current >= ma5)
    above_prev_high = bool(current > h2)

    components: dict[str, float] = {}
    reasons: list[str] = []
    if bounce_from_low_pct >= 4.0:
        components["low_recovery"] = 22.0
        reasons.append(f"최근 5일 저점 대비 {bounce_from_low_pct:.2f}% 회복")
    elif bounce_from_low_pct >= 3.0:
        components["low_recovery"] = 18.0
        reasons.append(f"최근 5일 저점 대비 {bounce_from_low_pct:.2f}% 회복")
    elif bounce_from_low_pct >= 2.0:
        components["low_recovery"] = 14.0
        reasons.append(f"최근 5일 저점 대비 {bounce_from_low_pct:.2f}% 회복")
    elif bounce_from_low_pct >= 1.0:
        components["low_recovery"] = 8.0
        reasons.append(f"최근 5일 저점 대비 {bounce_from_low_pct:.2f}% 회복")
    else:
        components["low_recovery"] = 0.0
        reasons.append(f"저점 회복 약함 {bounce_from_low_pct:.2f}%")
    components["higher_close"] = 16.0 if higher_close else 0.0
    if higher_close:
        reasons.append("최근 종가 구조 상승")
    components["higher_low"] = 14.0 if higher_low else 0.0
    if higher_low:
        reasons.append("최근 저점 높이기 확인")
    components["ma_reclaim"] = (8.0 if reclaim_ma3 else 0.0) + (8.0 if reclaim_ma5 else 0.0)
    if reclaim_ma3 or reclaim_ma5:
        reasons.append(f"단기 평균 회복 ma3={reclaim_ma3} ma5={reclaim_ma5}")
    if close_position >= 0.70:
        components["close_position"] = 10.0
        reasons.append(f"당일 범위 상단 마감 위치 {close_position:.2f}")
    elif close_position >= 0.50:
        components["close_position"] = 6.0
        reasons.append(f"당일 범위 중상단 마감 위치 {close_position:.2f}")
    else:
        components["close_position"] = 0.0
    components["prev_high_break"] = 10.0 if above_prev_high else 0.0
    if above_prev_high:
        reasons.append("현재가가 전일 고점 돌파")
    if volume_ratio is not None and up_day and volume_ratio >= 1.20:
        components["volume_confirm"] = 12.0
        reasons.append(f"상승일 거래량 동반 {volume_ratio:.2f}x")
    elif volume_ratio is not None and up_day and volume_ratio >= 0.90:
        components["volume_confirm"] = 8.0
        reasons.append(f"상승일 거래량 보통 {volume_ratio:.2f}x")
    elif up_day:
        components["volume_confirm"] = 4.0
        reasons.append("상승일이나 거래량 확인 약함")
    else:
        components["volume_confirm"] = 0.0

    score = sum(components.values())
    penalties: list[str] = []
    if c1 < c2 < c3:
        score -= 15.0
        penalties.append("최근 3일 종가 하락 배열")
    if current < recent_low_5 * 1.005:
        score = min(score, 35.0)
        penalties.append("현재가가 최근 5일 저점 근처")
    score = round(_clamp(score, 0.0, 100.0), 2)
    payload = {
        "ok": True,
        "score": score,
        "label": _quality_label(score),
        "components": components,
        "reasons": reasons[:8],
        "penalties": penalties,
        "metrics": {
            "current_price": current,
            "last_close": c1,
            "recent_low_5": recent_low_5,
            "bounce_from_low_pct": bounce_from_low_pct,
            "close_position": close_position,
            "ma3": ma3,
            "ma5": ma5,
            "reclaim_ma3": reclaim_ma3,
            "reclaim_ma5": reclaim_ma5,
            "higher_close": higher_close,
            "higher_low": higher_low,
            "above_prev_high": above_prev_high,
            "volume_ratio": volume_ratio,
            "up_day": up_day,
        },
        "note": "0~100 반등 품질 점수. 60+면 반등 구조 양호, 75+면 강한 반등, 45 미만은 반등 실패/약함.",
    }
    _rebound_quality_cache[cache_key] = (payload, now)
    return payload


def _current_pullback_features(position: dict[str, Any]) -> dict[str, Any]:
    history = position.get("signal_history") or {}
    current_price = _safe_float(position.get("last_price"), _safe_float(position.get("entry_price"), 0.0))
    entry_price = _safe_float(position.get("entry_price"), 0.0)
    target_price = _safe_float(position.get("target_price"), 0.0)
    stop_price = _safe_float(position.get("stop_price"), 0.0)
    proposed = history.get("proposed_vs_first_buy_pct")
    if proposed is None:
        first_buy_price = _safe_float(history.get("first_buy_price"), 0.0)
        proposed = (current_price / first_buy_price - 1.0) * 100.0 if current_price > 0 and first_buy_price > 0 else 0.0
    first_buy_pullback_depth = max(0.0, -_safe_float(proposed, 0.0))
    entry_drawdown_depth = max(0.0, (entry_price / current_price - 1.0) * 100.0) if current_price > 0 else 0.0
    target_upside_pct = (target_price / current_price - 1.0) * 100.0 if current_price > 0 and target_price > 0 else 0.0
    stop_downside_pct = (current_price - stop_price) / current_price * 100.0 if current_price > 0 and stop_price > 0 and stop_price < current_price else 0.0
    risk_reward = target_upside_pct / stop_downside_pct if stop_downside_pct > 0 else 0.0
    retention = history.get("ratio_retention")
    if retention is None:
        retention = _safe_float(position.get("entry_ratio"), 0.0) / max(_safe_float(position.get("entry_ratio"), 0.0), 0.0001)
    rebound_quality = _build_rebound_quality(position, current_price=current_price)
    return {
        "ticker": position.get("ticker"),
        "gate": position.get("gate"),
        "current_price": current_price,
        "entry_price": entry_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "first_buy_date": history.get("first_buy_date"),
        "first_buy_price": history.get("first_buy_price"),
        "consecutive_buy_days": _safe_int(history.get("consecutive_buy_days"), 0),
        "proposed_vs_first_buy_pct": _safe_float(proposed, 0.0),
        "first_buy_pullback_depth_pct": first_buy_pullback_depth,
        "entry_drawdown_depth_pct": entry_drawdown_depth,
        "score_retention": _safe_float(retention, 0.0),
        "rebound_confirmed": bool(history.get("rebound_confirmed")),
        "rebound_quality_score": rebound_quality.get("score"),
        "rebound_quality_label": rebound_quality.get("label"),
        "rebound_quality": rebound_quality,
        "target_upside_pct": target_upside_pct,
        "stop_downside_pct": stop_downside_pct,
        "risk_reward_to_target": risk_reward,
    }


def _normalize_prob(scores: dict[str, float]) -> dict[str, float]:
    cleaned = {k: max(1.0, float(v)) for k, v in scores.items()}
    total = sum(cleaned.values()) or 1.0
    return {k: round(v / total * 100.0, 2) for k, v in cleaned.items()}


def _confidence_level(edge: float, top_prob: float) -> str:
    if edge >= 15.0 and top_prob >= 45.0:
        return "HIGH"
    if edge >= 8.0:
        return "MEDIUM"
    if edge >= 4.0:
        return "LOW"
    return "CONFLICT"


def _classify(features: dict[str, Any], stats: dict[str, Any]) -> tuple[str, dict[str, float], list[str], dict[str, Any]]:
    depth = _safe_float(features.get("first_buy_pullback_depth_pct"), 0.0)
    retention = _safe_float(features.get("score_retention"), 0.0)
    rebound = bool(features.get("rebound_confirmed"))
    q = _safe_float(features.get("rebound_quality_score"), -1.0) if features.get("rebound_quality_score") is not None else -1.0
    stop_risk = _safe_float(features.get("stop_downside_pct"), 0.0)
    rr = _safe_float(features.get("risk_reward_to_target"), 0.0)
    target_hit_rate = _safe_float(stats.get("target_hit_rate"), 0.0)
    win_rate = _safe_float(stats.get("win_rate"), 0.0)
    win_p75 = stats.get("win_mae_p75")
    win_p90 = stats.get("win_mae_p90")
    loss_p50 = stats.get("loss_mae_p50")
    loss_p75 = stats.get("loss_mae_p75")

    scores = {"REBOUND_TO_TARGET": 30.0, "BASE_HOLD": 25.0, "DROP_MORE": 25.0}
    reasons: list[str] = []

    if win_p75 is not None and depth <= float(win_p75):
        scores["REBOUND_TO_TARGET"] += 16
        scores["BASE_HOLD"] += 6
        reasons.append(f"현재 눌림 {depth:.2f}%가 과거 수익거래 MAE p75 {float(win_p75):.2f}% 안쪽")
    elif win_p90 is not None and depth <= float(win_p90):
        scores["BASE_HOLD"] += 12
        scores["REBOUND_TO_TARGET"] += 4
        reasons.append(f"현재 눌림 {depth:.2f}%가 과거 수익거래 MAE p90 {float(win_p90):.2f}% 안쪽이나 p75 초과")
    elif win_p90 is not None:
        scores["DROP_MORE"] += 12
        if q >= 65.0:
            scores["BASE_HOLD"] += 5
            scores["REBOUND_TO_TARGET"] += 3
            reasons.append(f"과거 MAE p90 {float(win_p90):.2f}% 초과지만 현재 반등 품질 Q{q:.0f}로 충돌")
        else:
            reasons.append(f"현재 눌림 {depth:.2f}%가 과거 수익거래 MAE p90 {float(win_p90):.2f}% 초과")

    if loss_p50 is not None and depth >= float(loss_p50) and (win_p75 is None or depth > float(win_p75)):
        scores["DROP_MORE"] += 7
        reasons.append(f"현재 눌림이 손실거래 MAE 중앙값 {float(loss_p50):.2f}% 이상")
    if loss_p75 is not None and depth >= float(loss_p75):
        scores["DROP_MORE"] += 5
        reasons.append(f"현재 눌림이 손실거래 MAE p75 {float(loss_p75):.2f}% 이상")

    if retention >= 0.90:
        scores["REBOUND_TO_TARGET"] += 12
        reasons.append(f"score/threshold 유지율 {retention*100:.0f}%로 강함")
    elif retention >= 0.75:
        scores["BASE_HOLD"] += 8
        scores["REBOUND_TO_TARGET"] += 3
        reasons.append(f"score/threshold 유지율 {retention*100:.0f}%로 중립 이상")
    else:
        scores["DROP_MORE"] += 18
        reasons.append(f"score/threshold 유지율 {retention*100:.0f}%로 약함")

    if rebound:
        scores["REBOUND_TO_TARGET"] += 4
        reasons.append("최근 반등 확인 TRUE")
    else:
        scores["BASE_HOLD"] += 5
        scores["DROP_MORE"] += 5
        reasons.append("최근 반등 확인 부족")

    if q >= 75.0:
        scores["REBOUND_TO_TARGET"] += 16
        scores["BASE_HOLD"] += 4
        reasons.append(f"반등 품질 Q{q:.0f}: 강함")
    elif q >= 60.0:
        scores["REBOUND_TO_TARGET"] += 10
        scores["BASE_HOLD"] += 5
        reasons.append(f"반등 품질 Q{q:.0f}: 양호")
    elif q >= 45.0:
        scores["BASE_HOLD"] += 10
        scores["REBOUND_TO_TARGET"] += 2
        reasons.append(f"반등 품질 Q{q:.0f}: 약함")
    elif q >= 0.0:
        scores["DROP_MORE"] += 13
        reasons.append(f"반등 품질 Q{q:.0f}: 실패/부족")

    if rr >= 2.0:
        scores["REBOUND_TO_TARGET"] += 7
        reasons.append(f"목표/손절 기대비 {rr:.2f}로 양호")
    elif rr > 0 and rr < 1.1:
        scores["DROP_MORE"] += 7
        reasons.append(f"목표/손절 기대비 {rr:.2f}로 약함")

    if stop_risk and stop_risk <= 3.0:
        scores["DROP_MORE"] += 12
        reasons.append(f"stop까지 여유 {stop_risk:.2f}%로 매우 좁음")
    elif stop_risk and stop_risk <= 5.0:
        scores["DROP_MORE"] += 6
        reasons.append(f"stop까지 여유 {stop_risk:.2f}%로 좁음")

    if target_hit_rate >= 45.0:
        scores["REBOUND_TO_TARGET"] += 8
        reasons.append(f"과거 target 근접/도달률 {target_hit_rate:.1f}%")
    elif target_hit_rate <= 20.0 and stats.get("trade_count", 0) >= 8:
        scores["BASE_HOLD"] += 4
        scores["DROP_MORE"] += 4
        reasons.append(f"과거 target 근접/도달률 {target_hit_rate:.1f}%로 낮음")

    if win_rate >= 70.0:
        scores["REBOUND_TO_TARGET"] += 5
        scores["BASE_HOLD"] += 3
        reasons.append(f"과거 룰북 승률 {win_rate:.1f}%")
    elif win_rate < 50.0 and stats.get("trade_count", 0) >= 8:
        scores["DROP_MORE"] += 5
        reasons.append(f"과거 룰북 승률 {win_rate:.1f}%로 낮음")

    probs = _normalize_prob(scores)
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_prob = ranked[0]
    second_label, second_prob = ranked[1]
    edge = round(top_prob - second_prob, 2)
    confidence = _confidence_level(edge, top_prob)
    label = top_label

    conflict_reasons: list[str] = []
    if edge < 4.0:
        conflict_reasons.append(f"상위 예후 경합: {top_label} {top_prob:.1f}% vs {second_label} {second_prob:.1f}%")
    if top_label == "DROP_MORE" and q >= 75.0 and edge < 10.0:
        conflict_reasons.append(f"과거는 하락 위험이나 현재 반등 품질 Q{q:.0f}가 강해 경합 처리")
    if top_label == "REBOUND_TO_TARGET" and 0.0 <= q < 20.0 and edge < 12.0:
        conflict_reasons.append(f"과거/신호는 반등 우위이나 현재 반등 품질 Q{q:.0f}가 실패권이라 경합 처리")
    if conflict_reasons:
        label = "MIXED_CONFLICT"
        confidence = "CONFLICT"
        reasons.extend(conflict_reasons)

    diag = {
        "top_label": top_label,
        "top_prob": top_prob,
        "second_label": second_label,
        "second_prob": second_prob,
        "edge_pct": edge,
        "confidence_level": confidence,
        "raw_scores": {k: round(v, 3) for k, v in scores.items()},
        "history_weight_note": "과거 MAE/target 기록은 참고값이며, 현재 반등 품질과 충돌하면 MIXED_CONFLICT로 표시한다.",
    }
    return label, probs, reasons[:10], diag


def build_pullback_forecast_for_position(position: dict[str, Any]) -> dict[str, Any]:
    gate = str(position.get("gate") or "")
    features = _current_pullback_features(position)
    if gate not in PULLBACK_GATES:
        return {
            "ok": False,
            "scope": "not_pullback_gate",
            "label": "NOT_PULLBACK_SCOPE",
            "display": "—",
            "features": features,
            "reason": f"gate={gate} is not a pullback gate",
        }
    rows, diag = _load_rulebook_trades(position)
    stats = _summarize_trade_paths(rows)
    if stats.get("trade_count", 0) < 6:
        return {
            "ok": False,
            "scope": "pullback_gate",
            "label": "INSUFFICIENT_HISTORY",
            "display": "기록부족",
            "features": features,
            "stats": stats,
            "trade_source": diag,
            "reason": f"trade_count={stats.get('trade_count', 0)} < 6",
        }
    label, probs, reasons, decision = _classify(features, stats)
    display_map = {
        "REBOUND_TO_TARGET": "반등목표",
        "BASE_HOLD": "유지/횡보",
        "DROP_MORE": "추가하락",
        "MIXED_CONFLICT": "경합",
    }
    confidence_pct = decision.get("top_prob") if label == "MIXED_CONFLICT" else probs.get(label, 0.0)
    return {
        "ok": True,
        "scope": "pullback_gate",
        "label": label,
        "display": display_map.get(label, label),
        "probabilities": probs,
        "confidence_pct": confidence_pct,
        "decision": decision,
        "features": features,
        "rebound_quality": features.get("rebound_quality"),
        "stats": stats,
        "trade_source": diag,
        "reasons": reasons,
        "note": "과거 동일 룰북 기록 + 현재 신호 유지율 + 반등 품질을 섞은 휴리스틱. 경합은 예후 간 차이가 작거나 과거/현재 증거가 충돌한다는 뜻.",
    }


def attach_pullback_forecasts_to_strategy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    strategies = payload.get("strategies") or {}
    global_counts: Counter = Counter()
    global_quality_counts: Counter = Counter()
    global_quality_scores: list[float] = []
    global_ok = 0
    global_scoped = 0
    for sim in strategies.values():
        counts: Counter = Counter()
        quality_counts: Counter = Counter()
        quality_scores: list[float] = []
        scoped = 0
        ok_count = 0
        for pos in sim.get("open_positions") or []:
            if not isinstance(pos, dict):
                continue
            forecast = build_pullback_forecast_for_position(pos)
            pos["pullback_forecast"] = forecast
            label = str(forecast.get("label") or "UNKNOWN")
            rq = forecast.get("rebound_quality") or (forecast.get("features") or {}).get("rebound_quality") or {}
            q_label = str(rq.get("label") or "UNKNOWN")
            q_score = rq.get("score")
            if forecast.get("scope") == "pullback_gate":
                scoped += 1
                global_scoped += 1
                counts[label] += 1
                global_counts[label] += 1
                if q_label != "UNKNOWN":
                    quality_counts[q_label] += 1
                    global_quality_counts[q_label] += 1
                if q_score is not None:
                    quality_scores.append(_safe_float(q_score, 0.0))
                    global_quality_scores.append(_safe_float(q_score, 0.0))
                if forecast.get("ok"):
                    ok_count += 1
                    global_ok += 1
        sim["pullback_forecast_counts"] = dict(counts)
        sim["pullback_forecast_summary"] = {
            "scoped_positions": scoped,
            "ok_count": ok_count,
            "counts": dict(counts),
            "rebound_quality_counts": dict(quality_counts),
            "avg_rebound_quality_score": round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else None,
            "labels": {
                "REBOUND_TO_TARGET": "첫 신호 목표가 쪽 재상승 가능성이 가장 높음",
                "BASE_HOLD": "현재 구간 유지/횡보 가능성이 가장 높음",
                "DROP_MORE": "눌림 지속 또는 추가 하락 위험이 가장 높음",
                "MIXED_CONFLICT": "과거 경로와 현재 반등 품질이 충돌하거나 예후 차이가 작음",
                "INSUFFICIENT_HISTORY": "동일 룰북 과거 거래 수 부족",
                "STRONG_REBOUND": "반등 품질 강함",
                "HEALTHY_REBOUND": "반등 품질 양호",
                "WEAK_REBOUND": "반등 품질 약함",
                "FAILED_REBOUND": "반등 실패/부족",
            },
        }
    payload["pullback_forecast"] = {
        "enabled": True,
        "scoped_positions": global_scoped,
        "ok_count": global_ok,
        "counts": dict(global_counts),
        "rebound_quality_counts": dict(global_quality_counts),
        "avg_rebound_quality_score": round(sum(global_quality_scores) / len(global_quality_scores), 2) if global_quality_scores else None,
        "environment": check_pullback_forecast_environment(payload),
    }
    return payload


def check_pullback_forecast_environment(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "root_exists": ROOT.exists(),
        "stage3_tickers_root_exists": (ROOT / "tickers").exists(),
        "has_strategy_payload": isinstance(payload, dict),
        "sampled_pullback_positions": 0,
        "sampled_with_trade_file": 0,
        "sampled_with_enough_trades": 0,
        "usable": False,
        "notes": [],
    }
    positions: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for sim in (payload.get("strategies") or {}).values():
            for pos in sim.get("open_positions") or []:
                if isinstance(pos, dict) and str(pos.get("gate") or "") in PULLBACK_GATES:
                    positions.append(pos)
    checks["sampled_pullback_positions"] = len(positions)
    for pos in positions[:12]:
        rows, diag = _load_rulebook_trades(pos)
        if diag.get("ok"):
            checks["sampled_with_trade_file"] += 1
        if len(rows) >= 6:
            checks["sampled_with_enough_trades"] += 1
    if not checks["root_exists"]:
        checks["notes"].append("research root missing")
    if not checks["stage3_tickers_root_exists"]:
        checks["notes"].append("stage3 tickers root missing")
    if not positions:
        checks["notes"].append("current strategy payload has no pullback positions")
    if positions and checks["sampled_with_enough_trades"] == 0:
        checks["notes"].append("pullback positions found but matching trade history is insufficient")
    checks["usable"] = bool(checks["root_exists"] and checks["stage3_tickers_root_exists"] and (not positions or checks["sampled_with_enough_trades"] > 0))
    if checks["usable"]:
        checks["notes"].append("historical trade MAE/MFE/target/stop fields are available for pullback forecasting")
    return checks
