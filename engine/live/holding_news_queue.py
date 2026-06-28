from __future__ import annotations

import json, logging, os, re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import requests
from engine.live.news_translation import translate_articles_for_dashboard

log = logging.getLogger("live.holding_news_queue")
ROOT = Path(__file__).resolve().parents[2]
HOLDING_NEWS_CACHE_PATH = ROOT / "data" / "_system" / "holding_news_sentiment_cache.json"
NEWS_CACHE_DIR = ROOT / "data" / "_system" / "news_cache"
SYMBOL_DIR = ROOT / "data" / "symbols"
KST, NY = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
DEFAULT_INDIVIDUAL_CALL_BUDGET = 18
DEFAULT_HOLDING_WARNING_LIMIT = 18
DEFAULT_S4_MAX_AGE_DAYS = 5
MAX_SCORE_ARTICLE_AGE_DAYS = 3
HOLDING_NEWS_SCORE_LOGIC_VERSION = "direct_asset_v3_recent3d"
W_S1, W_S2, W_S3, W_S4 = 0.10, 0.36, 0.36, 0.18
_SUFFIX_RE = re.compile(r"\b(incorporated|inc|corp|corporation|class|co|company|ltd|limited|plc|holdings|holding|sa|spa|ag|nv|adr|common|stock)\b", re.I)

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
    try: return int(str(os.getenv(name, "")).strip() or default)
    except Exception: return default

def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value in (None, ""): return None
        v = float(value); return None if v != v else v
    except Exception: return None

def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime): dt = value
    else:
        raw = str(value or "").strip()
        if not raw: dt = datetime.now(KST)
        else:
            try: dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception: dt = datetime.now(KST)
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)

def _asof_date(asof: Any = None) -> str:
    return _parse_dt(asof).astimezone(NY).date().isoformat()

def _age_days(asof_date: str, score_date: Any) -> Optional[int]:
    raw = str(score_date or "").strip()[:10]
    if not raw: return None
    try: return (datetime.strptime(asof_date, "%Y-%m-%d").date() - datetime.strptime(raw, "%Y-%m-%d").date()).days
    except Exception: return None

def _normalize(values: list[Optional[float]]) -> list[float]:
    clean = [max(0.0, float(v)) for v in values if v is not None]
    if not clean: return [0.0 for _ in values]
    lo, hi = min(clean), max(clean)
    if hi <= lo: return [1.0 if v is not None and float(v) > 0 else 0.0 for v in values]
    return [0.0 if v is None else max(0.0, min(1.0, (float(v) - lo) / (hi - lo))) for v in values]

def rank_holding_news_queue(signals: Iterable[HoldingNewsSignal | dict[str, Any]], *, limit: int = DEFAULT_INDIVIDUAL_CALL_BUDGET, asof: Any = None, s4_max_age_days: int = DEFAULT_S4_MAX_AGE_DAYS, exclude_tickers: Optional[Iterable[str]] = None) -> list[HoldingNewsRank]:
    excluded = {str(t or "").upper().strip() for t in (exclude_tickers or []) if str(t or "").strip()}
    rows: list[HoldingNewsSignal] = []
    for raw in signals:
        r = raw if isinstance(raw, HoldingNewsSignal) else HoldingNewsSignal(str(raw.get("ticker") or "").upper().strip(), _float_or_none(raw.get("s1_age_days")), _float_or_none(raw.get("s2_price_risk")), _float_or_none(raw.get("s3_sentiment_risk")), _float_or_none(raw.get("s4_sell_omen_score")), raw.get("s4_score_date"))
        if r.ticker and r.ticker not in excluded: rows.append(r)
    asof_date = _asof_date(asof)
    s1, s2, s3 = [r.s1_age_days for r in rows], [r.s2_price_risk for r in rows], [r.s3_sentiment_risk for r in rows]
    s4, stale_flags, ages = [], [], []
    for r in rows:
        age = _age_days(asof_date, r.s4_score_date); stale = age is None or age < 0 or age > int(s4_max_age_days)
        ages.append(age); stale_flags.append(stale); s4.append(0.0 if stale else (_float_or_none(r.s4_sell_omen_score) or 0.0))
    n1, n2, n3, n4 = _normalize(s1), _normalize(s2), _normalize(s3), _normalize(s4)
    out = [HoldingNewsRank(r.ticker, round(W_S1*n1[i]+W_S2*n2[i]+W_S3*n3[i]+W_S4*n4[i],10), round(n1[i],10), round(n2[i],10), round(n3[i],10), round(n4[i],10), bool(stale_flags[i]), ages[i]) for i,r in enumerate(rows)]
    out.sort(key=lambda x: (-x.queue_score, -x.norm_s2, -x.norm_s3, -x.norm_s1, x.ticker))
    return out if limit is None or int(limit) <= 0 else out[:min(int(limit), len(out))]

