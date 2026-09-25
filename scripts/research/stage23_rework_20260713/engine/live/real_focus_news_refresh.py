from __future__ import annotations

import json, os, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from engine.live.holding_news_queue import (
    HOLDING_NEWS_CACHE_PATH, HOLDING_NEWS_SCORE_LOGIC_VERSION, MAX_SCORE_ARTICLE_AGE_DAYS,
    fetch_alpha_vantage_ticker_news_score, load_holding_news_cache, save_holding_news_cache_entry,
)

ROOT = Path(__file__).resolve().parents[2]
LIVE_SLOTS_STATE_PATH = ROOT / "data/_system/live_slots_state.json"
REAL_NEWS_STATE_PATH = ROOT / "data/_system/real_dashboard_news_state.json"
REAL_FOCUS_EVENTS_PATH = ROOT / "data/_system/real_focus_news_events.jsonl"
DEFAULT_BUDGET = 12
DEFAULT_CACHE_MAX_MINUTES = 180

@dataclass(frozen=True)
class RealNewsTarget:
    ticker: str
    role: str
    priority: int
    source_id: str = ""

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
    except Exception: pass
    return default

def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{int(time.time()*1000)}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    os.replace(tmp, path)

def _event(row: dict[str, Any]) -> None:
    REAL_FOCUS_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    r = dict(row); r.setdefault("time", utc_now_iso())
    with REAL_FOCUS_EVENTS_PATH.open("a", encoding="utf-8") as f: f.write(json.dumps(r, ensure_ascii=False, sort_keys=True)+"\n")

def _f(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""): return default
        v = float(value); return default if v != v else v
    except Exception: return default

def _dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw: return None
    try: d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception: return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

def _age_min(row: dict[str, Any] | None) -> float | None:
    d = _dt((row or {}).get("fetched_at"))
    return None if d is None else max(0.0, (datetime.now(timezone.utc)-d).total_seconds()/60.0)

def _usable(row: Any) -> bool:
    if not isinstance(row, dict): return False
    if row.get("score_logic_version") != HOLDING_NEWS_SCORE_LOGIC_VERSION: return False
    try: return int(row.get("max_score_article_age_days") or 0) == MAX_SCORE_ARTICLE_AGE_DAYS
    except Exception: return False

def _risk_label(score: float | None, stale: bool) -> str:
    if score is None: return "missing"
    if stale: return "stale"
    if score >= 0.6: return "high"
    if score >= 0.3: return "medium"
    return "low"

def _alpha_vantage_key(api_key: str | None = None) -> str:
    key = str(api_key or os.getenv("ALPHA_VANTAGE_KEY") or "").strip()
    if key:
        return key
    # Reuse the project's existing config loader so scheduled shells that do not
    # export .env still behave like the other news downloaders. The key value is
    # never logged or returned.
    try:
        from engine.core.config import Config
        Config()
    except Exception:
        pass
    return str(os.getenv("ALPHA_VANTAGE_KEY") or "").strip()

