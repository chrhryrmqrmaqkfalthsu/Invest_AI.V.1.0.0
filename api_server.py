# api_server.py — KINGMAKER 대시보드 + API.
# broker 직접 주문 금지. semi-auto 조작은 intent 파일만 기록한다.
import csv
import glob
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from engine.live.manual_buy_intent import (
    CENTRAL_BUY_CANDIDATES_PATH,
    MANUAL_BUY_INTENT_PATH,
    create_manual_buy_intent,
    load_candidate_state,
    read_json,
)
from engine.live.manual_sell_intent import (
    MANUAL_SELL_INTENT_PATH,
    POSITIONS_PATH as MANUAL_SELL_POSITIONS_PATH,
    create_manual_sell_intent,
    load_manual_sell_state,
)
from engine.live.news_article_summary import articles_for_ticker as holding_news_articles_for_ticker
from engine.live.holding_news_queue import HOLDING_NEWS_SCORE_LOGIC_VERSION, MAX_SCORE_ARTICLE_AGE_DAYS

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = "logs"
SYS = os.path.join("data", "_system")
UNIVERSE_MANIFEST_PATH = os.path.join(SYS, "live_universe_lr8d_stage1_manifest.json")
DASHBOARD_MAIN_PATH = BASE_DIR / "dashboard_home.html"
HOLDING_NEWS_CACHE_PATH = os.path.join(SYS, "holding_news_sentiment_cache.json")
POSITIONS_PATH = os.path.join(SYS, "positions.json")
SCHEDULED_OPEN_BUY_QUEUE_PATH = os.path.join(SYS, "scheduled_open_buy_queue.json")
NEWS_ALERT_STATE_PATH = os.path.join(SYS, "news_alert_state.json")
NEWS_RISK_LOW = 0.30
NEWS_RISK_HIGH = 0.60
NEWS_CACHE_STALE_HOURS = 72.0


def _holding_news_score_row_usable(row) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("score_logic_version") != HOLDING_NEWS_SCORE_LOGIC_VERSION:
        return False
    try:
        return int(row.get("max_score_article_age_days") or 0) == MAX_SCORE_ARTICLE_AGE_DAYS
    except Exception:
        return False


