from __future__ import annotations

import csv
import json
from pathlib import Path

from engine.live import candidate_gate


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _checker(tmp_path: Path) -> candidate_gate.CandidateGateChecker:
    v3 = tmp_path / "v3.csv"
    boil = tmp_path / "boil.csv"
    decision = tmp_path / "decision.json"
    _write_csv(
        v3,
        [
            "candidate_id",
            "final_p99_weightless_block_status",
            "p99_weightless_reason_codes",
            "p99_weightless_fail_components",
            "volume_reachability_label",
            "volume_weight",
            "policy_version",
        ],
        [
            {
                "candidate_id": "stage3:PASS:abc",
                "final_p99_weightless_block_status": "PASS",
                "volume_reachability_label": "REACHABLE",
                "volume_weight": "0.0",
                "policy_version": candidate_gate.V3_POLICY_VERSION,
            },
            {
                "candidate_id": "stage3:V3FAIL:def",
                "final_p99_weightless_block_status": "FAIL",
                "p99_weightless_reason_codes": "ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_MAX",
                "p99_weightless_fail_components": "volume",
                "volume_reachability_label": "UNREACHABLE",
                "volume_weight": "1.0",
                "policy_version": candidate_gate.V3_POLICY_VERSION,
            },
        ],
    )
    _write_csv(
        boil,
        [
            "candidate_id",
            "ticker",
            "vol_group_final",
            "weight_volume_surge",
            "near_zero",
            "nonvolume_entry_possible_market_cap",
            "block_reason",
            "v3_overlap_excluded",
        ],
        [
            {
                "candidate_id": "stage3:PASS:abc",
                "ticker": "PASS",
                "vol_group_final": "HIGH_VOL",
                "weight_volume_surge": "0.0",
                "near_zero": "True",
                "nonvolume_entry_possible_market_cap": "True",
                "block_reason": "BOIL_FINAL",
                "v3_overlap_excluded": "False",
            }
        ],
    )
    decision.write_text(
        json.dumps({"decision": "BLOCK_JUSTIFIED", "definition": {"v3_reachability_status": "PASS"}}),
        encoding="utf-8",
    )
    return candidate_gate.CandidateGateChecker(v3, boil, decision)


def test_shadow_never_blocks_and_block_mode_blocks_fail(tmp_path: Path) -> None:
    checker = _checker(tmp_path)
    shadow = checker.evaluate(
        {"candidate_id": "stage3:PASS:abc", "ticker": "PASS"}, enforcement="SHADOW"
    )
    assert shadow.aggregate_status == "FAIL"
    assert shadow.should_block is False
    assert [row.status for row in shadow.checks] == ["PASS", "FAIL"]

    block = checker.evaluate(
        {"candidate_id": "stage3:PASS:abc", "ticker": "PASS"}, enforcement="BLOCK"
    )
    assert block.aggregate_status == "FAIL"
    assert block.should_block is True

    v3_fail = checker.evaluate(
        {"candidate_id": "stage3:V3FAIL:def", "ticker": "V3FAIL"}, enforcement="SHADOW"
    )
    assert [row.status for row in v3_fail.checks] == ["FAIL", "PASS"]
    assert v3_fail.checks[1].reasons == ("NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS",)
    assert v3_fail.should_block is False


def test_missing_v3_catalog_row_is_hold_but_nonmember_boil_is_pass(tmp_path: Path) -> None:
    v3 = tmp_path / "v3.csv"
    boil = tmp_path / "boil.csv"
    decision = tmp_path / "decision.json"
    _write_csv(v3, ["candidate_id", "final_p99_weightless_block_status"], [])
    _write_csv(boil, ["candidate_id", "block_reason"], [])
    decision.write_text(json.dumps({"decision": "BLOCK_JUSTIFIED"}), encoding="utf-8")
    checker = candidate_gate.CandidateGateChecker(v3, boil, decision)
    result = checker.evaluate(
        {"candidate_id": "stage3:MISSING:x", "ticker": "MISSING"}, enforcement="SHADOW"
    )
    assert result.aggregate_status == "HOLD"
    assert result.should_block is False
    assert [row.status for row in result.checks] == ["HOLD", "PASS"]


def test_current_candidate_pool_uses_final_boil_exclusive_catalog() -> None:
    state = json.loads(Path("data/_system/live_slots_state.json").read_text(encoding="utf-8"))
    candidates = state.get("candidate_pool") or []
    assert len(candidates) == 18
    checker = candidate_gate.CandidateGateChecker()
    decisions = checker.evaluate_many(candidates, enforcement="SHADOW")
    assert len(decisions) == 18
    assert all(decision.should_block is False for decision in decisions)

    boil_fail = []
    v3_fail = []
    for decision in decisions:
        v3_result, boil_result = decision.checks
        if v3_result.status == "FAIL":
            v3_fail.append(decision.ticker)
        if boil_result.status == "FAIL":
            boil_fail.append(decision.ticker)
    assert v3_fail == ["BTBT", "BMI", "BCS", "BNTX", "CRK"]
    assert boil_fail == []


def test_policy_defaults_are_shadow() -> None:
    assert candidate_gate.integrated_gate_enforcement() == "SHADOW"
    assert candidate_gate.upstream_gate_enforcement() == "SHADOW"
