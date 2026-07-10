from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT_DIR = ROOT / "backup/pre_cleanup_20260710"

TARGETS = {
    "data/_system/pipeline": "data__system__pipeline.tar.gz",
    "data/_system/condition_db_sell_omen_clean": "data__system__condition_db_sell_omen_clean.tar.gz",
    "data/_system/condition_db_sell_omen_lr8d85": "data__system__condition_db_sell_omen_lr8d85.tar.gz",
    "data/_system/logs": "data__system__logs.tar.gz",
}

EXPECTED = {
    "data/_system/pipeline": {"files": 4155, "bytes": 82632425},
    "data/_system/condition_db_sell_omen_clean": {"files": 183, "bytes": 129892573},
    "data/_system/condition_db_sell_omen_lr8d85": {"files": 85, "bytes": 63017369},
    "data/_system/logs": {"files": 8, "bytes": 61554242},
}

PROTECTED_EXACT = {
    ".env",
    ".env.backup",
    "data/_system/candidate_denylist.json",
    "scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001",
}
PROTECTED_NAMES = {
    "rulebooks_all.jsonl",
    "entry_rulebooks.jsonl",
    "final_rulebooks.jsonl",
    "survivors.jsonl",
    "validation_results.jsonl",
    "exit_trades.jsonl",
}


def source_files(relative: str) -> list[Path]:
    root = ROOT / relative
    return sorted(path for path in root.rglob("*") if path.is_file())


def relative_manifest(files: list[Path]) -> dict[str, int]:
    return {path.relative_to(ROOT).as_posix(): path.stat().st_size for path in files}


def assert_not_protected(path_text: str) -> None:
    path = Path(path_text)
    lowered = path_text.lower()
    assert path_text not in PROTECTED_EXACT, path_text
    assert not path_text.startswith("data/_system/analysis/"), path_text
    assert not path_text.startswith("exp_batch_stage123_2009_20260616_full/"), path_text
    assert path.name not in PROTECTED_NAMES, path_text
    assert "profile_catalog" not in path.name.lower(), path_text
    assert "frozen_oos" not in lowered and "oos_reproduce_frozen" not in lowered, path_text


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    assert not SNAPSHOT_DIR.exists(), f"snapshot directory already exists: {SNAPSHOT_DIR}"

    source_manifests: dict[str, dict[str, int]] = {}
    for relative, expected in TARGETS.items():
        target = ROOT / relative
        assert target.is_dir(), f"missing target: {relative}"
        files = source_files(relative)
        manifest = relative_manifest(files)
        assert len(manifest) == EXPECTED[relative]["files"], (relative, len(manifest))
        assert sum(manifest.values()) == EXPECTED[relative]["bytes"], (relative, sum(manifest.values()))
        for item in manifest:
            assert_not_protected(item)
        source_manifests[relative] = manifest

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, object]] = []

    for relative, archive_name in TARGETS.items():
        target = ROOT / relative
        archive = SNAPSHOT_DIR / archive_name
        with tarfile.open(archive, mode="w:gz", compresslevel=6) as tar:
            tar.add(target, arcname=relative, recursive=True)

        with tarfile.open(archive, mode="r:gz") as tar:
            archive_manifest = {
                member.name.rstrip("/"): member.size
                for member in tar.getmembers()
                if member.isfile()
            }
            unsafe = [member.name for member in tar.getmembers() if member.name.startswith("/") or ".." in Path(member.name).parts]
            assert not unsafe, (relative, unsafe[:10])

        source_manifest = source_manifests[relative]
        assert archive_manifest == source_manifest, (
            relative,
            len(source_manifest),
            len(archive_manifest),
            sum(source_manifest.values()),
            sum(archive_manifest.values()),
        )
        archive_size = archive.stat().st_size
        archive_hash = sha256(archive)
        results.append({
            "source_path": relative,
            "source_file_count": len(source_manifest),
            "source_bytes": sum(source_manifest.values()),
            "archive_path": archive.relative_to(ROOT).as_posix(),
            "archive_bytes": archive_size,
            "archive_sha256": archive_hash,
            "tar_file_count": len(archive_manifest),
            "tar_payload_bytes": sum(archive_manifest.values()),
            "integrity": "PASS",
        })
        print("SNAPSHOT_OK|" + json.dumps(results[-1], ensure_ascii=False, sort_keys=True), flush=True)

    # All four archives must pass before any source deletion begins.
    assert len(results) == len(TARGETS)
    assert all(row["integrity"] == "PASS" for row in results)

    for relative in TARGETS:
        shutil.rmtree(ROOT / relative)
        assert not (ROOT / relative).exists(), relative
        print(f"DELETED|{relative}", flush=True)

    print("FINAL|" + json.dumps({
        "snapshot_dir": SNAPSHOT_DIR.relative_to(ROOT).as_posix(),
        "archive_count": len(results),
        "source_file_count": sum(int(row["source_file_count"]) for row in results),
        "source_bytes": sum(int(row["source_bytes"]) for row in results),
        "archive_bytes": sum(int(row["archive_bytes"]) for row in results),
        "deleted_paths": list(TARGETS),
        "status": "COMPLETE",
    }, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
