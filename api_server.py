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
