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
    risk = tmp_path / "risk.csv"
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
        ["candidate_id", "ticker", "vol_group", "weight_volume_surge", "check_boil"],
        [
            {
                "candidate_id": "stage3:PASS:abc",
                "ticker": "PASS",
                "vol_group": "HIGH_VOL",
                "weight_volume_surge": "0.0",
                "check_boil": "FAIL",
            },
            {
                "candidate_id": "stage3:V3FAIL:def",
                "ticker": "V3FAIL",
                "vol_group": "HIGH_VOL",
                "weight_volume_surge": "0.0",
                "check_boil": "PASS",
            },
        ],
    )
    _write_csv(
        risk,
        [
            "candidate_id",
            "vol_group_final",
            "nonvolume_entry_possible_market_cap",
            "weight_volume_surge",
            "risk_high_vol_volume_blind",
            "legacy_boil_check",
        ],
        [
            {
                "candidate_id": "stage3:PASS:abc",
                "vol_group_final": "HIGH_VOL",
                "nonvolume_entry_possible_market_cap": "True",
                "weight_volume_surge": "0.0",
                "risk_high_vol_volume_blind": "True",
                "legacy_boil_check": "FAIL",
            }
        ],
    )
    return candidate_gate.CandidateGateChecker(v3, boil, risk)


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
    assert v3_fail.should_block is False


def test_missing_catalog_row_is_hold_and_shadow_safe(tmp_path: Path) -> None:
    v3 = tmp_path / "v3.csv"
    boil = tmp_path / "boil.csv"
    risk = tmp_path / "risk.csv"
    _write_csv(v3, ["candidate_id", "final_p99_weightless_block_status"], [])
    _write_csv(boil, ["candidate_id", "check_boil"], [])
    _write_csv(risk, ["candidate_id"], [])
    checker = candidate_gate.CandidateGateChecker(v3, boil, risk)
    decision = checker.evaluate(
        {"candidate_id": "stage3:MISSING:x", "ticker": "MISSING"}, enforcement="SHADOW"
    )
    assert decision.aggregate_status == "HOLD"
    assert decision.should_block is False
    assert [row.status for row in decision.checks] == ["HOLD", "HOLD"]


def test_current_candidate_pool_matches_frozen_boil_dryrun() -> None:
    state = json.loads(Path("data/_system/live_slots_state.json").read_text(encoding="utf-8"))
    candidates = state.get("candidate_pool") or []
    assert len(candidates) == 18
    checker = candidate_gate.CandidateGateChecker()
    decisions = checker.evaluate_many(candidates, enforcement="SHADOW")
    assert len(decisions) == 18
    assert all(decision.should_block is False for decision in decisions)

    boil_fail = []
    for decision in decisions:
        boil_result = next(row for row in decision.checks if row.checker == "boil_high_vol_volume_blind")
        if boil_result.status == "FAIL":
            boil_fail.append(decision.ticker)
    assert boil_fail == ["BNTX"]


def test_policy_default_is_shadow() -> None:
    assert candidate_gate.integrated_gate_enforcement() == "SHADOW"
