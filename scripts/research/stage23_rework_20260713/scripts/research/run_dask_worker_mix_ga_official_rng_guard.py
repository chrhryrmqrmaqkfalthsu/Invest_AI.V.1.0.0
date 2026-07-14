#!/usr/bin/env python3
"""Run the official Dask GA with strict parent RNG isolation.

Dask client submission with ``pure=False`` consumes the process-global Python
``random`` state while creating task keys.  Different worker configurations use
different wave counts, so without a guard the GA's later crossover/mutation RNG
stream diverges even when every fitness result is bit-identical.

This launcher loads the official runner by value, wraps every distributed
fitness batch with Python and NumPy RNG save/restore, and then invokes the same
runner.  Candidate generation, selection, crossover and mutation therefore see
the exact same parent RNG stream in A and B.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import types
from pathlib import Path
from typing import Any

import cloudpickle
import numpy as np

HERE = Path(__file__).resolve()
TARGET = HERE.with_name("run_dask_worker_mix_ga_official.py")
MODULE_NAME = "_official_dask_mix_rng_guard_runtime"


def load_official_module() -> types.ModuleType:
    module = types.ModuleType(MODULE_NAME)
    module.__file__ = str(TARGET)
    module.__package__ = None
    sys.modules[MODULE_NAME] = module
    source = TARGET.read_text(encoding="utf-8")
    exec(compile(source, str(TARGET), "exec"), module.__dict__)
    cloudpickle.register_pickle_by_value(module)
    return module


def install_rng_guard(module: types.ModuleType) -> None:
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Official Dask GA with parent RNG guard")
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    parser.add_argument("--population", type=int, default=100)
    parser.add_argument("--generations", type=int, default=40)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.population <= 1 or args.generations <= 0:
        raise ValueError("population must be >1 and generations must be >0")

    module = load_official_module()
    install_rng_guard(module)
    module.POPULATION = int(args.population)
    module.GENERATIONS = int(args.generations)
    module.EXPECTED_EVALUATIONS = (
        module.POPULATION
        + int(module.POPULATION * (1.0 - 0.2)) * module.GENERATIONS
    )
    os.environ["KINGMAKER_DASK_PARENT_RNG_GUARD"] = "1"
    result = module.run(args.scheduler)
    result.setdefault("config", {})["parent_rng_guard"] = {
        "enabled": True,
        "scope": "every distributed fitness evaluation batch",
        "python_random_state_restored": True,
        "numpy_random_state_restored": True,
        "reason": "Dask submission consumes process-global Python random state",
    }
    print(module.json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
