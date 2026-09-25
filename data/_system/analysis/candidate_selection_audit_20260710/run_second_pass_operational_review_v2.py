from __future__ import annotations

import json
import sys
from pathlib import Path

import run_second_pass_operational_review as base


def fixed_graph():
    sys.path.insert(0, str(base.AUDIT.resolve()))
    import run_orphan_file_audit_safe as safe

    audit = safe.audit
    sources = audit.source_files()
    edges, reverse, errors = audit.build_import_graph(sources)
    alive_paths, literals = audit.active_closure(sources, edges)
    alive = {path.relative_to(base.ROOT).as_posix() for path in alive_paths}
    refs = {}
    for target, sources_set in literals.items():
        try:
            target_rel = target.relative_to(base.ROOT).as_posix()
        except Exception:
            continue
        refs[target_rel] = {
            source.relative_to(base.ROOT).as_posix()
            for source in sources_set
            if source in alive_paths
        }
    return alive, refs, len(errors)


def fixed_exp_complete(root: str):
    path = base.ROOT / root
    if not path.is_dir():
        return False, "root missing"
    if (path / "summary.json").exists():
        return True, "summary.json exists"
    if (path / "batch_summary.json").exists():
        return True, "batch_summary.json exists"
    status = path / "run_status.json"
    if status.exists():
        try:
            payload = json.loads(status.read_text(encoding="utf-8"))
            events = payload if isinstance(payload, list) else [payload]
            names = [str(item.get("event", "")).lower() for item in events if isinstance(item, dict)]
            returncodes = [item.get("returncode") for item in events if isinstance(item, dict) and "returncode" in item]
            if {"stage2_done", "stage3_done", "compare_done"}.issubset(names) and all(code == 0 for code in returncodes):
                return True, "run_status stage2/stage3/compare complete"
        except Exception:
            pass
    if any(token in root.lower() for token in base.ONEOFF):
        return True, "one-off experiment name"
    return False, "completion marker absent"


_original_classify = base.classify


def fixed_classify(row, alive, literals, opened, ps_text):
    result = _original_classify(row, alive, literals, opened, ps_text)
    if result is None:
        return None
    name = Path(str(row.path)).name.lower()
    if ".log.zip" in name and result["operational_reachable"] == "X" and result["open_file_handle"] == "X":
        result.update(
            second_pass_type="ROTATED_OR_FINISHED_LOG",
            safety_verdict="DELETE_OK",
            evidence="열린 핸들·활성 참조가 없는 압축 회전 로그",
        )
    return result


base.graph = fixed_graph
base.exp_complete = fixed_exp_complete
base.classify = fixed_classify

if __name__ == "__main__":
    raise SystemExit(base.main())
