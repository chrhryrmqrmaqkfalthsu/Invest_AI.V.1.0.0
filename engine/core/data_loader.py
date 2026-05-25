"""
시세 데이터 로더
- 한국 종목/ETF: pykrx (안정) + yfinance (보조)
- 미국 종목/ETF: yfinance
- 재시도 (3회, 지수 백오프)
- 메모리 캐시 (5분)
"""
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False

from engine.core.logger import get_logger

log = get_logger("data_loader")


# ---------- 캐시 ----------
_CACHE: dict[str, tuple[pd.DataFrame, float]] = {}
_CACHE_TTL_SEC = 300  # 5분


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
    """6자리 숫자 또는 .KS/.KQ 접미사면 한국 종목"""
    base = ticker.split(".")[0]
    return base.isdigit() and len(base) == 6


def normalize_ticker(ticker: str) -> dict:
    """
    티커를 yfinance/pykrx에 적합한 형태로 변환.
    반환: {'raw': '379800', 'yf': '379800.KS', 'krx': '379800', 'is_kr': True}
    """
    base = ticker.split(".")[0].upper()
    if is_korean_ticker(base):
        return {
            "raw": ticker,
            "yf": f"{base}.KS",
            "krx": base,
            "is_kr": True,
        }
    return {
        "raw": ticker,
        "yf": base,
        "krx": None,
        "is_kr": False,
    }


# ---------- pykrx 로더 (한국) ----------
def _load_korean_pykrx(
    ticker: str, start: str, end: str
) -> Optional[pd.DataFrame]:
    if not PYKRX_AVAILABLE:
        return None
    try:
        df = pykrx_stock.get_market_ohlcv(
            start.replace("-", ""),
            end.replace("-", ""),
            ticker,
        )
        if df is None or df.empty:
            return None
        # 한글 컬럼 → 영문
        df = df.rename(
            columns={
                "시가": "Open",
                "고가": "High",
                "저가": "Low",
                "종가": "Close",
                "거래량": "Volume",
            }
        )
        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"
        # 등락률 등 불필요 컬럼 제거
        keep = ["Open", "High", "Low", "Close", "Volume"]
        df = df[[c for c in keep if c in df.columns]]
        return df
    except Exception as e:
        log.warning(f"pykrx load failed for {ticker}: {e}")
        return None


# ---------- yfinance 로더 ----------
def _load_yfinance(
    yf_ticker: str, start: str, end: str
) -> Optional[pd.DataFrame]:
    try:
        df = yf.download(
            yf_ticker,
            start=start,
            end=end,
            progress=False,
            auto_adjust=False,
        )
        if df is None or df.empty:
            return None
        # MultiIndex 컬럼 처리 (yfinance 0.2.50+)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        keep = ["Open", "High", "Low", "Close", "Volume"]
        df = df[[c for c in keep if c in df.columns]]
        return df
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
    """
    OHLCV 시세 데이터 로드.

    Args:
        ticker: '379800', '379800.KS', 'AAPL' 등
        years: 가져올 기간 (년)
        end_date: 종료일 'YYYY-MM-DD', None이면 오늘
        use_cache: 5분 캐시 사용 여부
        max_retries: 실패 시 재시도 횟수

    Returns:
        DataFrame with columns [Open, High, Low, Close, Volume]
    """
    norm = normalize_ticker(ticker)
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (
        datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=365 * years + 30)
    ).strftime("%Y-%m-%d")

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
            if norm["is_kr"] and PYKRX_AVAILABLE:
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
            else:
                raise ValueError(
                    f"insufficient data: rows={len(df) if df is not None else 0}"
                )
        except Exception as e:
            last_err = e
            wait = 2 ** (attempt - 1)
            log.warning(
                f"attempt {attempt}/{max_retries} failed for {ticker}: {e} "
                f"(retry in {wait}s)"
            )
            time.sleep(wait)

    if df is None or df.empty:
        raise RuntimeError(
            f"Failed to load OHLCV for {ticker} after {max_retries} attempts: {last_err}"
        )

    df = df.sort_index()
    df = df.dropna(subset=["Close"])
    if use_cache:
        _cache_set(cache_key, df)

    log.info(
        f"loaded {ticker}: {len(df)} rows "
        f"({df.index[0].date()} ~ {df.index[-1].date()})"
    )
    return df


# ---------- 현재가 (실시간) ----------
def get_current_price(ticker: str) -> Optional[float]:
    """
    가장 최근 종가 / 현재가를 반환.
    KIS API 미연동 상태에서는 yfinance 또는 pykrx로 일봉 마지막 가격 사용.
    """
    norm = normalize_ticker(ticker)
    try:
        if norm["is_kr"] and PYKRX_AVAILABLE:
            today = datetime.now().strftime("%Y%m%d")
            df = pykrx_stock.get_market_ohlcv(today, today, norm["krx"])
            if df is not None and not df.empty:
                return float(df["종가"].iloc[-1])
        # fallback yfinance
        t = yf.Ticker(norm["yf"])
        hist = t.history(period="2d")
        if hist is not None and not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        log.warning(f"get_current_price failed for {ticker}: {e}")
    return None


# ---------- 캐시 무효화 ----------
def clear_cache() -> None:
    _CACHE.clear()
    log.info("data_loader cache cleared")


if __name__ == "__main__":
    # 간단 테스트 (네트워크 필요)
    print("=" * 50)
    print("data_loader 테스트")
    print("=" * 50)

    for tk in ["379800", "AAPL"]:
        try:
            df = load_ohlcv(tk, years=1)
            print(f"\n✅ {tk}: {len(df)} rows")
            print(f"  기간: {df.index[0].date()} ~ {df.index[-1].date()}")
            print(f"  최종가: {df['Close'].iloc[-1]:.2f}")
        except Exception as e:
            print(f"\n❌ {tk} 실패: {e}")
