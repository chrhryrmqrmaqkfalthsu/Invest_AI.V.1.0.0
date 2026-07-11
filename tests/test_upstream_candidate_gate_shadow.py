from __future__ import annotations

import csv
import json
from pathlib import Path

from engine.live.candidate_gate import CandidateGateChecker, CandidateGateDecision, GateCheckResult
from engine.live.upstream_candidate_gate import apply_upstream_gate_shadow


def _decision(cid: str, ticker: str, status: str, enforcement: str = "SHADOW") -> CandidateGateDecision:
    check = GateCheckResult(
        checker="v3_one_sided_threshold_reachability",
        status=status,
        reasons=(("TEST_FAIL",) if status == "FAIL" else ("TEST_PASS",)),
        evidence={"candidate_id": cid},
        policy_version="test-v3",
        source="test",
    )
    boil = GateCheckResult(
        checker="boil_high_vol_volume_blind",
        status="PASS",
        reasons=("NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS",),
        evidence={"candidate_id": cid},
        policy_version="test-boil",
        source="test",
    )
    return CandidateGateDecision(
        candidate_id=cid,
        ticker=ticker,
        enforcement=enforcement,
        aggregate_status=status,
        should_block=enforcement == "BLOCK" and status in {"FAIL", "HOLD"},
        policy_version="test",
        checked_at="2026-07-11T00:00:00+00:00",
        checks=(check, boil),
    )


class FakeChecker:
    def __init__(self, statuses: dict[str, str]):
        self.statuses = statuses

    def evaluate(self, candidate, enforcement=None):
        cid = candidate["candidate_id"]
        return _decision(cid, candidate["ticker"], self.statuses[cid], enforcement or "SHADOW")


def _key(row):
    return (row["elite_score"], row["metrics"]["oos_fitness"], row["metrics"]["oos_expectancy_pct"])


def test_shadow_returns_original_dedup_and_simulates_replacement() -> None:
    rows = [
        {"candidate_id": "stage3:AAA:top", "ticker": "AAA", "stage": "stage3", "elite_score": 10, "metrics": {"oos_fitness": 10, "oos_expectancy_pct": 10}},
        {"candidate_id": "stage3:AAA:next", "ticker": "AAA", "stage": "stage3", "elite_score": 9, "metrics": {"oos_fitness": 9, "oos_expectancy_pct": 9}},
        {"candidate_id": "stage3:BBB:only", "ticker": "BBB", "stage": "stage3", "elite_score": 8, "metrics": {"oos_fitness": 8, "oos_expectancy_pct": 8}},
    ]
    checker = FakeChecker({"stage3:AAA:top": "FAIL", "stage3:AAA:next": "PASS", "stage3:BBB:only": "PASS"})
    actual, summary = apply_upstream_gate_shadow(
        rows, stage="stage3", max_unique=10, sort_key=_key, checker=checker, enforcement="SHADOW", log_result=False
    )
    assert [r["candidate_id"] for r in actual] == ["stage3:AAA:top", "stage3:BBB:only"]
    assert summary["simulated_candidate_ids"] == ["stage3:AAA:next", "stage3:BBB:only"]
    assert summary["replacement_count"] == 1
    assert summary["replacements"][0]["replacement_candidate_id"] == "stage3:AAA:next"
    assert summary["actual_output_changed"] is False


def test_block_returns_simulated_filtered_dedup() -> None:
    rows = [
        {"candidate_id": "stage2:AAA:top", "ticker": "AAA", "stage": "stage2", "elite_score": 10, "metrics": {"oos_fitness": 10, "oos_expectancy_pct": 10}},
        {"candidate_id": "stage2:AAA:next", "ticker": "AAA", "stage": "stage2", "elite_score": 9, "metrics": {"oos_fitness": 9, "oos_expectancy_pct": 9}},
    ]
    checker = FakeChecker({"stage2:AAA:top": "FAIL", "stage2:AAA:next": "PASS"})
    actual, summary = apply_upstream_gate_shadow(
        rows, stage="stage2", max_unique=10, sort_key=_key, checker=checker, enforcement="BLOCK", log_result=False
    )
    assert [r["candidate_id"] for r in actual] == ["stage2:AAA:next"]
    assert summary["actual_output_changed"] is True


def test_final_boil_exclusive_catalog_is_authoritative(tmp_path: Path) -> None:
    v3 = tmp_path / "v3.csv"
    boil = tmp_path / "boil.csv"
    decision = tmp_path / "decision.json"
    with v3.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "final_p99_weightless_block_status", "p99_weightless_reason_codes"])
        writer.writeheader()
        writer.writerow({"candidate_id": "stage3:BNTX:x", "final_p99_weightless_block_status": "FAIL", "p99_weightless_reason_codes": "INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99"})
        writer.writerow({"candidate_id": "stage3:BOIL:y", "final_p99_weightless_block_status": "PASS", "p99_weightless_reason_codes": ""})
    with boil.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_id", "block_reason", "v3_overlap_excluded"])
        writer.writeheader()
        writer.writerow({"candidate_id": "stage3:BOIL:y", "block_reason": "BOIL_FINAL", "v3_overlap_excluded": "False"})
    decision.write_text(json.dumps({"decision": "BLOCK_JUSTIFIED", "definition": {"v3_reachability_status": "PASS"}}), encoding="utf-8")
    checker = CandidateGateChecker(v3, boil, decision)

    bntx = checker.evaluate({"candidate_id": "stage3:BNTX:x", "ticker": "BNTX"}, enforcement="SHADOW")
    assert [c.status for c in bntx.checks] == ["FAIL", "PASS"]
    assert bntx.aggregate_status == "FAIL"
    assert bntx.checks[1].reasons == ("NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS",)

    boil_row = checker.evaluate({"candidate_id": "stage3:BOIL:y", "ticker": "BOIL"}, enforcement="SHADOW")
    assert [c.status for c in boil_row.checks] == ["PASS", "FAIL"]


def test_current_bntx_reason_is_v3_not_boil() -> None:
    checker = CandidateGateChecker()
    decision = checker.evaluate({"candidate_id": "stage3:BNTX:d667608bc166", "ticker": "BNTX"}, enforcement="SHADOW")
    v3, boil = decision.checks
    assert v3.status == "FAIL"
    assert "INACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99" in v3.reasons
    assert boil.status == "PASS"
    assert boil.reasons == ("NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS",)
