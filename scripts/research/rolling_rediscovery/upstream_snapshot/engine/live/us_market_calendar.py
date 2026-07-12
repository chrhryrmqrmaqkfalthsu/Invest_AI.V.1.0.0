"""Calendar-backed NYSE/Nasdaq regular-session calendar.

Security and reliability rules
------------------------------
- Alpaca credentials are read only inside the fetch function and are never
  returned, logged, or written to cache.
- The cache contains a JSON list whose rows have exactly date/open/close.
- A fresh, range-covering cache is always preferred over the API.
- If Alpaca is unavailable, a stale covering cache is preferred; otherwise a
  deterministic static fallback keeps 2026-2027 live scheduling safe.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

log = logging.getLogger("us_market_calendar")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data/_system/calendars/us_xnys_2020_2027.json"
DEFAULT_START_DATE = date(2020, 1, 1)
DEFAULT_END_DATE = date(2027, 12, 31)
DEFAULT_CACHE_MAX_AGE_DAYS = 30
NY = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Exact static live fallback for the currently planned operating horizon.
# These dates are intentionally explicit so API/cache loss cannot turn a known
# full holiday into a trading session.
STATIC_CLOSED_DATES: set[date] = {
    # 2026
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}

# Verified against Alpaca get_calendar() for the complete 2026-2027 range.
STATIC_EARLY_CLOSE_DATES: set[date] = {
    date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26),
}

ApiFetcher = Callable[[date, date], Iterable[Any]]


@dataclass(frozen=True)
class MarketSession:
    date: date
    open: datetime
    close: datetime

    def to_cache_row(self) -> dict[str, str]:
        return {
            "date": self.date.isoformat(),
            "open": self.open.astimezone(NY).isoformat(),
            "close": self.close.astimezone(NY).isoformat(),
        }


def _as_local_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        local = value.replace(tzinfo=NY) if value.tzinfo is None else value.astimezone(NY)
        return local.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        local = parsed.replace(tzinfo=NY) if parsed.tzinfo is None else parsed.astimezone(NY)
        return local.date()
    except Exception:
        return date.fromisoformat(raw[:10])


def _as_local_datetime(value: Any, session_date: date, fallback_time: time) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, time):
        dt = datetime.combine(session_date, value)
    elif value is None:
        dt = datetime.combine(session_date, fallback_time)
    else:
        raw = str(value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            parsed_time = time.fromisoformat(raw)
            dt = datetime.combine(session_date, parsed_time)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=NY)
    return dt.astimezone(NY)


def _normalize_session(row: Any) -> MarketSession:
    if isinstance(row, MarketSession):
        return row
    if isinstance(row, dict):
        raw_date = row.get("date")
        raw_open = row.get("open")
        raw_close = row.get("close")
    else:
        raw_date = getattr(row, "date", None)
        raw_open = getattr(row, "open", None)
        raw_close = getattr(row, "close", None)
    session_date = _as_local_date(raw_date)
    return MarketSession(
        date=session_date,
        open=_as_local_datetime(raw_open, session_date, REGULAR_OPEN),
        close=_as_local_datetime(raw_close, session_date, REGULAR_CLOSE),
    )


def _build_static_sessions(start: date, end: date) -> list[MarketSession]:
    sessions: list[MarketSession] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in STATIC_CLOSED_DATES:
            close_time = EARLY_CLOSE if cursor in STATIC_EARLY_CLOSE_DATES else REGULAR_CLOSE
            sessions.append(
                MarketSession(
                    date=cursor,
                    open=datetime.combine(cursor, REGULAR_OPEN, tzinfo=NY),
                    close=datetime.combine(cursor, close_time, tzinfo=NY),
                )
            )
        cursor += timedelta(days=1)
    return sessions


def _fetch_from_alpaca(start: date, end: date, env_path: Path = DEFAULT_ENV_PATH) -> list[MarketSession]:
    """Fetch sessions without ever exposing credentials in logs or return data."""
    env = dotenv_values(str(env_path)) if env_path.exists() else {}
    api_key = (os.environ.get("ALPACA_API_KEY") or env.get("ALPACA_API_KEY") or "").strip()
    secret_key = (os.environ.get("ALPACA_SECRET_KEY") or env.get("ALPACA_SECRET_KEY") or "").strip()
    base_url = (os.environ.get("ALPACA_BASE_URL") or env.get("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").strip()
    if not api_key or not secret_key:
        raise RuntimeError("Alpaca calendar credentials unavailable")

    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest

    client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True, url_override=base_url)
    rows = client.get_calendar(GetCalendarRequest(start=start, end=end))
    return sorted((_normalize_session(row) for row in rows), key=lambda item: item.date)


class UsMarketCalendar:
    """Cache-first regular-session calendar for US equities."""

    def __init__(
        self,
        *,
        start: date = DEFAULT_START_DATE,
        end: date = DEFAULT_END_DATE,
        cache_path: Path | str = DEFAULT_CACHE_PATH,
        env_path: Path | str = DEFAULT_ENV_PATH,
        cache_max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
        api_fetcher: Optional[ApiFetcher] = None,
        allow_api: bool = True,
        force_refresh: bool = False,
    ):
        if end < start:
            raise ValueError("calendar end must be >= start")
        self.start = start
        self.end = end
        self.cache_path = Path(cache_path)
        self.env_path = Path(env_path)
        self.cache_max_age_days = max(0, int(cache_max_age_days))
        self.api_fetcher = api_fetcher
        self.allow_api = bool(allow_api)
        self.source = "uninitialized"
        self._sessions: dict[date, MarketSession] = {}
        self._load(force_refresh=force_refresh)

    @property
    def sessions(self) -> list[MarketSession]:
        return [self._sessions[key] for key in sorted(self._sessions)]

    @property
    def session_count_total(self) -> int:
        return len(self._sessions)

    @property
    def coverage_start(self) -> Optional[date]:
        return min(self._sessions) if self._sessions else None

    @property
    def coverage_end(self) -> Optional[date]:
        return max(self._sessions) if self._sessions else None

    def _cache_expired(self) -> bool:
        if not self.cache_path.exists():
            return True
        try:
            age = datetime.now().timestamp() - self.cache_path.stat().st_mtime
            return age > self.cache_max_age_days * 86400
        except Exception:
            return True

    @staticmethod
    def _covers_range(sessions: Iterable[MarketSession], start: date, end: date) -> bool:
        rows = sorted(sessions, key=lambda item: item.date)
        if not rows:
            return False
        # A full exchange calendar can legitimately start/end on a holiday or
        # weekend, so allow up to seven calendar days at either boundary.
        return rows[0].date <= start + timedelta(days=7) and rows[-1].date >= end - timedelta(days=7)

    def _read_cache(self) -> list[MarketSession]:
        if not self.cache_path.exists():
            return []
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            rows = [_normalize_session(item) for item in raw]
            return sorted(rows, key=lambda item: item.date)
        except Exception as exc:
            log.warning("US market calendar cache read failed (%s)", type(exc).__name__)
            return []

    def _write_cache(self, sessions: Iterable[MarketSession]) -> None:
        rows = [row.to_cache_row() for row in sorted(sessions, key=lambda item: item.date)]
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)

    def _fetch_api(self) -> list[MarketSession]:
        fetcher = self.api_fetcher
        if fetcher is None:
            return _fetch_from_alpaca(self.start, self.end, env_path=self.env_path)
        return sorted((_normalize_session(row) for row in fetcher(self.start, self.end)), key=lambda item: item.date)

    def _set_sessions(self, sessions: Iterable[MarketSession], source: str) -> None:
        self._sessions = {row.date: row for row in sessions if self.start <= row.date <= self.end}
        self.source = source

    def _load(self, force_refresh: bool) -> None:
        cached = self._read_cache()
        cache_covers = self._covers_range(cached, self.start, self.end)
        if cached and cache_covers and not force_refresh and not self._cache_expired():
            self._set_sessions(cached, "cache")
            return

        if self.allow_api:
            try:
                fetched = self._fetch_api()
                if self._covers_range(fetched, self.start, self.end):
                    self._write_cache(fetched)
                    self._set_sessions(fetched, "alpaca")
                    return
                log.warning("Alpaca US calendar response did not cover requested range; using safe fallback")
            except Exception as exc:
                # Only the exception type is logged; credentials and raw response are never logged.
                log.warning("Alpaca US calendar fetch failed (%s); using safe fallback", type(exc).__name__)

        if cached and cache_covers:
            self._set_sessions(cached, "stale_cache")
            return

        fallback = _build_static_sessions(self.start, self.end)
        self._set_sessions(fallback, "static_fallback")
        log.warning("US market calendar static fallback active; exact holiday coverage guaranteed for 2026-2027")

    def get_session(self, value: date | datetime | str) -> Optional[MarketSession]:
        return self._sessions.get(_as_local_date(value))

    def is_business_day(self, value: date | datetime | str) -> bool:
        return self.get_session(value) is not None

    def is_open(self, value: Optional[datetime] = None) -> bool:
        current = value or datetime.now(NY)
        current = current.replace(tzinfo=NY) if current.tzinfo is None else current.astimezone(NY)
        session = self._sessions.get(current.date())
        return bool(session and session.open <= current <= session.close)

    def next_open(self, value: Optional[datetime] = None) -> Optional[datetime]:
        current = value or datetime.now(NY)
        current = current.replace(tzinfo=NY) if current.tzinfo is None else current.astimezone(NY)
        today = self._sessions.get(current.date())
        if today is not None and current < today.open:
            return today.open
        for session_date in sorted(self._sessions):
            if session_date > current.date():
                return self._sessions[session_date].open
        return None

    def session_close(self, value: date | datetime | str) -> Optional[datetime]:
        session = self.get_session(value)
        return session.close if session else None

    def session_open(self, value: date | datetime | str) -> Optional[datetime]:
        session = self.get_session(value)
        return session.open if session else None

    def session_dates(self, start: date | datetime | str, end: date | datetime | str) -> list[date]:
        start_date = _as_local_date(start)
        end_date = _as_local_date(end)
        if end_date < start_date:
            return []
        return [key for key in sorted(self._sessions) if start_date < key <= end_date]

    def session_count(self, start: date | datetime | str, end: date | datetime | str) -> int:
        """Count sessions strictly after start and through end.

        This matches backtest holding_days: entering on a session and evaluating
        on the next session yields holding_trading_days == 1.
        """
        return len(self.session_dates(start, end))