app = FastAPI(title="KINGMAKER Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ===== 대시보드 HTML 서빙 =====
# 기존 구조: python -m http.server 8002가 HTML을 서빙하고, api_server:app 8001이 API를 담당했다.
# 새 구조: api_server:app 8001이 /dashboard에서 HTML까지 직접 서빙한다.
# 결과: 로컬 터널은 8001 하나면 충분하다. 8002는 호환/구버전 경로로만 남긴다.

def _dashboard_html_response():
    if not DASHBOARD_MAIN_PATH.exists():
        raise HTTPException(status_code=500, detail=f"dashboard file missing: {DASHBOARD_MAIN_PATH}")
    html = DASHBOARD_MAIN_PATH.read_text(encoding="utf-8")
    # 같은 8001 origin에서 HTML과 API를 같이 쓰게 만들어 터널/포트 혼선을 제거한다.
    html = html.replace('const API="http://localhost:8001";', 'const API=window.location.origin;')
    old_news_js = """async function loadNews(){
  let n; try{ n=await (await fetch(`${API}/api/live/news`)).json(); }catch(e){ return; }
  const sent=(n.sentiment||{}).entries||{};
  const rows=Object.values(sent).map(e=>
    `<div class=\"kv\"><span>${e.ticker}</span>
     <span style=\"color:${e.score>=0?'var(--up)':'var(--down)'}\">${fmt(e.score,3)} <small style=\"color:var(--dim)\">(${e.article_count||0}건)</small></span></div>`).join('');
  document.getElementById('news-sentiment').innerHTML=rows||'<div class=\"loading\">데이터 없음</div>';
}"""
    new_news_js = """function newsRiskView(score, missing, stale){
  if(missing) return {color:'var(--dim)', label:'캐시 없음', text:'—'};
  const n = Number(score);
  if(!isFinite(n)) return {color:'var(--dim)', label:'점수 없음', text:'—'};
  let color='var(--up)', label='낮음';
  if(n >= 0.60){ color='var(--down)'; label='높음'; }
  else if(n >= 0.30){ color='var(--gold)'; label='주의'; }
  if(stale) label += ' · 오래됨';
  return {color, label, text:n.toFixed(3)};
}
function newsAgeText(hours){
  const n = Number(hours);
  if(!isFinite(n)) return '';
  if(n < 1) return `${Math.max(0, Math.round(n*60))}분 전`;
  if(n < 72) return `${n.toFixed(1)}시간 전`;
  return `${(n/24).toFixed(1)}일 전`;
}
function renderNewsArticles(articles){
  const rows = Array.isArray(articles) ? articles.slice(0,2) : [];
  if(!rows.length) return '<div style="font-size:11px;color:var(--dim);margin-top:6px;">기사 제목/요약 캐시 없음</div>';
  return `<div style="margin-top:7px;display:flex;flex-direction:column;gap:6px;">${rows.map(a=>{
    const when = newsAgeText(a.published_age_hours);
    const sent = a.sentiment_score == null ? '' : `감성 ${Number(a.sentiment_score).toFixed(3)}`;
    const rel = a.relevance_score == null ? '' : `관련도 ${Number(a.relevance_score).toFixed(2)}`;
    const risk = a.risk_score == null ? '' : `위험 ${Number(a.risk_score).toFixed(3)}`;
    const meta = [when || htmlEsc(a.published_at || ''), htmlEsc(a.source || ''), sent, rel, risk].filter(Boolean).join(' · ');
    const title = htmlEsc(a.title || '제목 없음');
    const summary = htmlEsc(a.summary || '요약 없음');
    const href = a.url ? ` href="${htmlEsc(a.url)}" target="_blank" rel="noreferrer"` : '';
    return `<div style="border-top:1px solid var(--line);padding-top:6px;line-height:1.45;">
      <div style="font-size:11px;color:var(--dim);">${meta}</div>
      <a${href} style="color:var(--txt);font-size:12px;font-weight:700;text-decoration:none;">${title}</a>
      <div style="font-size:11px;color:#8b95a8;margin-top:2px;">${summary}</div>
    </div>`;
  }).join('')}</div>`;
}
async function loadNews(){
  let n; try{ n=await (await fetch(`${API}/api/live/news`)).json(); }catch(e){ return; }
  const sentiment = n.sentiment || {};
  const meta = sentiment.meta || {};
  const sent = sentiment.entries || {};
  const list = Object.values(sent).sort((a,b)=>Number(b.score ?? -1)-Number(a.score ?? -1));
  const note = `<div class="univ-note">보유 ${meta.held_count??list.length}종목 기준 · 비보유 캐시 ${meta.hidden_non_holding_count??0}개 숨김 · 위험점수 기준: 낮음 &lt;0.30 / 주의 0.30~0.60 / 높음 ≥0.60<br>뉴스 캐시는 run_live 시장시간 tick 후 기본 60분마다 갱신됩니다. 마지막 갱신 ${htmlEsc(meta.cache_updated_at || '확인 불가')}</div>`;
  const rows=list.map(e=>{
    const rv = newsRiskView(e.score, e.missing, e.stale);
    const fetched = newsAgeText(e.fetched_age_hours);
    const latest = newsAgeText(e.latest_article_age_hours);
    const sub = [
      `${Number(e.article_count || 0)}건`,
      fetched ? `캐시 ${fetched}` : '',
      latest ? `최신 기사 ${latest}` : '',
      e.source ? `source ${htmlEsc(e.source)}` : ''
    ].filter(Boolean).join(' · ');
    return `<div class="kv" style="align-items:flex-start;gap:14px;"><span style="flex:1;min-width:0;"><b>${htmlEsc(e.ticker || '')}</b><br><small style="color:var(--dim)">${sub || '데이터 없음'}</small>${renderNewsArticles(e.articles)}</span>
      <span style="color:${rv.color};font-size:17px;font-weight:900;text-align:right;min-width:64px;">${rv.text}<br><small style="font-size:11px;color:${rv.color}">${rv.label}</small></span></div>`;
  }).join('');
  document.getElementById('news-sentiment').innerHTML=note+(rows||'<div class="loading">보유 종목 뉴스 점수 없음</div>');
}"""
    if old_news_js in html:
        html = html.replace(old_news_js, new_news_js)
    return HTMLResponse(content=html, media_type="text/html")


@app.get("/", include_in_schema=False)
def root_dashboard_redirect():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return _dashboard_html_response()


@app.get("/dashboard_home.html", include_in_schema=False)
def dashboard_home_compat():
    return _dashboard_html_response()


@app.get("/dashboard_live.html", include_in_schema=False)
def dashboard_live_deprecated():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/dashboard.html", include_in_schema=False)
def dashboard_legacy_deprecated():
    return RedirectResponse(url="/dashboard", status_code=302)


# ===== 구버전 shadow dashboard API =====
def load_ticker(ticker: str):
    path = os.path.join(LOG_DIR, f"exit_shadow_{ticker}.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            inp = d.get("inputs", {}) or {}
            new = d.get("new", {}) or {}
            rows.append({
                "date": d.get("date"),
                "ticker": d.get("ticker"),
                "open": inp.get("open"),
                "high": inp.get("high"),
                "low": inp.get("low"),
                "close": inp.get("close"),
                "entry_price": inp.get("entry_price"),
                "stop_loss": inp.get("stop_loss"),
                "take_profit": inp.get("take_profit"),
                "direction": inp.get("direction"),
                "holding_days": d.get("holding_days"),
                "exit_reason": new.get("reason"),
                "exit_price": new.get("trigger_price") or new.get("fill_price_base"),
                "diff_type": d.get("difference_type"),
            })
    return rows


@app.get("/api/tickers")
def list_tickers():
    files = glob.glob(os.path.join(LOG_DIR, "exit_shadow_*.jsonl"))
    names = []
    for f in files:
        base = os.path.basename(f)
        name = base[len("exit_shadow_"):-len(".jsonl")]
        names.append(name)
    return sorted(names)


@app.get("/api/trades/{ticker}")
def trades(ticker: str):
    return load_ticker(ticker.upper())


# ===== 라이브 대시보드 엔드포인트 =====
def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _file_mtime_iso(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def _parse_iso(value) -> datetime | None:
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


def _age_hours(value, *, now: datetime | None = None) -> float | None:
    dt = _parse_iso(value)
    if dt is None:
        return None
    current = now or datetime.now(timezone.utc)
    return max(0.0, round((current - dt).total_seconds() / 3600.0, 3))


def _position_tickers_from_file() -> list[str]:
    pos = _read_json(POSITIONS_PATH, {})
    rows: list[str] = []
    for ticker, payload in (pos.items() if isinstance(pos, dict) else []):
        if not isinstance(payload, dict):
            continue
        t = str(ticker or "").upper().strip()
        if not t:
            continue
        try:
            shares = float(payload.get("shares") or 0)
        except Exception:
            shares = 0.0
        if shares > 0:
            rows.append(t)
    return sorted(set(rows))


def _news_risk_label(score) -> str:
    try:
        s = float(score)
    except Exception:
        return "missing"
    if s >= NEWS_RISK_HIGH:
        return "high"
    if s >= NEWS_RISK_LOW:
        return "medium"
    return "low"


# 현재가 캐시 30초. yfinance 독립 조회 — 봇 브로커와 무관.
_price_cache = {}


def _get_price(ticker):
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return None
    now = time.time()
    hit = _price_cache.get(ticker)
    if hit and now - hit[1] < 30:
        return hit[0]
    price = None
    # 장전/장후에는 fast_info가 정규장 종가에 머무를 수 있어 1분봉 pre/post close를 우선 사용한다.
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=True)
        if hist is not None and not hist.empty:
            close = hist["Close"].dropna()
            if not close.empty:
                price = float(close.iloc[-1])
    except Exception:
        price = None
    if price is None:
        try:
            data = yf.Ticker(ticker).fast_info
            raw = data.get("lastPrice") if hasattr(data, "get") else data["lastPrice"]
            if raw is not None:
                price = float(raw)
        except Exception:
            price = None
    if price is None:
        try:
            hist = yf.Ticker(ticker).history(period="2d", prepost=True)
            if hist is not None and not hist.empty:
                price = float(hist["Close"].iloc[-1])
        except Exception:
            price = None
    _price_cache[ticker] = (price, now)
    return price


@app.get("/api/live/positions")
def live_positions():
    pos = _read_json(POSITIONS_PATH, {})
    out = []
    for ticker, p in (pos.items() if isinstance(pos, dict) else []):
        if not isinstance(p, dict):
            continue
        cur = _get_price(ticker)
        entry = p.get("entry_price")
        pnl_pct = ((cur / entry - 1) * 100) if (cur and entry) else None
        out.append({
            "ticker": ticker,
            "entry_price": entry,
            "current_price": cur,
            "stop_price": p.get("stop_price"),
            "target_price": p.get("target_price"),
            "trailing_stop": p.get("trailing_stop"),
            "shares": p.get("shares"),
            "direction": p.get("rulebook_direction"),
            "exit_strategy": p.get("exit_strategy"),
            "max_holding_days": p.get("max_holding_days"),
            "entry_date": p.get("entry_date"),
            "pnl_pct": pnl_pct,
            "rulebook": p.get("rulebook_snapshot", {}),
            "entry": {
                "signal_score": p.get("signal_score_at_entry"),
                "signal_threshold": p.get("signal_threshold_at_entry"),
                "win_rate": p.get("win_rate_at_entry"),
                "market_score": p.get("entry_market_score"),
                "sector_score": p.get("entry_sector_score"),
                "vix": p.get("entry_vix_level"),
                "atr": p.get("atr_at_entry"),
            },
        })
    return out


@app.get("/api/live/slots")
def live_slots(max_slots: int = 8):
    """8슬롯 구조로 반환. 빈 슬롯은 empty=True."""
    filled = live_positions()
    slots = []
    for i in range(max_slots):
        if i < len(filled):
            slots.append({"slot": i + 1, "empty": False, **filled[i]})
        else:
            slots.append({"slot": i + 1, "empty": True})
    return slots


@app.get("/api/live/market")
def live_market():
    """시장 상태 / 이벤트 market_state.json."""
    return _read_json(os.path.join(SYS, "market_state.json"), {})


@app.get("/api/live/news")
def live_news():
    """보유 종목 뉴스 점수 + 알림 상태.

    holding_news_sentiment_cache.json은 과거 보유 종목의 캐시도 보존한다.
    대시보드에는 현재 positions.json에서 shares>0인 종목만 내려준다.
    점수는 0~1 위험점수이며, holding_news_queue._ticker_news_risk_score()에서
    AlphaVantage ticker_sentiment의 부정 감성 강도와 relevance로 계산된다.
    """
    now = datetime.now(timezone.utc)
    cache = _read_json(HOLDING_NEWS_CACHE_PATH, {})
    raw_entries = cache.get("entries") if isinstance(cache, dict) else {}
    if not isinstance(raw_entries, dict):
        raw_entries = {}
    held_tickers = _position_tickers_from_file()
    held_set = set(held_tickers)
    cache_tickers = {str(t or "").upper().strip() for t in raw_entries if str(t or "").strip()}
    filtered_entries: dict[str, dict] = {}
    for ticker in held_tickers:
        row = raw_entries.get(ticker) if isinstance(raw_entries.get(ticker), dict) else None
        articles = holding_news_articles_for_ticker(ticker, limit=2, now=now)
        if row is None:
            filtered_entries[ticker] = {
                "ticker": ticker,
                "score": None,
                "risk_label": "missing",
                "missing": True,
                "stale": True,
                "article_count": 0,
                "source": "cache_missing",
                "articles": articles,
            }
            continue
        fetched_age = _age_hours(row.get("fetched_at"), now=now)
        cached_articles = row.get("top_articles") if isinstance(row.get("top_articles"), list) else []
        if cached_articles:
            articles = cached_articles[:2]
        latest_article_time = row.get("latest_article_time_published") or (articles[0].get("published_at") if articles else "")
        latest_article_age = _age_hours(latest_article_time, now=now)
        score_row_usable = _holding_news_score_row_usable(row)
        score = row.get("score") if score_row_usable else None
        filtered_entries[ticker] = {
            **row,
            "ticker": ticker,
            "score": score,
            "risk_label": _news_risk_label(score),
            "missing": False,
            "stale": (fetched_age is None or fetched_age > NEWS_CACHE_STALE_HOURS or not score_row_usable),
            "score_logic_usable": score_row_usable,
            "score_unusable_reason": "" if score_row_usable else "score_logic_version_invalid",
            "fetched_age_hours": fetched_age,
            "latest_article_time_published": latest_article_time,
            "latest_article_age_hours": latest_article_age,
            "articles": articles,
        }
    hidden_non_holding = sorted(cache_tickers - held_set)
    return {
        "sentiment": {
            "entries": filtered_entries,
            "meta": {
                "held_count": len(held_tickers),
                "held_tickers": held_tickers,
                "cache_count": len(cache_tickers),
                "cache_updated_at": cache.get("updated_at") if isinstance(cache, dict) else "",
                "cache_file_mtime": _file_mtime_iso(HOLDING_NEWS_CACHE_PATH),
                "positions_file_mtime": _file_mtime_iso(POSITIONS_PATH),
                "hidden_non_holding_count": len(hidden_non_holding),
                "hidden_non_holding_tickers": hidden_non_holding,
                "held_missing_cache_tickers": [t for t in held_tickers if t not in cache_tickers],
                "risk_score_basis": "0~1 risk score = max negative AlphaVantage ticker_sentiment_score weighted by relevance; higher means worse news risk",
                "article_basis": "Shown articles come from cached AlphaVantage ticker news. Ranked by negative ticker sentiment risk, then recency; fallback is newest relevant article.",
                "risk_thresholds": {"low_lt": NEWS_RISK_LOW, "medium_gte": NEWS_RISK_LOW, "high_gte": NEWS_RISK_HIGH},
                "refresh_path": "scripts/run_live.py: tick_market -> refresh_holding_news_for_positions",
                "refresh_default_minutes": 60,
                "individual_budget_default": 18,
                "stale_after_hours": NEWS_CACHE_STALE_HOURS,
            },
        },
        "alerts": _read_json(NEWS_ALERT_STATE_PATH, {}),
    }


@app.get("/api/live/rulebooks")
def live_rulebooks():
    """data/symbols/*/parameters.json 룰북 요약."""
    out = []
    for path in glob.glob(os.path.join("data", "symbols", "*", "parameters.json")):
        d = _read_json(path, {})
        rb = d.get("rulebook", {}) or {}
        out.append({
            "ticker": (d.get("asset_meta", {}) or {}).get("ticker"),
            "direction": rb.get("direction"),
            "exit_strategy": rb.get("exit_strategy"),
            "max_holding_days": rb.get("max_holding_days"),
            "promotion_id": (d.get("promotion", {}) or {}).get("promotion_id"),
        })
    return out


@app.get("/api/live/candles/{ticker}")
def live_candles(ticker: str, interval: str = "1d", period: str = None):
    """yfinance OHLC. interval=1d/15m/5m/1m 등. 분봉은 장전/장후(prepost)를 포함하고 시간 초까지 반환."""
    default_period = {
        "1m": "5d", "2m": "5d", "5m": "1mo", "15m": "1mo",
        "30m": "1mo", "60m": "3mo", "1h": "3mo", "1d": "2y",
    }
    if period is None:
        period = default_period.get(interval, "1mo")
    intraday = interval not in ("1d", "1wk", "1mo")
    try:
        kwargs = {"period": period, "interval": interval}
        if intraday:
            kwargs["prepost"] = True
        df = yf.Ticker(ticker).history(**kwargs)
    except Exception:
        return []
    out = []
    for idx, row in df.iterrows():
        if intraday:
            t = int(idx.timestamp())
        else:
            t = idx.strftime("%Y-%m-%d")
        try:
            out.append({
                "time": t,
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]) if not (row["Volume"] != row["Volume"]) else 0,
            })
        except Exception:
            continue
    return out


