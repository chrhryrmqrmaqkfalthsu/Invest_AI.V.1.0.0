"""Floored grouped GA with a train↔stress precision consistency penalty.

The only model-selection change versus grouped_genetic_floored is inside
_evaluate_grouped_consistency():

    gap = max(0, train_precision - stress_precision)
    adjusted_precision = train_precision - 0.5 * gap

The existing grouped fitness is retained, but its 220-point train-precision
term is replaced by the adjusted-precision term.  Stress rows are never used
for feature domains, fallback repair, minimum train sample constraints, label
counts, interval construction, threshold construction, crossover or mutation.
They are only an out-of-train consistency scorer.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from engine.learning import grouped_genetic_floored as floored
from engine.learning.genetic import IntervalGAConfig, IntervalIndividual

GroupedIntervalIndividual = floored.GroupedIntervalIndividual
GroupedGAResult = floored.GroupedGAResult
group_count_details = floored.group_count_details
grouped_individual_mask = floored.grouped_individual_mask
group_threshold_bounds = floored.group_threshold_bounds
validate_grouped_gene = floored.validate_grouped_gene

CONSISTENCY_LAMBDA = 0.5
TRAIN_PRECISION_WEIGHT = 220.0


def _set_consistency_fields(
    individual: GroupedIntervalIndividual,
    *,
    stress_passed_count: int = 0,
    stress_precision: float = 0.0,
    precision_gap: float = 0.0,
    adjusted_precision: float = 0.0,
    penalty_points: float = 0.0,
) -> None:
    individual.stress_passed_count_for_fitness = int(stress_passed_count)
    individual.stress_precision_for_fitness = float(stress_precision)
    individual.precision_gap_for_fitness = float(precision_gap)
    individual.adjusted_precision_for_fitness = float(adjusted_precision)
    individual.consistency_penalty_points = float(penalty_points)
    individual.consistency_lambda = float(CONSISTENCY_LAMBDA)


def _evaluate_grouped_consistency(
    individual: GroupedIntervalIndividual,
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_stress: np.ndarray,
    y_stress: np.ndarray,
    group_indexes: list[np.ndarray],
    config: IntervalGAConfig,
    *,
    g3_group_index: int,
    g3_floor_norm: np.ndarray | None,
) -> tuple[float, str]:
    """Apply the single requested fitness change after normal train scoring."""
    original_fitness, reason = floored._evaluate_grouped(
        individual,
        x_train,
        y_train,
        group_indexes,
        config,
        g3_group_index=g3_group_index,
        g3_floor_norm=g3_floor_norm,
    )
    if reason != "OK":
        _set_consistency_fields(individual)
        return original_fitness, reason

    stress_mask = grouped_individual_mask(
        individual,
        x_stress,
        group_indexes,
        g3_group_index=g3_group_index,
        g3_floor_norm=g3_floor_norm,
    )
    stress_passed = int(stress_mask.sum())
    stress_precision = (
        float(np.mean(y_stress[stress_mask])) if stress_passed else 0.0
    )
    train_precision = float(individual.precision)
    gap = max(0.0, train_precision - stress_precision)
    adjusted_precision = train_precision - CONSISTENCY_LAMBDA * gap
    penalty_points = TRAIN_PRECISION_WEIGHT * CONSISTENCY_LAMBDA * gap

    # Existing fitness already contains train_precision * 220.  Subtracting
    # the scaled penalty is algebraically identical to replacing only that
    # term with adjusted_precision * 220; every other term remains unchanged.
    individual.fitness = float(original_fitness - penalty_points)
    _set_consistency_fields(
        individual,
        stress_passed_count=stress_passed,
        stress_precision=stress_precision,
        precision_gap=gap,
        adjusted_precision=adjusted_precision,
        penalty_points=penalty_points,
    )
    return individual.fitness, "OK"


def train_grouped_interval_ga(
    x_train: np.ndarray,
    y_train: np.ndarray,
    feature_names: list[str],
    group_indexes: list[np.ndarray],
    *,
    x_stress: np.ndarray,
    y_stress: np.ndarray,
    seed: int,
    config: IntervalGAConfig | None = None,
    g3_group_index: int = 2,
    g3_floor_norm: np.ndarray | None = None,
) -> GroupedGAResult:
    cfg = config or IntervalGAConfig()
    rng = np.random.default_rng(int(seed))
    population = [
        floored._random_grouped_individual(
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
                floored.base._repair_upper_fallback(
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
            _, reason = _evaluate_grouped_consistency(
                item,
                x_train,
                y_train,
                x_stress,
                y_stress,
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
                "fitness_stress_passed_count": getattr(
                    best, "stress_passed_count_for_fitness", 0
                ),
                "fitness_stress_precision": getattr(
                    best, "stress_precision_for_fitness", 0.0
                ),
                "fitness_precision_gap": getattr(
                    best, "precision_gap_for_fitness", 0.0
                ),
                "fitness_adjusted_precision": getattr(
                    best, "adjusted_precision_for_fitness", 0.0
                ),
                "fitness_consistency_penalty_points": getattr(
                    best, "consistency_penalty_points", 0.0
                ),
                "fitness_consistency_lambda": CONSISTENCY_LAMBDA,
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
            parent_a = floored.base._tournament(
                population, rng, cfg.tournament_size
            )
            parent_b = floored.base._tournament(
                population, rng, cfg.tournament_size
            )
            child = floored.base._crossover(parent_a, parent_b, rng)
            floored._mutate(child, rng, group_indexes, cfg)
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
        _evaluate_grouped_consistency(
            baseline,
            x_train,
            y_train,
            x_stress,
            y_stress,
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
