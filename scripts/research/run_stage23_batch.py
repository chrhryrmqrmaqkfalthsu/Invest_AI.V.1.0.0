#!/usr/bin/env python3
"""Thin Stage2/Stage3 batch orchestrator.

This wrapper intentionally does not import or modify GA/backtest learning logic.
It only parses ticker lists, invokes existing single-ticker scripts via
subprocess, writes logs, validates expected outputs, records wrapper-owned
completion markers, and writes operational indexes/notifications around those
subprocesses.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

try:
    from engine.live.telegram.notifier import TelegramNotifier
except Exception:  # pragma: no cover - notification must never block research
    TelegramNotifier = None  # type: ignore[assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE2_SCRIPT = PROJECT_ROOT / "scripts" / "research" / "run_stage2.py"
STAGE3_SCRIPT = PROJECT_ROOT / "scripts" / "research" / "run_stage3_aggressive.py"
CENTRAL_INDEX_NAME = "central_index.jsonl"
NOTIFICATION_EVENTS_NAME = "notification_events.jsonl"
GB = 1024 ** 3
DISK_WARN_FREE_BYTES = 50 * GB
DISK_CRITICAL_FREE_BYTES = 20 * GB
DISK_FATAL_FREE_BYTES = 10 * GB

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
    "FATAL_DISK_STOP",
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


@dataclass(frozen=True)
class DiskState:
    level: str
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_pct: float

    @property
    def free_gb(self) -> float:
        return float(self.free_bytes / GB)


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def append_jsonl_rows(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Append rows without truncating existing object indexes."""

    materialized = list(rows)
    if not materialized:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        for row in materialized:
            fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        fp.flush()
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
    return len(materialized)


def append_notification_event(out_root: Path, event: dict[str, Any]) -> int:
    """Append notification diagnostics without exposing token/chat_id values."""

    safe_event = {
        "created_at": utc_now_iso(),
        **{k: v for k, v in event.items() if k not in {"token", "chat_id", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}},
    }
    written = append_jsonl_rows(out_root / NOTIFICATION_EVENTS_NAME, [safe_event])
    log_view = {
        "event_type": safe_event.get("event_type"),
        "enabled": safe_event.get("enabled"),
        "result": safe_event.get("result"),
        "message_id": safe_event.get("message_id"),
        "error_class": safe_event.get("error_class"),
    }
    print("NOTIFICATION_EVENT " + json.dumps(log_view, ensure_ascii=False, sort_keys=True), flush=True)
    return written


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
    """Choose an output dir without deleting or overwriting prior failed artifacts."""

    canonical = ticker_root / stage_prefix
    marker = canonical / f"_{stage_prefix}_done.json"
    if not canonical.exists():
        return canonical, None
    if reusable_existing and marker.exists():
        return canonical, None
    if reusable_existing and not marker.exists():
        return canonical, f"existing incomplete {stage_prefix} directory cannot be reused safely without a done marker"
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


def rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def metric_subset(metrics: dict[str, Any] | None) -> dict[str, Any]:
    metrics = dict(metrics or {})
    keys = ["expectancy_pct", "max_drawdown_pct", "profit_factor", "trade_count", "win_rate", "fitness", "avg_return_pct", "median_holding_days"]
    return {key: metrics.get(key) for key in keys if key in metrics}


def stage2_period_metric_map(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for period in row.get("periods") or []:
        if not isinstance(period, dict):
            continue
        label = str(period.get("period_label") or period.get("label") or "")
        if label:
            out[label] = metric_subset(period)
    return out


def central_common(args: argparse.Namespace, out_root: Path, result: StageRunResult, ticker: str, event_type: str, source_file: Path, source_row_index: int) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "run_id": args.run_id,
        "out_root": str(out_root),
        "ticker": ticker,
        "attempt_dir": rel_path(result.out_dir, out_root),
        "source_file": rel_path(source_file, out_root),
        "source_row_index": source_row_index,
        "created_at": utc_now_iso(),
    }


