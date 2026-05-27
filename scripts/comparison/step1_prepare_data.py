"""
Step 1: 공통 데이터 준비
=========================
- 봇 어댑터로 069500 5년치 OHLCV 받기
- Colab(12파라미터) + 봇(28파라미터) 둘 다 사용할 지표 모두 계산
- 70/30 시점 분할 → train_idx, val_idx
- pickle 캐시
"""
import sys
import pickle
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np
import pandas as pd
import ta  # Colab과 동일한 지표 라이브러리

from engine.adapters.factory import get_adapter

TICKER = "069500"
YEARS = 5
HOLDING_DAYS = 20  # Colab 기본값
CACHE_PATH = Path("data/_system/comparison_069500.pkl")


def calc_indicators_colab_style(df: pd.DataFrame) -> pd.DataFrame:
    """Colab 코드의 calc_indicators() 그대로 — 12파라미터 학습기용"""
    df = df.copy()
    df['MA20']  = ta.trend.sma_indicator(df['Close'], 20)
    df['MA60']  = ta.trend.sma_indicator(df['Close'], 60)
    df['MA200'] = ta.trend.sma_indicator(df['Close'], 200)
    df['RSI']   = ta.momentum.rsi(df['Close'], 14)
    macd = ta.trend.MACD(df['Close'])
    df['MACD'] = macd.macd()
    df['MACD_signal'] = macd.macd_signal()
    bb = ta.volatility.BollingerBands(df['Close'])
    df['BB_upper']  = bb.bollinger_hband()
    df['BB_middle'] = bb.bollinger_mavg()
    df['BB_lower']  = bb.bollinger_lband()
    df['ATR'] = ta.volatility.average_true_range(df['High'], df['Low'], df['Close'], 14)
    df['Vol_MA5'] = df['Volume'].rolling(5).mean()
    return df


def main():
    print("=" * 70)
    print(f"Step 1: 공통 데이터 준비 ({TICKER}, {YEARS}년)")
    print("=" * 70)

    # 1) 봇 어댑터로 OHLCV
    print("\n[1/4] 봇 어댑터로 OHLCV 로드...")
    adapter = get_adapter(TICKER)
    df_bot = adapter.load_history(years=YEARS)
    print(f"  봇 어댑터: {len(df_bot)}행 ({df_bot.index[0].date()} ~ {df_bot.index[-1].date()})")
    print(f"  컬럼: {list(df_bot.columns)[:10]}... 총 {len(df_bot.columns)}개")

    # 2) Colab 스타일 지표 추가 (있는 컬럼은 그대로 유지)
    print("\n[2/4] Colab 스타일 지표 계산 (MA/RSI/MACD/BB/ATR/Vol_MA5)...")
    df = calc_indicators_colab_style(df_bot)
    print(f"  추가 후 컬럼: {len(df.columns)}개")

    # NaN 제거 (지표 워밍업 기간)
    df_clean = df.dropna(subset=['MA200', 'ATR', 'Vol_MA5'])
    print(f"  NaN 제거 후: {len(df_clean)}행 ({df_clean.index[0].date()} ~ {df_clean.index[-1].date()})")

    # 3) 70/30 분할
    print("\n[3/4] 70/30 분할...")
    split_idx = int(len(df_clean) * 0.70)
    split_date = df_clean.index[split_idx]
    print(f"  분할 지점: {split_idx}번째 행 = {split_date.date()}")
    print(f"  Train: {df_clean.index[0].date()} ~ {df_clean.index[split_idx-1].date()} ({split_idx}행)")
    print(f"  Val:   {df_clean.index[split_idx].date()} ~ {df_clean.index[-1].date()} ({len(df_clean)-split_idx}행)")

    # 인덱스 리스트 (백테스트가 진입 시점 i 를 받는 구조)
    # holding_days 만큼은 끝에서 제외 (청산 못 함)
    train_indices = list(range(0, split_idx - HOLDING_DAYS))
    val_indices   = list(range(split_idx, len(df_clean) - HOLDING_DAYS))
    print(f"  Train 진입 가능: {len(train_indices)} 포인트")
    print(f"  Val   진입 가능: {len(val_indices)} 포인트")

    # 4) 저장
    print("\n[4/4] 캐시 저장...")
    payload = {
        "ticker": TICKER,
        "years": YEARS,
        "holding_days": HOLDING_DAYS,
        "df": df_clean,                  # 전체 DataFrame (인덱스 포함)
        "train_indices": train_indices,
        "val_indices": val_indices,
        "split_idx": split_idx,
        "split_date": str(split_date.date()),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(payload, f)
    print(f"  ✅ 저장: {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1024:.1f} KB)")

    print("\n" + "=" * 70)
    print("✅ Step 1 완료")
    print(f"   다음: python scripts/comparison/step2_bot_learner.py")
    print("=" * 70)


if __name__ == "__main__":
    main()
