from __future__ import annotations

"""Safety wrapper for conservative reachability and preservation overrides."""

from collections import deque
from pathlib import Path

import run_orphan_file_audit as audit

_DYNAMIC_STAGE3_BODY = (
    audit.ROOT
    / "scripts/research/run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001"
)

_original_source_files = audit.source_files
_original_module_name = audit.module_name
_original_literal_targets = audit.literal_targets
_original_active_closure = audit.active_closure
_original_file_inventory = audit.file_inventory
_original_file_row = audit.file_row
_observed_literal_files: set[Path] = set()


def _source_files_with_dynamic_bodies():
    paths = set(_original_source_files())
    if _DYNAMIC_STAGE3_BODY.exists():
        paths.add(_DYNAMIC_STAGE3_BODY)
    return sorted(paths)


def _module_name_with_dynamic_bodies(path):
    if path == _DYNAMIC_STAGE3_BODY:
        return "scripts.research._stage3_aggressive_original_dynamic"
    return _original_module_name(path)


def _safe_literal_targets(value, source):
    try:
        targets = _original_literal_targets(value, source)
    except (OSError, ValueError):
        return []
    # Generic project roots are environment/context declarations, not evidence
    # that every descendant is live.
    broad = {
        audit.ROOT,
        audit.ROOT / "data",
        audit.ROOT / "data/_system",
        audit.ROOT / "engine",
        audit.ROOT / "scripts",
        audit.ROOT / "config",
        audit.ROOT / "live",
        audit.ROOT / "logs",
    }
    filtered = [target for target in targets if target not in broad]
    _observed_literal_files.update(target for target in filtered if target.is_file())
    return filtered


def _active_closure_with_dynamic_and_packages(paths, edges):
    alive, literals = _original_active_closure(paths, edges)
    path_set = set(paths)
    scanned_literals: set[Path] = set()

    def enqueue_package_inits(path: Path, queue: deque[Path]) -> None:
        parent = path.parent
        while parent != audit.ROOT and audit.ROOT in parent.parents:
            init_path = parent / "__init__.py"
            if init_path in path_set and init_path not in alive:
                queue.append(init_path)
            parent = parent.parent

    changed = True
    while changed:
        changed = False
        queue: deque[Path] = deque()

        # Python automatically executes parent package __init__.py files.
        for path in list(alive):
            enqueue_package_inits(path, queue)

        # Dynamic source bodies and other concrete literal file references are
        # executable dependencies when referenced by an already-live source.
        for target, sources in list(literals.items()):
            if target.is_file() and target in path_set and target not in alive:
                if any(source in alive for source in sources):
                    queue.append(target)

        # Newly admitted dynamic bodies may themselves contain literal paths.
        for source in list(alive):
            if source in scanned_literals or not source.is_file():
                continue
            scanned_literals.add(source)
            for value in audit.quoted_strings(source):
                for target in audit.literal_targets(value, source):
                    literals[target].add(source)
                    if target.is_file() and target in path_set and target not in alive:
                        queue.append(target)

        while queue:
            path = queue.popleft()
            if path in alive:
                continue
            alive.add(path)
            changed = True
            enqueue_package_inits(path, queue)
            for target in edges.get(path, set()):
                if target not in alive:
                    queue.append(target)

    return alive, literals


def _inventory_with_literal_files(paths):
    return _original_file_inventory(paths) | set(_observed_literal_files)


def _conservative_active_refs(path, alive, reverse, literals):
    refs = {source for source in reverse.get(path, set()) if source in alive}
    refs.update(source for source in literals.get(path, set()) if source in alive)
    # Propagate a directory reference only for concrete data/artifact roots.
    # A source directory literal must not make every module below it live.
    for target, sources in literals.items():
        if not target.is_dir():
            continue
        try:
            target_rel = audit.rel(target)
        except Exception:
            continue
        propagate = (
            target_rel.startswith("data/")
            and target_rel not in {"data", "data/_system"}
        ) or target_rel.startswith("exp_batch_stage123_2009_20260616_full")
        if not propagate:
            continue
        try:
            path.relative_to(target)
        except Exception:
            continue
        refs.update(source for source in sources if source in alive)
    names = sorted(audit.rel(source) for source in refs)
    return len(refs), ";".join(names[:8])


def _final_file_row(path, alive, reverse, literals, duplicates, tracked, git_latest, pids):
    row = _original_file_row(path, alive, reverse, literals, duplicates, tracked, git_latest, pids)
    path_text = str(row["path"])
    name = Path(path_text).name.lower()

    # Sensitive configuration is never a deletion candidate, including backups.
    if name.startswith(".env"):
        row.update(
            risk="KEEP",
            type="SENSITIVE_CONFIGURATION",
            protected_reason="sensitive configuration; excluded from deletion candidates",
            decision_reason="sensitive configuration or credential backup; manual handling only",
        )
        return row

    # Preserve original/derived rule-pool objects across all experiment trees.
    rule_pool_tokens = ("rulebook", "survivor", "profile_catalog")
    rule_pool_exact = {
        "validation_results.jsonl",
        "candidate_universe.json",
        "central_index.jsonl",
    }
    if any(token in name for token in rule_pool_tokens) or name in rule_pool_exact:
        row.update(
            risk="KEEP",
            type="PROTECTED_RULE_POOL_ARTIFACT",
            protected_reason="absolute preservation: original/derived rule-pool artifact",
            decision_reason="rulebook/survivor/catalog/index artifact; deletion candidate exclusion",
        )
        return row

    # Runtime-state backups are not automatically safe: they may be required for
    # incident recovery even when a canonical current file exists.
    if (
        row["risk"] == "SAFE_TO_DELETE"
        and row["type"] == "EXPLICIT_BACKUP_COPY"
        and path_text.startswith("data/_system/")
    ):
        row.update(
            risk="REVIEW",
            type="OPERATIONAL_STATE_BACKUP",
            protected_reason="",
            decision_reason="superseded runtime-state backup, but recovery value requires human review",
        )
    return row


audit.source_files = _source_files_with_dynamic_bodies
audit.module_name = _module_name_with_dynamic_bodies
audit.literal_targets = _safe_literal_targets
audit.active_closure = _active_closure_with_dynamic_and_packages
audit.file_inventory = _inventory_with_literal_files
audit.active_refs = _conservative_active_refs
audit.file_row = _final_file_row

if __name__ == "__main__":
    raise SystemExit(audit.main())
