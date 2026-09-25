"""LR-8C-FIX: Top-N 룰북 본문 저장 helper."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from engine.core.metadata import compute_rulebook_hash


def _rank_by_hash(selected_rows: Iterable[Mapping[str, Any]] | None) -> dict[str, int] | None:
    if selected_rows is None:
        return None
    out: dict[str, int] = {}
    for row in selected_rows:
        h = str(row.get("rulebook_hash") or "")
        if not h:
            continue
        try:
            rank = int(row.get("rank_is") or 0)
        except Exception:
            rank = 0
        out[h] = rank
    return out


def collect_rulebook_rows(
    run_key: str,
    ticker: str,
    year: Any,
    candidates: list[Any],
    selected_rows: Iterable[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return Rulebook body rows for the selected Top-N rows only.

    ``candidates`` contains live Rulebook objects. ``selected_rows`` is the
    already-filtered/capped JSON rows written to topn.jsonl. When provided,
    only hashes present in selected_rows are persisted, preserving rank_is.
    """
    selected_rank = _rank_by_hash(selected_rows)
    rows: list[dict[str, Any]] = []
    for fallback_rank, rb in enumerate(candidates, 1):
        rb_hash = compute_rulebook_hash(rb)
        if selected_rank is not None and rb_hash not in selected_rank:
            continue
        rank_is = selected_rank[rb_hash] if selected_rank is not None else fallback_rank
        rows.append(
            {
                "run_key": run_key,
                "ticker": ticker,
                "year": year,
                "rank_is": rank_is,
                "rulebook_hash": rb_hash,
                "rulebook": rb.to_dict(),
            }
        )
    rows.sort(key=lambda row: int(row.get("rank_is") or 0))
    return rows
