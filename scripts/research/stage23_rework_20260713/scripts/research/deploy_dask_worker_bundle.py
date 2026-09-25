#!/usr/bin/env python3
"""Deploy the Stage23 research engine bundle to heterogeneous Dask workers.

The bundle is built in memory and sent through Dask task arguments.  The
Windows worker receives ``C:\\kingmaker`` and the Linux worker receives an
isolated mirror under ``/tmp``.  Both workers therefore import the exact same
Stage23 source tree.

Included:
- complete Stage23 ``engine`` package
- ``config/policy.yaml``
- ``worker_preload.py``
- pure-Python ``loguru`` and ``python-dotenv`` packages needed by fitness

Excluded:
- ``.env`` and secrets
- market/price files
- output data and caches

Market history is transferred separately with ``Client.scatter`` and its root
SHA-256 is checked again inside each worker.  No worker reads a local market
file.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from dask.distributed import Client

HERE = Path(__file__).resolve()
WORKSPACE_ROOT = HERE.parents[2]
REPOSITORY_ROOT = HERE.parents[5]
ENGINE_SOURCE = WORKSPACE_ROOT / "engine"
POLICY_SOURCE = WORKSPACE_ROOT / "config/policy.yaml"
PRELOAD_SOURCE = HERE.with_name("dask_worker_preload.py")
SITE_PACKAGES = REPOSITORY_ROOT / "venv/lib/python3.10/site-packages"
VENDOR_SOURCES = {
    "loguru": SITE_PACKAGES / "loguru",
    "dotenv": SITE_PACKAGES / "dotenv",
}
MARKET_HISTORY_SOURCE = REPOSITORY_ROOT / "data/_system/market_history.csv"
MARKET_HISTORY_EXPECTED_SHA256 = "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38"
WINDOWS_PROJECT_ROOT = r"C:\kingmaker"
LINUX_PROJECT_ROOT = "/tmp/kingmaker_stage23_rework_20260713"
KEY_IMPORTS = (
    "engine",
    "engine.core.config",
    "engine.core.logger",
    "engine.strategies.rulebook",
    "engine.learning.backtest",
    "engine.learning.execution_mode_backtest",
    "engine.learning.genetic",
    "engine.learning.genetic_parallel",
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(path: Path) -> bool:
    return (
        "__pycache__" in path.parts
        or path.suffix in {".pyc", ".pyo"}
        or path.name == ".DS_Store"
    )


def _iter_tree(source: Path, archive_prefix: str) -> Iterable[tuple[str, Path]]:
    if not source.is_dir():
        raise FileNotFoundError(source)
    for path in sorted(source.rglob("*")):
        if path.is_file() and not _excluded(path):
            relative = path.relative_to(source).as_posix()
            yield f"{archive_prefix.rstrip('/')}/{relative}", path


def _build_bundle() -> tuple[bytes, dict[str, Any]]:
    required = [ENGINE_SOURCE, POLICY_SOURCE, PRELOAD_SOURCE, *VENDOR_SOURCES.values()]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"bundle sources missing: {missing}")

    entries: list[tuple[str, Path]] = list(_iter_tree(ENGINE_SOURCE, "engine"))
    entries.append(("config/policy.yaml", POLICY_SOURCE))
    entries.append(("worker_preload.py", PRELOAD_SOURCE))
    for package, source in VENDOR_SOURCES.items():
        entries.extend(_iter_tree(source, f"vendor/{package}"))

    file_manifest: dict[str, dict[str, Any]] = {}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for archive_name, source in entries:
            payload = source.read_bytes()
            archive.writestr(archive_name, payload)
            file_manifest[archive_name] = {
                "sha256": _sha256_bytes(payload),
                "size": len(payload),
            }
        manifest = {
            "format": "kingmaker_dask_worker_bundle_v1",
            "workspace_source": str(WORKSPACE_ROOT),
            "policy_sha256": _sha256_file(POLICY_SOURCE),
            "market_data_included": False,
            "env_included": False,
            "file_count": len(file_manifest),
            "key_imports": list(KEY_IMPORTS),
            "files": file_manifest,
        }
        archive.writestr(
            "DEPLOYMENT_MANIFEST.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )

    bundle = stream.getvalue()
    return bundle, {
        "bundle_sha256": _sha256_bytes(bundle),
        "bundle_size": len(bundle),
        "file_count": len(file_manifest),
        "engine_python_file_count": sum(
            name.startswith("engine/") and name.endswith(".py") for name in file_manifest
        ),
        "policy_sha256": manifest["policy_sha256"],
        "vendor_packages": sorted(VENDOR_SOURCES),
        "env_included": False,
        "market_data_included": False,
    }


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
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
        raise RuntimeError(f"deployed tree verification failed: {mismatches[:10]}")
    return {
        "file_count": manifest["file_count"],
        "policy_sha256": manifest["policy_sha256"],
        "key_imports": manifest["key_imports"],
    }


def _activate_root(root: Path, *, purge_engine: bool) -> dict[str, Any]:
    vendor = root / "vendor"
    preferred = [str(root), str(vendor)]
    sys.path[:] = preferred + [item for item in sys.path if item not in preferred]
    if purge_engine:
        for name in tuple(sys.modules):
            if name == "engine" or name.startswith("engine."):
                sys.modules.pop(name, None)
    importlib.invalidate_caches()

    imports: dict[str, str | None] = {}
    for module_name in KEY_IMPORTS:
        module = importlib.import_module(module_name)
        imports[module_name] = getattr(module, "__file__", None)
    return {
        "project_root": str(root),
        "vendor_root": str(vendor),
        "sys_path_head": list(sys.path[:5]),
        "imports": imports,
    }


def _deploy_task(
    bundle: bytes,
    expected_bundle_sha256: str,
    target_root: str,
    worker_address: str,
) -> dict[str, Any]:
    started = time.time()
    actual_bundle_sha = _sha256_bytes(bundle)
    if actual_bundle_sha != expected_bundle_sha256:
        raise RuntimeError(
            f"bundle SHA mismatch: expected={expected_bundle_sha256}, actual={actual_bundle_sha}"
        )

    root = Path(target_root)
    root.parent.mkdir(parents=True, exist_ok=True)
    temp = root.parent / f".{root.name}.deploy-{uuid.uuid4().hex}"
    previous = root.parent / f".{root.name}.previous-{uuid.uuid4().hex}"
    temp.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            _safe_extract(archive, temp)
        verification = _verify_tree(temp)
        (temp / "data/logs").mkdir(parents=True, exist_ok=True)

        had_previous = root.exists()
        if had_previous:
            root.rename(previous)
        try:
            temp.rename(root)
        except Exception:
            if had_previous and previous.exists() and not root.exists():
                previous.rename(root)
            raise
        if previous.exists():
            shutil.rmtree(previous)

        activation = _activate_root(root, purge_engine=True)
        return {
            "worker_address": worker_address,
            "status": "DEPLOYED",
            "target_root": str(root),
            "bundle_sha256": actual_bundle_sha,
            "verification": verification,
            "activation": activation,
            "os_name": os.name,
            "platform": sys.platform,
            "python": sys.version,
            "elapsed_seconds": time.time() - started,
        }
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def _reset_and_import(roots: dict[str, str]) -> dict[str, Any]:
    root = Path(roots["nt" if os.name == "nt" else "posix"])
    return _activate_root(root, purge_engine=True)


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


def _deploy_all(
    client: Client,
    bundle: bytes,
    bundle_summary: dict[str, Any],
) -> dict[str, Any]:
    workers = list(client.scheduler_info()["workers"])
    if len(workers) < 2:
        raise RuntimeError(f"at least two workers required, got {workers}")

    os_names = client.run(lambda: __import__("os").name)
    futures = []
    for address in workers:
        target = WINDOWS_PROJECT_ROOT if os_names[address] == "nt" else LINUX_PROJECT_ROOT
        futures.append(
            client.submit(
                _deploy_task,
                bundle,
                bundle_summary["bundle_sha256"],
                target,
                address,
                workers=[address],
                allow_other_workers=False,
                pure=False,
            )
        )
    results = client.gather(futures, direct=False)
    return {
        "workers": workers,
        "worker_os_names": os_names,
        "results": results,
    }


def _scatter_market(client: Client, workers: list[str]) -> dict[str, Any]:
    import pandas as pd

    source_bytes = MARKET_HISTORY_SOURCE.read_bytes()
    actual = _sha256_bytes(source_bytes)
    if actual != MARKET_HISTORY_EXPECTED_SHA256:
        raise RuntimeError(
            f"root market_history SHA mismatch: expected={MARKET_HISTORY_EXPECTED_SHA256}, actual={actual}"
        )
    payload = {
        "source_sha256": actual,
        "source_bytes": source_bytes,
        "market_history_df": pd.read_csv(io.BytesIO(source_bytes)),
    }

    rows: dict[str, Any] = {}
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
            raise RuntimeError(f"scatter validation failed: {address}: {result}")
        rows[address] = result
    return {
        "source": str(MARKET_HISTORY_SOURCE),
        "source_sha256": actual,
        "source_byte_count": len(source_bytes),
        "worker_results": rows,
        "delivery": "Client.scatter then function argument",
        "worker_local_file_read": False,
    }


def run(scheduler: str) -> dict[str, Any]:
    bundle, bundle_summary = _build_bundle()
    client = Client(scheduler, direct_to_workers=False)
    try:
        deployment = _deploy_all(client, bundle, bundle_summary)
        roots = {"nt": WINDOWS_PROJECT_ROOT, "posix": LINUX_PROJECT_ROOT}

        # Persist the path in the already-running worker processes.  The same
        # operation is repeated by worker_preload.py after future restarts.
        activation = client.run(_reset_and_import, roots)

        # Requested validation pattern.  Returning __file__ instead of the
        # module object keeps the result serializable while proving import.
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
            lambda: {
                module_name: getattr(
                    __import__(module_name, fromlist=["*"]),
                    "__file__",
                    None,
                )
                for module_name in KEY_IMPORTS
            }
        )
        scatter = _scatter_market(client, deployment["workers"])

        expected_roots = {
            address: (
                WINDOWS_PROJECT_ROOT
                if deployment["worker_os_names"][address] == "nt"
                else LINUX_PROJECT_ROOT
            )
            for address in deployment["workers"]
        }
        for address, probe in exact_import_probe.items():
            engine_file = str(probe[1])
            expected_root = expected_roots[address]
            if not engine_file.lower().startswith(expected_root.lower()):
                raise RuntimeError(
                    f"wrong engine import root: {address}: {engine_file}; expected={expected_root}"
                )

        return {
            "status": "PASS",
            "scheduler": scheduler,
            "bundle": bundle_summary,
            "deployment": deployment,
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
    parser = argparse.ArgumentParser(description="Deploy Stage23 engine bundle to Dask workers")
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