def build_stage2_central_index_rows(args: argparse.Namespace, out_root: Path, result: StageRunResult, ticker: str) -> list[dict[str, Any]]:
    if result.status not in {"STAGE2_DONE", "SKIPPED_EXISTING"}:
        return []
    source = result.out_dir / "survivors.jsonl"
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(read_jsonl(source), 1):
        rows.append(
            {
                **central_common(args, out_root, result, ticker, "stage2_survivor", source, idx),
                "stage": "stage2",
                "rulebook_hash": row.get("rulebook_hash"),
                "origin_train_labels": row.get("origin_train_labels"),
                "origin_count": row.get("origin_count"),
                "metrics": stage2_period_metric_map(row),
                "eligible": True,
                "artifact_paths": {
                    "survivors": rel_path(result.out_dir / "survivors.jsonl", out_root),
                    "rulebooks_all": rel_path(result.out_dir / "rulebooks_all.jsonl", out_root),
                    "period_metrics_all": rel_path(result.out_dir / "period_metrics_all.csv", out_root),
                    "trades": rel_path(result.out_dir / "trades.jsonl", out_root),
                    "rl_replay_trades": rel_path(result.out_dir / "rl_replay_trades.jsonl", out_root),
                    "summary": rel_path(result.out_dir / "summary.json", out_root),
                },
            }
        )
    return rows


def stage3_object_row(
    args: argparse.Namespace,
    out_root: Path,
    result: StageRunResult,
    ticker: str,
    event_type: str,
    source: Path,
    idx: int,
    row: dict[str, Any],
) -> dict[str, Any]:
    profile = {
        key: row.get(key)
        for key in ["holding_class", "risk_class", "return_class", "composite_tag"]
        if row.get(key) is not None
    }
    metrics = row.get("per_period_metrics") or {}
    if not metrics:
        metrics = {"stress": metric_subset(row.get("stress_metrics")), "bull": metric_subset(row.get("bull_metrics"))}
    return {
        **central_common(args, out_root, result, ticker, event_type, source, idx),
        "stage": "stage3",
        "rulebook_hash": row.get("rulebook_hash"),
        "entry_rulebook_hash": row.get("entry_rulebook_hash"),
        "entry_rank": row.get("entry_rank"),
        "exit_rank": row.get("exit_rank"),
        "stage3_rank": row.get("rank"),
        "eligible": row.get("eligible_stage3_basic"),
        "source_composite_fitness": row.get("source_composite_fitness", row.get("composite_fitness")),
        "profile": profile,
        "metrics": metrics,
        "artifact_paths": {
            "final_rulebooks": rel_path(result.out_dir / "final_rulebooks.jsonl", out_root),
            "validation_results": rel_path(result.out_dir / "validation_results.jsonl", out_root),
            "profile_catalog": rel_path(result.out_dir / "stage3_profile_catalog.jsonl", out_root),
            "ineligible": rel_path(result.out_dir / "stage3_ineligible.jsonl", out_root),
            "exit_trades": rel_path(result.out_dir / "exit_trades.jsonl", out_root),
            "rl_replay_trades": rel_path(result.out_dir / "rl_replay_trades.jsonl", out_root),
            "summary": rel_path(result.out_dir / "validate_result.json", out_root),
        },
    }


def build_stage3_central_index_rows(args: argparse.Namespace, out_root: Path, result: StageRunResult, ticker: str) -> list[dict[str, Any]]:
    if result.status not in {"STAGE3_DONE", "SKIPPED_EXISTING"}:
        return []
    specs = [
        ("stage3_final_rulebook", result.out_dir / "final_rulebooks.jsonl"),
        ("stage3_validation_result", result.out_dir / "validation_results.jsonl"),
        ("stage3_profile_catalog", result.out_dir / "stage3_profile_catalog.jsonl"),
        ("stage3_ineligible", result.out_dir / "stage3_ineligible.jsonl"),
    ]
    out: list[dict[str, Any]] = []
    for event_type, source in specs:
        for idx, row in enumerate(read_jsonl(source), 1):
            out.append(stage3_object_row(args, out_root, result, ticker, event_type, source, idx, row))
    return out


def append_central_index(out_root: Path, rows: Iterable[dict[str, Any]]) -> int:
    return append_jsonl_rows(out_root / CENTRAL_INDEX_NAME, rows)


