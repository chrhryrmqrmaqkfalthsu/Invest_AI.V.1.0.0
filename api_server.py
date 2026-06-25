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
import csv
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


@app.get("/api/live/candles/{ticker}")
def live_candles(ticker: str, interval: str = "1d", period: str = None):
    """yfinance OHLC. interval=1d/15m/5m/1m 등. 분봉은 시간(초)까지 반환."""
    # interval별 안전한 기본 기간 (yfinance 제약)
    default_period = {
        "1m": "5d", "2m": "5d", "5m": "1mo", "15m": "1mo",
        "30m": "1mo", "60m": "3mo", "1h": "3mo", "1d": "2y",
    }
    if period is None:
        period = default_period.get(interval, "1mo")
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval)
    except Exception:
        return []
    intraday = interval not in ("1d", "1wk", "1mo")
    out = []
    for idx, row in df.iterrows():
        if intraday:
            t = int(idx.timestamp())  # 분봉: UNIX 초 (UTC 기준 타임스탬프)
        else:
            t = idx.strftime("%Y-%m-%d")
        try:
            out.append({
                "time": t,
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
            })
        except Exception:
            continue
    return out


@app.get("/api/live/account")
def live_account():
    """계좌 요약: equity_snapshots.csv 마지막 줄 + trade_log.csv 누적손익 + safety_state.json 당일."""
    acct = {}
    # 1) 최신 스냅샷 (csv 마지막 줄)
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
                "cash": cash, "invested": invested, "total_value": total,
                "unrealized_pnl": unreal,
                "holdings_count": int(float(last.get("holdings_count") or 0)),
                "orders_today": int(float(last.get("orders_today") or 0)),
                "snapshot_time": last.get("timestamp"),
            })
            # 첫 스냅샷 대비 총수익률
            try:
                first_total = float(rows[0].get("total_value") or 0)
                if first_total:
                    acct["total_return_pct"] = (total / first_total - 1) * 100
            except Exception:
                pass
    except Exception:
        pass
    # 2) 누적 실현손익 (trade_log.csv pnl 합산)
    try:
        with open(os.path.join(SYS, "trade_log.csv"), encoding="utf-8") as f:
            tot = 0.0
            for r in csv.DictReader(f):
                try: tot += float(r.get("pnl_krw") or 0)
                except Exception: pass
            acct["realized_pnl_total"] = tot
    except Exception:
        pass
    # 3) 당일 실현손익
    safety = _read_json(os.path.join(SYS, "safety_state.json"), {})
    acct["realized_pnl_today"] = safety.get("realized_pnl_today")
    acct["consecutive_losses"] = safety.get("consecutive_losses")
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
                    try: return float(r.get(k) or 0)
                    except Exception: return None
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
    # 통계
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
    # 최신순 정렬
    rows.sort(key=lambda x: x["exited_at"] or "", reverse=True)
    return {"stats": stats, "trades": rows}
