"""
매수 신호 평가기
- 룰북 + 일봉 데이터 + 시장 컨텍스트 + 뉴스 감성 → 신호 점수
- 점수 ≥ rulebook.signal_threshold 이면 매수 신호
- 인버스(short) 종목은 신호 로직 반전
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from engine.core.logger import get_logger
from engine.core.indicators import is_bb_near_lower, is_volume_surge
from engine.strategies.rulebook import Rulebook

log = get_logger("evaluator")


@dataclass
class SignalResult:
    should_buy: bool
    score: float                    # 가중치 적용 합산 점수
    raw_score: float                # 시장 보정 전 점수
    threshold: float
    reasons: list                   # 점수 발생 이유 (디버깅)
    market_adjustment: float        # 시장 보정 배수
    components: dict                # 각 컴포넌트 점수 (가중치 적용 후)


def evaluate_signal(
    rb: Rulebook,
    df: pd.DataFrame,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    news_sentiment: float = 0.0,  # -1 ~ +1
) -> SignalResult:
    """
    가장 최근 봉(df의 마지막 행)에 대해 신호 평가.

    Args:
        rb: 종목 룰북
        df: calc_indicators 적용된 OHLCV+지표 DataFrame
        market_score: 0~100 (시장 컨텍스트 점수)
        sector_score: 0~100 (섹터 강도)
        vix_level: 변동성 지수
        news_sentiment: -1.0 ~ +1.0

    Returns:
        SignalResult
    """
    if df is None or len(df) < 60:
        return SignalResult(False, 0.0, 0.0, rb.signal_threshold, ["insufficient_data"], 1.0, {})

    row = df.iloc[-1]
    is_short = (rb.direction == "short")

    reasons: list = []
    components: dict = {}

    # ---------- 1) 정배열 ----------
    aligned = bool(row.get("Aligned_bull", 0))
    if is_short:
        # 인버스: 역배열(MA5 < MA20 < MA60)이 유리
        ma5 = row.get("MA5")
        ma20 = row.get("MA20")
        ma60 = row.get("MA60")
        aligned = (
            ma5 is not None and ma20 is not None and ma60 is not None
            and ma5 < ma20 < ma60
        )
    s_align = rb.weight_ma_align * (1.0 if aligned else 0.0)
    components["ma_align"] = s_align
    if s_align > 0:
        reasons.append(f"정배열(+{s_align:.2f})")

    # ---------- 2) MACD 골든크로스 ----------
    if is_short:
        # 인버스: 데드크로스(MACD가 시그널 아래로 하향)가 유리
        macd_event = (
            row.get("MACD") is not None
            and row.get("MACD_signal") is not None
            and row["MACD"] < row["MACD_signal"]
            and df["MACD"].iloc[-2] >= df["MACD_signal"].iloc[-2]
        )
    else:
        macd_event = bool(row.get("MACD_golden", 0))
    s_macd = rb.weight_macd_golden * (1.0 if macd_event else 0.0)
    components["macd"] = s_macd
    if s_macd > 0:
        reasons.append(f"MACD크로스(+{s_macd:.2f})")

    # ---------- 3) RSI 적정 구간 ----------
    rsi = row.get("RSI", 50)
    if is_short:
        # 인버스: 시장이 과매수일 때 매수 (RSI 65~80)
        rsi_low, rsi_high = max(rb.rsi_low + 30, 60), min(rb.rsi_high + 10, 85)
    else:
        rsi_low, rsi_high = rb.rsi_low, rb.rsi_high
    rsi_ok = rsi_low <= rsi <= rsi_high
    s_rsi = rb.weight_rsi_zone * (1.0 if rsi_ok else 0.0)
    components["rsi"] = s_rsi
    if s_rsi > 0:
        reasons.append(f"RSI {rsi:.0f}∈[{rsi_low:.0f},{rsi_high:.0f}](+{s_rsi:.2f})")

    # ---------- 4) 볼린저 ----------
    if is_short:
        # 인버스: 상단 근접 시 유리
        bb_upper = row.get("BB_upper")
        bb_ok = (
            bb_upper is not None and bb_upper > 0
            and row["Close"] >= bb_upper / rb.bb_proximity
        )
    else:
        bb_ok = is_bb_near_lower(row, proximity=rb.bb_proximity)
    s_bb = rb.weight_bb_near_lower * (1.0 if bb_ok else 0.0)
    components["bb"] = s_bb
    if s_bb > 0:
        reasons.append(f"BB근접(+{s_bb:.2f})")

    # ---------- 5) 거래량 급증 ----------
    vol_ok = is_volume_surge(row, threshold=rb.volume_surge_ratio)
    s_vol = rb.weight_volume_surge * (1.0 if vol_ok else 0.0)
    components["volume"] = s_vol
    if s_vol > 0:
        reasons.append(f"거래량×{row.get('Volume_ratio', 0):.1f}(+{s_vol:.2f})")

    # ---------- 6) 뉴스 감성 ----------
    eff_sent = -news_sentiment if is_short else news_sentiment
    s_news = rb.weight_news_sentiment * max(0.0, eff_sent)
    components["news"] = s_news
    if s_news > 0:
        reasons.append(f"뉴스({eff_sent:+.2f})(+{s_news:.2f})")

    # ---------- 합산 ----------
    raw_score = sum(components.values())

    # ---------- 시장 연관성 보정 ----------
    # market_score 0~100 → -1~+1로 정규화 (50 기준)
    market_norm = (market_score - 50) / 50.0
    sector_norm = (sector_score - 50) / 50.0
    vix_norm = (18 - vix_level) / 10.0  # VIX 낮을수록 양수

    correlation_adj = (
        market_norm * rb.market_score_weight
        + sector_norm * rb.sector_strength_weight
        + vix_norm * rb.vix_sensitivity
    )
    # 보정 강도 제한 ±30%
    market_adjustment = 1.0 + max(min(correlation_adj * 0.3, 0.3), -0.3)

    final_score = raw_score * market_adjustment

    should_buy = final_score >= rb.signal_threshold

    if market_adjustment != 1.0:
        reasons.append(f"시장보정×{market_adjustment:.2f}")

    return SignalResult(
        should_buy=should_buy,
        score=final_score,
        raw_score=raw_score,
        threshold=rb.signal_threshold,
        reasons=reasons,
        market_adjustment=market_adjustment,
        components=components,
    )


# ---------- 포지션 크기 계산 ----------
def calc_position_size_krw(
    rb: Rulebook,
    signal_score: float,
    position_limit_krw: float,
) -> float:
    """
    한도 내에서 실제 투자할 금액(원화) 계산.

    Args:
        rb: 룰북
        signal_score: 신호 점수 (시장보정 후)
        position_limit_krw: 해당 종목의 한도 금액

    Returns:
        투자 금액 (KRW)
    """
    strategy = rb.position_sizing_strategy

    if strategy == "fixed":
        ratio = rb.base_position_ratio

    elif strategy == "signal_scaled":
        # 신호가 임계값의 몇 배인지 → 곱셈
        ratio_signal = min(signal_score / max(rb.signal_threshold, 0.1), 2.0)
        ratio = rb.base_position_ratio * min(ratio_signal * rb.signal_multiplier, 1.0)

    elif strategy == "kelly_lite":
        # 켈리 단순화: win_rate × avg_return - (1-wr) × |avg_loss|
        # 학습 결과(rb.win_rate, rb.avg_return_pct)가 있어야 의미 있음
        wr = max(min(rb.win_rate / 100.0, 0.95), 0.05)
        avg = max(rb.avg_return_pct / 100.0, 0.001)
        kelly = wr - (1 - wr) / max(avg, 0.01)
        ratio = max(min(kelly * rb.base_position_ratio, 1.0), 0.2)

    else:
        ratio = rb.base_position_ratio

    return position_limit_krw * max(min(ratio, 1.0), 0.0)


if __name__ == "__main__":
    # 간단 테스트
    import numpy as np
    from engine.core.indicators import calc_indicators

    np.random.seed(42)
    n = 250
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5 + 0.05)
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
    df = calc_indicators(df)

    from engine.strategies.rulebook import default_rulebook

    rb_long = default_rulebook("TEST", "korean_etf", "long")
    rb_long.signal_threshold = 1.5
    res = evaluate_signal(rb_long, df, market_score=80, sector_score=90, vix_level=15)
    print("=" * 50)
    print("LONG 종목 신호 평가")
    print("=" * 50)
    print(f"  매수신호: {res.should_buy}")
    print(f"  점수: {res.score:.2f} (raw {res.raw_score:.2f} × market {res.market_adjustment:.2f})")
    print(f"  임계값: {res.threshold}")
    print(f"  컴포넌트: {res.components}")
    print(f"  이유: {res.reasons}")

    rb_short = default_rulebook("INV", "korean_etf", "short")
    rb_short.signal_threshold = 1.5
    res2 = evaluate_signal(rb_short, df, market_score=80, sector_score=90, vix_level=15)
    print("\n" + "=" * 50)
    print("SHORT(인버스) 종목 — 강세장에서 신호 약화 확인")
    print("=" * 50)
    print(f"  매수신호: {res2.should_buy}")
    print(f"  점수: {res2.score:.2f} (raw {res2.raw_score:.2f} × market {res2.market_adjustment:.2f})")

    # 포지션 사이징 테스트
    print("\n" + "=" * 50)
    print("포지션 사이징 테스트 (한도 120,000원)")
    print("=" * 50)
    for strat in ["fixed", "signal_scaled", "kelly_lite"]:
        rb_long.position_sizing_strategy = strat
        rb_long.base_position_ratio = 0.6
        rb_long.signal_multiplier = 1.2
        rb_long.win_rate = 60
        rb_long.avg_return_pct = 2.0
        amt = calc_position_size_krw(rb_long, res.score, 120000)
        print(f"  {strat:15}: {amt:,.0f}원")
