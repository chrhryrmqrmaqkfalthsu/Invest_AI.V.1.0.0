#!/usr/bin/env python3
"""Run full-training only for prequalified cutoff candidates.

This script intentionally skips screening and rolling_validation. It consumes a
fixed candidate list (for example cutoff_60_candidates.json from a completed
rolling batch) and runs only run_full_training() for those tickers.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import signal
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.pipeline.full_training import (  # noqa: E402
    DEFAULT_FULL_TRAINING_GA_CONFIG,
    SMOKE_FULL_TRAINING_GA_CONFIG,
    run_full_training,
    save_full_training_artifacts,
)

PIPELINE_ROOT = ROOT / "data/_system/pipeline/v1/runs"
DEFAULT_CANDIDATES = PIPELINE_ROOT / "au_1173_20260604/cutoff_60_candidates.json"
PROGRESS_FILENAME = "_full_training_progress.json"
SUMMARY_FILENAME = "full_training_batch_summary.json"
LOCK_FILENAME = ".full_training_batch.lock"
TERMINAL_DONE = "DONE"
STATUSES = {"PENDING", "RUNNING", "DONE", "ERROR"}
_STOP_REQUESTED = False

FullTrainingFn = Callable[..., dict[str, Any]]
SaveArtifactsFn = Callable[[dict[str, Any], str | Path], dict[str, str]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_dir(run_id: str) -> Path:
    return PIPELINE_ROOT / str(run_id)


def ticker_dir(run_id: str, ticker: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(ticker).upper())
    return run_dir(run_id) / safe


def progress_path(run_id: str) -> Path:
    return run_dir(run_id) / PROGRESS_FILENAME


def summary_path(run_id: str) -> Path:
    return run_dir(run_id) / SUMMARY_FILENAME


def lock_path(run_id: str) -> Path:
    return run_dir(run_id) / LOCK_FILENAME


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def acquire_run_lock(run_id: str) -> Path:
    path = lock_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if _pid_running(pid):
                raise RuntimeError(f"run_id {run_id} is already running with pid {pid}")
        except RuntimeError:
            raise
        except Exception:
            pass
        path.unlink(missing_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "created_at": utc_now()}).encode("utf-8"))
    finally:
        os.close(fd)
    return path


def release_run_lock(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def install_signal_handlers() -> None:
    def handler(signum, frame):  # noqa: ARG001
        global _STOP_REQUESTED
        _STOP_REQUESTED = True

    try:
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
    except Exception:
        pass


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def percentile(values: list[float], p: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(clean) - 1)
    frac = rank - lo
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def score_distribution(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if v is not None]
    return {
        "count": len(clean),
        "min": percentile(clean, 0.0),
        "p25": percentile(clean, 0.25),
        "p50": percentile(clean, 0.50),
        "p75": percentile(clean, 0.75),
        "max": percentile(clean, 1.0),
    }


def load_candidate_tickers(path: str | Path, cutoff: float = 60.0, limit: int | None = None) -> list[dict[str, Any]]:
    """Load cutoff candidates and re-check stock_score >= cutoff."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("candidates") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError(f"candidates JSON must contain a list or candidates list: {path}")
    out: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        if isinstance(row, str):
            ticker = row.upper().strip()
            score = cutoff
            payload = {"ticker": ticker, "stock_score": score}
        elif isinstance(row, dict):
            ticker = str(row.get("ticker") or "").upper().strip()
            score = safe_float(row.get("stock_score"), -1.0)
            payload = dict(row)
            payload["ticker"] = ticker
            payload["stock_score"] = score
        else:
            continue
        if not ticker or ticker in seen:
            continue
        if score < float(cutoff):
            continue
        out.append(payload)
        seen.add(ticker)
        if limit is not None and len(out) >= int(limit):
            break
    return out


def update_progress_counts(progress: dict[str, Any]) -> dict[str, Any]:
    entries = progress.get("tickers", {}) or {}
    counts = Counter((entry or {}).get("status", "PENDING") for entry in entries.values())
    progress["counts"] = {
        "total": len(entries),
        "pending": int(counts.get("PENDING", 0)),
        "running": int(counts.get("RUNNING", 0)),
        "done": int(counts.get("DONE", 0)),
        "error": int(counts.get("ERROR", 0)),
        "completed": int(counts.get("DONE", 0)) + int(counts.get("ERROR", 0)),
    }
    return progress


