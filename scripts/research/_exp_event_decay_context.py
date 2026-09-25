"""
실험 복사본: 이벤트 TTL + 선형 decay 보존 로직.

주의:
- 라이브 engine/market/context.py 원본을 수정하지 않기 위한 연구용 모듈이다.
- TTL 10/3/5일은 최적값이 아니라, 강한 사건(실제 낙폭 >=10점)이 t+15까지
  안정 회복하지 못한 관측에 기반한 보수적 기본값이다.
- impact_score 연동은 과거 데이터에서 실제 낙폭과 상관이 약해 폐기했다.
"""
from __future__ import annotations

import csv
import copy
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, Tuple

EVENT_IMPACT_MIN = -30.0
EVENT_IMPACT_MAX = 20.0

# 보수적 고정 TTL. 거래일 기준.
EVENT_TTL_DAYS = {
    "금리정책_인상": 10,
    "금리정책_인하": 10,
    "인플레이션": 10,
    "연준발언": 3,
    # 데이터 부족 이벤트는 default TTL 사용.
}
DEFAULT_EVENT_TTL_DAYS = 5


def _parse_dt(value: str | datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    return datetime.fromisoformat(text)


def _load_market_dates(market_history_path: str | Path) -> list[date]:
    path = Path(market_history_path)
    if not path.exists():
        return []
    dates: list[date] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("date")
            if not raw:
                continue
            dates.append(date.fromisoformat(raw[:10]))
    return sorted(set(dates))


def _weekday_trading_days_elapsed(start_dt: datetime, end_dt: datetime) -> int:
    """market_history.csv 범위 밖 검증용 fallback. start 다음날부터 end 당일까지 평일 수."""
    if end_dt.date() <= start_dt.date():
        return 0
    cur = start_dt.date() + timedelta(days=1)
    end = end_dt.date()
    n = 0
    while cur <= end:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def trading_days_elapsed(
    start_ts: str | datetime | date,
    end_ts: str | datetime | date,
    market_history_path: str | Path = "data/_system/market_history.csv",
) -> Tuple[int, str]:
    """start 다음 거래일부터 end 당일까지의 거래일 수.

    원칙은 market_history.csv 날짜 기준이다. 다만 현재 연구 데이터가 2026-06-11까지만
    있어 2026-06-12 이후 재생 검증은 평일 fallback을 사용하고 method로 표시한다.
    """
    start_dt = _parse_dt(start_ts)
    end_dt = _parse_dt(end_ts)
    if end_dt <= start_dt:
        return 0, "none"

    market_dates = _load_market_dates(market_history_path)
    if market_dates and market_dates[0] <= start_dt.date() <= market_dates[-1] and end_dt.date() <= market_dates[-1]:
        n = sum(1 for d in market_dates if start_dt.date() < d <= end_dt.date())
        return n, "market_history"

    return _weekday_trading_days_elapsed(start_dt, end_dt), "weekday_fallback"


def ttl_for_event(event_type: str) -> int:
    return int(EVENT_TTL_DAYS.get(event_type, DEFAULT_EVENT_TTL_DAYS))


def _raw_event_impact(event: Dict[str, Any]) -> float:
    meta = event.get("decay_meta") or {}
    if "original_total_impact_score" in meta:
        return float(meta["original_total_impact_score"])
    return float(event.get("total_impact_score", 0.0) or 0.0)


def _with_decay_meta(
    event_type: str,
    event: Dict[str, Any],
    detected_at: str,
    elapsed_days: int,
    weight: float,
    original_impact: float | None = None,
) -> Dict[str, Any]:
    out = copy.deepcopy(event)
    ttl = ttl_for_event(event_type)
    base_impact = _raw_event_impact(out) if original_impact is None else float(original_impact)
    out["total_impact_score"] = round(base_impact * weight, 2)
    out["decay_meta"] = {
        "ttl_days": ttl,
        "detected_at": detected_at,
        "elapsed_trading_days": elapsed_days,
        "decay_weight": round(weight, 4),
        "original_total_impact_score": round(base_impact, 2),
        "note": "research copy: fixed TTL + linear decay; not live code",
    }
    return out


def _decay_previous_event(
    event_type: str,
    event: Dict[str, Any],
    now_ts: str,
    market_history_path: str | Path,
) -> tuple[Dict[str, Any] | None, str]:
    ttl = ttl_for_event(event_type)
    meta = event.get("decay_meta") or {}
    detected_at = meta.get("detected_at") or meta.get("last_detected_at") or now_ts
    original_impact = _raw_event_impact(event)
    elapsed, method = trading_days_elapsed(detected_at, now_ts, market_history_path)
    if elapsed >= ttl:
        return None, method
    weight = max(0.0, (ttl - elapsed) / ttl)
    return _with_decay_meta(event_type, event, detected_at, elapsed, weight, original_impact), method


def merge_active_events_with_decay(
    previous_active_events: Dict[str, Dict[str, Any]] | None,
    previous_timestamp: str | datetime | None,
    new_active_events: Dict[str, Dict[str, Any]] | None,
    now_timestamp: str | datetime,
    market_history_path: str | Path = "data/_system/market_history.csv",
) -> tuple[float, Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """이전 이벤트를 TTL/decay로 이월한 뒤 신규 이벤트와 병합한다.

    병합 규칙:
    - 이전 이벤트: TTL 내면 선형 decay 후 유지, TTL 초과면 제거.
    - 신규 이벤트: 같은 유형이 새로 감지되면 detected_at을 now로 리셋한다.
    - 같은 유형 충돌 시 impact는 절댓값이 더 강한 쪽의 original impact를 유지한다.
    - active_events key와 기존 필드는 보존하고 decay_meta만 추가한다.
    """
    now_text = _parse_dt(now_timestamp).isoformat()
    prev = previous_active_events or {}
    new = new_active_events or {}
    merged: Dict[str, Dict[str, Any]] = {}
    methods: Dict[str, str] = {}
    removed: Dict[str, str] = {}

    for event_type, event in prev.items():
        carried, method = _decay_previous_event(event_type, event, now_text, market_history_path)
        methods[event_type] = method
        if carried is None:
            removed[event_type] = "ttl_expired"
            continue
        merged[event_type] = carried

    for event_type, event in new.items():
        ttl = ttl_for_event(event_type)
        new_impact = float(event.get("total_impact_score", 0.0) or 0.0)
        chosen = copy.deepcopy(event)
        chosen_impact = new_impact

        if event_type in merged:
            prev_impact = _raw_event_impact(merged[event_type])
            if abs(prev_impact) > abs(new_impact):
                chosen = copy.deepcopy(merged[event_type])
                chosen_impact = prev_impact
                # 신규 감지이므로 타이머는 리셋하되, 더 강한 기존 impact를 유지한다.
                chosen["articles"] = list(chosen.get("articles", [])) + list(event.get("articles", []))
                chosen["match_count"] = max(int(chosen.get("match_count", 0) or 0), int(event.get("match_count", 0) or 0))
            else:
                prev_articles = list(merged[event_type].get("articles", []))
                chosen["articles"] = list(event.get("articles", [])) + prev_articles

        merged[event_type] = _with_decay_meta(
            event_type=event_type,
            event=chosen,
            detected_at=now_text,
            elapsed_days=0,
            weight=1.0,
            original_impact=chosen_impact,
        )
        merged[event_type]["decay_meta"]["ttl_days"] = ttl
        methods[event_type] = "new_event_reset"

    total = round(sum(float(ev.get("total_impact_score", 0.0) or 0.0) for ev in merged.values()), 1)
    total = round(max(min(total, EVENT_IMPACT_MAX), EVENT_IMPACT_MIN), 1)
    debug = {
        "methods": methods,
        "removed": removed,
        "event_count": len(merged),
        "impact_min": EVENT_IMPACT_MIN,
        "impact_max": EVENT_IMPACT_MAX,
    }
    return total, merged, debug


def event_flags_from_active_events(active_events: Dict[str, Any]) -> Dict[str, int]:
    """learned_rulebook.py:282-296 reader 호환성 실험용 key 존재 플래그."""
    active = active_events or {}
    return {
        "has_war": int("전쟁" in active),
        "has_rate_hike": int("금리정책_인상" in active),
        "has_rate_cut": int("금리정책_인하" in active),
        "has_regulation_risk": int("규제강화" in active),
        "has_trade_conflict": int("무역분쟁" in active),
        "has_supply_chain": int("공급망충격" in active),
        "has_geopolitical": int("지정학_긴장" in active),
        "has_inflation": int("인플레이션" in active),
        "has_fed_statement": int("연준발언" in active),
    }