@app.get("/api/live/account")
def live_account():
    """계좌 요약: 스냅샷 기본값에 실시간 positions 현재가 합계를 우선 반영."""
    acct = {}
    rows = []
    snap_path = os.path.join(SYS, "equity_snapshots.csv")
    try:
        with open(snap_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if rows:
            last = rows[-1]
            cash = float(last.get("cash") or 0)
            invested = float(last.get("invested") or 0)
            total = float(last.get("total_value") or 0)
            unreal = float(last.get("unrealized_pnl") or 0)
            acct.update({
                "cash": cash,
                "invested": invested,
                "total_value": total,
                "unrealized_pnl": unreal,
                "holdings_count": int(float(last.get("holdings_count") or 0)),
                "orders_today": int(float(last.get("orders_today") or 0)),
                "snapshot_time": last.get("timestamp"),
                "account_source": "equity_snapshot",
            })
    except Exception:
        pass

    try:
        positions = live_positions()
        active = [p for p in positions if (p.get("shares") is not None and p.get("entry_price") is not None)]
        if active:
            rt_invested = 0.0
            rt_market_value = 0.0
            for p in active:
                try:
                    shares = float(p.get("shares") or 0)
                    entry = float(p.get("entry_price") or 0)
                    cur = p.get("current_price")
                    cur = float(cur) if cur is not None else entry
                except Exception:
                    continue
                rt_invested += entry * shares
                rt_market_value += cur * shares
            rt_unreal = rt_market_value - rt_invested
            base_total = acct.get("total_value") or (acct.get("cash") or 0) + (acct.get("invested") or 0)
            est_cash = base_total - rt_invested if base_total else acct.get("cash")
            acct.update({
                "cash": round(est_cash, 2) if est_cash is not None else None,
                "invested": round(rt_invested, 6),
                "total_value": round((est_cash or 0) + rt_market_value, 2) if est_cash is not None else round(rt_market_value, 2),
                "unrealized_pnl": round(rt_unreal, 6),
                "holdings_count": len(active),
                "positions_market_value": round(rt_market_value, 6),
                "account_source": "realtime_positions_estimated_cash",
            })
    except Exception:
        pass

    try:
        first_total = float(rows[0].get("total_value") or 0) if rows else 0
        total = float(acct.get("total_value") or 0)
        if first_total and total:
            acct["total_return_pct"] = (total / first_total - 1) * 100
    except Exception:
        pass

    try:
        with open(os.path.join(SYS, "trade_log.csv"), encoding="utf-8") as f:
            tot = 0.0
            for r in csv.DictReader(f):
                try:
                    tot += float(r.get("pnl_krw") or 0)
                except Exception:
                    pass
            acct["realized_pnl_total"] = tot
    except Exception:
        pass
    safety = _read_json(os.path.join(SYS, "safety_state.json"), {})
    acct["realized_pnl_today"] = safety.get("realized_pnl_today")
    acct["consecutive_losses"] = safety.get("consecutive_losses")
    if safety.get("orders_today") is not None:
        acct["orders_today"] = safety.get("orders_today")
    return acct


@app.get("/api/live/equity_curve")
def equity_curve():
    """equity_snapshots.csv를 시간순 (time, value) 배열로 반환."""
    out = []
    path = os.path.join(SYS, "equity_snapshots.csv")
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ts = r.get("timestamp")
                val = r.get("total_value")
                if not ts or not val:
                    continue
                try:
                    out.append({"time": ts, "value": round(float(val), 2)})
                except Exception:
                    continue
    except Exception:
        pass
    return out


@app.get("/api/live/trades_history")
def trades_history():
    """trade_log.csv 거래 내역 + 요약 통계."""
    rows = []
    path = os.path.join(SYS, "trade_log.csv")
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                def fnum(k):
                    try:
                        return float(r.get(k) or 0)
                    except Exception:
                        return None
                rows.append({
                    "exited_at": r.get("exited_at"),
                    "ticker": r.get("ticker"),
                    "direction": r.get("direction"),
                    "entry_date": r.get("entry_date"),
                    "entry_price": fnum("entry_price"),
                    "exit_price": fnum("exit_price"),
                    "shares": fnum("shares"),
                    "exit_reason": r.get("exit_reason"),
                    "holding_days": fnum("holding_days"),
                    "pnl_pct": fnum("pnl_pct"),
                    "pnl_krw": fnum("pnl_krw"),
                    "exit_strategy": r.get("exit_strategy"),
                })
    except Exception:
        pass
    n = len(rows)
    wins = [r for r in rows if (r["pnl_pct"] or 0) > 0]
    losses = [r for r in rows if (r["pnl_pct"] or 0) < 0]
    total_pnl = sum(r["pnl_krw"] or 0 for r in rows)
    avg_win = (sum(r["pnl_pct"] or 0 for r in wins) / len(wins)) if wins else 0
    avg_loss = (sum(r["pnl_pct"] or 0 for r in losses) / len(losses)) if losses else 0
    stats = {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / n * 100) if n else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
    }
    rows.sort(key=lambda x: x["exited_at"] or "", reverse=True)
    return {"stats": stats, "trades": rows}