def classify_disk_level(free_bytes: int, used_pct: float) -> str:
    if free_bytes < DISK_FATAL_FREE_BYTES:
        return "FATAL"
    if free_bytes < DISK_CRITICAL_FREE_BYTES or used_pct >= 90.0:
        return "CRITICAL"
    if free_bytes < DISK_WARN_FREE_BYTES or used_pct >= 70.0:
        return "WARN"
    return "OK"


def disk_state(path: Path) -> DiskState:
    usage = shutil.disk_usage(path)
    used_pct = float(usage.used / usage.total * 100.0) if usage.total else 0.0
    level = classify_disk_level(int(usage.free), used_pct)
    return DiskState(level=level, path=str(path), total_bytes=int(usage.total), used_bytes=int(usage.used), free_bytes=int(usage.free), used_pct=used_pct)


class BatchProgressNotifier:
    """Best-effort Telegram progress reporter with append-only diagnostics."""

    def __init__(self, *, run_id: str, out_root: Path, total_tickers: int, total_events: int) -> None:
        self.run_id = str(run_id)
        self.out_root = out_root
        self.total_tickers = int(total_tickers)
        self.total_events = int(total_events)
        self.started_at = time.time()
        self._last_edit_at = 0.0
        self._last_text = ""
        self._message_id = 0
        self._notifier = None
        self._record("notifier_init_begin", telegram_import_available=TelegramNotifier is not None)
        if TelegramNotifier is not None:
            try:
                self._notifier = TelegramNotifier(default_rate_limit_seconds=0)
            except Exception as exc:
                self._record(
                    "notifier_init_exception",
                    telegram_import_available=True,
                    error_class=exc.__class__.__name__,
                    error_message=str(exc)[:500],
                )
                self._notifier = None
        self._record(
            "notifier_init_done",
            telegram_import_available=TelegramNotifier is not None,
            notifier_created=self._notifier is not None,
            enabled=self.enabled,
        )

    @property
    def enabled(self) -> bool:
        return bool(self._notifier and getattr(self._notifier, "enabled", False))

    def _record(self, event_type: str, **payload: Any) -> None:
        try:
            append_notification_event(
                self.out_root,
                {
                    "event_type": event_type,
                    "run_id": self.run_id,
                    "enabled": self.enabled,
                    **payload,
                },
            )
        except Exception:
            return

    def _safe_send(self, text: str) -> bool:
        if not self.enabled:
            self._record("send_skipped", result=False, reason="notifier_disabled")
            return False
        try:
            result = bool(self._notifier.send(text[:3900]))
            self._record("send", result=result)
            return result
        except Exception as exc:
            self._record("send_exception", result=False, error_class=exc.__class__.__name__, error_message=str(exc)[:500])
            return False

    def _safe_progress(self, text: str, *, force: bool = False) -> bool:
        if not self.enabled:
            self._record("progress_skipped", result=False, reason="notifier_disabled")
            return False
        now = time.time()
        if not force and self._last_edit_at and now - self._last_edit_at < 30:
            return False
        if not force and text == self._last_text:
            return False
        try:
            if not self._message_id:
                message_id = int(self._notifier.send_progress(text[:3900]) or 0)
                self._message_id = message_id
                result = message_id > 0
                self._record("send_progress", result=result, message_id=message_id)
                if not result:
                    return False
            else:
                result = bool(self._notifier.edit_message(self._message_id, text[:3900]))
                self._record("edit_message", result=result, message_id=self._message_id)
                if not result:
                    return False
            self._last_text = text
            self._last_edit_at = now
            return True
        except Exception as exc:
            self._record("progress_exception", result=False, error_class=exc.__class__.__name__, error_message=str(exc)[:500])
            return False

    def start(self, args: argparse.Namespace, disk: DiskState) -> None:
        self._record(
            "start_called",
            stage2=bool(args.stage2),
            stage3_mode=args.stage3_mode,
            max_workers_stage2=args.max_workers_stage2,
            max_workers_stage3=args.max_workers_stage3,
            total_tickers=self.total_tickers,
            total_events=self.total_events,
            disk_free_gb=round(disk.free_gb, 3),
            disk_level=disk.level,
        )
        self._safe_send(
            "🚀 Stage123 배치 시작\n"
            f"run_id: {self.run_id}\n"
            f"tickers: {self.total_tickers}\n"
            f"stage2: {bool(args.stage2)}\n"
            f"stage3_mode: {args.stage3_mode}\n"
            f"workers: s2={args.max_workers_stage2}, s3={args.max_workers_stage3}\n"
            f"out_root: {self.out_root}\n"
            f"disk_free_gb: {disk.free_gb:.1f} ({disk.level})"
        )
        self.progress(stage2_done=0, stage3_done=0, failures=0, disk=disk, force=True)

    def format_progress(self, *, stage2_done: int, stage3_done: int, failures: int, disk: DiskState, final: bool = False) -> str:
        done_events = int(stage2_done) + int(stage3_done)
        elapsed = max(0.0, time.time() - self.started_at)
        rate = done_events / elapsed if elapsed > 0 else 0.0
        eta = (self.total_events - done_events) / rate if rate > 0 and self.total_events >= done_events else 0.0
        pct = (done_events / self.total_events * 100.0) if self.total_events else 0.0
        label = "완료" if final else "진행"
        return (
            f"Stage123 {self.run_id} ▸ {done_events}/{self.total_events} ({pct:.1f}%) | "
            f"S2 {stage2_done} | S3 {stage3_done} | 실패 {failures} | "
            f"disk {disk.free_gb:.1f}GB {disk.level} | ETA {eta/3600:.1f}h | {label}"
        )

    def progress(self, *, stage2_done: int, stage3_done: int, failures: int, disk: DiskState, force: bool = False) -> None:
        self._safe_progress(self.format_progress(stage2_done=stage2_done, stage3_done=stage3_done, failures=failures, disk=disk), force=force)

    def complete(self, *, stage2_done: int, stage3_done: int, failures: int, disk: DiskState) -> None:
        self._safe_progress(self.format_progress(stage2_done=stage2_done, stage3_done=stage3_done, failures=failures, disk=disk, final=True), force=True)
        self._safe_send(
            "✅ Stage123 배치 완료\n"
            f"run_id: {self.run_id}\n"
            f"stage2_done: {stage2_done}\n"
            f"stage3_done: {stage3_done}\n"
            f"failures: {failures}\n"
            f"disk_free_gb: {disk.free_gb:.1f} ({disk.level})\n"
            f"out_root: {self.out_root}"
        )

    def alert(self, title: str, message: str) -> None:
        self._safe_send(f"{title}\n{message}")


