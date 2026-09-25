#!/usr/bin/env python3
"""Run the official Dask GA with a dedicated parent-only RNG stream.

Dask client submission with ``pure=False`` consumes the process-global Python
``random`` state while creating task keys. Different worker configurations use
different wave counts, so sharing that global module changes later
selection/crossover/mutation even when every fitness result is bit-identical.

For each A/B GA call this launcher replaces ``engine.learning.genetic.random``
with a fresh ``random.Random(seed)`` instance. Dask background activity cannot
access that object. Worker helpers are also carried by value so the scheduler
does not need the research runner on its import path.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import types
from pathlib import Path
from typing import Any, Mapping

import cloudpickle
import numpy as np

HERE = Path(__file__).resolve()
TARGET = HERE.with_name("run_dask_worker_mix_ga_official.py")
MODULE_NAME = "_official_dask_mix_rng_guard_runtime"
ELITE_RATIO = 0.2


def worker_task(
    task_id: str,
    envelope: Mapping[str, Any],
    payload: Mapping[str, Any],
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    from engine.learning.dask_process_fitness import evaluate_via_worker_pool

    return evaluate_via_worker_pool(
        task_id,
        envelope,
        payload,
        payload_key=payload_key,
        max_workers=max_workers,
    )


def worker_warmup(
    payload: Mapping[str, Any],
    payload_key: str,
    max_workers: int,
) -> dict[str, Any]:
    from engine.learning.dask_process_fitness import warmup_worker_pool

    return warmup_worker_pool(
        payload,
        payload_key=payload_key,
        max_workers=max_workers,
    )


def worker_status() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import worker_pool_status

    return worker_pool_status()


def worker_shutdown() -> dict[str, Any]:
    from engine.learning.dask_process_fitness import shutdown_worker_pool

    return shutdown_worker_pool()


def load_official_module() -> types.ModuleType:
    module = types.ModuleType(MODULE_NAME)
    module.__file__ = str(TARGET)
    module.__package__ = None
    sys.modules[MODULE_NAME] = module
    source = TARGET.read_text(encoding="utf-8")
    exec(compile(source, str(TARGET), "exec"), module.__dict__)
    cloudpickle.register_pickle_by_value(module)
    return module


def install_scheduler_safe_helpers(module: types.ModuleType) -> None:
    module._worker_pool_task = worker_task
    module._worker_pool_warmup = worker_warmup
    module._worker_pool_status = worker_status
    module._worker_pool_shutdown = worker_shutdown


def install_evaluation_rng_guard(module: types.ModuleType) -> None:
    original = module._evaluate_batch_pool

    def guarded_evaluate_batch(*args: Any, **kwargs: Any) -> Any:
        python_state = random.getstate()
        numpy_state = np.random.get_state()
        try:
            return original(*args, **kwargs)
        finally:
            random.setstate(python_state)
            np.random.set_state(numpy_state)

    guarded_evaluate_batch.__name__ = "_evaluate_batch_pool_rng_guarded"
    guarded_evaluate_batch.__qualname__ = guarded_evaluate_batch.__name__
    module._evaluate_batch_pool = guarded_evaluate_batch


def install_dedicated_ga_rng(module: types.ModuleType) -> None:
    original_run_ga = module.BASE._run_ga

    def dedicated_run_ga(*args: Any, **kwargs: Any) -> Any:
        from engine.learning import genetic
        from engine.learning import genetic_parallel

        original_genetic_random = genetic.random
        original_parallel_random = genetic_parallel.random
        dedicated = random.Random(int(module.SEED))
        genetic.random = dedicated
        genetic_parallel.random = dedicated
        try:
            return original_run_ga(*args, **kwargs)
        finally:
            genetic.random = original_genetic_random
            genetic_parallel.random = original_parallel_random

    dedicated_run_ga.__name__ = "_run_ga_with_dedicated_parent_rng"
    dedicated_run_ga.__qualname__ = dedicated_run_ga.__name__
    module.BASE._run_ga = dedicated_run_ga


def expected_evaluation_count(population: int, generations: int) -> int:
    elite_count = max(1, int(population * ELITE_RATIO))
    offspring_count = population - elite_count
    return population + offspring_count * generations


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Official Dask GA with dedicated parent RNG")
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    parser.add_argument("--population", type=int, default=100)
    parser.add_argument("--generations", type=int, default=40)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.population <= 1 or args.generations <= 0:
        raise ValueError("population must be >1 and generations must be >0")

    module = load_official_module()
    install_scheduler_safe_helpers(module)
    install_evaluation_rng_guard(module)
    install_dedicated_ga_rng(module)
    module.POPULATION = int(args.population)
    module.GENERATIONS = int(args.generations)
    module.EXPECTED_EVALUATIONS = expected_evaluation_count(
        module.POPULATION,
        module.GENERATIONS,
    )
    os.environ["KINGMAKER_DASK_PARENT_RNG_GUARD"] = "dedicated-random-instance-v1"
    result = module.run(args.scheduler)
    result.setdefault("config", {})["parent_rng_guard"] = {
        "enabled": True,
        "mode": "dedicated_random.Random_per_ga_run",
        "seed": int(module.SEED),
        "scope": "candidate generation, tournament selection, crossover, mutation",
        "python_global_random_isolated": True,
        "evaluation_batch_state_restored": True,
        "numpy_random_state_restored": True,
        "reason": "Dask submission consumes process-global Python random state",
    }
    print(module.json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
