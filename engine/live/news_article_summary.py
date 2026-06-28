"""Helpers for showing the article basis behind holding-news risk scores.

The live holding-news cache stores the numeric risk score used by sell_omen. This
module reads the already-downloaded AlphaVantage ticker cache and extracts a
small, dashboard-safe article summary without making network calls.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TICKER_NEWS_CACHE_DIR = PROJECT_ROOT / "data" / "_system" / "ticker_news_cache"


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out != out:
            return None
        return out
    except Exception:
        return None


def _av_time_to_iso(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return raw


def _parse_iso(value: Any) -> datetime | None:
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


def _age_hours(value: Any, *, now: datetime | None = None) -> float | None:
    dt = _parse_iso(value)
    if dt is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, round((current - dt).total_seconds() / 3600.0, 3))


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _first_sentence(text: Any, limit: int = 220) -> str:
    value = " ".join(str(text or "").split())
    if not value:
        return ""
    # AlphaVantage summary is already concise. Keep the first sentence when it is informative,
    # otherwise fall back to a clipped summary.
    for marker in [". ", "! ", "? "]:
        idx = value.find(marker)
        if 40 <= idx <= limit:
            return _clip(value[: idx + 1], limit)
    return _clip(value, limit)


def _ticker_sentiment(article: dict[str, Any], ticker: str) -> dict[str, Any] | None:
    t = str(ticker or "").upper().strip()
    for item in article.get("ticker_sentiment") or []:
        if str(item.get("ticker") or "").upper().strip() == t:
            return item if isinstance(item, dict) else None
    return None


def _iter_ticker_cache_articles(ticker: str, *, max_files: int = 8) -> Iterable[dict[str, Any]]:
    t = str(ticker or "").upper().strip()
    if not t:
        return []
    ticker_dir = TICKER_NEWS_CACHE_DIR / t
    files = sorted(ticker_dir.glob(f"av_{t}_*.json.gz"), reverse=True)[: max(1, int(max_files))]
    rows: list[dict[str, Any]] = []
    for path in files:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        feed = payload.get("feed") if isinstance(payload, dict) else None
        if not isinstance(feed, list):
            continue
        rows.extend([a for a in feed if isinstance(a, dict)])
    return rows


def articles_for_ticker(ticker: str, *, limit: int = 2, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return latest relevant articles for a ticker from local AlphaVantage caches.

    The score cache may be fresher than the monthly raw ticker cache, because the
    live refresh previously persisted only the numeric score. Until the next cache
    schema stores full article snippets, this function intentionally prioritizes
    recency over old high-risk articles so the dashboard does not surface stale
    headlines next to current holdings.
    """
    t = str(ticker or "").upper().strip()
    if not t:
        return []
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for article in _iter_ticker_cache_articles(t):
        sent = _ticker_sentiment(article, t)
        if not sent:
            continue
        url = str(article.get("url") or "").strip()
        title = _clip(article.get("title"), 180)
        published_raw = str(article.get("time_published") or "")
        key = url or f"{published_raw}|{title.lower()}"
        if not title or key in seen:
            continue
        seen.add(key)
        score = _float_or_none(sent.get("ticker_sentiment_score")) or 0.0
        relevance = _float_or_none(sent.get("relevance_score"))
        rel = max(0.0, min(1.0, relevance if relevance is not None else 1.0))
        risk = max(0.0, -score) * rel
        published_at = _av_time_to_iso(published_raw)
        candidates.append({
            "ticker": t,
            "title": title,
            "summary": _first_sentence(article.get("summary"), 220),
            "url": url,
            "source": str(article.get("source") or article.get("source_domain") or "").strip(),
            "published_at": published_at,
            "published_raw": published_raw,
            "published_age_hours": _age_hours(published_at, now=now),
            "sentiment_score": round(float(score), 6),
            "sentiment_label": str(sent.get("ticker_sentiment_label") or "").strip(),
            "relevance_score": round(float(rel), 6),
            "risk_score": round(float(risk), 6),
            "basis": "latest_relevant_with_negative_risk" if risk > 0 else "latest_relevant",
        })
    # 화면 설명용은 최신성 우선. 같은 시각/날짜라면 위험도 높은 기사부터.
    candidates.sort(key=lambda x: (str(x.get("published_at") or ""), float(x.get("risk_score") or 0.0)), reverse=True)
    return candidates[: max(1, int(limit or 1))]
