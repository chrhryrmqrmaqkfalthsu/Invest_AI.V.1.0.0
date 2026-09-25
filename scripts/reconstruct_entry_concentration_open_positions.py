#!/usr/bin/env python3
"""현재 열린 Elite Shadow 포지션에 진입 몰빵 순위 복구 추정값을 붙인다.

주의:
- 원본 tick 당시 전체 BUY 후보 목록은 과거에 저장하지 않았기 때문에 100% 복구할 수 없다.
- 이 스크립트는 현재 열린 포지션 중 같은 진입 시간대에 속한 포지션끼리만 순위를 재구성한다.
- 결과에는 confidence=partial, rank_scope=current_open_positions_same_time_window를 명시한다.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from engine.live.elite_entry_concentration import score_entry_concentration
from engine.live.elite_shadow_report import build_elite_shadow_report
from engine.live.elite_shadow_trader import _acquire_lock, _release_lock, load_state, save_state, utc_now

STATE_SOURCE = "reconstructed_open_positions_only"
RANK_SCOPE = "current_open_positions_same_time_window"
CONFIDENCE = "partial"
DEFAULT_WINDOW_SEC = 180.0


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def item_dt(item: dict[str, Any]) -> datetime | None:
    dt = item.get("opened_at_dt")
    if isinstance(dt, datetime):
        return dt
    pos = item.get("position") or {}
    return parse_dt(pos.get("opened_at"))


def pos_key(pos: dict[str, Any]) -> str:
    return str(pos.get("candidate_id") or f"{pos.get('stage')}:{pos.get('ticker')}:{pos.get('rulebook_hash_short')}")


def candidate_key(row: dict[str, Any]) -> str:
    return str(row.get("candidate_id") or f"{row.get('stage')}:{row.get('ticker')}:{row.get('rulebook_hash_short')}")


def build_candidate_map() -> dict[str, dict[str, Any]]:
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    out: dict[str, dict[str, Any]] = {}
    for row in report.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        out[candidate_key(row)] = row
        out[f"{row.get('stage')}:{row.get('ticker')}:{row.get('rulebook_hash_short') or ''}"] = row
    return out


def group_positions(items: list[dict[str, Any]], window_sec: float) -> list[list[dict[str, Any]]]:
    sorted_items = sorted(items, key=lambda x: item_dt(x) or datetime.min.replace(tzinfo=timezone.utc))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_start: datetime | None = None
    for item in sorted_items:
        dt = item_dt(item)
        if dt is None:
            if current:
                groups.append(current)
                current = []
                current_start = None
            groups.append([item])
            continue
        if not current or current_start is None:
            current = [item]
            current_start = dt
            continue
        if (dt - current_start).total_seconds() <= window_sec:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
            current_start = dt
    if current:
        groups.append(current)
    return groups


def reconstruct_state(state: dict[str, Any], *, window_sec: float = DEFAULT_WINDOW_SEC, force: bool = False) -> dict[str, Any]:
    candidates = build_candidate_map()
    open_positions = state.get("open_positions") or {}
    if not isinstance(open_positions, dict):
        return {"ok": False, "reason": "open_positions_not_dict"}

    targets: list[dict[str, Any]] = []
    skipped_exact = 0
    skipped_missing_quality = 0
    skipped_missing_candidate = 0
    for key, pos in open_positions.items():
        if not isinstance(pos, dict):
            continue
        existing = pos.get("entry_concentration") or {}
        if existing and not force and existing.get("source") == "entry_tick_snapshot":
            skipped_exact += 1
            continue
        quality = pos.get("entry_quality") or {}
        if not quality:
            skipped_missing_quality += 1
            continue
        ckey = pos_key(pos)
        candidate = candidates.get(ckey) or candidates.get(f"{pos.get('stage')}:{pos.get('ticker')}:{pos.get('rulebook_hash_short') or ''}")
        if not candidate:
            skipped_missing_candidate += 1
            continue
        targets.append({
            "state_key": key,
            "position": pos,
            "candidate": candidate,
            "score": score_entry_concentration(candidate, quality),
            "opened_at_dt": parse_dt(pos.get("opened_at")),
        })

    groups = group_positions(targets, window_sec)
    reconstructed_at = utc_now()
    updated = 0
    group_summaries: list[dict[str, Any]] = []
    for group_idx, group in enumerate(groups, 1):
        ranked = sorted(group, key=lambda row: float((row.get("score") or {}).get("score") or 0.0), reverse=True)
        dts = [item_dt(row) for row in ranked if item_dt(row)]
        group_min = min(dts).isoformat() if dts else None
        group_max = max(dts).isoformat() if dts else None
        group_rows: list[dict[str, Any]] = []
        for rank, row in enumerate(ranked, 1):
            score = dict(row.get("score") or {})
            score.update({
                "source": STATE_SOURCE,
                "rank_scope": RANK_SCOPE,
                "confidence": CONFIDENCE,
                "reconstructed": True,
                "reconstructed_at": reconstructed_at,
                "rank_at_entry": rank,
                "rank_total": len(ranked),
                "ranked_at": reconstructed_at,
                "group_index": group_idx,
                "group_opened_at_min": group_min,
                "group_opened_at_max": group_max,
                "group_window_sec": window_sec,
                "reconstruction_note": "원본 tick 전체 BUY 후보가 아니라 현재 열린 포지션 중 같은 시간대끼리만 복구한 partial rank.",
            })
            pos = row["position"]
            pos["entry_concentration"] = score
            pos["entry_concentration_score"] = score.get("score")
            pos["entry_concentration_action"] = score.get("action")
            pos["entry_concentration_allowed"] = score.get("allowed")
            pos["entry_concentration_rank_at_entry"] = score.get("rank_at_entry")
            pos["entry_concentration_rank_total"] = score.get("rank_total")
            pos["entry_concentration_ranked_at"] = score.get("ranked_at")
            pos["entry_concentration_blocks"] = score.get("blocks")
            pos["entry_concentration_caps"] = score.get("caps")
            pos["entry_concentration_reconstructed"] = True
            pos["entry_concentration_confidence"] = CONFIDENCE
            pos["entry_concentration_rank_scope"] = RANK_SCOPE
            updated += 1
            group_rows.append({
                "rank": rank,
                "ticker": pos.get("ticker"),
                "opened_at": pos.get("opened_at"),
                "score": score.get("score"),
                "action": score.get("action"),
                "blocks": score.get("blocks"),
                "stage": pos.get("stage"),
                "bucket": pos.get("bucket"),
                "q": (score.get("inputs") or {}).get("q"),
                "label": (score.get("inputs") or {}).get("label"),
            })
        group_summaries.append({
            "group_index": group_idx,
            "rank_total": len(ranked),
            "opened_at_min": group_min,
            "opened_at_max": group_max,
            "rows": group_rows,
        })

    state["last_entry_concentration_reconstruction"] = {
        "time": reconstructed_at,
        "source": STATE_SOURCE,
        "rank_scope": RANK_SCOPE,
        "confidence": CONFIDENCE,
        "window_sec": window_sec,
        "updated_open_positions": updated,
        "target_open_positions": len(targets),
        "groups": group_summaries,
        "skipped_exact": skipped_exact,
        "skipped_missing_quality": skipped_missing_quality,
        "skipped_missing_candidate": skipped_missing_candidate,
    }
    return {"ok": True, **state["last_entry_concentration_reconstruction"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="state에 복구 추정값을 저장")
    ap.add_argument("--force", action="store_true", help="기존 reconstructed 값을 다시 계산")
    ap.add_argument("--window-sec", type=float, default=DEFAULT_WINDOW_SEC)
    args = ap.parse_args()

    if args.write and not _acquire_lock():
        print(json.dumps({"ok": False, "reason": "shadow_state_lock_busy"}, ensure_ascii=False, indent=2))
        return 2
    try:
        state = load_state()
        result = reconstruct_state(state, window_sec=args.window_sec, force=args.force)
        if args.write and result.get("ok"):
            save_state(state)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 1
    finally:
        if args.write:
            _release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
