"""Pair-preserving GA operators for the strict-AND interval redesign."""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from math import isfinite
from typing import Callable, Optional

import numpy as np

from engine.core.config import config
from engine.core.logger import get_logger
from engine.core.metadata import compute_rulebook_hash
from engine.strategies.rulebook import (
    CATEGORICAL_PARAMS,
    ENTRY_FEATURES,
    FIXED_MAX_HOLDING_DAYS,
    INTERVAL_DOMAIN_HIGH,
    INTERVAL_DOMAIN_LOW,
    MAX_NEAR_FULL_INTERVALS,
    MIN_INTERVAL_WIDTH,
    NEAR_FULL_INTERVAL_WIDTH,
    PARAM_RANGES,
    Rulebook,
    canonical_entry_intervals,
    validate_entry_intervals,
    validate_rulebook_intervals,
)

log = get_logger("strict_interval_ga")
_INTERVAL_EPS = 1e-9


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


def collect_top_rulebooks(ga_result: GAResult, n: int) -> list[Rulebook]:
    try:
        limit = int(n)
    except Exception:
        limit = 0
    if ga_result is None or limit <= 0:
        return []
    candidates = [getattr(ga_result, "best", None)] + list(getattr(ga_result, "final_population", []) or [])
    by_hash: dict[str, Rulebook] = {}
    for rulebook in candidates:
        if rulebook is None:
            continue
        key = compute_rulebook_hash(rulebook)
        current = by_hash.get(key)
        if current is None or float(rulebook.fitness) > float(current.fitness):
            by_hash[key] = rulebook
    ranked = sorted(by_hash.items(), key=lambda item: (-float(item[1].fitness), item[0]))
    return [rulebook for _, rulebook in ranked[:limit]]


def _rand_in(low: float, high: float) -> float:
    return float(random.uniform(float(low), float(high)))


def _random_interval() -> dict[str, float]:
    target_min = MIN_INTERVAL_WIDTH + _INTERVAL_EPS
    width = random.uniform(target_min, 0.85)
    low = random.uniform(INTERVAL_DOMAIN_LOW, INTERVAL_DOMAIN_HIGH - width)
    return {"low": float(low), "high": float(low + width)}


def _repair_interval_pair(low: object, high: object) -> dict[str, float]:
    try:
        low_value = float(low)
        high_value = float(high)
    except (TypeError, ValueError) as exc:
        raise ValueError("open_or_nonfinite_bound") from exc
    if not isfinite(low_value) or not isfinite(high_value):
        raise ValueError("open_or_nonfinite_bound")

    low_value = max(INTERVAL_DOMAIN_LOW, min(INTERVAL_DOMAIN_HIGH, low_value))
    high_value = max(INTERVAL_DOMAIN_LOW, min(INTERVAL_DOMAIN_HIGH, high_value))
    if low_value > high_value:
        low_value, high_value = high_value, low_value

    target_width = MIN_INTERVAL_WIDTH + _INTERVAL_EPS
    if high_value - low_value < target_width:
        center = (low_value + high_value) / 2.0
        low_value = center - target_width / 2.0
        high_value = center + target_width / 2.0
        if low_value < INTERVAL_DOMAIN_LOW:
            low_value = INTERVAL_DOMAIN_LOW
            high_value = target_width
        if high_value > INTERVAL_DOMAIN_HIGH:
            high_value = INTERVAL_DOMAIN_HIGH
            low_value = INTERVAL_DOMAIN_HIGH - target_width
    return {"low": float(low_value), "high": float(high_value)}


def _generate_interval_chromosome() -> dict[str, dict[str, float]]:
    for _ in range(100):
        chromosome = {feature: _random_interval() for feature in ENTRY_FEATURES}
        valid, _ = validate_entry_intervals(chromosome)
        if valid:
            return canonical_entry_intervals(chromosome)
    raise RuntimeError("failed to generate valid interval chromosome")


def _narrow_near_full_ranges(intervals: dict[str, dict[str, float]]) -> None:
    near_full = [
        feature
        for feature in ENTRY_FEATURES
        if intervals[feature]["high"] - intervals[feature]["low"] >= NEAR_FULL_INTERVAL_WIDTH
    ]
    for feature in near_full[MAX_NEAR_FULL_INTERVALS:]:
        intervals[feature] = _random_interval()


def _mutate_interval_chromosome(
    intervals: dict[str, dict[str, float]],
    *,
    mutation_rate: float,
    strength: float,
) -> dict[str, dict[str, float]]:
    mutated = canonical_entry_intervals(intervals)
    sigma = max(0.001, float(strength))
    for feature in ENTRY_FEATURES:
        if random.random() >= float(mutation_rate):
            continue
        if random.random() < 0.15:
            mutated[feature] = _random_interval()
            continue
        pair = mutated[feature]
        mutated[feature] = _repair_interval_pair(
            pair["low"] + random.gauss(0.0, sigma),
            pair["high"] + random.gauss(0.0, sigma),
        )
    _narrow_near_full_ranges(mutated)
    valid, reason = validate_entry_intervals(mutated)
    if not valid:
        raise ValueError(f"invalid mutated interval chromosome: {reason}")
    return canonical_entry_intervals(mutated)


