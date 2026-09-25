#!/usr/bin/env python3
"""Live-sync the Stage23 engine bundle into already-running Dask workers.

Windows cannot rename ``C:\\kingmaker`` while Loguru keeps files open.  This
installer verifies a temporary extraction and then copies bundle files into the
existing root in place.  Runtime logs/data are preserved.  The task functions
are defined in ``__main__`` so Dask serializes them by value.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from dask.distributed import Client

HERE = Path(__file__).resolve()
BASE_PATH = HERE.with_name("deploy_dask_worker_bundle.py")
BASE_SPEC = importlib.util.spec_from_file_location("_kingmaker_bundle_builder", BASE_PATH)
if BASE_SPEC is None or BASE_SPEC.loader is None:
    raise RuntimeError(f"cannot load bundle builder: {BASE_PATH}")
BASE = importlib.util.module_from_spec(BASE_SPEC)
BASE_SPEC.loader.exec_module(BASE)

WINDOWS_PROJECT_ROOT = BASE.WINDOWS_PROJECT_ROOT
LINUX_PROJECT_ROOT = BASE.LINUX_PROJECT_ROOT
KEY_IMPORTS = tuple(BASE.KEY_IMPORTS)
MARKET_HISTORY_SOURCE = BASE.MARKET_HISTORY_SOURCE
MARKET_HISTORY_EXPECTED_SHA256 = BASE.MARKET_HISTORY_EXPECTED_SHA256


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(bundle: bytes, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError(f"unsafe archive member: {info.filename}")
            output = target.joinpath(*member.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            if info.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            with archive.open(info) as source, output.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            resolved = output.resolve()
            if resolved != root and root not in resolved.parents:
                raise RuntimeError(f"archive escaped target root: {info.filename}")


def _verify_tree(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "DEPLOYMENT_MANIFEST.json").read_text(encoding="utf-8"))
    mismatches: list[dict[str, Any]] = []
    for relative, expected in dict(manifest["files"]).items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file():
            mismatches.append({"path": relative, "reason": "missing"})
            continue
        actual = _sha256_file(path)
        if actual != expected["sha256"]:
            mismatches.append(
                {
                    "path": relative,
                    "reason": "sha256",
                    "expected": expected["sha256"],
                    "actual": actual,
                }
            )
    if mismatches:
        raise RuntimeError(f"tree verification failed: {mismatches[:10]}")
    return {
        "file_count": int(manifest["file_count"]),
        "policy_sha256": manifest["policy_sha256"],
        "key_imports": list(manifest["key_imports"]),
    }


def _sync_tree(source: Path, destination: Path) -> int:
    copied = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    return copied


def _activate(root: Path, key_imports: tuple[str, ...]) -> dict[str, Any]:
    vendor = root / "vendor"
    preferred = [str(root), str(vendor)]
    sys.path[:] = preferred + [item for item in sys.path if item not in preferred]
    for name in tuple(sys.modules):
        if name == "engine" or name.startswith("engine."):
            sys.modules.pop(name, None)
    if os.name == "nt":
        sys.modules.pop("win32_setctime", None)
    importlib.invalidate_caches()

    imports: dict[str, str | None] = {}
    for module_name in key_imports:
        module = importlib.import_module(module_name)
        imports[module_name] = getattr(module, "__file__", None)
    return {
        "project_root": str(root),
        "vendor_root": str(vendor),
        "sys_path_head": list(sys.path[:5]),
        "imports": imports,
    }


def _install_task(
    bundle: bytes,
    expected_bundle_sha256: str,
    target_root: str,
    worker_address: str,
    key_imports: tuple[str, ...],
) -> dict[str, Any]:
    started = time.time()
    actual_bundle_sha = _sha256_bytes(bundle)
    if actual_bundle_sha != expected_bundle_sha256:
        raise RuntimeError(
            f"bundle SHA mismatch: expected={expected_bundle_sha256}, actual={actual_bundle_sha}"
        )

    root = Path(target_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    temp = root.parent / f".{root.name}.live-{uuid.uuid4().hex}"
    temp.mkdir(parents=True)
    try:
        _safe_extract(bundle, temp)
        temp_verification = _verify_tree(temp)
        root.mkdir(parents=True, exist_ok=True)
        copied = _sync_tree(temp, root)
        (root / "data/logs").mkdir(parents=True, exist_ok=True)
        root_verification = _verify_tree(root)
        activation = _activate(root, key_imports)
        return {
            "worker_address": worker_address,
            "status": "DEPLOYED_IN_PLACE",
            "target_root": str(root),
            "bundle_sha256": actual_bundle_sha,
            "copied_file_count": copied,
            "temp_verification": temp_verification,
            "root_verification": root_verification,
            "activation": activation,
            "os_name": os.name,
            "platform": sys.platform,
            "python": sys.version,
            "elapsed_seconds": time.time() - started,
        }
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def _reset_and_import(roots: dict[str, str], key_imports: tuple[str, ...]) -> dict[str, Any]:
    root = Path(roots["nt" if os.name == "nt" else "posix"])
    return _activate(root, key_imports)


def _scatter_probe(payload: dict[str, Any], expected_sha256: str) -> dict[str, Any]:
    import pandas as pd

    source_bytes = payload["source_bytes"]
    frame = payload["market_history_df"]
    actual = hashlib.sha256(source_bytes).hexdigest()
    return {
        "source_sha256": payload["source_sha256"],
        "actual_sha256": actual,
        "expected_sha256": expected_sha256,
        "sha_match": actual == expected_sha256 == payload["source_sha256"],
        "byte_count": len(source_bytes),
        "is_dataframe": isinstance(frame, pd.DataFrame),
        "row_count": len(frame),
        "columns": list(frame.columns),
        "local_file_read": False,
    }


def _scatter_market(client: Client, workers: list[str]) -> dict[str, Any]:
    import pandas as pd

    source_bytes = MARKET_HISTORY_SOURCE.read_bytes()
    actual = _sha256_bytes(source_bytes)
    if actual != MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError(
            f"root market SHA mismatch: expected={MARKET_HISTORY_EXPECTED_SHA256}, actual={actual}"
        )
    payload = {
        "source_sha256": actual,
        "source_bytes": source_bytes,
        "market_history_df": pd.read_csv(io.BytesIO(source_bytes)),
    }
    results: dict[str, Any] = {}
    for address in workers:
        scattered = client.scatter(
            payload,
            workers=[address],
            broadcast=False,
            direct=False,
            hash=False,
        )
        future = client.submit(
            _scatter_probe,
            scattered,
            actual,
            workers=[address],
            allow_other_workers=False,
            pure=False,
        )
        result = client.gather(future, direct=False)
        if not result["sha_match"] or not result["is_dataframe"]:
            raise RuntimeError(f"scatter verification failed: {address}: {result}")
        results[address] = result
    return {
        "source": str(MARKET_HISTORY_SOURCE),
        "source_sha256": actual,
        "source_byte_count": len(source_bytes),
        "worker_results": results,
        "delivery": "Client.scatter then function argument",
        "worker_local_file_read": False,
    }


def run(scheduler: str) -> dict[str, Any]:
    bundle, bundle_summary = BASE._build_bundle()
    client = Client(scheduler, direct_to_workers=False)
    try:
        workers = list(client.scheduler_info()["workers"])
        if len(workers) < 2:
            raise RuntimeError(f"at least two workers required, got {workers}")
        os_names = client.run(lambda: __import__("os").name)
        futures = []
        for address in workers:
            target = WINDOWS_PROJECT_ROOT if os_names[address] == "nt" else LINUX_PROJECT_ROOT
            futures.append(
                client.submit(
                    _install_task,
                    bundle,
                    bundle_summary["bundle_sha256"],
                    target,
                    address,
                    KEY_IMPORTS,
                    workers=[address],
                    allow_other_workers=False,
                    pure=False,
                )
            )
        install_results = client.gather(futures, direct=False)

        roots = {"nt": WINDOWS_PROJECT_ROOT, "posix": LINUX_PROJECT_ROOT}
        activation = client.run(_reset_and_import, roots, KEY_IMPORTS)
        exact_import_probe = client.run(
            lambda root_map: (
                __import__("sys").path.insert(
                    0,
                    root_map["nt" if __import__("os").name == "nt" else "posix"],
                ),
                __import__("engine").__file__,
            ),
            roots,
        )
        key_import_probe = client.run(
            lambda names: {
                name: getattr(__import__(name, fromlist=["*"]), "__file__", None)
                for name in names
            },
            KEY_IMPORTS,
        )
        scatter = _scatter_market(client, workers)

        for address, probe in exact_import_probe.items():
            expected = WINDOWS_PROJECT_ROOT if os_names[address] == "nt" else LINUX_PROJECT_ROOT
            if not str(probe[1]).lower().startswith(expected.lower()):
                raise RuntimeError(
                    f"wrong engine import root: {address}: {probe[1]}; expected={expected}"
                )

        return {
            "status": "PASS",
            "scheduler": scheduler,
            "bundle": bundle_summary,
            "workers": workers,
            "worker_os_names": os_names,
            "install_results": install_results,
            "activation": activation,
            "exact_client_run_import_probe": exact_import_probe,
            "key_import_probe": key_import_probe,
            "market_snapshot_scatter": scatter,
            "fitness_data_contract": {
                "direct_worker_file_read": False,
                "context_prepared_on_client": True,
                "context_delivery": "Client.scatter then fitness function argument",
                "backtest_function": "run_entry_backtest_period(rulebook, ctx, start, end)",
                "backtest_dataframe_source": "ctx['df'] and base_backtest_kwargs(ctx)",
                "core_backtest_refactor_required": False,
            },
            "preload_paths": {
                "windows": r"C:\kingmaker\worker_preload.py",
                "linux": f"{LINUX_PROJECT_ROOT}/worker_preload.py",
            },
        }
    finally:
        client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live-sync Stage23 engine bundle to Dask workers")
    parser.add_argument("--scheduler", default="tcp://localhost:8786")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args.scheduler)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
