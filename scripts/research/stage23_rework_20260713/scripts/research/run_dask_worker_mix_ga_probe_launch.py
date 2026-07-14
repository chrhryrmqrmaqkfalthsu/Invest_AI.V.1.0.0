#!/usr/bin/env python3
"""Run the Dask GA comparison with scheduler-safe, worker-local payloads.

The scheduler does not import project modules. Rulebooks and entry-domain
contexts cross it only as plain dictionaries/lists. Each candidate envelope is
scattered directly to its assigned worker so the Windows worker never tries to
fetch a dependency from the Linux worker's localhost address.
"""
from __future__ import annotations

import copy
import importlib.util
import os
import struct
import sys
from pathlib import Path
from typing import Any, Mapping

from dask.distributed import Client as DaskClient
from dask.distributed import get_worker

HERE = Path(__file__).resolve()
BASE_PATH = HERE.with_name("run_dask_worker_mix_ga_probe.py")
SPEC = importlib.util.spec_from_file_location("_dask_worker_mix_ga_probe_base", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load comparison runner: {BASE_PATH}")
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)


class _AtomicDictClient(DaskClient):
    """Scatter dictionaries as one opaque object instead of key/value futures."""

    def scatter(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(data, dict):
            return super().scatter([data], *args, **kwargs)[0]
        return super().scatter(data, *args, **kwargs)


# BASE.run constructs Client from its module global.  Replacing it here keeps
# context/envelope dictionaries atomic and prevents generic keys such as "df"
# from being deduplicated onto the Linux worker.
BASE.Client = _AtomicDictClient


def _float_hex(value: float) -> str:
    return struct.pack(">d", float(value)).hex()


def _entry_domain_payload(entry_ctx: Any) -> dict[str, dict[str, Any]]:
    return {
        feature: {
            **dict(entry_ctx.metadata[feature]),
            "values": entry_ctx.values[feature].tolist(),
        }
        for feature in entry_ctx.metadata
    }


def _runtime_attrs(rulebook: Any) -> dict[str, Any]:
    names = (
        "_entry_fitness_diagnostics",
        "_entry_exit_mutation_hint",
        "_entry_exit_mutation_applied",
    )
    return {
        name: copy.deepcopy(getattr(rulebook, name))
        for name in names
        if hasattr(rulebook, name)
    }


def _worker_evaluate_plain(
    task_envelope: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    import contextlib

    import numpy as np

    from engine.core.metadata import compute_rulebook_hash
    from engine.learning import execution_mode_backtest as execution_bt
    from engine.learning import genetic as genetic
    from engine.strategies.rulebook import Rulebook

    index = int(task_envelope["index"])
    stage = str(task_envelope["stage"])
    candidate = Rulebook.from_dict(dict(task_envelope["rulebook_payload"]))
    for name, value in dict(task_envelope.get("runtime_attrs") or {}).items():
        setattr(candidate, str(name), copy.deepcopy(value))
    entry_ctx = genetic._normalize_entry_feature_domain(task_envelope["entry_domain"])

    @contextlib.contextmanager
    def entry_phase_context():
        original_builder = execution_bt._build_daily_signal_tape
        original_simulate_exit = execution_bt.simulate_exit
        state: dict[str, Any] = {}

        def build_tape(*args: Any, **kwargs: Any) -> Any:
            tape = original_builder(*args, **kwargs)
            state["signal_tape"] = tape
            return tape

        def simulate_entry_exit(*args: Any, **kwargs: Any) -> Any:
            tape = state.get("signal_tape")
            if tape is None:
                raise RuntimeError("entry-phase signal tape missing")
            kwargs["entry_phase_exit"] = True
            kwargs["entry_phase_signal_tape"] = tape
            kwargs["entry_phase_max_holding_days"] = 7
            return original_simulate_exit(*args, **kwargs)

        execution_bt._build_daily_signal_tape = build_tape
        execution_bt.simulate_exit = simulate_entry_exit
        try:
            yield
        finally:
            execution_bt._build_daily_signal_tape = original_builder
            execution_bt.simulate_exit = original_simulate_exit

    def evaluate_fn(item: Any) -> float:
        split = payload["split"]
        with entry_phase_context():
            result = execution_bt.run_backtest_execution_mode(
                item,
                payload["df"],
                start_date=str(split["start"]),
                end_date=str(split["end"]),
                position_limit_krw=120_000.0,
                market_history_df=payload["market_history_df"],
                sector_name=str(payload.get("sector_name") or "tech"),
                ticker_sentiment=payload.get("ticker_sentiment"),
                fitness_mode="swing",
                use_llm_events=False,
                entry_execution_mode="t_plus_1_open",
                exit_execution_mode="conservative_core",
                fold_exit_policy="fold_end_mark_to_market",
                live_hard_stop_guard=True,
            )
        return float(getattr(result, "fitness", -1_000_000_000.0))

    fitness = genetic._evaluate_candidate(
        candidate,
        evaluate_fn,
        gene_scope="entry",
        entry_ctx=entry_ctx,
        stage=stage,
    )
    candidate.fitness = float(fitness)
    diagnostics = dict(
        getattr(candidate, execution_bt.ENTRY_FITNESS_DIAGNOSTICS_ATTR, {}) or {}
    )
    worker = get_worker()
    return {
        "index": index,
        "candidate_payload": candidate.to_dict(),
        "candidate_runtime_attrs": _runtime_attrs(candidate),
        "chromosome_hash": compute_rulebook_hash(candidate),
        "fitness": float(fitness),
        "fitness_hex": _float_hex(float(fitness)),
        "trade_count": diagnostics.get("trade_count"),
        "win_rate_pct": diagnostics.get("win_rate_pct"),
        "entry_gate_pass": diagnostics.get("entry_gate_pass"),
        "worker_address": worker.address,
        "worker_os_name": os.name,
        "worker_python": sys.version,
        "worker_numpy": np.__version__,
        "worker_engine_file": str(__import__("engine").__file__),
        "market_snapshot_sha256": payload["market_snapshot_sha256"],
        "worker_local_market_file_read": False,
    }


def _evaluate_batch_plain(
    client: Any,
    population: list[Any],
    *,
    stages: list[str],
    worker_addresses: list[str],
    payload_futures: Mapping[str, Any],
    entry_ctx: Any,
) -> tuple[list[Any], list[dict[str, Any]]]:
    from engine.strategies.rulebook import Rulebook

    if len(population) != len(stages):
        raise ValueError("population/stages length mismatch")
    domain_payload = _entry_domain_payload(entry_ctx)
    results: list[dict[str, Any]] = []

    for wave_start in range(0, len(population), len(worker_addresses)):
        futures = []
        envelope_futures = []
        for offset, address in enumerate(worker_addresses):
            index = wave_start + offset
            if index >= len(population):
                break
            rulebook = population[index]
            envelope = {
                "index": index,
                "rulebook_payload": rulebook.to_dict(),
                "runtime_attrs": _runtime_attrs(rulebook),
                "stage": stages[index],
                "entry_domain": domain_payload,
            }
            envelope_future = client.scatter(
                envelope,
                workers=[address],
                broadcast=False,
                direct=False,
                hash=False,
            )
            envelope_futures.append(envelope_future)
            futures.append(
                client.submit(
                    _worker_evaluate_plain,
                    envelope_future,
                    payload_futures[address],
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                )
            )
        results.extend(client.gather(futures, direct=False))
        client.cancel(envelope_futures, force=False)

    results.sort(key=lambda row: int(row["index"]))
    evaluated: list[Any] = []
    public_rows: list[dict[str, Any]] = []
    for row in results:
        candidate = Rulebook.from_dict(dict(row["candidate_payload"]))
        for name, value in dict(row["candidate_runtime_attrs"]).items():
            setattr(candidate, str(name), copy.deepcopy(value))
        evaluated.append(candidate)
        public = dict(row)
        public.pop("candidate_payload", None)
        public.pop("candidate_runtime_attrs", None)
        public_rows.append(public)
    return evaluated, public_rows


BASE._evaluate_batch = _evaluate_batch_plain


if __name__ == "__main__":
    raise SystemExit(BASE.main())
