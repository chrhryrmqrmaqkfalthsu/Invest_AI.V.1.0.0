"""Deterministic process-parallel GA adapter for Stage 3 entry scope.

후보 생성·selection·crossover·mutation의 난수 소비는 부모 프로세스에서
기존 순서대로 수행한다. fitness 평가만 fork worker에 분산하고 입력 index
순서대로 병합해 worker 완료 순서가 GA 결과에 영향을 주지 않도록 한다.
기본 evaluation_workers=1이며 기존 genetic.run_ga와 동일한 legacy 동작을
유지한다.
"""
from __future__ import annotations

import copy
import multiprocessing as mp
import random
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Mapping, Optional

import numpy as np

from engine.core.config import config
from engine.core.logger import get_logger
from engine.core.metadata import compute_rulebook_hash
from engine.learning import genetic as base
from engine.strategies.rulebook import ENTRY_INTERVAL_SPECS, Rulebook

log = get_logger("ga_parallel")

_WORKER_EVALUATE_FN: Callable[[Rulebook], float] | None = None
_WORKER_GENE_SCOPE: str = "legacy"
_WORKER_ENTRY_CTX: Any = None


def _worker_evaluate(task: tuple[int, Rulebook, str]) -> tuple[int, Rulebook, float]:
    index, rulebook, stage = task
    if _WORKER_EVALUATE_FN is None:
        raise RuntimeError("parallel evaluate function is not initialized")
    fitness = base._evaluate_candidate(
        rulebook,
        _WORKER_EVALUATE_FN,
        gene_scope=_WORKER_GENE_SCOPE,
        entry_ctx=_WORKER_ENTRY_CTX,
        stage=stage,
    )
    rulebook.fitness = float(fitness)
    return index, rulebook, float(fitness)


def _interval_widths(rulebook: Rulebook) -> dict[str, float]:
    widths: dict[str, float] = {}
    for feature, spec in ENTRY_INTERVAL_SPECS.items():
        low = float(getattr(rulebook, spec["low_field"]))
        high = float(getattr(rulebook, spec["high_field"]))
        widths[feature] = float(high - low)
    return widths


def _mutate_with_trace(
    rulebook: Rulebook,
    mutation_rate: float,
    mutation_strength: float,
    *,
    gene_scope: str,
    entry_ctx: Any,
) -> Rulebook:
    before = _interval_widths(rulebook) if gene_scope == "entry" else {}
    child = base.mutate(
        rulebook,
        mutation_rate,
        mutation_strength,
        gene_scope=gene_scope,
        entry_feature_domain=entry_ctx,
    )
    if gene_scope != "entry":
        return child
    after = _interval_widths(child)
    applied = dict(getattr(child, "_entry_exit_mutation_applied", {}) or {})
    direction = str(applied.get("direction") or "none")
    movements: dict[str, dict[str, Any]] = {}
    for feature in ENTRY_INTERVAL_SPECS:
        delta = float(after[feature] - before[feature])
        aligned = (
            (direction == "later" and delta > 1e-12)
            or (direction == "earlier" and delta < -1e-12)
        )
        movements[feature] = {
            "before_width": float(before[feature]),
            "after_width": float(after[feature]),
            "delta_width": delta,
            "aligned_with_hint": bool(aligned),
        }
    applied["width_movements"] = movements
    applied["fitness_input"] = False
    setattr(child, "_entry_exit_mutation_applied", applied)
    return child


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if np.isfinite(number) else float(default)


