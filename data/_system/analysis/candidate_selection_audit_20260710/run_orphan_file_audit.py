from __future__ import annotations

"""Kingmaker unused/orphan inventory (read-only except audit outputs)."""

import ast
import csv
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
CSV_PATH = OUT / "orphan_file_candidates.csv"
LIVE_PATH = OUT / "live_dependency_tree.csv"
SUMMARY_PATH = OUT / "orphan_file_audit_summary.json"
NOW = datetime.now(timezone.utc)

ENTRYPOINTS: dict[str, list[str]] = {
    "stage2_stage3_batch": [
        "scripts/research/run_stage23_batch.py",
        "scripts/research/run_stage2.py",
        "scripts/research/run_stage3_aggressive.py",
    ],
    "live_candidate_generation": [
        "scripts/research/build_stage3_live_pool.py",
        "engine/live/elite_shadow_report.py",
        "data/_system/ops/live_candidate_slots.py",
        "scripts/export_real_dashboard_buy_candidates.py",
    ],
    "live_execution": ["scripts/run_live.py"],
    "dashboard_runtime": [
        "api_server_candidate_only.py",
        "api_server_aftermarket.py",
        "scripts/dashboard_guard.sh",
        "scripts/ensure_caddy_dashboard_route.py",
    ],
    "scheduled_ops": [
        "scripts/live_candidate_slots_guard.sh",
        "scripts/build_sentiment_history.py",
    ],
}

SOURCE_ROOTS = ("engine", "scripts", "config", "docs", "tests", "live")
SOURCE_SUFFIXES = {".py", ".sh", ".html", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
PROTECTED_EXACT = {"data/_system/candidate_denylist.json"}
PROTECTED_PREFIXES = ("data/_system/analysis/", "exp_batch_stage123_2009_20260616_full/")
PROTECTED_NAMES = {"rulebooks_all.jsonl", "survivors.jsonl", "entry_rulebooks.jsonl", "final_rulebooks.jsonl", "exit_trades.jsonl"}
PROTECTED_PATTERNS = ("oos_reproduce_frozen", "frozen_oos")
BACKUP_MARKERS = (".bak", ".bak.", ".bak_", ".bak.before_", ".before_", ".backup")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def run_text(command: list[str]) -> str:
    try:
        return subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30).stdout
    except Exception as exc:
        return f"ERROR:{type(exc).__name__}:{exc}"


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TiB"


def protected(path_text: str) -> tuple[bool, str]:
    p = path_text.strip("/")
    if p in PROTECTED_EXACT:
        return True, "absolute preservation: candidate_denylist"
    if p.startswith("data/_system/analysis/"):
        return True, "absolute preservation: analysis/audit outputs"
    if p.startswith("exp_batch_stage123_2009_20260616_full/"):
        return True, "production Stage2/Stage3 batch and original artifacts"
    if Path(p).name in PROTECTED_NAMES:
        return True, f"absolute preservation: {Path(p).name}"
    if any(token in p for token in PROTECTED_PATTERNS):
        return True, "absolute preservation: frozen OOS/log artifact"
    return False, ""


def source_files() -> list[Path]:
    found: set[Path] = set()
    for p in ROOT.iterdir():
        if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES:
            found.add(p)
    for name in SOURCE_ROOTS:
        base = ROOT / name
        if base.exists():
            found.update(
                p for p in base.rglob("*")
                if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES
                and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
            )
    ops = ROOT / "data/_system/ops"
    if ops.exists():
        found.update(p for p in ops.rglob("*") if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES)
    return sorted(found)


