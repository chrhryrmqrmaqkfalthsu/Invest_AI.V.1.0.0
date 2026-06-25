from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.live.manual_buy_intent import (
    atomic_write_json,
    candidate_id_for,
    create_manual_buy_intent,
    load_candidate_state,
    read_json,
)


def write_candidate(path: Path, *, status: str, note: str = "", block_code: str = "", retry_count: int = 0, trade_date: str = "2026-06-25"):
    cid = candidate_id_for(trade_date, "FIX_entity")
    row = {
        "candidate_id": cid,
        "trade_date": trade_date,
        "status": status,
        "ticker": "FIX",
        "entity_id": "FIX_entity",
        "notional": 24_946.53,
        "price": 1_998.01,
        "manual_intent_id": f"manual:{cid}",
        "note": note,
    }
    if block_code:
        row["block_code"] = block_code
    if retry_count:
        row["retry_count"] = retry_count
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "trade_date": trade_date,
            "buy_mode": "semi_auto",
            "updated_at": "2026-06-25T00:00:00+00:00",
            "candidates": {cid: row},
        },
    )
    return cid


def test_limit_notional_blocked_candidate_allows_one_manual_retry(tmp_path):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    cid = write_candidate(candidate_path, status="blocked", block_code="LIMIT_NOTIONAL", note="old block")
    atomic_write_json(
        intent_path,
        {
            "schema_version": 1,
            "trade_date": "2026-06-25",
            "intents": {
                f"manual:{cid}": {
                    "intent_id": f"manual:{cid}",
                    "candidate_id": cid,
                    "trade_date": "2026-06-25",
                    "ticker": "FIX",
                    "status": "blocked",
                    "note": "old block",
                }
            },
        },
    )

    row = create_manual_buy_intent(candidate_id=cid, source="test", candidate_path=candidate_path, intent_path=intent_path)

    assert row["status"] == "pending"
    assert row["intent_id"] == f"manual-retry1:{cid}"
    assert row["retry_count"] == 1
    assert row["retry_code"] == "LIMIT_NOTIONAL"
    state = load_candidate_state(candidate_path)
    candidate = state["candidates"][cid]
    assert candidate["status"] == "manual_requested"
    assert candidate["manual_intent_id"] == f"manual-retry1:{cid}"
    assert candidate["retry_count"] == 1
    intents = read_json(intent_path, {})["intents"]
    assert intents[f"manual:{cid}"]["status"] == "blocked"
    assert intents[f"manual-retry1:{cid}"]["status"] == "pending"


def test_limit_notional_retry_is_rejected_after_one_retry(tmp_path):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    cid = write_candidate(candidate_path, status="blocked", block_code="LIMIT_NOTIONAL", retry_count=1)

    with pytest.raises(ValueError, match="candidate is not pending"):
        create_manual_buy_intent(candidate_id=cid, candidate_path=candidate_path, intent_path=intent_path)


def test_non_limit_blocked_candidate_still_rejected(tmp_path):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    cid = write_candidate(candidate_path, status="blocked", block_code="MARKET_CLOSED", note="장 마감")

    with pytest.raises(ValueError, match="candidate is not pending"):
        create_manual_buy_intent(candidate_id=cid, candidate_path=candidate_path, intent_path=intent_path)


@pytest.mark.parametrize("status", ["manual_executed", "auto_executed", "expired"])
def test_other_terminal_candidates_are_not_retried(tmp_path, status):
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    cid = write_candidate(candidate_path, status=status, block_code="LIMIT_NOTIONAL", note="LIMIT_NOTIONAL")

    with pytest.raises(ValueError, match="candidate is not pending"):
        create_manual_buy_intent(candidate_id=cid, candidate_path=candidate_path, intent_path=intent_path)


def test_legacy_limit_notional_log_evidence_allows_one_retry_without_state_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    candidate_path = tmp_path / "central_buy_candidates.json"
    intent_path = tmp_path / "manual_buy_intent.json"
    cid = write_candidate(candidate_path, status="blocked", note="runner blocked or did not attempt order")
    (logs / "live_semiauto_20260625.log").write_text(
        "FIX BUY 차단: [LIMIT_NOTIONAL] 주문금액 24,963.80 > 한도 24,960.41\n"
        f"[CENTRAL-CONTROL][SEMI-AUTO] manual_timing blocked candidate={cid}\n",
        encoding="utf-8",
    )

    row = create_manual_buy_intent(candidate_id=cid, candidate_path=candidate_path, intent_path=intent_path)

    assert row["intent_id"] == f"manual-retry1:{cid}"
    assert row["retry_code"] == "LIMIT_NOTIONAL"
    assert load_candidate_state(candidate_path)["candidates"][cid]["retry_count"] == 1