class BatchRuntime:
    def __init__(self, *, args: argparse.Namespace, out_root: Path, total_tickers: int) -> None:
        total_events = 0
        if args.stage2:
            total_events += total_tickers
        if args.stage3_mode != "none":
            total_events += total_tickers
        self.args = args
        self.out_root = out_root
        self.notifier = BatchProgressNotifier(run_id=args.run_id, out_root=out_root, total_tickers=total_tickers, total_events=total_events)
        self.disk_levels_sent: set[str] = set()
        self.fatal_disk_stop = False
        self.last_disk = disk_state(out_root)

    def check_disk(self) -> DiskState:
        self.last_disk = disk_state(self.out_root)
        if self.last_disk.level in {"WARN", "CRITICAL", "FATAL"} and self.last_disk.level not in self.disk_levels_sent:
            self.disk_levels_sent.add(self.last_disk.level)
            self.notifier.alert(
                f"⚠️ Stage123 disk {self.last_disk.level}",
                f"run_id: {self.args.run_id}\nout_root: {self.out_root}\nfree_gb: {self.last_disk.free_gb:.1f}\nused_pct: {self.last_disk.used_pct:.1f}%",
            )
        if self.last_disk.level == "FATAL":
            self.fatal_disk_stop = True
        return self.last_disk