def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists(): return {}
    try:
        with path.open("r", encoding="utf-8") as f: data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("holding news cache load failed: %s", exc); return {}

def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)

def load_holding_news_cache(path: Path = HOLDING_NEWS_CACHE_PATH) -> dict[str, Any]:
    return _load_json(Path(path))

def _score_row_usable(row: Any) -> bool:
    if not isinstance(row, dict) or row.get("score_logic_version") != HOLDING_NEWS_SCORE_LOGIC_VERSION: return False
    try: return int(row.get("max_score_article_age_days") or 0) == MAX_SCORE_ARTICLE_AGE_DAYS
    except Exception: return False

def save_holding_news_cache_entry(ticker: str, *, score: float, fetched_at: Any = None, score_date: Optional[str] = None, source: str = "alphavantage_holding_news", article_count: int = 0, latest_article_time_published: str = "", raw_feed_count: int = 0, top_articles: Optional[list[dict[str, Any]]] = None, path: Path = HOLDING_NEWS_CACHE_PATH) -> dict[str, Any]:
    t = str(ticker or "").upper().strip()
    if not t: raise ValueError("ticker is required")
    dt = _parse_dt(fetched_at or datetime.now(timezone.utc)); date = str(score_date or dt.astimezone(NY).date().isoformat())[:10]
    row = {"ticker":t,"date":date,"score":max(0.0,min(1.0,float(score or 0.0))),"fetched_at":dt.astimezone(timezone.utc).isoformat(timespec="seconds"),"source":source,"score_logic_version":HOLDING_NEWS_SCORE_LOGIC_VERSION,"max_score_article_age_days":MAX_SCORE_ARTICLE_AGE_DAYS,"article_count":int(article_count or 0),"raw_feed_count":int(raw_feed_count or 0),"latest_article_time_published":str(latest_article_time_published or ""),"top_articles":list(top_articles or [])[:2]}
    cache = load_holding_news_cache(path); entries = cache.setdefault("entries", {})
    if not isinstance(entries, dict): entries = {}; cache["entries"] = entries
    entries[t] = row; cache["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds"); _atomic_write_json(Path(path), cache)
    return dict(row)

def lookup_holding_news_cache_score(ticker: str, *, path: Path = HOLDING_NEWS_CACHE_PATH) -> Optional[dict[str, Any]]:
    t = str(ticker or "").upper().strip(); entries = load_holding_news_cache(path).get("entries")
    if not t or not isinstance(entries, dict) or not _score_row_usable(entries.get(t)): return None
    row = entries[t]
    try: score = float(row.get("score"))
    except Exception: return None
    if not 0.0 <= score <= 1.0: return None
    return {"ticker":t,"date":str(row.get("date") or row.get("score_date") or "")[:10],"score":score,"model_train_end":str(row.get("source") or "holding_news_cache"),"score_year":str(row.get("fetched_at") or "")[:4],"fetched_at":row.get("fetched_at", ""),"article_count":int(row.get("article_count") or 0),"source":row.get("source", "holding_news_cache")}

