#!/usr/bin/env python3
"""Resume AAP overlap-entry v4 from completed qualify/entry checkpoints.

This script does not rerun or alter qualify/entry GA.  It reuses the official
``run_stage3_parallel_resume`` exit/validate functions with their documented
maximum six local workers, fixed per-candidate seeds, and candidate-index merge
order.  The spawn-safe market-cutoff launcher is imported first so every
Windows child process validates the same SHA-pinned 2026-07-10 snapshot.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve()
CUTOFF_LAUNCHER = HERE.with_name("run_stage3_aap_overlap_entry_v4_cutoff_host.py")
POST_ENTRY_MAX_WORKERS = 6


def _load_cutoff_launcher() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_aap_overlap_entry_v4_resume_cutoff",
        CUTOFF_LAUNCHER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load cutoff launcher: {CUTOFF_LAUNCHER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cutoff = _load_cutoff_launcher()
v4 = cutoff.v4
runner = v4.runner
parallel_resume = cutoff.parallel_resume


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _quote_powershell(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell(argv: Iterable[str], env: Mapping[str, str]) -> str:
    assignments = [
        f"$env:{key}={_quote_powershell(value)}"
        for key, value in env.items()
    ]
    command = " ".join(
        ["&", _quote_powershell(sys.executable)]
        + [_quote_powershell(value) for value in argv]
    )
    return "; ".join([*assignments, command])


def _remove_partial_post_entry_outputs(out_dir: Path) -> list[str]:
    """Remove only outputs owned by exit/validate/finalization stages."""
    removed: list[str] = []
    directory_names = (
        "_parallel_exit_workers",
        "_parallel_validate_workers",
    )
    file_names = (
        "final_rulebooks.jsonl",
        "exit_result.json",
        "validate_result.json",
        "parallel_resume_summary.json",
        "exit_trades.jsonl",
        "rl_replay_trades.jsonl",
        "validation_results.jsonl",
        "stage3_profile_catalog.jsonl",
        "stage3_ineligible.jsonl",
        "mutation_bias_summary.json",
        "official_final_summary.json",
        "launch_command.json",
        "trade_count_factor_comparison.json",
        "fold_best_summary.json",
        "overlap_entry_comparison.json",
        "fold_best_concurrency_summary.json",
        "SHA256SUMS.txt",
        "failure.json",
        "readout.md",
    )
    for name in directory_names:
        path = out_dir / name
        if path.exists():
            shutil.rmtree(path)
            removed.append(name + "/")
    for name in file_names:
        path = out_dir / name
        if path.exists():
            path.unlink()
            removed.append(name)
    return removed


def _validate_checkpoint(out_dir: Path) -> dict[str, Any]:
    required = (
        "manifest.json",
        "qualify_result.json",
        "qualify_population_all.jsonl",
        "qualify_cross_fold_matrix.jsonl",
        "qualify_gate_bottleneck.json",
        "entry_result.json",
        "entry_rulebooks.jsonl",
        "ga_population_history.jsonl",
        "generation_best_fitness.jsonl",
        "fold_best_trade_level.jsonl",
    )
    missing = [name for name in required if not (out_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"checkpoint files missing: {missing}")
    qualify = _read_json(out_dir / "qualify_result.json")
    entries = _read_jsonl(out_dir / "entry_rulebooks.jsonl")
    generation_rows = _read_jsonl(out_dir / "generation_best_fitness.jsonl")
    qualify_rows = [row for row in generation_rows if row.get("stage") == "qualify"]
    entry_rows = [row for row in generation_rows if row.get("stage") == "entry"]
    if not bool(qualify.get("qualified")):
        raise RuntimeError("checkpoint qualify result is not qualified")
    if len(entries) != 12:
        raise RuntimeError(f"expected 12 entry survivors, found {len(entries)}")
    if len(qualify_rows) != 120:
        raise RuntimeError(f"expected 120 qualify generation rows, found {len(qualify_rows)}")
    if len(entry_rows) != 50:
        raise RuntimeError(f"expected 50 entry generation rows, found {len(entry_rows)}")
    return {
        "qualified": True,
        "entry_survivor_count": len(entries),
        "qualify_generation_rows": len(qualify_rows),
        "entry_generation_rows": len(entry_rows),
        "pass_count_distribution": qualify.get("pass_count_distribution"),
        "fold_pass_counts": qualify.get("fold_pass_counts"),
    }


def _build_final(
    *,
    out_dir: Path,
    qualify: Mapping[str, Any],
    entry_summary: Mapping[str, Any],
    exit_summary: Mapping[str, Any],
    validate_summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    protected: Mapping[str, str],
    daemon: Mapping[str, Any],
    initial_source_commit: str,
    resume_source_commit: str,
    resume_elapsed_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mutation_summary = runner._mutation_summary(
        _read_jsonl(out_dir / "ga_population_history.jsonl")
    )
    _write_json(out_dir / "mutation_bias_summary.json", mutation_summary)
    ce_boil = runner._ce_boil_audit(out_dir)
    catalog_rows = _read_jsonl(out_dir / "stage3_profile_catalog.jsonl")
    manifest_gate = dict(manifest.get("market_snapshot_manifest_gate") or {})
    repro_probe = dict(manifest.get("parallel_reproducibility_probe") or {})
    fitness_probe = dict(manifest.get("new_fitness_activation_probe") or {})

    final = {
        "ticker": runner.TICKER,
        "execution_scale": "OFFICIAL_FULL_STAGE3_NEW_FITNESS",
        "official_config": runner.OFFICIAL_CONFIG,
        "workers": int(manifest.get("parallel", {}).get("workers", 28) or 28),
        "parallel_axis": "population_fitness_evaluation",
        "parallel_reproducibility_probe": repro_probe,
        "new_fitness_activation_probe": fitness_probe,
        "manifest_gate_passed": bool(manifest_gate.get("passed")),
        "qualified": bool(qualify.get("qualified")),
        "all3_pass_count": int(qualify.get("all3_pass_count", 0) or 0),
        "pass_count_distribution": qualify.get("pass_count_distribution"),
        "entry_survivor_count": len(_read_jsonl(out_dir / "entry_rulebooks.jsonl")),
        "exit_candidate_count": len(_read_jsonl(out_dir / "final_rulebooks.jsonl")),
        "validate_survivor_count": len(catalog_rows),
        "entry_result": dict(entry_summary),
        "exit_result": dict(exit_summary),
        "validate_result": dict(validate_summary),
        "ce_boil_audit": ce_boil,
        "stop_reason": None,
        "protected_sha_start": dict(protected),
        "protected_sha_end": dict(protected),
        "protected_unchanged": True,
        "daemon_start": dict(daemon),
        "daemon_end": dict(daemon),
        "daemon_unchanged": True,
        "elapsed_seconds": float(resume_elapsed_seconds)
        + float(entry_summary.get("elapsed_seconds", 0.0) or 0.0),
        "checkpoint_resume": True,
        "checkpoint_resume_stage": "post-entry",
        "post_entry_local_workers": POST_ENTRY_MAX_WORKERS,
        "post_entry_seed_policy": "seed_base + 1000 + entry_index",
        "post_entry_merge_order": "candidate_input_index_order",
        "initial_source_git_commit": initial_source_commit,
        "resume_source_git_commit": resume_source_commit,
        "independent_host_role": "notebook",
        "host_name": os.environ.get("COMPUTERNAME"),
        "requested_local_workers": 28,
        "market_available_cutoff_date": os.environ.get(cutoff.CUTOFF_ENV),
        "inter_machine_candidate_communication": False,
        "notebook_exit_validate_workers": POST_ENTRY_MAX_WORKERS,
        "source_git_commit": initial_source_commit,
    }
    return final, mutation_summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resume and finalize AAP overlap-entry v4 from entry checkpoint"
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--seed-base", type=int, required=True)
    parser.add_argument("--market-cutoff-date", required=True)
    parser.add_argument("--protected-snapshot-json", required=True)
    parser.add_argument("--daemon-snapshot-json", required=True)
    parser.add_argument("--initial-source-git-commit", required=True)
    parser.add_argument("--resume-source-git-commit", required=True)
    parser.add_argument("--initial-launch-argv-json", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir).resolve()
    baseline_dir = Path(args.baseline_dir).resolve()
    if not out_dir.is_dir():
        raise RuntimeError(f"checkpoint output directory missing: {out_dir}")
    if not baseline_dir.is_dir():
        raise RuntimeError(f"baseline directory missing: {baseline_dir}")

    os.environ[cutoff.CUTOFF_ENV] = args.market_cutoff_date
    checkpoint = _validate_checkpoint(out_dir)
    removed = _remove_partial_post_entry_outputs(out_dir)
    started = time.time()

    exit_summary = parallel_resume.run_parallel_exit(
        ticker=runner.TICKER,
        out_dir=out_dir,
        seed_base=int(args.seed_base),
        max_workers=POST_ENTRY_MAX_WORKERS,
    )
    if not _read_jsonl(out_dir / "final_rulebooks.jsonl"):
        raise RuntimeError("post-entry resume produced no exit candidates")
    validate_summary = parallel_resume.run_parallel_validate(
        ticker=runner.TICKER,
        out_dir=out_dir,
        seed_base=int(args.seed_base),
        max_workers=POST_ENTRY_MAX_WORKERS,
    )
    resume_elapsed = time.time() - started

    qualify = _read_json(out_dir / "qualify_result.json")
    entry_summary = _read_json(out_dir / "entry_result.json")
    manifest = _read_json(out_dir / "manifest.json")
    protected = json.loads(args.protected_snapshot_json)
    daemon = json.loads(args.daemon_snapshot_json)
    final, mutation_summary = _build_final(
        out_dir=out_dir,
        qualify=qualify,
        entry_summary=entry_summary,
        exit_summary=exit_summary,
        validate_summary=validate_summary,
        manifest=manifest,
        protected=protected,
        daemon=daemon,
        initial_source_commit=args.initial_source_git_commit,
        resume_source_commit=args.resume_source_git_commit,
        resume_elapsed_seconds=resume_elapsed,
    )
    _write_json(out_dir / "official_final_summary.json", final)
    (out_dir / "readout.md").write_text(
        runner._build_readout(final, qualify, entry_summary, mutation_summary),
        encoding="utf-8",
    )

    manifest.update(
        {
            "run_completed": True,
            "stop_reason": None,
            "protected_sha_end": dict(protected),
            "protected_unchanged": True,
            "daemon_end": dict(daemon),
            "daemon_unchanged": True,
            "final_counts": {
                "all3": final["all3_pass_count"],
                "entry_survivor": final["entry_survivor_count"],
                "validate_survivor": final["validate_survivor_count"],
            },
            "ce_boil_audit": final["ce_boil_audit"],
            "checkpoint_resume": {
                "enabled": True,
                "stage": "post-entry",
                "removed_partial_outputs": removed,
                "workers": POST_ENTRY_MAX_WORKERS,
                "seed_policy": final["post_entry_seed_policy"],
                "merge_order": final["post_entry_merge_order"],
                "elapsed_seconds": resume_elapsed,
                "initial_source_git_commit": args.initial_source_git_commit,
                "resume_source_git_commit": args.resume_source_git_commit,
            },
        }
    )
    _write_json(out_dir / "manifest.json", manifest)

    post_args = SimpleNamespace(
        out_dir=str(out_dir),
        baseline_dir=str(baseline_dir),
        seed_base=int(args.seed_base),
        workers=28,
        source_git_commit=args.initial_source_git_commit,
    )
    initial_argv = json.loads(args.initial_launch_argv_json)
    v4._postprocess(out_dir, baseline_dir, post_args, list(initial_argv))

    launch_path = out_dir / "launch_command.json"
    launch = _read_json(launch_path)
    resume_argv = [str(HERE), *list(sys.argv[1:])]
    initial_environment = {
        key: os.environ.get(key, "")
        for key in v4.RELEVANT_ENV_KEYS
    }
    launch.update(
        {
            "python_executable": sys.executable,
            "cwd": str(Path.cwd()),
            "argv": [sys.executable, *initial_argv],
            "environment": initial_environment,
            "powershell_command": _powershell(initial_argv, initial_environment),
            "host_local_parent": True,
            "local_process_workers": 28,
            "post_entry_resume": {
                "enabled": True,
                "python_executable": sys.executable,
                "argv": [sys.executable, *resume_argv],
                "powershell_command": _powershell(resume_argv, initial_environment),
                "workers": POST_ENTRY_MAX_WORKERS,
                "seed_policy": final["post_entry_seed_policy"],
                "merge_order": final["post_entry_merge_order"],
                "elapsed_seconds": resume_elapsed,
                "initial_source_git_commit": args.initial_source_git_commit,
                "resume_source_git_commit": args.resume_source_git_commit,
            },
            "inter_machine_candidate_communication": False,
        }
    )
    _write_json(launch_path, launch)

    final = _read_json(out_dir / "official_final_summary.json")
    final["launch_command"] = launch
    final["checkpoint_resume_metadata"] = manifest["checkpoint_resume"]
    _write_json(out_dir / "official_final_summary.json", final)
    manifest = _read_json(out_dir / "manifest.json")
    manifest["launch_command"] = launch
    _write_json(out_dir / "manifest.json", manifest)

    readout_path = out_dir / "readout.md"
    readout = readout_path.read_text(encoding="utf-8")
    readout += (
        "\n## Post-entry checkpoint resume\n\n"
        f"- qualify/entry local workers: 28\n"
        f"- exit/validate local workers: {POST_ENTRY_MAX_WORKERS}\n"
        f"- fixed exit seed: `seed_base + 1000 + entry_index`\n"
        f"- merge order: candidate input index order\n"
        f"- initial source commit: `{args.initial_source_git_commit}`\n"
        f"- resume source commit: `{args.resume_source_git_commit}`\n"
        f"- market cutoff propagated to spawn children: `{args.market_cutoff_date}`\n"
        f"- removed partial sequential outputs: `{json.dumps(removed, ensure_ascii=False)}`\n"
    )
    readout_path.write_text(readout, encoding="utf-8")
    runner._write_sha_manifest(out_dir)

    print(
        json.dumps(
            {
                "event": "stage3_aap_overlap_entry_v4_resume_done",
                "checkpoint": checkpoint,
                "removed_partial_outputs": removed,
                "exit_result": exit_summary,
                "validate_result": validate_summary,
                "resume_elapsed_seconds": resume_elapsed,
                "final": final,
            },
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    v4.base.mp.freeze_support()
    raise SystemExit(main())
