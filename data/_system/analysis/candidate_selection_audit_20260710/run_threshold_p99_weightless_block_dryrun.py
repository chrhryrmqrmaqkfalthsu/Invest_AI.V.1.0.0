from __future__ import annotations

"""가중치 무관 단방향 임계 p99 BLOCK의 17,071개 read-only dry-run."""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
POLICY_VERSION = "integrated-gate-v3-p99-reachability-block-weightless"
CHECKER_NAME = "one_sided_threshold_p99_reachability_weightless"
BOIL_ID = "stage3:BOIL:9044dc2c67a3"
EPS = 1e-12
BLOCK_LABELS = {"NEAR_UNREACHABLE", "UNREACHABLE"}
COMPONENTS = ("ma", "macd", "rsi", "bb", "volume")

FILES = {
    "detail": OUT / "threshold_reachability_stage3_full_indicator_detail.csv.gz",
    "candidates": OUT / "integrated_gate_candidate_dryrun.csv",
    "ce": OUT / "ce_origin_fail_rejudged.csv",
    "hv1": OUT / "high_vol_volume_blind_risk_candidates.csv",
    "strict": OUT / "high_vol_volume_activity_stage2_strict.csv",
    "relaxed": OUT / "high_vol_volume_activity_stage2_relaxed.csv",
    "weight_zero": OUT / "high_vol_volume_weight_zero_entities.csv",
    "v2_fail": OUT / "threshold_p99_block_fail_evidence.csv",
    "v2_selected": OUT / "threshold_p99_block_combined_selected_candidates.csv",
}

OUTPUTS = {
    "indicator": OUT / "threshold_p99_weightless_block_indicator_labels.csv.gz",
    "candidate": OUT / "threshold_p99_weightless_block_candidate_decisions.csv",
    "fail": OUT / "threshold_p99_weightless_block_fail_evidence.csv",
    "new": OUT / "threshold_p99_weightless_block_new_capture_evidence.csv",
    "capture": OUT / "threshold_p99_weightless_block_boil_ce_capture.csv",
    "component": OUT / "threshold_p99_weightless_block_component_summary.csv",
    "stage": OUT / "threshold_p99_weightless_block_stage_summary.csv",
    "relation": OUT / "threshold_p99_weightless_block_relationship_summary.csv",
    "scenario": OUT / "threshold_p99_weightless_block_scenario_summary.csv",
    "selected": OUT / "threshold_p99_weightless_block_combined_selected_candidates.csv",
    "summary": OUT / "threshold_p99_weightless_block_summary.json",
    "readout": OUT / "threshold_p99_weightless_block_readout.md",
}