@app.get("/api/live/universe")
def live_universe():
    """라이브 유니버스 후보군 manifest. broker를 import하지 않고 파일만 읽는다."""
    data = read_json(UNIVERSE_MANIFEST_PATH, {})
    items = data.get("items", []) if isinstance(data, dict) else []
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append({
            "ticker": it.get("ticker"),
            "combo_id": it.get("combo_id"),
            "win_rate": it.get("selected_rulebook_win_rate"),
            "profit_factor": it.get("selected_rulebook_profit_factor"),
            "expectancy_pct": it.get("selected_rulebook_expectancy_pct"),
            "stress_worst_expectancy_pct": it.get("stress_worst_expectancy_pct"),
            "worst_drawdown_pct": it.get("worst_drawdown_pct"),
            "trade_count": it.get("selected_rulebook_trade_count"),
            "source_label": it.get("selected_rulebook_source_label"),
        })
    rows.sort(key=lambda r: (r.get("expectancy_pct") or -999), reverse=True)
    return {
        "count": data.get("count", len(rows)) if isinstance(data, dict) else len(rows),
        "exported_at": data.get("exported_at", "") if isinstance(data, dict) else "",
        "run_id": data.get("run_id", "") if isinstance(data, dict) else "",
        "items": rows,
    }


class ManualBuyIntentRequest(BaseModel):
    candidate_id: str
    source: str = "dashboard"