def _av_time_to_iso(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw: return ""
    try: return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except Exception: return raw

def _av_raw_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw: return None
    try: return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception: return None

def _clip(value: Any, limit: int) -> str:
    s = " ".join(str(value or "").split())
    return s if len(s) <= limit else s[:max(0, limit-1)].rstrip()+"…"

def _first_sentence(value: Any, limit: int = 220) -> str:
    s = " ".join(str(value or "").split())
    if not s: return ""
    for m in [". ", "! ", "? "]:
        i = s.find(m)
        if 40 <= i <= limit: return _clip(s[:i+1], limit)
    return _clip(s, limit)

def _norm_text(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()

def _asset_name(ticker: str) -> str:
    try:
        with (SYMBOL_DIR / str(ticker).upper() / "parameters.json").open("r", encoding="utf-8") as f: d = json.load(f)
        return str(((d.get("asset_meta") or {}).get("name") or "")).strip()
    except Exception: return ""

def _asset_aliases(ticker: str) -> set[str]:
    t = str(ticker or "").upper().strip(); out = {t.lower()} if t else set(); name = _asset_name(t)
    clean, full = _norm_text(_SUFFIX_RE.sub(" ", name)), _norm_text(name)
    for x in (clean, full):
        if len(x) >= 4: out.add(x)
    parts = [p for p in clean.split() if len(p) >= 4 and p not in {"the", "and"}]
    if parts: out.add(parts[0])
    if len(parts) >= 2: out.add(" ".join(parts[:2]))
    return out

def _article_directly_mentions_asset(article: dict[str, Any], ticker: str) -> bool:
    t = str(ticker or "").upper().strip(); raw = f"{article.get('title') or ''} {article.get('summary') or ''} {article.get('url') or ''}"
    if re.search(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])", raw.upper()): return True
    hay = f" {_norm_text(raw)} "; return any(f" {_norm_text(a)} " in hay for a in _asset_aliases(t) if len(_norm_text(a)) >= 4)

def _valid_recent_direct_article_rows(ticker: str, feed: Iterable[dict[str, Any]], *, now: Any = None, max_age_days: int = MAX_SCORE_ARTICLE_AGE_DAYS) -> list[dict[str, Any]]:
    t = str(ticker or "").upper().strip(); current = _parse_dt(now or datetime.now(timezone.utc)).astimezone(timezone.utc); max_age_seconds = max(0, int(max_age_days))*86400
    rows: list[dict[str, Any]] = []
    for article in feed or []:
        if not isinstance(article, dict) or not _article_directly_mentions_asset(article, t): continue
        raw_time = str(article.get("time_published") or ""); published = _av_raw_dt(raw_time)
        if published is None or (current-published).total_seconds() > max_age_seconds or published > current: continue
        for item in article.get("ticker_sentiment") or []:
            if str(item.get("ticker") or "").upper().strip() != t: continue
            sentiment = _float_or_none(item.get("ticker_sentiment_score")) or 0.0; relevance = _float_or_none(item.get("relevance_score")); rel = max(0.0, min(1.0, relevance if relevance is not None else 1.0)); risk = max(0.0, -sentiment)*rel
            rows.append({"ticker":t,"title":_clip(article.get("title"),180),"summary":_first_sentence(article.get("summary"),220),"url":str(article.get("url") or "").strip(),"source":str(article.get("source") or article.get("source_domain") or "").strip(),"published_at":_av_time_to_iso(raw_time),"published_raw":raw_time,"published_age_hours":round((current-published).total_seconds()/3600,3),"sentiment_score":round(float(sentiment),6),"sentiment_label":str(item.get("ticker_sentiment_label") or "").strip(),"relevance_score":round(float(rel),6),"risk_score":round(float(risk),6),"basis":"score_recent3d_direct_with_negative_risk" if risk > 0 else "score_recent3d_direct"})
            break
    rows.sort(key=lambda x: (float(x.get("risk_score") or 0.0), str(x.get("published_at") or "")), reverse=True)
    return rows

def _ticker_news_risk_score(ticker: str, feed: Iterable[dict[str, Any]], *, now: Any = None, max_age_days: int = MAX_SCORE_ARTICLE_AGE_DAYS) -> tuple[float, int, str]:
    rows = _valid_recent_direct_article_rows(ticker, feed, now=now, max_age_days=max_age_days)
    if not rows: return 0.0, 0, ""
    return max(0.0, min(1.0, max(float(r.get("risk_score") or 0.0) for r in rows))), len(rows), max(str(r.get("published_at") or "") for r in rows)

def fetch_alpha_vantage_ticker_news_score(ticker: str, *, api_key: str, limit: int = 50, session: Any = None, timeout: int = 30) -> dict[str, Any]:
    t = str(ticker or "").upper().strip()
    if not t: raise ValueError("ticker is required")
    client = session or requests; params = {"function":"NEWS_SENTIMENT","tickers":t,"sort":"LATEST","limit":int(limit or 50),"apikey":api_key}
    response = client.get("https://www.alphavantage.co/query", params=params, timeout=timeout); response.raise_for_status(); data = response.json()
    if any(k in data for k in ("Information", "Note", "Error Message")): raise RuntimeError(str(data.get("Information") or data.get("Note") or data.get("Error Message"))[:500])
    feed = data.get("feed") or []
    if not isinstance(feed, list): feed = []
    now = datetime.now(timezone.utc); rows = _valid_recent_direct_article_rows(t, feed, now=now); score, article_count, latest_article = _ticker_news_risk_score(t, feed, now=now)
    return {"ticker":t,"score":score,"article_count":article_count,"latest_article_time_published":latest_article,"raw_feed_count":len(feed),"score_logic_version":HOLDING_NEWS_SCORE_LOGIC_VERSION,"max_score_article_age_days":MAX_SCORE_ARTICLE_AGE_DAYS,"top_articles":translate_articles_for_dashboard(rows[:2])}

def _cache_age_days_from_fetched_at(row: Optional[dict[str, Any]], now: Any = None) -> float:
    if not row or not row.get("fetched_at"): return 9999.0
    return max(0.0, (_parse_dt(now or datetime.now(timezone.utc)).astimezone(timezone.utc)-_parse_dt(row.get("fetched_at")).astimezone(timezone.utc)).total_seconds()/86400.0)

def _price_risk_from_position(pos: Any, broker: Any = None) -> float:
    ticker = str(getattr(pos, "ticker", "") or "").upper().strip(); entry = _float_or_none(getattr(pos,"entry_price",None)) or 0.0; high = _float_or_none(getattr(pos,"highest_price",None)) or entry; low = _float_or_none(getattr(pos,"lowest_price",None)) or entry; price = None
    if broker is not None and ticker:
        try: price = _float_or_none(broker.get_current_price(ticker))
        except Exception: price = None
    if price is None: price = _float_or_none(getattr(pos,"current_price",None)) or entry
    vals = []
    if entry > 0 and price > 0: vals.append(max(0.0, entry/price-1.0))
    if high > 0 and price > 0: vals.append(max(0.0, high/price-1.0))
    if high > 0 and low > 0: vals.append(max(0.0, (high-low)/high))
    return max(vals or [0.0])

def build_holding_news_signals(positions: Iterable[Any], *, broker: Any = None, asof: Any = None, cache_path: Path = HOLDING_NEWS_CACHE_PATH) -> list[HoldingNewsSignal]:
    from engine.live.news_alerts import lookup_live_sell_omen_score
    now = asof or datetime.now(timezone.utc); entries = load_holding_news_cache(cache_path).get("entries"); entries = entries if isinstance(entries, dict) else {}; rows = []
    for pos in positions or []:
        ticker = str(getattr(pos,"ticker","") or "").upper().strip()
        if not ticker: continue
        cached = entries.get(ticker); s4_row = lookup_live_sell_omen_score(ticker, asof=asof); s3 = float(cached.get("score") or 0.0) if _score_row_usable(cached) else 0.0
        rows.append(HoldingNewsSignal(ticker, _cache_age_days_from_fetched_at(cached, now=now), _price_risk_from_position(pos, broker=broker), s3, float(s4_row.get("score")) if s4_row else None, str(s4_row.get("date")) if s4_row else None))
    return rows

def recent_no_ticker_covered_tickers(*, max_age_minutes: int = 90, now: Any = None) -> set[str]:
    current = _parse_dt(now or datetime.now(timezone.utc)).astimezone(timezone.utc); files = list(NEWS_CACHE_DIR.glob("av_market_*.json")) + list((NEWS_CACHE_DIR/"daily").glob("av_market_*.json"))
    if not files: return set()
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try: mtime = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc)
    except OSError: return set()
    if (current-mtime).total_seconds() > max(0, int(max_age_minutes))*60: return set()
    feed = _load_json(latest).get("feed") or []; covered = set()
    for article in feed:
        for item in article.get("ticker_sentiment") or []:
            ticker = str(item.get("ticker") or "").upper().strip()
            if ticker: covered.add(ticker)
    return covered