def _population_summary(population: list[Rulebook], generation: int) -> dict[str, Any]:
    fitnesses = [_safe_float(getattr(rb, "fitness", 0.0)) for rb in population]
    hint_counts = {"earlier": 0, "later": 0, "none": 0}
    applied_counts = {"earlier": 0, "later": 0, "none": 0}
    alignment = {
        "earlier_total_feature_moves": 0,
        "earlier_aligned_feature_moves": 0,
        "later_total_feature_moves": 0,
        "later_aligned_feature_moves": 0,
    }
    width_values = {feature: [] for feature in ENTRY_INTERVAL_SPECS}
    disqualified = 0
    mae_penalized = 0
    mae_penalties: list[float] = []
    primary_values: list[float] = []
    win_rates: list[float] = []

    for rb in population:
        hint = dict(getattr(rb, "_entry_exit_mutation_hint", {}) or {})
        hint_direction = str(hint.get("direction") or "none")
        if hint_direction not in hint_counts:
            hint_direction = "none"
        hint_counts[hint_direction] += 1

        applied = dict(getattr(rb, "_entry_exit_mutation_applied", {}) or {})
        applied_direction = str(applied.get("direction") or "none")
        if applied_direction not in applied_counts:
            applied_direction = "none"
        applied_counts[applied_direction] += 1
        if applied_direction in {"earlier", "later"}:
            movements = dict(applied.get("width_movements") or {})
            total_key = f"{applied_direction}_total_feature_moves"
            aligned_key = f"{applied_direction}_aligned_feature_moves"
            for movement in movements.values():
                delta = _safe_float((movement or {}).get("delta_width"))
                if abs(delta) <= 1e-12:
                    continue
                alignment[total_key] += 1
                if bool((movement or {}).get("aligned_with_hint")):
                    alignment[aligned_key] += 1

        diagnostics = dict(getattr(rb, "_entry_fitness_diagnostics", {}) or {})
        if diagnostics:
            if bool(diagnostics.get("disqualified")):
                disqualified += 1
            penalty = max(_safe_float(diagnostics.get("mae_penalty")), 0.0)
            if penalty > 0.0:
                mae_penalized += 1
                mae_penalties.append(penalty)
            primary_values.append(_safe_float(diagnostics.get("primary_objective_pct_per_day")))
            win_rates.append(_safe_float(diagnostics.get("win_rate_pct")))

        for feature, width in _interval_widths(rb).items():
            width_values[feature].append(width)

    best = max(population, key=lambda item: _safe_float(getattr(item, "fitness", float("-inf"))))
    return {
        "generation": int(generation),
        "population_count": len(population),
        "best_hash": compute_rulebook_hash(best),
        "best_fitness": max(fitnesses) if fitnesses else None,
        "mean_fitness": float(np.mean(fitnesses)) if fitnesses else None,
        "median_fitness": float(np.median(fitnesses)) if fitnesses else None,
        "entry_fitness": {
            "diagnostic_count": len(primary_values),
            "win_gate_disqualified_count": int(disqualified),
            "win_gate_pass_count": int(len(primary_values) - disqualified),
            "mae_penalized_count": int(mae_penalized),
            "mean_mae_penalty_among_penalized": float(np.mean(mae_penalties)) if mae_penalties else 0.0,
            "mean_primary_objective_pct_per_day": float(np.mean(primary_values)) if primary_values else None,
            "mean_win_rate_pct": float(np.mean(win_rates)) if win_rates else None,
        },
        "mutation_hint_direction_counts": hint_counts,
        "mutation_applied_direction_counts": applied_counts,
        "mutation_width_alignment": {
            **alignment,
            "earlier_alignment_rate": (
                alignment["earlier_aligned_feature_moves"] / alignment["earlier_total_feature_moves"]
                if alignment["earlier_total_feature_moves"]
                else None
            ),
            "later_alignment_rate": (
                alignment["later_aligned_feature_moves"] / alignment["later_total_feature_moves"]
                if alignment["later_total_feature_moves"]
                else None
            ),
        },
        "mean_interval_widths": {
            feature: float(np.mean(values)) if values else None
            for feature, values in width_values.items()
        },
    }


def _evaluate_batch(
    population: list[Rulebook],
    *,
    evaluate_fn: Callable[[Rulebook], float],
    gene_scope: str,
    entry_ctx: Any,
    stages: list[str],
    executor: ProcessPoolExecutor | None,
) -> list[Rulebook]:
    if len(population) != len(stages):
        raise ValueError("population/stages length mismatch")
    if executor is None:
        evaluated: list[Rulebook] = []
        for rb, stage in zip(population, stages):
            rb.fitness = base._evaluate_candidate(
                rb,
                evaluate_fn,
                gene_scope=gene_scope,
                entry_ctx=entry_ctx,
                stage=stage,
            )
            evaluated.append(rb)
        return evaluated

    tasks = [(index, rb, stages[index]) for index, rb in enumerate(population)]
    results = list(executor.map(_worker_evaluate, tasks, chunksize=1))
    results.sort(key=lambda row: row[0])
    return [row[1] for row in results]


