"""Rolling rediscovery interval-gene GA.

This is the working-copy replacement of the copied Stage2 GA.  Every gene is a
bilateral normalized interval [low, high].  A row passes only when every feature
independently falls inside its own interval; no weighted sum or compensation
path exists.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True)
class IntervalGAConfig:
    population: int = 48
    generations: int = 20
    elite_count: int = 8
    tournament_size: int = 4
    mutation_rate: float = 0.18
    mutation_sigma: float = 0.07
    patience: int = 6
    min_width_norm: float = 0.10
    max_near_full_width_norm: float = 0.98
    max_near_full_gene_count: int = 2
    upper_fallback_probability: float = 0.10


@dataclass
class IntervalIndividual:
    low: np.ndarray
    high: np.ndarray
    fitness: float = float("-inf")
    pass_probability: float = 0.0
    decision_threshold: float = 1.0
    passed_count: int = 0
    precision: float = 0.0
    recall: float = 0.0
    coverage: float = 0.0
    lift: float = 0.0
    fallback_events: list[dict[str, Any]] = field(default_factory=list)
    invalid_reason: str = ""

    def clone(self) -> "IntervalIndividual":
        return copy.deepcopy(self)


@dataclass
class IntervalGAResult:
    best: IntervalIndividual
    history: list[dict[str, Any]]
    generations_run: int
    rejected_narrow_count: int
    rejected_open_count: int
    rejected_near_full_count: int
    fallback_events: list[dict[str, Any]]


def individual_mask(individual: IntervalIndividual, x_norm: np.ndarray) -> np.ndarray:
    """Strict all-feature AND.  There is intentionally no score summation."""
    if x_norm.size == 0:
        return np.zeros(len(x_norm), dtype=bool)
    finite = np.isfinite(x_norm)
    inside = (x_norm >= individual.low) & (x_norm <= individual.high)
    return np.all(finite & inside, axis=1)


def validate_interval_gene(individual: IntervalIndividual, config: IntervalGAConfig) -> tuple[bool, str]:
    if individual.low.shape != individual.high.shape:
        return False, "dimension_mismatch"
    if not np.isfinite(individual.low).all() or not np.isfinite(individual.high).all():
        return False, "open_or_nonfinite_bound"
    if (individual.low < 0.0).any() or (individual.high > 1.0).any():
        return False, "outside_normalized_domain"
    widths = individual.high - individual.low
    if (widths <= 0.0).any():
        return False, "not_bilateral"
    if (widths + 1e-12 < config.min_width_norm).any():
        return False, "min_width_violation"
    if int(np.sum(widths >= config.max_near_full_width_norm)) > config.max_near_full_gene_count:
        return False, "too_many_near_full_ranges"
    return True, "OK"


def _decision_threshold(base_rate: float) -> float:
    # [추정] pilot threshold.  Same value is used for entry and exit.
    return float(min(0.80, max(0.45, base_rate + 0.08)))


def _train_min_pass(n: int) -> int:
    # [추정] pilot gate: at least 20 rows and at least 2% of train rows.
    return max(20, int(math.ceil(max(0, n) * 0.02)))


def _repair_upper_fallback(
    individual: IntervalIndividual,
    x_train: np.ndarray,
    y_train: np.ndarray,
    feature_names: list[str],
    config: IntervalGAConfig,
    *,
    generation: int,
    individual_index: int,
) -> list[dict[str, Any]]:
    """Repair only missing/non-finite upper bounds using successful-trade maxima.

    Finite but too-narrow intervals are not repaired; they are rejected by the
    minimum-width gate.  This distinction makes the BOIL/narrow-gene cut testable.
    """
    events: list[dict[str, Any]] = []
    positive = y_train.astype(int) == 1
    for j, name in enumerate(feature_names):
        if math.isfinite(float(individual.high[j])):
            continue
        success_values = x_train[positive, j]
        success_values = success_values[np.isfinite(success_values)]
        success_max = float(np.max(success_values)) if len(success_values) else 1.0
        low = float(np.clip(individual.low[j], 0.0, 1.0 - config.min_width_norm))
        fallback_high = max(success_max, low + config.min_width_norm)
        fallback_high = float(np.clip(fallback_high, low + config.min_width_norm, 1.0))
        individual.low[j] = low
        individual.high[j] = fallback_high
        event = {
            "generation": generation,
            "individual_index": individual_index,
            "feature": name,
            "reason": "upper_learning_failed_nonfinite",
            "successful_trade_max_norm": success_max,
            "low_norm": low,
            "fallback_high_norm": fallback_high,
            "applied": True,
        }
        individual.fallback_events.append(event)
        events.append(event)
    return events


def _evaluate(
    individual: IntervalIndividual,
    x_train: np.ndarray,
    y_train: np.ndarray,
    config: IntervalGAConfig,
) -> tuple[float, str]:
    valid, reason = validate_interval_gene(individual, config)
    if not valid:
        individual.invalid_reason = reason
        individual.fitness = -1_000_000.0
        return individual.fitness, reason

    mask = individual_mask(individual, x_train)
    passed = int(mask.sum())
    minimum = _train_min_pass(len(y_train))
    base_rate = float(np.mean(y_train)) if len(y_train) else 0.0
    if passed < minimum:
        individual.passed_count = passed
        individual.invalid_reason = "thin_sample"
        individual.fitness = -100_000.0 + passed
        return individual.fitness, "thin_sample"

    positives = int(np.sum(y_train[mask]))
    precision = positives / passed if passed else 0.0
    recall = positives / max(1, int(np.sum(y_train)))
    coverage = passed / max(1, len(y_train))
    lift = precision - base_rate
    threshold = _decision_threshold(base_rate)
    widths = individual.high - individual.low

    # Precision-centered, sample-gated objective.  Width only supplies a small
    # regularizer; it cannot compensate for a failed feature because selection
    # is always the strict AND mask above.
    score = (
        precision * 220.0
        + lift * 100.0
        + recall * 12.0
        + min(coverage, 0.35) * 8.0
        + math.log1p(passed) * 1.5
        - float(np.mean(widths)) * 4.0
    )
    if precision < threshold:
        score -= 60.0 + (threshold - precision) * 100.0

    individual.fitness = float(score)
    individual.pass_probability = float(precision)
    individual.decision_threshold = threshold
    individual.passed_count = passed
    individual.precision = float(precision)
    individual.recall = float(recall)
    individual.coverage = float(coverage)
    individual.lift = float(lift)
    individual.invalid_reason = ""
    return individual.fitness, "OK"


def _random_individual(rng: np.random.Generator, n_features: int, config: IntervalGAConfig, index: int) -> IntervalIndividual:
    # Broad-but-bilateral intervals make 12-way AND feasible while remaining
    # narrower than an effectively unbounded [0, 1] gene.
    widths = rng.uniform(0.35, 0.90, n_features)
    lows = rng.uniform(0.0, 1.0 - widths)
    highs = lows + widths
    if index == 0:
        lows[:] = 0.05
        highs[:] = 0.95
    elif rng.random() < config.upper_fallback_probability:
        highs[int(rng.integers(0, n_features))] = np.nan
    return IntervalIndividual(lows.astype(float), highs.astype(float))


def _tournament(population: list[IntervalIndividual], rng: np.random.Generator, size: int) -> IntervalIndividual:
    indexes = rng.choice(len(population), size=min(size, len(population)), replace=False)
    return max((population[int(i)] for i in indexes), key=lambda item: item.fitness)


def _crossover(a: IntervalIndividual, b: IntervalIndividual, rng: np.random.Generator) -> IntervalIndividual:
    choose = rng.random(len(a.low)) < 0.5
    return IntervalIndividual(
        low=np.where(choose, a.low, b.low).astype(float),
        high=np.where(choose, a.high, b.high).astype(float),
    )


def _mutate(individual: IntervalIndividual, rng: np.random.Generator, config: IntervalGAConfig) -> None:
    for j in range(len(individual.low)):
        if rng.random() < config.mutation_rate:
            individual.low[j] += float(rng.normal(0.0, config.mutation_sigma))
        if rng.random() < config.mutation_rate:
            individual.high[j] += float(rng.normal(0.0, config.mutation_sigma))
        if rng.random() < config.upper_fallback_probability * 0.04:
            individual.high[j] = np.nan
    individual.low = np.clip(individual.low, -0.15, 1.05)
    finite_high = np.isfinite(individual.high)
    individual.high[finite_high] = np.clip(individual.high[finite_high], -0.05, 1.15)


def train_interval_ga(
    x_train: np.ndarray,
    y_train: np.ndarray,
    feature_names: list[str],
    *,
    seed: int,
    config: IntervalGAConfig | None = None,
) -> IntervalGAResult:
    cfg = config or IntervalGAConfig()
    rng = np.random.default_rng(int(seed))
    population = [_random_individual(rng, len(feature_names), cfg, i) for i in range(cfg.population)]
    fallback_events: list[dict[str, Any]] = []
    rejected_narrow = 0
    rejected_open = 0
    rejected_near_full = 0

    def evaluate_population(items: list[IntervalIndividual], generation: int) -> None:
        nonlocal rejected_narrow, rejected_open, rejected_near_full
        for index, item in enumerate(items):
            fallback_events.extend(
                _repair_upper_fallback(
                    item,
                    x_train,
                    y_train,
                    feature_names,
                    cfg,
                    generation=generation,
                    individual_index=index,
                )
            )
            _, reason = _evaluate(item, x_train, y_train, cfg)
            if reason == "min_width_violation":
                rejected_narrow += 1
            elif reason in {"open_or_nonfinite_bound", "not_bilateral"}:
                rejected_open += 1
            elif reason == "too_many_near_full_ranges":
                rejected_near_full += 1

    evaluate_population(population, 0)
    best_overall = max(population, key=lambda item: item.fitness).clone()
    history: list[dict[str, Any]] = []
    no_improve = 0

    for generation in range(1, cfg.generations + 1):
        population.sort(key=lambda item: item.fitness, reverse=True)
        best = population[0]
        finite_fitness = [item.fitness for item in population if item.fitness > -999_999]
        history.append(
            {
                "generation": generation,
                "best_fitness": best.fitness,
                "mean_fitness": float(np.mean(finite_fitness)) if finite_fitness else -1_000_000.0,
                "best_passed_count": best.passed_count,
                "best_precision": best.precision,
                "best_recall": best.recall,
                "best_coverage": best.coverage,
                "best_lift": best.lift,
                "best_pass_probability": best.pass_probability,
                "decision_threshold": best.decision_threshold,
            }
        )
        if best.fitness > best_overall.fitness + 1e-12:
            best_overall = best.clone()
            no_improve = 0
        else:
            no_improve += 1
        if no_improve >= cfg.patience:
            break

        elites = [item.clone() for item in population[: max(1, cfg.elite_count)]]
        children: list[IntervalIndividual] = []
        while len(elites) + len(children) < cfg.population:
            p1 = _tournament(population, rng, cfg.tournament_size)
            p2 = _tournament(population, rng, cfg.tournament_size)
            child = _crossover(p1, p2, rng)
            _mutate(child, rng, cfg)
            children.append(child)
        evaluate_population(children, generation)
        population = elites + children

    # Final safety: the selected individual must be fully bilateral and wide enough.
    valid, reason = validate_interval_gene(best_overall, cfg)
    if not valid:
        baseline = IntervalIndividual(np.full(len(feature_names), 0.05), np.full(len(feature_names), 0.95))
        _evaluate(baseline, x_train, y_train, cfg)
        best_overall = baseline
        reason = "baseline_replacement"
    best_overall.invalid_reason = "" if reason == "OK" else reason

    return IntervalGAResult(
        best=best_overall,
        history=history,
        generations_run=len(history),
        rejected_narrow_count=rejected_narrow,
        rejected_open_count=rejected_open,
        rejected_near_full_count=rejected_near_full,
        fallback_events=fallback_events,
    )