def module_name(path: Path) -> str | None:
    if path.suffix != ".py":
        return None
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def import_names(current: str, node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    mod = node.module or ""
    if node.level:
        package = current.split(".")[:-1]
        up = max(0, node.level - 1)
        if up:
            package = package[:-up] if up <= len(package) else []
        base = ".".join(package + ([mod] if mod else []))
    else:
        base = mod
    names = [base] if base else []
    if base:
        names.extend(f"{base}.{a.name}" for a in node.names if a.name != "*")
    return names


def resolve_module(name: str, modules: dict[str, Path]) -> Path | None:
    cur = name
    while cur:
        if cur in modules:
            return modules[cur]
        cur = cur.rpartition(".")[0]
    return None


def build_import_graph(paths: list[Path]) -> tuple[dict[Path, set[Path]], dict[Path, set[Path]], dict[str, list[str]]]:
    modules = {m: p for p in paths if (m := module_name(p))}
    module_for_path = {p: m for m, p in modules.items()}
    edges: dict[Path, set[Path]] = defaultdict(set)
    reverse: dict[Path, set[Path]] = defaultdict(set)
    errors: dict[str, list[str]] = {}
    for path, current in module_for_path.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
        except Exception as exc:
            errors[rel(path)] = [f"{type(exc).__name__}:{exc}"]
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in import_names(current, node):
                target = resolve_module(name, modules)
                if target and target != path:
                    edges[path].add(target)
                    reverse[target].add(path)
    return edges, reverse, errors


def quoted_strings(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    out: list[str] = []
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
            out.extend(str(n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
        except Exception:
            pass
    out.extend(m.group(2) for m in re.finditer(r"(['\"])(.{1,300}?)\1", text, flags=re.S))
    return out


def literal_targets(value: str, source: Path) -> list[Path]:
    text = value.strip().replace("\\", "/")
    if "://" in text or "\n" in text or len(text) > 300:
        return []
    root_prefix = str(ROOT) + "/"
    text = text.replace("${BASE}/", "").replace("$BASE/", "")
    if text.startswith(root_prefix):
        text = text[len(root_prefix):]
    if text.startswith("~/kingmaker/"):
        text = text[len("~/kingmaker/"):]
    values = [text]
    pattern = r"(?:^|\s)((?:(?:engine|scripts|data|config|live)/|(?:exp_[A-Za-z0-9_.-]+)/)[A-Za-z0-9_./*{}-]+)"
    values.extend(re.findall(pattern, text))
    result: list[Path] = []
    for raw in values:
        raw = raw.strip(" '\"()[],:;")
        if not raw or any(c in raw for c in "{}*"):
            continue
        for probe in (ROOT / raw, source.parent / raw):
            try:
                resolved = probe.resolve()
                resolved.relative_to(ROOT.resolve())
            except Exception:
                continue
            if resolved.exists():
                result.append(resolved)
    return result


def active_closure(paths: list[Path], edges: dict[Path, set[Path]]) -> tuple[set[Path], dict[Path, set[Path]]]:
    roots = {ROOT / p for values in ENTRYPOINTS.values() for p in values if (ROOT / p).exists()}
    alive: set[Path] = set()
    literals: dict[Path, set[Path]] = defaultdict(set)
    queue: deque[Path] = deque(sorted(roots))
    while queue:
        p = queue.popleft()
        if p in alive:
            continue
        alive.add(p)
        queue.extend(t for t in edges.get(p, set()) if t not in alive)
    changed = True
    while changed:
        changed = False
        for source in list(alive):
            if not source.is_file():
                continue
            for value in quoted_strings(source):
                for target in literal_targets(value, source):
                    literals[target].add(source)
                    if target.is_file() and target.suffix == ".py" and target in paths and target not in alive:
                        queue.append(target)
        while queue:
            p = queue.popleft()
            if p in alive:
                continue
            alive.add(p)
            changed = True
            queue.extend(t for t in edges.get(p, set()) if t not in alive)
    return alive, literals


def active_refs(path: Path, alive: set[Path], reverse: dict[Path, set[Path]], literals: dict[Path, set[Path]]) -> tuple[int, str]:
    refs = {p for p in reverse.get(path, set()) if p in alive}
    refs.update(p for p in literals.get(path, set()) if p in alive)
    for target, sources in literals.items():
        try:
            path.relative_to(target)
        except Exception:
            continue
        refs.update(p for p in sources if p in alive)
    names = sorted(rel(p) for p in refs)
    return len(refs), ";".join(names[:8])


def git_metadata() -> tuple[set[str], dict[str, tuple[str, str]]]:
    tracked = set(run_text(["git", "ls-files"]).splitlines())
    latest: dict[str, tuple[str, str]] = {}
    date = commit = ""
    for line in run_text(["git", "log", "--format=@@%aI|%h", "--name-only", "--", "."]).splitlines():
        if line.startswith("@@"):
            date, _, commit = line[2:].partition("|")
        elif line.strip() and line.strip() not in latest:
            latest[line.strip()] = (date, commit)
    return tracked, latest


def digest(path: Path) -> str:
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            return ""
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        return h.hexdigest()
    except Exception:
        return ""


def file_inventory(paths: list[Path]) -> set[Path]:
    found = set(paths)
    found.update(p for p in ROOT.iterdir() if p.is_file() and p.name != ".gitignore")
    system = ROOT / "data/_system"
    if system.exists():
        found.update(p for p in system.iterdir() if p.is_file())
    for name in ("logs", "data/logs"):
        base = ROOT / name
        if base.exists():
            found.update(p for p in base.rglob("*") if p.is_file())
    for exp in ROOT.glob("exp_*"):
        if exp.is_dir() and exp.name != "exp_batch_stage123_2009_20260616_full":
            found.update(p for p in exp.rglob("*") if p.is_file())
    return {
        p for p in found if p.is_file() and ".git" not in p.parts and "venv" not in p.parts
        and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
    }


def aggregate_dirs() -> list[Path]:
    found: set[Path] = set()
    found.update(p for p in ROOT.rglob("__pycache__") if p.is_dir() and ".git" not in p.parts and "venv" not in p.parts)
    if (ROOT / ".pytest_cache").exists():
        found.add(ROOT / ".pytest_cache")
    for name in ("backup", "data/backups", "data/symbols", "exp_batch_stage123_2009_20260616_full"):
        if (ROOT / name).exists():
            found.add(ROOT / name)
    system = ROOT / "data/_system"
    if system.exists():
        found.update(p for p in system.iterdir() if p.is_dir() and p.name != "ops")
    return sorted(found)


def dir_stats(path: Path) -> dict[str, Any]:
    count = size = protected_hits = 0
    latest = 0.0
    examples: list[str] = []
    suffixes: Counter[str] = Counter()
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {".git", "venv"}]
        for name in files:
            p = Path(base) / name
            count += 1
            suffixes[p.suffix.lower() or "[none]"] += 1
            try:
                st = p.stat()
                size += st.st_size
                latest = max(latest, st.st_mtime)
            except Exception:
                pass
            if protected(rel(p))[0]:
                protected_hits += 1
                if len(examples) < 5:
                    examples.append(rel(p))
    dt = datetime.fromtimestamp(latest, timezone.utc) if latest else None
    return {
        "file_count": count,
        "size_bytes": size,
        "mtime": dt.isoformat() if dt else "",
        "days": max(0.0, (NOW - dt).total_seconds() / 86400) if dt else "",
        "protected_hits": protected_hits,
        "protected_examples": examples,
        "suffixes": dict(suffixes.most_common(8)),
    }


def explicit_backup(path_text: str) -> bool:
    name = Path(path_text).name.lower()
    if name.startswith(".env"):
        return False
    return any(mark in name for mark in BACKUP_MARKERS) or name.endswith("~")


def file_row(path: Path, alive: set[Path], reverse: dict[Path, set[Path]], literals: dict[Path, set[Path]], duplicates: list[str], tracked: set[str], git_latest: dict[str, tuple[str, str]], pids: set[int]) -> dict[str, Any]:
    text = rel(path)
    is_protected, protection = protected(text)
    refs, trace = active_refs(path, alive, reverse, literals)
    st = path.stat()
    dt = datetime.fromtimestamp(st.st_mtime, timezone.utc)
    days = max(0.0, (NOW - dt).total_seconds() / 86400)
    risk, kind, reason = "REVIEW", "UNREACHED_FILE", "not reachable from confirmed entrypoints; manual review required"
    roots = {x for values in ENTRYPOINTS.values() for x in values}
    if is_protected:
        risk, kind, reason = "KEEP", "PROTECTED_ARTIFACT", protection
    elif text in roots:
        risk, kind, reason = "KEEP", "PIPELINE_ENTRYPOINT", "confirmed pipeline/runtime/scheduled entrypoint"
    elif path in alive:
        risk, kind, reason = "KEEP", "LIVE_PIPELINE_DEPENDENCY", "reachable through recursive project imports/references"
    elif refs:
        risk, kind, reason = "KEEP", "LIVE_LITERAL_RESOURCE", "referenced by active pipeline code or launch script"
    elif explicit_backup(text):
        risk, kind, reason = "SAFE_TO_DELETE", "EXPLICIT_BACKUP_COPY", "filename explicitly marks superseded backup; canonical source remains"
    elif re.match(r"^\..+\.tmp\.\d+(?:\.|$)", path.name) and days > 1 / 24:
        m = re.search(r"\.tmp\.(\d+)", path.name)
        pid = int(m.group(1)) if m else -1
        if pid not in pids:
            risk, kind, reason = "SAFE_TO_DELETE", "STALE_ATOMIC_TEMP", f"atomic temp from non-running pid={pid}; older than one hour"
    elif path.suffix in {".tmp", ".swp"} and days > 1:
        risk, kind, reason = "SAFE_TO_DELETE", "STALE_TEMP", "temporary/editor file older than one day"
    elif text.startswith("tests/"):
        kind, reason = "UNREACHED_TEST_SUPPORT", "not runtime-reachable, but may protect behavior"
    elif text.startswith("docs/") or path.suffix == ".md":
        kind, reason = "DOCUMENTATION_OR_NOTE", "documentation/history value requires review"
    elif path.suffix == ".py":
        if text.startswith("scripts/research/") or path.name.startswith("_"):
            kind, reason = "ONE_OFF_OR_RESEARCH_SCRIPT", "standalone research/helper script not reached from current entrypoints"
        elif text.startswith("engine/"):
            kind, reason = "UNREACHED_ENGINE_MODULE", "engine module not reached; dynamic/manual use remains possible"
        else:
            kind, reason = "DEAD_OR_MANUAL_SCRIPT", "no active import/reference reachability; manual invocation remains possible"
    elif path.suffix in {".log", ".out"}:
        kind, reason = "LOG_ARTIFACT", "generated log not referenced by active path; retention policy required"
    elif path.suffix in {".lock", ".pid"}:
        kind, reason = "LOCK_OR_PID_ARTIFACT", "may be stale, but deletion can affect process coordination"
    elif text.startswith("exp_"):
        kind, reason = "ONE_OFF_EXPERIMENT_ARTIFACT", "outside confirmed current batch; research evidence may matter"
    if len(duplicates) > 1 and risk == "REVIEW":
        kind = "EXACT_DUPLICATE_REVIEW"
        reason += f"; exact-content duplicates={duplicates[:6]}"
    git_date, git_hash = git_latest.get(text, ("", ""))
    return {
        "path": text, "path_kind": "file", "type": kind, "risk": risk,
        "size_bytes": st.st_size, "size_human": human_size(st.st_size), "file_count": 1,
        "recent_modified_utc": dt.isoformat(), "days_since_modified": round(days, 3),
        "reachable": path in alive, "reachable_from": trace, "reference_count": refs,
        "last_reference_trace": trace or "none in active import/literal graph",
        "git_tracked": text in tracked, "git_last_commit_date": git_date, "git_last_commit": git_hash,
        "protected_reason": protection, "decision_reason": reason,
    }


def directory_row(path: Path, stats: dict[str, Any], alive: set[Path], reverse: dict[Path, set[Path]], literals: dict[Path, set[Path]]) -> dict[str, Any]:
    text = rel(path)
    is_protected, protection = protected(text + "/")
    refs, trace = active_refs(path, alive, reverse, literals)
    risk, kind, reason = "REVIEW", "AGGREGATED_DIRECTORY", "large tree represented as one row; recursive deletion not authorized"
    if is_protected:
        risk, kind, reason = "KEEP", "PROTECTED_DIRECTORY", protection
    elif stats["protected_hits"]:
        risk, kind = "KEEP", "MIXED_DIRECTORY_WITH_PROTECTED_ARTIFACTS"
        reason = f"contains {stats['protected_hits']} protected artifacts; directory deletion prohibited"
        protection = ";".join(stats["protected_examples"])
    elif refs:
        risk, kind, reason = "KEEP", "LIVE_REFERENCED_DIRECTORY", "active code references this directory or an ancestor"
    elif path.name == "__pycache__" or ".pytest_cache" in path.parts:
        risk, kind, reason = "SAFE_TO_DELETE", "REGENERABLE_CACHE_DIRECTORY", "interpreter/test cache only"
    elif text in {"backup", "data/backups"}:
        kind, reason = "BACKUP_ARCHIVE_DIRECTORY", "recovery value cannot be determined automatically"
    elif text in {"logs", "data/logs"}:
        kind, reason = "LOG_DIRECTORY", "contains current/historical logs; retention policy required"
    elif text.startswith("data/_system/research"):
        kind, reason = "RESEARCH_CACHE_AND_ARTIFACT_DIRECTORY", "large research tree; audits may reuse inputs"
    elif text.startswith("data/_system/"):
        kind, reason = "SYSTEM_DATA_DIRECTORY", "dynamic filename access may exist; manual review required"
    return {
        "path": text, "path_kind": "directory", "type": kind, "risk": risk,
        "size_bytes": stats["size_bytes"], "size_human": human_size(stats["size_bytes"]), "file_count": stats["file_count"],
        "recent_modified_utc": stats["mtime"], "days_since_modified": round(stats["days"], 3) if stats["days"] != "" else "",
        "reachable": bool(refs), "reachable_from": trace, "reference_count": refs,
        "last_reference_trace": trace or "none in active import/literal graph",
        "git_tracked": False, "git_last_commit_date": "", "git_last_commit": "",
        "protected_reason": protection, "decision_reason": reason + f"; top_suffixes={stats['suffixes']}",
    }


def main() -> int:
    sources = source_files()
    edges, reverse, parse_errors = build_import_graph(sources)
    alive, literals = active_closure(sources, edges)
    tracked, git_latest = git_metadata()
    pids = {int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()}
    files = file_inventory(sources)

    hash_groups: dict[str, list[str]] = defaultdict(list)
    hash_by_file: dict[Path, str] = {}
    for path in sorted(files):
        if path.suffix.lower() in SOURCE_SUFFIXES and not protected(rel(path))[0]:
            value = digest(path)
            if value:
                hash_groups[value].append(rel(path))
                hash_by_file[path] = value

    rows = [
        file_row(path, alive, reverse, literals, hash_groups.get(hash_by_file.get(path, ""), []), tracked, git_latest, pids)
        for path in sorted(files)
    ]
    rows.extend(directory_row(path, dir_stats(path), alive, reverse, literals) for path in aggregate_dirs())
    order = {"SAFE_TO_DELETE": 0, "REVIEW": 1, "KEEP": 2}
    rows.sort(key=lambda r: (order.get(r["risk"], 9), r["path"]))

    fields = [
        "path", "path_kind", "type", "risk", "size_bytes", "size_human", "file_count",
        "recent_modified_utc", "days_since_modified", "reachable", "reachable_from", "reference_count",
        "last_reference_trace", "git_tracked", "git_last_commit_date", "git_last_commit",
        "protected_reason", "decision_reason",
    ]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    categories = {ROOT / p: category for category, values in ENTRYPOINTS.items() for p in values if (ROOT / p).exists()}
    live_rows = []
    for path in sorted(alive):
        importers = sorted(rel(p) for p in reverse.get(path, set()) if p in alive)
        resources = sorted(rel(target) for target, refs in literals.items() if path in refs)
        live_rows.append({
            "path": rel(path), "entrypoint_category": categories.get(path, ""),
            "direct_active_importer_count": len(importers), "direct_active_importers": "|".join(importers[:20]),
            "literal_resources_referenced": "|".join(resources[:30]),
        })
    with LIVE_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(live_rows[0]) if live_rows else ["path"])
        writer.writeheader()
        writer.writerows(live_rows)

    row_counts = Counter(r["risk"] for r in rows)
    represented_files = Counter()
    represented_sizes = Counter()
    type_counts = Counter(r["type"] for r in rows)
    for row in rows:
        represented_files[row["risk"]] += int(row["file_count"])
        represented_sizes[row["risk"]] += int(row["size_bytes"])
    summary = {
        "created_at": NOW.isoformat(),
        "mode": "read-only identification; no deletion/move/source modification",
        "entrypoints": ENTRYPOINTS,
        "source_files_scanned": len(sources),
        "active_dependency_files": len(alive),
        "python_import_edges": sum(len(v) for v in edges.values()),
        "literal_reference_targets": len(literals),
        "parse_error_count": len(parse_errors),
        "parse_errors": parse_errors,
        "inventory_rows": len(rows),
        "risk_row_counts": dict(sorted(row_counts.items())),
        "risk_represented_file_counts": dict(sorted(represented_files.items())),
        "risk_represented_size_bytes": dict(sorted(represented_sizes.items())),
        "risk_represented_size_human": {k: human_size(v) for k, v in sorted(represented_sizes.items())},
        "top_type_counts": dict(type_counts.most_common(30)),
        "safe_rows": [
            {"path": r["path"], "path_kind": r["path_kind"], "type": r["type"], "size_bytes": r["size_bytes"], "file_count": r["file_count"], "decision_reason": r["decision_reason"]}
            for r in rows if r["risk"] == "SAFE_TO_DELETE"
        ],
        "largest_review_rows": [
            {"path": r["path"], "path_kind": r["path_kind"], "type": r["type"], "size_bytes": r["size_bytes"], "file_count": r["file_count"], "decision_reason": r["decision_reason"]}
            for r in sorted((x for x in rows if x["risk"] == "REVIEW"), key=lambda x: int(x["size_bytes"]), reverse=True)[:50]
        ],
        "launch_evidence": {
            "crontab": run_text(["crontab", "-l"]),
            "kingmaker_service": run_text(["systemctl", "cat", "kingmaker.service"]),
            "running_project_processes": run_text(["bash", "-lc", "ps -eo pid,ppid,lstart,cmd | grep -E '/home/.*/kingmaker|kingmaker/' | grep -v grep"]),
        },
        "limitations": [
            "Static reachability cannot exclude reflection, plugins, manual invocation, or external automation.",
            "Large data/research/cache trees are represented as directory rows; REVIEW never authorizes recursive deletion.",
            "A directory containing protected rule pools, exit_trades, frozen/audit artifacts is KEEP.",
            "Unreachable tests/docs/research modules remain REVIEW unless explicitly backup/cache/temp.",
        ],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "source_files_scanned": len(sources), "active_dependency_files": len(alive),
        "inventory_rows": len(rows), "risk_row_counts": dict(row_counts),
        "risk_represented_file_counts": dict(represented_files),
        "risk_represented_size_human": {k: human_size(v) for k, v in represented_sizes.items()},
        "parse_error_count": len(parse_errors),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
