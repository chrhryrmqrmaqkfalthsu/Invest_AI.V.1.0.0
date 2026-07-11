"""Static candidate gate checkers for live shadow/block enforcement.

The implementation consumes the frozen validation catalogs that established
v3 reachability and final BOIL-exclusive decisions. It does not invent new
thresholds or recompute research labels with a different data window.
"""
from __future__ import annotations

import csv
import fcntl
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
V3_SOURCE = PROJECT_ROOT / "data/_system/analysis/candidate_selection_audit_20260710/threshold_p99_weightless_block_candidate_decisions.csv"
BOIL_SOURCE = PROJECT_ROOT / "data/_system/analysis/candidate_selection_audit_20260710/boil_block_exclusive_targets.csv"
BOIL_DECISION_SOURCE = PROJECT_ROOT / "data/_system/analysis/candidate_selection_audit_20260710/boil_block_enforcement_decision.json"
SHADOW_LOG_DIR = PROJECT_ROOT / "data/_system/analysis/boil_v3_shadow"
POLICY_VERSION = "integrated-gate-v3-boil-20260710"
V3_POLICY_VERSION = "integrated-gate-v3-p99-reachability-block-weightless"
BOIL_POLICY_VERSION = "high-vol-volume-blind-near-zero-v3-exclusive"
VALID_ENFORCEMENTS = {"SHADOW", "BLOCK"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
        if out != out or out in {float("inf"), float("-inf")}:
            return default
        return out
    except Exception:
        return default


def _policy_value(key: str, default: str) -> str:
    try:
        from engine.core.config import config

        raw = str(config.get(key, default) or default).strip().upper()
    except Exception:
        return default
    return raw if raw in VALID_ENFORCEMENTS else default


def integrated_gate_enforcement() -> str:
    """Live candidate-slot hook enforcement, defaulting safely to SHADOW."""
    return _policy_value("live.integrated_gate_enforcement", "SHADOW")


def upstream_gate_enforcement() -> str:
    """Elite report upstream gate enforcement, defaulting safely to SHADOW."""
    return _policy_value("live.upstream_gate_enforcement", "SHADOW")


@dataclass(frozen=True)
class GateCheckResult:
    checker: str
    status: str
    reasons: tuple[str, ...]
    evidence: dict[str, Any]
    policy_version: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["reasons"] = list(self.reasons)
        return row


@dataclass(frozen=True)
class CandidateGateDecision:
    candidate_id: str
    ticker: str
    enforcement: str
    aggregate_status: str
    should_block: bool
    policy_version: str
    checked_at: str
    checks: tuple[GateCheckResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "ticker": self.ticker,
            "enforcement": self.enforcement,
            "aggregate_status": self.aggregate_status,
            "should_block": self.should_block,
            "policy_version": self.policy_version,
            "checked_at": self.checked_at,
            "checks": [row.to_dict() for row in self.checks],
        }


class CandidateGateChecker:
    """Evaluate frozen v3 and final BOIL-exclusive policies."""

    def __init__(
        self,
        v3_source: Path = V3_SOURCE,
        boil_source: Path = BOIL_SOURCE,
        boil_decision_source: Path = BOIL_DECISION_SOURCE,
    ):
        self.v3_source = Path(v3_source)
        self.boil_source = Path(boil_source)
        self.boil_decision_source = Path(boil_decision_source)
        self._v3_rows = self._load_catalog(self.v3_source)
        self._boil_rows = self._load_catalog(self.boil_source)
        self._boil_decision = self._load_json(self.boil_decision_source)

    @staticmethod
    def _load_catalog(path: Path) -> dict[str, dict[str, str]]:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {
                str(row.get("candidate_id") or ""): dict(row)
                for row in csv.DictReader(handle)
                if row.get("candidate_id")
            }

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def check_v3(self, candidate_id: str) -> GateCheckResult:
        row = self._v3_rows.get(candidate_id)
        if row is None:
            return GateCheckResult(
                checker="v3_one_sided_threshold_reachability",
                status="HOLD",
                reasons=("V3_CATALOG_ROW_MISSING",),
                evidence={"candidate_id": candidate_id},
                policy_version=V3_POLICY_VERSION,
                source=str(self.v3_source),
            )
        status = str(row.get("final_p99_weightless_block_status") or "HOLD").upper()
        if status not in {"PASS", "FAIL", "HOLD"}:
            status = "HOLD"
        reasons = tuple(
            part for part in str(row.get("p99_weightless_reason_codes") or "").split("|") if part
        )
        if not reasons:
            reasons = ("V3_REACHABILITY_PASS",) if status == "PASS" else ("V3_STATUS_UNRESOLVED",)
        return GateCheckResult(
            checker="v3_one_sided_threshold_reachability",
            status=status,
            reasons=reasons,
            evidence={
                "candidate_id": candidate_id,
                "fail_components": str(row.get("p99_weightless_fail_components") or ""),
                "bb_reachability_label": str(row.get("bb_reachability_label") or ""),
                "volume_reachability_label": str(row.get("volume_reachability_label") or ""),
                "volume_weight": _as_float(row.get("volume_weight")),
                "source_policy_version": str(row.get("policy_version") or ""),
            },
            policy_version=V3_POLICY_VERSION,
            source=str(self.v3_source),
        )

    def check_boil(self, candidate_id: str, v3_result: GateCheckResult) -> GateCheckResult:
        row = self._boil_rows.get(candidate_id)
        if row is None:
            return GateCheckResult(
                checker="boil_high_vol_volume_blind",
                status="PASS",
                reasons=("NOT_IN_FINAL_BOIL_EXCLUSIVE_TARGETS",),
                evidence={
                    "candidate_id": candidate_id,
                    "v3_status": v3_result.status,
                    "exclusive_membership": False,
                    "decision": str(self._boil_decision.get("decision") or ""),
                    "definition": self._boil_decision.get("definition") or {},
                },
                policy_version=BOIL_POLICY_VERSION,
                source=str(self.boil_source),
            )
        return GateCheckResult(
            checker="boil_high_vol_volume_blind",
            status="FAIL",
            reasons=(str(row.get("block_reason") or "HIGH_VOL_VOLUME_BLIND_AND_ABS_WEIGHT_VOLUME_SURGE_LTE_0_05_AND_V3_PASS"),),
            evidence={
                "candidate_id": candidate_id,
                "exclusive_membership": True,
                "vol_group": str(row.get("vol_group_final") or "").upper(),
                "nonvolume_entry_possible_market_cap": _as_bool(row.get("nonvolume_entry_possible_market_cap")),
                "weight_volume_surge": _as_float(row.get("weight_volume_surge")),
                "near_zero": _as_bool(row.get("near_zero")),
                "v3_overlap_excluded": _as_bool(row.get("v3_overlap_excluded")),
                "v3_status": v3_result.status,
                "decision": str(self._boil_decision.get("decision") or ""),
            },
            policy_version=BOIL_POLICY_VERSION,
            source=str(self.boil_source),
        )

    def evaluate(self, candidate: dict[str, Any], enforcement: str | None = None) -> CandidateGateDecision:
        candidate_id = str(
            candidate.get("candidate_id")
            or f"{candidate.get('stage')}:{candidate.get('ticker')}:{candidate.get('rulebook_hash_short')}"
        )
        ticker = str(candidate.get("ticker") or "").upper()
        mode = str(enforcement or integrated_gate_enforcement()).upper()
        if mode not in VALID_ENFORCEMENTS:
            mode = "SHADOW"
        v3 = self.check_v3(candidate_id)
        boil = self.check_boil(candidate_id, v3)
        statuses = {v3.status, boil.status}
        if "FAIL" in statuses:
            aggregate = "FAIL"
        elif "HOLD" in statuses:
            aggregate = "HOLD"
        else:
            aggregate = "PASS"
        return CandidateGateDecision(
            candidate_id=candidate_id,
            ticker=ticker,
            enforcement=mode,
            aggregate_status=aggregate,
            should_block=mode == "BLOCK" and aggregate in {"FAIL", "HOLD"},
            policy_version=POLICY_VERSION,
            checked_at=utc_now(),
            checks=(v3, boil),
        )

    def evaluate_many(
        self, candidates: Iterable[dict[str, Any]], enforcement: str | None = None
    ) -> list[CandidateGateDecision]:
        return [self.evaluate(candidate, enforcement=enforcement) for candidate in candidates]


def append_candidate_gate_log(
    decision: CandidateGateDecision,
    *,
    path: str,
    candidate_snapshot: dict[str, Any] | None = None,
) -> Path | None:
    """Append a gate decision. Failure never changes candidate evaluation."""
    try:
        SHADOW_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc)
        log_path = SHADOW_LOG_DIR / f"candidate_gate_{timestamp:%Y%m%d}.jsonl"
        payload = decision.to_dict()
        payload["path"] = path
        payload["candidate_snapshot"] = dict(candidate_snapshot or {})
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        with log_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.write(line)
                handle.flush()
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return log_path
    except Exception:
        return None
