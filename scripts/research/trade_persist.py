"""LR-8C-FIX: Top-N 후보별 OOS 거래 로그 저장 helper."""
from __future__ import annotations

from typing import Any, Mapping


def collect_trade_rows(
    candidate_trades_by_hash: Mapping[str, dict[str, Any]],
    selected_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return trade rows only for candidates written to topn.jsonl."""
    rows: list[dict[str, Any]] = []
    for selected in selected_rows:
        h = str(selected.get("rulebook_hash") or "")
        if not h or h not in candidate_trades_by_hash:
            continue
        row = dict(candidate_trades_by_hash[h])
        try:
            row["rank_is"] = int(selected.get("rank_is") or row.get("rank_is") or 0)
        except Exception:
            row["rank_is"] = row.get("rank_is") or 0
        rows.append(row)
    rows.sort(key=lambda row: int(row.get("rank_is") or 0))
    return rows
