from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.live.holding_news_queue import (
    HOLDING_NEWS_CACHE_PATH,
    HOLDING_NEWS_SCORE_LOGIC_VERSION,
    MAX_SCORE_ARTICLE_AGE_DAYS,
    fetch_alpha_vantage_ticker_news_score,
    load_holding_news_cache,
    save_holding_news_cache_entry,
)

DEFAULT_CANDIDATE_NEWS_CACHE_MAX_MINUTES = 180
DEFAULT_CANDIDATE_NEWS_FETCH_BUDGET = 8
CANDIDATE_NEWS_GUARD_FETCH_ENV = "CANDIDATE_NEWS_GUARD_ALLOW_FETCH"


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def candidate_news_guard_fetch_enabled() -> bool:
    """Return whether legacy/paper candidate news guard may spend API quota.

    Default is intentionally OFF. Individual-news quota is now reserved for
    real-trading focus refresh: live candidate slots + real broker holdings.
    Existing next-open/paper candidate selection may still read fresh cache rows,
    but it does not fetch new AlphaVantage ticker news unless this explicit env
    override is enabled.
    """
    return _boolish(os.getenv(CANDIDATE_NEWS_GUARD_FETCH_ENV, "0"))


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        return None if out != out else out
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_minutes(value: Any, *, now: Any = None) -> float | None:
    dt = _parse_dt(value)
    if dt is None:
        return None
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    return max(0.0, (current - dt).total_seconds() / 60.0)


def _score_row_usable(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("score_logic_version") != HOLDING_NEWS_SCORE_LOGIC_VERSION:
        return False
    try:
        return int(row.get("max_score_article_age_days") or 0) == MAX_SCORE_ARTICLE_AGE_DAYS
    except Exception:
        return False


def candidate_news_required(rulebook: dict[str, Any] | None) -> bool:
    """Return whether a learned rulebook asks for sell-omen-like candidate news guard.

    This intentionally uses the learned sell_omen switch. Rulebooks that did not
    learn sell_omen do not spend API budget and are not blocked by this guard.
    """
    rb = rulebook if isinstance(rulebook, dict) else {}
    return _boolish(rb.get("sell_omen_enabled"))


def candidate_news_threshold(rulebook: dict[str, Any] | None) -> float:
    rb = rulebook if isinstance(rulebook, dict) else {}
    raw = _float_or_none(rb.get("sell_omen_threshold"))
    if raw is None:
        return 1.0
    return max(0.0, min(1.0, float(raw)))


def _cache_row(ticker: str, *, cache_path: Path) -> dict[str, Any] | None:
    t = str(ticker or "").upper().strip()
    entries = load_holding_news_cache(cache_path).get("entries")
    if not t or not isinstance(entries, dict):
        return None
    row = entries.get(t)
    return row if _score_row_usable(row) else None


def _result_from_row(
    ticker: str,
    rulebook: dict[str, Any] | None,
    row: dict[str, Any] | None,
    *,
    enabled: bool,
    fresh: bool,
    fetched: bool = False,
    source: str = "cache",
    error: str = "",
) -> dict[str, Any]:
    threshold = candidate_news_threshold(rulebook)
    score = _float_or_none((row or {}).get("score"))
    score = max(0.0, min(1.0, float(score))) if score is not None else None
    blocked = bool(enabled and fresh and score is not None and score >= threshold)
    return {
        "enabled": bool(enabled),
        "required": bool(enabled),
        "ticker": str(ticker or "").upper().strip(),
        "score": score,
        "threshold": threshold,
        "blocked": blocked,
        "fresh": bool(fresh),
        "stale": bool(enabled and not fresh),
        "fetched": bool(fetched),
        "source": source,
        "error": str(error or "")[:300],
        "fetched_at": (row or {}).get("fetched_at", ""),
        "article_count": int((row or {}).get("article_count") or 0),
        "latest_article_time_published": str((row or {}).get("latest_article_time_published") or ""),
        "score_logic_version": (row or {}).get("score_logic_version", HOLDING_NEWS_SCORE_LOGIC_VERSION),
        "max_score_article_age_days": int((row or {}).get("max_score_article_age_days") or MAX_SCORE_ARTICLE_AGE_DAYS),
        "top_articles": list((row or {}).get("top_articles") or [])[:2],
    }


def check_candidate_news_guard(
    ticker: str,
    rulebook: dict[str, Any] | None,
    *,
    api_key: str | None = None,
    allow_fetch: bool = True,
    cache_path: Path = HOLDING_NEWS_CACHE_PATH,
    cache_max_minutes: int | None = None,
    now: Any = None,
) -> dict[str, Any]:
    """Read/cache latest individual news risk and decide candidate exclusion.

    Paper/next-open candidate selection no longer spends individual-news API
    quota by default. It can still use a fresh cache row. New ticker fetches are
    reserved for ``real_focus_news_refresh`` unless the explicit environment
    override ``CANDIDATE_NEWS_GUARD_ALLOW_FETCH=1`` is set.
    """
    t = str(ticker or "").upper().strip()
    enabled = candidate_news_required(rulebook)
    if not enabled:
        return _result_from_row(t, rulebook, None, enabled=False, fresh=False, source="disabled")

    max_minutes = int(cache_max_minutes or os.getenv("CANDIDATE_NEWS_CACHE_MAX_MINUTES") or DEFAULT_CANDIDATE_NEWS_CACHE_MAX_MINUTES)
    row = _cache_row(t, cache_path=Path(cache_path))
    age_min = _age_minutes((row or {}).get("fetched_at"), now=now) if row else None
    if row is not None and age_min is not None and age_min <= max_minutes:
        out = _result_from_row(t, rulebook, row, enabled=True, fresh=True, source="cache")
        out["cache_age_minutes"] = round(age_min, 3)
        return out

    key = str(api_key or os.getenv("ALPHA_VANTAGE_KEY") or "").strip()
    legacy_fetch_allowed = bool(allow_fetch and key and candidate_news_guard_fetch_enabled())
    if legacy_fetch_allowed:
        try:
            fetched = fetch_alpha_vantage_ticker_news_score(t, api_key=key)
            saved = save_holding_news_cache_entry(
                t,
                score=float(fetched.get("score") or 0.0),
                fetched_at=now or datetime.now(timezone.utc),
                article_count=int(fetched.get("article_count") or 0),
                latest_article_time_published=str(fetched.get("latest_article_time_published") or ""),
                raw_feed_count=int(fetched.get("raw_feed_count") or 0),
                top_articles=fetched.get("top_articles") or [],
                source="alphavantage_candidate_news_guard",
                path=Path(cache_path),
            )
            return _result_from_row(t, rulebook, saved, enabled=True, fresh=True, fetched=True, source="alphavantage")
        except Exception as exc:
            stale = _result_from_row(t, rulebook, row, enabled=True, fresh=False, source="fetch_error", error=f"{type(exc).__name__}:{exc}")
            if row is not None and age_min is not None:
                stale["cache_age_minutes"] = round(age_min, 3)
            return stale

    if not key:
        source = "api_key_missing"
    elif not allow_fetch:
        source = "fetch_budget_exhausted"
    else:
        source = "paper_candidate_news_fetch_disabled"
    stale = _result_from_row(t, rulebook, row, enabled=True, fresh=False, source=source)
    if row is not None and age_min is not None:
        stale["cache_age_minutes"] = round(age_min, 3)
    stale["fetch_env"] = CANDIDATE_NEWS_GUARD_FETCH_ENV
    return stale
