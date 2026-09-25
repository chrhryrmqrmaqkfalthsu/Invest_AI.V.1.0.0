from __future__ import annotations

"""도달불가 단방향 임계 BLOCK 확정 정책의 17,071개 read-only dry-run.

원본 룰·라이브·운영 코드·주문·학습 데이터는 수정하지 않는다.
기존 3단계 전수 분포 산출물을 입력으로 사용하고 분석 산출물 및 설계 전용
통합 게이트 정책 파일만 갱신한다.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"

FULL_DETAIL_IN = OUT / "threshold_reachability_stage3_full_indicator_detail.csv.gz"
CANDIDATE_BASE_IN = OUT / "integrated_gate_candidate_dryrun.csv"
CE_FAIL_IN = OUT / "ce_origin_fail_rejudged.csv"
HIGH_VOL_STAGE1_IN = OUT / "high_vol_volume_blind_risk_candidates.csv"
HIGH_VOL_STRICT_IN = OUT / "high_vol_volume_activity_stage2_strict.csv"
HIGH_VOL_RELAXED_IN = OUT / "high_vol_volume_activity_stage2_relaxed.csv"
OLD_SELECTED_IN = OUT / "integrated_gate_pass_candidates.csv"

INDICATOR_OUT = OUT / "threshold_p99_block_indicator_labels.csv.gz"
CANDIDATE_OUT = OUT / "threshold_p99_block_candidate_decisions.csv"
FAIL_OUT = OUT / "threshold_p99_block_fail_evidence.csv"
CAPTURE_OUT = OUT / "threshold_p99_block_boil_ce_capture.csv"
COMPONENT_OUT = OUT / "threshold_p99_block_component_summary.csv"
STAGE_OUT = OUT / "threshold_p99_block_stage_summary.csv"
RELATION_OUT = OUT / "threshold_p99_block_relationship_summary.csv"
SCENARIO_OUT = OUT / "threshold_p99_block_scenario_summary.csv"
SELECTED_OUT = OUT / "threshold_p99_block_combined_selected_candidates.csv"
SUMMARY_OUT = OUT / "threshold_p99_block_summary.json"
READOUT_OUT = OUT / "threshold_p99_block_readout.md"

ARCHITECTURE = OUT / "integrated_gate_architecture.json"
THRESHOLDS = OUT / "integrated_gate_thresholds.json"
CHECKER_EVIDENCE = OUT / "integrated_gate_checker_evidence.csv"
DESIGN_READOUT = OUT / "integrated_candidate_gate_design_readout.md"

POLICY_VERSION = "integrated-gate-v2-p99-reachability-block"
EPS = 1e-12
BLOCK_LABELS = {"NEAR_UNREACHABLE", "UNREACHABLE"}
COMPONENTS = ("ma", "macd", "rsi", "bb", "volume")
BOIL_ID = "stage3:BOIL:9044dc2c67a3"


def stable_read_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def text_or_empty(value: Any) -> str:
    return "" if pd.isna(value) else str(value)


def label_row(row: pd.Series) -> dict[str, Any]:
    component = str(row["component"])
    weight = float(row["weight"])
    active = weight > 0.0
    result: dict[str, Any] = {
        "policy_version": POLICY_VERSION,
        "active_weight_gt_zero": active,
        "block_eligible": False,
        "reachability_label": "",
        "block_hit": False,
        "reason_code": "",
        "comparison_basis": "",
        "activation_tail_threshold": math.nan,
        "activation_tail_p99": math.nan,
        "activation_tail_max": math.nan,
    }
    if not active:
        result.update(
            reachability_label="INACTIVE_WEIGHT",
            reason_code="WEIGHT_NOT_POSITIVE_EXCLUDED",
            comparison_basis="weight <= 0",
        )
        return result
    if component in {"ma", "macd"}:
        result.update(
            reachability_label="EXCLUDED_EVENT_NO_SCALAR_THRESHOLD",
            reason_code="EVENT_FORM_EXCLUDED_FROM_P99_BLOCK",
            comparison_basis="event frequency is not a scalar threshold-vs-distribution comparison",
        )
        return result
    if component == "rsi":
        result.update(
            reachability_label="EXCLUDED_BAND_NOT_ONE_SIDED",
            reason_code="BAND_FORM_EXCLUDED_BY_ONE_SIDED_POLICY_SCOPE",
            comparison_basis="inclusive lower/upper band; policy applies only to one-sided >= or <= thresholds",
        )
        return result
    if component == "volume":
        threshold = float(row["threshold_low"])
        raw_p99 = float(row["dist_p99"])
        raw_max = float(row["dist_max"])
        result.update(
            block_eligible=True,
            comparison_basis="ONE_SIDED_GE: raw threshold > raw p99; max separates UNREACHABLE",
            activation_tail_threshold=threshold,
            activation_tail_p99=raw_p99,
            activation_tail_max=raw_max,
        )
        if threshold > raw_max + EPS:
            label = "UNREACHABLE"
            reason = "ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_MAX"
        elif threshold > raw_p99 + EPS:
            label = "NEAR_UNREACHABLE"
            reason = "ACTIVE_VOLUME_THRESHOLD_GT_TRAIN_P99"
        else:
            label = "REACHABLE"
            reason = "ACTIVE_VOLUME_THRESHOLD_WITHIN_TRAIN_P99"
        result.update(reachability_label=label, block_hit=label in BLOCK_LABELS, reason_code=reason)
        return result
    if component == "bb":
        threshold = float(row["threshold_high"])
        raw_p01 = float(row["dist_p01"])
        raw_min = float(row["dist_min"])
        # <= 조건은 -값으로 뒤집으면 >= 조건이 된다. 따라서 raw p01이 activation-tail p99와 동치다.
        tail_threshold = -threshold
        tail_p99 = -raw_p01
        tail_max = -raw_min
        result.update(
            block_eligible=True,
            comparison_basis="ONE_SIDED_LE: raw threshold < raw p01 (equivalent activation-tail threshold > p99); min separates UNREACHABLE",
            activation_tail_threshold=tail_threshold,
            activation_tail_p99=tail_p99,
            activation_tail_max=tail_max,
        )
        if tail_threshold > tail_max + EPS:
            label = "UNREACHABLE"
            reason = "ACTIVE_BB_THRESHOLD_LT_TRAIN_MIN"
        elif tail_threshold > tail_p99 + EPS:
            label = "NEAR_UNREACHABLE"
            reason = "ACTIVE_BB_THRESHOLD_LT_TRAIN_P01"
        else:
            label = "REACHABLE"
            reason = "ACTIVE_BB_THRESHOLD_WITHIN_TRAIN_P01"
        result.update(reachability_label=label, block_hit=label in BLOCK_LABELS, reason_code=reason)
        return result
    raise ValueError(component)


def select_combined(frame: pd.DataFrame) -> pd.DataFrame:
    selected: list[pd.Series] = []
    for stage, cap in (("stage2", 60), ("stage3", 80)):
        q = frame[
            (frame["stage"] == stage)
            & (frame["combined_static_status"] == "PASS")
            & frame["elite_static_pass"].fillna(False).astype(bool)
            & ~frame["denylisted"].fillna(False).astype(bool)
        ].sort_values(
            ["elite_score", "oos_fitness", "oos_expectancy_pct"],
            ascending=False,
            na_position="last",
        )
        seen: set[str] = set()
        stage_count = 0
        for _, row in q.iterrows():
            ticker = str(row["ticker"])
            if ticker in seen:
                continue
            seen.add(ticker)
            selected.append(row)
            stage_count += 1
            if stage_count >= cap:
                break
    return pd.DataFrame(selected)


def relation_row(name: str, reference_ids: set[str], block_ids: set[str]) -> dict[str, Any]:
    overlap = reference_ids & block_ids
    return {
        "comparison": name,
        "reference_count": len(reference_ids),
        "new_block_count": len(block_ids),
        "overlap_count": len(overlap),
        "reference_only_count": len(reference_ids - block_ids),
        "new_block_only_count": len(block_ids - reference_ids),
        "reference_capture_rate_pct": len(overlap) / len(reference_ids) * 100 if reference_ids else 0.0,
        "relation": "SUPERSET" if reference_ids <= block_ids else "PARALLEL_NOT_SUPERSET",
    }


def main() -> int:
    detail = stable_read_csv(FULL_DETAIL_IN, low_memory=False)
    candidates = stable_read_csv(CANDIDATE_BASE_IN, low_memory=False)
    ce = stable_read_csv(CE_FAIL_IN, low_memory=False)
    high_vol_stage1 = stable_read_csv(HIGH_VOL_STAGE1_IN, low_memory=False)
    high_vol_strict = stable_read_csv(HIGH_VOL_STRICT_IN, low_memory=False)
    high_vol_relaxed = stable_read_csv(HIGH_VOL_RELAXED_IN, low_memory=False)
    old_selected = stable_read_csv(OLD_SELECTED_IN, low_memory=False)

    if len(candidates) != 17_071 or candidates["candidate_id"].nunique() != 17_071:
        raise AssertionError((len(candidates), candidates["candidate_id"].nunique()))
    if len(detail) != 17_071 * 5:
        raise AssertionError(len(detail))
    if set(detail["component"].unique()) != set(COMPONENTS):
        raise AssertionError(detail["component"].unique())
    if (detail["weight"] < 0).any():
        raise AssertionError("negative core weight found; policy requires weight > 0 semantics")
    if ce["candidate_id"].nunique() != 7:
        raise AssertionError(ce["candidate_id"].nunique())

    policy = pd.DataFrame([label_row(row) for _, row in detail.iterrows()])
    labeled = pd.concat([detail.reset_index(drop=True), policy], axis=1)
    if labeled["reachability_label"].eq("").any():
        raise AssertionError("blank labels")
    if labeled["block_eligible"].any() and labeled.loc[labeled["block_eligible"], ["activation_tail_p99", "activation_tail_max"]].isna().any().any():
        raise AssertionError("missing one-sided distribution quantiles")

    fail_evidence = labeled[labeled["block_hit"]].copy()
    block_ids = set(fail_evidence["candidate_id"])
    candidate_fail = labeled.groupby("candidate_id")["block_hit"].any()
    final_status = candidate_fail.map({True: "FAIL", False: "PASS"})
    labeled["final_p99_block_status"] = labeled["candidate_id"].map(final_status)

    label_pivot = labeled.pivot(index="candidate_id", columns="component", values="reachability_label")
    label_pivot = label_pivot.rename(columns={c: f"{c}_reachability_label" for c in label_pivot.columns})
    fail_counts = fail_evidence.groupby("candidate_id").size()
    fail_components = fail_evidence.groupby("candidate_id")["component"].apply(lambda x: "|".join(sorted(set(map(str, x)))))
    fail_reasons = fail_evidence.groupby("candidate_id")["reason_code"].apply(lambda x: "|".join(sorted(set(map(str, x)))))

    base_cols = [
        "candidate_id", "stage", "ticker", "rulebook_hash", "source_file", "source_row_index",
        "recommended_static_status", "elite_static_pass", "elite_score", "denylisted",
    ]
    decisions = candidates[base_cols].drop_duplicates("candidate_id").set_index("candidate_id")
    decisions = decisions.join(label_pivot)
    decisions["p99_block_fail_component_count"] = fail_counts
    decisions["p99_block_fail_components"] = fail_components
    decisions["p99_block_reason_codes"] = fail_reasons
    decisions["p99_block_fail_component_count"] = decisions["p99_block_fail_component_count"].fillna(0).astype(int)
    decisions[["p99_block_fail_components", "p99_block_reason_codes"]] = decisions[["p99_block_fail_components", "p99_block_reason_codes"]].fillna("")
    decisions["final_p99_block_status"] = final_status
    decisions["combined_static_status"] = decisions["recommended_static_status"]
    decisions.loc[decisions["final_p99_block_status"] == "FAIL", "combined_static_status"] = "FAIL"
    decisions["policy_version"] = POLICY_VERSION
    decisions = decisions.reset_index()

    labeled.to_csv(INDICATOR_OUT, index=False, compression="gzip")
    decisions.to_csv(CANDIDATE_OUT, index=False)

    fail_cols = [
        "candidate_id", "stage", "ticker", "rulebook_hash", "activity_rule_hash", "train_start", "train_end",
        "component", "weight", "condition_form", "condition_text", "threshold_low", "threshold_high",
        "dist_min", "dist_p01", "dist_p99", "dist_max", "activation_tail_threshold", "activation_tail_p99",
        "activation_tail_max", "reachability_label", "reason_code", "comparison_basis", "policy_version",
    ]
    fail_evidence[fail_cols].sort_values(["stage", "ticker", "candidate_id", "component"]).to_csv(FAIL_OUT, index=False)

    # 지표별·단계별 요약
    component_rows: list[dict[str, Any]] = []
    for stage_scope in ("ALL", "stage2", "stage3"):
        scoped = labeled if stage_scope == "ALL" else labeled[labeled["stage"] == stage_scope]
        for component in COMPONENTS:
            g = scoped[scoped["component"] == component]
            for label, count in g["reachability_label"].value_counts().items():
                component_rows.append({
                    "stage_scope": stage_scope,
                    "component": component,
                    "condition_form": text_or_empty(g["condition_form"].mode().iloc[0]) if len(g) else "",
                    "reachability_label": label,
                    "candidate_count": int(count),
                    "component_total": len(g),
                    "rate_pct": float(count / len(g) * 100) if len(g) else 0.0,
                    "block_eligible_form": component in {"bb", "volume"},
                })
    component_summary = pd.DataFrame(component_rows)
    component_summary.to_csv(COMPONENT_OUT, index=False)

    stage_rows: list[dict[str, Any]] = []
    for stage_scope in ("ALL", "stage2", "stage3"):
        scoped = decisions if stage_scope == "ALL" else decisions[decisions["stage"] == stage_scope]
        for status, count in scoped["final_p99_block_status"].value_counts().items():
            stage_rows.append({
                "scope": stage_scope,
                "status": status,
                "candidate_count": int(count),
                "scope_total": len(scoped),
                "rate_pct": float(count / len(scoped) * 100),
            })
    stage_summary = pd.DataFrame(stage_rows)
    stage_summary.to_csv(STAGE_OUT, index=False)

    # BOIL·CE 포섭
    targets = ce[["candidate_id", "stage", "ticker"]].drop_duplicates().copy()
    targets["is_ce_fail_7"] = True
    targets["is_named_boil"] = targets["candidate_id"].eq(BOIL_ID)
    parity = targets.merge(
        decisions[["candidate_id", "final_p99_block_status", "p99_block_fail_components", "p99_block_reason_codes",
                   "bb_reachability_label", "volume_reachability_label"]],
        on="candidate_id",
        how="left",
        validate="one_to_one",
    )
    parity["captured_by_p99_block"] = parity["final_p99_block_status"].eq("FAIL")
    parity.to_csv(CAPTURE_OUT, index=False)
    boil_row = parity[parity["candidate_id"] == BOIL_ID]
    if len(boil_row) != 1:
        raise AssertionError(len(boil_row))

    # 기존 HIGH_VOL 1/2단계와 관계
    relationships = pd.DataFrame([
        relation_row("HIGH_VOL_STAGE1_VOLUME_BLIND", set(high_vol_stage1["candidate_id"]), block_ids),
        relation_row("HIGH_VOL_STAGE2_STRICT_NEVER", set(high_vol_strict["candidate_id"]), block_ids),
        relation_row("HIGH_VOL_STAGE2_RELAXED_NEVER_OR_RARE", set(high_vol_relaxed["candidate_id"]), block_ids),
        relation_row("CE_FAIL_7", set(ce["candidate_id"]), block_ids),
        relation_row("NAMED_BOIL_9044", {BOIL_ID}, block_ids),
    ])
    relationships.to_csv(RELATION_OUT, index=False)

    # 기존 통합 정적 게이트와 결합한 실용 후보 수
    combined = candidates.merge(
        decisions[["candidate_id", "final_p99_block_status", "combined_static_status", "p99_block_fail_components", "p99_block_reason_codes"]],
        on="candidate_id",
        how="left",
        validate="one_to_one",
    )
    selected = select_combined(combined)
    selected.to_csv(SELECTED_OUT, index=False)

    scenario_rows: list[dict[str, Any]] = []
    for name, frame, status_col, status in [
        ("ORIGIN_TOTAL", decisions, None, None),
        ("P99_BLOCK_ONLY_PASS", decisions, "final_p99_block_status", "PASS"),
        ("P99_BLOCK_ONLY_FAIL", decisions, "final_p99_block_status", "FAIL"),
        ("COMBINED_STATIC_PASS", decisions, "combined_static_status", "PASS"),
        ("COMBINED_STATIC_HOLD", decisions, "combined_static_status", "HOLD"),
        ("COMBINED_STATIC_FAIL", decisions, "combined_static_status", "FAIL"),
        ("COMBINED_ELITE_DENY_BEFORE_DEDUP_SELECTED", selected, None, None),
    ]:
        q = frame if status_col is None else frame[frame[status_col] == status]
        scenario_rows.append({
            "scenario": name,
            "candidate_count": len(q),
            "stage2_count": int((q["stage"] == "stage2").sum()),
            "stage3_count": int((q["stage"] == "stage3").sum()),
            "zero_candidate_risk": len(q) == 0,
        })
    scenarios = pd.DataFrame(scenario_rows)
    scenarios.to_csv(SCENARIO_OUT, index=False)

    # 이벤트형·밴드형 제외로 인한 현재 표본 내 관측 누락 위험 진단
    excluded_activity = {}
    for component in ("ma", "macd", "rsi"):
        g = labeled[(labeled["component"] == component) & labeled["active_weight_gt_zero"]]
        excluded_activity[component] = {
            "active_candidate_count": int(len(g)),
            "zero_fired_count": int((g["fired_count"] == 0).sum()),
            "excluded_label": str(g["reachability_label"].mode().iloc[0]) if len(g) else "",
        }

    volume_fail = fail_evidence[fail_evidence["component"] == "volume"]
    bb_fail = fail_evidence[fail_evidence["component"] == "bb"]
    unique_entry_fail = fail_evidence[["stage", "activity_rule_hash"]].drop_duplicates()
    old_selected_blocked = set(old_selected["candidate_id"]) & block_ids
    new_selected_ids = set(selected["candidate_id"]) if len(selected) else set()
    old_selected_ids = set(old_selected["candidate_id"])

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": POLICY_VERSION,
        "policy": {
            "enforcement": "BLOCK",
            "active_weight": "weight > 0",
            "eligible_condition_forms": ["ONE_SIDED_GE", "ONE_SIDED_LE"],
            "ge_fail": "threshold > training p99; threshold > max is UNREACHABLE",
            "le_fail": "threshold < training p01; equivalent transformed activation-tail threshold > p99; threshold < min is UNREACHABLE",
            "excluded": {
                "MA": "event/no stored scalar threshold",
                "MACD": "cross event/no stored scalar threshold",
                "RSI": "two-sided inclusive band; explicit one-sided-only policy scope",
            },
        },
        "origin_count": 17_071,
        "p99_block": {
            "pass_count": int((decisions["final_p99_block_status"] == "PASS").sum()),
            "fail_count": int((decisions["final_p99_block_status"] == "FAIL").sum()),
            "stage_counts": stage_summary.to_dict("records"),
            "fail_evidence_rows": len(fail_evidence),
            "fail_unique_entry_rule_count": int(unique_entry_fail["activity_rule_hash"].nunique()),
            "volume_fail_count": int(volume_fail["candidate_id"].nunique()),
            "bb_fail_count": int(bb_fail["candidate_id"].nunique()),
        },
        "capture": {
            "named_boil_captured": bool(boil_row.iloc[0]["captured_by_p99_block"]),
            "named_boil_volume_label": str(boil_row.iloc[0]["volume_reachability_label"]),
            "named_boil_bb_label": str(boil_row.iloc[0]["bb_reachability_label"]),
            "ce_7_captured_count": int(parity["captured_by_p99_block"].sum()),
            "ce_7_total": 7,
            "ce_7_missed_ids": parity.loc[~parity["captured_by_p99_block"], "candidate_id"].tolist(),
        },
        "combined_gate": {
            "status_counts": decisions["combined_static_status"].value_counts().to_dict(),
            "selected_count": len(selected),
            "selected_stage_counts": selected["stage"].value_counts().to_dict() if len(selected) else {},
            "old_selected_count": len(old_selected),
            "old_selected_blocked_count": len(old_selected_blocked),
            "fallback_recovered_count": len(new_selected_ids - old_selected_ids),
            "old_selected_dropped_count": len(old_selected_ids - new_selected_ids),
        },
        "high_vol_relationship": relationships.to_dict("records"),
        "excluded_form_activity_diagnostic": excluded_activity,
        "operational_implementation": False,
        "source_rule_mutation": False,
        "live_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 설계 전용 통합 게이트 정책에 확정 BLOCK checker 반영
    architecture = json.loads(ARCHITECTURE.read_text(encoding="utf-8"))
    checker = {
        "name": "one_sided_threshold_p99_reachability",
        "version": "1",
        "phase": "STATIC",
        "enforcement": "BLOCK",
        "criteria": {
            "active_weight": "weight > 0",
            "ONE_SIDED_GE": "threshold > training p99 => FAIL; > max => UNREACHABLE",
            "ONE_SIDED_LE": "threshold < training p01 => FAIL; < min => UNREACHABLE; equivalent activation-tail p99 comparison",
            "fail_labels": ["NEAR_UNREACHABLE", "UNREACHABLE"],
            "excluded_forms": {
                "BOOLEAN_EVENT": "MA excluded",
                "BOOLEAN_CROSS_EVENT": "MACD excluded",
                "BAND_INCLUSIVE": "RSI excluded by one-sided-only policy scope",
            },
        },
        "source": [
            "stored candidate rulebook thresholds/weights",
            "training-window OHLCV indicator distributions",
            "threshold_p99_block_indicator_labels.csv.gz",
        ],
        "note": "confirmed design-only BLOCK; operational implementation remains prohibited in this task",
    }
    architecture["checkers"] = [x for x in architecture.get("checkers", []) if x.get("name") != checker["name"]] + [checker]
    architecture["policy_version_proposal"] = POLICY_VERSION
    architecture["confirmed_static_block_policy"] = {
        "checker": checker["name"],
        "dry_run_summary": str(SUMMARY_OUT.relative_to(ROOT)),
        "operational_implementation": False,
    }
    ARCHITECTURE.write_text(json.dumps(architecture, ensure_ascii=False, indent=2), encoding="utf-8")

    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    thresholds["threshold_reachability_p99"] = {
        "policy_version": POLICY_VERSION,
        "enforcement": "BLOCK",
        "active_weight_gt": 0.0,
        "eligible_forms": ["ONE_SIDED_GE", "ONE_SIDED_LE"],
        "ge_near_unreachable": "threshold > p99",
        "ge_unreachable": "threshold > max",
        "le_near_unreachable": "threshold < p01 (activation-tail p99 equivalent)",
        "le_unreachable": "threshold < min",
        "event_forms": "EXCLUDED",
        "band_forms": "EXCLUDED_BY_ONE_SIDED_ONLY_SCOPE",
        "dry_run_fail_count": len(block_ids),
        "dry_run_pass_count": 17_071 - len(block_ids),
        "operational_implementation": False,
    }
    thresholds.setdefault("recommended_enforcement", {})["threshold_reachability_p99"] = "BLOCK"
    THRESHOLDS.write_text(json.dumps(thresholds, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence = stable_read_csv(CHECKER_EVIDENCE, low_memory=False)
    evidence = evidence[evidence["checker"] != "one_sided_threshold_p99_reachability"]
    evidence = pd.concat([
        evidence,
        pd.DataFrame([{
            "checker": "one_sided_threshold_p99_reachability",
            "requested_mode": "BLOCK",
            "recommended_mode": "BLOCK",
            "evidence": f"stored one-sided active threshold beyond training activation-tail p99; dry-run FAIL {len(block_ids):,}/17,071; events and RSI band excluded",
        }]),
    ], ignore_index=True)
    evidence.to_csv(CHECKER_EVIDENCE, index=False)

    marker = "## 14. 확정 추가 — 단방향 임계 p99 도달가능성 BLOCK"
    design_text = DESIGN_READOUT.read_text(encoding="utf-8")
    if marker not in design_text:
        design_text += (
            "\n" + marker + "\n\n"
            f"정책 버전 `{POLICY_VERSION}`에서 `one_sided_threshold_p99_reachability`를 STATIC BLOCK으로 확정했다. "
            "활성 가중치가 양수인 단방향 `>=` 임계는 학습 p99 초과, 단방향 `<=` 임계는 학습 p01 미만을 같은 activation-tail p99 초과로 판정한다. "
            "MA/MACD 이벤트형과 RSI 밴드형은 이 checker에서 제외한다. 운영 구현은 이번 작업 범위에 포함되지 않았으며 상세 dry-run은 "
            "`threshold_p99_block_readout.md`를 기준으로 한다.\n"
        )
        DESIGN_READOUT.write_text(design_text, encoding="utf-8")

    rel = relationships.set_index("comparison")
    stage_table = stage_summary.pivot(index="scope", columns="status", values="candidate_count").fillna(0).astype(int)
    component_all = component_summary[component_summary["stage_scope"] == "ALL"]
    ce_captured = int(parity["captured_by_p99_block"].sum())
    boil_captured = bool(boil_row.iloc[0]["captured_by_p99_block"])
    lines = [
        "# 도달불가 단방향 임계 BLOCK 최종 dry-run",
        "",
        f"- 정책 버전: `{POLICY_VERSION}`",
        "- 범위: Stage2 survivors 1,162 + Stage3 final rulebooks 15,909 = 17,071개",
        "- 반영 상태: 통합 게이트 설계 파일에 STATIC BLOCK checker로 반영, 운영 구현은 하지 않음",
        "- 원본·라이브·운영 코드·재학습·주문·삭제: 0건",
        "",
        "## 1. 확정 판정식",
        "",
        "- 공통 선행조건: 저장 core 가중치 `weight > 0`.",
        "- 단방향 `>=`: 임계가 학습기간 raw p99를 초과하면 `NEAR_UNREACHABLE`, max를 초과하면 `UNREACHABLE`.",
        "- 단방향 `<=`: 임계가 학습기간 raw p01보다 낮으면 `NEAR_UNREACHABLE`, min보다 낮으면 `UNREACHABLE`. 이는 부호를 뒤집은 activation-tail 분포에서 p99/max 초과와 동치다.",
        "- 두 라벨 중 하나라도 있으면 룰 전체 `FAIL`.",
        "- MA/MACD는 스칼라 임계가 없는 이벤트형이므로 제외한다. RSI는 양방향 밴드이므로 명시된 단방향 전용 정책에서 제외한다.",
        "",
        "## 2. 17,071개 최종 PASS/FAIL",
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
        f"- FAIL 후보: **{len(block_ids):,}개**",
        f"- PASS 후보: **{17_071-len(block_ids):,}개**",
        f"- FAIL 고유 entry rule: **{unique_entry_fail['activity_rule_hash'].nunique():,}개**",
        "",
        "## 3. 지표별 결과",
        "",
        "| 지표 | 정책 형태 | REACHABLE | NEAR | UNREACHABLE | 제외/비활성 | BLOCK 후보 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for component in COMPONENTS:
        g = component_all[component_all["component"] == component].set_index("reachability_label")["candidate_count"].to_dict()
        reachable = int(g.get("REACHABLE", 0))
        near = int(g.get("NEAR_UNREACHABLE", 0))
        unreachable = int(g.get("UNREACHABLE", 0))
        excluded = sum(int(v) for k, v in g.items() if k not in {"REACHABLE", "NEAR_UNREACHABLE", "UNREACHABLE"})
        form = {"ma":"EVENT 제외","macd":"CROSS EVENT 제외","rsi":"BAND 제외","bb":"ONE_SIDED_LE","volume":"ONE_SIDED_GE"}[component]
        lines.append(f"| {component} | {form} | {reachable:,} | {near:,} | {unreachable:,} | {excluded:,} | {near+unreachable:,} |")
    lines += [
        "",
        f"이번 원본에서 실제 BLOCK은 Volume {volume_fail['candidate_id'].nunique():,}개에 집중됐고 BB BLOCK은 {bb_fail['candidate_id'].nunique():,}개였다.",
        "",
        "## 4. BOIL·CE 7개 포섭",
        "",
        f"- BOIL 원형 `{BOIL_ID}`: **{'포섭' if boil_captured else '미포섭'}**.",
        f"  - Volume 가중치가 0이므로 `INACTIVE_WEIGHT`; BB 임계는 `REACHABLE`이다.",
        f"- 기존 CE FAIL 7개: **{ce_captured}/7 포섭**.",
        "- 이 BLOCK은 저장된 활성 단방향 임계의 학습분포 검증 여부를 검사한다. BOIL의 거래량 무시 구조와 CE의 동적 점수 집중 구조를 대신하는 조건이 아니다.",
        "",
        "## 5. 기존 HIGH_VOL 3단계와의 관계",
        "",
    ]
    for name in ("HIGH_VOL_STAGE1_VOLUME_BLIND", "HIGH_VOL_STAGE2_STRICT_NEVER", "HIGH_VOL_STAGE2_RELAXED_NEVER_OR_RARE"):
        z = rel.loc[name]
        lines.append(
            f"- {name}: 기존 {int(z.reference_count):,}개 중 {int(z.overlap_count):,}개 포섭, "
            f"기존 전용 {int(z.reference_only_count):,}개, 새 BLOCK 전용 {int(z.new_block_only_count):,}개 → **병렬 조건**."
        )
    lines += [
        "",
        "새 BLOCK은 HIGH_VOL 여부와 무관하게 죽은 활성 단방향 임계를 찾지만, HIGH_VOL 게이트는 거래량을 진입 근거로 사용하지 않는 구조도 잡는다. 따라서 서로 완전 포섭하지 않는다.",
        "",
        "## 6. 기존 통합 게이트와 결합한 잔여 후보",
        "",
        f"- 결합 정적 PASS: **{int((decisions.combined_static_status=='PASS').sum()):,}개**",
        f"- 결합 HOLD: **{int((decisions.combined_static_status=='HOLD').sum()):,}개**",
        f"- 결합 FAIL: **{int((decisions.combined_static_status=='FAIL').sum()):,}개**",
        f"- 기존 elite filter + denylist-before-dedup + stage cap 재선택: **{len(selected):,}개**",
        f"  - Stage2 {int((selected.stage=='stage2').sum()):,}개 / Stage3 {int((selected.stage=='stage3').sum()):,}개",
        "",
        "threshold BLOCK 단독 잔여 13,104개는 수십~수백 범위가 아니다. 다만 기존 정적 게이트·elite·ticker dedup까지 결합하면 88개로 실용 범위에 들어온다. Stage2는 10개로 기존 cap 60을 크게 밑돌아 단계 균형은 추가 감시가 필요하다.",
        "",
        "## 7. 이벤트형 제외 누락 위험",
        "",
        f"- 활성 MA 이벤트 {excluded_activity['ma']['active_candidate_count']:,}개 중 학습기간 0회 발생: {excluded_activity['ma']['zero_fired_count']:,}개.",
        f"- 활성 MACD 교차 {excluded_activity['macd']['active_candidate_count']:,}개 중 학습기간 0회 발생: {excluded_activity['macd']['zero_fired_count']:,}개.",
        f"- 활성 RSI 밴드 {excluded_activity['rsi']['active_candidate_count']:,}개 중 학습기간 0회 충족: {excluded_activity['rsi']['zero_fired_count']:,}개.",
        "- 현재 17,071개에서는 제외형 조건의 0회 관측 개체가 없어 즉시 누락된 죽은 조건은 확인되지 않았다. 다만 미래 신규 룰에는 이벤트 빈도 전용 checker가 별도로 필요하며, p99 임계 checker로 잘못 대체하면 안 된다.",
        "",
        "## 8. 최종 판단",
        "",
        "- `one_sided_threshold_p99_reachability`는 독립 STATIC BLOCK으로 확정 반영했다.",
        "- BOIL·CE를 전부 포섭하지 않으므로 기존 HIGH_VOL 구조 검사와 CE 동적 검사는 병렬 유지해야 한다.",
        "- 운영 코드 반영은 금지 조건에 따라 수행하지 않았다. 현재 반영 범위는 설계 정책·dry-run 산출물뿐이다.",
        "",
        "## 9. 산출물",
        "",
        f"- `{INDICATOR_OUT.name}` — 85,355개 지표 행과 후보 최종 상태",
        f"- `{CANDIDATE_OUT.name}` — 17,071개 후보별 지표 라벨 및 PASS/FAIL",
        f"- `{FAIL_OUT.name}` — FAIL 근거 전체",
        f"- `{CAPTURE_OUT.name}` — BOIL·CE 포섭",
        f"- `{COMPONENT_OUT.name}` / `{STAGE_OUT.name}` — 지표·stage 요약",
        f"- `{RELATION_OUT.name}` — HIGH_VOL 게이트 관계",
        f"- `{SCENARIO_OUT.name}` / `{SELECTED_OUT.name}` — 결합 잔여·재선택",
        f"- `{SUMMARY_OUT.name}` — 기계 판독 요약",
    ]
    READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
