"""Portable deterministic process-parallel GA for Linux and Windows.

RNG-consuming GA operations remain in the parent process.  Child processes only
run candidate fitness evaluation.  Results are merged by candidate input index,
never completion order.  Linux uses ``fork``; Windows uses ``spawn`` with a
cloudpickle initializer so nested evaluation functions and immutable context are
installed once per local worker process.
"""
from __future__ import annotations

import copy
import multiprocessing as mp
import random
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Mapping, Optional

import cloudpickle
import numpy as np

from engine.core.config import config
from engine.core.logger import get_logger
from engine.core.metadata import compute_rulebook_hash
from engine.learning import genetic as base
from engine.learning import genetic_parallel as original
from engine.strategies.rulebook import Rulebook

log = get_logger("ga_parallel_portable")

_WORKER_EVALUATE_FN: Callable[[Rulebook], float] | None = None
_WORKER_GENE_SCOPE: str = "legacy"
_WORKER_ENTRY_CTX: Any = None


def _worker_init(evaluate_payload: bytes, gene_scope: str, entry_ctx_payload: bytes) -> None:
    global _WORKER_EVALUATE_FN, _WORKER_GENE_SCOPE, _WORKER_ENTRY_CTX
    _WORKER_EVALUATE_FN = cloudpickle.loads(evaluate_payload)
    _WORKER_GENE_SCOPE = str(gene_scope)
    _WORKER_ENTRY_CTX = cloudpickle.loads(entry_ctx_payload)


def _worker_evaluate(task: tuple[int, Rulebook, str]) -> tuple[int, Rulebook, float]:
    index, rulebook, stage = task
    if _WORKER_EVALUATE_FN is None:
        raise RuntimeError("portable parallel evaluate function is not initialized")
    fitness = base._evaluate_candidate(
        rulebook,
        _WORKER_EVALUATE_FN,
        gene_scope=_WORKER_GENE_SCOPE,
        entry_ctx=_WORKER_ENTRY_CTX,
        stage=stage,
    )
    rulebook.fitness = float(fitness)
    return index, rulebook, float(fitness)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if np.isfinite(number) else float(default)


def _population_summary(population: list[Rulebook], generation: int) -> dict[str, Any]:
    summary = dict(original._population_summary(population, generation))
    diagnostics = [
        dict(getattr(rb, "_entry_fitness_diagnostics", {}) or {})
        for rb in population
    ]
    diagnostics = [row for row in diagnostics if row]
    trade_count_fail = sum(not bool(row.get("trade_count_gate_pass")) for row in diagnostics)
    win_only_fail = sum(
        bool(row.get("trade_count_gate_pass"))
        and not bool(row.get("win_rate_threshold_pass"))
        for row in diagnostics
    )
    both_pass = sum(
        bool(row.get("trade_count_gate_pass"))
        and bool(row.get("win_rate_threshold_pass"))
        for row in diagnostics
    )
    realized_values = [max(_safe_float(row.get("realized_loss_penalty")), 0.0) for row in diagnostics]
    realized_positive = [value for value in realized_values if value > 0.0]
    mae_values = [max(_safe_float(row.get("mae_penalty")), 0.0) for row in diagnostics]
    mae_positive = [value for value in mae_values if value > 0.0]
    gate_before = [_safe_float(row.get("fitness_before_entry_gate")) for row in diagnostics]
    final_values = [_safe_float(row.get("final_fitness")) for row in diagnostics]
    trade_counts = [int(_safe_float(row.get("trade_count"), 0.0)) for row in diagnostics]
    entry = dict(summary.get("entry_fitness") or {})
    entry.update(
        {
            "trade_count_below_12_count": int(trade_count_fail),
            "trade_count_met_but_win_rate_below_60_count": int(win_only_fail),
            "both_entry_gates_pass_count": int(both_pass),
            "realized_loss_penalized_count": len(realized_positive),
            "realized_loss_penalized_rate": (
                len(realized_positive) / len(diagnostics) if diagnostics else 0.0
            ),
            "mean_realized_loss_penalty_among_penalized": (
                float(np.mean(realized_positive)) if realized_positive else 0.0
            ),
            "mean_realized_loss_penalty_all": (
                float(np.mean(realized_values)) if realized_values else 0.0
            ),
            "mae_penalized_rate": len(mae_positive) / len(diagnostics) if diagnostics else 0.0,
            "mean_fitness_before_entry_gate": float(np.mean(gate_before)) if gate_before else None,
            "mean_final_fitness": float(np.mean(final_values)) if final_values else None,
            "trade_count_min": min(trade_counts) if trade_counts else None,
            "trade_count_median": float(np.median(trade_counts)) if trade_counts else None,
            "trade_count_max": max(trade_counts) if trade_counts else None,
            "trade_count_12_13_count": sum(value in {12, 13} for value in trade_counts),
            "trade_count_12_13_rate": (
                sum(value in {12, 13} for value in trade_counts) / len(trade_counts)
                if trade_counts
                else 0.0
            ),
        }
    )
    summary["entry_fitness"] = entry
    summary["process_start_method"] = "spawn" if mp.get_start_method(allow_none=True) == "spawn" else None
    return summary


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
    """Run deterministic parent-RNG GA with local process-only fitness workers."""
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
    start_method = "fork" if "fork" in mp.get_all_start_methods() else "spawn"
    if workers > 1:
        evaluate_payload = cloudpickle.dumps(evaluate_fn)
        entry_ctx_payload = cloudpickle.dumps(entry_ctx)
        executor = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context(start_method),
            initializer=_worker_init,
            initargs=(evaluate_payload, scope, entry_ctx_payload),
        )

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
            log.info(
                f"Gen {generation:2d}: best={best.fitness:.6f}, avg={average:.6f}, "
                f"workers={workers}, start={start_method}"
            )
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
                child = original._mutate_with_trace(
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
                stages=[
                    f"offspring-evaluate-{generation}-{elite_count + index}"
                    for index in range(len(children))
                ],
                executor=executor,
            )
            population = elites + children
            population_history.append(_population_summary(population, generation))

        if scope == "entry":
            if entry_ctx is None:
                raise RuntimeError("entry domain context missing for final result")
            base._require_valid_entry_candidate(best_overall, entry_ctx, stage="best-overall")
            for index, rulebook in enumerate(population):
                base._require_valid_entry_candidate(
                    rulebook,
                    entry_ctx,
                    stage=f"final-population-{index}",
                )

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
        result.process_start_method = start_method
        return result
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)


def reproducibility_probe(
    base_rulebook: Rulebook,
    evaluate_fn: Callable[[Rulebook], float],
    ga_config: base.GAConfig,
    *,
    parallel_workers: int = 6,
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
        evaluation_workers=parallel_workers,
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
        "workers": int(parallel_workers),
        "merge_order": "input_index_order",
        "random_generation_location": "parent_process_only",
        "process_start_method": "fork" if "fork" in mp.get_all_start_methods() else "spawn",
    }
