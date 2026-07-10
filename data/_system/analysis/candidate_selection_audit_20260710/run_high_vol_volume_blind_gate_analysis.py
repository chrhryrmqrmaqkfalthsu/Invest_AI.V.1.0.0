from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
AUDIT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SPARSE = AUDIT / "sparse_indicator_entry_structure_full.csv"
GATE = AUDIT / "integrated_gate_candidate_dryrun.csv"
VOL_REF = ROOT / "data/_system/analysis/vol_perstock_mae_mfe_20260707/per_candidate_summary.csv"

TICKERS = AUDIT / "high_vol_volume_blind_ticker_classification.csv"
HIGH_ALL = AUDIT / "high_vol_volume_blind_all_high_vol.csv"
RISK = AUDIT / "high_vol_volume_blind_risk_candidates.csv"
PARITY = AUDIT / "high_vol_volume_blind_boil_ce_parity.csv"
SCOPE = AUDIT / "high_vol_volume_blind_scope_summary.csv"
SUMMARY = AUDIT / "high_vol_volume_blind_summary.json"

NON_VOLUME_WEIGHTS = [
    "weight_ma_align",
    "weight_macd_golden",
    "weight_rsi_zone",
    "weight_bb_near_lower",
]


def as_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def main() -> int:
    sparse = pd.read_csv(SPARSE, low_memory=False)
    gate = pd.read_csv(
        GATE,
        usecols=["candidate_id", "ticker", "history_avg_atr_pct", "vol_group", "check_boil"],
        low_memory=False,
    ).rename(columns={"vol_group": "legacy_candidate_vol_group", "check_boil": "legacy_boil_check"})
    frame = sparse.merge(gate, on=["candidate_id", "ticker"], how="left", validate="one_to_one")
    frame["ticker_key"] = frame["ticker"].astype(str).str.upper()

    ref = pd.read_csv(VOL_REF)
    ref = ref[ref["split"].astype(str).str.upper().eq("OOS")].copy()
    ref["ticker_key"] = ref["ticker"].astype(str).str.upper()
    medians = ref.groupby("vol_group")["avg_atr14_pct"].median().to_dict()
    low_mid_boundary = (float(medians["LOW_VOL"]) + float(medians["MID_VOL"])) / 2.0
    mid_high_boundary = (float(medians["MID_VOL"]) + float(medians["HIGH_VOL"])) / 2.0

    exact = (
        ref.groupby("ticker_key", as_index=False)
        .agg(
            exact_vol_group=("vol_group", lambda s: s.mode().iloc[0]),
            exact_avg_std20=("avg_std20", "median"),
            exact_avg_std20_ann=("avg_std20_ann", "median"),
            exact_avg_atr14_pct=("avg_atr14_pct", "median"),
            exact_reference_candidate_count=("candidate_id", "size"),
        )
    )
    conflicts = ref.groupby("ticker_key")["vol_group"].nunique()
    assert int(conflicts.gt(1).sum()) == 0
    exact_keys = set(exact["ticker_key"])

    proxy_source = frame[~frame["ticker_key"].isin(exact_keys)].copy()
    proxy = (
        proxy_source.groupby("ticker_key", as_index=False)
        .agg(
            proxy_atr_median_pct=("history_avg_atr_pct", "median"),
            proxy_atr_min_pct=("history_avg_atr_pct", "min"),
            proxy_atr_max_pct=("history_avg_atr_pct", "max"),
            proxy_candidate_count=("candidate_id", "size"),
            proxy_stage_count=("stage", "nunique"),
        )
    )
    assert proxy["proxy_atr_median_pct"].notna().all()
    proxy["proxy_vol_group"] = pd.cut(
        proxy["proxy_atr_median_pct"],
        bins=[-np.inf, low_mid_boundary, mid_high_boundary, np.inf],
        labels=["LOW_VOL", "MID_VOL", "HIGH_VOL"],
        include_lowest=True,
    ).astype(str)

    ticker_base = (
        frame.groupby("ticker_key", as_index=False)
        .agg(
            ticker=("ticker", "first"),
            origin_candidate_count=("candidate_id", "size"),
            origin_stage_count=("stage", "nunique"),
            origin_stage2_count=("stage", lambda s: int(s.eq("stage2").sum())),
            origin_stage3_count=("stage", lambda s: int(s.eq("stage3").sum())),
        )
    )
    classification = ticker_base.merge(exact, on="ticker_key", how="left").merge(proxy, on="ticker_key", how="left")
    classification["classification_method"] = np.where(
        classification["exact_vol_group"].notna(),
        "EXACT_FROZEN_IS_STD20_TERCILE",
        "ATR_PROXY_TICKER_MEDIAN",
    )
    classification["vol_group_final"] = classification["exact_vol_group"].fillna(classification["proxy_vol_group"])
    classification["classification_value"] = np.where(
        classification["exact_vol_group"].notna(),
        classification["exact_avg_std20"],
        classification["proxy_atr_median_pct"],
    )
    classification["classification_metric"] = np.where(
        classification["exact_vol_group"].notna(),
        "IS_AVG_STD20",
        "MEDIAN_CANDIDATE_HISTORY_ATR_PCT",
    )
    classification["low_mid_atr_proxy_boundary_pct"] = low_mid_boundary
    classification["mid_high_atr_proxy_boundary_pct"] = mid_high_boundary
    classification["classification_limit"] = np.where(
        classification["exact_vol_group"].notna(),
        "93-candidate frozen analysis mapped consistently by ticker",
        "proxy from median candidate trade-entry ATR; not exact full-ticker IS std20 tercile",
    )
    assert len(classification) == frame["ticker_key"].nunique() == 531
    assert classification["vol_group_final"].notna().all()
    classification.sort_values(["vol_group_final", "ticker_key"]).to_csv(TICKERS, index=False)

    group_map = classification.set_index("ticker_key")[
        ["vol_group_final", "classification_method", "classification_metric", "classification_value", "classification_limit"]
    ]
    frame = frame.join(group_map, on="ticker_key")
    frame["origin_complete_bool"] = as_bool(frame["origin_complete_bool"])
    frame["boil_existing_bool"] = as_bool(frame["boil_existing"])
    frame["ce_existing_fail_bool"] = as_bool(frame["ce_existing_fail"])
    frame["nonvolume_weight_sum"] = frame[NON_VOLUME_WEIGHTS].clip(lower=0).sum(axis=1)
    frame["nonvolume_market_cap_final_score"] = frame["nonvolume_weight_sum"] * frame["market_multiplier_cap"]
    frame["nonvolume_market_cap_threshold_margin"] = frame["nonvolume_market_cap_final_score"] - frame["signal_threshold"]
    frame["nonvolume_entry_possible_market_cap"] = frame["market_cap_nonvolume_min_count"].notna()
    frame["volume_weight_near_zero"] = frame["weight_volume_surge"].abs().le(0.05)
    frame["risk_high_vol_volume_blind"] = frame["vol_group_final"].eq("HIGH_VOL") & frame["nonvolume_entry_possible_market_cap"]
    frame["risk_reason"] = np.where(
        frame["risk_high_vol_volume_blind"],
        "HIGH_VOL_AND_ENTRY_POSSIBLE_WITHOUT_VOLUME_AT_MARKET_CAP",
        "",
    )
    frame["scope_status"] = np.select(
        [
            ~frame["vol_group_final"].eq("HIGH_VOL"),
            frame["risk_high_vol_volume_blind"],
        ],
        ["OUT_OF_SCOPE_MID_LOW", "RISK"],
        default="HIGH_VOL_NOT_POSSIBLE_WITHOUT_VOLUME",
    )

    columns = [
        "candidate_id", "stage", "ticker", "rulebook_hash", "source_file", "source_row_index",
        "vol_group_final", "classification_method", "classification_metric", "classification_value", "classification_limit",
        "signal_threshold", "market_multiplier_cap", "market_adjustment_strength", "use_market_entry_adjustment",
        *NON_VOLUME_WEIGHTS, "weight_volume_surge", "volume_weight_near_zero",
        "nonvolume_weight_sum", "nonvolume_market_cap_final_score", "nonvolume_market_cap_threshold_margin",
        "market_cap_nonvolume_min_count", "market_cap_nonvolume_subset", "neutral_nonvolume_min_count", "neutral_nonvolume_subset",
        "nonvolume_entry_possible_market_cap", "risk_high_vol_volume_blind", "risk_reason", "scope_status",
        "origin_complete_bool", "boil_existing_bool", "ce_existing_fail_bool",
        "history_avg_atr_pct", "legacy_candidate_vol_group", "legacy_boil_check",
        "base_n", "base_avg_pnl_pct", "base_win_rate_pct", "holdout_n", "holdout_avg_pnl_pct", "holdout_win_rate_pct",
    ]
    high = frame[frame["vol_group_final"].eq("HIGH_VOL")].sort_values(["scope_status", "stage", "ticker", "candidate_id"])
    risk = frame[frame["risk_high_vol_volume_blind"]].sort_values(["stage", "ticker", "candidate_id"])
    high[columns].to_csv(HIGH_ALL, index=False)
    risk[columns].to_csv(RISK, index=False)

    parity_rows: list[pd.DataFrame] = []
    for family, mask_col in (("BOIL", "boil_existing_bool"), ("CE", "ce_existing_fail_bool")):
        part = frame[frame[mask_col]].copy()
        part.insert(0, "defect_family", family)
        part["in_scope_high_vol"] = part["vol_group_final"].eq("HIGH_VOL")
        part["captured_by_stage1"] = part["risk_high_vol_volume_blind"]
        part["out_of_scope_reason"] = np.where(part["in_scope_high_vol"], "", part["vol_group_final"] + "_NOT_STAGE1")
        parity_rows.append(part[[
            "defect_family", "candidate_id", "stage", "ticker", "vol_group_final", "classification_method",
            "in_scope_high_vol", "captured_by_stage1", "out_of_scope_reason",
            "weight_volume_surge", "volume_weight_near_zero", "market_cap_nonvolume_min_count",
            "market_cap_nonvolume_subset", "nonvolume_market_cap_threshold_margin",
            "legacy_candidate_vol_group", "legacy_boil_check",
        ]])
    parity = pd.concat(parity_rows, ignore_index=True).sort_values(["defect_family", "stage", "ticker", "candidate_id"])
    parity.to_csv(PARITY, index=False)

    scope_rows: list[dict[str, object]] = []
    for scope_name, scoped in (
        ("ALL_ORIGINS", frame),
        ("COMPLETE_ORIGINS", frame[frame["origin_complete_bool"]]),
        ("STAGE2", frame[frame["stage"].eq("stage2")]),
        ("STAGE3", frame[frame["stage"].eq("stage3")]),
    ):
        high_mask = scoped["vol_group_final"].eq("HIGH_VOL")
        risk_mask = scoped["risk_high_vol_volume_blind"]
        scope_rows.extend([
            {"scope": scope_name, "metric": "ORIGIN_COUNT", "count": len(scoped), "denominator": len(scoped), "rate_pct": 100.0},
            {"scope": scope_name, "metric": "HIGH_VOL_COUNT", "count": int(high_mask.sum()), "denominator": len(scoped), "rate_pct": float(high_mask.mean() * 100.0) if len(scoped) else 0.0},
            {"scope": scope_name, "metric": "RISK_COUNT", "count": int(risk_mask.sum()), "denominator": len(scoped), "rate_pct": float(risk_mask.mean() * 100.0) if len(scoped) else 0.0},
            {"scope": scope_name, "metric": "RISK_WITHIN_HIGH_VOL", "count": int(risk_mask.sum()), "denominator": int(high_mask.sum()), "rate_pct": float(risk_mask.sum() / high_mask.sum() * 100.0) if high_mask.sum() else 0.0},
            {"scope": scope_name, "metric": "HIGH_VOL_SAFE_WITHOUT_VOLUME_IMPOSSIBLE", "count": int((high_mask & ~risk_mask).sum()), "denominator": int(high_mask.sum()), "rate_pct": float((high_mask & ~risk_mask).sum() / high_mask.sum() * 100.0) if high_mask.sum() else 0.0},
        ])
    for method, part in classification.groupby("classification_method"):
        scope_rows.append({
            "scope": "TICKER_CLASSIFICATION",
            "metric": method,
            "count": len(part),
            "denominator": len(classification),
            "rate_pct": float(len(part) / len(classification) * 100.0),
        })
    pd.DataFrame(scope_rows).to_csv(SCOPE, index=False)

    def parity_summary(family: str) -> dict[str, object]:
        part = parity[parity["defect_family"].eq(family)]
        high_part = part[part["in_scope_high_vol"]]
        return {
            "total": len(part),
            "final_high_vol": len(high_part),
            "final_mid_low_out_of_scope": int((~part["in_scope_high_vol"]).sum()),
            "captured_total": int(part["captured_by_stage1"].sum()),
            "captured_within_high_vol": int(high_part["captured_by_stage1"].sum()),
            "capture_rate_within_high_vol_pct": float(high_part["captured_by_stage1"].mean() * 100.0) if len(high_part) else 0.0,
            "final_group_distribution": part["vol_group_final"].value_counts().to_dict(),
        }

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "policy": "stage1-high-vol-entry-without-volume-market-cap",
        "origin_count": len(frame),
        "origin_ticker_count": frame["ticker_key"].nunique(),
        "classification": {
            "exact_tickers": int(classification["classification_method"].eq("EXACT_FROZEN_IS_STD20_TERCILE").sum()),
            "proxy_tickers": int(classification["classification_method"].eq("ATR_PROXY_TICKER_MEDIAN").sum()),
            "exact_origin_candidates": int(frame["classification_method"].eq("EXACT_FROZEN_IS_STD20_TERCILE").sum()),
            "proxy_origin_candidates": int(frame["classification_method"].eq("ATR_PROXY_TICKER_MEDIAN").sum()),
            "ticker_group_counts": classification["vol_group_final"].value_counts().to_dict(),
            "candidate_group_counts": frame["vol_group_final"].value_counts().to_dict(),
            "frozen_is_std20_edges": [0.01253285764551156, 0.024276749601364084, 0.03447246492476784, 0.07360942474911485],
            "reference_atr_group_medians_pct": {key: float(value) for key, value in medians.items()},
            "proxy_low_mid_boundary_pct": low_mid_boundary,
            "proxy_mid_high_boundary_pct": mid_high_boundary,
            "unjudged_tickers": 0,
            "limitation": "Only 91 tickers have exact frozen IS avg_std20 groups. Remaining 440 use ticker-median candidate history ATR proxy.",
        },
        "risk": {
            "high_vol_candidates": len(high),
            "risk_candidates": len(risk),
            "safe_high_vol_candidates": len(high) - len(risk),
            "risk_pct_all_origins": float(len(risk) / len(frame) * 100.0),
            "risk_pct_high_vol": float(len(risk) / len(high) * 100.0),
            "risk_stage_counts": risk["stage"].value_counts().to_dict(),
            "nonvolume_min_count_distribution_high_vol": {
                str(key): int(value) for key, value in high["market_cap_nonvolume_min_count"].value_counts(dropna=False).items()
            },
        },
        "parity": {
            "BOIL": parity_summary("BOIL"),
            "CE": parity_summary("CE"),
        },
        "assumptions": {
            "market_multiplier": "1 + clamp(market_adjustment_strength,0,1) when enabled; else 1.0",
            "noncore_additives": "news/topic/event/crash bonus fixed at zero",
            "risk_condition": "final HIGH_VOL and any MA/MACD/RSI/BB subset reaches threshold without volume",
            "N_independent": True,
        },
        "no_source_mutation": True,
        "no_live_candidate_change": True,
        "no_training": True,
        "no_order": True,
        "no_delete": True,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
