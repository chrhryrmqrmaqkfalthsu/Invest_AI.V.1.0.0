"""
시세 데이터 로더
- 한국 종목/ETF: pykrx (안정) + yfinance (보조)
- 미국 종목/ETF: yfinance
- 재시도 (3회, 지수 백오프)
- 메모리 캐시 (5분)

pykrx는 import 시점에 KRX 로그인 경고를 출력할 수 있으므로 한국 종목을
실제로 조회할 때만 lazy import한다. US-only live startup에서는 pykrx를 건드리지 않는다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from engine.core.logger import get_logger

log = get_logger("data_loader")

_pykrx_stock = None
_pykrx_checked = False


# ---------- pykrx lazy import ----------
def _get_pykrx_stock():
    global _pykrx_stock, _pykrx_checked
    if _pykrx_checked:
        return _pykrx_stock
    _pykrx_checked = True
    try:
        from pykrx import stock as stock_mod
        _pykrx_stock = stock_mod
    except ImportError:
        _pykrx_stock = None
    return _pykrx_stock


# ---------- 캐시 ----------
_CACHE: dict[str, tuple[pd.DataFrame, float]] = {}
_CACHE_TTL_SEC = 300


def _cache_get(key: str) -> Optional[pd.DataFrame]:
    if key in _CACHE:
        df, ts = _CACHE[key]
        if time.time() - ts < _CACHE_TTL_SEC:
            return df.copy()
    return None


def _cache_set(key: str, df: pd.DataFrame) -> None:
    _CACHE[key] = (df.copy(), time.time())


# ---------- 티커 판별 ----------
def is_korean_ticker(ticker: str) -> bool:
    base = ticker.split(".")[0]
    return base.isdigit() and len(base) == 6


def normalize_ticker(ticker: str) -> dict:
    base = ticker.split(".")[0].upper()
    if is_korean_ticker(base):
        return {"raw": ticker, "yf": f"{base}.KS", "krx": base, "is_kr": True}
    return {"raw": ticker, "yf": base, "krx": None, "is_kr": False}


# ---------- pykrx 로더 (한국) ----------
def _load_korean_pykrx(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    pykrx_stock = _get_pykrx_stock()
    if pykrx_stock is None:
        return None
    try:
        df = pykrx_stock.get_market_ohlcv(start.replace("-", ""), end.replace("-", ""), ticker)
        if df is None or df.empty:
            return None
        df = df.rename(columns={"시가": "Open", "고가": "High", "저가": "Low", "종가": "Close", "거래량": "Volume"})
        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"
        keep = ["Open", "High", "Low", "Close", "Volume"]
        return df[[c for c in keep if c in df.columns]]
    except Exception as e:
        log.warning(f"pykrx load failed for {ticker}: {e}")
        return None


# ---------- yfinance 로더 ----------
def _load_yfinance(yf_ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    try:
        df = yf.download(yf_ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        keep = ["Open", "High", "Low", "Close", "Volume"]
        return df[[c for c in keep if c in df.columns]]
    except Exception as e:
        log.warning(f"yfinance load failed for {yf_ticker}: {e}")
        return None


# ---------- 메인 로더 ----------
def load_ohlcv(
    ticker: str,
    years: int = 5,
    end_date: Optional[str] = None,
    use_cache: bool = True,
    max_retries: int = 3,
) -> pd.DataFrame:
    norm = normalize_ticker(ticker)
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365 * years + 30)).strftime("%Y-%m-%d")

    cache_key = f"{norm['raw']}_{years}y_{end_date}"
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            log.debug(f"cache hit: {cache_key}")
            return cached

    df: Optional[pd.DataFrame] = None
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            if norm["is_kr"]:
                df = _load_korean_pykrx(norm["krx"], start_date, end_date)
                if df is None or len(df) < 200:
                    log.info(
                        f"pykrx insufficient for {ticker} "
                        f"(rows={len(df) if df is not None else 0}), fallback yfinance"
                    )
                    df = _load_yfinance(norm["yf"], start_date, end_date)
            else:
                df = _load_yfinance(norm["yf"], start_date, end_date)

            if df is not None and len(df) >= 200:
                break
            raise ValueError(f"insufficient data: rows={len(df) if df is not None else 0}")
        except Exception as e:
            last_err = e
            wait = 2 ** (attempt - 1)
            log.warning(f"attempt {attempt}/{max_retries} failed for {ticker}: {e} (retry in {wait}s)")
            time.sleep(wait)

    if df is None or df.empty:
        raise RuntimeError(f"Failed to load OHLCV for {ticker} after {max_retries} attempts: {last_err}")

    df = df.sort_index().dropna(subset=["Close"])
    if use_cache:
        _cache_set(cache_key, df)
    log.info(f"loaded {ticker}: {len(df)} rows ({df.index[0].date()} ~ {df.index[-1].date()})")
    return df


# ---------- 현재가 ----------
def get_current_price_with_source(ticker: str) -> Optional[dict[str, Any]]:
    norm = normalize_ticker(ticker)
    pykrx_error = None
    try:
        if norm["is_kr"]:
            pykrx_stock = _get_pykrx_stock()
            if pykrx_stock is not None:
                today = datetime.now().strftime("%Y%m%d")
                try:
                    df = pykrx_stock.get_market_ohlcv(today, today, norm["krx"])
                    if df is not None and not df.empty:
                        price = float(df["종가"].iloc[-1])
                        try:
                            quote_date = pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d")
                        except Exception:
                            quote_date = datetime.now().strftime("%Y-%m-%d")
                        return {
                            "price": price,
                            "source": "pykrx_today_ohlcv",
                            "quote_date": quote_date,
                            "normalized": norm,
                        }
                except Exception as e:
                    pykrx_error = str(e)
                    log.debug(f"pykrx current price failed for {ticker}: {e}")

        t = yf.Ticker(norm["yf"])
        hist = t.history(period="2d")
        if hist is not None and not hist.empty:
            price = float(hist["Close"].iloc[-1])
            try:
                quote_date = pd.Timestamp(hist.index[-1]).strftime("%Y-%m-%d")
            except Exception:
                quote_date = None
            return {
                "price": price,
                "source": "yfinance_2d_fallback" if norm["is_kr"] else "yfinance_2d",
                "quote_date": quote_date,
                "normalized": norm,
                "pykrx_error": pykrx_error,
            }
    except Exception as e:
        log.warning(f"get_current_price_with_source failed for {ticker}: {e}")
    return None


def get_current_price(ticker: str) -> Optional[float]:
    q = get_current_price_with_source(ticker)
    if not q:
        return None
    price = q.get("price")
    if price is None or float(price) <= 0:
        return None
    return float(price)


def clear_cache() -> None:
    _CACHE.clear()
    log.info("data_loader cache cleared")


if __name__ == "__main__":
    print("data_loader smoke")
    for tk in ["379800", "AAPL"]:
        try:
            print(tk, get_current_price_with_source(tk))
        except Exception as e:
            print(tk, e)
