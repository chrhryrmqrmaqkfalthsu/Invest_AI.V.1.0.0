# api_server.py — 읽기 전용 대시보드 API (봇 코드는 건드리지 않음)
import json, glob, os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

LOG_DIR = "logs"
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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

# ===== 라이브 대시보드 엔드포인트 (읽기 전용) =====
import glob
import time
from functools import lru_cache

import yfinance as yf

SYS = os.path.join("data", "_system")


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# 현재가 캐시 (30초). yfinance 독립 조회 — 봇 브로커와 무관.
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
    try:
        data = yf.Ticker(ticker).fast_info
        raw = data.get("lastPrice") if hasattr(data, "get") else data["lastPrice"]
        if raw is not None:
            price = float(raw)
    except Exception:
        price = None
    if price is None:
        try:
            hist = yf.Ticker(ticker).history(period="2d")
            if hist is not None and not hist.empty:
                price = float(hist["Close"].iloc[-1])
        except Exception:
            price = None
    _price_cache[ticker] = (price, now)
    return price


@app.get("/api/live/positions")
def live_positions():
    pos = _read_json(os.path.join(SYS, "positions.json"), {})
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
    """시장 상태 / 이벤트 (market_state.json)."""
    return _read_json(os.path.join(SYS, "market_state.json"), {})


@app.get("/api/live/news")
def live_news():
    """보유 종목 뉴스 점수 + 알림 상태."""
    return {
        "sentiment": _read_json(os.path.join(SYS, "holding_news_sentiment_cache.json"), {}),
        "alerts": _read_json(os.path.join(SYS, "news_alert_state.json"), {}),
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
