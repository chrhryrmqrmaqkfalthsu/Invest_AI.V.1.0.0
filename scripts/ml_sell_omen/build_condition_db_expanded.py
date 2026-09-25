"""Build a clean expanded condition DB for sell-omen ML.

Purpose:
    B-1b용 학습셋 확장 스크립트. ticker_sentiment 원천과 yfinance 가격 데이터를
    결합해 종목별 일별 condition_db를 만든다.

Leakage policy:
    - ticker_sentiment는 effective_date = news_date + lag_days 이후에만 사용한다.
    - GPT 시장 이벤트 has_*는 기본 생성하지 않는다.
    - 기본 market history는 가격 기반 score/vix가 있는 market_history.csv다.
      지정 파일에 해당 컬럼이 없으면 안전한 기본값(market_score=50, vix=18)으로 채운다.
    - fwd_*는 라벨 원천으로만 쓰며 학습 피처에서는 제외해야 한다.

이 스크립트는 데이터 빌더이며 모델 학습은 poc_train.py에서 수행한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SENTIMENT_DIR = ROOT / "data" / "_system" / "ticker_sentiment"
DEFAULT_MARKET_HISTORY = ROOT / "data" / "_system" / "market_history.csv"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "_system" / "condition_db_sell_omen_clean"

NEWS_COLUMNS = [
    "news_count",
    "sentiment_avg",
    "sentiment_std",
    "bullish_ratio",
    "bearish_ratio",
    "relevance_avg",
    "high_rel_count",
]
TOPIC_PREFIXES = ("sent_", "cnt_")
OUTPUT_COLUMNS = [
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "RSI",
    "MACD",
    "MACD_golden",
    "Aligned_bull",
    "Volume_ratio",
    "Trend_pct",
    "Momentum_20d",
    "BB_pos",
    "market_score",
    "vix",
    "fwd_5d",
    "fwd_10d",
    "fwd_20d",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build expanded clean condition DB for sell-omen ML")
    parser.add_argument("--sentiment-dir", type=Path, default=DEFAULT_SENTIMENT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--market-history", type=Path, default=DEFAULT_MARKET_HISTORY)
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--tickers", nargs="*", default=None, help="명시 tickers. 없으면 ticker_sentiment 전체")
    parser.add_argument("--max-tickers", type=int, default=0, help="0이면 제한 없음")
    parser.add_argument("--sentiment-lag-days", type=int, default=1)
    parser.add_argument("--include-price-market", action="store_true", help="market history의 score/vix 가격 기반 컬럼 병합. 없으면 기본값 사용")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def _list_tickers(sentiment_dir: Path, explicit: list[str] | None, max_tickers: int) -> list[str]:
    if explicit:
        tickers = [str(t).upper().strip() for t in explicit if str(t).strip()]
    else:
        tickers = sorted({p.name.removesuffix("_daily.csv").upper() for p in sentiment_dir.glob("*_daily.csv")})
    if max_tickers and max_tickers > 0:
        tickers = tickers[: int(max_tickers)]
    return tickers


def _download_prices(ticker: str, start: str, end: str | None) -> pd.DataFrame:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance is required to build expanded condition DB") from exc

    df = yf.download(ticker, start=start, end=end, auto_adjust=False, progress=False, threads=False)
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df = df.reset_index()
    if "Date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "Date"})
    keep = [c for c in ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"] if c in df.columns]
    df = df[keep].copy()
    if "Close" not in df.columns and "Adj Close" in df.columns:
        df["Close"] = df["Adj Close"]
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            df[col] = df["Close"] if col != "Volume" else 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["Date", "Open", "High", "Low", "Close", "Volume"]]


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0.0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy().sort_values("Date").reset_index(drop=True)
    close = out["Close"].astype(float)
    volume = out["Volume"].astype(float)

    out["RSI"] = _rsi(close).fillna(50.0)
    ema12 = close.ewm(span=12, adjust=False, min_periods=1).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=1).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False, min_periods=1).mean()
    out["MACD"] = macd
    out["MACD_golden"] = ((macd > signal) & (macd.shift(1) <= signal.shift(1))).astype(int)

    ma20 = close.rolling(20, min_periods=1).mean()
    ma60 = close.rolling(60, min_periods=1).mean()
    out["Aligned_bull"] = ((ma20 > ma60) & (close > ma20)).astype(int)
    out["Volume_ratio"] = volume / volume.shift(1).rolling(20, min_periods=1).mean().replace(0.0, np.nan)
    out["Trend_pct"] = (close / ma20.replace(0.0, np.nan) - 1.0) * 100.0
    out["Momentum_20d"] = close.pct_change(20) * 100.0

    std20 = close.rolling(20, min_periods=2).std()
    upper = ma20 + 2.0 * std20
    lower = ma20 - 2.0 * std20
    out["BB_pos"] = (close - lower) / (upper - lower).replace(0.0, np.nan)

    for n in (5, 10, 20):
        out[f"fwd_{n}d"] = (close.shift(-n) / close - 1.0) * 100.0

    return out


def _load_sentiment(ticker: str, sentiment_dir: Path, lag_days: int) -> pd.DataFrame:
    path = sentiment_dir / f"{ticker}_daily.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "date" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize() + pd.Timedelta(days=int(lag_days))
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    cols = ["Date"]
    for col in df.columns:
        if col in NEWS_COLUMNS or col.startswith(TOPIC_PREFIXES):
            cols.append(col)
    return df[cols]


def _merge_sentiment(price_df: pd.DataFrame, sent_df: pd.DataFrame) -> pd.DataFrame:
    if sent_df.empty:
        return price_df.copy()
    merged = pd.merge_asof(
        price_df.sort_values("Date"),
        sent_df.sort_values("Date"),
        on="Date",
        direction="backward",
    )
    return merged


def _load_market_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    date_col = "date" if "date" in df.columns else ("Date" if "Date" in df.columns else None)
    if date_col is None:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
    rename = {}
    if "score" in df.columns:
        rename["score"] = "market_score"
    if "vix" in df.columns:
        rename["vix"] = "vix"
    elif "vix_level" in df.columns:
        rename["vix_level"] = "vix"
    df = df.rename(columns=rename)
    keep = [c for c in ["Date", "market_score", "vix"] if c in df.columns]
    return df[keep].dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)


def _coerce_numeric_column(out: pd.DataFrame, col: str, default: float) -> pd.Series:
    if col not in out.columns:
        return pd.Series(default, index=out.index, dtype="float64")
    series = pd.to_numeric(out[col], errors="coerce")
    if not isinstance(series, pd.Series):
        series = pd.Series(default, index=out.index, dtype="float64")
    return series.fillna(default).astype(float)


def _merge_market(df: pd.DataFrame, market_df: pd.DataFrame) -> pd.DataFrame:
    if market_df.empty or "Date" not in market_df.columns:
        out = df.copy()
        out["market_score"] = 50.0
        out["vix"] = 18.0
        return out
    out = pd.merge_asof(
        df.sort_values("Date"),
        market_df.sort_values("Date"),
        on="Date",
        direction="backward",
    )
    out["market_score"] = _coerce_numeric_column(out, "market_score", 50.0)
    out["vix"] = _coerce_numeric_column(out, "vix", 18.0)
    return out


def _finalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in NEWS_COLUMNS:
        if col not in out.columns:
            out[col] = 0.0
    topic_cols = sorted([c for c in out.columns if c.startswith(TOPIC_PREFIXES)])
    for col in OUTPUT_COLUMNS + NEWS_COLUMNS + topic_cols:
        if col not in out.columns:
            out[col] = np.nan
    ordered = OUTPUT_COLUMNS + NEWS_COLUMNS + topic_cols
    out = out[ordered].copy()
    numeric_cols = [c for c in out.columns if c != "Date"]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    fill_zero_cols = NEWS_COLUMNS + topic_cols
    for col in fill_zero_cols:
        out[col] = out[col].fillna(0.0)
    out["market_score"] = out["market_score"].fillna(50.0)
    out["vix"] = out["vix"].fillna(18.0)
    out["Volume_ratio"] = out["Volume_ratio"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    return out


def build_one_ticker(
    ticker: str,
    *,
    sentiment_dir: Path,
    market_df: pd.DataFrame,
    start: str,
    end: str | None,
    sentiment_lag_days: int,
    include_price_market: bool,
) -> pd.DataFrame:
    price = _download_prices(ticker, start=start, end=end)
    if price.empty:
        return pd.DataFrame()
    price = _add_price_features(price)
    sentiment = _load_sentiment(ticker, sentiment_dir, sentiment_lag_days)
    merged = _merge_sentiment(price, sentiment)
    if include_price_market:
        merged = _merge_market(merged, market_df)
    else:
        merged["market_score"] = 50.0
        merged["vix"] = 18.0
    return _finalize_columns(merged)


def main() -> int:
    args = _parse_args()
    tickers = _list_tickers(args.sentiment_dir, args.tickers, args.max_tickers)
    if not tickers:
        raise RuntimeError(f"no tickers found from {args.sentiment_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    market_df = _load_market_history(args.market_history) if args.include_price_market else pd.DataFrame()
    market_cols = [c for c in ["market_score", "vix"] if c in market_df.columns]

    print("=== B-1b clean condition DB builder ===")
    print(f"tickers={len(tickers)} output_dir={args.output_dir}")
    print(f"sentiment_lag_days={args.sentiment_lag_days} include_price_market={bool(args.include_price_market)}")
    print(f"market_history={args.market_history}")
    print(f"market_history_columns={market_cols if market_cols else 'DEFAULTS_ONLY'}")
    print("gpt_market_events=EXCLUDED_BY_DESIGN")

    ok = 0
    failed = 0
    for ticker in tickers:
        out_path = args.output_dir / f"{ticker}.csv"
        if out_path.exists() and not args.overwrite:
            print(f"SKIP {ticker}: exists")
            continue
        try:
            df = build_one_ticker(
                ticker,
                sentiment_dir=args.sentiment_dir,
                market_df=market_df,
                start=args.start,
                end=args.end,
                sentiment_lag_days=args.sentiment_lag_days,
                include_price_market=bool(args.include_price_market),
            )
            if df.empty:
                print(f"MISS {ticker}: no price data")
                failed += 1
                continue
            df.to_csv(out_path, index=False)
            print(f"OK {ticker}: rows={len(df)} path={out_path}")
            ok += 1
        except Exception as exc:
            print(f"FAIL {ticker}: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed += 1
    print(f"done ok={ok} failed={failed}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
