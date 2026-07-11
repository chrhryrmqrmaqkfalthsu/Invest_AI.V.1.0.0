"""Upstream v3/BOIL gate simulation for elite report candidate construction.

SHADOW mode returns the original sorted/deduplicated candidates unchanged and
logs what BLOCK would have removed or replaced before ticker deduplication.
"""
from __future__ import annotations

import fcntl
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from engine.live.candidate_gate import (
    CandidateGateChecker,
    CandidateGateDecision,
    upstream_gate_enforcement,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_LOG_DIR = PROJECT_ROOT / "data/_system/analysis/upstream_gate_shadow"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_id(row: dict[str, Any]) -> str:
    return str(
        row.get("candidate_id")
        or f"{row.get('stage')}:{row.get('ticker')}:{row.get('rulebook_hash_short')}"
    )


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or "").upper().strip()


def _decision_reason(decision: CandidateGateDecision) -> str:
    failures = []
    holds = []
    for check in decision.checks:
        if check.status == "FAIL":
            failures.append(f"{check.checker}:{'|'.join(check.reasons)}")
        elif check.status == "HOLD":
            holds.append(f"{check.checker}:{'|'.join(check.reasons)}")
    return ";".join(failures or holds) or "PASS"


def _dedup_sorted(
    rows: list[dict[str, Any]],
    *,
    max_unique: int,
    sort_key: Callable[[dict[str, Any]], Any],
) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=sort_key, reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in ordered:
        ticker = _ticker(row)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(row)
        if len(out) >= max_unique:
            break
    return out


def _append_log(payload: dict[str, Any]) -> Path | None:
    try:
        UPSTREAM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = UPSTREAM_LOG_DIR / f"upstream_gate_{now:%Y%m%d}.jsonl"
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return path
    except Exception:
        return None


def apply_upstream_gate_shadow(
    rows: list[dict[str, Any]],
    *,
    stage: str,
    max_unique: int,
    sort_key: Callable[[dict[str, Any]], Any],
    checker: CandidateGateChecker | None = None,
    enforcement: str | None = None,
    log_result: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Evaluate before ticker dedup; SHADOW returns baseline candidates unchanged."""
    checker = checker or CandidateGateChecker()
    mode = str(enforcement or upstream_gate_enforcement()).upper()
    if mode not in {"SHADOW", "BLOCK"}:
        mode = "SHADOW"

    decisions: dict[str, CandidateGateDecision] = {}
    for row in rows:
        cid = _candidate_id(row)
        decisions[cid] = checker.evaluate(row, enforcement=mode)

    baseline = _dedup_sorted(rows, max_unique=max_unique, sort_key=sort_key)
    eligible_rows = [
        row
        for row in rows
        if decisions[_candidate_id(row)].aggregate_status == "PASS"
    ]
    simulated = _dedup_sorted(eligible_rows, max_unique=max_unique, sort_key=sort_key)

    baseline_by_ticker = {_ticker(row): row for row in baseline}
    simulated_by_ticker = {_ticker(row): row for row in simulated}
    removed = []
    replacements = []
    for ticker, original in baseline_by_ticker.items():
        original_decision = decisions[_candidate_id(original)]
        replacement = simulated_by_ticker.get(ticker)
        if original_decision.aggregate_status == "PASS":
            continue
        removed_row = {
            "ticker": ticker,
            "candidate_id": _candidate_id(original),
            "stage": original.get("stage"),
            "elite_score": original.get("elite_score"),
            "aggregate_status": original_decision.aggregate_status,
            "reason": _decision_reason(original_decision),
            "replacement_candidate_id": _candidate_id(replacement) if replacement else None,
            "replacement_elite_score": replacement.get("elite_score") if replacement else None,
        }
        removed.append(removed_row)
        if replacement is not None:
            replacements.append(removed_row)

    decision_counts = Counter(d.aggregate_status for d in decisions.values())
    baseline_ids = [_candidate_id(row) for row in baseline]
    simulated_ids = [_candidate_id(row) for row in simulated]
    payload = {
        "timestamp": _utc_now(),
        "path": "engine.live.elite_shadow_report.pre_ticker_dedup",
        "stage": stage,
        "enforcement": mode,
        "raw_candidate_count": len(rows),
        "decision_counts": dict(decision_counts),
        "baseline_selected_count": len(baseline),
        "simulated_selected_count": len(simulated),
        "baseline_candidate_ids": baseline_ids,
        "simulated_candidate_ids": simulated_ids,
        "removed_selected": removed,
        "replacement_count": len(replacements),
        "replacements": replacements,
        "actual_output_changed": mode == "BLOCK",
        "decisions": [decisions[_candidate_id(row)].to_dict() for row in rows],
    }
    if log_result:
        log_path = _append_log(payload)
        payload["log_path"] = str(log_path) if log_path else None

    return (simulated if mode == "BLOCK" else baseline), payload