def batch_counts(rows_by_ticker: dict[str, dict[str, Any]]) -> tuple[int, int, int]:
    stage2_done = 0
    stage3_done = 0
    failures = 0
    for row in rows_by_ticker.values():
        s2 = row.get("stage2_status")
        s3 = row.get("stage3_status")
        if s2:
            stage2_done += 1
        if s3:
            stage3_done += 1
        if str(row.get("status") or "").endswith("FAILED") or row.get("status") == "FATAL_DISK_STOP":
            failures += 1
        elif s2 == "STAGE2_FAILED" or s3 == "STAGE3_FAILED":
            failures += 1
    return stage2_done, stage3_done, failures


def notify_runtime_progress(runtime: BatchRuntime, rows_by_ticker: dict[str, dict[str, Any]], *, force: bool = False) -> None:
    stage2_done, stage3_done, failures = batch_counts(rows_by_ticker)
    disk = runtime.check_disk()
    runtime.notifier.progress(stage2_done=stage2_done, stage3_done=stage3_done, failures=failures, disk=disk, force=force)


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
        "central_index": str(Path(args.out_root_resolved) / CENTRAL_INDEX_NAME) if getattr(args, "out_root_resolved", None) else CENTRAL_INDEX_NAME,
        "notification_events": str(Path(args.out_root_resolved) / NOTIFICATION_EVENTS_NAME) if getattr(args, "out_root_resolved", None) else NOTIFICATION_EVENTS_NAME,
        "disk_thresholds_gb": {"warn_free_lt": 50, "critical_free_lt": 20, "fatal_free_lt": 10},
        "updated_at": utc_now_iso(),
    }
    write_json(out_root / "batch_summary.json", summary)


def write_index_and_summary(out_root: Path, rows_by_ticker: dict[str, dict[str, Any]], args: argparse.Namespace) -> None:
    rows = list(rows_by_ticker.values())
    write_jsonl(out_root / "batch_index.jsonl", rows)
    write_summary(out_root, rows, args)


def disk_stop_result(ticker: str, out_root: Path, stage: str, reason: str) -> StageRunResult:
    out_dir = (out_root / "tickers" / safe_ticker_dir_name(ticker) / stage).resolve()
    return StageRunResult("FATAL_DISK_STOP", out_dir, None, None, None, reason, 0.0, None, None)


def run_stage2_batch(tickers: list[str], out_root: Path, args: argparse.Namespace, rows_by_ticker: dict[str, dict[str, Any]], runtime: BatchRuntime) -> None:
    if not args.stage2:
        return
    workers = max(1, args.max_workers_stage2)
    next_idx = 0
    disk_stopped = False
    futures: dict[Any, tuple[str, str]] = {}

    def submit_one(executor: ThreadPoolExecutor) -> bool:
        nonlocal next_idx, disk_stopped
        if next_idx >= len(tickers):
            return False
        disk = runtime.check_disk()
        if disk.level == "FATAL":
            disk_stopped = True
            return False
        ticker = tickers[next_idx]
        next_idx += 1
        started = utc_now_iso()
        futures[executor.submit(run_stage2_for_ticker, ticker, out_root, skip_existing=args.skip_existing, retry_failed=args.retry_failed, dry_run=args.dry_run)] = (ticker, started)
        return True

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while len(futures) < workers and submit_one(executor):
            pass
        while futures:
            done, _ = wait(set(futures), return_when=FIRST_COMPLETED)
            for future in done:
                ticker, started = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = StageRunResult("STAGE2_FAILED", (out_root / "tickers" / safe_ticker_dir_name(ticker) / "stage2").resolve(), None, None, None, f"wrapper exception: {exc}")
                rows_by_ticker[ticker] = row_from_results(ticker, stage2_result=result, stage3_result=None, started_at=started, finished_at=utc_now_iso())
                append_central_index(out_root, build_stage2_central_index_rows(args, out_root, result, ticker))
                print(f"{ticker} {result.status} {result.reason}".rstrip(), flush=True)
                write_index_and_summary(out_root, rows_by_ticker, args)
                notify_runtime_progress(runtime, rows_by_ticker)
            while len(futures) < workers and submit_one(executor):
                pass

    if disk_stopped:
        for ticker in tickers[next_idx:]:
            result = disk_stop_result(ticker, out_root, "stage2", "disk free below fatal threshold; new Stage2 submissions stopped")
            rows_by_ticker.setdefault(ticker, row_from_results(ticker, stage2_result=result, stage3_result=None, started_at=utc_now_iso(), finished_at=utc_now_iso()))
        write_index_and_summary(out_root, rows_by_ticker, args)
        notify_runtime_progress(runtime, rows_by_ticker, force=True)


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


