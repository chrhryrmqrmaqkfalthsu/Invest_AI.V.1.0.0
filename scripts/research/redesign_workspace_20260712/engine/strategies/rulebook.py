"""Strict-AND interval Rulebook schema for the isolated redesign workspace.

The production Rulebook remains untouched.  This workspace schema keeps legacy
fields for JSON compatibility while entry selection is represented only by
finite, bilateral low/high intervals in normalized [0, 1] space.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import isfinite
from typing import Any, Mapping


INTERVAL_SCHEMA_VERSION = 1
ENTRY_FEATURES: tuple[str, ...] = (
    "ma_trend",
    "macd_hist",
    "rsi",
    "bb_position",
    "volume_ratio",
)
INTERVAL_DOMAIN_LOW = 0.0
INTERVAL_DOMAIN_HIGH = 1.0
MIN_INTERVAL_WIDTH = 0.10
NEAR_FULL_INTERVAL_WIDTH = 0.98
MAX_NEAR_FULL_INTERVALS = 2
FIXED_MAX_HOLDING_DAYS = 7


def default_entry_intervals() -> dict[str, dict[str, float]]:
    """Return broad but bilateral defaults for the five Phase-1 features."""
    return {
        "ma_trend": {"low": 0.35, "high": 0.75},
        "macd_hist": {"low": 0.35, "high": 0.75},
        "rsi": {"low": 0.25, "high": 0.75},
        "bb_position": {"low": 0.00, "high": 0.70},
        "volume_ratio": {"low": 0.02, "high": 0.80},
    }


def _coerce_interval_pair(raw: Any) -> tuple[float, float] | None:
    if not isinstance(raw, Mapping):
        return None
    if "low" not in raw or "high" not in raw:
        return None
    try:
        return float(raw["low"]), float(raw["high"])
    except (TypeError, ValueError):
        return None


def validate_entry_intervals(
    intervals: Mapping[str, Any] | None,
    *,
    min_width: float = MIN_INTERVAL_WIDTH,
    near_full_width: float = NEAR_FULL_INTERVAL_WIDTH,
    max_near_full: int = MAX_NEAR_FULL_INTERVALS,
) -> tuple[bool, str]:
    """Validate the strict-AND entry chromosome.

    The reason names intentionally match the already verified research
    interval implementation so Phase-3 reports remain comparable.
    """
    if not isinstance(intervals, Mapping):
        return False, "open_or_nonfinite_bound"

    near_full_count = 0
    for feature in ENTRY_FEATURES:
        pair = _coerce_interval_pair(intervals.get(feature))
        if pair is None:
            return False, "open_or_nonfinite_bound"
        low, high = pair
        if not isfinite(low) or not isfinite(high):
            return False, "open_or_nonfinite_bound"
        if low < INTERVAL_DOMAIN_LOW or high > INTERVAL_DOMAIN_HIGH:
            return False, "outside_normalized_domain"
        if high <= low:
            return False, "not_bilateral"
        width = high - low
        if width < float(min_width):
            return False, "min_width_violation"
        if width >= float(near_full_width):
            near_full_count += 1

    if near_full_count > int(max_near_full):
        return False, "too_many_near_full_ranges"
    return True, "ok"


def canonical_entry_intervals(intervals: Mapping[str, Any] | None) -> dict[str, dict[str, float]]:
    """Return a deterministic deep copy or raise for an invalid chromosome."""
    valid, reason = validate_entry_intervals(intervals)
    if not valid:
        raise ValueError(f"invalid entry intervals: {reason}")
    assert intervals is not None
    return {
        feature: {
            "low": float(intervals[feature]["low"]),
            "high": float(intervals[feature]["high"]),
        }
        for feature in ENTRY_FEATURES
    }


def validate_rulebook_intervals(rulebook: "Rulebook") -> tuple[bool, str]:
    return validate_entry_intervals(getattr(rulebook, "entry_intervals", None))


@dataclass
class Rulebook:
    # Metadata
    ticker: str = ""
    asset_type: str = ""
    direction: str = "long"
    version: str = "strict_interval_v1"
    generated_at: str = ""
    mask_schema_version: int = 2
    interval_schema_version: int = INTERVAL_SCHEMA_VERSION

    # Strict-AND entry chromosome
    entry_intervals: dict[str, dict[str, float]] = field(default_factory=default_entry_intervals)

    # Entry sizing is separate from the boolean entry decision.
    position_sizing_strategy: str = "signal_scaled"
    base_position_ratio: float = 0.60
    signal_multiplier: float = 1.0

    # Market context affects sizing quality only, never strict-AND pass/fail.
    market_score_weight: float = 0.0
    sector_strength_weight: float = 0.0
    sector_name: str = "tech"
    vix_sensitivity: float = 0.0
    market_adjustment_strength: float = 0.30
    use_market_entry_adjustment: bool = True

    # Redesign exit contract.
    stop_loss_atr: float = 2.0
    max_holding_days: int = FIXED_MAX_HOLDING_DAYS

    # Legacy fields retained only for loading and artifact compatibility.
    use_news_global: bool = False
    use_event_block: bool = False
    weight_ma_align: float = 0.0
    weight_macd_golden: float = 0.0
    weight_rsi_zone: float = 0.0
    weight_bb_near_lower: float = 0.0
    weight_volume_surge: float = 0.0
    weight_news_sentiment: float = 0.0
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
    news_zscore_window: int = 60
    news_block_cap: float = 4.0
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    bb_proximity: float = 1.05
    volume_surge_ratio: float = 1.5
    macd_min_hist: float = 0.0
    signal_threshold: float = 1.0
    exit_strategy: str = "strict_interval"
    take_profit_atr: float = 1_000_000.0
    trailing_atr: float = 1_000_000.0
    trailing_activation_profit_pct: float = 1_000_000.0
    breakeven_enabled: bool = False
    breakeven_trigger_profit_pct: float = 0.0
    breakeven_floor_profit_pct: float = 0.0
    sell_omen_enabled: bool = False
    sell_omen_threshold: float = 1.0
    add_buy_enabled: bool = False
    add_buy_trigger_profit_pct: float = 2.0
    add_buy_max_count: int = 1
    add_buy_size_ratio: float = 0.5
    add_buy_min_signal_score: float = 1.5
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
    event_strength_multiplier: float = 1.0
    stop_loss_atr_bear: float = 2.0
    take_profit_atr_bull: float = 1_000_000.0
    trailing_atr_volatile: float = 1_000_000.0
    crash_buy_enabled: bool = False
    crash_threshold_score: float = 25.0
    earnings_blackout_days: int = 0
    disclosure_weight: float = 0.0
    analyst_weight: float = 0.0

    # Backtest metrics
    fitness: float = 0.0
    fitness_daily_efficiency: float = 0.0
    win_rate: float = 0.0
    avg_return_pct: float = 0.0
    expectancy_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    trade_count: int = 0

    def __post_init__(self) -> None:
        self.entry_intervals = canonical_entry_intervals(self.entry_intervals)
        self.max_holding_days = FIXED_MAX_HOLDING_DAYS
        self.exit_strategy = "strict_interval"
        self.take_profit_atr = 1_000_000.0
        self.trailing_atr = 1_000_000.0
        self.trailing_activation_profit_pct = 1_000_000.0
        self.add_buy_enabled = False
        self.breakeven_enabled = False
        self.sell_omen_enabled = False
        self.crash_buy_enabled = False
        self.use_news_global = False
        self.use_event_block = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entry_intervals"] = canonical_entry_intervals(self.entry_intervals)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Rulebook":
        known = set(cls.__dataclass_fields__)
        filtered = {key: value for key, value in dict(data).items() if key in known}

        interval_keys_present = "entry_intervals" in data
        if interval_keys_present:
            filtered["entry_intervals"] = canonical_entry_intervals(data.get("entry_intervals"))
        else:
            # Legacy scalar rulebooks receive safe bilateral defaults.  A
            # one-sided new-schema payload is never silently repaired.
            filtered["entry_intervals"] = default_entry_intervals()
            filtered["interval_schema_version"] = INTERVAL_SCHEMA_VERSION

        filtered["max_holding_days"] = FIXED_MAX_HOLDING_DAYS
        return cls(**filtered)


# Only non-entry genes remain scalar.  Entry genes are exclusively generated
# and mutated as low/high pairs in engine.learning.genetic.
PARAM_RANGES: dict[str, tuple[float, float]] = {
    "stop_loss_atr": (1.0, 3.5),
    "base_position_ratio": (0.20, 1.0),
    "signal_multiplier": (0.50, 2.0),
    "market_score_weight": (-1.0, 1.0),
    "sector_strength_weight": (-1.0, 1.0),
    "vix_sensitivity": (-1.0, 1.0),
    "market_adjustment_strength": (0.0, 1.0),
}

CATEGORICAL_PARAMS: dict[str, list[Any]] = {
    "position_sizing_strategy": ["fixed", "signal_scaled", "kelly_lite"],
    "use_market_entry_adjustment": [False, True],
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
