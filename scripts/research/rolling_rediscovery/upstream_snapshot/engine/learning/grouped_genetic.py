"""Grouped count-threshold interval GA for the two-symbol hybrid pilot.

Each feature owns a bilateral normalized interval.  A feature contributes one
point when its value is finite and inside that interval.  Group scores are the
unweighted counts of passed features; each group passes when its count reaches
its learned integer threshold.  Final entry is the strict AND across groups.

The existing strict-AND interval GA remains untouched in genetic.py.  This
module mirrors its population / generation / mutation skeleton so the only
structural change is the grouped entry gate and four integer threshold genes.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from engine.learning.genetic import (
    IntervalGAConfig,
    IntervalIndividual,
    _decision_threshold,
    _repair_upper_fallback,
    _train_min_pass,
    validate_interval_gene,
)


@dataclass
class GroupedIntervalIndividual:
    low: np.ndarray
    high: np.ndarray
    group_thresholds: np.ndarray
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

    def clone(self) -> "GroupedIntervalIndividual":
        return copy.deepcopy(self)


@dataclass
class GroupedGAResult:
    best: GroupedIntervalIndividual
    history: list[dict[str, Any]]
    generations_run: int
    rejected_narrow_count: int
    rejected_open_count: int
    rejected_near_full_count: int
    rejected_group_threshold_count: int
    fallback_events: list[dict[str, Any]]


def validate_grouped_gene(
    individual: GroupedIntervalIndividual,
    group_indexes: list[np.ndarray],
    config: IntervalGAConfig,
) -> tuple[bool, str]:
    interval = IntervalIndividual(individual.low.copy(), individual.high.copy())
    valid, reason = validate_interval_gene(interval, config)
    if not valid:
        return False, reason
    if len(individual.group_thresholds) != len(group_indexes):
        return False, "group_threshold_dimension_mismatch"
    for threshold, indexes in zip(individual.group_thresholds, group_indexes):
        value = float(threshold)
        if not math.isfinite(value) or abs(value - round(value)) > 1e-12:
            return False, "group_threshold_not_integer"
        if int(round(value)) < 1 or int(round(value)) > len(indexes):
            return False, "group_threshold_out_of_range"
    return True, "OK"


def group_count_details(
    individual: GroupedIntervalIndividual,
    x_norm: np.ndarray,
    group_indexes: list[np.ndarray],
    *,
    g3_group_index: int = 2,
    g3_floor_norm: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return final mask, feature pass matrix, group counts and group pass matrix."""
    if x_norm.size == 0:
        rows = len(x_norm)
        return (
            np.zeros(rows, dtype=bool),
            np.zeros((rows, len(individual.low)), dtype=bool),
            np.zeros((rows, len(group_indexes)), dtype=int),
            np.zeros((rows, len(group_indexes)), dtype=bool),
        )
    finite = np.isfinite(x_norm)
    feature_pass = finite & (x_norm >= individual.low) & (x_norm <= individual.high)
    group_counts = np.column_stack(
        [np.sum(feature_pass[:, indexes], axis=1) for indexes in group_indexes]
    ).astype(int)
    thresholds = np.asarray(individual.group_thresholds, dtype=int)
    group_pass = group_counts >= thresholds.reshape(1, -1)

    if g3_floor_norm is not None:
        indexes = group_indexes[int(g3_group_index)]
        floors = np.asarray(g3_floor_norm, dtype=float)
        if floors.shape != (len(indexes),):
            raise ValueError(
                f"G3 floor shape mismatch: floors={floors.shape}, group={len(indexes)}"
            )
        floor_ok = np.all(
            np.isfinite(x_norm[:, indexes])
            & (x_norm[:, indexes] >= floors.reshape(1, -1)),
            axis=1,
        )
        group_pass[:, int(g3_group_index)] &= floor_ok

    final_mask = np.all(group_pass, axis=1)
    return final_mask, feature_pass, group_counts, group_pass


def grouped_individual_mask(
    individual: GroupedIntervalIndividual,
    x_norm: np.ndarray,
    group_indexes: list[np.ndarray],
    *,
    g3_group_index: int = 2,
    g3_floor_norm: np.ndarray | None = None,
) -> np.ndarray:
    return group_count_details(
        individual,
        x_norm,
        group_indexes,
        g3_group_index=g3_group_index,
        g3_floor_norm=g3_floor_norm,
    )[0]


