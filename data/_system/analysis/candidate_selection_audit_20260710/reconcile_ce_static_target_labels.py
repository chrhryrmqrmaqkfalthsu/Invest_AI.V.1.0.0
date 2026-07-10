from __future__ import annotations

"""최종 summary의 타깃 경계를 target/matrix 산출물에 재적용해 일관성을 맞춘다."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
TARGET = OUT / "ce_static_target_labels.csv.gz"
MATRIX = OUT / "ce_static_feature_matrix.csv.gz"
SUMMARY = OUT / "ce_static_predictor_summary.json"


def apply(frame: pd.DataFrame, thresholds: dict[str, dict[str, float]]) -> pd.DataFrame:
    result = frame.copy()
    result["is_oos_gap_pp"] = result["base_avg_pnl_pct"] - result["eval_avg_pnl_pct"]
    result["oos_to_is_pnl_ratio"] = result["eval_avg_pnl_pct"] / result["base_avg_pnl_pct"].replace(0, np.nan)
    collapse = []
    tail = []
    high_win = []
    for row in result.itertuples(index=False):
        t = thresholds[str(row.stage)]
        collapse.append(
            bool(
                row.base_avg_pnl_pct > 0
                and row.is_oos_gap_pp >= t["collapse_gap_q90_pp"]
                and row.oos_to_is_pnl_ratio <= 0.50
            )
        )
        tail.append(bool(row.eval_avg_pnl_pct > 0 and row.eval_worst_mae_pct <= t["tail_worst_mae_q10_pct"]))
        high_win.append(
            bool(
                row.eval_avg_pnl_pct > 0
                and row.eval_win_rate_pct >= t["high_win_q75_pct"]
                and row.eval_worst_to_median_win >= t["worst_to_median_win_q75"]
                and row.eval_top3_loss_share >= t["top3_loss_share_q50"]
            )
        )
    result["target_is_oos_collapse"] = collapse
    result["target_positive_tail_risk"] = tail
    result["target_high_win_large_loss"] = high_win
    result["target_bad"] = result[[
        "target_is_oos_collapse", "target_positive_tail_risk", "target_high_win_large_loss"
    ]].any(axis=1)
    result["target_reason"] = result.apply(
        lambda row: "|".join(
            name for name, flag in (
                ("IS_OOS_COLLAPSE", row.target_is_oos_collapse),
                ("POSITIVE_MEAN_EXTREME_TAIL", row.target_positive_tail_risk),
                ("HIGH_WIN_LARGE_LOSS", row.target_high_win_large_loss),
            ) if flag
        ),
        axis=1,
    )
    return result


def main() -> int:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    thresholds = summary["target_definition"]
    target = apply(pd.read_csv(TARGET, low_memory=False), thresholds)
    matrix = apply(pd.read_csv(MATRIX, low_memory=False), thresholds)
    target.to_csv(TARGET, index=False, compression="gzip")
    matrix.to_csv(MATRIX, index=False, compression="gzip")
    print({
        "target_discovery_bad": int(target.loc[target["evaluation_split"].eq("INTERNAL_DISCOVERY") & target["history_eligible"].fillna(False).astype(bool), "target_bad"].sum()),
        "target_validation_bad": int(target.loc[target["evaluation_split"].eq("FROZEN_OOS_VALIDATION") & target["history_eligible"].fillna(False).astype(bool), "target_bad"].sum()),
        "matrix_discovery_bad": int(matrix.loc[matrix["matrix_split"].eq("INTERNAL_DISCOVERY"), "target_bad"].sum()),
        "matrix_validation_bad": int(matrix.loc[matrix["matrix_split"].eq("FROZEN_OOS_VALIDATION"), "target_bad"].sum()),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
