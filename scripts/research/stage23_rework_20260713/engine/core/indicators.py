"""
기술 지표 계산 모듈
- 이동평균 (MA5/20/60/200)
- RSI, MACD, Bollinger Bands, ATR
- 거래량 지표
- 추세, 모멘텀 보조 지표
"""
from typing import Optional

import numpy as np
import pandas as pd

from engine.core.logger import get_logger

log = get_logger("indicators")


# ---------- 개별 지표 함수 ----------
def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=1).mean()


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def calc_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd = ema_fast - ema_slow
    macd_signal = calc_ema(macd, signal)
    macd_hist = macd - macd_signal
    return macd, macd_signal, macd_hist


def calc_bollinger(
    series: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = calc_sma(series, period)
    std = series.rolling(window=period, min_periods=1).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    return upper, middle, lower


def calc_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return atr


def calc_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    low_min = low.rolling(window=k_period, min_periods=1).min()
    high_max = high.rolling(window=k_period, min_periods=1).max()
    k = 100 * (close - low_min) / (high_max - low_min).replace(0, np.nan)
    k = k.fillna(50)
    d = k.rolling(window=d_period, min_periods=1).mean()
    return k, d


# ---------- 일괄 적용 ----------
def calc_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    OHLCV 데이터프레임에 모든 기술 지표를 추가하여 반환.
    필수 컬럼: Open, High, Low, Close, Volume
    """
    required = {"Open", "High", "Low", "Close", "Volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df = df.copy()

    # 이동평균
    df["MA5"] = calc_sma(df["Close"], 5)
    df["MA20"] = calc_sma(df["Close"], 20)
    df["MA60"] = calc_sma(df["Close"], 60)
    df["MA200"] = calc_sma(df["Close"], 200)

    # RSI
    df["RSI"] = calc_rsi(df["Close"], 14)

    # MACD
    macd, signal, hist = calc_macd(df["Close"])
    df["MACD"] = macd
    df["MACD_signal"] = signal
    df["MACD_hist"] = hist

    # Bollinger Bands
    bb_upper, bb_mid, bb_lower = calc_bollinger(df["Close"])
    df["BB_upper"] = bb_upper
    df["BB_middle"] = bb_mid
    df["BB_lower"] = bb_lower
    df["BB_width"] = (bb_upper - bb_lower) / bb_mid

    # ATR
    df["ATR"] = calc_atr(df["High"], df["Low"], df["Close"], 14)
    df["ATR_pct"] = df["ATR"] / df["Close"] * 100

    # 거래량
    df["Volume_MA5"] = calc_sma(df["Volume"], 5)
    df["Volume_MA20"] = calc_sma(df["Volume"], 20)
    df["Volume_ratio"] = df["Volume"] / df["Volume_MA5"].replace(0, np.nan)

    # Stochastic
    stoch_k, stoch_d = calc_stochastic(df["High"], df["Low"], df["Close"])
    df["Stoch_K"] = stoch_k
    df["Stoch_D"] = stoch_d

    # 추세 (현재가 vs MA200)
    df["Trend_pct"] = (df["Close"] - df["MA200"]) / df["MA200"].replace(0, np.nan) * 100

    # 모멘텀 (과거 20일 대비 변화율)
    df["Momentum_20d"] = df["Close"].pct_change(20) * 100

    # 정배열 여부 (MA5 > MA20 > MA60)
    df["Aligned_bull"] = (
        (df["MA5"] > df["MA20"]) & (df["MA20"] > df["MA60"])
    ).astype(int)

    # 골든 크로스 발생 (당일)
    df["MACD_golden"] = (
        (df["MACD"] > df["MACD_signal"])
        & (df["MACD"].shift(1) <= df["MACD_signal"].shift(1))
    ).astype(int)

    log.debug(f"calc_indicators: {len(df)} rows, {len(df.columns)} cols")
    return df


# ---------- 단일 행 신호 평가용 헬퍼 ----------
def is_bb_near_lower(row: pd.Series, proximity: float = 1.05) -> bool:
    """현재가가 볼린저 하단 근처(proximity 배수 이내)인지"""
    if pd.isna(row.get("BB_lower")) or row["BB_lower"] == 0:
        return False
    return row["Close"] <= row["BB_lower"] * proximity


def is_volume_surge(row: pd.Series, threshold: float = 1.5) -> bool:
    """거래량이 5일 평균의 threshold 배 이상인지"""
    ratio = row.get("Volume_ratio", 0)
    return bool(ratio >= threshold)


def is_rsi_in_range(row: pd.Series, low: float, high: float) -> bool:
    rsi = row.get("RSI", 50)
    return low <= rsi <= high


if __name__ == "__main__":
    # 더미 데이터로 테스트
    n = 250
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(n))
    df = pd.DataFrame(
        {
            "Open": close + np.random.randn(n) * 0.3,
            "High": close + np.abs(np.random.randn(n)) * 0.5,
            "Low": close - np.abs(np.random.randn(n)) * 0.5,
            "Close": close,
            "Volume": np.random.randint(1000, 5000, n),
        },
        index=idx,
    )
    out = calc_indicators(df)
    print(f"✅ indicators 테스트")
    print(f"  입력 컬럼: {len(df.columns)}, 출력 컬럼: {len(out.columns)}")
    print(f"  마지막 행 RSI: {out['RSI'].iloc[-1]:.2f}")
    print(f"  마지막 행 ATR: {out['ATR'].iloc[-1]:.4f}")
    print(f"  정배열: {out['Aligned_bull'].iloc[-1]}")
