#!/usr/bin/env python3
"""Run the AAP/POWI hybrid test with balanced group-threshold bounds.

The prior two-symbol runner is reused unchanged for feature construction,
labels, validation gates, candidate selection, rolling target-date exits and
strict-AND comparison.  This entrypoint injects only the floored grouped GA:
G1/G2 thresholds 2..3 and G3/G4 thresholds exactly 2.
"""
from __future__ import annotations

import json
import math
import runpy
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class _NoopLogger:
    def debug(self, *args: Any, **kwargs: Any) -> None:
        return None

    def info(self, *args: Any, **kwargs: Any) -> None:
        return None

    def warning(self, *args: Any, **kwargs: Any) -> None:
        return None

    def error(self, *args: Any, **kwargs: Any) -> None:
        return None

    def success(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bind(self, *args: Any, **kwargs: Any) -> "_NoopLogger":
        return self


logger_stub = types.ModuleType("engine.core.logger")
logger_stub.get_logger = lambda name="": _NoopLogger()
logger_stub.trade_logger = lambda: _NoopLogger()
sys.modules["engine.core.logger"] = logger_stub

HERE = Path(__file__).resolve().parent
ISOLATED_ROOT = HERE.parents[1]
KINGMAKER_ROOT = HERE.parents[5]
if str(ISOLATED_ROOT) not in sys.path:
    sys.path.insert(0, str(ISOLATED_ROOT))

from engine.learning import grouped_genetic_floored as floored_ga

BASE_RUNNER = HERE / "run_hybrid_group_test_2sym.py"
OUT_DIR = KINGMAKER_ROOT / "data/_system/analysis/hybrid_group_test_2sym_floored_20260712"
PRIOR_DIR = KINGMAKER_ROOT / "data/_system/analysis/hybrid_group_test_2sym_20260712"
STRICT_DETAIL_DIR = KINGMAKER_ROOT / "data/_system/analysis/pilot_survivor_detail_20260712"
TICKERS = ["AAP", "POWI"]


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _parse_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def _apply_threshold_audit() -> None:
    threshold_path = OUT_DIR / "group_threshold_check.csv"
    thresholds = pd.read_csv(threshold_path)
    thresholds["threshold_min_allowed"] = 2
    thresholds["threshold_max_allowed"] = thresholds["group_size"].astype(int) - 1
    thresholds["threshold_floor_valid"] = (
        thresholds["learned_threshold"].astype(int)
        >= thresholds["threshold_min_allowed"].astype(int)
    ) & (
        thresholds["learned_threshold"].astype(int)
        <= thresholds["threshold_max_allowed"].astype(int)
    )
    thresholds["full_group_threshold_forbidden"] = (
        thresholds["learned_threshold"].astype(int)
        == thresholds["group_size"].astype(int)
    )
    thresholds.to_csv(threshold_path, index=False)

    learned_path = OUT_DIR / "learned_genes.csv"
    learned = pd.read_csv(learned_path)
    is_threshold = learned["gene_type"].eq("GROUP_THRESHOLD")
    learned["threshold_min_allowed"] = np.where(is_threshold, 2, np.nan)
    learned["threshold_max_allowed"] = np.where(
        is_threshold, learned["group_size"].astype(float) - 1.0, np.nan
    )
    learned["threshold_floor_valid"] = np.where(
        is_threshold,
        (
            learned["group_threshold"].astype(float)
            >= learned["threshold_min_allowed"].astype(float)
        )
        & (
            learned["group_threshold"].astype(float)
            <= learned["threshold_max_allowed"].astype(float)
        ),
        np.nan,
    )
    learned.to_csv(learned_path, index=False)

    training_path = OUT_DIR / "training_log.csv"
    training = pd.read_csv(training_path)
    group_sizes = np.array([4, 4, 3, 3], dtype=int)
    floor_valid: list[bool] = []
    for raw in training["best_group_thresholds"]:
        values = np.asarray(json.loads(raw), dtype=int)
        floor_valid.append(
            bool(
                len(values) == 4
                and np.all(values >= 2)
                and np.all(values <= group_sizes - 1)
            )
        )
    training["threshold_range_spec"] = "G1=2..3|G2=2..3|G3=2..2|G4=2..2"
    training["threshold_floor_valid"] = floor_valid
    training.to_csv(training_path, index=False)

    survivor_path = OUT_DIR / "survivor_summary.csv"
    survivors = pd.read_csv(survivor_path)
    survivor_floor_valid: list[bool] = []
    for raw in survivors["group_thresholds_json"]:
        values_by_group = json.loads(raw)
        values = np.array(
            [
                values_by_group["G1_PULLBACK"],
                values_by_group["G2_VOLATILITY"],
                values_by_group["G3_RANGE_EXPANSION"],
                values_by_group["G4_VOLUME_CONFIRMATION"],
            ],
            dtype=int,
        )
        survivor_floor_valid.append(
            bool(np.all(values >= 2) and np.all(values <= group_sizes - 1))
        )
    survivors["threshold_range_spec"] = "G1=2..3|G2=2..3|G3=2..2|G4=2..2"
    survivors["threshold_floor_valid"] = survivor_floor_valid
    survivors["full_group_molbbang_present"] = False
    survivors.to_csv(survivor_path, index=False)


def _rename_trade_outputs() -> None:
    mappings = {
        "aap_trades_hybrid.csv": "aap_trades_floored.csv",
        "powi_trades_hybrid.csv": "powi_trades_floored.csv",
        "comparison_vs_strict_and.csv": "floored_vs_strict_internal.csv",
    }
    for old_name, new_name in mappings.items():
        old_path = OUT_DIR / old_name
        new_path = OUT_DIR / new_name
        if new_path.exists():
            new_path.unlink()
        old_path.replace(new_path)


def _trade_frame(method: str, ticker: str) -> pd.DataFrame:
    ticker_lower = ticker.lower()
    if method == "STRICT_AND_12_BASELINE":
        return pd.read_csv(STRICT_DETAIL_DIR / f"{ticker_lower}_trades.csv")
    if method == "HYBRID_GROUP_COUNT_AND_UNFLOORED":
        return pd.read_csv(PRIOR_DIR / f"{ticker_lower}_trades_hybrid.csv")
    if method == "HYBRID_GROUP_COUNT_AND_FLOORED":
        return pd.read_csv(OUT_DIR / f"{ticker_lower}_trades_floored.csv")
    raise ValueError(method)


def _trade_diagnostics(method: str, ticker: str) -> dict[str, Any]:
    frame = _trade_frame(method, ticker)
    extended = frame["holding_sessions"].astype(int) > 2
    losses = frame["net_return_pct"].astype(float) < 0.0
    large_losses = frame["net_return_pct"].astype(float) <= -5.0
    extended_frame = frame[extended]
    extended_losses = frame[extended & losses]
    return {
        "extended_trade_count": int(extended.sum()),
        "extended_loss_count": int((extended & losses).sum()),
        "extended_loss_le_minus5_count": int((extended & large_losses).sum()),
        "worst_extended_return_pct": (
            float(extended_frame["net_return_pct"].min())
            if len(extended_frame)
            else np.nan
        ),
        "worst_extended_loss_pct": (
            float(extended_losses["net_return_pct"].min())
            if len(extended_losses)
            else np.nan
        ),
        "max_holding_sessions_observed": int(frame["holding_sessions"].max())
        if len(frame)
        else 0,
        "total_target_extensions": int(frame["target_extension_count"].sum())
        if "target_extension_count" in frame
        else 0,
    }


def _pooled_trade_diagnostics(method: str) -> dict[str, Any]:
    frames = [_trade_frame(method, ticker) for ticker in TICKERS]
    frame = pd.concat(frames, ignore_index=True)
    extended = frame["holding_sessions"].astype(int) > 2
    losses = frame["net_return_pct"].astype(float) < 0.0
    large_losses = frame["net_return_pct"].astype(float) <= -5.0
    extended_frame = frame[extended]
    extended_losses = frame[extended & losses]
    return {
        "extended_trade_count": int(extended.sum()),
        "extended_loss_count": int((extended & losses).sum()),
        "extended_loss_le_minus5_count": int((extended & large_losses).sum()),
        "worst_extended_return_pct": (
            float(extended_frame["net_return_pct"].min())
            if len(extended_frame)
            else np.nan
        ),
        "worst_extended_loss_pct": (
            float(extended_losses["net_return_pct"].min())
            if len(extended_losses)
            else np.nan
        ),
        "max_holding_sessions_observed": int(frame["holding_sessions"].max())
        if len(frame)
        else 0,
        "total_target_extensions": int(frame["target_extension_count"].sum())
        if "target_extension_count" in frame
        else 0,
    }


def _build_three_way(summary: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    prior = pd.read_csv(PRIOR_DIR / "comparison_vs_strict_and.csv")
    current = pd.read_csv(OUT_DIR / "floored_vs_strict_internal.csv")

    strict = prior[prior["method"].eq("STRICT_AND_12_BASELINE")].copy()
    unfloored = prior[prior["method"].eq("HYBRID_GROUP_COUNT_AND")].copy()
    unfloored["method"] = "HYBRID_GROUP_COUNT_AND_UNFLOORED"
    floored = current[current["method"].eq("HYBRID_GROUP_COUNT_AND")].copy()
    floored["method"] = "HYBRID_GROUP_COUNT_AND_FLOORED"

    survivor_candidate_counts = {
        "STRICT_AND_12_BASELINE": 2,
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": 0,
        "HYBRID_GROUP_COUNT_AND_FLOORED": int(summary["survivor_candidate_count"]),
    }
    survivor_ticker_counts = {
        "STRICT_AND_12_BASELINE": 2,
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": 0,
        "HYBRID_GROUP_COUNT_AND_FLOORED": len(summary["survivor_tickers"]),
    }
    threshold_constraints = {
        "STRICT_AND_12_BASELINE": "N/A: 12-feature strict AND",
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": "G1/G2=1..4; G3/G4=1..3",
        "HYBRID_GROUP_COUNT_AND_FLOORED": "G1/G2=2..3; G3/G4=2..2",
    }

    combined = pd.concat([strict, unfloored, floored], ignore_index=True)
    combined["survivor_candidate_count"] = combined["method"].map(
        survivor_candidate_counts
    )
    combined["survivor_candidate_denominator"] = 6
    combined["survivor_ticker_count"] = combined["method"].map(
        survivor_ticker_counts
    )
    combined["threshold_constraint"] = combined["method"].map(
        threshold_constraints
    )

    diagnostics: list[dict[str, Any]] = []
    for _, row in combined.iterrows():
        method = str(row["method"])
        ticker = str(row["ticker"])
        diag = (
            _pooled_trade_diagnostics(method)
            if ticker == "ALL_2_POOLED"
            else _trade_diagnostics(method, ticker)
        )
        diagnostics.append(diag)
    diag_frame = pd.DataFrame(diagnostics)
    combined = pd.concat([combined.reset_index(drop=True), diag_frame], axis=1)

    order = {
        "STRICT_AND_12_BASELINE": 0,
        "HYBRID_GROUP_COUNT_AND_UNFLOORED": 1,
        "HYBRID_GROUP_COUNT_AND_FLOORED": 2,
    }
    ticker_order = {"AAP": 0, "POWI": 1, "ALL_2_POOLED": 2}
    combined["_method_order"] = combined["method"].map(order)
    combined["_ticker_order"] = combined["ticker"].map(ticker_order)
    combined = combined.sort_values(["_ticker_order", "_method_order"]).drop(
        columns=["_method_order", "_ticker_order"]
    )
    combined.to_csv(OUT_DIR / "three_way_comparison.csv", index=False)

    pooled = combined[combined["ticker"].eq("ALL_2_POOLED")].set_index("method")
    prior_row = pooled.loc["HYBRID_GROUP_COUNT_AND_UNFLOORED"]
    floor_row = pooled.loc["HYBRID_GROUP_COUNT_AND_FLOORED"]
    strict_row = pooled.loc["STRICT_AND_12_BASELINE"]

    compound_delta_prior = float(
        floor_row["compounded_return_pct"] - prior_row["compounded_return_pct"]
    )
    precision_delta_prior = float(
        floor_row["oos_precision"] - prior_row["oos_precision"]
    )
    survivor_delta = int(
        survivor_candidate_counts["HYBRID_GROUP_COUNT_AND_FLOORED"]
        - survivor_candidate_counts["HYBRID_GROUP_COUNT_AND_UNFLOORED"]
    )

    if (
        survivor_delta > 0
        and compound_delta_prior >= 0.50
        and precision_delta_prior >= -0.02
    ) or (
        compound_delta_prior >= 1.00
        and precision_delta_prior >= 0.0
        and float(floor_row["max_drawdown_pct"])
        >= float(prior_row["max_drawdown_pct"])
    ):
        verdict = "FLOORED_BETTER"
    elif (
        compound_delta_prior <= -0.50
        or precision_delta_prior <= -0.05
        or (
            int(survivor_candidate_counts["HYBRID_GROUP_COUNT_AND_FLOORED"]) == 0
            and float(floor_row["compounded_return_pct"])
            < float(strict_row["compounded_return_pct"]) - 1.0
        )
    ):
        verdict = "FLOORED_WORSE"
    else:
        verdict = "SIMILAR"

    summary.update(
        {
            "threshold_constraint": {
                "G1_PULLBACK": [2, 3],
                "G2_VOLATILITY": [2, 3],
                "G3_RANGE_EXPANSION": [2, 2],
                "G4_VOLUME_CONFIRMATION": [2, 2],
            },
            "full_group_threshold_forbidden": True,
            "prior_unfloored_pooled_compounded_return_pct": float(
                prior_row["compounded_return_pct"]
            ),
            "floored_pooled_compounded_return_pct": float(
                floor_row["compounded_return_pct"]
            ),
            "strict_pooled_compounded_return_pct_three_way": float(
                strict_row["compounded_return_pct"]
            ),
            "floored_minus_unfloored_compound_pctpoint": compound_delta_prior,
            "floored_minus_unfloored_precision": precision_delta_prior,
            "floored_survivor_candidate_count": int(
                survivor_candidate_counts["HYBRID_GROUP_COUNT_AND_FLOORED"]
            ),
            "floored_survivor_ticker_count": int(
                survivor_ticker_counts["HYBRID_GROUP_COUNT_AND_FLOORED"]
            ),
            "three_way_verdict": verdict,
        }
    )
    return combined, verdict


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    namespace = runpy.run_path(str(BASE_RUNNER), run_name="hybrid_group_base_floored")
    namespace["OUT_DIR"] = OUT_DIR
    namespace["train_grouped_interval_ga"] = floored_ga.train_grouped_interval_ga
    namespace["validate_grouped_gene"] = floored_ga.validate_grouped_gene

    summary = namespace["run"]()
    _apply_threshold_audit()
    _rename_trade_outputs()
    _, verdict = _build_three_way(summary)
    summary["generated_by"] = str(Path(__file__).relative_to(KINGMAKER_ROOT))
    summary["provisional_verdict_from_base_runner"] = summary.get(
        "provisional_verdict"
    )
    summary["provisional_verdict"] = verdict
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