class ManualSellIntentRequest(BaseModel):
    ticker: str
    shares_requested: float | None = None
    source: str = "dashboard"


def _scheduled_open_buy_candidate_state(include_blocked: bool = False) -> dict | None:
    """next_open draft/final queue를 대시보드 후보 패널 형식으로 변환한다.

    central_buy_candidates.json은 구 semi-auto 후보 파일이고, next_open 자동매수 후보는
    scheduled_open_buy_queue.json에 저장된다. 대시보드는 하나의 후보 패널만 갖고 있으므로
    여기에서 next_open 큐를 우선 표시한다. 단, 화면에서 수동 즉시매수 버튼이 켜지지 않도록
    manual_buy_enabled=False와 전용 status를 내려준다.
    """
    queue = _read_json(SCHEDULED_OPEN_BUY_QUEUE_PATH, {})
    if not isinstance(queue, dict):
        return None
    items = queue.get("items")
    if not isinstance(items, list) or not items:
        return None
    candidates: dict[str, dict] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        raw_status = str(row.get("status") or queue.get("item_status") or "").strip().lower()
        if not include_blocked and raw_status in {"executed", "blocked", "expired", "cancelled", "canceled"}:
            continue
        decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
        ticker = str(row.get("ticker") or decision.get("ticker") or "").upper().strip()
        entity_id = str(row.get("entity_id") or decision.get("entity_id") or "").strip()
        if not ticker or not entity_id:
            continue
        if raw_status == "draft":
            display_status = "next_open_draft"
            action_label = "매수하기"
        elif raw_status == "pending":
            display_status = "next_open_pending"
            action_label = "매수하기"
        elif raw_status == "submitted":
            display_status = "manual_requested"
            action_label = "주문 제출"
        elif raw_status == "executed":
            display_status = "auto_executed"
            action_label = "자동 체결"
        elif raw_status == "blocked":
            display_status = "blocked"
            action_label = "차단됨"
        else:
            display_status = raw_status or "next_open_draft"
            action_label = "대기"
        cid = str(row.get("candidate_id") or f"{queue.get('execution_session')}:{entity_id}")
        candidates[cid] = {
            "candidate_id": cid,
            "ticker": ticker,
            "entity_id": entity_id,
            "trade_date": queue.get("execution_session") or row.get("execution_session"),
            "execution_session": queue.get("execution_session") or row.get("execution_session"),
            "signal_session": queue.get("signal_session") or row.get("signal_session"),
            "source": "scheduled_open_buy_queue",
            "queue_phase": queue.get("queue_phase"),
            "queue_status": queue.get("status"),
            "item_status": raw_status,
            "status": display_status,
            "manual_buy_enabled": False,
            "action_label": action_label,
            "note": row.get("note") or (
                "next_open draft 후보: final 확정 전까지 수동 즉시매수 비활성"
                if raw_status == "draft" else
                "next_open final 후보: 개장 후 자동 실행 대기"
                if raw_status == "pending" else ""
            ),
            "created_at": row.get("created_at") or queue.get("updated_at"),
            "updated_at": queue.get("updated_at") or row.get("updated_at"),
            "price": row.get("reference_price"),
            "reference_price": row.get("reference_price"),
            "notional": row.get("notional") or decision.get("notional"),
            "shares": row.get("shares") or decision.get("shares"),
            "score": decision.get("score"),
            "confidence": decision.get("confidence"),
            "strength": decision.get("strength"),
            "effective_strength": decision.get("strength"),
            "signal_score": row.get("signal_score"),
            "signal_threshold": row.get("signal_threshold"),
            "stage": row.get("stage"),
            "rulebook_hash": row.get("rulebook_hash"),
            "candidate_news": row.get("candidate_news") if isinstance(row.get("candidate_news"), dict) else None,
        }
    if not candidates:
        return None
    return {
        "schema_version": 1,
        "buy_mode": "next_open",
        "source": "scheduled_open_buy_queue",
        "queue_status": queue.get("status"),
        "queue_phase": queue.get("queue_phase"),
        "item_status": queue.get("item_status"),
        "signal_session": queue.get("signal_session"),
        "trade_date": queue.get("execution_session"),
        "execution_session": queue.get("execution_session"),
        "updated_at": queue.get("updated_at"),
        "diagnostics": queue.get("diagnostics") if isinstance(queue.get("diagnostics"), dict) else {},
        "manual_buy_enabled": False,
        "candidates": candidates,
    }