def run_ga(
    base_rulebook: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    ga_config: Optional[base.GAConfig] = None,
    seed_rulebooks: Optional[list] = None,
    on_generation: Optional[Callable[[int, Rulebook, float], None]] = None,
    *,
    gene_scope: str = "legacy",
    entry_feature_domain: Mapping[str, Any] | None = None,
    evaluation_workers: int = 1,
) -> base.GAResult:
    """기존 GA와 동일하되 fitness evaluation만 deterministic fork 병렬화한다."""
    global _WORKER_EVALUATE_FN, _WORKER_GENE_SCOPE, _WORKER_ENTRY_CTX

    scope = base._normalize_gene_scope(gene_scope)
    entry_ctx = (
        base._normalize_entry_feature_domain(entry_feature_domain)
        if scope == "entry"
        else None
    )
    cfg = ga_config or base.GAConfig(
        population=config.get("learning.population", 40),
        generations=config.get("learning.generations", 25),
        elite_ratio=config.get("learning.elite_ratio", 0.2),
        mutation_rate=config.get("learning.mutation_rate", 0.15),
        seed_pattern_ratio=config.get("learning.seed_pattern_ratio", 0.33),
    )
    workers = max(1, min(int(evaluation_workers or 1), int(cfg.population)))
    if cfg.random_seed is not None:
        random.seed(cfg.random_seed)
        np.random.seed(cfg.random_seed)

    population: list[Rulebook] = []
    seed_count = int(cfg.population * cfg.seed_pattern_ratio)
    if seed_rulebooks:
        for seed in seed_rulebooks[:seed_count]:
            if scope == "entry":
                if entry_ctx is None:
                    raise RuntimeError("entry domain context missing for seed")
                candidate = base._prepare_entry_seed(seed, base_rulebook, entry_ctx)
            else:
                candidate = copy.deepcopy(seed)
                candidate.ticker = base_rulebook.ticker
                candidate.asset_type = base_rulebook.asset_type
                candidate.direction = base_rulebook.direction
                candidate.sector_name = base_rulebook.sector_name
                candidate = base.mutate(candidate, mutation_rate=0.1, strength=0.1)
            population.append(candidate)
    while len(population) < cfg.population:
        population.append(
            base.random_rulebook(
                base_rulebook,
                gene_scope=scope,
                entry_feature_domain=entry_ctx,
            )
        )

    executor: ProcessPoolExecutor | None = None
    if workers > 1:
        if "fork" not in mp.get_all_start_methods():
            raise RuntimeError("deterministic GA population parallelism requires fork start method")
        _WORKER_EVALUATE_FN = evaluate_fn
        _WORKER_GENE_SCOPE = scope
        _WORKER_ENTRY_CTX = entry_ctx
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"))

    population_history: list[dict[str, Any]] = []
    fitness_history: list = []
    try:
        population = _evaluate_batch(
            population,
            evaluate_fn=evaluate_fn,
            gene_scope=scope,
            entry_ctx=entry_ctx,
            stages=[f"initial-evaluate-{index}" for index in range(len(population))],
            executor=executor,
        )
        population_history.append(_population_summary(population, 0))
        best_overall = copy.deepcopy(max(population, key=lambda item: item.fitness))
        no_improve = 0

        for generation in range(1, cfg.generations + 1):
            population.sort(key=lambda item: item.fitness, reverse=True)
            best = population[0]
            average = float(np.mean([rulebook.fitness for rulebook in population]))
            fitness_history.append((generation, best.fitness, average))
            log.info(f"Gen {generation:2d}: best={best.fitness:.6f}, avg={average:.6f}, workers={workers}")
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
                if scope == "entry":
                    if entry_ctx is None:
                        raise RuntimeError("entry domain context missing for elite")
                    try:
                        base._require_valid_entry_candidate(
                            candidate,
                            entry_ctx,
                            stage=f"elite-{generation}-{elite_index}",
                        )
                    except ValueError:
                        candidate = base.random_rulebook(
                            base_rulebook,
                            gene_scope=scope,
                            entry_feature_domain=entry_ctx,
                        )
                        candidate = _evaluate_batch(
                            [candidate],
                            evaluate_fn=evaluate_fn,
                            gene_scope=scope,
                            entry_ctx=entry_ctx,
                            stages=[f"elite-regenerated-{generation}-{elite_index}"],
                            executor=executor,
                        )[0]
                elites.append(candidate)

            children: list[Rulebook] = []
            while len(elites) + len(children) < cfg.population:
                parent_1 = base.tournament_select(population, cfg.tournament_size)
                parent_2 = base.tournament_select(population, cfg.tournament_size)
                child = base.crossover(
                    parent_1,
                    parent_2,
                    gene_scope=scope,
                    entry_feature_domain=entry_ctx,
                )
                child = _mutate_with_trace(
                    child,
                    cfg.mutation_rate,
                    cfg.mutation_strength,
                    gene_scope=scope,
                    entry_ctx=entry_ctx,
                )
                children.append(child)

            children = _evaluate_batch(
                children,
                evaluate_fn=evaluate_fn,
                gene_scope=scope,
                entry_ctx=entry_ctx,
                stages=[f"offspring-evaluate-{generation}-{elite_count + index}" for index in range(len(children))],
                executor=executor,
            )
            population = elites + children
            population_history.append(_population_summary(population, generation))

        if scope == "entry":
            if entry_ctx is None:
                raise RuntimeError("entry domain context missing for final result")
            base._require_valid_entry_candidate(best_overall, entry_ctx, stage="best-overall")
            for index, rulebook in enumerate(population):
                base._require_valid_entry_candidate(rulebook, entry_ctx, stage=f"final-population-{index}")

        result = base.GAResult(
            best=best_overall,
            fitness_history=fitness_history,
            final_population=population,
            generations_run=len(fitness_history),
        )
        result.population_diagnostics_history = population_history
        result.evaluation_workers = workers
        result.parallel_axis = "population_fitness_evaluation"
        result.parallel_merge_order = "input_index_order"
        result.random_generation_location = "parent_process_only"
        return result
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)
        _WORKER_EVALUATE_FN = None
        _WORKER_GENE_SCOPE = "legacy"
        _WORKER_ENTRY_CTX = None


