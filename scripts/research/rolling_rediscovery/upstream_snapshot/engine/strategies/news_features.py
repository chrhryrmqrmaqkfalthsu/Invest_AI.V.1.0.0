"""
news_features.py - v6 토픽별 뉴스 감성 -> z-score 정규화 피처

핵심:
  - 토픽별 raw 감성(sent_*)을 "최근 W일 분포 기준 z-score"로 변환
  - z = (sent - rolling_mean) / rolling_std   (그 날짜 '이전' W일만; lookahead 방지)
  - 표본 부족(min_samples 미만)이면 0 (희소 데이터 안정성)
  - z는 ±Z_CLIP 로 clip (이상치 방지)
  - 신뢰도 conf = min(1, cnt / CONF_K)

학습(backtest)과 라이브(per_ticker_news)가 이 모듈을 공유 -> 동일 로직 보장.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from engine.market.ticker_sentiment import AV_TOPICS

# 안정성 파라미터
MIN_SAMPLES = 3      # 윈도우 내 그 토픽 유효표본이 이 수 미만이면 z=0
Z_CLIP = 3.0         # z-score 절대값 상한
CONF_K = 2.0         # 신뢰도 포화 기준 기사수 (cnt>=2 -> conf=1.0)
MIN_STD = 1e-4       # std가 이보다 작으면 0 나눗셈 방지 -> z=0


def _zscore(value: float, hist: List[float]) -> float:
    """value 를 hist(과거 표본) 기준 z-score 로. 표본부족/저분산이면 0."""
    n = len(hist)
    if n < MIN_SAMPLES:
        return 0.0
    mean = sum(hist) / n
    var = sum((x - mean) ** 2 for x in hist) / n
    std = var ** 0.5
    if std < MIN_STD:
        return 0.0
    z = (value - mean) / std
    if z > Z_CLIP:
        z = Z_CLIP
    elif z < -Z_CLIP:
        z = -Z_CLIP
    return z


def _confidence(cnt: float) -> float:
    if cnt <= 0:
        return 0.0
    c = cnt / CONF_K
    return 1.0 if c >= 1.0 else c


def precompute_topic_features(
    sent_csv: Dict[str, dict],
    window: int,
) -> Dict[str, Dict[str, float]]:
    """
    전체 시계열의 토픽별 z-score 피처를 한 번에 계산 (백테스트용, 빠름).

    Args:
        sent_csv: load_csv(ticker) 결과. {date_str: {sent_*, cnt_*, ...}}
        window:   롤링 윈도우 W (news_zscore_window)

    Returns:
        {date_str: {topic: z_norm_weighted}}
        z_norm_weighted = zscore(과거 W일 기준) × confidence(cnt)
        그 날 그 토픽 기사가 없으면(cnt=0) 해당 토픽 키 생략(=0 취급).
    """
    dates = sorted(sent_csv.keys())
    out: Dict[str, Dict[str, float]] = {}

    # 토픽별 (date, sent, cnt) 시퀀스 미리 구성
    series = {t: [] for t in AV_TOPICS}  # [(date, sent, cnt), ...] cnt>0만
    for d in dates:
        row = sent_csv[d]
        for t in AV_TOPICS:
            cnt = row.get("cnt_%s" % t, 0)
            if cnt and cnt > 0:
                series[t].append((d, row.get("sent_%s" % t, 0.0), cnt))

    # 토픽별로 인덱스 순회하며 '과거 W개' 표본으로 z 계산
    for t in AV_TOPICS:
        seq = series[t]
        vals = [s for (_, s, _) in seq]
        for i, (d, sent, cnt) in enumerate(seq):
            lo = max(0, i - window)
            hist = vals[lo:i]              # i 이전만 (오늘 제외 -> lookahead 방지)
            z = _zscore(sent, hist)
            conf = _confidence(cnt)
            feat = z * conf
            if feat != 0.0:
                out.setdefault(d, {})[t] = round(feat, 6)
    return out


def compute_topic_features_asof(
    sent_csv: Dict[str, dict],
    as_of_date: str,
    window: int,
) -> Dict[str, float]:
    """
    단일 시점(as_of_date) 토픽 피처 (라이브용).
    as_of_date '이전' W일 표본으로 그 날 감성을 z-score.
    그 날 데이터가 없으면 {} 반환.
    """
    dates = sorted(sent_csv.keys())
    if as_of_date not in sent_csv:
        return {}
    result: Dict[str, float] = {}
    for t in AV_TOPICS:
        cnt = sent_csv[as_of_date].get("cnt_%s" % t, 0)
        if not cnt or cnt <= 0:
            continue
        # as_of 이전 그 토픽 표본 W개
        hist = []
        for d in dates:
            if d >= as_of_date:
                break
            c = sent_csv[d].get("cnt_%s" % t, 0)
            if c and c > 0:
                hist.append(sent_csv[d].get("sent_%s" % t, 0.0))
        hist = hist[-window:]
        z = _zscore(sent_csv[as_of_date].get("sent_%s" % t, 0.0), hist)
        conf = _confidence(cnt)
        feat = z * conf
        if feat != 0.0:
            result[t] = round(feat, 6)
    return result