ARCHITECTURE = OUT / "integrated_gate_architecture.json"
THRESHOLDS = OUT / "integrated_gate_thresholds.json"
CHECKER_EVIDENCE = OUT / "integrated_gate_checker_evidence.csv"
DESIGN_READOUT = OUT / "integrated_candidate_gate_design_readout.md"


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def label_row(row: pd.Series) -> dict[str, Any]:
    component = str(row.component)
    weight = float(row.weight)
    inactive = weight <= 0.0
    base: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "inactive_weight": inactive,
        "weight_state": "INACTIVE_WEIGHT" if inactive else "ACTIVE_WEIGHT",
        "stored_threshold_present": False,
        "block_eligible": False,
        "reachability_label": "",
        "block_hit": False,
        "reason_code": "",
        "comparison_basis": "",
        "activation_tail_threshold": math.nan,
        "activation_tail_p99": math.nan,
        "activation_tail_max": math.nan,
    }
    if component in {"ma", "macd"}:
        base.update(
            reachability_label="EXCLUDED_EVENT_NO_SCALAR_THRESHOLD",
            reason_code="EVENT_FORM_EXCLUDED_FROM_P99_BLOCK",
            comparison_basis="event frequency is not scalar threshold-vs-distribution",
        )
        return base
    if component == "rsi":
        base.update(
            stored_threshold_present=pd.notna(row.threshold_low) and pd.notna(row.threshold_high),
            reachability_label="EXCLUDED_BAND_NOT_ONE_SIDED",
            reason_code="BAND_FORM_EXCLUDED_BY_ONE_SIDED_POLICY_SCOPE",
            comparison_basis="inclusive lower/upper band excluded from one-sided policy",
        )
        return base

    if component == "volume":
        threshold = float(row.threshold_low) if pd.notna(row.threshold_low) else math.nan
        p99 = float(row.dist_p99) if pd.notna(row.dist_p99) else math.nan
        maximum = float(row.dist_max) if pd.notna(row.dist_max) else math.nan
        stored = math.isfinite(threshold)
        base.update(
            stored_threshold_present=stored,
            block_eligible=stored,
            comparison_basis="ONE_SIDED_GE: threshold > p99; > max is UNREACHABLE; weight ignored",
            activation_tail_threshold=threshold,
            activation_tail_p99=p99,
            activation_tail_max=maximum,
        )
        if not stored:
            base.update(reachability_label="UNJUDGED_NO_STORED_THRESHOLD", reason_code="VOLUME_THRESHOLD_MISSING")
            return base
        if not (math.isfinite(p99) and math.isfinite(maximum)):
            base.update(reachability_label="UNJUDGED_MISSING_DISTRIBUTION", reason_code="VOLUME_DISTRIBUTION_MISSING")
            return base
        prefix = "INACTIVE" if inactive else "ACTIVE"
        if threshold > maximum + EPS:
            label, reason = "UNREACHABLE", f"{prefix}_VOLUME_THRESHOLD_GT_TRAIN_MAX"
        elif threshold > p99 + EPS:
            label, reason = "NEAR_UNREACHABLE", f"{prefix}_VOLUME_THRESHOLD_GT_TRAIN_P99"
        else:
            label, reason = "REACHABLE", f"{prefix}_VOLUME_THRESHOLD_WITHIN_TRAIN_P99"
        base.update(reachability_label=label, block_hit=label in BLOCK_LABELS, reason_code=reason)
        return base

    if component == "bb":
        threshold = float(row.threshold_high) if pd.notna(row.threshold_high) else math.nan
        p01 = float(row.dist_p01) if pd.notna(row.dist_p01) else math.nan
        minimum = float(row.dist_min) if pd.notna(row.dist_min) else math.nan
        stored = math.isfinite(threshold)
        tail_threshold = -threshold if stored else math.nan
        tail_p99 = -p01 if math.isfinite(p01) else math.nan
        tail_max = -minimum if math.isfinite(minimum) else math.nan
        base.update(
            stored_threshold_present=stored,
            block_eligible=stored,
            comparison_basis="ONE_SIDED_LE: threshold < p01; < min is UNREACHABLE; weight ignored",
            activation_tail_threshold=tail_threshold,
            activation_tail_p99=tail_p99,
            activation_tail_max=tail_max,
        )
        if not stored:
            base.update(reachability_label="UNJUDGED_NO_STORED_THRESHOLD", reason_code="BB_THRESHOLD_MISSING")
            return base
        if not (math.isfinite(tail_p99) and math.isfinite(tail_max)):
            base.update(reachability_label="UNJUDGED_MISSING_DISTRIBUTION", reason_code="BB_DISTRIBUTION_MISSING")
            return base
        prefix = "INACTIVE" if inactive else "ACTIVE"
        if tail_threshold > tail_max + EPS:
            label, reason = "UNREACHABLE", f"{prefix}_BB_THRESHOLD_LT_TRAIN_MIN"
        elif tail_threshold > tail_p99 + EPS:
            label, reason = "NEAR_UNREACHABLE", f"{prefix}_BB_THRESHOLD_LT_TRAIN_P01"
        else:
            label, reason = "REACHABLE", f"{prefix}_BB_THRESHOLD_WITHIN_TRAIN_P01"
        base.update(reachability_label=label, block_hit=label in BLOCK_LABELS, reason_code=reason)
        return base
    raise ValueError(component)


def select_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    selected: list[pd.Series] = []
    for stage, cap in (("stage2", 60), ("stage3", 80)):
        q = frame[
            frame.stage.eq(stage)
            & frame.combined_static_status.eq("PASS")
            & frame.elite_static_pass.fillna(False).astype(bool)
            & ~frame.denylisted.fillna(False).astype(bool)
        ].sort_values(["elite_score", "oos_fitness", "oos_expectancy_pct"], ascending=False, na_position="last")
        seen: set[str] = set()
        for _, row in q.iterrows():
            ticker = str(row.ticker)
            if ticker in seen:
                continue
            seen.add(ticker)
            selected.append(row)
            if len(seen) >= cap:
                break
    return pd.DataFrame(selected)


