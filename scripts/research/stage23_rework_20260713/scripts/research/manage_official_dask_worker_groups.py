#!/usr/bin/env python3
"""Start, inspect and stop temporary process-based Dask worker groups.

The existing VM and notebook workers remain available as management channels.
This script starts eight 1-thread VM workers and twenty-eight 1-thread Windows
notebook workers.  Each child worker uses no Dask memory limit and loads the
deployed Stage23 preload before accepting tasks.
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

from dask.distributed import Client

VM_PREFIX = "official-vm"
NOTEBOOK_PREFIX = "official-notebook"
VM_WORKERS = 8
NOTEBOOK_WORKERS = 28
WINDOWS_PRELOAD = r"C:\kingmaker\worker_preload.py"
LINUX_PRELOAD = "/tmp/kingmaker_stage23_rework_20260713/worker_preload.py"


def _worker_groups(client: Client) -> dict[str, dict[str, Any]]:
    info = client.scheduler_info()["workers"]
    return {
        address: {
            "name": str(row.get("name")),
            "nthreads": int(row.get("nthreads", 0)),
            "memory_limit": int(row.get("memory_limit", 0)),
            "status": row.get("status"),
        }
        for address, row in info.items()
        if str(row.get("name", "")).startswith((VM_PREFIX, NOTEBOOK_PREFIX))
    }


def _launch_windows_group() -> dict[str, Any]:
    args = [
        sys.executable,
        "-m",
        "distributed.cli.dask_worker",
        "tcp://localhost:8786",
        "--nthreads",
        "1",
        "--nworkers",
        str(NOTEBOOK_WORKERS),
        "--memory-limit",
        "0",
        "--no-dashboard",
        "--name",
        NOTEBOOK_PREFIX,
        "--preload",
        WINDOWS_PRELOAD,
    ]
    creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return {
        "pid": int(process.pid),
        "python": sys.executable,
        "args": args,
        "creationflags": creationflags,
    }


def _stop_windows_group(pid: int) -> dict[str, Any]:
    completed = subprocess.run(
        ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return {
        "pid": int(pid),
        "returncode": int(completed.returncode),
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }


def _find_management_notebook_worker(client: Client) -> str:
    environments = client.run(
        lambda: {
            "os_name": __import__("os").name,
            "python": __import__("sys").executable,
        }
    )
    candidates = [
        address
        for address, row in environments.items()
        if row["os_name"] == "nt"
        and not str(client.scheduler_info()["workers"][address].get("name", "")).startswith(
            NOTEBOOK_PREFIX
        )
    ]
    if not candidates:
        raise RuntimeError("original Windows management worker not found")
    return sorted(candidates)[0]


def _launch_vm_group() -> dict[str, Any]:
    environment = dict(os.environ)
    user_site = "/home/g3000kkw/.local/lib/python3.10/site-packages"
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = user_site if not existing else f"{user_site}:{existing}"
    args = [
        sys.executable,
        "-m",
        "distributed.cli.dask_worker",
        "tcp://localhost:8786",
        "--nthreads",
        "1",
        "--nworkers",
        str(VM_WORKERS),
        "--memory-limit",
        "0",
        "--no-dashboard",
        "--name",
        VM_PREFIX,
        "--preload",
        LINUX_PRELOAD,
    ]
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=environment,
    )
    return {
        "pid": int(process.pid),
        "python": sys.executable,
        "args": args,
        "start_new_session": True,
    }


def _stop_vm_group(pid: int) -> dict[str, Any]:
    try:
        os.killpg(int(pid), signal.SIGTERM)
        return {"pid": int(pid), "signal": "SIGTERM", "status": "sent"}
    except ProcessLookupError:
        return {"pid": int(pid), "status": "already_gone"}


def _wait_for_counts(client: Client, *, vm: int, notebook: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + float(timeout)
    latest: dict[str, dict[str, Any]] = {}
    while time.monotonic() < deadline:
        latest = _worker_groups(client)
        vm_count = sum(row["name"].startswith(VM_PREFIX) for row in latest.values())
        notebook_count = sum(
            row["name"].startswith(NOTEBOOK_PREFIX) for row in latest.values()
        )
        if vm_count == vm and notebook_count == notebook:
            return {
                "status": "PASS",
                "vm_count": vm_count,
                "notebook_count": notebook_count,
                "workers": latest,
            }
        time.sleep(2.0)
    raise RuntimeError(
        f"worker group count timeout: expected vm={vm}, notebook={notebook}, actual={latest}"
    )


def start(client: Client, timeout: float) -> dict[str, Any]:
    existing = _worker_groups(client)
    if existing:
        raise RuntimeError(f"official worker groups already exist: {existing}")
    vm_launch = _launch_vm_group()
    management_worker = _find_management_notebook_worker(client)
    notebook_future = client.submit(
        _launch_windows_group,
        workers=[management_worker],
        allow_other_workers=False,
        pure=False,
        key="launch-official-notebook-worker-group",
    )
    notebook_launch = client.gather(notebook_future, direct=False)
    counts = _wait_for_counts(
        client,
        vm=VM_WORKERS,
        notebook=NOTEBOOK_WORKERS,
        timeout=timeout,
    )
    return {
        "status": "PASS",
        "vm_launch": vm_launch,
        "notebook_launch": notebook_launch,
        "management_notebook_worker": management_worker,
        "registered": counts,
    }


def stop(client: Client, vm_pid: int, notebook_pid: int, timeout: float) -> dict[str, Any]:
    groups = _worker_groups(client)
    addresses = list(groups)
    retire_result = None
    if addresses:
        retire_result = client.retire_workers(
            workers=addresses,
            close_workers=True,
            remove=True,
        )
    vm_stop = _stop_vm_group(vm_pid)
    management_worker = _find_management_notebook_worker(client)
    notebook_future = client.submit(
        _stop_windows_group,
        int(notebook_pid),
        workers=[management_worker],
        allow_other_workers=False,
        pure=False,
        key="stop-official-notebook-worker-group",
    )
    notebook_stop = client.gather(notebook_future, direct=False)
    counts = _wait_for_counts(client, vm=0, notebook=0, timeout=timeout)
    return {
        "status": "PASS",
        "retire_result": retire_result,
        "vm_stop": vm_stop,
        "notebook_stop": notebook_stop,
        "remaining": counts,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage official Dask worker groups")
    parser.add_argument("action", choices=("start", "status", "stop"))
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--vm-pid", type=int)
    parser.add_argument("--notebook-pid", type=int)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = Client(args.scheduler, direct_to_workers=False)
    try:
        if args.action == "start":
            result = start(client, args.timeout)
        elif args.action == "status":
            result = {"status": "PASS", "workers": _worker_groups(client)}
        else:
            if args.vm_pid is None or args.notebook_pid is None:
                raise ValueError("stop requires --vm-pid and --notebook-pid")
            result = stop(client, args.vm_pid, args.notebook_pid, args.timeout)
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