def run_stage3_batch(tickers: list[str], out_root: Path, args: argparse.Namespace, rows_by_ticker: dict[str, dict[str, Any]], runtime: BatchRuntime) -> None:
    selected = tickers_for_stage3(tickers, rows_by_ticker, args)
    if not selected:
        return
    workers = max(1, args.max_workers_stage3)
    next_idx = 0
    disk_stopped = False
    futures: dict[Any, tuple[str, str]] = {}

    def submit_one(executor: ThreadPoolExecutor) -> bool:
        nonlocal next_idx, disk_stopped
        if next_idx >= len(selected):
            return False
        disk = runtime.check_disk()
        if disk.level == "FATAL":
            disk_stopped = True
            return False
        ticker = selected[next_idx]
        next_idx += 1
        started = rows_by_ticker.get(ticker, {}).get("started_at") or utc_now_iso()
        futures[executor.submit(run_stage3_for_ticker, ticker, out_root, mode=args.stage3_mode, skip_existing=args.skip_existing, retry_failed=args.retry_failed, dry_run=args.dry_run)] = (ticker, started)
        return True

    with ThreadPoolExecutor(max_workers=workers) as executor:
        while len(futures) < workers and submit_one(executor):
            pass
        while futures:
            done, _ = wait(set(futures), return_when=FIRST_COMPLETED)
            for future in done:
                ticker, started = futures.pop(future)
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
                append_central_index(out_root, build_stage3_central_index_rows(args, out_root, result, ticker))
                print(f"{ticker} {result.status} {result.reason}".rstrip(), flush=True)
                write_index_and_summary(out_root, rows_by_ticker, args)
                notify_runtime_progress(runtime, rows_by_ticker)
            while len(futures) < workers and submit_one(executor):
                pass

    if disk_stopped:
        for ticker in selected[next_idx:]:
            prior = rows_by_ticker.get(ticker)
            stage2_result = None
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
            result = disk_stop_result(ticker, out_root, "stage3", "disk free below fatal threshold; new Stage3 submissions stopped")
            rows_by_ticker[ticker] = row_from_results(ticker, stage2_result=stage2_result, stage3_result=result, started_at=prior.get("started_at") if prior else utc_now_iso(), finished_at=utc_now_iso())
        write_index_and_summary(out_root, rows_by_ticker, args)
        notify_runtime_progress(runtime, rows_by_ticker, force=True)


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
    args.out_root_resolved = str(out_root)

    tickers = parse_ticker_file(tickers_path)
    if args.limit is not None:
        tickers = tickers[: max(0, args.limit)]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "logs").mkdir(parents=True, exist_ok=True)
    rows_by_ticker: dict[str, dict[str, Any]] = {}
    runtime = BatchRuntime(args=args, out_root=out_root, total_tickers=len(tickers))
    runtime.notifier.start(args, runtime.last_disk)

    if args.dry_run:
        print(f"DRY-RUN run_id={args.run_id} out_root={out_root} tickers={len(tickers)}", flush=True)
    run_stage2_batch(tickers, out_root, args, rows_by_ticker, runtime)
    if not args.stage2:
        for ticker in tickers:
            rows_by_ticker.setdefault(
                ticker,
                row_from_results(ticker, stage2_result=None, stage3_result=None, started_at=utc_now_iso(), finished_at=utc_now_iso()),
            )
    run_stage3_batch(tickers, out_root, args, rows_by_ticker, runtime)
    write_index_and_summary(out_root, rows_by_ticker, args)
    stage2_done, stage3_done, failures = batch_counts(rows_by_ticker)
    runtime.notifier.complete(stage2_done=stage2_done, stage3_done=stage3_done, failures=failures, disk=runtime.check_disk())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
