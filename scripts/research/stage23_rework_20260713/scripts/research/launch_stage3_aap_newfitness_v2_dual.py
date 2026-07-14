#!/usr/bin/env python3
"""Deploy, launch, monitor, and collect two independent AAP v2 GA runs.

VM and notebook never share candidate-level tasks.  The notebook receives one
source/data bundle, launches a standalone Python parent, and communicates again
only for status and final artifact collection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

from dask.distributed import Client

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
REPOSITORY_ROOT = HERE.parents[5]
HOST_RUNNER_REL = PurePosixPath(
    "scripts/research/stage23_rework_20260713/scripts/research/"
    "run_stage3_aap_newfitness_v2_host.py"
)
NOTEBOOK_ROOT = r"C:\kingmaker_aap_v2_20260714"
NOTEBOOK_WORKSPACE = NOTEBOOK_ROOT + r"\scripts\research\stage23_rework_20260713"
NOTEBOOK_OUT = (
    NOTEBOOK_ROOT
    + r"\data\_system\analysis\stage3_aap_newfitness_v2_20260714\AAP\NOTEBOOK_MAX"
)
VM_OUT = (
    REPOSITORY_ROOT
    / "data/_system/analysis/stage3_aap_newfitness_v2_20260714/AAP/VM_6PROC"
)
LOCAL_NOTEBOOK_OUT = (
    REPOSITORY_ROOT
    / "data/_system/analysis/stage3_aap_newfitness_v2_20260714/AAP/NOTEBOOK_MAX"
)
VM_PID_PATH = REPOSITORY_ROOT / "data/_system/ops/aap_newfitness_v2_vm.pid"
VM_SUPERVISOR_LOG = REPOSITORY_ROOT / "data/_system/ops/aap_newfitness_v2_vm_supervisor.log"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _protected_snapshot() -> dict[str, str]:
    paths = (
        REPOSITORY_ROOT / ".env",
        REPOSITORY_ROOT / "data/_system/market_history.csv",
        REPOSITORY_ROOT / "data/_system/market_history_v2.csv",
    )
    return {
        str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"): _sha256_file(path)
        for path in paths
    }


def _daemon_snapshot() -> dict[str, Any]:
    pid = 494330
    proc = Path(f"/proc/{pid}")
    if not proc.is_dir():
        raise RuntimeError(f"required daemon PID is not alive: {pid}")
    fields = (proc / "stat").read_text(encoding="utf-8").split()
    cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
        "utf-8", errors="replace"
    ).strip()
    return {
        "pid": pid,
        "state": fields[2] if len(fields) > 2 else None,
        "starttime_ticks": fields[21] if len(fields) > 21 else None,
        "cmdline": cmdline,
        "snapshot_source": "VM_proxy_for_notebook_independent_run",
    }


def _market_cutoff_date() -> str:
    import pandas as pd

    path = REPOSITORY_ROOT / "data/_system/market_history.csv"
    frame = pd.read_csv(path, usecols=["date"])
    return pd.to_datetime(frame["date"], errors="raise").max().date().isoformat()


def _iter_source_files() -> list[Path]:
    roots = (
        WORKSPACE_ROOT / "engine",
        WORKSPACE_ROOT / "scripts/research",
    )
    files: list[Path] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.suffix not in {".py", ".yaml", ".yml", ".json"}:
                continue
            files.append(path)
    files.append(WORKSPACE_ROOT / "config/policy.yaml")
    data_files = (
        REPOSITORY_ROOT / "data/_system/market_history.csv",
        REPOSITORY_ROOT / "data/_system/market_history_v2.csv",
        REPOSITORY_ROOT / "data/_system/calendars/us_xnys_2020_2027.json",
        REPOSITORY_ROOT / "data/_system/analysis/ohlc_snapshot_20260707/AAP_ohlcv.csv",
    )
    files.extend(data_files)
    unique = sorted({path.resolve() for path in files})
    missing = [str(path) for path in unique if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"bundle source missing: {missing}")
    return unique


def _build_payload() -> tuple[dict[str, bytes], dict[str, Any]]:
    payload: dict[str, bytes] = {}
    manifest: dict[str, dict[str, Any]] = {}
    for path in _iter_source_files():
        relative = path.relative_to(REPOSITORY_ROOT).as_posix()
        data = path.read_bytes()
        payload[relative] = data
        manifest[relative] = {"sha256": _sha256_bytes(data), "size": len(data)}
    return payload, {
        "file_count": len(payload),
        "byte_count": sum(len(data) for data in payload.values()),
        "files": manifest,
        "env_included": False,
        "candidate_communication": False,
    }


def _install_payload_task(
    root_text: str,
    payload: dict[str, bytes],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    root = Path(root_text)
    root.mkdir(parents=True, exist_ok=True)
    installed: dict[str, str] = {}
    for relative, data in payload.items():
        target = root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        expected = manifest["files"][relative]["sha256"]
        if actual != expected:
            raise RuntimeError(
                f"notebook bundle SHA mismatch: {relative}: expected={expected}, actual={actual}"
            )
        installed[relative] = actual
    return {
        "status": "INSTALLED",
        "root": str(root),
        "file_count": len(installed),
        "byte_count": sum(len(data) for data in payload.values()),
        "host": os.environ.get("COMPUTERNAME"),
        "python": sys.executable,
    }


def _launch_notebook_task(
    root_text: str,
    output_text: str,
    seed: int,
    workers: int,
    cutoff: str,
    protected_json: str,
    daemon_json: str,
    source_commit: str,
) -> dict[str, Any]:
    root = Path(root_text)
    workspace = root / "scripts/research/stage23_rework_20260713"
    runner_path = root.joinpath(*HOST_RUNNER_REL.parts)
    output = Path(output_text)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"notebook output must be new or empty: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = root / "notebook_supervisor.log"
    pid_path = root / "notebook_parent.pid"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(workspace),
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    command = [
        sys.executable,
        str(runner_path),
        "--out-dir",
        str(output),
        "--seed-base",
        str(seed),
        "--workers",
        str(workers),
        "--host-role",
        "notebook",
        "--market-cutoff-date",
        cutoff,
        "--protected-snapshot-json",
        protected_json,
        "--daemon-snapshot-json",
        daemon_json,
        "--source-git-commit",
        source_commit,
    ]
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    with log_path.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(workspace),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return {
        "status": "STARTED",
        "pid": process.pid,
        "workers": workers,
        "output": str(output),
        "log": str(log_path),
        "command": command,
    }


def _notebook_status_task(root_text: str, output_text: str) -> dict[str, Any]:
    root = Path(root_text)
    output = Path(output_text)
    pid_path = root / "notebook_parent.pid"
    pid = int(pid_path.read_text(encoding="utf-8").strip()) if pid_path.is_file() else None
    alive = False
    if pid:
        probe = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        alive = str(pid) in probe.stdout
    run_log = output / "run.log"
    supervisor = root / "notebook_supervisor.log"
    source = run_log if run_log.is_file() else supervisor
    tail = ""
    if source.is_file():
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-12:])
    return {
        "pid": pid,
        "alive": alive,
        "completed": (output / "official_final_summary.json").is_file(),
        "failed": (output / "failure.json").is_file(),
        "generation_log_rows": sum(
            1 for _ in (output / "generation_best_fitness.jsonl").open(encoding="utf-8")
        )
        if (output / "generation_best_fitness.jsonl").is_file()
        else 0,
        "tail": tail,
    }


def _collect_notebook_task(output_text: str) -> dict[str, bytes]:
    output = Path(output_text)
    if not output.is_dir():
        raise FileNotFoundError(output)
    return {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in sorted(output.rglob("*"))
        if path.is_file()
    }


def _windows_worker(client: Client) -> str:
    workers = list(client.scheduler_info()["workers"])
    os_names = client.run(lambda: __import__("os").name)
    matches = [address for address in workers if os_names.get(address) == "nt"]
    if len(matches) != 1:
        raise RuntimeError(f"exactly one Windows worker required: workers={workers}, os={os_names}")
    return matches[0]


def _launch_vm(
    *,
    seed: int,
    cutoff: str,
    source_commit: str,
) -> dict[str, Any]:
    if VM_OUT.exists() and any(VM_OUT.iterdir()):
        raise FileExistsError(f"VM output must be new or empty: {VM_OUT}")
    VM_OUT.parent.mkdir(parents=True, exist_ok=True)
    VM_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    runner_path = REPOSITORY_ROOT.joinpath(*HOST_RUNNER_REL.parts)
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(WORKSPACE_ROOT),
            "PYTHONUNBUFFERED": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    command = [
        str(REPOSITORY_ROOT / "venv/bin/python"),
        str(runner_path),
        "--out-dir",
        str(VM_OUT),
        "--seed-base",
        str(seed),
        "--workers",
        "6",
        "--host-role",
        "vm",
        "--market-cutoff-date",
        cutoff,
        "--source-git-commit",
        source_commit,
    ]
    with VM_SUPERVISOR_LOG.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(WORKSPACE_ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    VM_PID_PATH.write_text(str(process.pid), encoding="utf-8")
    return {
        "status": "STARTED",
        "pid": process.pid,
        "workers": 6,
        "output": str(VM_OUT),
        "log": str(VM_SUPERVISOR_LOG),
        "command": command,
    }


def _vm_status() -> dict[str, Any]:
    pid = int(VM_PID_PATH.read_text(encoding="utf-8").strip()) if VM_PID_PATH.is_file() else None
    alive = bool(pid and Path(f"/proc/{pid}").is_dir())
    run_log = VM_OUT / "run.log"
    source = run_log if run_log.is_file() else VM_SUPERVISOR_LOG
    tail = ""
    if source.is_file():
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-12:])
    return {
        "pid": pid,
        "alive": alive,
        "completed": (VM_OUT / "official_final_summary.json").is_file(),
        "failed": (VM_OUT / "failure.json").is_file(),
        "generation_log_rows": sum(
            1 for _ in (VM_OUT / "generation_best_fitness.jsonl").open(encoding="utf-8")
        )
        if (VM_OUT / "generation_best_fitness.jsonl").is_file()
        else 0,
        "tail": tail,
    }


def launch(scheduler: str, source_commit: str) -> dict[str, Any]:
    seed = 2026071401
    cutoff = _market_cutoff_date()
    protected = _protected_snapshot()
    daemon = _daemon_snapshot()
    payload, bundle_manifest = _build_payload()
    client = Client(scheduler, direct_to_workers=False)
    try:
        windows = _windows_worker(client)
        install_future = client.submit(
            _install_payload_task,
            NOTEBOOK_ROOT,
            payload,
            bundle_manifest,
            workers=[windows],
            allow_other_workers=False,
            pure=False,
        )
        install = install_future.result(timeout=300)
        notebook_future = client.submit(
            _launch_notebook_task,
            NOTEBOOK_ROOT,
            NOTEBOOK_OUT,
            seed,
            28,
            cutoff,
            json.dumps(protected, sort_keys=True),
            json.dumps(daemon, sort_keys=True),
            source_commit,
            workers=[windows],
            allow_other_workers=False,
            pure=False,
        )
        notebook = notebook_future.result(timeout=60)
    finally:
        client.close()
    vm = _launch_vm(seed=seed, cutoff=cutoff, source_commit=source_commit)
    return {
        "status": "DUAL_STARTED",
        "market_cutoff_date": cutoff,
        "seed": seed,
        "source_commit": source_commit,
        "bundle": bundle_manifest,
        "notebook_install": install,
        "notebook": notebook,
        "vm": vm,
        "protected_start": protected,
        "daemon_start": daemon,
    }


def status(scheduler: str) -> dict[str, Any]:
    client = Client(scheduler, direct_to_workers=False)
    try:
        windows = _windows_worker(client)
        future = client.submit(
            _notebook_status_task,
            NOTEBOOK_ROOT,
            NOTEBOOK_OUT,
            workers=[windows],
            allow_other_workers=False,
            pure=False,
        )
        notebook = future.result(timeout=60)
    finally:
        client.close()
    return {"vm": _vm_status(), "notebook": notebook}


def collect(scheduler: str) -> dict[str, Any]:
    client = Client(scheduler, direct_to_workers=False)
    try:
        windows = _windows_worker(client)
        future = client.submit(
            _collect_notebook_task,
            NOTEBOOK_OUT,
            workers=[windows],
            allow_other_workers=False,
            pure=False,
        )
        files = future.result(timeout=300)
    finally:
        client.close()
    LOCAL_NOTEBOOK_OUT.mkdir(parents=True, exist_ok=True)
    for relative, data in files.items():
        target = LOCAL_NOTEBOOK_OUT.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {
        "status": "COLLECTED",
        "source": NOTEBOOK_OUT,
        "destination": str(LOCAL_NOTEBOOK_OUT),
        "file_count": len(files),
        "byte_count": sum(len(data) for data in files.values()),
        "files": {
            relative: _sha256_bytes(data)
            for relative, data in sorted(files.items())
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dual independent AAP v2 launcher")
    parser.add_argument("mode", choices=("launch", "status", "collect"))
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    parser.add_argument("--source-commit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.mode == "launch":
        if not args.source_commit:
            raise SystemExit("--source-commit is required for launch")
        result = launch(args.scheduler, args.source_commit)
    elif args.mode == "status":
        result = status(args.scheduler)
    else:
        result = collect(args.scheduler)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
