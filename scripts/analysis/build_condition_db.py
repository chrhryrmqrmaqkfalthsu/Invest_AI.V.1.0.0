#!/usr/bin/env python3
"""모든 거래일의 상태벡터 + 미래수익률(5/10/20일) 조건 DB 생성/분석.
학습 코드 무수정. 백테스트와 동일 소스(market_history, ticker_sentiment) 재사용.
사용법: python scripts/analysis/build_condition_db.py AAPL
"""
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from engine.core.data_loader import load_ohlcv
from engine.core.indicators import calc_indicators
from engine.market.context import get_market_history, lookup_market_at
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment

EVENT_KEYS = ["has_war","has_rate_hike","has_rate_cut","has_geopolitical",
              "has_tariff","has_export_ban","has_earnings_shock","has_oil_surge",
              "has_banking_crisis","has_inflation","has_fed_statement"]
HORIZONS = [5, 10, 20]

def build(ticker: str) -> pd.DataFrame:
    df = load_ohlcv(ticker, years=6)
    df = calc_indicators(df)
    df = df.reset_index().rename(columns={df.index.name or "index": "Date"})
    if "Date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"])

    mkt_hist = get_market_history(years=7)
    tsent = load_ticker_sentiment(ticker) or {}

    closes = df["Close"].values.astype(float)
    n = len(df)
    rows = []
    for i in range(n):
        r = df.iloc[i]
        dkey = r["Date"].strftime("%Y-%m-%d")
        try:
            mkt = lookup_market_at(mkt_hist, r["Date"]) or {}
        except Exception:
            mkt = {}
        s = tsent.get(dkey, {}) if isinstance(tsent, dict) else {}

        row = {
            "Date": dkey,
            "Close": closes[i],
            "RSI": r.get("RSI"),
            "MACD": r.get("MACD"),
            "MACD_golden": r.get("MACD_golden"),
            "Aligned_bull": r.get("Aligned_bull"),
            "Volume_ratio": r.get("Volume_ratio"),
            "Trend_pct": r.get("Trend_pct"),
            "Momentum_20d": r.get("Momentum_20d"),
            "BB_pos": (closes[i]-r.get("BB_lower",closes[i]))/((r.get("BB_upper",closes[i])-r.get("BB_lower",closes[i])) or 1),
            "market_score": float(mkt.get("score", 50.0)),
            "vix": float(mkt.get("vix", 20.0)),
            "sentiment_avg": float(s.get("sentiment_avg", 0.0)) if isinstance(s, dict) else 0.0,
            "news_count": float(s.get("news_count", 0.0)) if isinstance(s, dict) else 0.0,
        }
        if isinstance(s, dict):
            for k, v in s.items():
                if k.startswith("sent_") or k.startswith("cnt_") or k in ("bullish_ratio","bearish_ratio","relevance_avg"):
                    try: row[k] = float(v)
                    except: pass
        for k in EVENT_KEYS:
            row[k] = int(mkt.get(k, 0) or 0)
        for h in HORIZONS:
            row[f"fwd_{h}d"] = (closes[i+h]/closes[i]-1)*100 if i+h < n else np.nan
        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.dropna(subset=[f"fwd_{HORIZONS[-1]}d"])
    return out

def stat(sub, base, label):
    n = len(sub)
    if n == 0: return None
    m = sub["fwd_20d"].mean(); w = (sub["fwd_20d"]>0).mean()*100
    flag = " (표본부족)" if n < 20 else ""
    return f"  {label:28s} N={n:4d}  20d평균={m:+.2f}%  승률={w:4.1f}%  vs기준 {m-base:+.2f}%p{flag}"

def analyze(out: pd.DataFrame, ticker: str):
    base = out["fwd_20d"].mean()
    bw = (out["fwd_20d"]>0).mean()*100
    print(f"\n{'='*70}\n{ticker} 조건 DB 분석  (전체 {len(out)}일, 기준 20d평균 {base:+.2f}%, 승률 {bw:.1f}%)\n{'='*70}")

    print("\n[RSI 구간]")
    for lo,hi in [(0,30),(30,40),(40,50),(50,60),(60,70),(70,100)]:
        r = stat(out[(out.RSI>=lo)&(out.RSI<hi)], base, f"RSI {lo}-{hi}")
        if r: print(r)

    print("\n[단일 플래그]")
    for col in ["MACD_golden","Aligned_bull"]:
        for v in [0,1]:
            r = stat(out[out[col]==v], base, f"{col}={v}")
            if r: print(r)

    print("\n[sentiment_avg 구간]  (현재 룰북이 쓰는 유일한 뉴스 피처)")
    for lo,hi in [(-1,0),(0,0.15),(0.15,0.3),(0.3,1)]:
        r = stat(out[(out.sentiment_avg>=lo)&(out.sentiment_avg<hi)], base, f"sent_avg {lo}~{hi}")
        if r: print(r)

    topic_cols = [c for c in out.columns if c.startswith("sent_") and c!="sentiment_avg"]
    if topic_cols:
        print("\n[토픽별 감성 — 현재 미사용]  (>0.25 일 때 20d 미래수익)")
        for c in topic_cols:
            hi = out[out[c] > 0.25]
            r = stat(hi, base, f"{c}>0.25")
            if r and len(hi) >= 10: print(r)

    print("\n[조합: 룰북 핵심 조건]")
    combos = [
        ("RSI 40-55 & MACD_golden", (out.RSI>=40)&(out.RSI<55)&(out.MACD_golden==1)),
        ("Aligned_bull & sent>0.2", (out.Aligned_bull==1)&(out.sentiment_avg>0.2)),
        ("RSI<35 & sent>0.2", (out.RSI<35)&(out.sentiment_avg>0.2)),
    ]
    for lbl, mask in combos:
        r = stat(out[mask], base, lbl)
        if r: print(r)

if __name__ == "__main__":
    tk = sys.argv[1] if len(sys.argv)>1 else "AAPL"
    out = build(tk)
    path = ROOT/"data/_system/condition_db"/f"{tk}.csv"
    out.to_csv(path, index=False)
    print(f"저장: {path}  ({len(out)}행 x {len(out.columns)}컬럼)")
    analyze(out, tk)