def initialize_progress(
    candidates: list[dict[str, Any]],
    run_id: str,
    *,
    existing: dict[str, Any] | None = None,
    source_path: str | None = None,
    cutoff: float = 60.0,
) -> dict[str, Any]:
    now = utc_now()
    progress = dict(existing or {})
    progress.setdefault("run_id", run_id)
    progress.setdefault("started_at", now)
    progress["updated_at"] = now
    progress["source_candidates"] = source_path
    progress["cutoff"] = float(cutoff)
    progress.setdefault("tickers", {})
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        progress["tickers"].setdefault(
            ticker,
            {
                "status": "PENDING",
                "candidate": candidate,
                "updated_at": now,
                "result": {},
            },
        )
        progress["tickers"][ticker].setdefault("candidate", candidate)
    update_progress_counts(progress)
    return progress


def load_progress(run_id: str) -> dict[str, Any]:
    return load_json(progress_path(run_id), {}) or {}


def save_progress(run_id: str, progress: dict[str, Any]) -> None:
    progress["updated_at"] = utc_now()
    update_progress_counts(progress)
    atomic_write_json(progress_path(run_id), progress)


def select_pending_tickers(candidates: list[dict[str, Any]], progress: dict[str, Any], resume: bool = True) -> list[str]:
    entries = progress.get("tickers", {}) or {}
    pending: list[str] = []
    for candidate in candidates:
        ticker = str(candidate.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        if not resume:
            pending.append(ticker)
            continue
        status = (entries.get(ticker, {}) or {}).get("status", "PENDING")
        if status != TERMINAL_DONE:
            pending.append(ticker)
    return pending


def set_ticker_status(progress: dict[str, Any], ticker: str, status: str, result: dict[str, Any] | None = None) -> None:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    t = str(ticker).upper().strip()
    entry = progress.setdefault("tickers", {}).setdefault(t, {})
    entry["status"] = status
    entry["updated_at"] = utc_now()
    if result is not None:
        entry["result"] = result


def summarize_ticker_result(result: dict[str, Any]) -> dict[str, Any]:
    dist = result.get("member_score_distribution", {}) or {}
    return {
        "ticker": result.get("ticker"),
        "status": result.get("status", "DONE"),
        "elapsed_sec": result.get("elapsed_sec"),
        "member_count": result.get("member_count"),
        "qualified_count": result.get("qualified_count"),
        "member_score_min": dist.get("min"),
        "member_score_p50": dist.get("median"),
        "member_score_max": dist.get("max"),
        "qualified_score_min": dist.get("qualified_min"),
        "qualified_score_p50": dist.get("qualified_median"),
        "qualified_score_max": dist.get("qualified_max"),
        "data_start": result.get("data_start"),
        "data_end": result.get("data_end"),
        "outputs": result.get("outputs", {}),
        "error": result.get("error", {}),
    }


def process_candidate_ticker(
    ticker: str,
    run_id: str,
    *,
    ga_mode: str = "default",
    full_training_fn: FullTrainingFn = run_full_training,
    save_artifacts_fn: SaveArtifactsFn = save_full_training_artifacts,
) -> dict[str, Any]:
    started = time.time()
    ticker = str(ticker or "").upper().strip()
    out_dir = ticker_dir(run_id, ticker)
    try:
        ga_config = SMOKE_FULL_TRAINING_GA_CONFIG if ga_mode == "smoke" else DEFAULT_FULL_TRAINING_GA_CONFIG
        result = full_training_fn(ticker, run_id=run_id, ga_config=ga_config)
        paths = save_artifacts_fn(result, out_dir)
        result = dict(result)
        result["outputs"] = paths
        result["status"] = "DONE"
        result["elapsed_sec"] = result.get("elapsed_sec", time.time() - started)
        return summarize_ticker_result(result)
    except Exception as exc:
        return {
            "ticker": ticker,
            "status": "ERROR",
            "elapsed_sec": time.time() - started,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=8),
            },
        }


