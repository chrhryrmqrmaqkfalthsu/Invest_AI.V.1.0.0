"""Floored group-threshold variant of the grouped interval GA.

Only the integer group-threshold search domain differs from grouped_genetic:

* every group threshold is at least 2;
* every group threshold is at most group_size - 1.

For the current 4/4/3/3 groups this yields 2..3, 2..3, 2..2 and 2..2.
Feature interval genes, fallback repair, fitness, selection, crossover, mutation
rates, population, generations and patience are unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from engine.learning import grouped_genetic as base
from engine.learning.genetic import IntervalGAConfig, IntervalIndividual, validate_interval_gene

GroupedIntervalIndividual = base.GroupedIntervalIndividual
GroupedGAResult = base.GroupedGAResult
group_count_details = base.group_count_details
grouped_individual_mask = base.grouped_individual_mask


def group_threshold_bounds(group_indexes: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    minimums = np.full(len(group_indexes), 2, dtype=int)
    maximums = np.array([len(indexes) - 1 for indexes in group_indexes], dtype=int)
    if np.any(maximums < minimums):
        raise ValueError(
            "floored grouped GA requires every group to contain at least three features"
        )
    return minimums, maximums


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

    minimums, maximums = group_threshold_bounds(group_indexes)
    for threshold, minimum, maximum in zip(
        individual.group_thresholds, minimums, maximums
    ):
        value = float(threshold)
        if not math.isfinite(value) or abs(value - round(value)) > 1e-12:
            return False, "group_threshold_not_integer"
        integer = int(round(value))
        if integer < int(minimum):
            return False, "group_threshold_below_floor"
        if integer > int(maximum):
            return False, "group_threshold_above_cap"
    return True, "OK"


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
    minimums, maximums = group_threshold_bounds(group_indexes)
    thresholds = np.array(
        [
            int(rng.integers(int(minimum), int(maximum) + 1))
            for minimum, maximum in zip(minimums, maximums)
        ],
        dtype=int,
    )
    if index == 0:
        lows[:] = 0.05
        highs[:] = 0.95
        thresholds[:] = minimums
    elif rng.random() < config.upper_fallback_probability:
        highs[int(rng.integers(0, n_features))] = np.nan
    return GroupedIntervalIndividual(
        lows.astype(float), highs.astype(float), thresholds.astype(int)
    )


def _mutate(
    individual: GroupedIntervalIndividual,
    rng: np.random.Generator,
    group_indexes: list[np.ndarray],
    config: IntervalGAConfig,
) -> None:
    for feature_index in range(len(individual.low)):
        if rng.random() < config.mutation_rate:
            individual.low[feature_index] += float(
                rng.normal(0.0, config.mutation_sigma)
            )
        if rng.random() < config.mutation_rate:
            individual.high[feature_index] += float(
                rng.normal(0.0, config.mutation_sigma)
            )
        if rng.random() < config.upper_fallback_probability * 0.04:
            individual.high[feature_index] = np.nan

    minimums, maximums = group_threshold_bounds(group_indexes)
    for group_index, (minimum, maximum) in enumerate(zip(minimums, maximums)):
        if rng.random() < config.mutation_rate:
            step = -1 if rng.random() < 0.5 else 1
            individual.group_thresholds[group_index] = int(
                np.clip(
                    individual.group_thresholds[group_index] + step,
                    int(minimum),
                    int(maximum),
                )
            )

    individual.low = np.clip(individual.low, -0.15, 1.05)
    finite_high = np.isfinite(individual.high)
    individual.high[finite_high] = np.clip(
        individual.high[finite_high], -0.05, 1.15
    )


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
    # The original grouped fitness is reused unchanged after the stricter
    # threshold-domain validation succeeds.
    return base._evaluate_grouped(
        individual,
        x_train,
        y_train,
        group_indexes,
        config,
        g3_group_index=g3_group_index,
        g3_floor_norm=g3_floor_norm,
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
        _random_grouped_individual(
            rng, len(feature_names), group_indexes, cfg, population_index
        )
        for population_index in range(cfg.population)
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
        for individual_index, item in enumerate(items):
            interval_proxy = IntervalIndividual(
                item.low, item.high, fallback_events=item.fallback_events
            )
            fallback_events.extend(
                base._repair_upper_fallback(
                    interval_proxy,
                    x_train,
                    y_train,
                    feature_names,
                    cfg,
                    generation=generation,
                    individual_index=individual_index,
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
        finite_fitness = [
            item.fitness for item in population if item.fitness > -999_999
        ]
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

        elites = [
            item.clone() for item in population[: max(1, cfg.elite_count)]
        ]
        children: list[GroupedIntervalIndividual] = []
        while len(elites) + len(children) < cfg.population:
            parent_a = base._tournament(population, rng, cfg.tournament_size)
            parent_b = base._tournament(population, rng, cfg.tournament_size)
            child = base._crossover(parent_a, parent_b, rng)
            _mutate(child, rng, group_indexes, cfg)
            children.append(child)
        evaluate_population(children, generation)
        population = elites + children

    valid, reason = validate_grouped_gene(best_overall, group_indexes, cfg)
    if not valid:
        minimums, _ = group_threshold_bounds(group_indexes)
        baseline = GroupedIntervalIndividual(
            np.full(len(feature_names), 0.05),
            np.full(len(feature_names), 0.95),
            minimums.copy(),
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