def _evaluate_grouped(
    individual: GroupedIntervalIndividual,
    x_train: np.ndarray,
    y_train: np.ndarray,
    group_indexes: list[np.ndarray],
    config: IntervalGAConfig,
    *,
    g3_group_index: int,
    g3_floor_norm: np.ndarray | None,
) -> tuple[float, str]:
    valid, reason = validate_grouped_gene(individual, group_indexes, config)
    if not valid:
        individual.invalid_reason = reason
        individual.fitness = -1_000_000.0
        return individual.fitness, reason

    mask = grouped_individual_mask(
        individual,
        x_train,
        group_indexes,
        g3_group_index=g3_group_index,
        g3_floor_norm=g3_floor_norm,
    )
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

    # Same precision-led skeleton as the strict interval GA.  A small complexity
    # term discourages thresholds of one in every group without making them
    # impossible when the train evidence supports broad coverage.
    normalized_thresholds = np.array(
        [t / len(indexes) for t, indexes in zip(individual.group_thresholds, group_indexes)],
        dtype=float,
    )
    score = (
        precision * 220.0
        + lift * 100.0
        + recall * 12.0
        + min(coverage, 0.35) * 8.0
        + math.log1p(passed) * 1.5
        - float(np.mean(widths)) * 4.0
        + float(np.mean(normalized_thresholds)) * 1.5
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


def _random_grouped_individual(
    rng: np.random.Generator,
    n_features: int,
    group_indexes: list[np.ndarray],
    config: IntervalGAConfig,
    index: int,
) -> GroupedIntervalIndividual:
    widths = rng.uniform(0.35, 0.90, n_features)
    lows = rng.uniform(0.0, 1.0 - widths)
    highs = lows + widths
    thresholds = np.array(
        [int(rng.integers(1, len(indexes) + 1)) for indexes in group_indexes],
        dtype=int,
    )
    if index == 0:
        lows[:] = 0.05
        highs[:] = 0.95
        thresholds[:] = 1
    elif rng.random() < config.upper_fallback_probability:
        highs[int(rng.integers(0, n_features))] = np.nan
    return GroupedIntervalIndividual(
        lows.astype(float), highs.astype(float), thresholds.astype(int)
    )


def _tournament(
    population: list[GroupedIntervalIndividual],
    rng: np.random.Generator,
    size: int,
) -> GroupedIntervalIndividual:
    indexes = rng.choice(len(population), size=min(size, len(population)), replace=False)
    return max((population[int(i)] for i in indexes), key=lambda item: item.fitness)


def _crossover(
    a: GroupedIntervalIndividual,
    b: GroupedIntervalIndividual,
    rng: np.random.Generator,
) -> GroupedIntervalIndividual:
    choose_features = rng.random(len(a.low)) < 0.5
    choose_groups = rng.random(len(a.group_thresholds)) < 0.5
    return GroupedIntervalIndividual(
        low=np.where(choose_features, a.low, b.low).astype(float),
        high=np.where(choose_features, a.high, b.high).astype(float),
        group_thresholds=np.where(
            choose_groups, a.group_thresholds, b.group_thresholds
        ).astype(int),
    )


def _mutate(
    individual: GroupedIntervalIndividual,
    rng: np.random.Generator,
    group_indexes: list[np.ndarray],
    config: IntervalGAConfig,
) -> None:
    for j in range(len(individual.low)):
        if rng.random() < config.mutation_rate:
            individual.low[j] += float(rng.normal(0.0, config.mutation_sigma))
        if rng.random() < config.mutation_rate:
            individual.high[j] += float(rng.normal(0.0, config.mutation_sigma))
        if rng.random() < config.upper_fallback_probability * 0.04:
            individual.high[j] = np.nan
    for j, indexes in enumerate(group_indexes):
        if rng.random() < config.mutation_rate:
            step = -1 if rng.random() < 0.5 else 1
            individual.group_thresholds[j] = int(
                np.clip(individual.group_thresholds[j] + step, 1, len(indexes))
            )
    individual.low = np.clip(individual.low, -0.15, 1.05)
    finite_high = np.isfinite(individual.high)
    individual.high[finite_high] = np.clip(
        individual.high[finite_high], -0.05, 1.15
    )


def train_grouped_interval_ga(
    x_train: np.ndarray,
    y_train: np.ndarray,
    feature_names: list[str],
    group_indexes: list[np.ndarray],
    *,
    seed: int,
    config: IntervalGAConfig | None = None,
    g3_group_index: int = 2,
    g3_floor_norm: np.ndarray | None = None,
) -> GroupedGAResult:
    cfg = config or IntervalGAConfig()
    rng = np.random.default_rng(int(seed))
    population = [
        _random_grouped_individual(rng, len(feature_names), group_indexes, cfg, i)
        for i in range(cfg.population)
    ]
    fallback_events: list[dict[str, Any]] = []
    rejected_narrow = 0
    rejected_open = 0
    rejected_near_full = 0
    rejected_group_threshold = 0

    def evaluate_population(
        items: list[GroupedIntervalIndividual], generation: int
    ) -> None:
        nonlocal rejected_narrow, rejected_open, rejected_near_full
        nonlocal rejected_group_threshold
        for index, item in enumerate(items):
            interval_proxy = IntervalIndividual(
                item.low, item.high, fallback_events=item.fallback_events
            )
            fallback_events.extend(
                _repair_upper_fallback(
                    interval_proxy,
                    x_train,
                    y_train,
                    feature_names,
                    cfg,
                    generation=generation,
                    individual_index=index,
                )
            )
            item.low = interval_proxy.low
            item.high = interval_proxy.high
            item.fallback_events = interval_proxy.fallback_events
            _, reason = _evaluate_grouped(
                item,
                x_train,
                y_train,
                group_indexes,
                cfg,
                g3_group_index=g3_group_index,
                g3_floor_norm=g3_floor_norm,
            )
            if reason == "min_width_violation":
                rejected_narrow += 1
            elif reason in {"open_or_nonfinite_bound", "not_bilateral"}:
                rejected_open += 1
            elif reason == "too_many_near_full_ranges":
                rejected_near_full += 1
            elif reason.startswith("group_threshold"):
                rejected_group_threshold += 1

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
                "mean_fitness": float(np.mean(finite_fitness))
                if finite_fitness
                else -1_000_000.0,
                "best_passed_count": best.passed_count,
                "best_precision": best.precision,
                "best_recall": best.recall,
                "best_coverage": best.coverage,
                "best_lift": best.lift,
                "best_pass_probability": best.pass_probability,
                "decision_threshold": best.decision_threshold,
                "best_group_thresholds": best.group_thresholds.astype(int).tolist(),
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
        children: list[GroupedIntervalIndividual] = []
        while len(elites) + len(children) < cfg.population:
            p1 = _tournament(population, rng, cfg.tournament_size)
            p2 = _tournament(population, rng, cfg.tournament_size)
            child = _crossover(p1, p2, rng)
            _mutate(child, rng, group_indexes, cfg)
            children.append(child)
        evaluate_population(children, generation)
        population = elites + children

    valid, reason = validate_grouped_gene(best_overall, group_indexes, cfg)
    if not valid:
        baseline = GroupedIntervalIndividual(
            np.full(len(feature_names), 0.05),
            np.full(len(feature_names), 0.95),
            np.ones(len(group_indexes), dtype=int),
        )
        _evaluate_grouped(
            baseline,
            x_train,
            y_train,
            group_indexes,
            cfg,
            g3_group_index=g3_group_index,
            g3_floor_norm=g3_floor_norm,
        )
        best_overall = baseline
        reason = "baseline_replacement"
    best_overall.invalid_reason = "" if reason == "OK" else reason

    return GroupedGAResult(
        best=best_overall,
        history=history,
        generations_run=len(history),
        rejected_narrow_count=rejected_narrow,
        rejected_open_count=rejected_open,
        rejected_near_full_count=rejected_near_full,
        rejected_group_threshold_count=rejected_group_threshold,
        fallback_events=fallback_events,
    )
