#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from common import OUT, PID, live_hashes, process_snapshot, rel, sha256, validate_csv, write_csv


def main() -> int:
    state = json.loads((OUT / "analysis_state.json").read_text(encoding="utf-8"))
    pre_hashes = dict(state["baseline"]["live_hashes"])
    post_hashes = live_hashes()
    pre_daemon = dict(state["baseline"]["daemon"])
    post_daemon = process_snapshot(PID)

    immutability = []
    for path in sorted(set(pre_hashes) | set(post_hashes)):
        pre = pre_hashes.get(path, "MISSING")
        post = post_hashes.get(path, "MISSING")
        same = pre == post
        immutability.append({
            "path": path,
            "pre_sha256": pre,
            "post_sha256": post,
            "unchanged": str(same).lower(),
            "status": "PASS" if same else "FAIL",
            "notes": "live source/config unchanged",
        })
    daemon_same = pre_daemon.get("running") == "true" and post_daemon.get("running") == "true" and pre_daemon.get("identity") == post_daemon.get("identity")
    immutability.append({
        "path": f"process:live_candidate_slots.py PID {PID}",
        "pre_sha256": pre_daemon.get("identity", ""),
        "post_sha256": post_daemon.get("identity", ""),
        "unchanged": str(daemon_same).lower(),
        "status": "PASS" if daemon_same else "FAIL",
        "notes": "daemon PID/start/cmd unchanged",
    })
    write_csv(OUT / "immutability_check.csv", immutability,
              ["path", "pre_sha256", "post_sha256", "unchanged", "status", "notes"])

    checked = [OUT / "cluster_distribution.csv", OUT / "gap_day_signal_inclusion.csv",
               OUT / "entity_training_sample_definition.csv", OUT / "immutability_check.csv"]
    validation = [validate_csv(path) for path in checked]
    write_csv(OUT / "validation.csv", validation,
              ["path", "row_count", "column_count", "parse_error_count", "status", "error"])
    validation.append(validate_csv(OUT / "validation.csv"))

    live_failures = sum(row["status"] != "PASS" for row in immutability)
    csv_errors = sum(int(row["parse_error_count"]) for row in validation)
    if live_failures:
        raise RuntimeError(f"live immutability failures={live_failures}")
    if csv_errors:
        raise RuntimeError(f"CSV parse errors={csv_errors}")

    targets = sorted(path for path in OUT.iterdir() if path.is_file() and path.name != "manifest.sha256")
    with (OUT / "manifest.sha256").open("w", encoding="utf-8") as fp:
        for path in targets:
            fp.write(f"{sha256(path)}  {path.name}\n")

    result = {
        "csv_parse_errors": csv_errors,
        "live_immutability_failures": live_failures,
        "daemon_pid": PID,
        "daemon_unchanged": daemon_same,
        "manifest_file_count": len(targets),
        "validation": validation,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