def _worker_process_candidate(ticker: str, run_id: str, ga_mode: str) -> dict[str, Any]:
    return process_candidate_ticker(ticker, run_id, ga_mode=ga_mode)


def summarize_batch(progress: dict[str, Any]) -> dict[str, Any]:
    entries = progress.get("tickers", {}) or {}
    results = [(entry or {}).get("result", {}) or {} for entry in entries.values()]
    status_counts = Counter((entry or {}).get("status", "PENDING") for entry in entries.values())
    qualified_counts = [int(r.get("qualified_count") or 0) for r in results if r]
    member_scores: list[float] = []
    qualified_zero: list[str] = []
    errors: list[dict[str, Any]] = []
    elapsed: list[float] = []
    for ticker, entry in entries.items():
        result = (entry or {}).get("result", {}) or {}
        if not result:
            continue
        if result.get("status") == "ERROR" or (entry or {}).get("status") == "ERROR":
            errors.append({"ticker": ticker, "error": result.get("error", {})})
        if result.get("qualified_count") == 0 and (entry or {}).get("status") == "DONE":
            qualified_zero.append(ticker)
        for key in ("member_score_min", "member_score_p50", "member_score_max", "qualified_score_min", "qualified_score_p50", "qualified_score_max"):
            if result.get(key) is not None:
                member_scores.append(safe_float(result.get(key)))
        if result.get("elapsed_sec") is not None:
            elapsed.append(safe_float(result.get("elapsed_sec")))

    return {
        "total": len(entries),
        "status_counts": dict(status_counts),
        "done_count": int(status_counts.get("DONE", 0)),
        "error_count": int(status_counts.get("ERROR", 0)),
        "qualified_count_distribution": score_distribution([float(x) for x in qualified_counts]),
        "member_score_distribution": score_distribution(member_scores),
        "qualified_zero_tickers": sorted(qualified_zero),
        "error_tickers": errors,
        "elapsed_sec_distribution": score_distribution(elapsed),
    }


def run_full_training_candidate_batch(
    candidates_path: str | Path = DEFAULT_CANDIDATES,
    run_id: str | None = None,
    max_workers: int = 8,
    resume: bool = True,
    cutoff: float = 60.0,
    limit: int | None = None,
    ga_mode: str = "default",
) -> dict[str, Any]:
    global _STOP_REQUESTED
    _STOP_REQUESTED = False
    install_signal_handlers()
    run_id = run_id or f"full_training_{uuid4()}"
    candidates_path = Path(candidates_path)
    candidates = load_candidate_tickers(candidates_path, cutoff=cutoff, limit=limit)
    if not candidates:
        raise ValueError(f"no candidates found at cutoff {cutoff}: {candidates_path}")

    lock = acquire_run_lock(run_id)
    started = time.time()
    try:
        existing = load_progress(run_id) if resume else {}
        progress = initialize_progress(
            candidates,
            run_id,
            existing=existing,
            source_path=str(candidates_path),
            cutoff=cutoff,
        )
        progress["ga_mode"] = ga_mode
        progress["max_workers"] = int(max_workers)
        save_progress(run_id, progress)

        pending = select_pending_tickers(candidates, progress, resume=resume)
        if pending:
            with futures.ProcessPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
                future_map = {}
                for ticker in pending:
                    if _STOP_REQUESTED:
                        break
                    set_ticker_status(progress, ticker, "RUNNING")
                    save_progress(run_id, progress)
                    future = executor.submit(_worker_process_candidate, ticker, run_id, ga_mode)
                    future_map[future] = ticker

                for future in futures.as_completed(future_map):
                    ticker = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {
                            "ticker": ticker,
                            "status": "ERROR",
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                                "traceback": traceback.format_exc(limit=8),
                            },
                        }
                    status = "DONE" if result.get("status") == "DONE" else "ERROR"
                    set_ticker_status(progress, ticker, status, result)
                    save_progress(run_id, progress)
                    if _STOP_REQUESTED:
                        break

        summary = summarize_batch(progress)
        payload = {
            "run_id": run_id,
            "source_candidates": str(candidates_path),
            "cutoff": float(cutoff),
            "ga_mode": ga_mode,
            "max_workers": int(max_workers),
            "elapsed_sec": time.time() - started,
            "progress_path": str(progress_path(run_id)),
            "summary_path": str(summary_path(run_id)),
            "summary": summary,
            "results": [
                (progress.get("tickers", {}) or {}).get(str(c.get("ticker")).upper(), {}).get("result", {})
                for c in candidates
            ],
        }
        atomic_write_json(summary_path(run_id), payload)
        progress["summary"] = summary
        save_progress(run_id, progress)
        return payload
    finally:
        release_run_lock(lock)


def fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return str(value)


def print_summary(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {}) or {}
    qdist = summary.get("qualified_count_distribution", {}) or {}
    mdist = summary.get("member_score_distribution", {}) or {}
    edist = summary.get("elapsed_sec_distribution", {}) or {}
    print("=" * 120)
    print("Full-training-only candidate batch summary")
    print("=" * 120)
    print(f"run_id:          {payload.get('run_id')}")
    print(f"source:          {payload.get('source_candidates')}")
    print(f"cutoff:          {payload.get('cutoff')}")
    print(f"ga_mode:         {payload.get('ga_mode')}")
    print(f"elapsed_sec:     {fmt(payload.get('elapsed_sec'), 2)}")
    print(f"max_workers:     {payload.get('max_workers')}")
    print(f"progress_path:   {payload.get('progress_path')}")
    print()
    print("Counts")
    print(f"  total:         {summary.get('total')}")
    print(f"  done:          {summary.get('done_count')}")
    print(f"  error:         {summary.get('error_count')}")
    print(f"  status_counts: {summary.get('status_counts')}")
    print()
    print("Qualified member count distribution")
    for key in ("count", "min", "p25", "p50", "p75", "max"):
        print(f"  {key:8s}: {qdist.get(key)}")
    print()
    print("Member score distribution (summary endpoints from each ticker)")
    for key in ("count", "min", "p25", "p50", "p75", "max"):
        print(f"  {key:8s}: {mdist.get(key)}")
    print()
    print("Elapsed sec distribution")
    for key in ("count", "min", "p25", "p50", "p75", "max"):
        print(f"  {key:8s}: {edist.get(key)}")
    if summary.get("qualified_zero_tickers"):
        print()
        print("qualified_count == 0:", ", ".join(summary["qualified_zero_tickers"]))
    if summary.get("error_tickers"):
        print()
        print("Errors")
        for item in summary["error_tickers"][:20]:
            err = item.get("error", {}) or {}
            print(f"  {item.get('ticker')}: {err.get('type')} {err.get('message')}")
    print()
    print("ticker | status | members | qualified | score_min/p50/max | sec")
    print("-" * 100)
    for row in payload.get("results", []):
        if not row:
            continue
        print(
            f"{str(row.get('ticker')):8s} | "
            f"{str(row.get('status')):6s} | "
            f"{str(row.get('member_count') or ''):7s} | "
            f"{str(row.get('qualified_count') or ''):9s} | "
            f"{fmt(row.get('member_score_min'))}/{fmt(row.get('member_score_p50'))}/{fmt(row.get('member_score_max'))} | "
            f"{fmt(row.get('elapsed_sec'), 1)}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run full-training only for cutoff candidates.")
    parser.add_argument("--candidates", default=str(DEFAULT_CANDIDATES), help="Path to cutoff candidates JSON.")
    parser.add_argument("--run-id", help="Run id. Defaults to full_training_<uuid>.")
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--cutoff", type=float, default=60.0)
    parser.add_argument("--limit", type=int, help="Optional first-N limit for smoke validation.")
    parser.add_argument("--smoke", action="store_true", help="Use SMOKE_FULL_TRAINING_GA_CONFIG(pop20xgen15) instead of default pop40xgen35.")
    args = parser.parse_args(argv)

    payload = run_full_training_candidate_batch(
        candidates_path=args.candidates,
        run_id=args.run_id,
        max_workers=args.max_workers,
        resume=not args.no_resume,
        cutoff=args.cutoff,
        limit=args.limit,
        ga_mode="smoke" if args.smoke else "default",
    )
    print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
