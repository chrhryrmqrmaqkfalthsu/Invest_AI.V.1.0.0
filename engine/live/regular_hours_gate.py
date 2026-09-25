"""US regular-session decision gate for virtual Elite Shadow systems.

목적:
- Elite Shadow/Exit Omen/Peak Exit/Exit Policy Lab의 진입·청산 decision을 미국 정규장에만 허용한다.
- 정규장 밖에서는 가격 모니터링/대시보드 조회는 가능하지만, 신규 진입과 청산은 막는다.

주의:
- 간단한 주중 09:30~16:00 America/New_York 기준이다.
- 거래소 휴장일/조기폐장은 별도 캘린더가 없으면 100% 반영하지 못한다.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REGULAR_START = time(9, 30)
REGULAR_END = time(16, 0)


def regular_hours_snapshot(now: datetime | None = None) -> dict[str, Any]:
    """현재 시각 기준 정규장 decision 허용 여부를 반환한다."""
    now_utc = now.astimezone(timezone.utc) if isinstance(now, datetime) else datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    is_weekday = now_et.weekday() < 5
    local_t = now_et.time().replace(tzinfo=None)
    in_regular = bool(is_weekday and REGULAR_START <= local_t < REGULAR_END)
    if not is_weekday:
        reason = "not_us_weekday"
    elif local_t < REGULAR_START:
        reason = "before_regular_open"
    elif local_t >= REGULAR_END:
        reason = "after_regular_close"
    else:
        reason = "regular_session"
    return {
        "is_regular_hours": in_regular,
        "allow_decision": in_regular,
        "reason": reason,
        "timezone": "America/New_York",
        "now_utc": now_utc.isoformat(),
        "now_et": now_et.isoformat(),
        "regular_start": "09:30",
        "regular_end": "16:00",
    }