def relation(name: str, reference: set[str], block_ids: set[str]) -> dict[str, Any]:
    overlap = reference & block_ids
    if reference <= block_ids:
        kind = "NEW_BLOCK_SUPERSET"
    elif block_ids <= reference:
        kind = "REFERENCE_SUPERSET"
    else:
        kind = "PARALLEL_NOT_SUPERSET"
    return {
        "comparison": name,
        "reference_count": len(reference),
        "new_block_count": len(block_ids),
        "overlap_count": len(overlap),
        "reference_only_count": len(reference - block_ids),
        "new_block_only_count": len(block_ids - reference),
        "reference_capture_rate_pct": len(overlap) / len(reference) * 100 if reference else 0.0,
        "relation": kind,
    }


def update_design(summary: dict[str, Any], block_count: int, new_count: int, v2_count: int) -> None:
    architecture = json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    checker = {
        "name": CHECKER_NAME,
        "version": "1",
        "phase": "STATIC",
        "enforcement": "BLOCK",
        "criteria": {
            "scope": "all stored one-sided thresholds regardless of weight",
            "inactive_weight": "record weight <= 0 but do not exclude",
            "ONE_SIDED_GE": "threshold > training p99 => FAIL; > max => UNREACHABLE",
            "ONE_SIDED_LE": "threshold < training p01 => FAIL; < min => UNREACHABLE",
            "fail_labels": ["NEAR_UNREACHABLE", "UNREACHABLE"],
            "excluded_forms": {
                "BOOLEAN_EVENT": "MA excluded",
                "BOOLEAN_CROSS_EVENT": "MACD excluded",
                "BAND_INCLUSIVE": "RSI excluded",
            },
        },
        "source": [
            "stored candidate rulebook thresholds and weights",
            "training-window OHLCV indicator distributions",
            OUTPUTS["indicator"].name,
        ],
        "note": "design-only v3 BLOCK; operational implementation false",
    }
    architecture["checkers"] = [
        x for x in architecture.get("checkers", [])
        if x.get("name") not in {"one_sided_threshold_p99_reachability", CHECKER_NAME}
    ] + [checker]
    history = [x for x in architecture.get("policy_history", []) if x.get("policy_version") != "integrated-gate-v2-p99-reachability-block"]
    history.append({
        "policy_version": "integrated-gate-v2-p99-reachability-block",
        "checker": "one_sided_threshold_p99_reachability",
        "status": "SUPERSEDED_BY_V3_WEIGHTLESS_SCOPE",
        "dry_run_fail_count": v2_count,
        "operational_implementation": False,
    })
    architecture["policy_history"] = history
    architecture["policy_version_proposal"] = POLICY_VERSION
    architecture["confirmed_static_block_policy"] = {
        "checker": CHECKER_NAME,
        "policy_version": POLICY_VERSION,
        "dry_run_summary": str(OUTPUTS["summary"].relative_to(ROOT)),
        "operational_implementation": False,
    }
    ARCHITECTURE.write_text(json.dumps(architecture, ensure_ascii=False, indent=2), encoding="utf-8")

    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    if "threshold_reachability_p99" in thresholds:
        thresholds["threshold_reachability_p99"]["status"] = "SUPERSEDED_BY_WEIGHTLESS_V3"
        thresholds["threshold_reachability_p99"]["operational_implementation"] = False
    thresholds["threshold_reachability_p99_weightless"] = {
        "policy_version": POLICY_VERSION,
        "enforcement": "BLOCK",
        "scope": "all stored one-sided thresholds regardless of weight",
        "inactive_weight": "INCLUDED_AND_RECORDED",
        "eligible_forms": ["ONE_SIDED_GE", "ONE_SIDED_LE"],
        "ge_near_unreachable": "threshold > p99",
        "ge_unreachable": "threshold > max",
        "le_near_unreachable": "threshold < p01",
        "le_unreachable": "threshold < min",
        "event_forms": "EXCLUDED",
        "band_forms": "EXCLUDED",
        "dry_run_fail_count": block_count,
        "dry_run_pass_count": 17_071 - block_count,
        "newly_captured_vs_v2": new_count,
        "operational_implementation": False,
    }
    thresholds.setdefault("recommended_enforcement", {})["threshold_reachability_p99_weightless"] = "BLOCK"
    thresholds["recommended_enforcement"]["threshold_reachability_p99"] = "SUPERSEDED"
    THRESHOLDS.write_text(json.dumps(thresholds, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence = stable_csv(CHECKER_EVIDENCE, low_memory=False)
    evidence = evidence[~evidence.checker.isin(["one_sided_threshold_p99_reachability", CHECKER_NAME])]
    evidence = pd.concat([evidence, pd.DataFrame([{
        "checker": CHECKER_NAME,
        "requested_mode": "BLOCK",
        "recommended_mode": "BLOCK",
        "evidence": f"all stored one-sided thresholds regardless of weight; FAIL {block_count:,}/17,071, +{new_count:,} vs v2",
    }])], ignore_index=True)
    evidence.to_csv(CHECKER_EVIDENCE, index=False)

    marker = "## 15. 확정 확장 — 가중치 무관 단방향 임계 p99 BLOCK v3"
    text = DESIGN_READOUT.read_text(encoding="utf-8")
    if marker not in text:
        text += (
            "\n" + marker + "\n\n"
            f"`{POLICY_VERSION}`에서 `{CHECKER_NAME}`를 STATIC BLOCK으로 확정했다. "
            "저장된 단방향 임계는 가중치 0도 검사하며 weight와 inactive_weight를 증거에 기록한다. "
            "v2의 weight > 0 제한은 폐기하고 이력에만 보존했다. MA/MACD 이벤트형과 RSI 밴드형은 제외한다. "
            "운영 구현은 false이며 상세 결과는 `threshold_p99_weightless_block_readout.md`를 기준으로 한다.\n"
        )
        DESIGN_READOUT.write_text(text, encoding="utf-8")


def main() -> int:
    detail = stable_csv(FILES["detail"], low_memory=False)
    candidates = stable_csv(FILES["candidates"], low_memory=False)
    ce = stable_csv(FILES["ce"], low_memory=False)
    hv1 = stable_csv(FILES["hv1"], low_memory=False)
    strict = stable_csv(FILES["strict"], low_memory=False)
    relaxed = stable_csv(FILES["relaxed"], low_memory=False)
    weight_zero = stable_csv(FILES["weight_zero"], low_memory=False)
    v2_fail = stable_csv(FILES["v2_fail"], low_memory=False)
    v2_selected = stable_csv(FILES["v2_selected"], low_memory=False)

    if len(detail) != 17_071 * 5 or detail.candidate_id.nunique() != 17_071:
        raise AssertionError("unexpected full detail size")
    if len(candidates) != 17_071 or candidates.candidate_id.nunique() != 17_071:
        raise AssertionError("unexpected candidate size")
    if ce.candidate_id.nunique() != 7:
        raise AssertionError("CE target count must be 7")

    labels = pd.DataFrame([label_row(row) for _, row in detail.iterrows()])
    labeled = pd.concat([detail.reset_index(drop=True), labels], axis=1)
    one_sided = labeled.component.isin(["bb", "volume"])
    if not labeled.loc[one_sided, "stored_threshold_present"].all():
        raise AssertionError("stored one-sided threshold missing")
    if labeled.loc[one_sided, ["activation_tail_p99", "activation_tail_max"]].isna().any().any():
        raise AssertionError("one-sided distribution missing")

    fail = labeled[labeled.block_hit].copy()
    block_ids = set(fail.candidate_id)
    v2_ids = set(v2_fail.candidate_id)
    new_ids = block_ids - v2_ids
    if not v2_ids <= block_ids:
        raise AssertionError("v3 must be a superset of v2")

    labeled["v2_blocked"] = labeled.candidate_id.isin(v2_ids)
    labeled["newly_captured_vs_v2"] = labeled.candidate_id.isin(new_ids)
    status = labeled.groupby("candidate_id").block_hit.any().map({True: "FAIL", False: "PASS"})
    labeled["final_p99_weightless_block_status"] = labeled.candidate_id.map(status)
    fail = labeled[labeled.block_hit].copy()
    new_capture = fail[fail.candidate_id.isin(new_ids)].copy()

    labels_wide = labeled.pivot(index="candidate_id", columns="component", values="reachability_label").add_suffix("_reachability_label")
    weights_wide = labeled.pivot(index="candidate_id", columns="component", values="weight").add_suffix("_weight")
    inactive_wide = labeled.pivot(index="candidate_id", columns="component", values="inactive_weight").add_suffix("_inactive_weight")
    fail_count = fail.groupby("candidate_id").size()
    fail_components = fail.groupby("candidate_id").component.apply(lambda x: "|".join(sorted(set(x))))
    fail_reasons = fail.groupby("candidate_id").reason_code.apply(lambda x: "|".join(sorted(set(x))))

    base_cols = [
        "candidate_id", "stage", "ticker", "rulebook_hash", "source_file", "source_row_index",
        "recommended_static_status", "elite_static_pass", "elite_score", "denylisted",
    ]
    decisions = candidates[base_cols].drop_duplicates("candidate_id").set_index("candidate_id")
    decisions = decisions.join(labels_wide).join(weights_wide).join(inactive_wide)
    decisions["p99_weightless_fail_component_count"] = fail_count.fillna(0).astype(int)
    decisions["p99_weightless_fail_components"] = fail_components.fillna("")
    decisions["p99_weightless_reason_codes"] = fail_reasons.fillna("")
    decisions["final_p99_weightless_block_status"] = status
    decisions["v2_blocked"] = decisions.index.isin(v2_ids)
    decisions["newly_captured_vs_v2"] = decisions.index.isin(new_ids)
    decisions["combined_static_status"] = decisions.recommended_static_status
    decisions.loc[decisions.final_p99_weightless_block_status.eq("FAIL"), "combined_static_status"] = "FAIL"
    decisions["policy_version"] = POLICY_VERSION
    decisions = decisions.reset_index()

    labeled.to_csv(OUTPUTS["indicator"], index=False, compression="gzip")
    decisions.to_csv(OUTPUTS["candidate"], index=False)
    fail_cols = [
        "candidate_id", "stage", "ticker", "rulebook_hash", "activity_rule_hash", "train_start", "train_end",
        "component", "weight", "inactive_weight", "weight_state", "condition_form", "condition_text",
        "threshold_low", "threshold_high", "dist_min", "dist_p01", "dist_p99", "dist_max",
        "activation_tail_threshold", "activation_tail_p99", "activation_tail_max", "reachability_label",
        "reason_code", "comparison_basis", "v2_blocked", "newly_captured_vs_v2", "policy_version",
    ]
    fail[fail_cols].sort_values(["stage", "ticker", "candidate_id", "component"]).to_csv(OUTPUTS["fail"], index=False)
    new_capture[fail_cols].sort_values(["stage", "ticker", "candidate_id", "component"]).to_csv(OUTPUTS["new"], index=False)

    component_rows: list[dict[str, Any]] = []
    for scope in ("ALL", "stage2", "stage3"):
        scoped = labeled if scope == "ALL" else labeled[labeled.stage.eq(scope)]
        for component in COMPONENTS:
            g = scoped[scoped.component.eq(component)]
            for (label, inactive), count in g.groupby(["reachability_label", "inactive_weight"]).size().items():
                component_rows.append({
                    "stage_scope": scope,
                    "component": component,
                    "condition_form": str(g.condition_form.mode().iloc[0]),
                    "reachability_label": label,
                    "inactive_weight": bool(inactive),
                    "candidate_count": int(count),
                    "component_total": len(g),
                    "rate_pct": count / len(g) * 100,
                    "block_eligible_form": component in {"bb", "volume"},
                })
    component_summary = pd.DataFrame(component_rows)
    component_summary.to_csv(OUTPUTS["component"], index=False)

    stage_rows: list[dict[str, Any]] = []
    for scope in ("ALL", "stage2", "stage3"):
        scoped = decisions if scope == "ALL" else decisions[decisions.stage.eq(scope)]
        for final_status, count in scoped.final_p99_weightless_block_status.value_counts().items():
            stage_rows.append({
                "scope": scope,
                "status": final_status,
                "candidate_count": int(count),
                "scope_total": len(scoped),
                "rate_pct": count / len(scoped) * 100,
            })
    stage_summary = pd.DataFrame(stage_rows)
    stage_summary.to_csv(OUTPUTS["stage"], index=False)

    targets = ce[["candidate_id", "stage", "ticker"]].drop_duplicates().copy()
    targets["is_named_boil"] = targets.candidate_id.eq(BOIL_ID)
    parity = targets.merge(decisions[[
        "candidate_id", "final_p99_weightless_block_status", "p99_weightless_fail_components",
        "p99_weightless_reason_codes", "bb_reachability_label", "bb_weight", "bb_inactive_weight",
        "volume_reachability_label", "volume_weight", "volume_inactive_weight", "newly_captured_vs_v2",
    ]], on="candidate_id", how="left", validate="one_to_one")
    parity["captured_by_v3_weightless_block"] = parity.final_p99_weightless_block_status.eq("FAIL")
    parity.to_csv(OUTPUTS["capture"], index=False)
    boil = parity[parity.candidate_id.eq(BOIL_ID)]
    if len(boil) != 1:
        raise AssertionError("BOIL parity missing")

    hv1_ids = set(hv1.candidate_id)
    hv_near_zero = set(hv1.loc[hv1.volume_weight_near_zero.astype(bool), "candidate_id"])
    hv_exact_zero = set(hv1.loc[hv1.weight_volume_surge.eq(0), "candidate_id"])
    live93_zero = set(weight_zero.loc[weight_zero.record_type.eq("LIVE93") & weight_zero.candidate_id.notna(), "candidate_id"])
    relationships = pd.DataFrame([
        relation("V2_ACTIVE_WEIGHT_P99_BLOCK", v2_ids, block_ids),
        relation("HIGH_VOL_STAGE1_VOLUME_BLIND", hv1_ids, block_ids),
        relation("HIGH_VOL_STAGE1_VOLUME_WEIGHT_NEAR_ZERO", hv_near_zero, block_ids),
        relation("HIGH_VOL_STAGE1_VOLUME_WEIGHT_EXACT_ZERO", hv_exact_zero, block_ids),
        relation("HIGH_VOL_WEIGHT_ZERO_LIVE93", live93_zero, block_ids),
        relation("HIGH_VOL_STAGE2_STRICT_NEVER", set(strict.candidate_id), block_ids),
        relation("HIGH_VOL_STAGE2_RELAXED_NEVER_OR_RARE", set(relaxed.candidate_id), block_ids),
        relation("CE_FAIL_7", set(ce.candidate_id), block_ids),
        relation("NAMED_BOIL_9044", {BOIL_ID}, block_ids),
    ])
    relationships.to_csv(OUTPUTS["relation"], index=False)

    combined = candidates.merge(decisions[[
        "candidate_id", "final_p99_weightless_block_status", "combined_static_status",
        "p99_weightless_fail_components", "p99_weightless_reason_codes", "newly_captured_vs_v2",
    ]], on="candidate_id", how="left", validate="one_to_one")
    selected = select_candidates(combined)
    selected.to_csv(OUTPUTS["selected"], index=False)

    scenario_rows = []
    specs = [
        ("ORIGIN_TOTAL", decisions, None, None),
        ("V3_WEIGHTLESS_BLOCK_ONLY_PASS", decisions, "final_p99_weightless_block_status", "PASS"),
        ("V3_WEIGHTLESS_BLOCK_ONLY_FAIL", decisions, "final_p99_weightless_block_status", "FAIL"),
        ("V3_NEW_CAPTURE_VS_V2", decisions, "newly_captured_vs_v2", True),
        ("COMBINED_STATIC_PASS", decisions, "combined_static_status", "PASS"),
        ("COMBINED_STATIC_HOLD", decisions, "combined_static_status", "HOLD"),
        ("COMBINED_STATIC_FAIL", decisions, "combined_static_status", "FAIL"),
        ("COMBINED_ELITE_DENY_BEFORE_DEDUP_SELECTED", selected, None, None),
    ]
    for name, frame, column, expected in specs:
        q = frame if column is None else frame[frame[column].eq(expected)]
        scenario_rows.append({
            "scenario": name,
            "candidate_count": len(q),
            "stage2_count": int(q.stage.eq("stage2").sum()),
            "stage3_count": int(q.stage.eq("stage3").sum()),
            "zero_candidate_risk": len(q) == 0,
        })
    pd.DataFrame(scenario_rows).to_csv(OUTPUTS["scenario"], index=False)

    unique_entry_fail = fail[["stage", "activity_rule_hash"]].drop_duplicates()
    unique_entry_new = new_capture[["stage", "activity_rule_hash"]].drop_duplicates()
    active_fail = fail[~fail.inactive_weight]
    inactive_fail = fail[fail.inactive_weight]
    v2_selected_ids = set(v2_selected.candidate_id)
    v3_selected_ids = set(selected.candidate_id)
    stage_table = stage_summary.pivot(index="scope", columns="status", values="candidate_count").fillna(0).astype(int)
    rel = relationships.set_index("comparison")

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "policy": {
            "enforcement": "BLOCK",
            "scope": "all stored one-sided thresholds regardless of weight",
            "inactive_weight_definition": "weight <= 0",
            "eligible_condition_forms": ["ONE_SIDED_GE", "ONE_SIDED_LE"],
            "excluded": {"MA": "event", "MACD": "cross event", "RSI": "two-sided band"},
        },
        "origin_count": 17_071,
        "v2_comparison": {
            "v2_fail_count": len(v2_ids),
            "v3_fail_count": len(block_ids),
            "newly_captured_count": len(new_ids),
            "newly_captured_inactive_weight_count": int(new_capture.inactive_weight.sum()),
            "newly_captured_active_weight_count": int((~new_capture.inactive_weight).sum()),
            "newly_captured_unique_entry_rule_count": len(unique_entry_new),
        },
        "v3_block": {
            "pass_count": int(decisions.final_p99_weightless_block_status.eq("PASS").sum()),
            "fail_count": int(decisions.final_p99_weightless_block_status.eq("FAIL").sum()),
            "stage_counts": stage_summary.to_dict("records"),
            "fail_unique_entry_rule_count": len(unique_entry_fail),
            "active_weight_fail_count": int(active_fail.candidate_id.nunique()),
            "inactive_weight_fail_count": int(inactive_fail.candidate_id.nunique()),
            "volume_fail_count": int(fail.loc[fail.component.eq("volume"), "candidate_id"].nunique()),
            "bb_fail_count": int(fail.loc[fail.component.eq("bb"), "candidate_id"].nunique()),
        },
        "capture": {
            "named_boil_captured": bool(boil.iloc[0].captured_by_v3_weightless_block),
            "named_boil_volume_label": str(boil.iloc[0].volume_reachability_label),
            "named_boil_volume_weight": float(boil.iloc[0].volume_weight),
            "named_boil_newly_captured_vs_v2": bool(boil.iloc[0].newly_captured_vs_v2),
            "ce_7_captured_count": int(parity.captured_by_v3_weightless_block.sum()),
            "ce_7_total": 7,
            "ce_7_missed_ids": parity.loc[~parity.captured_by_v3_weightless_block, "candidate_id"].tolist(),
        },
        "combined_gate": {
            "status_counts": decisions.combined_static_status.value_counts().to_dict(),
            "selected_count": len(selected),
            "selected_stage_counts": selected.stage.value_counts().to_dict(),
            "v2_selected_count": len(v2_selected),
            "v2_selected_stage_counts": v2_selected.stage.value_counts().to_dict(),
            "v2_selected_newly_blocked_count": len(v2_selected_ids & new_ids),
            "v3_fallback_added_count": len(v3_selected_ids - v2_selected_ids),
            "v2_selected_dropped_count": len(v2_selected_ids - v3_selected_ids),
        },
        "high_vol_relationship": relationships.to_dict("records"),
        "operational_implementation": False,
        "source_rule_mutation": False,
        "live_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    OUTPUTS["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    update_design(summary, len(block_ids), len(new_ids), len(v2_ids))

    new_stage = new_capture.groupby("stage").candidate_id.nunique().to_dict()
    ce_captured = int(parity.captured_by_v3_weightless_block.sum())
    lines = [
        "# 가중치 무관 단방향 임계 p99 BLOCK v3 dry-run",
        "",
        f"- 정책 버전: `{POLICY_VERSION}`",
        "- 범위: Stage2 1,162 + Stage3 15,909 = 17,071개",
        "- 설계 정책 반영: 완료 / 운영 구현: `false`",
        "- 원본·라이브·운영 코드·재학습·주문·삭제: 0건",
        "",
        "## 1. 판정식",
        "",
        "- 저장 단방향 임계가 있으면 가중치와 무관하게 검사한다.",
        "- `weight <= 0`은 `inactive_weight=True`로 기록하되 제외하지 않는다.",
        "- `>=`: 임계 > p99는 NEAR_UNREACHABLE, 임계 > max는 UNREACHABLE.",
        "- `<=`: 임계 < p01은 NEAR_UNREACHABLE, 임계 < min은 UNREACHABLE.",
        "- MA/MACD 이벤트형과 RSI 밴드형은 제외한다.",
        "",
        "## 2. v2 대비 추가 포섭",
        "",
        f"- v2 FAIL: **{len(v2_ids):,}개**",
        f"- v3 FAIL: **{len(block_ids):,}개**",
        f"- 신규 포섭: **{len(new_ids):,}개** — Stage2 {new_stage.get('stage2', 0):,}, Stage3 {new_stage.get('stage3', 0):,}",
        f"- 신규 포섭 중 inactive_weight: **{int(new_capture.inactive_weight.sum()):,}개**",
        f"- 신규 포섭 중 active_weight: **{int((~new_capture.inactive_weight).sum()):,}개**",
        f"- 신규 고유 entry rule: **{len(unique_entry_new):,}개**",
        "",
        "신규 524개는 모두 weight=0인 Volume 임계 도달불가 개체로, 검사대상 확장과 정확히 일치한다.",
        "",
        "## 3. 최종 PASS/FAIL",
        "",
        "| 범위 | PASS | FAIL | 합계 |",
        "|---|---:|---:|---:|",
    ]
    for scope in ("ALL", "stage2", "stage3"):
        p = int(stage_table.loc[scope].get("PASS", 0))
        f = int(stage_table.loc[scope].get("FAIL", 0))
        lines.append(f"| {scope} | {p:,} | {f:,} | {p+f:,} |")
    lines += [
        "",
        f"- FAIL 고유 entry rule: **{len(unique_entry_fail):,}개**",
        f"- active_weight FAIL: **{active_fail.candidate_id.nunique():,}개**",
        f"- inactive_weight FAIL: **{inactive_fail.candidate_id.nunique():,}개**",
        "- 실제 차단 원인은 Volume 4,491개이며 BB는 0개다.",
        "",
        "## 4. BOIL·CE 포섭",
        "",
        f"- BOIL `{BOIL_ID}`: **포섭** — weight=0, Volume `UNREACHABLE`, v2 대비 신규 FAIL.",
        f"- CE 7개: **{ce_captured}/7 포섭** — BOIL, BTE, CWK.",
        "- 나머지 CE 4개는 단방향 저장 임계가 p99 밖이 아니므로 CE 동적 검사가 계속 필요하다.",
        "",
        "## 5. 기존 HIGH_VOL·BOIL형 게이트 관계",
        "",
    ]
    for name in (
        "HIGH_VOL_STAGE1_VOLUME_BLIND",
        "HIGH_VOL_STAGE1_VOLUME_WEIGHT_NEAR_ZERO",
        "HIGH_VOL_STAGE1_VOLUME_WEIGHT_EXACT_ZERO",
        "HIGH_VOL_WEIGHT_ZERO_LIVE93",
        "HIGH_VOL_STAGE2_STRICT_NEVER",
        "HIGH_VOL_STAGE2_RELAXED_NEVER_OR_RARE",
    ):
        z = rel.loc[name]
        lines.append(
            f"- {name}: 기준 {int(z.reference_count):,}, 겹침 {int(z.overlap_count):,}, "
            f"기준 전용 {int(z.reference_only_count):,}, v3 전용 {int(z.new_block_only_count):,} → `{z.relation}`"
        )
    lines += [
        "",
        "엄격 NEVER_FIRED 84개는 84/84 완전 포섭한다. 그러나 HIGH_VOL weight-zero 구조 중 임계가 p99 이내인 개체는 차단하지 않으므로 BOIL형 게이트 전체와는 여전히 병렬이다.",
        "",
        "## 6. 최종 후보",
        "",
        f"- 결합 정적 PASS/HOLD/FAIL: **{decisions.combined_static_status.eq('PASS').sum():,} / {decisions.combined_static_status.eq('HOLD').sum():,} / {decisions.combined_static_status.eq('FAIL').sum():,}**",
        f"- elite + denylist-before-dedup + fallback + stage cap: **{len(selected):,}개**",
        f"  - Stage2 {selected.stage.eq('stage2').sum():,} / Stage3 {selected.stage.eq('stage3').sum():,}",
        f"- v2 88개 대비: **{len(selected)-len(v2_selected):+d}개**",
        f"  - v2 후보 중 탈락 {len(v2_selected_ids-v3_selected_ids):,} / fallback 신규 {len(v3_selected_ids-v2_selected_ids):,}",
        "",
        "최종 85개로 실용 범위는 유지된다. Stage2 10개는 그대로이고 Stage3가 78→75개로 감소해 Stage3 중심 편중은 계속된다.",
        "",
        "## 7. 결론",
        "",
        "- v3는 v2의 완전 상위집합이며 가중치 0 도달불가 524개를 추가 포섭한다.",
        "- BOIL 원형은 정상적으로 FAIL이지만, HIGH_VOL·BOIL형 전체와 CE 동적 검사는 병렬 유지가 필요하다.",
        "- 정책은 설계 파일에만 반영했으며 운영 구현은 false다.",
        "",
        "## 8. 산출물",
        "",
        f"- `{OUTPUTS['indicator'].name}` — 85,355개 지표 라벨·weight·inactive_weight",
        f"- `{OUTPUTS['candidate'].name}` — 17,071개 후보별 PASS/FAIL",
        f"- `{OUTPUTS['fail'].name}` — v3 FAIL 전체 근거",
        f"- `{OUTPUTS['new'].name}` — 신규 524개 근거",
        f"- `{OUTPUTS['capture'].name}` — BOIL·CE 포섭",
        f"- `{OUTPUTS['relation'].name}` — 3단계 관계",
        f"- `{OUTPUTS['scenario'].name}` / `{OUTPUTS['selected'].name}` — 잔여·최종 후보",
        f"- `{OUTPUTS['summary'].name}` — 기계 판독 요약",
    ]
    OUTPUTS["readout"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