def _crossover_interval_chromosome(
    parent1: dict[str, dict[str, float]],
    parent2: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    first = canonical_entry_intervals(parent1)
    second = canonical_entry_intervals(parent2)
    child = {
        feature: copy.deepcopy(first[feature] if random.random() < 0.5 else second[feature])
        for feature in ENTRY_FEATURES
    }
    return canonical_entry_intervals(child)


def _finalize_rulebook_genes(rulebook: Rulebook) -> Rulebook:
    rulebook.entry_intervals = canonical_entry_intervals(rulebook.entry_intervals)
    rulebook.max_holding_days = FIXED_MAX_HOLDING_DAYS
    rulebook.exit_strategy = "strict_interval"
    rulebook.take_profit_atr = 1_000_000.0
    rulebook.trailing_atr = 1_000_000.0
    rulebook.trailing_activation_profit_pct = 1_000_000.0
    rulebook.add_buy_enabled = False
    rulebook.breakeven_enabled = False
    rulebook.sell_omen_enabled = False
    rulebook.crash_buy_enabled = False
    rulebook.use_news_global = False
    rulebook.use_event_block = False
    valid, reason = validate_rulebook_intervals(rulebook)
    if not valid:
        raise ValueError(f"invalid strict interval rulebook: {reason}")
    return rulebook


def random_rulebook(base: Rulebook) -> Rulebook:
    rulebook = copy.deepcopy(base)
    rulebook.entry_intervals = _generate_interval_chromosome()
    for name, (low, high) in PARAM_RANGES.items():
        if hasattr(rulebook, name):
            setattr(rulebook, name, _rand_in(low, high))
    for name, choices in CATEGORICAL_PARAMS.items():
        if hasattr(rulebook, name):
            setattr(rulebook, name, random.choice(choices))
    return _finalize_rulebook_genes(rulebook)


def mutate(rulebook: Rulebook, mutation_rate: float, strength: float) -> Rulebook:
    child = copy.deepcopy(rulebook)
    child.entry_intervals = _mutate_interval_chromosome(
        child.entry_intervals,
        mutation_rate=mutation_rate,
        strength=strength,
    )
    for name, (low, high) in PARAM_RANGES.items():
        if random.random() < mutation_rate and hasattr(child, name):
            current = float(getattr(child, name))
            sigma = (high - low) * strength
            setattr(child, name, float(max(low, min(high, current + random.gauss(0.0, sigma)))))
    for name, choices in CATEGORICAL_PARAMS.items():
        if random.random() < mutation_rate / 2.0 and hasattr(child, name):
            setattr(child, name, random.choice(choices))
    return _finalize_rulebook_genes(child)


def crossover(parent1: Rulebook, parent2: Rulebook) -> Rulebook:
    child = copy.deepcopy(parent1)
    child.entry_intervals = _crossover_interval_chromosome(parent1.entry_intervals, parent2.entry_intervals)
    for name in PARAM_RANGES:
        if hasattr(child, name) and random.random() < 0.5:
            setattr(child, name, getattr(parent2, name))
    for name in CATEGORICAL_PARAMS:
        if hasattr(child, name) and random.random() < 0.5:
            setattr(child, name, getattr(parent2, name))
    return _finalize_rulebook_genes(child)


def validate_population_intervals(population: list[Rulebook]) -> dict[str, object]:
    reasons: dict[str, int] = {}
    invalid = 0
    for rulebook in population:
        valid, reason = validate_rulebook_intervals(rulebook)
        if not valid:
            invalid += 1
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "population": len(population),
        "invalid_count": invalid,
        "valid_count": len(population) - invalid,
        "invalid_reasons": reasons,
        "passed": invalid == 0,
    }


def tournament_select(population: list[Rulebook], k: int) -> Rulebook:
    return max(random.sample(population, min(k, len(population))), key=lambda item: item.fitness)


def run_ga(
    base_rulebook: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    ga_config: Optional[GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable[[int, Rulebook, float], None]] = None,
) -> GAResult:
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
            rulebook = Rulebook.from_dict(seed.to_dict() if hasattr(seed, "to_dict") else vars(seed))
            rulebook.ticker = base_rulebook.ticker
            rulebook.asset_type = base_rulebook.asset_type
            rulebook.direction = base_rulebook.direction
            rulebook.sector_name = base_rulebook.sector_name
            population.append(mutate(rulebook, mutation_rate=0.10, strength=0.10))
    while len(population) < cfg.population:
        population.append(random_rulebook(base_rulebook))

    gate = validate_population_intervals(population)
    if not gate["passed"]:
        raise RuntimeError(f"initial interval gate failed: {gate}")
    for rulebook in population:
        rulebook.fitness = float(evaluate_fn(rulebook))

    history: list[tuple[int, float, float]] = []
    best_overall = copy.deepcopy(max(population, key=lambda item: item.fitness))
    no_improve = 0
    for generation in range(1, cfg.generations + 1):
        population.sort(key=lambda item: item.fitness, reverse=True)
        best = population[0]
        average = float(np.mean([item.fitness for item in population]))
        history.append((generation, float(best.fitness), average))
        log.info("Gen %2d: best=%.6f, avg=%.6f", generation, best.fitness, average)
        if on_generation:
            on_generation(generation, best, average)
        if best.fitness > best_overall.fitness:
            best_overall = copy.deepcopy(best)
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= cfg.early_stop_no_improve:
                break

        elite_count = max(1, int(cfg.population * cfg.elite_ratio))
        new_population = [copy.deepcopy(item) for item in population[:elite_count]]
        while len(new_population) < cfg.population:
            child = crossover(
                tournament_select(population, cfg.tournament_size),
                tournament_select(population, cfg.tournament_size),
            )
            child = mutate(child, cfg.mutation_rate, cfg.mutation_strength)
            child.fitness = float(evaluate_fn(child))
            new_population.append(child)
        gate = validate_population_intervals(new_population)
        if not gate["passed"]:
            raise RuntimeError(f"generation interval gate failed: {gate}")
        population = new_population

    return GAResult(
        best=best_overall,
        fitness_history=history,
        final_population=population,
        generations_run=len(history),
    )
