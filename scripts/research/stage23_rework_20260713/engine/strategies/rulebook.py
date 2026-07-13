"""
룰북 (Rulebook) 데이터 클래스
- GA가 학습하는 모든 파라미터를 담음
- v4: 기본 16개 + 포지션사이징 + 추가매수 + 시장연관성 + 개별주 전용
- strict entry interval schema v2: 5개 연속 feature의 low/high + empirical domain 검증
"""
from dataclasses import dataclass, field, asdict
from math import isfinite
from numbers import Real
from typing import Any, Mapping, Optional


STRICT_ENTRY_INTERVAL_SCHEMA_VERSION = 2
ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO = 0.20
ENTRY_INTERVAL_NEAR_FULL_RATIO = 0.80
ENTRY_INTERVAL_MIN_FEATURE_SUPPORT = 25
ENTRY_INTERVAL_MIN_JOINT_SUPPORT = 12
ENTRY_INTERVAL_MAX_NEAR_FULL_COUNT = 1


ENTRY_INTERVAL_SPECS: dict[str, dict[str, Any]] = {
    "ma_trend": {
        "low_field": "ma_trend_low",
        "high_field": "ma_trend_high",
        "hard_min": -100.0,
        "hard_max": None,
        "min_width_iqr_ratio": ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO,
        "near_full_ratio": ENTRY_INTERVAL_NEAR_FULL_RATIO,
    },
    "macd_hist": {
        "low_field": "macd_hist_low",
        "high_field": "macd_hist_high",
        "hard_min": None,
        "hard_max": None,
        "min_width_iqr_ratio": ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO,
        "near_full_ratio": ENTRY_INTERVAL_NEAR_FULL_RATIO,
    },
    "rsi": {
        "low_field": "rsi_low",
        "high_field": "rsi_high",
        "hard_min": 0.0,
        "hard_max": 100.0,
        "min_width_iqr_ratio": ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO,
        "near_full_ratio": ENTRY_INTERVAL_NEAR_FULL_RATIO,
    },
    "bb_position": {
        "low_field": "bb_position_low",
        "high_field": "bb_position_high",
        "hard_min": None,
        "hard_max": None,
        "min_width_iqr_ratio": ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO,
        "near_full_ratio": ENTRY_INTERVAL_NEAR_FULL_RATIO,
    },
    "volume_ratio": {
        "low_field": "volume_ratio_low",
        "high_field": "volume_ratio_high",
        "hard_min": 0.0,
        "hard_max": None,
        "min_width_iqr_ratio": ENTRY_INTERVAL_MIN_WIDTH_IQR_RATIO,
        "near_full_ratio": ENTRY_INTERVAL_NEAR_FULL_RATIO,
    },
}


ENTRY_DOMAIN_REQUIRED_KEYS = {
    "train_min",
    "train_max",
    "q01",
    "q99",
    "iqr",
    "sample_count",
    "interval_support_count",
}


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(float(value))


def _as_float(value: Any) -> float:
    return float(value)


