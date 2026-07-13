"""
매수 신호 평가기
- legacy schema v1: 기존 가중 합산 score >= threshold 진입
- strict entry schema v2+: 5개 연속 feature interval의 strict-AND로만 진입
- 뉴스·시장·이벤트 합산 점수는 quality score로 보존하며 사이징·정렬·진단에만 사용
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from typing import Any, Mapping, Optional

import pandas as pd

from engine.core.logger import get_logger
from engine.core.indicators import is_bb_near_lower, is_volume_surge
from engine.strategies.rulebook import (
    ENTRY_INTERVAL_SPECS,
    STRICT_ENTRY_INTERVAL_SCHEMA_VERSION,
    Rulebook,
    validate_entry_feature_domains,
    validate_entry_intervals,
)

log = get_logger("evaluator")


@dataclass
class EntryIntervalResult:
    passed: bool
    reasons: list[str]
    checks: dict[str, dict[str, Any]]
    features: dict[str, float]


@dataclass
class SignalResult:
    should_buy: bool
    score: float                    # quality score (시장보정 후, 진단/사이징용)
    raw_score: float                # 시장 보정 전 quality score
    threshold: float                # quality score 진단 기준
    reasons: list                   # 신호/품질 발생 이유 (디버깅)
    market_adjustment: float        # 시장 보정 배수
    components: dict                # 각 quality 컴포넌트 점수
    quality_score: float = 0.0
    strict_entry: bool = False
    entry_features: dict = field(default_factory=dict)
    interval_checks: dict = field(default_factory=dict)


def _finite_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(float(value))


def extract_entry_features(df: pd.DataFrame) -> dict[str, float]:
    """가장 최근 봉에서 strict entry용 5개 연속 feature를 추출한다.

    반환 feature:
      ma_trend      = 0.5 * [(MA5/MA20-1) + (MA20/MA60-1)] * 100
      macd_hist     = MACD_hist / Close * 100
      rsi           = RSI
      bb_position   = (Close-BB_lower) / (BB_upper-BB_lower)
      volume_ratio  = Volume_ratio

    값은 clip하지 않는다. 계산 불가·누락·비유한 값은 NaN으로 남겨
    evaluate_entry_intervals()가 fail-closed 처리한다.
    """
    names = tuple(ENTRY_INTERVAL_SPECS)
    if df is None or len(df) == 0:
        return {name: float("nan") for name in names}

    row = df.iloc[-1]

    def value(name: str) -> float:
        raw = row.get(name)
        if not _finite_number(raw):
            return float("nan")
        return float(raw)

    ma5 = value("MA5")
    ma20 = value("MA20")
    ma60 = value("MA60")
    close = value("Close")
    macd_hist = value("MACD_hist")
    rsi = value("RSI")
    bb_lower = value("BB_lower")
    bb_upper = value("BB_upper")
    volume_ratio = value("Volume_ratio")

    if all(isfinite(v) for v in (ma5, ma20, ma60)) and ma20 != 0.0 and ma60 != 0.0:
        ma_trend = 0.5 * (((ma5 / ma20) - 1.0) + ((ma20 / ma60) - 1.0)) * 100.0
    else:
        ma_trend = float("nan")

    if isfinite(macd_hist) and isfinite(close) and close != 0.0:
        normalized_macd = macd_hist / close * 100.0
    else:
        normalized_macd = float("nan")

    bb_width = bb_upper - bb_lower
    if all(isfinite(v) for v in (close, bb_lower, bb_upper)) and bb_width != 0.0:
        bb_position = (close - bb_lower) / bb_width
    else:
        bb_position = float("nan")

    return {
        "ma_trend": float(ma_trend),
        "macd_hist": float(normalized_macd),
        "rsi": float(rsi),
        "bb_position": float(bb_position),
        "volume_ratio": float(volume_ratio),
    }


def evaluate_entry_intervals(
    rb: Rulebook,
    feature_values: Mapping[str, Any],
) -> EntryIntervalResult:
    """Schema v2 strict interval을 fail-closed로 평가한다.

    검사 순서:
      raw feature 존재 -> NaN/Inf -> 고정 mathematical domain
      -> 학습 q01~q99 domain(OOD) -> learned low/high interval

    어떤 단계에서도 값을 clip하지 않는다.
    """
    checks: dict[str, dict[str, Any]] = {}
    normalized_features: dict[str, float] = {}

    try:
        schema_version = int(getattr(rb, "entry_interval_schema_version", 1))
    except (TypeError, ValueError):
        return EntryIntervalResult(
            passed=False,
            reasons=["strict_entry: invalid schema version"],
            checks=checks,
            features=normalized_features,
        )

    if schema_version < STRICT_ENTRY_INTERVAL_SCHEMA_VERSION:
        return EntryIntervalResult(
            passed=False,
            reasons=["strict_entry: legacy schema"],
            checks=checks,
            features=normalized_features,
        )

    if not isinstance(feature_values, Mapping):
        return EntryIntervalResult(
            passed=False,
            reasons=["strict_entry: feature payload missing"],
            checks=checks,
            features=normalized_features,
        )

    domains = getattr(rb, "entry_feature_domains", None)
    if not isinstance(domains, Mapping):
        return EntryIntervalResult(
            passed=False,
            reasons=["strict_entry: entry_feature_domains missing"],
            checks=checks,
            features=normalized_features,
        )

    try:
        schema_errors = validate_entry_intervals(rb) + validate_entry_feature_domains(rb)
    except Exception as exc:
        return EntryIntervalResult(
            passed=False,
            reasons=[f"strict_entry: schema validation error ({type(exc).__name__})"],
            checks=checks,
            features=normalized_features,
        )
    if schema_errors:
        return EntryIntervalResult(
            passed=False,
            reasons=[f"strict_entry: schema invalid ({'; '.join(schema_errors)})"],
            checks=checks,
            features=normalized_features,
        )

    for feature_name, spec in ENTRY_INTERVAL_SPECS.items():
        raw_value = feature_values.get(feature_name)
        check: dict[str, Any] = {
            "raw_value": raw_value,
            "finite": False,
            "hard_domain_pass": False,
            "empirical_domain_pass": False,
            "interval_pass": False,
        }
        checks[feature_name] = check

        if not _finite_number(raw_value):
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} missing or NaN/Inf"],
                checks=checks,
                features=normalized_features,
            )

        value = float(raw_value)
        normalized_features[feature_name] = value
        check["raw_value"] = value
        check["finite"] = True

        hard_min = spec.get("hard_min")
        hard_max = spec.get("hard_max")
        if hard_min is not None and value < float(hard_min):
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} below hard domain"],
                checks=checks,
                features=normalized_features,
            )
        if hard_max is not None and value > float(hard_max):
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} above hard domain"],
                checks=checks,
                features=normalized_features,
            )
        check["hard_domain_pass"] = True

        metadata = domains.get(feature_name)
        if not isinstance(metadata, Mapping):
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} empirical domain missing"],
                checks=checks,
                features=normalized_features,
            )
        q01 = metadata.get("q01")
        q99 = metadata.get("q99")
        if not _finite_number(q01) or not _finite_number(q99):
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} empirical domain invalid"],
                checks=checks,
                features=normalized_features,
            )

        q01_value = float(q01)
        q99_value = float(q99)
        check["q01"] = q01_value
        check["q99"] = q99_value
        if value < q01_value or value > q99_value:
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} outside learned q01~q99 domain"],
                checks=checks,
                features=normalized_features,
            )
        check["empirical_domain_pass"] = True

        low = getattr(rb, spec["low_field"], None)
        high = getattr(rb, spec["high_field"], None)
        if not _finite_number(low) or not _finite_number(high):
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} interval invalid"],
                checks=checks,
                features=normalized_features,
            )

        low_value = float(low)
        high_value = float(high)
        check["low"] = low_value
        check["high"] = high_value
        if value < low_value or value > high_value:
            return EntryIntervalResult(
                passed=False,
                reasons=[f"strict_entry: {feature_name} outside learned interval"],
                checks=checks,
                features=normalized_features,
            )
        check["interval_pass"] = True

    return EntryIntervalResult(
        passed=True,
        reasons=["strict_entry: all 5 intervals passed"],
        checks=checks,
        features=normalized_features,
    )


def evaluate_signal(
    rb: Rulebook,
    df: pd.DataFrame,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    news_sentiment: float = 0.0,
    event_flags: dict = None,
    topic_features: dict = None,
) -> SignalResult:
    """가장 최근 봉에 대해 진입 판정과 quality score를 계산한다."""
    if df is None or len(df) < 60:
        return SignalResult(
            should_buy=False,
            score=0.0,
            raw_score=0.0,
            threshold=rb.signal_threshold,
            reasons=["insufficient_data"],
            market_adjustment=1.0,
            components={},
            quality_score=0.0,
            strict_entry=False,
            entry_features={},
            interval_checks={},
        )

    row = df.iloc[-1]
    is_short = rb.direction == "short"

    try:
        schema_version = int(getattr(rb, "entry_interval_schema_version", 1))
        strict_entry = schema_version >= STRICT_ENTRY_INTERVAL_SCHEMA_VERSION
        schema_version_invalid = False
    except (TypeError, ValueError):
        strict_entry = True
        schema_version_invalid = True

    entry_features = extract_entry_features(df)
    if schema_version_invalid:
        interval_result = EntryIntervalResult(
            passed=False,
            reasons=["strict_entry: invalid schema version"],
            checks={},
            features=entry_features,
        )
    elif strict_entry:
        interval_result = evaluate_entry_intervals(rb, entry_features)
    else:
        interval_result = EntryIntervalResult(
            passed=False,
            reasons=["strict_entry: legacy schema"],
            checks={},
            features=entry_features,
        )

    reasons: list[str] = []
    components: dict[str, float] = {}

    # ---------- 1) 정배열: quality component only ----------
    aligned = bool(row.get("Aligned_bull", 0))
    if is_short:
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

    # ---------- 2) MACD event: quality component only ----------
    if is_short:
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

    # ---------- 3) RSI legacy zone: quality component only ----------
    rsi = row.get("RSI", 50)
    if is_short:
        rsi_low, rsi_high = max(rb.rsi_low + 30, 60), min(rb.rsi_high + 10, 85)
    else:
        rsi_low, rsi_high = rb.rsi_low, rb.rsi_high
    rsi_ok = rsi_low <= rsi <= rsi_high
    s_rsi = rb.weight_rsi_zone * (1.0 if rsi_ok else 0.0)
    components["rsi"] = s_rsi
    if s_rsi > 0:
        reasons.append(f"RSI {rsi:.0f}∈[{rsi_low:.0f},{rsi_high:.0f}](+{s_rsi:.2f})")

    # ---------- 4) Bollinger legacy gate: quality component only ----------
    if is_short:
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

    # ---------- 5) 거래량 legacy gate: quality component only ----------
    vol_ok = is_volume_surge(row, threshold=rb.volume_surge_ratio)
    s_vol = rb.weight_volume_surge * (1.0 if vol_ok else 0.0)
    components["volume"] = s_vol
    if s_vol > 0:
        reasons.append(f"거래량×{row.get('Volume_ratio', 0):.1f}(+{s_vol:.2f})")

    # ---------- 6) 뉴스 감성: quality component only ----------
    eff_sent = -news_sentiment if is_short else news_sentiment
    s_news = rb.weight_news_sentiment * eff_sent
    if not getattr(rb, "use_news_global", True):
        s_news = 0.0
    components["news"] = s_news
    if abs(s_news) > 0.01:
        reasons.append(f"전체톤({eff_sent:+.2f})({s_news:+.2f})")

    topics = [
        "blockchain", "earnings", "ipo", "mergers_and_acquisitions",
        "financial_markets", "economy_fiscal", "economy_monetary",
        "economy_macro", "energy_transportation", "finance",
        "life_sciences", "manufacturing", "real_estate",
        "retail_wholesale", "technology",
    ]
    topic_news = 0.0
    if topic_features:
        for topic in topics:
            feature = topic_features.get(topic, 0.0)
            if feature == 0.0:
                continue
            weight = getattr(rb, "weight_news_" + topic, 0.0)
            topic_score = weight * feature
            if is_short:
                topic_score = -topic_score
            topic_news += topic_score
        cap = getattr(rb, "news_block_cap", 4.0)
        if topic_news > cap:
            topic_news = cap
        elif topic_news < -cap:
            topic_news = -cap
    components["news_topics"] = topic_news
    if abs(topic_news) > 0.01:
        reasons.append(f"토픽뉴스({topic_news:+.2f})")

    raw_score = sum(components.values())

    # ---------- 이벤트: quality component only ----------
    event_adj = 0.0
    if event_flags and getattr(rb, "use_event_block", True):
        event_adj += event_flags.get("has_war", 0) * rb.event_response_war
        event_adj += event_flags.get("has_rate_hike", 0) * rb.event_response_rate_hike
        event_adj += event_flags.get("has_rate_cut", 0) * rb.event_response_rate_cut
        event_adj += event_flags.get("has_geopolitical", 0) * rb.event_response_geopolitical
        event_adj += event_flags.get("has_tariff", 0) * rb.event_response_tariff
        event_adj += event_flags.get("has_export_ban", 0) * rb.event_response_export_ban
        event_adj += event_flags.get("has_earnings_shock", 0) * rb.event_response_earnings_shock
        event_adj += event_flags.get("has_oil_surge", 0) * rb.event_response_oil_surge
        event_adj += event_flags.get("has_banking_crisis", 0) * rb.event_response_banking_crisis
        event_adj += event_flags.get("has_inflation", 0) * rb.event_response_inflation
        event_adj += event_flags.get("has_fed_statement", 0) * rb.event_response_fed_statement
        event_adj *= rb.event_strength_multiplier

    components["events"] = event_adj
    raw_score += event_adj
    if abs(event_adj) > 0.1:
        reasons.append(f"이벤트반응({event_adj:+.2f})")

    if rb.crash_buy_enabled and market_score <= rb.crash_threshold_score:
        crash_bonus = 2.0
        raw_score += crash_bonus
        reasons.append(f"폭락매수+{crash_bonus:.1f}(score={market_score:.0f})")

    # ---------- 시장 보정: quality score only ----------
    market_norm = (market_score - 50) / 50.0
    sector_norm = (sector_score - 50) / 50.0
    vix_norm = (18 - vix_level) / 10.0

    correlation_adj = (
        market_norm * rb.market_score_weight
        + sector_norm * rb.sector_strength_weight
        + vix_norm * rb.vix_sensitivity
    )
    strength = max(0.0, min(1.0, rb.market_adjustment_strength))
    market_adjustment = 1.0 + max(min(correlation_adj * strength, strength), -strength)
    if not getattr(rb, "use_market_entry_adjustment", True):
        market_adjustment = 1.0

    quality_score = raw_score * market_adjustment

    if strict_entry:
        should_buy = interval_result.passed
        reasons = interval_result.reasons + reasons
    else:
        should_buy = quality_score >= rb.signal_threshold

    if market_adjustment != 1.0:
        reasons.append(f"시장보정×{market_adjustment:.2f}")

    return SignalResult(
        should_buy=should_buy,
        score=quality_score,
        raw_score=raw_score,
        threshold=rb.signal_threshold,
        reasons=reasons,
        market_adjustment=market_adjustment,
        components=components,
        quality_score=quality_score,
        strict_entry=strict_entry,
        entry_features=interval_result.features,
        interval_checks=interval_result.checks,
    )


def get_dynamic_exit_params(rb, market_score: float = 50.0, vix_level: float = 18.0) -> tuple:
    """시장 상태별 동적 손절익절 ATR을 반환한다."""
    if market_score < 40:
        stop_loss = rb.stop_loss_atr_bear
    else:
        stop_loss = rb.stop_loss_atr

    if market_score >= 70:
        take_profit = rb.take_profit_atr_bull
    else:
        take_profit = rb.take_profit_atr

    if vix_level > 25:
        trailing = rb.trailing_atr_volatile
    else:
        trailing = rb.trailing_atr

    return float(stop_loss), float(take_profit), float(trailing)


def calc_position_size_krw(
    rb: Rulebook,
    signal_score: float,
    position_limit_krw: float,
) -> float:
    """Quality score를 이용해 한도 내 실제 투자 금액을 계산한다.

    Schema v2에서도 호출자는 ``SignalResult.score`` 또는
    ``SignalResult.quality_score``를 전달한다. Strict interval 통과 여부는
    포지션 크기 계산 전에 evaluate_signal().should_buy로 이미 결정된다.
    """
    strategy = rb.position_sizing_strategy

    if strategy == "fixed":
        ratio = rb.base_position_ratio

    elif strategy == "signal_scaled":
        ratio_signal = min(signal_score / max(rb.signal_threshold, 0.1), 2.0)
        ratio = rb.base_position_ratio * min(ratio_signal * rb.signal_multiplier, 1.0)

    elif strategy == "kelly_lite":
        win_rate = max(min(rb.win_rate / 100.0, 0.95), 0.05)
        average_return = max(rb.avg_return_pct / 100.0, 0.001)
        kelly = win_rate - (1 - win_rate) / max(average_return, 0.01)
        ratio = max(min(kelly * rb.base_position_ratio, 1.0), 0.2)

    else:
        ratio = rb.base_position_ratio

    return position_limit_krw * max(min(ratio, 1.0), 0.0)


if __name__ == "__main__":
    import numpy as np
    from engine.core.indicators import calc_indicators
    from engine.strategies.rulebook import default_rulebook

    np.random.seed(42)
    count = 250
    index = pd.date_range("2024-01-01", periods=count, freq="D")
    close = 100 + np.cumsum(np.random.randn(count) * 0.5 + 0.05)
    frame = pd.DataFrame(
        {
            "Open": close + np.random.randn(count) * 0.3,
            "High": close + np.abs(np.random.randn(count)) * 0.5,
            "Low": close - np.abs(np.random.randn(count)) * 0.5,
            "Close": close,
            "Volume": np.random.randint(1000, 5000, count),
        },
        index=index,
    )
    frame = calc_indicators(frame)

    legacy_rulebook = default_rulebook("TEST", "korean_etf", "long")
    legacy_rulebook.signal_threshold = 1.5
    result = evaluate_signal(
        legacy_rulebook,
        frame,
        market_score=80,
        sector_score=90,
        vix_level=15,
    )
    print("=" * 50)
    print("LEGACY LONG 종목 신호 평가")
    print("=" * 50)
    print(f"  매수신호: {result.should_buy}")
    print(f"  quality score: {result.quality_score:.2f}")
    print(f"  임계값: {result.threshold}")
