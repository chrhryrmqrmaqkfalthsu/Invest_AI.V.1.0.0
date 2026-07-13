#!/usr/bin/env python3
"""정식 Stage3 qualify 완료 후 안전한 6병렬 후속 전환 오케스트레이터.

- 기존 --stage all 프로세스의 qualify_result.json 생성을 감시
- qualify checkpoint 생성 즉시 기존 PID를 TERM으로 종료
- 통과 종목만 entry-only 재개
- entry 완료 후 AAP 3 worker + POWI 3 worker로 post-entry 병렬 실행
- 전체 후보 worker 합계 최대 6
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
PYTHON = WORKSPACE_ROOT.parents[2] / "venv/bin/python"
RESUME = HERE.with_name("run_stage3_parallel_resume.py")


def _log(path: Path, event: str, **payload: Any) -> None:
    row = {"ts": time.time(), "event": event, **payload}
    text = json.dumps(row, ensure_ascii=False)
    print(text, flush=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text + "\n")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _terminate(pid: int, log_path: Path, ticker: str) -> None:
    if not _alive(pid):
        _log(log_path, "original_process_already_stopped", ticker=ticker, pid=pid)
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 10.0
    while time.time() < deadline and _alive(pid):
        time.sleep(0.2)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
        _log(log_path, "original_process_sigkill", ticker=ticker, pid=pid)
    else:
        _log(log_path, "original_process_stopped", ticker=ticker, pid=pid)


def _run(cmd: list[str], log_file: Path, event_log: Path, ticker: str, stage: str) -> int:
    _log(event_log, "stage_start", ticker=ticker, stage=stage, cmd=cmd)
    with log_file.open("a", encoding="utf-8") as handle:
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, cwd=WORKSPACE_ROOT)
        code = proc.wait()
    _log(event_log, "stage_done", ticker=ticker, stage=stage, returncode=code)
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--aap-pid", required=True, type=int)
    parser.add_argument("--powi-pid", required=True, type=int)
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    event_log = root / "auto_parallel_handoff.log"
    configs = {
        "AAP": {"pid": args.aap_pid, "seed": 2026071301, "dir": root / "AAP"},
        "POWI": {"pid": args.powi_pid, "seed": 2026071302, "dir": root / "POWI"},
    }
    _log(event_log, "watch_start", root=str(root), configs={k: {"pid": v["pid"], "seed": v["seed"]} for k, v in configs.items()})

    pending = set(configs)
    qualified: list[str] = []
    while pending:
        for ticker in list(pending):
            cfg = configs[ticker]
            qpath = cfg["dir"] / "qualify_result.json"
            if qpath.is_file():
                data = json.loads(qpath.read_text(encoding="utf-8"))
                _log(event_log, "qualify_checkpoint_detected", ticker=ticker, qualified=bool(data.get("qualified")), all3_pass_count=int(data.get("all3_pass_count", 0) or 0))
                _terminate(int(cfg["pid"]), event_log, ticker)
                if any((cfg["dir"] / name).exists() for name in ("entry_result.json", "entry_rulebooks.jsonl", "entry_rejected_overlap.json")):
                    _log(event_log, "entry_artifact_collision", ticker=ticker)
                    return 3
                if bool(data.get("qualified")):
                    qualified.append(ticker)
                pending.remove(ticker)
        if pending:
            time.sleep(max(0.05, args.poll_seconds))

    if not qualified:
        _log(event_log, "no_qualified_tickers")
        return 0

    entry_procs: dict[str, subprocess.Popen[Any]] = {}
    entry_logs: dict[str, Any] = {}
    for ticker in qualified:
        cfg = configs[ticker]
        cmd = [
            str(PYTHON), str(RESUME), "--ticker", ticker,
            "--out-dir", str(cfg["dir"]), "--seed-base", str(cfg["seed"]),
            "--stage", "entry",
        ]
        log_file = cfg["dir"] / "entry_resume.log"
        handle = log_file.open("a", encoding="utf-8")
        entry_logs[ticker] = handle
        entry_procs[ticker] = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, cwd=WORKSPACE_ROOT)
        _log(event_log, "entry_resume_started", ticker=ticker, pid=entry_procs[ticker].pid)

    entry_ok: list[str] = []
    for ticker, proc in entry_procs.items():
        code = proc.wait()
        entry_logs[ticker].close()
        _log(event_log, "entry_resume_finished", ticker=ticker, returncode=code)
        cfg = configs[ticker]
        if code == 0 and (cfg["dir"] / "entry_rulebooks.jsonl").is_file():
            entry_ok.append(ticker)

    if not entry_ok:
        _log(event_log, "no_entry_survivors")
        return 0

    workers_each = 6 if len(entry_ok) == 1 else 3
    post_procs: dict[str, subprocess.Popen[Any]] = {}
    post_logs: dict[str, Any] = {}
    for ticker in entry_ok:
        cfg = configs[ticker]
        cmd = [
            str(PYTHON), str(RESUME), "--ticker", ticker,
            "--out-dir", str(cfg["dir"]), "--seed-base", str(cfg["seed"]),
            "--stage", "post-entry", "--max-workers", str(workers_each),
        ]
        log_file = cfg["dir"] / "post_entry_parallel.log"
        handle = log_file.open("a", encoding="utf-8")
        post_logs[ticker] = handle
        post_procs[ticker] = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, cwd=WORKSPACE_ROOT)
        _log(event_log, "post_entry_parallel_started", ticker=ticker, pid=post_procs[ticker].pid, workers=workers_each)

    failed = False
    for ticker, proc in post_procs.items():
        code = proc.wait()
        post_logs[ticker].close()
        _log(event_log, "post_entry_parallel_finished", ticker=ticker, returncode=code)
        failed = failed or code != 0

    _log(event_log, "handoff_complete", qualified=qualified, entry_ok=entry_ok, workers_each=workers_each, failed=failed)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