def refresh_holding_news_for_positions(positions: Iterable[Any], *, broker: Any = None, notifier: Any = None, asof: Any = None, budget: int = DEFAULT_INDIVIDUAL_CALL_BUDGET, dry_run: bool = False, api_key: Optional[str] = None, exclude_tickers: Optional[Iterable[str]] = None, cache_path: Path = HOLDING_NEWS_CACHE_PATH) -> dict[str, Any]:
    held_positions = list(positions or []); held_tickers = [str(getattr(p,"ticker","") or "").upper().strip() for p in held_positions]; held_tickers = [t for t in held_tickers if t]
    if len(held_tickers) > DEFAULT_HOLDING_WARNING_LIMIT:
        msg = f"[HOLDING-NEWS-BUDGET] holdings={len(held_tickers)} exceeds protected limit {DEFAULT_HOLDING_WARNING_LIMIT}"; log.warning(msg)
        if notifier is not None:
            try: notifier.send(f"⚠️ 보유 뉴스 감시 한도 초과: {len(held_tickers)}개 보유 / 보호 보장 {DEFAULT_HOLDING_WARNING_LIMIT}개")
            except Exception as exc: log.warning("holding news limit warning send failed: %s", exc)
    market_covered = set(exclude_tickers or []); selected = rank_holding_news_queue(build_holding_news_signals(held_positions, broker=broker, asof=asof, cache_path=cache_path), limit=min(int(budget), DEFAULT_INDIVIDUAL_CALL_BUDGET), asof=asof, exclude_tickers=market_covered); selected_tickers = [r.ticker for r in selected]
    result = {"held_count":len(held_tickers),"budget":min(int(budget),DEFAULT_INDIVIDUAL_CALL_BUDGET),"market_covered_count":len(set(held_tickers)&market_covered),"selected_tickers":selected_tickers,"selected_count":len(selected_tickers),"dry_run":bool(dry_run),"cache_updates":[],"errors":{}}
    if dry_run or not selected_tickers: return result
    key = str(api_key or os.getenv("ALPHA_VANTAGE_KEY") or "").strip()
    if not key:
        result["errors"]["__all__"] = "ALPHA_VANTAGE_KEY missing"; log.warning("holding news refresh skipped: ALPHA_VANTAGE_KEY missing"); return result
    for ticker in selected_tickers[:DEFAULT_INDIVIDUAL_CALL_BUDGET]:
        try:
            fetched = fetch_alpha_vantage_ticker_news_score(ticker, api_key=key)
            result["cache_updates"].append(save_holding_news_cache_entry(ticker, score=float(fetched.get("score") or 0.0), fetched_at=asof or datetime.now(timezone.utc), article_count=int(fetched.get("article_count") or 0), latest_article_time_published=str(fetched.get("latest_article_time_published") or ""), raw_feed_count=int(fetched.get("raw_feed_count") or 0), top_articles=fetched.get("top_articles") or [], path=cache_path))
        except Exception as exc:
            result["errors"][ticker] = str(exc)[:300]; log.warning("holding news refresh failed for %s: %s", ticker, exc)
    return result
