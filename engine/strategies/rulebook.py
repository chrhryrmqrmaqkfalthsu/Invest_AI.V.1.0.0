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

    # ===== 신호 가중치 (기본 16개) =====
    weight_ma_align: float = 1.0       # 정배열
    weight_macd_golden: float = 1.0    # MACD 골든크로스
    weight_rsi_zone: float = 1.0       # RSI 적정 구간
    weight_bb_near_lower: float = 1.0  # 볼린저 하단 근접
    weight_volume_surge: float = 1.0   # 거래량 급증
    weight_news_sentiment: float = 2.0 # 뉴스 감성 (개별주 강화, ×2.0)

    # ===== 지표 임계값 =====
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    bb_proximity: float = 1.05         # 1.0 = 정확히 하단, 1.1 = 10% 위까지 허용
    volume_surge_ratio: float = 1.5    # 5일 평균의 1.5배 이상
    macd_min_hist: float = 0.0         # MACD 히스토그램 최소값

    # ===== 진입 신호 임계값 =====
    signal_threshold: float = 2.0      # 점수 합계가 이 값 이상이면 매수

    # ===== 청산 전략 =====
    exit_strategy: str = "hybrid"      # 'fixed' | 'trailing' | 'hybrid'
    stop_loss_atr: float = 2.0         # 손절: 진입가 - (ATR × N)
    take_profit_atr: float = 3.0       # 익절: 진입가 + (ATR × N)
    trailing_atr: float = 1.5          # 트레일링 스톱 거리
    max_holding_days: int = 20

    # ===== 포지션 사이징 (v4 신규) =====
    position_sizing_strategy: str = "fixed"  # 'fixed' | 'signal_scaled' | 'kelly_lite'
    base_position_ratio: float = 1.0   # 한도 대비 기본 비율 (1.0 = 전액)
    signal_multiplier: float = 1.0     # signal_scaled에서 신호 강도 배수

    # ===== 추가매수 (v4 신규) =====
    add_buy_enabled: bool = False
    add_buy_trigger_profit_pct: float = 2.0  # 수익 N% 도달 시 발동
    add_buy_max_count: int = 1               # 최대 추가매수 횟수
    add_buy_size_ratio: float = 0.5          # 초기매수 대비 비율
    add_buy_min_signal_score: float = 1.5    # 추가매수 시 신호 최소값

    # ===== 시장 연관성 (v4 신규) =====
    market_score_weight: float = 0.0   # +1: 강세장 유리, -1: 약세장 유리
    sector_strength_weight: float = 0.0
    sector_name: str = "tech"          # 어느 섹터에 연동되는지
    vix_sensitivity: float = 0.0       # +1: 변동성 유리, -1: 변동성 불리

    # ===== 이벤트 반응 (v5 신규: 11개 카테고리별 종목 반응) =====
    # -2.0 = 강한 악재, 0 = 무관, +2.0 = 강한 호재
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

    # ===== 이벤트 강도 & 시장 보정 한도 (v5 신규) =====
    event_strength_multiplier: float = 1.0   # 이벤트 영향 전체 강도
    market_adjustment_strength: float = 0.3  # 시장보정 한도 (기존 고정 0.3 → 학습 가능)

    # ===== 동적 손절익절 (v5 신규) =====
    stop_loss_atr_bear: float = 2.0          # 약세장(score<40)에서 손절 ATR
    take_profit_atr_bull: float = 3.5        # 강세장(score>=70)에서 익절 ATR
    trailing_atr_volatile: float = 2.0       # 고변동성(vix>25) 트레일링
    crash_buy_enabled: bool = False          # 폭락장 매수 활성화 (금/안전자산용)
    crash_threshold_score: float = 25.0      # 폭락 판단 점수 (이하면 폭락)

    # ===== 개별주 전용 (asset_type 'korean_stock' / 'us_stock'만 활성) =====
    earnings_blackout_days: int = 0    # 어닝 전후 N일 거래 회피
    disclosure_weight: float = 0.0     # 공시 영향력
    analyst_weight: float = 0.0        # 애널리스트 의견 가중치

    # ===== 백테스트 성과 (학습 결과 기록용) =====
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
        # 알려진 필드만 추출 (이후 버전 호환)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# ---------- 파라미터 범위 (GA용) ----------
PARAM_RANGES = {
    # 가중치
    "weight_ma_align":        (0.0, 2.0),
    "weight_macd_golden":     (0.0, 2.0),
    "weight_rsi_zone":        (0.0, 2.0),
    "weight_bb_near_lower":   (0.0, 2.0),
    "weight_volume_surge":    (0.0, 2.0),
    "weight_news_sentiment":  (1.0, 3.0),  # 개별주 뉴스 강화

    # 임계값
    "rsi_low":                (20.0, 40.0),
    "rsi_high":               (60.0, 80.0),
    "bb_proximity":           (1.0, 1.15),
    "volume_surge_ratio":     (1.2, 2.5),
    "macd_min_hist":          (-0.5, 0.5),

    # 신호
    "signal_threshold":       (1.5, 4.0),

    # 청산
    "stop_loss_atr":          (1.0, 3.5),
    "take_profit_atr":        (1.5, 5.0),
    "trailing_atr":           (1.0, 3.0),
    "max_holding_days":       (5, 30),

    # 포지션 사이징
    "base_position_ratio":    (0.3, 1.0),
    "signal_multiplier":      (0.5, 2.0),

    # 추가매수
    "add_buy_trigger_profit_pct": (0.5, 3.5),
    "add_buy_max_count":          (0, 3),
    "add_buy_size_ratio":         (0.3, 1.0),
    "add_buy_min_signal_score":   (1.0, 2.5),

    # 시장 연관성
    "market_score_weight":    (-1.0, 1.0),
    "sector_strength_weight": (-1.0, 1.0),
    "vix_sensitivity":        (-1.0, 1.0),

    # 이벤트 반응 (v5 신규, -2.0 ~ +2.0)
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

    # 이벤트 강도 & 시장 보정 한도 (v5 신규)
    "event_strength_multiplier":  (0.5, 3.0),
    "market_adjustment_strength": (0.0, 1.0),

    # 동적 손절익절 (v5 신규)
    "stop_loss_atr_bear":     (1.0, 5.0),
    "take_profit_atr_bull":   (1.5, 6.0),
    "trailing_atr_volatile":  (1.0, 4.0),
    "crash_threshold_score":  (10.0, 40.0),

    # 개별주 전용
    "earnings_blackout_days": (0, 3),
    "disclosure_weight":      (0.0, 2.0),
    "analyst_weight":         (0.0, 2.0),
}

CATEGORICAL_PARAMS = {
    "exit_strategy":             ["fixed", "trailing", "hybrid"],
    "position_sizing_strategy":  ["fixed", "signal_scaled", "kelly_lite"],
    "add_buy_enabled":           [False, True],
    "crash_buy_enabled":         [False, True],  # v5 신규
}


def default_rulebook(ticker: str, asset_type: str = "korean_etf", direction: str = "long") -> Rulebook:
    """기본 룰북 (학습 전 초기값)"""
    rb = Rulebook(ticker=ticker, asset_type=asset_type, direction=direction)
    # 인버스는 시장 연관성 음수 시작
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
