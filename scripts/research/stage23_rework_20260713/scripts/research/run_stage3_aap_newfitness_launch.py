#!/usr/bin/env python3
"""AAP 새 fitness 정식 runner launcher.

정식 runner의 preflight probe가 원본 backup module에 노출되지 않은 helper를
참조하지 않도록 실제 engine Rulebook/genetic 생성 경로를 명시한다. 학습,
병렬 평가, 산출물 로직은 정식 runner 구현을 그대로 사용한다.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

from engine.learning import genetic

HERE = Path(__file__).resolve()
RUNNER_PATH = HERE.with_name("run_stage3_aap_newfitness_official.py")


def _load_runner() -> Any:
    spec = importlib.util.spec_from_file_location("_aap_newfitness_official_launch", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _reproducibility_probe() -> dict[str, Any]:
    base_rb = runner.Rulebook(ticker="AAP_REPRO", asset_type="us_stock", direction="long")

    def evaluate(rulebook: Any) -> float:
        return (
            -abs(runner._safe_float(getattr(rulebook, "signal_threshold", 0.0)) - 2.5) * 10.0
            -abs(runner._safe_float(getattr(rulebook, "base_position_ratio", 0.0)) - 0.7) * 20.0
            + 50.0
        )

    cfg = runner.mod._base.make_ga_config(population=12, generations=3, seed=2026071499)
    return runner.genetic_parallel.reproducibility_probe(base_rb, evaluate, cfg)


def _new_fitness_activation_probe(ctx: dict[str, Any]) -> dict[str, Any]:
    split = dict(runner.mod._base.TRAIN_SPLITS[0])
    domain = runner.mod.build_entry_feature_domain(ctx, start=split["start"], end=split["end"])
    rb = genetic.random_rulebook(
        copy.deepcopy(ctx["base_rulebook"]),
        gene_scope="entry",
        entry_feature_domain=domain,
    )
    result, diagnostics = runner._entry_scope_result(rb, ctx, split)
    checks = {
        "gene_scope_marker": diagnostics.get("scope") == "entry",
        "primary_formula": diagnostics.get("primary_objective") == "mean(net_realized_pnl_pct / max(holding_days, 1))",
        "mae_threshold": runner._safe_float(diagnostics.get("mae_threshold_pct")) == -2.0,
        "win_gate": runner._safe_float(diagnostics.get("win_rate_gate_pct")) == 60.0,
        "mutation_hint_only": dict(diagnostics.get("exit_mutation_hint") or {}).get("fitness_input") is False,
        "stage2_legacy_default": runner.genetic_parallel.run_ga.__kwdefaults__.get("gene_scope") == "legacy",
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "sample_trade_count": int(getattr(result, "trade_count", 0) or 0),
        "sample_final_fitness": runner._safe_float(getattr(result, "fitness", 0.0)),
    }


runner._reproducibility_probe = _reproducibility_probe
runner._new_fitness_activation_probe = _new_fitness_activation_probe


if __name__ == "__main__":
    raise SystemExit(runner.main())