def reproducibility_probe(
    base_rulebook: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    ga_config: base.GAConfig,
) -> dict[str, Any]:
    sequential = run_ga(
        base_rulebook,
        evaluate_fn,
        ga_config=copy.deepcopy(ga_config),
        gene_scope="legacy",
        evaluation_workers=1,
    )
    parallel = run_ga(
        base_rulebook,
        evaluate_fn,
        ga_config=copy.deepcopy(ga_config),
        gene_scope="legacy",
        evaluation_workers=6,
    )
    seq_population = [
        (compute_rulebook_hash(rb), float(rb.fitness))
        for rb in sequential.final_population
    ]
    par_population = [
        (compute_rulebook_hash(rb), float(rb.fitness))
        for rb in parallel.final_population
    ]
    passed = (
        compute_rulebook_hash(sequential.best) == compute_rulebook_hash(parallel.best)
        and sequential.fitness_history == parallel.fitness_history
        and seq_population == par_population
    )
    return {
        "passed": bool(passed),
        "sequential_best_hash": compute_rulebook_hash(sequential.best),
        "parallel_best_hash": compute_rulebook_hash(parallel.best),
        "fitness_history_equal": sequential.fitness_history == parallel.fitness_history,
        "final_population_equal": seq_population == par_population,
        "parallel_axis": "population_fitness_evaluation",
        "workers": 6,
        "merge_order": "input_index_order",
        "random_generation_location": "parent_process_only",
    }