def validate_entry_feature_domains(
    rulebook: "Rulebook",
    *,
    min_feature_support: int = ENTRY_INTERVAL_MIN_FEATURE_SUPPORT,
    min_joint_support: int = ENTRY_INTERVAL_MIN_JOINT_SUPPORT,
) -> list[str]:
    """Strict entry empirical-domain metadata를 검증한다.

    반환값이 빈 리스트면 유효하다. 이 함수는 입력을 변경하지 않는 순수 함수다.
    """
    errors: list[str] = []
    domains = rulebook.entry_feature_domains

    if not isinstance(domains, Mapping):
        return ["entry_feature_domains must be a mapping"]

    for feature_name, spec in ENTRY_INTERVAL_SPECS.items():
        metadata = domains.get(feature_name)
        if not isinstance(metadata, Mapping):
            errors.append(f"{feature_name}: domain metadata missing")
            continue

        missing = sorted(ENTRY_DOMAIN_REQUIRED_KEYS.difference(metadata.keys()))
        if missing:
            errors.append(f"{feature_name}: missing domain keys {missing}")
            continue

        numeric_keys = ("train_min", "train_max", "q01", "q99", "iqr")
        invalid_numeric = [key for key in numeric_keys if not _is_finite_number(metadata.get(key))]
        if invalid_numeric:
            errors.append(f"{feature_name}: non-finite domain values {invalid_numeric}")
            continue

        train_min = _as_float(metadata["train_min"])
        train_max = _as_float(metadata["train_max"])
        q01 = _as_float(metadata["q01"])
        q99 = _as_float(metadata["q99"])
        iqr = _as_float(metadata["iqr"])

        if train_max < train_min:
            errors.append(f"{feature_name}: train_max < train_min")
        if q99 <= q01:
            errors.append(f"{feature_name}: q99 must be greater than q01")
        if q01 < train_min or q99 > train_max:
            errors.append(f"{feature_name}: q01/q99 outside train_min/train_max")
        if iqr <= 0.0:
            errors.append(f"{feature_name}: iqr must be positive")

        sample_count = metadata.get("sample_count")
        support_count = metadata.get("interval_support_count")
        if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count <= 0:
            errors.append(f"{feature_name}: sample_count must be a positive integer")
        if not isinstance(support_count, int) or isinstance(support_count, bool):
            errors.append(f"{feature_name}: interval_support_count must be an integer")
        elif support_count < min_feature_support:
            errors.append(
                f"{feature_name}: interval_support_count {support_count} < {min_feature_support}"
            )
        elif isinstance(sample_count, int) and support_count > sample_count:
            errors.append(f"{feature_name}: interval_support_count > sample_count")

        low = getattr(rulebook, spec["low_field"])
        high = getattr(rulebook, spec["high_field"])
        if _is_finite_number(low) and _is_finite_number(high):
            low_f = _as_float(low)
            high_f = _as_float(high)
            if high_f < q01 or low_f > q99:
                errors.append(f"{feature_name}: interval does not overlap empirical q01~q99 domain")

    joint_support_count = rulebook.entry_joint_support_count
    if not isinstance(joint_support_count, int) or isinstance(joint_support_count, bool):
        errors.append("entry_joint_support_count must be an integer")
    elif joint_support_count < min_joint_support:
        errors.append(
            f"entry_joint_support_count {joint_support_count} < {min_joint_support}"
        )

    return errors


def validate_entry_intervals(
    rulebook: "Rulebook",
    *,
    max_near_full_intervals: int = ENTRY_INTERVAL_MAX_NEAR_FULL_COUNT,
) -> list[str]:
    """Strict entry low/high interval의 구조·폭·고정 domain을 검증한다.

    반환값이 빈 리스트면 유효하다. empirical support 검증은
    validate_entry_feature_domains()가 담당한다.
    """
    errors: list[str] = []
    near_full_count = 0

    for feature_name, spec in ENTRY_INTERVAL_SPECS.items():
        low = getattr(rulebook, spec["low_field"], None)
        high = getattr(rulebook, spec["high_field"], None)

        if not _is_finite_number(low) or not _is_finite_number(high):
            errors.append(f"{feature_name}: low/high must be finite numbers")
            continue

        low_f = _as_float(low)
        high_f = _as_float(high)
        hard_min = spec["hard_min"]
        hard_max = spec["hard_max"]

        if hard_min is not None and low_f < float(hard_min):
            errors.append(f"{feature_name}: low below hard_min {hard_min}")
        if hard_max is not None and high_f > float(hard_max):
            errors.append(f"{feature_name}: high above hard_max {hard_max}")
        if high_f <= low_f:
            errors.append(f"{feature_name}: high must be greater than low")
            continue

        metadata = rulebook.entry_feature_domains.get(feature_name, {})
        if not isinstance(metadata, Mapping):
            continue
        if not all(_is_finite_number(metadata.get(k)) for k in ("q01", "q99", "iqr")):
            continue

        q01 = _as_float(metadata["q01"])
        q99 = _as_float(metadata["q99"])
        iqr = _as_float(metadata["iqr"])
        empirical_span = q99 - q01
        interval_width = high_f - low_f
        minimum_width = iqr * float(spec["min_width_iqr_ratio"])

        if interval_width < minimum_width:
            errors.append(
                f"{feature_name}: width {interval_width:.12g} < minimum {minimum_width:.12g}"
            )

        if empirical_span > 0.0 and interval_width >= empirical_span * float(spec["near_full_ratio"]):
            near_full_count += 1

    if near_full_count > max_near_full_intervals:
        errors.append(
            f"near-full interval count {near_full_count} > allowed {max_near_full_intervals}"
        )

    return errors


