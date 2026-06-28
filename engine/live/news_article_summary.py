from __future__ import annotations

import gzip, json, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from engine.live.news_translation import translate_articles_for_dashboard

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "_system" / "ticker_news_cache"
SYMS = ROOT / "data" / "symbols"
SUFFIX = re.compile(r"\b(inc|corp|corporation|class|company|ltd|limited|plc|holdings|holding|common|stock)\b", re.I)


def _dt(v: Any):
    s = str(v or "").strip()
    if not s: return None
    try:
        if re.fullmatch(r"\d{8}T\d{6}", s):
            return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        x = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (x if x.tzinfo else x.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None


def _iso(v: Any):
    x = _dt(v)
    return x.isoformat(timespec="seconds") if x else str(v or "").strip()


def _age(v: Any, now=None):
    x = _dt(v)
    return None if not x else max(0.0, round(((now or datetime.now(timezone.utc))-x).total_seconds()/3600, 3))


def _clip(v: Any, n: int):
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[:n-1].rstrip()+"…"


def _summary(v: Any):
    s = " ".join(str(v or "").split())
    if not s: return ""
    for m in [". ", "! ", "? "]:
        i = s.find(m)
        if 40 <= i <= 220: return _clip(s[:i+1], 220)
    return _clip(s, 220)


def _num(v: Any):
    try:
        x = float(v)
        return None if x != x else x
    except Exception:
        return None


def _norm(s: Any):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _asset_name(t: str):
    try:
        d = json.load((SYMS / t / "parameters.json").open(encoding="utf-8"))
        return str(((d.get("asset_meta") or {}).get("name") or "")).strip()
    except Exception:
        return ""


def _aliases(t: str):
    out = {t.lower()}
    name = _asset_name(t)
    clean = _norm(SUFFIX.sub(" ", name))
    full = _norm(name)
    for a in [clean, full]:
        if len(a) >= 4: out.add(a)
    parts = [p for p in clean.split() if len(p) >= 4 and p not in {"the", "and"}]
    if parts: out.add(parts[0])
    if len(parts) >= 2: out.add(" ".join(parts[:2]))
    return out


def _direct(a: dict, t: str):
    raw = f"{a.get('title') or ''} {a.get('summary') or ''} {a.get('url') or ''}"
    if re.search(rf"(?<![A-Z0-9]){re.escape(t)}(?![A-Z0-9])", raw.upper()): return True
    hay = f" {_norm(raw)} "
    return any(f" {_norm(x)} " in hay for x in _aliases(t) if len(_norm(x)) >= 4)


def _sent(a: dict, t: str):
    for x in a.get("ticker_sentiment") or []:
        if str(x.get("ticker") or "").upper().strip() == t: return x
    return None


def _articles(t: str):
    rows = []
    for p in sorted((CACHE / t).glob(f"av_{t}_*.json.gz"), reverse=True)[:8]:
        try:
            rows += [x for x in (json.load(gzip.open(p, "rt", encoding="utf-8")).get("feed") or []) if isinstance(x, dict)]
        except Exception: pass
    return rows


def articles_for_ticker(ticker: str, *, limit: int = 2, now=None, min_published_at=None, max_detail_lag_days: int = 7):
    """Return direct ticker/company article snippets for dashboard display.

    These snippets are display-only. They are filtered for direct ticker/company
    mention so peer articles such as Apple->DELL are excluded. Old direct snippets
    are still shown as 참고 기사 because the numeric score cache currently does not
    persist the full fresh AlphaVantage feed.
    """
    t = str(ticker or "").upper().strip()
    seen, out = set(), []
    for a in _articles(t):
        s = _sent(a, t)
        if not s or not _direct(a, t): continue
        pub_raw = str(a.get("time_published") or "")
        pub_iso = _iso(pub_raw)
        title = _clip(a.get("title"), 180)
        url = str(a.get("url") or "").strip()
        key = url or pub_raw + title.lower()
        if not title or key in seen: continue
        seen.add(key)
        sc = _num(s.get("ticker_sentiment_score")) or 0.0
        rel = _num(s.get("relevance_score")); rel = max(0.0, min(1.0, rel if rel is not None else 1.0))
        risk = max(0.0, -sc) * rel
        out.append({"ticker":t,"title":title,"summary":_summary(a.get("summary")),"url":url,"source":str(a.get("source") or a.get("source_domain") or "").strip(),"published_at":pub_iso,"published_raw":pub_raw,"published_age_hours":_age(pub_iso, now),"sentiment_score":round(sc,6),"sentiment_label":str(s.get("ticker_sentiment_label") or "").strip(),"relevance_score":round(rel,6),"risk_score":round(risk,6),"basis":"display_direct_reference_with_negative_risk" if risk > 0 else "display_direct_reference"})
    out.sort(key=lambda x:(str(x.get("published_at") or ""), float(x.get("risk_score") or 0)), reverse=True)
    return translate_articles_for_dashboard(out[:max(1, int(limit or 1))])