@app.get("/api/live/central_candidates")
def central_candidates(include_blocked: bool = False):
    """대시보드 매수 후보.

    next_open 모드에서는 scheduled_open_buy_queue.json의 draft/final 큐를 우선 표시한다.
    구 semi-auto 후보가 필요하거나 next_open 큐가 없으면 central_buy_candidates.json을 반환한다.
    """
    scheduled_state = _scheduled_open_buy_candidate_state(include_blocked=include_blocked)
    if scheduled_state is not None:
        return scheduled_state

    state = load_candidate_state(CENTRAL_BUY_CANDIDATES_PATH)
    candidates = state.get("candidates") if isinstance(state, dict) else None
    hidden_statuses = {"manual_executed", "auto_executed", "expired"}
    if not include_blocked:
        hidden_statuses.add("blocked")
    if isinstance(candidates, dict):
        state = dict(state)
        state["candidates"] = {
            cid: row
            for cid, row in candidates.items()
            if not (
                isinstance(row, dict)
                and str(row.get("status") or "") in hidden_statuses
            )
        }
    return state


@app.get("/api/live/manual_buy_intents")
def manual_buy_intents():
    """대시보드 확인용 manual BUY intent 상태. broker를 import하지 않고 파일만 읽는다."""
    return read_json(MANUAL_BUY_INTENT_PATH, {"schema_version": 1, "intents": {}})


