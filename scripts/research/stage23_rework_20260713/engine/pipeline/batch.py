"""Batch orchestration for the staged pipeline.

Current scope: screening -> rolling validation with progress, resume, and
parallel execution. Full training is a disabled hook for a future task.
"""
from __future__ import annotations

import concurrent.futures as futures
import json
import os
import signal
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from engine.pipeline.orchestrator import PIPELINE_ROOT, process_ticker, write_json

PROGRESS_FILENAME = "_progress.json"
SUMMARY_FILENAME = "batch_summary.json"
LOCK_FILENAME = ".batch.lock"
TERMINAL_STATUSES = {"ROLLING_DONE", "FULL_TRAINING_DONE", "SCREENED_OUT", "ERROR"}
ACTIVE_STATUSES = {"RUNNING"}

_STOP_REQUESTED = False


class RunLockError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_dir(run_id: str) -> Path:
    return PIPELINE_ROOT / str(run_id)


def progress_path(run_id: str) -> Path:
    return run_dir(run_id) / PROGRESS_FILENAME


def summary_path(run_id: str) -> Path:
    return run_dir(run_id) / SUMMARY_FILENAME


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def load_progress(run_id: str) -> dict[str, Any]:
    path = progress_path(run_id)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def initialize_progress(tickers: list[str], run_id: str, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    now = utc_now()
    progress = dict(existing or {})
    progress.setdefault("run_id", run_id)
    progress.setdefault("started_at", now)
    progress["updated_at"] = now
    progress["total"] = len(tickers)
    progress.setdefault("tickers", {})
    for ticker in tickers:
        t = str(ticker).upper().strip()
        progress["tickers"].setdefault(
            t,
            {
                "status": "PENDING",
                "updated_at": now,
                "result": {},
            },
        )
    update_progress_counts(progress)
    return progress


def update_progress_counts(progress: dict[str, Any]) -> dict[str, Any]:
    entries = progress.get("tickers", {}) or {}
    counts = Counter((entry or {}).get("status", "PENDING") for entry in entries.values())
    progress["counts"] = {
        "total": len(entries),
        "pending": int(counts.get("PENDING", 0)),
        "running": int(counts.get("RUNNING", 0)),
        "rolling_done": int(counts.get("ROLLING_DONE", 0)) + int(counts.get("FULL_TRAINING_DONE", 0)),
        "full_training_done": int(counts.get("FULL_TRAINING_DONE", 0)),
        "screened_out": int(counts.get("SCREENED_OUT", 0)),
        "error": int(counts.get("ERROR", 0)),
        "completed": int(sum(counts.get(status, 0) for status in TERMINAL_STATUSES)),
    }
    return progress


def save_progress(run_id: str, progress: dict[str, Any]) -> None:
    progress["updated_at"] = utc_now()
    update_progress_counts(progress)
    atomic_write_json(progress_path(run_id), progress)


def select_pending_tickers(tickers: list[str], progress: dict[str, Any], resume: bool = True) -> list[str]:
    if not resume:
        return [str(t).upper().strip() for t in tickers if str(t).strip()]
    entries = progress.get("tickers", {}) or {}
    pending: list[str] = []
    for ticker in tickers:
        t = str(ticker).upper().strip()
        if not t:
            continue
        status = (entries.get(t, {}) or {}).get("status", "PENDING")
        if status not in TERMINAL_STATUSES:
            pending.append(t)
    return pending


def set_ticker_status(
    progress: dict[str, Any],
    ticker: str,
    status: str,
    result: dict[str, Any] | None = None,
) -> None:
    t = str(ticker).upper().strip()
    entry = progress.setdefault("tickers", {}).setdefault(t, {})
    entry["status"] = status
    entry["updated_at"] = utc_now()
    if result is not None:
        entry["result"] = summarize_ticker_result(result)


def summarize_ticker_result(result: dict[str, Any]) -> dict[str, Any]:
    screening = result.get("screening", {}) or {}
    rolling = result.get("rolling", {}) or {}
    full_training = result.get("full_training", {}) or {}
    return {
        "ticker": result.get("ticker"),
        "final_status": result.get("final_status"),
        "final_stage": result.get("final_stage"),
        "passed": result.get("passed"),
        "reason_code": result.get("reason_code"),
        "elapsed_sec": result.get("elapsed_sec"),
        "adv_usd_252d": screening.get("adv_usd_252d"),
        "screening_status": screening.get("status"),
        "screening_reason_code": screening.get("reason_code"),
        "viability_executed": screening.get("viability_executed"),
        "stock_score": rolling.get("stock_score"),
        "consistency_score": rolling.get("consistency_score"),
        "quality_score": rolling.get("quality_score"),
        "liquidity_weight": rolling.get("liquidity_weight"),
        "excluded": rolling.get("excluded"),
        "exclude_reason": rolling.get("exclude_reason"),
        "pass_count": rolling.get("pass_count"),
        "full_training_executed": full_training.get("executed"),
        "full_training_reason_code": full_training.get("reason_code"),
        "full_training_member_count": full_training.get("member_count"),
        "full_training_qualified_count": full_training.get("qualified_count"),
        "outputs": result.get("outputs", {}),
        "error": result.get("error", {}),
    }


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * p
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(r.get("final_status", "UNKNOWN") for r in results)
    screened_reasons = Counter((r.get("screening", {}) or {}).get("reason_code") or "PASS" for r in results)
    error_count = int(status_counts.get("ERROR", 0))

    rolling_scores: list[float] = []
    rolling_excluded_count = 0
    rolling_zero_count = 0
    for result in results:
        rolling = result.get("rolling", {}) or {}
        if not rolling:
            continue
        if rolling.get("excluded"):
            rolling_excluded_count += 1
        score = rolling.get("stock_score")
        try:
            f = float(score)
            rolling_scores.append(f)
            if f == 0.0:
                rolling_zero_count += 1
        except Exception:
            pass

    distribution = {
        "count": len(rolling_scores),
        "min": _percentile(rolling_scores, 0.0),
        "p10": _percentile(rolling_scores, 0.10),
        "p25": _percentile(rolling_scores, 0.25),
        "p50": _percentile(rolling_scores, 0.50),
        "p75": _percentile(rolling_scores, 0.75),
        "p90": _percentile(rolling_scores, 0.90),
        "max": _percentile(rolling_scores, 1.0),
        "zero_score_count": rolling_zero_count,
        "excluded_count": rolling_excluded_count,
    }

    full_training_done = int(status_counts.get("FULL_TRAINING_DONE", 0))
    return {
        "total": len(results),
        "status_counts": dict(status_counts),
        "screened_out_count": int(status_counts.get("SCREENED_OUT", 0)),
        "rolling_done_count": int(status_counts.get("ROLLING_DONE", 0)) + full_training_done,
        "full_training_done_count": full_training_done,
        "error_count": error_count,
        "screening_reason_counts": dict(screened_reasons),
        "stock_score_distribution": distribution,
    }


def _lock_path(run_id: str) -> Path:
    return run_dir(run_id) / LOCK_FILENAME


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
    path = _lock_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if _pid_running(pid):
                raise RunLockError(f"run_id {run_id} is already running with pid {pid}")
        except RunLockError:
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


def release_run_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def _install_signal_handlers() -> None:
    def handler(signum, frame):  # noqa: ARG001
        global _STOP_REQUESTED
        _STOP_REQUESTED = True

    try:
        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
    except Exception:
        pass


def _worker_process_ticker(ticker: str, run_id: str, run_full_training: bool) -> dict[str, Any]:
    return process_ticker(ticker, run_id, run_full_training=run_full_training)


def run_batch(
    tickers: list[str],
    run_id: str | None = None,
    max_workers: int = 8,
    resume: bool = True,
    run_full_training: bool = False,
) -> dict[str, Any]:
    """Run screening -> rolling batch with progress/resume/parallelism."""
    global _STOP_REQUESTED
    _STOP_REQUESTED = False
    _install_signal_handlers()

    clean_tickers = []
    seen = set()
    for ticker in tickers:
        t = str(ticker).upper().strip()
        if t and t not in seen:
            clean_tickers.append(t)
            seen.add(t)
    if not clean_tickers:
        raise ValueError("tickers is empty")

    if run_id is None or not resume:
        run_id = run_id or str(uuid4())
    lock_path = acquire_run_lock(run_id)

    started = time.time()
    try:
        existing = load_progress(run_id) if resume else {}
        progress = initialize_progress(clean_tickers, run_id, existing=existing)
        pending = select_pending_tickers(clean_tickers, progress, resume=resume)
        for ticker in pending:
            set_ticker_status(progress, ticker, "PENDING")
        save_progress(run_id, progress)

        results: list[dict[str, Any]] = []
        if pending:
            workers = max(1, int(max_workers or 1))
            with futures.ProcessPoolExecutor(max_workers=workers) as executor:
                future_to_ticker: dict[futures.Future, str] = {}
                for ticker in pending:
                    if _STOP_REQUESTED:
                        break
                    set_ticker_status(progress, ticker, "RUNNING")
                    save_progress(run_id, progress)
                    fut = executor.submit(_worker_process_ticker, ticker, run_id, run_full_training)
                    future_to_ticker[fut] = ticker

                for fut in futures.as_completed(future_to_ticker):
                    ticker = future_to_ticker[fut]
                    if _STOP_REQUESTED:
                        # Let already submitted tasks finish if they complete soon;
                        # pending unsubmitted tickers remain PENDING in progress.
                        pass
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = {
                            "ticker": ticker,
                            "run_id": run_id,
                            "final_stage": "error",
                            "final_status": "ERROR",
                            "passed": False,
                            "reason_code": "ERROR",
                            "screening": {},
                            "rolling": {},
                            "outputs": {},
                            "error": {"type": type(exc).__name__, "message": str(exc)},
                            "elapsed_sec": None,
                        }
                    status = result.get("final_status") or "ERROR"
                    set_ticker_status(progress, ticker, status, result)
                    save_progress(run_id, progress)
                    results.append(result)
        else:
            entries = progress.get("tickers", {}) or {}
            for ticker in clean_tickers:
                entry = entries.get(ticker, {}) or {}
                result = dict(entry.get("result", {}) or {})
                if result:
                    result.setdefault("ticker", ticker)
                    result.setdefault("final_status", entry.get("status"))
                    result.setdefault("screening", {"reason_code": result.get("screening_reason_code")})
                    result.setdefault("rolling", {"stock_score": result.get("stock_score")})
                    results.append(result)

        # Use progress results as source of truth so resumed/skipped terminal rows
        # are included in the final summary.
        all_results = []
        for ticker, entry in (progress.get("tickers", {}) or {}).items():
            r = dict((entry or {}).get("result", {}) or {})
            if r:
                r.setdefault("ticker", ticker)
                r.setdefault("final_status", entry.get("status"))
                r.setdefault("screening", {"reason_code": r.get("screening_reason_code")})
                r.setdefault(
                    "rolling",
                    {
                        "stock_score": r.get("stock_score"),
                        "excluded": r.get("excluded"),
                        "exclude_reason": r.get("exclude_reason"),
                    },
                )
                all_results.append(r)
        summary = summarize_results(all_results)
        payload = {
            "run_id": run_id,
            "started_at": progress.get("started_at"),
            "finished_at": utc_now(),
            "elapsed_sec": time.time() - started,
            "max_workers": max_workers,
            "resume": resume,
            "run_full_training": run_full_training,
            "tickers": clean_tickers,
            "pending_executed": pending,
            "summary": summary,
            "progress_path": str(progress_path(run_id)),
            "results": all_results,
        }
        atomic_write_json(summary_path(run_id), payload)
        progress["summary"] = summary
        progress["summary_path"] = str(summary_path(run_id))
        save_progress(run_id, progress)
        return payload
    finally:
        release_run_lock(lock_path)


__all__ = [
    "TERMINAL_STATUSES",
    "RunLockError",
    "atomic_write_json",
    "initialize_progress",
    "load_progress",
    "progress_path",
    "run_batch",
    "save_progress",
    "select_pending_tickers",
    "summarize_results",
    "update_progress_counts",
]