def collect_real_holding_targets() -> tuple[list[RealNewsTarget], dict[str, Any]]:
    try:
        from engine.live.real_dashboard_api import _get_real_broker
        broker = _get_real_broker()
        if broker is None: return [], {"ok": False, "error": "real_broker_unavailable"}
        out = []
        for pos in broker.get_holdings() or []:
            t = str(getattr(pos, "ticker", "") or "").upper().strip()
            if t: out.append(RealNewsTarget(t, "real_holding", 0, "alpaca_live"))
        return out, {"ok": True, "holding_count": len(out)}
    except Exception as exc:
        return [], {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

def collect_real_candidate_targets(max_candidates: int = 8) -> tuple[list[RealNewsTarget], dict[str, Any]]:
    state = _read_json(LIVE_SLOTS_STATE_PATH, {})
    if not isinstance(state, dict) or not state: return [], {"ok": False, "error": "live_slots_state_missing"}
    rows, seen = [], set()
    for row in state.get("slots") or []:
        if isinstance(row, dict) and row.get("candidate_id") and row.get("ticker"):
            rows.append(row); seen.add(str(row.get("candidate_id")))
    for row in state.get("candidate_pool") or []:
        if len(rows) >= max_candidates: break
        if not isinstance(row, dict) or not row.get("candidate_id") or not row.get("ticker"): continue
        cid = str(row.get("candidate_id"))
        if cid in seen: continue
        rows.append(row); seen.add(cid)
    out = [RealNewsTarget(str(r.get("ticker") or "").upper().strip(), "real_candidate_slot", 10+i, str(r.get("candidate_id") or "")) for i, r in enumerate(rows[:max_candidates])]
    return [x for x in out if x.ticker], {"ok": True, "candidate_count": len(out), "state_updated_at": state.get("updated_at", "")}

def _dedupe(targets: Iterable[RealNewsTarget]) -> list[RealNewsTarget]:
    best: dict[str, RealNewsTarget] = {}
    for x in targets:
        t = str(x.ticker or "").upper().strip()
        if not t: continue
        old = best.get(t)
        if old is None or x.priority < old.priority: best[t] = RealNewsTarget(t, x.role, x.priority, x.source_id)
        elif x.role not in old.role: best[t] = RealNewsTarget(t, old.role+","+x.role, old.priority, old.source_id or x.source_id)
    return sorted(best.values(), key=lambda x: (x.priority, x.ticker))

def _entry(ticker: str, row: dict[str, Any] | None, target: RealNewsTarget, cache_max_minutes: int) -> dict[str, Any]:
    age = _age_min(row); fresh = bool(_usable(row) and age is not None and age <= cache_max_minutes)
    score = _f((row or {}).get("score"), None) if _usable(row) else None
    return {"ticker": ticker, "role": target.role, "source_id": target.source_id, "score": score, "risk_label": _risk_label(score, not fresh), "missing": row is None, "stale": not fresh, "fresh": fresh, "fetched_at": (row or {}).get("fetched_at", ""), "cache_age_minutes": round(age,3) if age is not None else None, "article_count": int((row or {}).get("article_count") or 0), "latest_article_time_published": str((row or {}).get("latest_article_time_published") or ""), "source": (row or {}).get("source", "real_focus_news_missing"), "articles": list((row or {}).get("top_articles") or [])[:2], "score_logic_version": (row or {}).get("score_logic_version", HOLDING_NEWS_SCORE_LOGIC_VERSION), "max_score_article_age_days": int((row or {}).get("max_score_article_age_days") or MAX_SCORE_ARTICLE_AGE_DAYS)}

def refresh_real_focus_news(*, budget: int = DEFAULT_BUDGET, dry_run: bool = False, cache_max_minutes: int = DEFAULT_CACHE_MAX_MINUTES, api_key: str | None = None) -> dict[str, Any]:
    holdings, holding_meta = collect_real_holding_targets(); candidates, candidate_meta = collect_real_candidate_targets(8)
    targets = _dedupe([*holdings, *candidates]); selected = targets[:max(0, int(budget))]
    key = _alpha_vantage_key(api_key); cache = load_holding_news_cache(HOLDING_NEWS_CACHE_PATH); entries = cache.get("entries") if isinstance(cache.get("entries"), dict) else {}
    fetched_count, errors = 0, {}
    for target in selected:
        row = entries.get(target.ticker); age = _age_min(row); fresh = bool(_usable(row) and age is not None and age <= cache_max_minutes)
        if fresh or dry_run: continue
        if not key: errors[target.ticker] = "ALPHA_VANTAGE_KEY missing"; continue
        try:
            fetched = fetch_alpha_vantage_ticker_news_score(target.ticker, api_key=key)
            saved = save_holding_news_cache_entry(target.ticker, score=float(fetched.get("score") or 0.0), fetched_at=datetime.now(timezone.utc), article_count=int(fetched.get("article_count") or 0), latest_article_time_published=str(fetched.get("latest_article_time_published") or ""), raw_feed_count=int(fetched.get("raw_feed_count") or 0), top_articles=fetched.get("top_articles") or [], source="alphavantage_real_focus_news", path=HOLDING_NEWS_CACHE_PATH)
            entries[target.ticker] = saved; fetched_count += 1
        except Exception as exc: errors[target.ticker] = f"{type(exc).__name__}: {exc}"[:300]
    real_entries = {t.ticker: _entry(t.ticker, entries.get(t.ticker), t, cache_max_minutes) for t in selected}
    state = {"sentiment": {"entries": real_entries, "meta": {"source": "real_focus_news_refresh", "isolated": True, "state_path": str(REAL_NEWS_STATE_PATH), "cache_path": str(HOLDING_NEWS_CACHE_PATH), "updated_at": utc_now_iso(), "target_count": len(targets), "selected_count": len(selected), "budget": int(budget), "fetched_count": fetched_count, "dry_run": bool(dry_run), "api_key_present": bool(key), "cache_max_minutes": int(cache_max_minutes), "holding_meta": holding_meta, "candidate_meta": candidate_meta, "holding_tickers": [x.ticker for x in holdings], "candidate_tickers": [x.ticker for x in candidates], "selected_tickers": [x.ticker for x in selected], "paper_candidate_news_fetch_disabled_by_default": True}}, "alerts": {"errors": errors}}
    _write_json(REAL_NEWS_STATE_PATH, state)
    result = {"ok": True, "dry_run": bool(dry_run), "target_count": len(targets), "selected_count": len(selected), "selected_tickers": [x.ticker for x in selected], "holding_tickers": [x.ticker for x in holdings], "candidate_tickers": [x.ticker for x in candidates], "fetched_count": fetched_count, "errors": errors, "real_news_state_path": str(REAL_NEWS_STATE_PATH), "events_path": str(REAL_FOCUS_EVENTS_PATH)}
    _event({"event": "REAL_FOCUS_NEWS_REFRESH", **result}); return result
