#!/usr/bin/env python3
"""Thin Stage2/Stage3 batch orchestrator.

This wrapper intentionally does not import or modify GA/backtest learning logic.
It only parses ticker lists, invokes existing single-ticker scripts via
subprocess, writes logs, validates expected outputs, and records wrapper-owned
completion markers.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE2_SCRIPT = PROJECT_ROOT / "scripts" / "research" / "run_stage2.py"
STAGE3_SCRIPT = PROJECT_ROOT / "scripts" / "research" / "run_stage3_aggressive.py"

Status = Literal[
    "PENDING",
    "RUNNING_STAGE2",
    "STAGE2_DONE",
    "STAGE2_FAILED",
    "RUNNING_STAGE3",
    "STAGE3_QUALIFY_REJECTED",
    "STAGE3_DONE",
    "STAGE3_FAILED",
    "SKIPPED_EXISTING",
]
Stage3Mode = Literal["none", "qualify-only", "all", "resume-qualified"]


@dataclass(frozen=True)
class StageRunResult:
    status: str
    out_dir: Path
    marker_path: Path | None = None
    returncode: int | None = None
    qualified: bool | None = None
    reason: str = ""
    elapsed_seconds: float = 0.0
    command: list[str] | None = None
    log_path: Path | None = None


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def default_run_id() -> str:
    return "stage23_" + datetime.now().strftime("%Y%m%d_%H%M")


def safe_ticker_dir_name(ticker: str) -> str:
    safe = re.sub(r"[^A-Z0-9._-]+", "_", ticker.upper().strip())
    return safe or "UNKNOWN"


def parse_ticker_file(path: Path) -> list[str]:
    """Parse one-ticker-per-line TXT, normalize uppercase, dedupe preserving order."""

    seen: set[str] = set()
    tickers: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        ticker = raw.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        tickers.append(ticker)
    return tickers


def resolve_tickers_path(raw_path: str) -> Path:
    """Resolve --tickers early so subprocess work never starts with a bad path."""

    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"tickers file not found: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"tickers path is not a file: {path}")
    return path


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def stage2_done_marker(out_dir: Path) -> Path:
    return out_dir / "_stage2_done.json"


def stage3_done_marker(out_dir: Path) -> Path:
    return out_dir / "_stage3_done.json"


def stage2_output_valid(out_dir: Path) -> tuple[bool, str, list[str]]:
    required = ["summary.json", "rl_replay_trades.jsonl", "config.json"]
    missing = [name for name in required if not (out_dir / name).exists()]
    if missing:
        return False, "missing required Stage2 outputs: " + ",".join(missing), required
    return True, "", required


def stage3_qualify_output_valid(out_dir: Path) -> tuple[bool, str, bool | None, list[str]]:
    required = ["qualify_result.json"]
    qpath = out_dir / "qualify_result.json"
    if not qpath.exists():
        return False, "missing qualify_result.json", None, required
    payload = read_json(qpath)
    if payload is None:
        return False, "invalid qualify_result.json", None, required
    return True, "", bool(payload.get("qualified")), required


def stage3_full_output_valid(out_dir: Path) -> tuple[bool, str, bool | None, list[str]]:
    q_ok, q_reason, qualified, _ = stage3_qualify_output_valid(out_dir)
    if not q_ok:
        return False, q_reason, None, ["qualify_result.json"]
    if qualified is False:
        return True, "", False, ["qualify_result.json"]

    required = [
        "qualify_result.json",
        "entry_result.json",
        "exit_result.json",
        "validate_result.json",
        "stage3_profile_catalog.jsonl",
        "last_run_summary.json",
    ]
    missing = [name for name in required if not (out_dir / name).exists()]
    if missing:
        return False, "missing required Stage3 full outputs: " + ",".join(missing), qualified, required
    summary = read_json(out_dir / "last_run_summary.json")
    if summary is None:
        return False, "invalid last_run_summary.json", qualified, required
    if summary.get("stage") != "all":
        return False, "last_run_summary stage is not all", qualified, required
    summaries = summary.get("summaries")
    if not isinstance(summaries, list) or len(summaries) < 4:
        return False, "last_run_summary does not contain four stage summaries", qualified, required
    return True, "", qualified, required


def marker_status(marker: Path) -> dict[str, Any] | None:
    if not marker.exists():
        return None
    return read_json(marker)


def latest_done_marker(ticker_root: Path, stage_prefix: str) -> tuple[Path, dict[str, Any]] | tuple[None, None]:
    candidates = sorted(ticker_root.glob(f"{stage_prefix}*/_{stage_prefix}_done.json"))
    for marker in reversed(candidates):
        payload = marker_status(marker)
        if payload:
            return marker, payload
    return None, None


def stage3_marker_satisfies(payload: dict[str, Any], mode: Stage3Mode) -> bool:
    status = payload.get("status")
    marker_mode = payload.get("mode")
    if mode == "none":
        return True
    if mode == "qualify-only":
        return status in {"STAGE3_DONE", "STAGE3_QUALIFY_REJECTED"} and marker_mode in {"qualify-only", "all", "resume-qualified"}
    if mode in {"all", "resume-qualified"}:
        return status in {"STAGE3_DONE", "STAGE3_QUALIFY_REJECTED"} and marker_mode in {"all", "resume-qualified"}
    return False


def choose_run_dir(ticker_root: Path, stage_prefix: str, *, retry_failed: bool, reusable_existing: bool = False) -> tuple[Path, str | None]:
    """Choose an output dir without deleting prior failed artifacts."""

    canonical = ticker_root / stage_prefix
    if not canonical.exists() or reusable_existing:
        return canonical, None
    marker = canonical / f"_{stage_prefix}_done.json"
    if marker.exists():
        return canonical, None
    if not retry_failed:
        return canonical, f"existing incomplete {stage_prefix} directory; use --retry-failed to create {stage_prefix}_retry<N>"
    idx = 1
    while True:
        candidate = ticker_root / f"{stage_prefix}_retry{idx}"
        if not candidate.exists():
            return candidate, None
        idx += 1


def command_to_text(command: list[str]) -> str:
    return " ".join(command)


def run_subprocess(command: list[str], log_path: Path) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("COMMAND: " + command_to_text(command) + "\n")
        log.flush()
        completed = subprocess.run(command, cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT)
    return int(completed.returncode), time.time() - started


def make_stage2_marker(ticker: str, out_dir: Path, returncode: int, elapsed: float, command: list[str], log_path: Path) -> StageRunResult:
    valid, reason, required = stage2_output_valid(out_dir)
    if returncode == 0 and valid:
        marker = stage2_done_marker(out_dir)
        payload = {
            "ticker": ticker,
            "stage": "stage2",
            "status": "STAGE2_DONE",
            "returncode": returncode,
            "validated_outputs": required,
            "out_dir": str(out_dir),
            "command": command,
            "log_path": str(log_path),
            "elapsed_seconds": elapsed,
            "finished_at": utc_now_iso(),
        }
        write_json(marker, payload)
        return StageRunResult("STAGE2_DONE", out_dir, marker, returncode, None, "", elapsed, command, log_path)
    final_reason = reason if returncode == 0 else f"Stage2 subprocess returned {returncode}"
    return StageRunResult("STAGE2_FAILED", out_dir, None, returncode, None, final_reason, elapsed, command, log_path)


def make_stage3_marker(
    ticker: str,
    out_dir: Path,
    returncode: int,
    elapsed: float,
    command: list[str],
    log_path: Path,
    mode: Stage3Mode,
) -> StageRunResult:
    if mode == "qualify-only":
        valid, reason, qualified, required = stage3_qualify_output_valid(out_dir)
    else:
        valid, reason, qualified, required = stage3_full_output_valid(out_dir)
    if returncode == 0 and valid:
        status = "STAGE3_DONE" if qualified else "STAGE3_QUALIFY_REJECTED"
        marker = stage3_done_marker(out_dir)
        payload = {
            "ticker": ticker,
            "stage": "stage3",
            "mode": mode,
            "status": status,
            "qualified": qualified,
            "returncode": returncode,
            "validated_outputs": required,
            "out_dir": str(out_dir),
            "command": command,
            "log_path": str(log_path),
            "elapsed_seconds": elapsed,
            "finished_at": utc_now_iso(),
        }
        write_json(marker, payload)
        return StageRunResult(status, out_dir, marker, returncode, qualified, "", elapsed, command, log_path)
    final_reason = reason if returncode == 0 else f"Stage3 subprocess returned {returncode}"
    return StageRunResult("STAGE3_FAILED", out_dir, None, returncode, qualified, final_reason, elapsed, command, log_path)


def run_stage2_for_ticker(
    ticker: str,
    out_root: Path,
    *,
    skip_existing: bool,
    retry_failed: bool,
    dry_run: bool,
) -> StageRunResult:
    ticker_root = out_root / "tickers" / safe_ticker_dir_name(ticker)
    marker, payload = latest_done_marker(ticker_root, "stage2")
    if skip_existing and marker and payload and payload.get("status") == "STAGE2_DONE":
        return StageRunResult("SKIPPED_EXISTING", marker.parent, marker, int(payload.get("returncode", 0)), None, "Stage2 done marker exists")
    out_dir, preflight_error = choose_run_dir(ticker_root, "stage2", retry_failed=retry_failed)
    out_dir = out_dir.resolve()
    command = [sys.executable, str(STAGE2_SCRIPT), "--ticker", ticker, "--out-dir", str(out_dir)]
    log_path = (out_root / "logs" / f"{safe_ticker_dir_name(ticker)}_stage2.log").resolve()
    if dry_run:
        print(f"DRY-RUN STAGE2 {ticker}: {command_to_text(command)}", flush=True)
        return StageRunResult("PENDING", out_dir, None, None, None, "dry-run", 0.0, command, log_path)
    if preflight_error:
        return StageRunResult("STAGE2_FAILED", out_dir, None, None, None, preflight_error, 0.0, command, log_path)
    returncode, elapsed = run_subprocess(command, log_path)
    return make_stage2_marker(ticker, out_dir, returncode, elapsed, command, log_path)


def run_stage3_for_ticker(
    ticker: str,
    out_root: Path,
    *,
    mode: Stage3Mode,
    skip_existing: bool,
    retry_failed: bool,
    dry_run: bool,
) -> StageRunResult:
    if mode == "none":
        return StageRunResult("PENDING", (out_root / "tickers" / safe_ticker_dir_name(ticker) / "stage3").resolve(), None, None, None, "stage3 disabled")
    ticker_root = out_root / "tickers" / safe_ticker_dir_name(ticker)
    marker, payload = latest_done_marker(ticker_root, "stage3")
    if skip_existing and marker and payload and stage3_marker_satisfies(payload, mode):
        return StageRunResult("SKIPPED_EXISTING", marker.parent.resolve(), marker.resolve(), int(payload.get("returncode", 0)), payload.get("qualified"), f"Stage3 {mode} marker exists")

    reusable_existing = False
    if mode == "resume-qualified":
        if not payload:
            return StageRunResult("STAGE3_FAILED", (ticker_root / "stage3").resolve(), None, None, None, "resume-qualified requires an existing qualify-only marker")
        if payload.get("qualified") is not True:
            return StageRunResult("STAGE3_QUALIFY_REJECTED", (marker.parent if marker else ticker_root / "stage3").resolve(), marker.resolve() if marker else None, int(payload.get("returncode", 0)), False, "previous qualify-only marker rejected ticker")
        out_dir = marker.parent if marker else ticker_root / "stage3"
        preflight_error = None
        reusable_existing = True
    else:
        out_dir, preflight_error = choose_run_dir(ticker_root, "stage3", retry_failed=retry_failed, reusable_existing=False)
    out_dir = out_dir.resolve()

    stage_arg = "qualify" if mode == "qualify-only" else "all"
    command = [sys.executable, str(STAGE3_SCRIPT), "--ticker", ticker, "--stage", stage_arg, "--out-dir", str(out_dir)]
    log_path = (out_root / "logs" / f"{safe_ticker_dir_name(ticker)}_stage3.log").resolve()
    if dry_run:
        print(f"DRY-RUN STAGE3 {ticker}: {command_to_text(command)}", flush=True)
        return StageRunResult("PENDING", out_dir, None, None, None, "dry-run", 0.0, command, log_path)
    if preflight_error and not reusable_existing:
        return StageRunResult("STAGE3_FAILED", out_dir, None, None, None, preflight_error, 0.0, command, log_path)
    returncode, elapsed = run_subprocess(command, log_path)
    return make_stage3_marker(ticker, out_dir, returncode, elapsed, command, log_path, mode)


def row_from_results(
    ticker: str,
    *,
    stage2_result: StageRunResult | None,
    stage3_result: StageRunResult | None,
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    status = "PENDING"
    reason_parts: list[str] = []
    if stage2_result:
        status = stage2_result.status
        if stage2_result.reason:
            reason_parts.append(stage2_result.reason)
    if stage3_result:
        status = stage3_result.status
        if stage3_result.reason:
            reason_parts.append(stage3_result.reason)
    return {
        "ticker": ticker,
        "status": status,
        "stage2_status": stage2_result.status if stage2_result else None,
        "stage3_status": stage3_result.status if stage3_result else None,
        "qualified": stage3_result.qualified if stage3_result else None,
        "stage2_out_dir": str(stage2_result.out_dir) if stage2_result else None,
        "stage3_out_dir": str(stage3_result.out_dir) if stage3_result else None,
        "stage2_marker": str(stage2_result.marker_path) if stage2_result and stage2_result.marker_path else None,
        "stage3_marker": str(stage3_result.marker_path) if stage3_result and stage3_result.marker_path else None,
        "stage2_returncode": stage2_result.returncode if stage2_result else None,
        "stage3_returncode": stage3_result.returncode if stage3_result else None,
        "stage2_elapsed_seconds": stage2_result.elapsed_seconds if stage2_result else None,
        "stage3_elapsed_seconds": stage3_result.elapsed_seconds if stage3_result else None,
        "stage2_command": stage2_result.command if stage2_result else None,
        "stage3_command": stage3_result.command if stage3_result else None,
        "reason": "; ".join(part for part in reason_parts if part),
        "started_at": started_at,
        "finished_at": finished_at,
    }


def write_summary(out_root: Path, rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    counts = Counter(row.get("status") for row in rows)
    summary = {
        "run_id": args.run_id,
        "out_root": str(out_root),
        "ticker_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "stage2_enabled": bool(args.stage2),
        "stage3_mode": args.stage3_mode,
        "max_workers_stage2": args.max_workers_stage2,
        "max_workers_stage3": args.max_workers_stage3,
        "skip_existing": bool(args.skip_existing),
        "retry_failed": bool(args.retry_failed),
        "dry_run": bool(args.dry_run),
        "updated_at": utc_now_iso(),
    }
    write_json(out_root / "batch_summary.json", summary)


def write_index_and_summary(out_root: Path, rows_by_ticker: dict[str, dict[str, Any]], args: argparse.Namespace) -> None:
    rows = list(rows_by_ticker.values())
    write_jsonl(out_root / "batch_index.jsonl", rows)
    write_summary(out_root, rows, args)


def run_stage2_batch(tickers: list[str], out_root: Path, args: argparse.Namespace, rows_by_ticker: dict[str, dict[str, Any]]) -> None:
    if not args.stage2:
        return
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers_stage2)) as executor:
        futures = {}
        for ticker in tickers:
            started = utc_now_iso()
            futures[executor.submit(run_stage2_for_ticker, ticker, out_root, skip_existing=args.skip_existing, retry_failed=args.retry_failed, dry_run=args.dry_run)] = (ticker, started)
        for future in as_completed(futures):
            ticker, started = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = StageRunResult("STAGE2_FAILED", (out_root / "tickers" / safe_ticker_dir_name(ticker) / "stage2").resolve(), None, None, None, f"wrapper exception: {exc}")
            rows_by_ticker[ticker] = row_from_results(ticker, stage2_result=result, stage3_result=None, started_at=started, finished_at=utc_now_iso())
            print(f"{ticker} {result.status} {result.reason}".rstrip(), flush=True)
            write_index_and_summary(out_root, rows_by_ticker, args)


def tickers_for_stage3(tickers: list[str], rows_by_ticker: dict[str, dict[str, Any]], args: argparse.Namespace) -> list[str]:
    if args.stage3_mode == "none":
        return []
    if args.dry_run:
        return tickers
    if not args.stage2:
        return tickers
    selected: list[str] = []
    for ticker in tickers:
        row = rows_by_ticker.get(ticker)
        if row and row.get("stage2_status") in {"STAGE2_DONE", "SKIPPED_EXISTING"}:
            selected.append(ticker)
    return selected


def run_stage3_batch(tickers: list[str], out_root: Path, args: argparse.Namespace, rows_by_ticker: dict[str, dict[str, Any]]) -> None:
    selected = tickers_for_stage3(tickers, rows_by_ticker, args)
    if not selected:
        return
    with ThreadPoolExecutor(max_workers=max(1, args.max_workers_stage3)) as executor:
        futures = {}
        for ticker in selected:
            started = rows_by_ticker.get(ticker, {}).get("started_at") or utc_now_iso()
            futures[executor.submit(run_stage3_for_ticker, ticker, out_root, mode=args.stage3_mode, skip_existing=args.skip_existing, retry_failed=args.retry_failed, dry_run=args.dry_run)] = (ticker, started)
        for future in as_completed(futures):
            ticker, started = futures[future]
            stage2_result = None
            prior = rows_by_ticker.get(ticker)
            if prior and prior.get("stage2_status"):
                stage2_result = StageRunResult(
                    prior["stage2_status"],
                    Path(prior["stage2_out_dir"]) if prior.get("stage2_out_dir") else (out_root / "tickers" / safe_ticker_dir_name(ticker) / "stage2").resolve(),
                    Path(prior["stage2_marker"]) if prior.get("stage2_marker") else None,
                    prior.get("stage2_returncode"),
                    None,
                    "",
                    float(prior.get("stage2_elapsed_seconds") or 0.0),
                    prior.get("stage2_command"),
                )
            try:
                result = future.result()
            except Exception as exc:
                result = StageRunResult("STAGE3_FAILED", (out_root / "tickers" / safe_ticker_dir_name(ticker) / "stage3").resolve(), None, None, None, f"wrapper exception: {exc}")
            rows_by_ticker[ticker] = row_from_results(ticker, stage2_result=stage2_result, stage3_result=result, started_at=started, finished_at=utc_now_iso())
            print(f"{ticker} {result.status} {result.reason}".rstrip(), flush=True)
            write_index_and_summary(out_root, rows_by_ticker, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch wrapper for Stage2/Stage3 single-ticker research scripts")
    parser.add_argument("--tickers", required=True, help="One-ticker-per-line TXT file")
    parser.add_argument("--run-id", default=None, help="Run id. Default: stage23_<YYYYMMDD_HHMM>")
    parser.add_argument("--out-root", default=None, help="Output root. Default: exp_batch_stage23_<run_id>")
    parser.add_argument("--stage2", action="store_true", help="Run Stage2 before Stage3")
    parser.add_argument("--stage3-mode", choices=["none", "qualify-only", "all", "resume-qualified"], default="qualify-only")
    parser.add_argument("--max-workers-stage2", type=int, default=1)
    parser.add_argument("--max-workers-stage3", type=int, default=1)
    parser.add_argument("--skip-existing", action="store_true", help="Skip when a wrapper-owned done marker satisfies the requested stage")
    parser.add_argument("--retry-failed", action="store_true", help="Preserve incomplete dirs and retry in stage<N>_retry dirs")
    parser.add_argument("--limit", type=int, default=None, help="Run only first N parsed tickers")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without running subprocesses")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.run_id = args.run_id or default_run_id()
    try:
        tickers_path = resolve_tickers_path(args.tickers)
    except FileNotFoundError as exc:
        parser.exit(2, f"{exc}\n")
    args.tickers = str(tickers_path)

    out_root = Path(args.out_root).expanduser() if args.out_root else PROJECT_ROOT / f"exp_batch_stage23_{args.run_id}"
    if not out_root.is_absolute():
        out_root = PROJECT_ROOT / out_root
    out_root = out_root.resolve()

    tickers = parse_ticker_file(tickers_path)
    if args.limit is not None:
        tickers = tickers[: max(0, args.limit)]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "logs").mkdir(parents=True, exist_ok=True)
    rows_by_ticker: dict[str, dict[str, Any]] = {}

    if args.dry_run:
        print(f"DRY-RUN run_id={args.run_id} out_root={out_root} tickers={len(tickers)}", flush=True)
    run_stage2_batch(tickers, out_root, args, rows_by_ticker)
    if not args.stage2:
        for ticker in tickers:
            rows_by_ticker.setdefault(
                ticker,
                row_from_results(ticker, stage2_result=None, stage3_result=None, started_at=utc_now_iso(), finished_at=utc_now_iso()),
            )
    run_stage3_batch(tickers, out_root, args, rows_by_ticker)
    write_index_and_summary(out_root, rows_by_ticker, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
