from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
CSV_PATH = OUT / "filter_gate_outputs.csv"
POOL_DIR = ROOT / "data/_system/central/stage3_live_pool"


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""


def stats(paths: list[Path]) -> dict[str, object]:
    files = [path for path in paths if path.is_file()]
    mtimes = [path.stat().st_mtime for path in files]
    records = 0
    for path in files:
        with path.open("rb") as handle:
            records += sum(1 for line in handle if line.strip())
    return {
        "path_count": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
        "record_count": records,
        "latest_modified_utc": utc_iso(max(mtimes)) if mtimes else "",
        "sample_paths": "|".join(path.relative_to(ROOT).as_posix() for path in files[:3]),
    }


def main() -> int:
    with CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])

    for row in rows:
        if row.get("artifact") == "Stage3 live-pool summaries/rejected samples":
            row["artifact"] = "Stage3 live-pool summaries"
            row["note"] = "stale pool metadata; two pool variants were built from different source snapshots"

    rejected_paths = sorted(POOL_DIR.glob("rejected_sample*.jsonl"))
    rejected_stats = stats(rejected_paths)
    rows.append(
        {
            "artifact": "Stage3 live-pool rejected samples",
            "path_pattern": "data/_system/central/stage3_live_pool/rejected_sample*.jsonl",
            "producer": "build_stage3_live_pool.py",
            "live_usage": "not current runtime",
            "verdict": "STALE_OUTPUT",
            "regeneration": "pool rebuild와 함께 재생성",
            "note": "6/26의 두 비활성 pool 빌드에서 나온 탈락 샘플",
            **rejected_stats,
        }
    )

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print({"rows": len(rows), "rejected_sample_files": len(rejected_paths), "rejected_sample_bytes": rejected_stats["size_bytes"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