@app.post("/api/live/manual_buy_intent")
def manual_buy_intent(req: ManualBuyIntentRequest):
    """후보 매수 intent만 기록한다. 실제 broker 주문은 paper 프로세스가 처리한다."""
    try:
        row = create_manual_buy_intent(
            candidate_id=req.candidate_id,
            source=req.source or "dashboard",
            candidate_path=CENTRAL_BUY_CANDIDATES_PATH,
            intent_path=MANUAL_BUY_INTENT_PATH,
        )
        return {"ok": True, "intent": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/live/manual_sell_intents")
def manual_sell_intents():
    """대시보드 확인용 manual SELL intent 상태. broker를 import하지 않고 파일만 읽는다."""
    return load_manual_sell_state(MANUAL_SELL_INTENT_PATH)


@app.post("/api/live/manual_sell_intent")
def manual_sell_intent(req: ManualSellIntentRequest):
    """보유 종목 청산 intent만 기록한다. 실제 broker 주문은 paper 프로세스가 처리한다."""
    try:
        row = create_manual_sell_intent(
            ticker=req.ticker,
            shares_requested=req.shares_requested,
            source=req.source or "dashboard",
            positions_path=MANUAL_SELL_POSITIONS_PATH,
            intent_path=MANUAL_SELL_INTENT_PATH,
        )
        return {"ok": True, "intent": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
