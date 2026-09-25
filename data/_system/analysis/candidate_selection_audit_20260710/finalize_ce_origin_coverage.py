from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
AUDIT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
FULL = AUDIT / "ce_origin_full_calculation.csv"
UNJUDGED = AUDIT / "ce_origin_residual_unjudged.csv"
FAILS = AUDIT / "ce_origin_fail_rejudged.csv"
PARITY = AUDIT / "ce_origin_live93_parity.csv"
SUMMARY = AUDIT / "ce_origin_coverage_summary.json"


def main() -> int:
    frame = pd.read_csv(FULL, low_memory=False)
    frame["static_core_weight_sum_to_threshold"] = (
        frame["static_positive_core_weight_sum"] / frame["signal_threshold"].where(frame["signal_threshold"].gt(0))
    )
    frame["static_ratio_metric_is_ce_equivalent"] = False
    frame["exact_ratio_calculable"] = frame["ratio_exact"].notna()
    frame["exact_top2_calculable"] = frame["top2_exact_pct"].notna()
    frame["dynamic_inputs_required"] = (
        "ASOF_OHLCV_INDICATOR_ACTIVATION|MARKET_SCORE|SECTOR_SCORE|VIX|NEWS_SENTIMENT|TOPIC_FEATURES|EVENT_FLAGS"
    )
    frame.to_csv(FULL, index=False)
    frame[frame["ce_status"].astype(str).str.startswith("UNJUDGED")].to_csv(UNJUDGED, index=False)
    frame[frame["ce_status"].eq("FAIL")].to_csv(FAILS, index=False)

    parity = pd.read_csv(PARITY)
    finite = parity["static_vs_realized_top2_abs_diff"].dropna()
    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    payload["full_table_exact_ratio_count"] = int(frame["exact_ratio_calculable"].sum())
    payload["full_table_exact_top2_count"] = int(frame["exact_top2_calculable"].sum())
    payload["full_table_static_threshold_count"] = int(frame["signal_threshold"].notna().sum())
    payload["full_table_static_weight_top2_count"] = int(frame["static_weight_top2_share_pct"].notna().sum())
    payload["static_proxy_warning"] = (
        "static_core_weight_sum_to_threshold and static_weight_top2_share_pct use all configured weights; "
        "they are not CE ratio/top2 because realized indicator activation and dynamic context are absent"
    )
    payload["static_vs_realized_top2_mean_abs_diff_pctp"] = float(finite.mean())
    payload["static_vs_realized_top2_min_abs_diff_pctp"] = float(finite.min())
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print({
        "rows": len(frame),
        "exact_ratio": int(frame["exact_ratio_calculable"].sum()),
        "exact_top2": int(frame["exact_top2_calculable"].sum()),
        "residual_unjudged": int(frame["ce_status"].astype(str).str.startswith("UNJUDGED").sum()),
        "static_proxy_top2_mean_abs_diff": float(finite.mean()),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
