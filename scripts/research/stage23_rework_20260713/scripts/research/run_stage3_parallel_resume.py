#!/usr/bin/env python3
"""Stage 3 post-qualify 병렬 재개 runner.

지원 흐름:
- entry: 기존 정식 entry-only 실행(종목 단위 병렬은 외부에서 최대 2개)
- exit: entry 후보별 최대 6병렬
- validate: final 후보별 최대 6병렬, worker 임시 디렉터리 후 부모 병합
- post-entry: exit -> validate

현재 실행 중 프로세스를 변형하지 않는다. qualify_result.json / entry_rulebooks.jsonl /
final_rulebooks.jsonl checkpoint를 기준으로 명시적으로 재개한다.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import multiprocessing as mp
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve()
OFFICIAL_RUNNER = HERE.with_name("run_stage3_official_full.py")
MAX_WORKERS_LIMIT = 6


def _load_official() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("_stage3_parallel_resume_official", OFFICIAL_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load official Stage3 runner: {OFFICIAL_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._apply_official_config()
    return module


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _exit_worker(payload: dict[str, Any]) -> dict[str, Any]:
    official = _load_official()
    shared = official.shared
    ticker = str(payload["ticker"])
    entry_row = dict(payload["entry_row"])
    seed = int(payload["seed"])
    worker_log = Path(payload["worker_log"])
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    market, _ = shared._preflight_market_snapshot()
    ctx, _ = shared._load_snapshot_context(ticker, market)
    with worker_log.open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        rows = shared.mod._base._run_exit_ga_for_entry(
            entry_row=entry_row,
            ctx=ctx,
            seed=seed,
            weights=shared.mod._base.DEFAULT_EXIT_FITNESS_WEIGHTS,
        )
    return {
        "entry_index": int(payload["entry_index"]),
        "entry_rulebook_hash": entry_row.get("rulebook_hash"),
        "rows": rows,
        "elapsed_seconds": time.time() - started,
        "worker_log": str(worker_log),
    }


def run_parallel_exit(*, ticker: str, out_dir: Path, seed_base: int, max_workers: int) -> dict[str, Any]:
    official = _load_official()
    shared = official.shared
    entry_path = out_dir / "entry_rulebooks.jsonl"
    entries = _read_jsonl(entry_path)
    if not entries:
        raise RuntimeError("entry_rulebooks.jsonl is empty")
    workers = max(1, min(int(max_workers), MAX_WORKERS_LIMIT, len(entries)))
    temp_root = out_dir / "_parallel_exit_workers"
    temp_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    results: list[dict[str, Any]] = []
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = []
        for index, entry_row in enumerate(entries, 1):
            futures.append(
                pool.submit(
                    _exit_worker,
                    {
                        "ticker": ticker,
                        "entry_index": index,
                        "entry_row": entry_row,
                        "seed": seed_base + 1000 + index,
                        "worker_log": str(temp_root / f"entry_{index:03d}.log"),
                    },
                )
            )
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({"event": "stage3_parallel_exit_worker_done", **{k: result[k] for k in ("entry_index", "entry_rulebook_hash", "elapsed_seconds")}}, ensure_ascii=False), flush=True)

    final_rows: list[dict[str, Any]] = []
    for result in sorted(results, key=lambda row: int(row["entry_index"])):
        final_rows.extend(result["rows"])
    final_rows.sort(key=lambda row: shared.mod._base.safe_float(row.get("composite_fitness"), float("-inf")), reverse=True)
    _write_jsonl(out_dir / "final_rulebooks.jsonl", final_rows)
    summary = {
        "ticker": ticker,
        "stage": "exit",
        "mode": "entry_candidate_parallel",
        "max_workers": workers,
        "entry_count": len(entries),
        "final_rulebook_count": len(final_rows),
        "best_composite_fitness": shared.mod._base.safe_float(final_rows[0].get("composite_fitness")) if final_rows else None,
        "best_hash": final_rows[0].get("rulebook_hash") if final_rows else None,
        "worker_results": [{k: row[k] for k in ("entry_index", "entry_rulebook_hash", "elapsed_seconds", "worker_log")} for row in sorted(results, key=lambda item: int(item["entry_index"]))],
        "elapsed_seconds": time.time() - started,
    }
    _write_json(out_dir / "exit_result.json", summary)
    return summary


def _validate_worker(payload: dict[str, Any]) -> dict[str, Any]:
    official = _load_official()
    shared = official.shared
    ticker = str(payload["ticker"])
    row = dict(payload["final_row"])
    worker_dir = Path(payload["worker_dir"])
    worker_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(worker_dir / "final_rulebooks.jsonl", [row])
    market, _ = shared._preflight_market_snapshot()
    ctx, _ = shared._load_snapshot_context(ticker, market)
    log_path = worker_dir / "worker.log"
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        summary = shared.mod._base.run_validate(ticker, worker_dir, seed_base=int(payload["seed_base"]), context=ctx)
    outputs = {}
    for name in (
        "exit_trades.jsonl",
        "rl_replay_trades.jsonl",
        "validation_results.jsonl",
        "stage3_profile_catalog.jsonl",
        "stage3_ineligible.jsonl",
    ):
        path = worker_dir / name
        outputs[name] = _read_jsonl(path) if path.exists() else []
    return {
        "candidate_index": int(payload["candidate_index"]),
        "rulebook_hash": row.get("rulebook_hash"),
        "summary": summary,
        "outputs": outputs,
        "elapsed_seconds": time.time() - started,
        "worker_log": str(log_path),
    }


def run_parallel_validate(*, ticker: str, out_dir: Path, seed_base: int, max_workers: int) -> dict[str, Any]:
    final_rows = _read_jsonl(out_dir / "final_rulebooks.jsonl")
    if not final_rows:
        raise RuntimeError("final_rulebooks.jsonl is empty")
    workers = max(1, min(int(max_workers), MAX_WORKERS_LIMIT, len(final_rows)))
    temp_root = out_dir / "_parallel_validate_workers"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    results: list[dict[str, Any]] = []
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = []
        for index, row in enumerate(final_rows, 1):
            futures.append(
                pool.submit(
                    _validate_worker,
                    {
                        "ticker": ticker,
                        "candidate_index": index,
                        "final_row": row,
                        "seed_base": seed_base,
                        "worker_dir": str(temp_root / f"candidate_{index:03d}"),
                    },
                )
            )
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({"event": "stage3_parallel_validate_worker_done", **{k: result[k] for k in ("candidate_index", "rulebook_hash", "elapsed_seconds")}}, ensure_ascii=False), flush=True)

    ordered = sorted(results, key=lambda row: int(row["candidate_index"]))
    output_names = (
        "exit_trades.jsonl",
        "rl_replay_trades.jsonl",
        "validation_results.jsonl",
        "stage3_profile_catalog.jsonl",
        "stage3_ineligible.jsonl",
    )
    merged: dict[str, list[dict[str, Any]]] = {name: [] for name in output_names}
    for result in ordered:
        for name in output_names:
            merged[name].extend(result["outputs"][name])
    for name, rows in merged.items():
        _write_jsonl(out_dir / name, rows)

    eligible_count = len(merged["stage3_profile_catalog.jsonl"])
    ineligible_count = len(merged["stage3_ineligible.jsonl"])
    summary = {
        "ticker": ticker,
        "stage": "validate",
        "mode": "final_candidate_parallel_then_parent_merge",
        "max_workers": workers,
        "candidate_count": len(final_rows),
        "eligible_count": eligible_count,
        "ineligible_count": ineligible_count,
        "worker_results": [{k: row[k] for k in ("candidate_index", "rulebook_hash", "elapsed_seconds", "worker_log")} for row in ordered],
        "outputs": {name: name for name in output_names},
        "elapsed_seconds": time.time() - started,
    }
    _write_json(out_dir / "validate_result.json", summary)
    return summary


def run_entry(*, ticker: str, out_dir: Path, seed_base: int) -> dict[str, Any]:
    official = _load_official()
    shared = official.shared
    market, _ = shared._preflight_market_snapshot()
    ctx, _ = shared._load_snapshot_context(ticker, market)
    qualify = json.loads((out_dir / "qualify_result.json").read_text(encoding="utf-8"))
    if not bool(qualify.get("qualified")):
        raise RuntimeError(f"{ticker} did not qualify")
    return shared.mod.run_entry_ga(
        ticker,
        out_dir,
        seed_base=seed_base,
        use_fitness_cache=False,
        code_commit=shared.mod._base.resolve_code_commit(shared.mod._base.PROJECT_ROOT),
        context=ctx,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resume Stage3 with safe candidate-level parallelism")
    parser.add_argument("--ticker", required=True, choices=["AAP", "POWI"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed-base", required=True, type=int)
    parser.add_argument("--stage", required=True, choices=["entry", "exit", "validate", "post-entry"])
    parser.add_argument("--max-workers", type=int, default=6)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    workers = max(1, min(int(args.max_workers), MAX_WORKERS_LIMIT))
    out_dir = Path(args.out_dir).resolve()
    summaries = []
    if args.stage == "entry":
        summaries.append(run_entry(ticker=args.ticker, out_dir=out_dir, seed_base=args.seed_base))
    elif args.stage == "exit":
        summaries.append(run_parallel_exit(ticker=args.ticker, out_dir=out_dir, seed_base=args.seed_base, max_workers=workers))
    elif args.stage == "validate":
        summaries.append(run_parallel_validate(ticker=args.ticker, out_dir=out_dir, seed_base=args.seed_base, max_workers=workers))
    else:
        summaries.append(run_parallel_exit(ticker=args.ticker, out_dir=out_dir, seed_base=args.seed_base, max_workers=workers))
        summaries.append(run_parallel_validate(ticker=args.ticker, out_dir=out_dir, seed_base=args.seed_base, max_workers=workers))
    _write_json(out_dir / "parallel_resume_summary.json", {"ticker": args.ticker, "stage": args.stage, "max_workers": workers, "summaries": summaries})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
