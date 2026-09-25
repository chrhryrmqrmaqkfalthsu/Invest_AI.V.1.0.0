"""Trading-session helpers for live next-open workflows."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _as_et_datetime(value: datetime | None = None) -> datetime:
    now = value or datetime.now(ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return _as_et_datetime(value).date()
    return value


def is_session_day(clock: Any, session: date | datetime) -> bool:
    """Return whether ``session`` is a regular trading session for ``clock``."""
    day = _as_date(session)
    if hasattr(clock, "is_business_day"):
        return bool(clock.is_business_day(datetime.combine(day, time(12, 0), tzinfo=ET)))
    return day.weekday() < 5


def current_or_next_session(clock: Any, now: datetime | None = None) -> date:
    """Return today's session if it is openable, otherwise the next session date."""
    now_et = _as_et_datetime(now)
    if is_session_day(clock, now_et.date()):
        return now_et.date()
    if hasattr(clock, "next_open"):
        nxt = clock.next_open(now_et)
        if nxt is not None:
            return _as_et_datetime(nxt).date()
    cursor = now_et.date() + timedelta(days=1)
    for _ in range(14):
        if is_session_day(clock, cursor):
            return cursor
        cursor += timedelta(days=1)
    raise RuntimeError("next trading session not found within 14 days")


def previous_session_date(clock: Any, session: date | datetime) -> date:
    """Return the previous regular trading session before ``session``."""
    target = _as_date(session)
    calendar = getattr(clock, "calendar", None)
    if calendar is not None and hasattr(calendar, "session_dates"):
        sessions = calendar.session_dates(target - timedelta(days=21), target - timedelta(days=1))
        if sessions:
            return sessions[-1]
    cursor = target - timedelta(days=1)
    for _ in range(21):
        if is_session_day(clock, cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise RuntimeError(f"previous trading session not found before {target}")


def session_open_dt(clock: Any, session: date | datetime) -> datetime:
    """Return the timezone-aware open datetime for ``session``."""
    day = _as_date(session)
    calendar = getattr(clock, "calendar", None)
    if calendar is not None and hasattr(calendar, "session_open"):
        opened = calendar.session_open(day)
        if opened is not None:
            return _as_et_datetime(opened)
    return datetime.combine(day, time(9, 30), tzinfo=ET)
