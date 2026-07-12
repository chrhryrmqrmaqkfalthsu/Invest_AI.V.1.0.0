#!/usr/bin/env python3
"""Compute relative-normalized features and L2 information metrics.

Analysis-only script. It reads preserved 50-symbol feature/OHLCV inputs and
writes only under relative_feature_information_20260712. It does not import or
execute GA, training, threshold-search, backtest, or live modules.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_classif

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/relative_feature_information_20260712"
PILOT = ROOT / "data/_system/analysis/stage2_3_rediscovery_pilot_20260712"
PREVIOUS = ROOT / "data/_system/analysis/relative_target_label_feasibility_20260712"
FEATURE_PATH = PILOT / "feature_set.csv"
SYMBOL_PATH = PILOT / "symbol_list.csv"
PREVIOUS_SUMMARY_PATH = PREVIOUS / "summary.json"

MI_NEIGHBORS = 5
MI_RANDOM_STATE = 20260712
MIN_PERCENTILE_HISTORY = 20
TOP_N = 5

RELATIVE_FEATURES = [
    "pullback5_atr14",
    "max_up_day5_atr14",
    "max_down_day5_atr14",
    "net_move5_atr14",
    "fade_after_surge_atr14",
    "true_range_d1_atr14",
    "range5_atr14",
    "avg_true_range5_atr14",
    "rv20_ratio_d1_d6",
    "rv5_to_rv20",
    "atr14_change5_pct",
    "close_pos5_history_pctile",
    "pullback5_atr_history_pctile",
    "range5_atr_history_pctile",
]

EXISTING_FEATURES = [
    "pullback_from_high5_pct",
    "fade_after_surge_score",
    "inv_close_pos5",
    "inv_ret_d1_pct",
    "single_up_day5_pct",
    "atr14_pct",
    "realized_vol20_pct",
    "bb_width20_pct",
    "true_range_d1_pct",
    "range_vs_atr14",
    "range_vs_range20",
    "volume_ratio5_prior",
    "volume_ratio20_prior",
    "volume_chg1_pct",
]

CATEGORY = {
    "pullback5_atr14": "RELATIVE_PULLBACK_SURGE",
    "max_up_day5_atr14": "RELATIVE_PULLBACK_SURGE",
    "max_down_day5_atr14": "RELATIVE_PULLBACK_SURGE",
    "net_move5_atr14": "RELATIVE_PULLBACK_SURGE",
    "fade_after_surge_atr14": "RELATIVE_PULLBACK_SURGE",
    "true_range_d1_atr14": "RELATIVE_RANGE",
    "range5_atr14": "RELATIVE_RANGE",
    "avg_true_range5_atr14": "RELATIVE_RANGE",
    "rv20_ratio_d1_d6": "VOLATILITY_CHANGE",
    "rv5_to_rv20": "VOLATILITY_CHANGE",
    "atr14_change5_pct": "VOLATILITY_CHANGE",
    "close_pos5_history_pctile": "INTERNAL_PERCENTILE",
    "pullback5_atr_history_pctile": "INTERNAL_PERCENTILE",
    "range5_atr_history_pctile": "INTERNAL_PERCENTILE",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_ohlcv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.tz_localize(None)
    frame = frame.dropna(subset=["Date"]).set_index("Date").sort_index()
    frame.index = pd.DatetimeIndex(frame.index).normalize()
    frame = frame[~frame.index.duplicated(keep="last")]
    for column in ["Open", "High", "Low", "Close", "Volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def causal_expanding_percentile(
    values: pd.Series,
    *,
    min_history: int = MIN_PERCENTILE_HISTORY,
) -> pd.Series:
    sorted_values: list[float] = []
    output = np.full(len(values), np.nan, dtype=float)
    for index, raw_value in enumerate(values.to_numpy(float)):
        if not math.isfinite(raw_value):
            continue
        left = bisect.bisect_left(sorted_values, raw_value)
        right = bisect.bisect_right(sorted_values, raw_value)
        new_count = len(sorted_values) + 1
        average_rank = (left + right + 2.0) / 2.0
        if new_count >= min_history:
            output[index] = average_rank / new_count
        bisect.insort(sorted_values, raw_value)
    return pd.Series(output, index=values.index)


def calculate_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].astype(float)
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    close_diff = close.diff()
    returns_pct = close.pct_change() * 100.0
    rv5 = returns_pct.rolling(5, min_periods=5).std(ddof=1)
    rv20 = returns_pct.rolling(20, min_periods=20).std(ddof=1)

    high5 = high.rolling(5, min_periods=5).max()
    low5 = low.rolling(5, min_periods=5).min()
    range5_abs = high5 - low5
    pullback_abs = high5 - close
    max_up_abs = close_diff.clip(lower=0.0).rolling(5, min_periods=5).max()
    max_down_abs = (-close_diff).clip(lower=0.0).rolling(5, min_periods=5).max()
    close_pos5 = (close - low5) / range5_abs.replace(0, np.nan)

    pullback5_atr14 = pullback_abs / atr14.replace(0, np.nan)
    max_up_day5_atr14 = max_up_abs / atr14.replace(0, np.nan)
    max_down_day5_atr14 = max_down_abs / atr14.replace(0, np.nan)
    range5_atr14 = range5_abs / atr14.replace(0, np.nan)

    relative = pd.DataFrame(
        {
            "pullback5_atr14": pullback5_atr14,
            "max_up_day5_atr14": max_up_day5_atr14,
            "max_down_day5_atr14": max_down_day5_atr14,
            "net_move5_atr14": (close - close.shift(5)) / atr14.replace(0, np.nan),
            "fade_after_surge_atr14": max_up_day5_atr14 * pullback5_atr14,
            "true_range_d1_atr14": true_range / atr14.replace(0, np.nan),
            "range5_atr14": range5_atr14,
            "avg_true_range5_atr14": true_range.rolling(5, min_periods=5).mean()
            / atr14.replace(0, np.nan),
            "rv20_ratio_d1_d6": rv20 / rv20.shift(5).replace(0, np.nan),
            "rv5_to_rv20": rv5 / rv20.replace(0, np.nan),
            "atr14_change5_pct": 100.0 * (atr14 / atr14.shift(5).replace(0, np.nan) - 1.0),
            "close_pos5_history_pctile": causal_expanding_percentile(close_pos5),
            "pullback5_atr_history_pctile": causal_expanding_percentile(pullback5_atr14),
            "range5_atr_history_pctile": causal_expanding_percentile(range5_atr14),
        },
        index=frame.index,
    )

    bb_mid = close.rolling(20, min_periods=1).mean()
    bb_std = close.rolling(20, min_periods=1).std(ddof=1)
    range_pct = 100.0 * (high - low) / close.replace(0, np.nan)
    existing_expansion = pd.DataFrame(
        {
            "atr14_abs_d1": atr14,
            "atr14_pct": 100.0 * atr14 / close.replace(0, np.nan),
            "realized_vol20_pct": rv20,
            "bb_width20_pct": 400.0 * bb_std / bb_mid.replace(0, np.nan),
            "true_range_d1_pct": 100.0 * true_range / close.replace(0, np.nan),
            "range_vs_atr14": (high - low) / atr14.replace(0, np.nan),
            "range_vs_range20": range_pct
            / range_pct.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan),
            "volume_ratio5_prior": volume
            / volume.shift(1).rolling(5, min_periods=5).mean().replace(0, np.nan),
            "volume_ratio20_prior": volume
            / volume.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan),
            "volume_chg1_pct": 100.0
            * (volume / volume.shift(1).replace(0, np.nan) - 1.0),
        },
        index=frame.index,
    )

    combined = pd.concat([relative, existing_expansion], axis=1).shift(1)
    combined.index.name = "date"
    return combined.reset_index()


def safe_corr(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3 or np.std(x) <= 1e-15 or np.std(y) <= 1e-15:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def mi_bits(x: np.ndarray, y: np.ndarray) -> float:
    value = mutual_info_classif(
        x.reshape(-1, 1),
        y,
        discrete_features=False,
        n_neighbors=MI_NEIGHBORS,
        random_state=MI_RANDOM_STATE,
    )[0]
    return float(value / math.log(2.0))


def binary_entropy_bits(rate: float) -> float:
    if rate <= 0.0 or rate >= 1.0:
        return 0.0
    return -(rate * math.log2(rate) + (1.0 - rate) * math.log2(1.0 - rate))


def information_rows(
    frame: pd.DataFrame,
    features: list[str],
    label_column: str,
    feature_set_name: str,
) -> list[dict[str, Any]]:
    y = frame[label_column].to_numpy(int)
    rate = float(np.mean(y))
    entropy = binary_entropy_bits(rate)
    within_ticker_rank = frame.groupby("ticker", sort=False)[features].rank(
        pct=True,
        method="average",
    )
    rows: list[dict[str, Any]] = []
    for feature in features:
        x = frame[feature].to_numpy(float)
        ranked = within_ticker_rank[feature].to_numpy(float)
        mutual_information = mi_bits(x, y)
        rows.append(
            {
                "feature_set": feature_set_name,
                "feature": feature,
                "category": CATEGORY.get(feature, "EXISTING_14"),
                "label": label_column,
                "sample_count": len(frame),
                "label_positive_rate": rate,
                "label_entropy_bits": entropy,
                "pearson_corr": safe_corr(x, y),
                "mutual_information_bits": mutual_information,
                "mi_entropy_fraction": mutual_information / entropy if entropy else np.nan,
                "within_ticker_rank_pearson_corr": safe_corr(ranked, y),
                "within_ticker_rank_mi_bits": mi_bits(ranked, y),
                "within_ticker_rank_note": "FULL_SAMPLE_DIAGNOSTIC_NOT_LIVE_FEATURE",
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(rows, key=lambda row: row["mutual_information_bits"], reverse=True)
    rank_mi = sorted(rows, key=lambda row: row["within_ticker_rank_mi_bits"], reverse=True)
    return {
        "sample_count": int(rows[0]["sample_count"]),
        "label_positive_rate": float(rows[0]["label_positive_rate"]),
        "top_n": TOP_N,
        "top5_mi_bits_sum": float(
            sum(row["mutual_information_bits"] for row in ranked[:TOP_N])
        ),
        "max_single_feature_mi_bits": float(ranked[0]["mutual_information_bits"]),
        "abs_corr_ge_0_10_feature_count": int(
            sum(abs(row["pearson_corr"]) >= 0.10 for row in rows)
        ),
        "max_abs_pearson_corr": float(max(abs(row["pearson_corr"]) for row in rows)),
        "top_features_by_mi": [row["feature"] for row in ranked[:TOP_N]],
        "within_ticker_rank_top5_mi_bits_sum": float(
            sum(row["within_ticker_rank_mi_bits"] for row in rank_mi[:TOP_N])
        ),
        "within_ticker_rank_abs_corr_ge_0_10_feature_count": int(
            sum(abs(row["within_ticker_rank_pearson_corr"]) >= 0.10 for row in rows)
        ),
        "within_ticker_rank_top_features_by_mi": [
            row["feature"] for row in rank_mi[:TOP_N]
        ],
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    base = pd.read_csv(FEATURE_PATH)
    base["date"] = pd.to_datetime(base["date"], errors="coerce").dt.tz_localize(None)
    symbols = pd.read_csv(SYMBOL_PATH).sort_values("selection_order")

    feature_parts: list[pd.DataFrame] = []
    source_checks: list[dict[str, Any]] = []
    for row in symbols.to_dict("records"):
        ticker = str(row["ticker"])
        source = ROOT / str(row["source_path"])
        actual_sha = sha256(source)
        expected_sha = str(row["source_sha256"])
        if actual_sha != expected_sha:
            raise RuntimeError(f"source SHA mismatch: {ticker}")
        ohlcv = read_ohlcv(source)
        calculated = calculate_features(ohlcv)
        calculated.insert(0, "ticker", ticker)
        feature_parts.append(calculated)
        source_checks.append(
            {
                "ticker": ticker,
                "source_path": str(row["source_path"]),
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "sha_match": True,
                "ohlcv_rows": len(ohlcv),
                "ohlcv_first_date": str(ohlcv.index.min().date()),
                "ohlcv_last_date": str(ohlcv.index.max().date()),
            }
        )

    calculated_all = pd.concat(feature_parts, ignore_index=True)
    merged = base.merge(
        calculated_all,
        on=["ticker", "date"],
        how="left",
        validate="many_to_one",
    )
    merged["inv_close_pos5"] = 1.0 - merged["close_pos5"].astype(float)
    merged["inv_ret_d1_pct"] = -merged["ret_d1_pct"].astype(float)

    future_high = merged[["future_high_d1", "future_high_d2"]].max(axis=1)
    entry_open = merged["entry_open_d0"].astype(float)
    merged["L0_FIXED_3PCT"] = (future_high >= entry_open * 1.03).astype(int)
    merged["L2_RV20_1_0X_2D"] = (
        future_high
        >= entry_open
        * (1.0 + math.sqrt(2.0) * merged["realized_vol20_pct"] / 100.0)
    ).astype(int)
    l0_mismatch = int(
        (merged["L0_FIXED_3PCT"] != merged["label_2d3pct"].astype(int)).sum()
    )
    if l0_mismatch:
        raise RuntimeError(f"L0 mismatch against preserved label: {l0_mismatch}")

    required = RELATIVE_FEATURES + EXISTING_FEATURES + [
        "L0_FIXED_3PCT",
        "L2_RV20_1_0X_2D",
    ]
    valid = merged.dropna(subset=required).copy()
    finite = np.isfinite(valid[RELATIVE_FEATURES + EXISTING_FEATURES].to_numpy(float)).all(axis=1)
    valid = valid.loc[finite].copy()
    date_counts = valid.groupby("date")["ticker"].nunique()
    common_dates = date_counts[date_counts == 50].index
    common = valid[valid["date"].isin(common_dates)].copy()
    common = common.sort_values(["ticker", "date"]).reset_index(drop=True)
    counts = common.groupby("ticker").size()
    if len(counts) != 50 or counts.nunique() != 1:
        raise RuntimeError(f"unequal common sample: {counts.to_dict()}")

    output_columns = [
        "ticker",
        "date",
        "regime",
        "entry_open_d0",
        "future_high_d1",
        "future_high_d2",
        "L2_RV20_1_0X_2D",
    ] + RELATIVE_FEATURES
    relative_feature_set = common[output_columns].copy()
    relative_feature_set["date"] = relative_feature_set["date"].dt.strftime("%Y-%m-%d")
    relative_feature_set.to_csv(OUT / "relative_feature_set.csv", index=False)

    relative_rows = information_rows(
        common,
        RELATIVE_FEATURES,
        "L2_RV20_1_0X_2D",
        "NEW_RELATIVE_14",
    )
    pd.DataFrame(relative_rows).to_csv(
        OUT / "relative_feature_information.csv",
        index=False,
    )

    existing_l0_rows = information_rows(
        common,
        EXISTING_FEATURES,
        "L0_FIXED_3PCT",
        "EXISTING_14",
    )
    existing_l2_rows = information_rows(
        common,
        EXISTING_FEATURES,
        "L2_RV20_1_0X_2D",
        "EXISTING_14",
    )
    relative_summary = summarize(relative_rows)
    same_sample_l0 = summarize(existing_l0_rows)
    same_sample_l2 = summarize(existing_l2_rows)

    previous_summary = json.loads(PREVIOUS_SUMMARY_PATH.read_text(encoding="utf-8"))
    previous_l0 = previous_summary["labels"]["L0_FIXED_3PCT"]
    previous_l2 = previous_summary["labels"]["L2_RV20_1_0X_2D"]

    comparison_rows = [
        {
            "feature_set": "EXISTING_14",
            "label": "L0_FIXED_3PCT",
            "benchmark_scope": "PRIOR_75000_COMMON_ROWS",
            "benchmark_sample_count": 75000,
            "top5_mi_bits_sum": previous_l0["top5_mi_bits_sum"],
            "max_single_feature_mi_bits": 0.09939042160517653,
            "abs_corr_ge_0_10_feature_count": previous_l0[
                "abs_corr_ge_0_10_feature_count"
            ],
            "same_sample_count": same_sample_l0["sample_count"],
            "same_sample_top5_mi_bits_sum": same_sample_l0["top5_mi_bits_sum"],
            "same_sample_max_single_feature_mi_bits": same_sample_l0[
                "max_single_feature_mi_bits"
            ],
            "same_sample_abs_corr_ge_0_10_feature_count": same_sample_l0[
                "abs_corr_ge_0_10_feature_count"
            ],
            "within_ticker_rank_top5_mi_bits_sum": same_sample_l0[
                "within_ticker_rank_top5_mi_bits_sum"
            ],
            "top_features_by_mi": json.dumps(
                same_sample_l0["top_features_by_mi"],
                ensure_ascii=False,
            ),
        },
        {
            "feature_set": "EXISTING_14",
            "label": "L2_RV20_1_0X_2D",
            "benchmark_scope": "PRIOR_75000_COMMON_ROWS",
            "benchmark_sample_count": 75000,
            "top5_mi_bits_sum": previous_l2["top5_mi_bits_sum"],
            "max_single_feature_mi_bits": 0.09306177733208396,
            "abs_corr_ge_0_10_feature_count": previous_l2[
                "abs_corr_ge_0_10_feature_count"
            ],
            "same_sample_count": same_sample_l2["sample_count"],
            "same_sample_top5_mi_bits_sum": same_sample_l2["top5_mi_bits_sum"],
            "same_sample_max_single_feature_mi_bits": same_sample_l2[
                "max_single_feature_mi_bits"
            ],
            "same_sample_abs_corr_ge_0_10_feature_count": same_sample_l2[
                "abs_corr_ge_0_10_feature_count"
            ],
            "within_ticker_rank_top5_mi_bits_sum": same_sample_l2[
                "within_ticker_rank_top5_mi_bits_sum"
            ],
            "top_features_by_mi": json.dumps(
                same_sample_l2["top_features_by_mi"],
                ensure_ascii=False,
            ),
        },
        {
            "feature_set": "NEW_RELATIVE_14",
            "label": "L2_RV20_1_0X_2D",
            "benchmark_scope": "CURRENT_COMMON_ROWS",
            "benchmark_sample_count": relative_summary["sample_count"],
            "top5_mi_bits_sum": relative_summary["top5_mi_bits_sum"],
            "max_single_feature_mi_bits": relative_summary[
                "max_single_feature_mi_bits"
            ],
            "abs_corr_ge_0_10_feature_count": relative_summary[
                "abs_corr_ge_0_10_feature_count"
            ],
            "same_sample_count": relative_summary["sample_count"],
            "same_sample_top5_mi_bits_sum": relative_summary[
                "top5_mi_bits_sum"
            ],
            "same_sample_max_single_feature_mi_bits": relative_summary[
                "max_single_feature_mi_bits"
            ],
            "same_sample_abs_corr_ge_0_10_feature_count": relative_summary[
                "abs_corr_ge_0_10_feature_count"
            ],
            "within_ticker_rank_top5_mi_bits_sum": relative_summary[
                "within_ticker_rank_top5_mi_bits_sum"
            ],
            "top_features_by_mi": json.dumps(
                relative_summary["top_features_by_mi"],
                ensure_ascii=False,
            ),
        },
    ]
    pd.DataFrame(comparison_rows).to_csv(
        OUT / "three_way_information_comparison.csv",
        index=False,
    )
    pd.DataFrame(source_checks).to_csv(OUT / "source_checks.csv", index=False)

    l2_benchmark = float(previous_l2["top5_mi_bits_sum"])
    l0_benchmark = float(previous_l0["top5_mi_bits_sum"])
    new_top5 = float(relative_summary["top5_mi_bits_sum"])
    corr_count = int(relative_summary["abs_corr_ge_0_10_feature_count"])
    material_threshold = l2_benchmark * 1.10
    if new_top5 >= material_threshold and corr_count >= 3:
        verdict = "RELATIVE_FEATURE_PROMISING"
    elif new_top5 > l2_benchmark:
        verdict = "RELATIVE_FEATURE_MARGINAL"
    else:
        verdict = "RELATIVE_FEATURE_NOHELP"

    summary = {
        "verdict": verdict,
        "question": "Do relative-normalized features recover L2 MI/correlation versus the existing 14 features?",
        "sample": {
            "tickers": 50,
            "rows_per_ticker": int(counts.iloc[0]),
            "rows_total": len(common),
            "first_date": str(common["date"].min().date()),
            "last_date": str(common["date"].max().date()),
            "l2_positive_rate": float(common["L2_RV20_1_0X_2D"].mean()),
            "l0_mismatch_against_preserved_label": l0_mismatch,
        },
        "benchmarks": {
            "existing_14_l2_top5_mi_bits": l2_benchmark,
            "existing_14_l0_top5_mi_bits": l0_benchmark,
            "material_improvement_threshold_10pct_over_l2": material_threshold,
        },
        "new_relative_14": relative_summary,
        "same_sample_existing_14_l0": same_sample_l0,
        "same_sample_existing_14_l2": same_sample_l2,
        "change_vs_prior_l2_pct": 100.0 * (new_top5 / l2_benchmark - 1.0),
        "distance_to_l0_pct": 100.0 * (new_top5 / l0_benchmark),
        "relative_strength_market_proxy": {
            "status": "NOT_AVAILABLE",
            "checked": ["SPY", "QQQ", "IWM", "VTI"],
            "reason": "No market/sector proxy OHLCV in frozen snapshot",
        },
        "method": {
            "feature_count": len(RELATIVE_FEATURES),
            "feature_cutoff": "D-1",
            "percentile_contract": "CAUSAL_EXPANDING_AVERAGE_RANK_MIN_20",
            "mi_estimator": "sklearn.feature_selection.mutual_info_classif",
            "mi_neighbors": MI_NEIGHBORS,
            "mi_random_state": MI_RANDOM_STATE,
            "mi_unit": "bits",
            "training_or_ga_run": False,
        },
        "input_sha256": {
            "feature_set.csv": sha256(FEATURE_PATH),
            "symbol_list.csv": sha256(SYMBOL_PATH),
            "previous_summary.json": sha256(PREVIOUS_SUMMARY_PATH),
        },
    }
    (OUT / "summary.json").write_text(
        json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