@dataclass
class Rulebook:
    # ===== 메타 =====
    ticker: str = ""
    asset_type: str = ""              # 'korean_etf' 등
    direction: str = "long"           # 'long' | 'short'
    version: str = "v5"
    generated_at: str = ""
    mask_schema_version: int = 1       # 0=legacy hash compatible, 1+=use_xxx mask schema
    use_news_global: bool = True
    use_event_block: bool = True
    use_market_entry_adjustment: bool = True

    # ===== strict entry interval schema =====
    entry_interval_schema_version: int = 1
    ma_trend_low: float = 0.0
    ma_trend_high: float = 0.0
    macd_hist_low: float = 0.0
    macd_hist_high: float = 0.0
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    bb_position_low: float = 0.0
    bb_position_high: float = 0.0
    volume_ratio_low: float = 0.0
    volume_ratio_high: float = 0.0
    entry_feature_domains: dict[str, dict[str, Any]] = field(default_factory=dict)
    entry_joint_support_count: int = 0

    # ===== 신호 가중치 (기본 16개) =====
    weight_ma_align: float = 1.0
    weight_macd_golden: float = 1.0
    weight_rsi_zone: float = 1.0
    weight_bb_near_lower: float = 1.0
    weight_volume_surge: float = 1.0
    weight_news_sentiment: float = 2.0

    # ===== v6: 토픽별 뉴스 감성 가중치 =====
    weight_news_blockchain: float = 0.0
    weight_news_earnings: float = 0.0
    weight_news_ipo: float = 0.0
    weight_news_mergers_and_acquisitions: float = 0.0
    weight_news_financial_markets: float = 0.0
    weight_news_economy_fiscal: float = 0.0
    weight_news_economy_monetary: float = 0.0
    weight_news_economy_macro: float = 0.0
    weight_news_energy_transportation: float = 0.0
    weight_news_finance: float = 0.0
    weight_news_life_sciences: float = 0.0
    weight_news_manufacturing: float = 0.0
    weight_news_real_estate: float = 0.0
    weight_news_retail_wholesale: float = 0.0
    weight_news_technology: float = 0.0

    # ===== v6: 뉴스 z-score 윈도우 & 블록 상한 =====
    news_zscore_window: int = 60
    news_block_cap: float = 4.0

    # ===== 기존 지표 임계값 (legacy schema 호환) =====
    bb_proximity: float = 1.05
    volume_surge_ratio: float = 1.5
    macd_min_hist: float = 0.0

    # ===== 진입 신호 임계값 =====
    signal_threshold: float = 2.0

    # ===== 청산 전략 =====
    exit_strategy: str = "hybrid"
    stop_loss_atr: float = 2.0
    take_profit_atr: float = 3.0
    trailing_atr: float = 1.5
    trailing_activation_profit_pct: float = 3.0
    breakeven_enabled: bool = False
    breakeven_trigger_profit_pct: float = 0.0
    breakeven_floor_profit_pct: float = 0.0
    sell_omen_enabled: bool = False
    sell_omen_threshold: float = 1.0
    max_holding_days: int = 20

    # ===== 포지션 사이징 =====
    position_sizing_strategy: str = "fixed"
    base_position_ratio: float = 1.0
    signal_multiplier: float = 1.0

    # ===== 추가매수 =====
    add_buy_enabled: bool = False
    add_buy_trigger_profit_pct: float = 2.0
    add_buy_max_count: int = 1
    add_buy_size_ratio: float = 0.5
    add_buy_min_signal_score: float = 1.5

    # ===== 시장 연관성 =====
    market_score_weight: float = 0.0
    sector_strength_weight: float = 0.0
    sector_name: str = "tech"
    vix_sensitivity: float = 0.0

    # ===== 이벤트 반응 =====
    event_response_war: float = 0.0
    event_response_rate_hike: float = 0.0
    event_response_rate_cut: float = 0.0
    event_response_geopolitical: float = 0.0
    event_response_tariff: float = 0.0
    event_response_export_ban: float = 0.0
    event_response_earnings_shock: float = 0.0
    event_response_oil_surge: float = 0.0
    event_response_banking_crisis: float = 0.0
    event_response_inflation: float = 0.0
    event_response_fed_statement: float = 0.0

    # ===== 이벤트 강도 & 시장 보정 한도 =====
    event_strength_multiplier: float = 1.0
    market_adjustment_strength: float = 0.3

    # ===== 동적 손절익절 =====
    stop_loss_atr_bear: float = 2.0
    take_profit_atr_bull: float = 3.5
    trailing_atr_volatile: float = 2.0
    crash_buy_enabled: bool = False
    crash_threshold_score: float = 25.0

    # ===== 개별주 전용 =====
    earnings_blackout_days: int = 0
    disclosure_weight: float = 0.0
    analyst_weight: float = 0.0

    # ===== 백테스트 성과 =====
    fitness: float = 0.0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    expectancy_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    trade_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Rulebook":
        if not isinstance(d, Mapping):
            raise TypeError("Rulebook.from_dict() requires a mapping")

        try:
            schema_version = int(d.get("entry_interval_schema_version", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("entry_interval_schema_version must be an integer") from exc

        if schema_version >= STRICT_ENTRY_INTERVAL_SCHEMA_VERSION:
            required_fields = {"entry_interval_schema_version", "entry_feature_domains", "entry_joint_support_count"}
            for spec in ENTRY_INTERVAL_SPECS.values():
                required_fields.add(spec["low_field"])
                required_fields.add(spec["high_field"])
            missing = sorted(required_fields.difference(d.keys()))
            if missing:
                raise ValueError(f"strict entry interval payload missing fields: {missing}")

            domains = d.get("entry_feature_domains")
            if not isinstance(domains, Mapping):
                raise ValueError("strict entry_feature_domains must be a mapping")
            missing_domains = sorted(set(ENTRY_INTERVAL_SPECS).difference(domains.keys()))
            if missing_domains:
                raise ValueError(f"strict entry_feature_domains missing features: {missing_domains}")

        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        if "mask_schema_version" not in d:
            filtered["mask_schema_version"] = 0
        if "breakeven_enabled" not in d:
            try:
                filtered["breakeven_enabled"] = float(d.get("breakeven_trigger_profit_pct", 0.0) or 0.0) > 0.0
            except Exception:
                filtered["breakeven_enabled"] = False
        if "sell_omen_enabled" not in d:
            filtered["sell_omen_enabled"] = False

        rulebook = cls(**filtered)
        if schema_version >= STRICT_ENTRY_INTERVAL_SCHEMA_VERSION:
            errors = validate_entry_intervals(rulebook) + validate_entry_feature_domains(rulebook)
            if errors:
                raise ValueError("invalid strict entry interval payload: " + "; ".join(errors))
        return rulebook


PARAM_RANGES = {
    "weight_ma_align":        (0.0, 2.0),
    "weight_macd_golden":     (0.0, 2.0),
    "weight_rsi_zone":        (0.0, 2.0),
    "weight_bb_near_lower":   (0.0, 2.0),
    "weight_volume_surge":    (0.0, 2.0),
    "weight_news_sentiment":  (0.0, 1.5),

    "weight_news_blockchain": (-3.0, 3.0),
    "weight_news_earnings": (-3.0, 3.0),
    "weight_news_ipo": (-3.0, 3.0),
    "weight_news_mergers_and_acquisitions": (-3.0, 3.0),
    "weight_news_financial_markets": (-3.0, 3.0),
    "weight_news_economy_fiscal": (-3.0, 3.0),
    "weight_news_economy_monetary": (-3.0, 3.0),
    "weight_news_economy_macro": (-3.0, 3.0),
    "weight_news_energy_transportation": (-3.0, 3.0),
    "weight_news_finance": (-3.0, 3.0),
    "weight_news_life_sciences": (-3.0, 3.0),
    "weight_news_manufacturing": (-3.0, 3.0),
    "weight_news_real_estate": (-3.0, 3.0),
    "weight_news_retail_wholesale": (-3.0, 3.0),
    "weight_news_technology": (-3.0, 3.0),

    "news_zscore_window":     (20, 120),
    "news_block_cap":         (2.0, 6.0),

    "rsi_low":                (20.0, 40.0),
    "rsi_high":               (60.0, 80.0),
    "bb_proximity":           (1.0, 1.15),
    "volume_surge_ratio":     (1.2, 2.5),

    "signal_threshold":       (1.5, 4.0),

    "stop_loss_atr":          (1.0, 3.5),
    "take_profit_atr":        (1.5, 5.0),
    "trailing_atr":           (1.0, 3.0),
    "trailing_activation_profit_pct": (1.0, 8.0),
    "breakeven_trigger_profit_pct": (4.0, 8.0),
    "breakeven_floor_profit_pct": (1.0, 3.0),
    "sell_omen_threshold":    (0.30, 0.70),
    "max_holding_days":       (5, 30),

    "base_position_ratio":    (0.3, 1.0),
    "signal_multiplier":      (0.5, 2.0),

    "market_score_weight":    (-1.0, 1.0),
    "sector_strength_weight": (-1.0, 1.0),
    "vix_sensitivity":        (-1.0, 1.0),

    "event_response_war":               (-2.0, 2.0),
    "event_response_rate_hike":         (-2.0, 2.0),
    "event_response_rate_cut":          (-2.0, 2.0),
    "event_response_geopolitical":      (-2.0, 2.0),
    "event_response_tariff":            (-2.0, 2.0),
    "event_response_export_ban":        (-2.0, 2.0),
    "event_response_earnings_shock":    (-2.0, 2.0),
    "event_response_oil_surge":         (-2.0, 2.0),
    "event_response_banking_crisis":    (-2.0, 2.0),
    "event_response_inflation":         (-2.0, 2.0),
    "event_response_fed_statement":     (-2.0, 2.0),

    "event_strength_multiplier":  (0.5, 3.0),
    "market_adjustment_strength": (0.0, 1.0),

    "stop_loss_atr_bear":     (1.0, 5.0),
    "take_profit_atr_bull":   (1.5, 6.0),
    "trailing_atr_volatile":  (1.0, 4.0),
    "crash_threshold_score":  (10.0, 40.0),
}

CATEGORICAL_PARAMS = {
    "exit_strategy":                 ["fixed", "trailing", "hybrid"],
    "position_sizing_strategy":      ["fixed", "signal_scaled", "kelly_lite"],
    "breakeven_enabled":             [False, True],
    "sell_omen_enabled":             [False, True],
    "crash_buy_enabled":             [False, True],
    "use_news_global":               [False, True],
    "use_event_block":               [False, True],
    "use_market_entry_adjustment":   [False, True],
}


def default_rulebook(ticker: str, asset_type: str = "korean_etf", direction: str = "long") -> Rulebook:
    rb = Rulebook(ticker=ticker, asset_type=asset_type, direction=direction)
    if direction == "short":
        rb.market_score_weight = -0.5
        rb.sector_strength_weight = -0.3
    else:
        rb.market_score_weight = 0.5
        rb.sector_strength_weight = 0.3
    return rb


if __name__ == "__main__":
    rb = default_rulebook("379800", "korean_etf", "long")
    d = rb.to_dict()
    print("✅ Rulebook 기본값 생성")
    print(f"  필드 수: {len(d)}")
    print(f"  학습 가능 수치 파라미터: {len(PARAM_RANGES)}")
    print(f"  카테고리 파라미터: {len(CATEGORICAL_PARAMS)}")
    print(f"  ticker={rb.ticker}, direction={rb.direction}")
    print(f"  exit_strategy={rb.exit_strategy}, signal_threshold={rb.signal_threshold}")
    print(f"  market_score_weight={rb.market_score_weight}")

    rb2 = Rulebook.from_dict(d)
    print(f"\n✅ 직렬화/역직렬화 정상: {rb2.ticker == rb.ticker}")
