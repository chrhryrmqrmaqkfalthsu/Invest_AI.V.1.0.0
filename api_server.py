# api_server.py — 대시보드 API. broker 직접 주문 금지, semi-auto는 intent 파일만 기록.
import json, glob, os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

# ===== 라이브 대시보드 엔드포인트 =====
import csv
import glob
import time

import yfinance as yf

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

SYS = os.path.join("data", "_system")
UNIVERSE_MANIFEST_PATH = os.path.join(SYS, "live_universe_lr8d_stage1_manifest.json")


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
                "cash": cash, "invested": invested, "total_value": total,
                "unrealized_pnl": unreal,
                "holdings_count": int(float(last.get("holdings_count") or 0)),
                "orders_today": int(float(last.get("orders_today") or 0)),
                "snapshot_time": last.get("timestamp"),
                "account_source": "equity_snapshot",
            })
    except Exception:
        pass

    # equity_snapshots.csv는 하루/주기 스냅샷이라 장중 신규 매수를 늦게 반영할 수 있다.
    # 따라서 현재 positions.json + yfinance 현재가로 보유 투자금/평가금/미실현손익을 우선 보정한다.
    try:
        positions = live_positions()
        active = [
            p for p in positions
            if (p.get("shares") is not None and p.get("entry_price") is not None)
        ]
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
                try: tot += float(r.get("pnl_krw") or 0)
                except Exception: pass
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


@app.get("/api/live/central_candidates")
def central_candidates():
    """central-control semi_auto 대기 후보. broker를 import하지 않고 파일만 읽는다."""
    return load_candidate_state(CENTRAL_BUY_CANDIDATES_PATH)


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
