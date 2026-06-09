"""
룰북 (Rulebook) 데이터 클래스
- GA가 학습하는 모든 파라미터를 담음
- v4: 기본 16개 + 포지션사이징 + 추가매수 + 시장연관성 + 개별주 전용
"""
from dataclasses import dataclass, field, asdict
from typing import Optional


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

    # ===== 지표 임계값 =====
    rsi_low: float = 30.0
    rsi_high: float = 70.0
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
    breakeven_trigger_profit_pct: float = 0.0  # enabled=True일 때 MFE가 N% 이상이면 breakeven_stop 활성
    breakeven_floor_profit_pct: float = 0.0    # enabled=True일 때 breakeven_stop = avg_cost * (1 + floor/100)
    sell_omen_enabled: bool = False
    sell_omen_threshold: float = 1.0           # enabled=True일 때 ML sell_omen_score >= threshold면 청산
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
        return cls(**filtered)


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

    "add_buy_trigger_profit_pct": (0.5, 3.5),
    "add_buy_max_count":          (0, 3),
    "add_buy_size_ratio":         (0.3, 1.0),

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
    "add_buy_enabled":               [False, True],
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
    print(f"✅ Rulebook 기본값 생성")
    print(f"  필드 수: {len(d)}")
    print(f"  학습 가능 수치 파라미터: {len(PARAM_RANGES)}")
    print(f"  카테고리 파라미터: {len(CATEGORICAL_PARAMS)}")
    print(f"  ticker={rb.ticker}, direction={rb.direction}")
    print(f"  exit_strategy={rb.exit_strategy}, signal_threshold={rb.signal_threshold}")
    print(f"  market_score_weight={rb.market_score_weight}")

    rb2 = Rulebook.from_dict(d)
    print(f"\n✅ 직렬화/역직렬화 정상: {rb2.ticker == rb.ticker}")
