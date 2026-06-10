"""Holding-news refresh priority queue and cache helpers.

This module is intentionally side-effect-light: the ranking function is pure, while
cache/API helpers are used by the live runner to keep sell_omen news risk fresh
within the AlphaVantage daily request budget.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("live.holding_news_queue")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOLDING_NEWS_CACHE_PATH = PROJECT_ROOT / "data" / "_system" / "holding_news_sentiment_cache.json"
NEWS_CACHE_DIR = PROJECT_ROOT / "data" / "_system" / "news_cache"
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")

DEFAULT_INDIVIDUAL_CALL_BUDGET = 18
DEFAULT_HOLDING_WARNING_LIMIT = 18
DEFAULT_S4_MAX_AGE_DAYS = 5

W_S1 = 0.10
W_S2 = 0.36
W_S3 = 0.36
W_S4 = 0.18


@dataclass(frozen=True)
class HoldingNewsSignal:
    ticker: str
    s1_age_days: Optional[float] = None
    s2_price_risk: Optional[float] = None
    s3_sentiment_risk: Optional[float] = None
    s4_sell_omen_score: Optional[float] = None
    s4_score_date: Optional[str] = None


@dataclass(frozen=True)
class HoldingNewsRank:
    ticker: str
    queue_score: float
    norm_s1: float
    norm_s2: float
    norm_s3: float
    norm_s4: float
    s4_stale: bool
    s4_age_days: Optional[int]


def _int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except Exception:
        return default


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        v = float(value)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            dt = datetime.now(KST)
        else:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt


def _asof_date(asof: Any = None) -> str:
    return _parse_dt(asof).astimezone(NY).date().isoformat()


def _age_days(asof_date: str, score_date: Any) -> Optional[int]:
    raw = str(score_date or "").strip()[:10]
    if not raw:
        return None
    try:
        a = datetime.strptime(asof_date, "%Y-%m-%d").date()
        s = datetime.strptime(raw, "%Y-%m-%d").date()
        return (a - s).days
    except Exception:
        return None


def _normalize(values: list[Optional[float]]) -> list[float]:
    clean = [max(0.0, float(v)) for v in values if v is not None]
    if not clean:
        return [0.0 for _ in values]
    lo = min(clean)
    hi = max(clean)
    if hi <= lo:
        return [1.0 if v is not None and float(v) > 0 else 0.0 for v in values]
    out: list[float] = []
    for v in values:
        if v is None:
            out.append(0.0)
        else:
            out.append(max(0.0, min(1.0, (float(v) - lo) / (hi - lo))))
    return out


def rank_holding_news_queue(
    signals: Iterable[HoldingNewsSignal | dict[str, Any]],
    *,
    limit: int = DEFAULT_INDIVIDUAL_CALL_BUDGET,
    asof: Any = None,
    s4_max_age_days: int = DEFAULT_S4_MAX_AGE_DAYS,
    exclude_tickers: Optional[Iterable[str]] = None,
) -> list[HoldingNewsRank]:
    """Return top-N tickers ranked by the fixed holding-news queue formula.

    queue_score = 0.10*norm(S1) + 0.36*norm(S2) + 0.36*norm(S3) + 0.18*norm(S4_eff)
    S4_eff is zero when score_date is older than s4_max_age_days.
    """
    excluded = {str(t or "").upper().strip() for t in (exclude_tickers or []) if str(t or "").strip()}
    rows: list[HoldingNewsSignal] = []
    for raw in signals:
        if isinstance(raw, HoldingNewsSignal):
            row = raw
        else:
            row = HoldingNewsSignal(
                ticker=str(raw.get("ticker") or "").upper().strip(),
                s1_age_days=_float_or_none(raw.get("s1_age_days")),
                s2_price_risk=_float_or_none(raw.get("s2_price_risk")),
                s3_sentiment_risk=_float_or_none(raw.get("s3_sentiment_risk")),
                s4_sell_omen_score=_float_or_none(raw.get("s4_sell_omen_score")),
                s4_score_date=raw.get("s4_score_date"),
            )
        ticker = row.ticker.upper().strip()
        if not ticker or ticker in excluded:
            continue
        rows.append(HoldingNewsSignal(
            ticker=ticker,
            s1_age_days=row.s1_age_days,
            s2_price_risk=row.s2_price_risk,
            s3_sentiment_risk=row.s3_sentiment_risk,
            s4_sell_omen_score=row.s4_sell_omen_score,
            s4_score_date=row.s4_score_date,
        ))

    asof_date = _asof_date(asof)
    s1_values = [r.s1_age_days for r in rows]
    s2_values = [r.s2_price_risk for r in rows]
    s3_values = [r.s3_sentiment_risk for r in rows]
    s4_values: list[Optional[float]] = []
    s4_stale: list[bool] = []
    s4_ages: list[Optional[int]] = []
    for r in rows:
        age = _age_days(asof_date, r.s4_score_date)
        stale = age is None or age < 0 or age > int(s4_max_age_days)
        s4_ages.append(age)
        s4_stale.append(stale)
        if stale:
            s4_values.append(0.0)
        else:
            s4_values.append(_float_or_none(r.s4_sell_omen_score) or 0.0)

    n1 = _normalize(s1_values)
    n2 = _normalize(s2_values)
    n3 = _normalize(s3_values)
    n4 = _normalize(s4_values)
    ranked: list[HoldingNewsRank] = []
    for i, r in enumerate(rows):
        score = W_S1 * n1[i] + W_S2 * n2[i] + W_S3 * n3[i] + W_S4 * n4[i]
        ranked.append(HoldingNewsRank(
            ticker=r.ticker,
            queue_score=round(float(score), 10),
            norm_s1=round(float(n1[i]), 10),
            norm_s2=round(float(n2[i]), 10),
            norm_s3=round(float(n3[i]), 10),
            norm_s4=round(float(n4[i]), 10),
            s4_stale=bool(s4_stale[i]),
            s4_age_days=s4_ages[i],
        ))
    ranked.sort(key=lambda x: (-x.queue_score, -x.norm_s2, -x.norm_s3, -x.norm_s1, x.ticker))
    if limit is None or int(limit) <= 0:
        return ranked
    return ranked[: min(int(limit), len(ranked))]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("holding news cache load failed: %s", exc)
        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def load_holding_news_cache(path: Path = HOLDING_NEWS_CACHE_PATH) -> dict[str, Any]:
    return _load_json(Path(path))


def save_holding_news_cache_entry(
    ticker: str,
    *,
    score: float,
    fetched_at: Any = None,
    score_date: Optional[str] = None,
    source: str = "alphavantage_holding_news",
    article_count: int = 0,
    latest_article_time_published: str = "",
    path: Path = HOLDING_NEWS_CACHE_PATH,
) -> dict[str, Any]:
    t = str(ticker or "").upper().strip()
    if not t:
        raise ValueError("ticker is required")
    dt = _parse_dt(fetched_at or datetime.now(timezone.utc))
    date = str(score_date or dt.astimezone(NY).date().isoformat())[:10]
    row = {
        "ticker": t,
        "date": date,
        "score": max(0.0, min(1.0, float(score or 0.0))),
        "fetched_at": dt.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "article_count": int(article_count or 0),
        "latest_article_time_published": str(latest_article_time_published or ""),
    }
    cache = load_holding_news_cache(path)
    entries = cache.setdefault("entries", {})
    if not isinstance(entries, dict):
        entries = {}
        cache["entries"] = entries
    entries[t] = row
    cache["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write_json(Path(path), cache)
    return dict(row)


def lookup_holding_news_cache_score(ticker: str, *, path: Path = HOLDING_NEWS_CACHE_PATH) -> Optional[dict[str, Any]]:
    t = str(ticker or "").upper().strip()
    if not t:
        return None
    cache = load_holding_news_cache(path)
    entries = cache.get("entries") if isinstance(cache, dict) else None
    if not isinstance(entries, dict):
        return None
    row = entries.get(t)
    if not isinstance(row, dict):
        return None
    try:
        score = float(row.get("score"))
    except Exception:
        return None
    if not 0.0 <= score <= 1.0:
        return None
    return {
        "ticker": t,
        "date": str(row.get("date") or row.get("score_date") or "")[:10],
        "score": score,
        "model_train_end": str(row.get("source") or "holding_news_cache"),
        "score_year": str(row.get("fetched_at") or "")[:4],
        "fetched_at": row.get("fetched_at", ""),
        "article_count": int(row.get("article_count") or 0),
        "source": row.get("source", "holding_news_cache"),
    }


def _av_time_to_iso(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return raw


def _ticker_news_risk_score(ticker: str, feed: Iterable[dict[str, Any]]) -> tuple[float, int, str]:
    t = str(ticker or "").upper().strip()
    risks: list[float] = []
    latest_time = ""
    article_count = 0
    for article in feed or []:
        time_published = str(article.get("time_published") or "")
        if time_published and (not latest_time or time_published > latest_time):
            latest_time = time_published
        matched = False
        for item in article.get("ticker_sentiment") or []:
            if str(item.get("ticker") or "").upper().strip() != t:
                continue
            matched = True
            sentiment = _float_or_none(item.get("ticker_sentiment_score")) or 0.0
            relevance = _float_or_none(item.get("relevance_score"))
            rel = max(0.0, min(1.0, relevance if relevance is not None else 1.0))
            risks.append(max(0.0, -sentiment) * rel)
        if matched:
            article_count += 1
    if not risks:
        return 0.0, article_count, _av_time_to_iso(latest_time)
    # Worst relevant negative article drives emergency exit risk.
    return max(0.0, min(1.0, max(risks))), article_count, _av_time_to_iso(latest_time)


def fetch_alpha_vantage_ticker_news_score(
    ticker: str,
    *,
    api_key: str,
    limit: int = 50,
    session: Any = None,
    timeout: int = 30,
) -> dict[str, Any]:
    t = str(ticker or "").upper().strip()
    if not t:
        raise ValueError("ticker is required")
    client = session or requests
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": t,
        "sort": "LATEST",
        "limit": int(limit or 50),
        "apikey": api_key,
    }
    response = client.get("https://www.alphavantage.co/query", params=params, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if any(k in data for k in ("Information", "Note", "Error Message")):
        message = data.get("Information") or data.get("Note") or data.get("Error Message")
        raise RuntimeError(str(message)[:500])
    feed = data.get("feed") or []
    if not isinstance(feed, list):
        feed = []
    score, article_count, latest_article = _ticker_news_risk_score(t, feed)
    return {
        "ticker": t,
        "score": score,
        "article_count": article_count,
        "latest_article_time_published": latest_article,
        "raw_feed_count": len(feed),
    }


def _cache_age_days_from_fetched_at(row: Optional[dict[str, Any]], now: Any = None) -> float:
    if not row:
        return 9999.0
    raw = row.get("fetched_at") or ""
    if not raw:
        return 9999.0
    fetched = _parse_dt(raw).astimezone(timezone.utc)
    current = _parse_dt(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return max(0.0, (current - fetched).total_seconds() / 86400.0)


def _price_risk_from_position(pos: Any, broker: Any = None) -> float:
    ticker = str(getattr(pos, "ticker", "") or "").upper().strip()
    entry = _float_or_none(getattr(pos, "entry_price", None)) or 0.0
    high = _float_or_none(getattr(pos, "highest_price", None)) or entry
    low = _float_or_none(getattr(pos, "lowest_price", None)) or entry
    price = None
    if broker is not None and ticker:
        try:
            price = _float_or_none(broker.get_current_price(ticker))
        except Exception:
            price = None
    if price is None:
        price = _float_or_none(getattr(pos, "current_price", None)) or entry
    components = []
    if entry > 0 and price > 0:
        components.append(max(0.0, entry / price - 1.0))
    if high > 0 and price > 0:
        components.append(max(0.0, high / price - 1.0))
    if high > 0 and low > 0:
        components.append(max(0.0, (high - low) / high))
    return max(components or [0.0])


def build_holding_news_signals(
    positions: Iterable[Any],
    *,
    broker: Any = None,
    asof: Any = None,
    cache_path: Path = HOLDING_NEWS_CACHE_PATH,
) -> list[HoldingNewsSignal]:
    from engine.live.news_alerts import lookup_live_sell_omen_score

    now = asof or datetime.now(timezone.utc)
    cache = load_holding_news_cache(cache_path)
    entries = cache.get("entries") if isinstance(cache, dict) else {}
    if not isinstance(entries, dict):
        entries = {}
    rows: list[HoldingNewsSignal] = []
    for pos in positions or []:
        ticker = str(getattr(pos, "ticker", "") or "").upper().strip()
        if not ticker:
            continue
        cached = entries.get(ticker) if isinstance(entries, dict) else None
        s1_age = _cache_age_days_from_fetched_at(cached, now=now)
        s2 = _price_risk_from_position(pos, broker=broker)
        s3 = 0.0
        if isinstance(cached, dict):
            s3 = float(cached.get("score") or 0.0)
        s4_row = lookup_live_sell_omen_score(ticker, asof=asof)
        rows.append(HoldingNewsSignal(
            ticker=ticker,
            s1_age_days=s1_age,
            s2_price_risk=s2,
            s3_sentiment_risk=s3,
            s4_sell_omen_score=(float(s4_row.get("score")) if s4_row else None),
            s4_score_date=(str(s4_row.get("date")) if s4_row else None),
        ))
    return rows


def recent_no_ticker_covered_tickers(*, max_age_minutes: int = 90, now: Any = None) -> set[str]:
    current = _parse_dt(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    files = list(NEWS_CACHE_DIR.glob("av_market_*.json")) + list((NEWS_CACHE_DIR / "daily").glob("av_market_*.json"))
    if not files:
        return set()
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        mtime = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc)
    except OSError:
        return set()
    if (current - mtime).total_seconds() > max(0, int(max_age_minutes)) * 60:
        return set()
    data = _load_json(latest)
    feed = data.get("feed") if isinstance(data, dict) else []
    covered: set[str] = set()
    for article in feed or []:
        for item in article.get("ticker_sentiment") or []:
            ticker = str(item.get("ticker") or "").upper().strip()
            if ticker:
                covered.add(ticker)
    return covered


def refresh_holding_news_for_positions(
    positions: Iterable[Any],
    *,
    broker: Any = None,
    notifier: Any = None,
    asof: Any = None,
    budget: int = DEFAULT_INDIVIDUAL_CALL_BUDGET,
    dry_run: bool = False,
    api_key: Optional[str] = None,
    exclude_tickers: Optional[Iterable[str]] = None,
    cache_path: Path = HOLDING_NEWS_CACHE_PATH,
) -> dict[str, Any]:
    held_positions = list(positions or [])
    held_tickers = [str(getattr(p, "ticker", "") or "").upper().strip() for p in held_positions]
    held_tickers = [t for t in held_tickers if t]
    if len(held_tickers) > DEFAULT_HOLDING_WARNING_LIMIT:
        msg = f"[HOLDING-NEWS-BUDGET] holdings={len(held_tickers)} exceeds protected limit {DEFAULT_HOLDING_WARNING_LIMIT}"
        log.warning(msg)
        if notifier is not None:
            try:
                notifier.send(f"⚠️ 보유 뉴스 감시 한도 초과: {len(held_tickers)}개 보유 / 보호 보장 {DEFAULT_HOLDING_WARNING_LIMIT}개")
            except Exception as exc:
                log.warning("holding news limit warning send failed: %s", exc)

    market_covered = set(exclude_tickers or [])
    signals = build_holding_news_signals(held_positions, broker=broker, asof=asof, cache_path=cache_path)
    selected = rank_holding_news_queue(signals, limit=min(int(budget), DEFAULT_INDIVIDUAL_CALL_BUDGET), asof=asof, exclude_tickers=market_covered)
    selected_tickers = [r.ticker for r in selected]
    result: dict[str, Any] = {
        "held_count": len(held_tickers),
        "budget": min(int(budget), DEFAULT_INDIVIDUAL_CALL_BUDGET),
        "market_covered_count": len(set(held_tickers) & market_covered),
        "selected_tickers": selected_tickers,
        "selected_count": len(selected_tickers),
        "dry_run": bool(dry_run),
        "cache_updates": [],
        "errors": {},
    }
    if dry_run or not selected_tickers:
        return result
    key = str(api_key or os.getenv("ALPHA_VANTAGE_KEY") or "").strip()
    if not key:
        result["errors"]["__all__"] = "ALPHA_VANTAGE_KEY missing"
        log.warning("holding news refresh skipped: ALPHA_VANTAGE_KEY missing")
        return result
    for ticker in selected_tickers[:DEFAULT_INDIVIDUAL_CALL_BUDGET]:
        try:
            fetched = fetch_alpha_vantage_ticker_news_score(ticker, api_key=key)
            row = save_holding_news_cache_entry(
                ticker,
                score=float(fetched.get("score") or 0.0),
                fetched_at=asof or datetime.now(timezone.utc),
                article_count=int(fetched.get("article_count") or 0),
                latest_article_time_published=str(fetched.get("latest_article_time_published") or ""),
                path=cache_path,
            )
            result["cache_updates"].append(row)
        except Exception as exc:
            result["errors"][ticker] = str(exc)[:300]
            log.warning("holding news refresh failed for %s: %s", ticker, exc)
    return result
