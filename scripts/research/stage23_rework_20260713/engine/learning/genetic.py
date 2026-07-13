"""
유전 알고리즘 (GA v5)
- legacy scope: 기존 수치/카테고리 유전자를 그대로 진화
- entry scope: strict interval pair + position/context quality gene만 진화
- entry scope는 fold empirical domain과 정렬된 raw feature values를 받아
  interval support를 실제 계산하며 exit 14-field를 진화하지 않음
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from engine.core.config import config
from engine.core.logger import get_logger
from engine.core.metadata import compute_rulebook_hash
from engine.pipeline.exit_gene import EXIT_FIELDS
from engine.strategies.rulebook import (
    CATEGORICAL_PARAMS,
    ENTRY_INTERVAL_MAX_NEAR_FULL_COUNT,
    ENTRY_INTERVAL_MIN_FEATURE_SUPPORT,
    ENTRY_INTERVAL_MIN_JOINT_SUPPORT,
    ENTRY_INTERVAL_SPECS,
    PARAM_RANGES,
    STRICT_ENTRY_INTERVAL_SCHEMA_VERSION,
    Rulebook,
    validate_entry_feature_domains,
    validate_entry_intervals,
)

log = get_logger("ga")


@dataclass
class GAConfig:
    population: int = 40
    generations: int = 25
    elite_ratio: float = 0.2
    mutation_rate: float = 0.15
    mutation_strength: float = 0.2
    tournament_size: int = 3
    seed_pattern_ratio: float = 0.33
    early_stop_no_improve: int = 8
    random_seed: Optional[int] = None


@dataclass
class GAResult:
    best: Rulebook
    fitness_history: list
    final_population: list
    generations_run: int


@dataclass(frozen=True)
class _EntryDomainContext:
    """Entry GA 전용 fold domain.

    metadata에는 Rulebook에 직렬화할 scalar 통계만 보관하고, values에는
    support 계산용으로 같은 행에 정렬된 raw fold feature 배열을 보관한다.
    raw values는 Rulebook에 저장하지 않는다.
    """

    metadata: dict[str, dict[str, float | int]]
    values: dict[str, np.ndarray]
    sample_count: int
    eligible_indices: np.ndarray
    scaled_matrix: np.ndarray


def collect_top_rulebooks(ga_result: GAResult, n: int) -> list[Rulebook]:
    """GA 결과에서 fitness 기준 상위 N개 비중복 룰북을 반환한다."""
    try:
        limit = int(n)
    except Exception:
        limit = 0
    if ga_result is None or limit <= 0:
        return []

    candidates: list[Rulebook] = []
    best = getattr(ga_result, "best", None)
    if best is not None:
        candidates.append(best)
    candidates.extend(list(getattr(ga_result, "final_population", []) or []))

    by_hash: dict[str, Rulebook] = {}
    for rb in candidates:
        if rb is None:
            continue
        rulebook_hash = compute_rulebook_hash(rb)
        current = by_hash.get(rulebook_hash)
        rb_fitness = getattr(rb, "fitness", None)
        current_fitness = getattr(current, "fitness", None) if current is not None else None
        rb_score = float(rb_fitness) if rb_fitness is not None else float("-inf")
        current_score = float(current_fitness) if current_fitness is not None else float("-inf")
        if current is None or rb_score > current_score:
            by_hash[rulebook_hash] = rb

    ranked = sorted(
        by_hash.items(),
        key=lambda item: (
            -(float(getattr(item[1], "fitness", None)) if getattr(item[1], "fitness", None) is not None else float("-inf")),
            item[0],
        ),
    )
    return [rb for _, rb in ranked[:limit]]


def _rand_in(low, high, integer: bool = False):
    if integer:
        return random.randint(int(low), int(high))
    return random.uniform(low, high)


_INT_PARAMS = {"max_holding_days"}
_MASK_CATEGORICAL_PARAMS = {
    "use_news_global",
    "use_event_block",
    "use_market_entry_adjustment",
}
_ENTRY_SCOPE = "entry"
_LEGACY_SCOPE = "legacy"
_ENTRY_INTERVAL_FIELDS = frozenset(
    field_name
    for spec in ENTRY_INTERVAL_SPECS.values()
    for field_name in (spec["low_field"], spec["high_field"])
)
_ENTRY_POSITION_NUMERIC_PARAMS = {
    "base_position_ratio",
    "signal_multiplier",
}
_ENTRY_CONTEXT_NUMERIC_PARAMS = {
    "weight_news_sentiment",
    "news_zscore_window",
    "news_block_cap",
    "market_score_weight",
    "sector_strength_weight",
    "vix_sensitivity",
    "event_strength_multiplier",
    "market_adjustment_strength",
}
_ENTRY_CONTEXT_NUMERIC_PARAMS.update(
    key for key in PARAM_RANGES if key.startswith("weight_news_")
)
_ENTRY_CONTEXT_NUMERIC_PARAMS.update(
    key for key in PARAM_RANGES if key.startswith("event_response_")
)
_ENTRY_NUMERIC_PARAMS = frozenset(
    (_ENTRY_POSITION_NUMERIC_PARAMS | _ENTRY_CONTEXT_NUMERIC_PARAMS)
    .intersection(PARAM_RANGES)
    .difference(_ENTRY_INTERVAL_FIELDS)
    .difference(EXIT_FIELDS)
)
_ENTRY_CATEGORICAL_PARAMS = frozenset(
    {
        "position_sizing_strategy",
        "use_news_global",
        "use_event_block",
        "use_market_entry_adjustment",
    }.intersection(CATEGORICAL_PARAMS).difference(EXIT_FIELDS)
)

if (_ENTRY_NUMERIC_PARAMS | _ENTRY_CATEGORICAL_PARAMS).intersection(EXIT_FIELDS):
    raise RuntimeError("entry gene scope must not contain Stage 3 exit fields")


def _mark_mask_schema_if_needed(rb: Rulebook) -> None:
    if any(key in CATEGORICAL_PARAMS and hasattr(rb, key) for key in _MASK_CATEGORICAL_PARAMS):
        rb.mask_schema_version = max(int(getattr(rb, "mask_schema_version", 0) or 0), 1)


def _clamp_float(value: object, low: float, high: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = low
    return float(max(low, min(high, number)))


def _disable_add_buy_genes(rb: Rulebook) -> None:
    rb.add_buy_enabled = False
    rb.add_buy_trigger_profit_pct = 2.0
    rb.add_buy_max_count = 1
    rb.add_buy_size_ratio = 0.5
    rb.add_buy_min_signal_score = 1.5


def _normalize_dependent_params(rb: Rulebook) -> None:
    if not bool(getattr(rb, "breakeven_enabled", False)):
        rb.breakeven_trigger_profit_pct = 0.0
        rb.breakeven_floor_profit_pct = 0.0
    else:
        trigger_low, trigger_high = PARAM_RANGES["breakeven_trigger_profit_pct"]
        floor_low, floor_high = PARAM_RANGES["breakeven_floor_profit_pct"]
        rb.breakeven_trigger_profit_pct = _clamp_float(
            getattr(rb, "breakeven_trigger_profit_pct", trigger_low),
            trigger_low,
            trigger_high,
        )
        rb.breakeven_floor_profit_pct = _clamp_float(
            getattr(rb, "breakeven_floor_profit_pct", floor_low),
            floor_low,
            floor_high,
        )

    if not bool(getattr(rb, "sell_omen_enabled", False)):
        rb.sell_omen_threshold = 1.0
    else:
        threshold_low, threshold_high = PARAM_RANGES["sell_omen_threshold"]
        rb.sell_omen_threshold = _clamp_float(
            getattr(rb, "sell_omen_threshold", threshold_high),
            threshold_low,
            threshold_high,
        )


def _finalize_rulebook_genes(
    rb: Rulebook,
    *,
    gene_scope: str = _LEGACY_SCOPE,
) -> Rulebook:
    """공통 후처리.

    Entry scope에서는 Stage 3 EXIT_FIELDS를 보존해야 하므로 breakeven/
    sell-omen dependent normalization을 실행하지 않는다.
    """
    scope = _normalize_gene_scope(gene_scope)
    _mark_mask_schema_if_needed(rb)
    if scope == _LEGACY_SCOPE:
        _normalize_dependent_params(rb)
    _disable_add_buy_genes(rb)
    return rb


def _normalize_gene_scope(gene_scope: str | None) -> str:
    scope = str(gene_scope or _LEGACY_SCOPE).strip().lower()
    if scope not in {_LEGACY_SCOPE, _ENTRY_SCOPE}:
        raise ValueError(f"unsupported gene_scope: {gene_scope!r}")
    return scope


def _finite_float(value: Any, *, label: str) -> float:
    if not isinstance(value, Real) or isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not np.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _positive_int(value: Any, *, label: str) -> int:
    if not isinstance(value, Integral) or isinstance(value, bool) or int(value) <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def _numbers_close(actual: float, supplied: float) -> bool:
    return bool(np.isclose(actual, supplied, rtol=1e-6, atol=1e-9, equal_nan=False))


def _normalize_entry_feature_domain(
    entry_feature_domain: Mapping[str, Any] | _EntryDomainContext | None,
) -> _EntryDomainContext:
    """Fold domain을 검증하고 support 계산용 정렬 배열로 정규화한다.

    각 feature payload 필수 키:
      train_min, train_max, q01, q99, iqr, sample_count, values

    ``values``는 5개 feature에서 같은 행 순서와 같은 길이를 가져야 한다.
    supplied scalar 통계는 raw values에서 다시 계산한 값과 일치해야 한다.
    """
    if isinstance(entry_feature_domain, _EntryDomainContext):
        return entry_feature_domain
    if not isinstance(entry_feature_domain, Mapping):
        raise ValueError("entry gene scope requires entry_feature_domain mapping")

    metadata: dict[str, dict[str, float | int]] = {}
    values: dict[str, np.ndarray] = {}
    expected_count: int | None = None

    for feature_name in ENTRY_INTERVAL_SPECS:
        payload = entry_feature_domain.get(feature_name)
        if not isinstance(payload, Mapping):
            raise ValueError(f"entry_feature_domain missing feature: {feature_name}")

        raw_values = payload.get("values", payload.get("observations"))
        if raw_values is None or isinstance(raw_values, (str, bytes)):
            raise ValueError(f"{feature_name}: aligned raw values are required")
        try:
            array = np.asarray(list(raw_values), dtype=float)
        except Exception as exc:
            raise ValueError(f"{feature_name}: raw values must be numeric") from exc
        if array.ndim != 1 or array.size == 0:
            raise ValueError(f"{feature_name}: raw values must be a non-empty 1D sequence")
        if not bool(np.isfinite(array).all()):
            raise ValueError(f"{feature_name}: raw values contain NaN/Inf")

        sample_count = _positive_int(payload.get("sample_count"), label=f"{feature_name}.sample_count")
        if array.size != sample_count:
            raise ValueError(
                f"{feature_name}: values length {array.size} != sample_count {sample_count}"
            )
        if expected_count is None:
            expected_count = sample_count
        elif sample_count != expected_count:
            raise ValueError("entry feature raw values must be row-aligned with equal length")

        train_min = _finite_float(payload.get("train_min"), label=f"{feature_name}.train_min")
        train_max = _finite_float(payload.get("train_max"), label=f"{feature_name}.train_max")
        q01 = _finite_float(payload.get("q01"), label=f"{feature_name}.q01")
        q99 = _finite_float(payload.get("q99"), label=f"{feature_name}.q99")
        iqr = _finite_float(payload.get("iqr"), label=f"{feature_name}.iqr")

        computed = {
            "train_min": float(np.min(array)),
            "train_max": float(np.max(array)),
            "q01": float(np.quantile(array, 0.01)),
            "q99": float(np.quantile(array, 0.99)),
            "iqr": float(np.quantile(array, 0.75) - np.quantile(array, 0.25)),
        }
        supplied = {
            "train_min": train_min,
            "train_max": train_max,
            "q01": q01,
            "q99": q99,
            "iqr": iqr,
        }
        for key, actual in computed.items():
            if not _numbers_close(actual, supplied[key]):
                raise ValueError(
                    f"{feature_name}: supplied {key} does not match aligned raw values"
                )

        if train_max < train_min:
            raise ValueError(f"{feature_name}: train_max < train_min")
        if q99 <= q01:
            raise ValueError(f"{feature_name}: q99 must be greater than q01")
        if q01 < train_min or q99 > train_max:
            raise ValueError(f"{feature_name}: q01/q99 outside train_min/train_max")
        if iqr <= 0.0:
            raise ValueError(f"{feature_name}: iqr must be positive")
        if iqr * float(ENTRY_INTERVAL_SPECS[feature_name]["min_width_iqr_ratio"]) >= (q99 - q01):
            raise ValueError(f"{feature_name}: empirical span too narrow for minimum interval width")

        metadata[feature_name] = {
            "train_min": train_min,
            "train_max": train_max,
            "q01": q01,
            "q99": q99,
            "iqr": iqr,
            "sample_count": sample_count,
        }
        values[feature_name] = array

    if expected_count is None:
        raise ValueError("entry_feature_domain is empty")

    feature_order = list(ENTRY_INTERVAL_SPECS)
    eligible_mask = np.ones(expected_count, dtype=bool)
    scaled_columns: list[np.ndarray] = []
    for feature_name in feature_order:
        array = values[feature_name]
        domain = metadata[feature_name]
        eligible_mask &= (array >= float(domain["q01"])) & (array <= float(domain["q99"]))
        median = float(np.median(array))
        scaled_columns.append((array - median) / float(domain["iqr"]))

    eligible_indices = np.flatnonzero(eligible_mask)
    minimum_eligible = max(ENTRY_INTERVAL_MIN_FEATURE_SUPPORT, ENTRY_INTERVAL_MIN_JOINT_SUPPORT)
    if eligible_indices.size < minimum_eligible:
        raise ValueError(
            f"joint q01~q99 eligible rows {eligible_indices.size} < {minimum_eligible}"
        )

    return _EntryDomainContext(
        metadata=metadata,
        values=values,
        sample_count=expected_count,
        eligible_indices=eligible_indices,
        scaled_matrix=np.column_stack(scaled_columns),
    )


def _entry_validation_errors(rb: Rulebook) -> list[str]:
    return validate_entry_intervals(
        rb,
        max_near_full_intervals=ENTRY_INTERVAL_MAX_NEAR_FULL_COUNT,
    ) + validate_entry_feature_domains(
        rb,
        min_feature_support=ENTRY_INTERVAL_MIN_FEATURE_SUPPORT,
        min_joint_support=ENTRY_INTERVAL_MIN_JOINT_SUPPORT,
    )


def _apply_entry_domain_and_support(rb: Rulebook, ctx: _EntryDomainContext) -> Rulebook:
    domains: dict[str, dict[str, float | int]] = {}
    joint_mask = np.ones(ctx.sample_count, dtype=bool)

    for feature_name, spec in ENTRY_INTERVAL_SPECS.items():
        low = float(getattr(rb, spec["low_field"]))
        high = float(getattr(rb, spec["high_field"]))
        inside = (ctx.values[feature_name] >= low) & (ctx.values[feature_name] <= high)
        support_count = int(np.count_nonzero(inside))
        joint_mask &= inside
        domain = dict(ctx.metadata[feature_name])
        domain["interval_support_count"] = support_count
        domains[feature_name] = domain

    rb.entry_interval_schema_version = STRICT_ENTRY_INTERVAL_SCHEMA_VERSION
    rb.entry_feature_domains = domains
    rb.entry_joint_support_count = int(np.count_nonzero(joint_mask))
    return rb


def _require_valid_entry_candidate(rb: Rulebook, ctx: _EntryDomainContext, *, stage: str) -> Rulebook:
    _apply_entry_domain_and_support(rb, ctx)
    errors = _entry_validation_errors(rb)
    if errors:
        raise ValueError(f"invalid entry candidate at {stage}: {'; '.join(errors)}")
    return rb


def _clamp_entry_pair(
    center: float,
    width: float,
    *,
    q01: float,
    q99: float,
    minimum_width: float,
) -> tuple[float, float]:
    span = q99 - q01
    width = max(float(width), float(minimum_width))
    if width >= span:
        raise ValueError("entry interval width must be smaller than empirical q01~q99 span")
    low = center - width / 2.0
    low = max(q01, min(low, q99 - width))
    high = low + width
    return float(low), float(high)


def _sample_entry_interval_pairs(
    rb: Rulebook,
    ctx: _EntryDomainContext,
    *,
    max_attempts: int = 256,
) -> Rulebook:
    """같은 empirical row cluster에서 5개 low/high pair를 함께 생성한다."""
    minimum_cluster = max(ENTRY_INTERVAL_MIN_FEATURE_SUPPORT, ENTRY_INTERVAL_MIN_JOINT_SUPPORT)
    maximum_cluster = min(
        int(ctx.eligible_indices.size),
        max(minimum_cluster, min(60, int(ctx.eligible_indices.size // 3))),
    )

    for _ in range(max(1, int(max_attempts))):
        candidate = copy.deepcopy(rb)
        anchor_idx = int(random.choice(ctx.eligible_indices.tolist()))
        target_count = random.randint(minimum_cluster, maximum_cluster)
        distances = np.max(
            np.abs(ctx.scaled_matrix - ctx.scaled_matrix[anchor_idx]),
            axis=1,
        ).copy()
        eligible_mask = np.zeros(ctx.sample_count, dtype=bool)
        eligible_mask[ctx.eligible_indices] = True
        distances[~eligible_mask] = np.inf
        selected = np.argpartition(distances, target_count - 1)[:target_count]

        try:
            for feature_name, spec in ENTRY_INTERVAL_SPECS.items():
                selected_values = ctx.values[feature_name][selected]
                domain = ctx.metadata[feature_name]
                minimum_width = float(domain["iqr"]) * float(spec["min_width_iqr_ratio"])
                low, high = _clamp_entry_pair(
                    center=float((np.min(selected_values) + np.max(selected_values)) / 2.0),
                    width=float(np.max(selected_values) - np.min(selected_values)),
                    q01=float(domain["q01"]),
                    q99=float(domain["q99"]),
                    minimum_width=minimum_width,
                )
                setattr(candidate, spec["low_field"], low)
                setattr(candidate, spec["high_field"], high)
        except ValueError:
            continue

        _finalize_rulebook_genes(candidate, gene_scope=_ENTRY_SCOPE)
        _apply_entry_domain_and_support(candidate, ctx)
        if not _entry_validation_errors(candidate):
            return candidate

    raise RuntimeError("failed to generate a valid strict entry chromosome")


def _randomize_allowed_numeric(rb: Rulebook, allowed_params: Sequence[str]) -> None:
    for key in allowed_params:
        if key not in PARAM_RANGES or not hasattr(rb, key):
            continue
        low, high = PARAM_RANGES[key]
        setattr(rb, key, _rand_in(low, high, integer=(key in _INT_PARAMS)))


def _randomize_allowed_categorical(rb: Rulebook, allowed_params: Sequence[str]) -> None:
    for key in allowed_params:
        if key in CATEGORICAL_PARAMS and hasattr(rb, key):
            setattr(rb, key, random.choice(CATEGORICAL_PARAMS[key]))


def _legacy_random_rulebook(base: Rulebook) -> Rulebook:
    rb = copy.deepcopy(base)
    for key, (low, high) in PARAM_RANGES.items():
        if hasattr(rb, key):
            setattr(rb, key, _rand_in(low, high, integer=(key in _INT_PARAMS)))
    for key, choices in CATEGORICAL_PARAMS.items():
        if hasattr(rb, key):
            setattr(rb, key, random.choice(choices))
    return _finalize_rulebook_genes(rb)


def random_rulebook(
    base: Rulebook,
    *,
    gene_scope: str = _LEGACY_SCOPE,
    entry_feature_domain: Mapping[str, Any] | _EntryDomainContext | None = None,
    max_attempts: int = 256,
) -> Rulebook:
    scope = _normalize_gene_scope(gene_scope)
    if scope == _LEGACY_SCOPE:
        return _legacy_random_rulebook(base)

    ctx = _normalize_entry_feature_domain(entry_feature_domain)
    rb = copy.deepcopy(base)
    _randomize_allowed_numeric(rb, sorted(_ENTRY_NUMERIC_PARAMS))
    _randomize_allowed_categorical(rb, sorted(_ENTRY_CATEGORICAL_PARAMS))
    rb = _sample_entry_interval_pairs(rb, ctx, max_attempts=max_attempts)
    return _require_valid_entry_candidate(rb, ctx, stage="random")


def _legacy_mutate(rb: Rulebook, mutation_rate: float, strength: float) -> Rulebook:
    new_rb = copy.deepcopy(rb)
    for key, (low, high) in PARAM_RANGES.items():
        if random.random() < mutation_rate and hasattr(new_rb, key):
            current = getattr(new_rb, key)
            sigma = (high - low) * strength
            if key in _INT_PARAMS:
                value = int(round(current + random.gauss(0, sigma)))
                value = max(int(low), min(int(high), value))
            else:
                value = current + random.gauss(0, sigma)
                value = max(low, min(high, value))
            setattr(new_rb, key, value)

    for key, choices in CATEGORICAL_PARAMS.items():
        if random.random() < mutation_rate / 2 and hasattr(new_rb, key):
            setattr(new_rb, key, random.choice(choices))
    return _finalize_rulebook_genes(new_rb)


def _mutate_entry_context_genes(
    rb: Rulebook,
    mutation_rate: float,
    strength: float,
) -> Rulebook:
    candidate = copy.deepcopy(rb)
    for key in sorted(_ENTRY_NUMERIC_PARAMS):
        if random.random() >= mutation_rate or not hasattr(candidate, key):
            continue
        low, high = PARAM_RANGES[key]
        current = getattr(candidate, key)
        sigma = (high - low) * strength
        if key in _INT_PARAMS:
            value = int(round(current + random.gauss(0, sigma)))
            value = max(int(low), min(int(high), value))
        else:
            value = float(current) + random.gauss(0, sigma)
            value = max(low, min(high, value))
        setattr(candidate, key, value)

    for key in sorted(_ENTRY_CATEGORICAL_PARAMS):
        if random.random() < mutation_rate / 2 and hasattr(candidate, key):
            setattr(candidate, key, random.choice(CATEGORICAL_PARAMS[key]))
    return candidate


def mutate(
    rb: Rulebook,
    mutation_rate: float,
    strength: float,
    *,
    gene_scope: str = _LEGACY_SCOPE,
    entry_feature_domain: Mapping[str, Any] | _EntryDomainContext | None = None,
    max_attempts: int = 128,
) -> Rulebook:
    scope = _normalize_gene_scope(gene_scope)
    if scope == _LEGACY_SCOPE:
        return _legacy_mutate(rb, mutation_rate, strength)

    ctx = _normalize_entry_feature_domain(entry_feature_domain)
    context_mutated = _mutate_entry_context_genes(rb, mutation_rate, strength)

    for _ in range(max(1, int(max_attempts))):
        candidate = copy.deepcopy(context_mutated)
        try:
            for feature_name, spec in ENTRY_INTERVAL_SPECS.items():
                low = float(getattr(rb, spec["low_field"]))
                high = float(getattr(rb, spec["high_field"]))
                if random.random() < mutation_rate:
                    domain = ctx.metadata[feature_name]
                    span = float(domain["q99"]) - float(domain["q01"])
                    minimum_width = float(domain["iqr"]) * float(spec["min_width_iqr_ratio"])
                    center = (low + high) / 2.0 + random.gauss(0.0, span * strength)
                    width = (high - low) * float(np.exp(random.gauss(0.0, strength)))
                    low, high = _clamp_entry_pair(
                        center=center,
                        width=width,
                        q01=float(domain["q01"]),
                        q99=float(domain["q99"]),
                        minimum_width=minimum_width,
                    )
                setattr(candidate, spec["low_field"], low)
                setattr(candidate, spec["high_field"], high)
        except (TypeError, ValueError):
            continue

        _finalize_rulebook_genes(candidate, gene_scope=_ENTRY_SCOPE)
        _apply_entry_domain_and_support(candidate, ctx)
        if not _entry_validation_errors(candidate):
            return candidate

    fallback = _sample_entry_interval_pairs(context_mutated, ctx, max_attempts=max_attempts)
    return _require_valid_entry_candidate(fallback, ctx, stage="mutation-fallback")


def _legacy_crossover(p1: Rulebook, p2: Rulebook) -> Rulebook:
    child = copy.deepcopy(p1)
    for key in PARAM_RANGES:
        if hasattr(child, key) and random.random() < 0.5:
            setattr(child, key, getattr(p2, key))
    for key in CATEGORICAL_PARAMS:
        if hasattr(child, key) and random.random() < 0.5:
            setattr(child, key, getattr(p2, key))
    return _finalize_rulebook_genes(child)


def crossover(
    p1: Rulebook,
    p2: Rulebook,
    *,
    gene_scope: str = _LEGACY_SCOPE,
    entry_feature_domain: Mapping[str, Any] | _EntryDomainContext | None = None,
    max_attempts: int = 128,
) -> Rulebook:
    scope = _normalize_gene_scope(gene_scope)
    if scope == _LEGACY_SCOPE:
        return _legacy_crossover(p1, p2)

    ctx = _normalize_entry_feature_domain(entry_feature_domain)
    base = copy.deepcopy(p1)
    for key in sorted(_ENTRY_NUMERIC_PARAMS):
        if hasattr(base, key) and random.random() < 0.5:
            setattr(base, key, getattr(p2, key))
    for key in sorted(_ENTRY_CATEGORICAL_PARAMS):
        if hasattr(base, key) and random.random() < 0.5:
            setattr(base, key, getattr(p2, key))

    for _ in range(max(1, int(max_attempts))):
        child = copy.deepcopy(base)
        for spec in ENTRY_INTERVAL_SPECS.values():
            parent = p2 if random.random() < 0.5 else p1
            setattr(child, spec["low_field"], getattr(parent, spec["low_field"]))
            setattr(child, spec["high_field"], getattr(parent, spec["high_field"]))
        _finalize_rulebook_genes(child, gene_scope=_ENTRY_SCOPE)
        _apply_entry_domain_and_support(child, ctx)
        if not _entry_validation_errors(child):
            return child

    fallback = _sample_entry_interval_pairs(base, ctx, max_attempts=max_attempts)
    return _require_valid_entry_candidate(fallback, ctx, stage="crossover-fallback")


def tournament_select(population: list, k: int) -> Rulebook:
    contenders = random.sample(population, min(k, len(population)))
    return max(contenders, key=lambda item: item.fitness)


def _prepare_entry_seed(
    seed: Rulebook,
    base_rulebook: Rulebook,
    ctx: _EntryDomainContext,
) -> Rulebook:
    """타 종목/타 fold seed를 현재 fold domain에 재검증·재생성한다."""
    candidate = copy.deepcopy(seed)
    candidate.ticker = base_rulebook.ticker
    candidate.asset_type = base_rulebook.asset_type
    candidate.direction = base_rulebook.direction
    candidate.sector_name = base_rulebook.sector_name

    try:
        _finalize_rulebook_genes(candidate, gene_scope=_ENTRY_SCOPE)
        _require_valid_entry_candidate(candidate, ctx, stage="seed-current-domain")
    except (TypeError, ValueError):
        candidate = _sample_entry_interval_pairs(candidate, ctx)

    candidate = mutate(
        candidate,
        mutation_rate=0.1,
        strength=0.1,
        gene_scope=_ENTRY_SCOPE,
        entry_feature_domain=ctx,
    )
    return _require_valid_entry_candidate(candidate, ctx, stage="seed-final")


def _evaluate_candidate(
    rb: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    *,
    gene_scope: str,
    entry_ctx: _EntryDomainContext | None,
    stage: str,
) -> float:
    if gene_scope == _ENTRY_SCOPE:
        if entry_ctx is None:
            raise RuntimeError("entry domain context missing before evaluation")
        _require_valid_entry_candidate(rb, entry_ctx, stage=stage)
    return float(evaluate_fn(rb))


def run_ga(
    base_rulebook: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    ga_config: Optional[GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable[[int, Rulebook, float], None]] = None,
    *,
    gene_scope: str = _LEGACY_SCOPE,
    entry_feature_domain: Mapping[str, Any] | None = None,
) -> GAResult:
    """유전 알고리즘을 실행한다.

    ``legacy`` scope는 기존 유전자 전체를 그대로 사용한다.
    ``entry`` scope는 strict interval pair와 position/context quality gene만
    진화하며 Stage 3 EXIT_FIELDS를 보존한다.

    entry_feature_domain은 feature별 train_min/train_max/q01/q99/iqr/
    sample_count 및 같은 행에 정렬된 values를 제공해야 한다. raw values는
    support 계산에만 사용되며 Rulebook에는 저장하지 않는다.
    """
    scope = _normalize_gene_scope(gene_scope)
    entry_ctx = (
        _normalize_entry_feature_domain(entry_feature_domain)
        if scope == _ENTRY_SCOPE
        else None
    )

    cfg = ga_config or GAConfig(
        population=config.get("learning.population", 40),
        generations=config.get("learning.generations", 25),
        elite_ratio=config.get("learning.elite_ratio", 0.2),
        mutation_rate=config.get("learning.mutation_rate", 0.15),
        seed_pattern_ratio=config.get("learning.seed_pattern_ratio", 0.33),
    )
    if cfg.random_seed is not None:
        random.seed(cfg.random_seed)
        np.random.seed(cfg.random_seed)

    population: list[Rulebook] = []
    seed_count = int(cfg.population * cfg.seed_pattern_ratio)
    if seed_rulebooks:
        for seed in seed_rulebooks[:seed_count]:
            if scope == _ENTRY_SCOPE:
                if entry_ctx is None:
                    raise RuntimeError("entry domain context missing for seed")
                candidate = _prepare_entry_seed(seed, base_rulebook, entry_ctx)
            else:
                candidate = copy.deepcopy(seed)
                candidate.ticker = base_rulebook.ticker
                candidate.asset_type = base_rulebook.asset_type
                candidate.direction = base_rulebook.direction
                candidate.sector_name = base_rulebook.sector_name
                candidate = mutate(candidate, mutation_rate=0.1, strength=0.1)
            population.append(candidate)

    while len(population) < cfg.population:
        population.append(
            random_rulebook(
                base_rulebook,
                gene_scope=scope,
                entry_feature_domain=entry_ctx,
            )
        )

    for index, rulebook in enumerate(population):
        rulebook.fitness = _evaluate_candidate(
            rulebook,
            evaluate_fn,
            gene_scope=scope,
            entry_ctx=entry_ctx,
            stage=f"initial-evaluate-{index}",
        )

    fitness_history: list = []
    best_overall = copy.deepcopy(max(population, key=lambda item: item.fitness))
    no_improve = 0

    for generation in range(1, cfg.generations + 1):
        population.sort(key=lambda item: item.fitness, reverse=True)
        best = population[0]
        average = float(np.mean([rulebook.fitness for rulebook in population]))
        fitness_history.append((generation, best.fitness, average))
        log.info(f"Gen {generation:2d}: best={best.fitness:.3f}, avg={average:.3f}")
        if on_generation:
            on_generation(generation, best, average)

        if best.fitness > best_overall.fitness:
            best_overall = copy.deepcopy(best)
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= cfg.early_stop_no_improve:
                log.info(f"early stop at gen {generation} (no improvement for {no_improve})")
                break

        elite_count = max(1, int(cfg.population * cfg.elite_ratio))
        elites: list[Rulebook] = []
        for elite_index, elite in enumerate(population[:elite_count]):
            candidate = copy.deepcopy(elite)
            if scope == _ENTRY_SCOPE:
                if entry_ctx is None:
                    raise RuntimeError("entry domain context missing for elite")
                try:
                    _require_valid_entry_candidate(
                        candidate,
                        entry_ctx,
                        stage=f"elite-{generation}-{elite_index}",
                    )
                except ValueError:
                    candidate = random_rulebook(
                        base_rulebook,
                        gene_scope=scope,
                        entry_feature_domain=entry_ctx,
                    )
                    candidate.fitness = _evaluate_candidate(
                        candidate,
                        evaluate_fn,
                        gene_scope=scope,
                        entry_ctx=entry_ctx,
                        stage=f"elite-regenerated-{generation}-{elite_index}",
                    )
            elites.append(candidate)

        new_population = elites
        while len(new_population) < cfg.population:
            parent_1 = tournament_select(population, cfg.tournament_size)
            parent_2 = tournament_select(population, cfg.tournament_size)
            child = crossover(
                parent_1,
                parent_2,
                gene_scope=scope,
                entry_feature_domain=entry_ctx,
            )
            child = mutate(
                child,
                cfg.mutation_rate,
                cfg.mutation_strength,
                gene_scope=scope,
                entry_feature_domain=entry_ctx,
            )
            child.fitness = _evaluate_candidate(
                child,
                evaluate_fn,
                gene_scope=scope,
                entry_ctx=entry_ctx,
                stage=f"offspring-evaluate-{generation}-{len(new_population)}",
            )
            new_population.append(child)

        population = new_population

    if scope == _ENTRY_SCOPE:
        if entry_ctx is None:
            raise RuntimeError("entry domain context missing for final result")
        _require_valid_entry_candidate(best_overall, entry_ctx, stage="best-overall")
        for index, rulebook in enumerate(population):
            _require_valid_entry_candidate(rulebook, entry_ctx, stage=f"final-population-{index}")

    return GAResult(
        best=best_overall,
        fitness_history=fitness_history,
        final_population=population,
        generations_run=len(fitness_history),
    )


if __name__ == "__main__":
    from engine.strategies.rulebook import default_rulebook

    base = default_rulebook("TEST", "korean_etf", "long")

    def fake_evaluate(rb: Rulebook) -> float:
        target_signal_threshold = 2.5
        target_base_ratio = 0.7
        return (
            -abs(rb.signal_threshold - target_signal_threshold) * 10
            - abs(rb.base_position_ratio - target_base_ratio) * 20
            + 50
        )

    cfg = GAConfig(population=20, generations=10, random_seed=42)
    result = run_ga(base, fake_evaluate, ga_config=cfg)

    print("=" * 60)
    print(f"GA 테스트 결과 ({result.generations_run} 세대)")
    print("=" * 60)
    print(f"  최고 fitness:        {result.best.fitness:.3f}")
    print(f"  signal_threshold:    {result.best.signal_threshold:.3f} (target 2.5)")
    print(f"  base_position_ratio: {result.best.base_position_ratio:.3f} (target 0.7)")
    print(f"  exit_strategy:       {result.best.exit_strategy}")
    print("  세대별 추이 (gen, best, avg):")
    for generation, best, average in result.fitness_history:
        print(f"    Gen {generation:2d}: best={best:.2f}, avg={average:.2f}")
