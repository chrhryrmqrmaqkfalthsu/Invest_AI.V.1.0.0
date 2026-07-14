#!/usr/bin/env python3
"""Verify bit-identical entry fitness between patched and thread-safe paths."""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import random
import struct
import sys

import numpy as np


def float_hex(value: float) -> str:
    return struct.pack(">d", float(value)).hex()


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    scripts_dir = pathlib.Path(__file__).resolve().parent
    workspace_root = scripts_dir.parents[1]
    stage3 = load_module(
        "_threadsafe_equivalence_stage3",
        scripts_dir / "run_stage3_aggressive.py",
    )
    probe = load_module(
        "_threadsafe_equivalence_probe",
        scripts_dir / "run_dask_worker_mix_ga_probe.py",
    )

    from engine.learning import genetic
    from engine.learning.entry_fitness_threadsafe import run_entry_backtest_threadsafe

    _, metadata = stage3._load_research_market_snapshot_bundle()
    context = stage3.prepare_research_ticker_context("AAP")
    split = next(item for item in stage3._base.TRAIN_SPLITS if item["label"] == "train_3")
    entry_domain = stage3.build_entry_feature_domain(
        context,
        start=split["start"],
        end=split["end"],
    )
    entry_ctx = genetic._normalize_entry_feature_domain(entry_domain)
    payload = {
        "df": context["df"],
        "market_history_df": context["market_history_df"],
        "sector_name": context.get("sector_name", "tech"),
        "ticker_sentiment": context.get("ticker_sentiment"),
        "split": dict(split),
        "market_snapshot_sha256": metadata["primary"]["sha256"],
    }

    random.seed(2026071401)
    np.random.seed(2026071401)
    candidates = [
        genetic.random_rulebook(
            context["base_rulebook"],
            gene_scope="entry",
            entry_feature_domain=entry_ctx,
        )
        for _ in range(8)
    ]

    rows = []
    for index, source in enumerate(candidates):
        patched = copy.deepcopy(source)
        direct = copy.deepcopy(source)

        def patched_fn(rulebook):
            return float(probe._run_entry_backtest(rulebook, payload).fitness)

        def direct_fn(rulebook):
            result = run_entry_backtest_threadsafe(
                rulebook,
                payload["df"],
                start_date=str(split["start"]),
                end_date=str(split["end"]),
                position_limit_krw=120_000.0,
                market_history_df=payload["market_history_df"],
                sector_name=payload["sector_name"],
                ticker_sentiment=payload["ticker_sentiment"],
                use_llm_events=False,
                entry_execution_mode="t_plus_1_open",
                exit_execution_mode="conservative_core",
                fold_exit_policy="fold_end_mark_to_market",
                live_hard_stop_guard=True,
                entry_phase_max_holding_days=7,
            )
            return float(result.fitness)

        patched_fitness = genetic._evaluate_candidate(
            patched,
            patched_fn,
            gene_scope="entry",
            entry_ctx=entry_ctx,
            stage=f"patched-{index}",
        )
        direct_fitness = genetic._evaluate_candidate(
            direct,
            direct_fn,
            gene_scope="entry",
            entry_ctx=entry_ctx,
            stage=f"direct-{index}",
        )
        patched_diag = copy.deepcopy(getattr(patched, "_entry_fitness_diagnostics", {}))
        direct_diag = copy.deepcopy(getattr(direct, "_entry_fitness_diagnostics", {}))
        patched_hint = copy.deepcopy(getattr(patched, "_entry_exit_mutation_hint", {}))
        direct_hint = copy.deepcopy(getattr(direct, "_entry_exit_mutation_hint", {}))
        equal = (
            float_hex(patched_fitness) == float_hex(direct_fitness)
            and patched_diag == direct_diag
            and patched_hint == direct_hint
        )
        rows.append(
            {
                "index": index,
                "patched_fitness": patched_fitness,
                "direct_fitness": direct_fitness,
                "patched_hex": float_hex(patched_fitness),
                "direct_hex": float_hex(direct_fitness),
                "diagnostics_equal": patched_diag == direct_diag,
                "mutation_hint_equal": patched_hint == direct_hint,
                "equal": equal,
            }
        )
    if not all(row["equal"] for row in rows):
        raise RuntimeError(f"thread-safe equivalence failed: {rows}")
    print(
        json.dumps(
            {
                "status": "PASS",
                "candidate_count": len(rows),
                "market_snapshot_sha256": payload["market_snapshot_sha256"],
                "rows": rows,
                "workspace_root": str(workspace_root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
