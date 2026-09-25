from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
AUDIT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
FULL = AUDIT / "ce_origin_full_calculation.csv"
PARITY = AUDIT / "ce_origin_live93_parity.csv"
SUMMARY = AUDIT / "ce_origin_coverage_summary.json"


def main() -> int:
    full = pd.read_csv(FULL, low_memory=False)[
        ["candidate_id", "static_core_weight_sum_to_threshold"]
    ]
    parity = pd.read_csv(PARITY).merge(full, on="candidate_id", how="left", validate="one_to_one")
    parity["static_vs_realized_ratio_abs_diff"] = (
        parity["static_core_weight_sum_to_threshold"] - parity["ratio_recomputed"]
    ).abs()
    parity["static_ratio_exact_match"] = parity["static_vs_realized_ratio_abs_diff"].le(1e-12)
    parity.to_csv(PARITY, index=False)

    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    ratio_diff = parity["static_vs_realized_ratio_abs_diff"].dropna()
    payload["static_vs_realized_ratio_mean_abs_diff"] = float(ratio_diff.mean())
    payload["static_vs_realized_ratio_min_abs_diff"] = float(ratio_diff.min())
    payload["static_ratio_exact_match_count"] = int(parity["static_ratio_exact_match"].sum())
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print({
        "rows": len(parity),
        "mean_abs_diff": float(ratio_diff.mean()),
        "min_abs_diff": float(ratio_diff.min()),
        "exact_matches": int(parity["static_ratio_exact_match"].sum()),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
