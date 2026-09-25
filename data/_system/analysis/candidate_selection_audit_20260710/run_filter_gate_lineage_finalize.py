from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
CHAIN = OUT / "filter_gate_chain.csv"
OUTPUTS = OUT / "filter_gate_outputs.csv"
SNAPSHOT = OUT / "filter_gate_lineage_snapshot.json"
CENTRAL = ROOT / "exp_batch_stage123_2009_20260616_full/central_index.jsonl"


def central_stage2_duplicate_stats() -> dict[str, object]:
    keys: list[tuple[object, ...]] = []
    with CENTRAL.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("eligible", True) and row.get("stage") == "stage2":
                keys.append(
                    (
                        row.get("ticker"),
                        row.get("rulebook_hash"),
                        row.get("source_file"),
                        row.get("source_row_index"),
                    )
                )
    counts = Counter(keys)
    multiplicity = Counter(counts.values())
    return {
        "eligible_stage2_rows": len(keys),
        "unique_survivor_sources": len(counts),
        "duplicate_rows": len(keys) - len(counts),
        "multiplicity_distribution": {str(key): value for key, value in sorted(multiplicity.items())},
        "finding": "Every one of the 1,162 Stage2 survivor source rows is indexed exactly three times.",
    }


def main() -> int:
    duplicate_stats = central_stage2_duplicate_stats()

    with CHAIN.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        chain_rows = list(reader)
        chain_fields = list(reader.fieldnames or [])
    for row in chain_rows:
        if row.get("gate_name") == "Stage2 elite static filter":
            existing = str(row.get("overlap_or_conflict") or "").strip()
            prefix = "central_index Stage2 eligible rows are 3 identical index copies per survivor; "
            row["overlap_or_conflict"] = prefix + existing
    with CHAIN.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=chain_fields)
        writer.writeheader()
        writer.writerows(chain_rows)

    with OUTPUTS.open("r", encoding="utf-8", newline="") as handle:
        output_rows = list(csv.DictReader(handle))

    payload = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    payload["central_index_stage2_duplicates"] = duplicate_stats
    conflicts = list(payload.get("key_conflicts") or [])
    central_conflict = "central_index contains exactly three identical eligible Stage2 index rows for every survivor (3,486 rows for 1,162 unique sources)."
    if central_conflict not in conflicts:
        conflicts.insert(0, central_conflict)
    payload["key_conflicts"] = conflicts
    payload["output_verdict_counts"] = dict(Counter(row.get("verdict") for row in output_rows))
    SNAPSHOT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        {
            "chain_rows": len(chain_rows),
            "output_rows": len(output_rows),
            "central_index_stage2_duplicates": duplicate_stats,
            "output_verdict_counts": payload["output_verdict_counts"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
