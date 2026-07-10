from __future__ import annotations

"""Research-only signal contribution concentration analysis.

Safety:
- Reads frozen OHLC/trade/candidate artifacts only.
- Does not mutate live candidates, rulebooks, settings, positions, or orders.
- Does not train or write into source rule pools.
"""

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine.core.indicators import calc_indicators, is_bb_near_lower, is_volume_surge
from engine.live.elite_shadow_trader import _load_rulebook_for_candidate
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import Rulebook

SEED = 42
BOOTSTRAP_REPS = 2000
MIN_ROWS_PER_SIDE = 500
MIN_CANDIDATES_PER_SIDE = 15
CORE_COMPONENTS = ("ma_align", "macd", "rsi", "bb", "volume")

ROOT = Path(__file__).resolve().parents[5]
ANALYSIS_ROOT = ROOT / "data/_system/analysis"
FROZEN_DIR = ANALYSIS_ROOT / "oos_reproduce_frozen_20260707"
TRADE_PATH = FROZEN_DIR / "oos_trades_frozen.csv"
CANDIDATE_PATH = FROZEN_DIR / "candidate_universe.json"
OHLC_DIR = ANALYSIS_ROOT / "ohlc_snapshot_20260707"


def _safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _load_snapshot(ticker: str) -> pd.DataFrame:
    path = OHLC_DIR / f"{ticker}_ohlcv.csv"
    raw = pd.read_csv(path)
    raw.index = pd.to_datetime(raw["Date"], errors="coerce")
    raw = raw.drop(columns=["Date"])
    raw = raw[~raw.index.isna()].sort_index()
    return calc_indicators(raw[["Open", "High", "Low", "Close", "Volume"]])


def _technical_components(rb: Rulebook, df: pd.DataFrame, idx: int) -> dict[str, float]:
    row = df.iloc[idx]
    is_short = rb.direction == "short"

    aligned = bool(row.get("Aligned_bull", 0))
    if is_short:
        ma5, ma20, ma60 = row.get("MA5"), row.get("MA20"), row.get("MA60")
        aligned = bool(
            ma5 is not None
            and ma20 is not None
            and ma60 is not None
            and pd.notna(ma5)
            and pd.notna(ma20)
            and pd.notna(ma60)
            and ma5 < ma20 < ma60
        )
    ma_align = float(rb.weight_ma_align) if aligned else 0.0

    if is_short:
        macd_event = bool(
            idx >= 1
            and pd.notna(row.get("MACD"))
            and pd.notna(row.get("MACD_signal"))
            and row["MACD"] < row["MACD_signal"]
            and df["MACD"].iloc[idx - 1] >= df["MACD_signal"].iloc[idx - 1]
        )
    else:
        macd_event = bool(row.get("MACD_golden", 0))
    macd = float(rb.weight_macd_golden) if macd_event else 0.0

    rsi_value = _safe_float(row.get("RSI"), 50.0)
    if is_short:
        rsi_low = max(float(rb.rsi_low) + 30.0, 60.0)
        rsi_high = min(float(rb.rsi_high) + 10.0, 85.0)
    else:
        rsi_low, rsi_high = float(rb.rsi_low), float(rb.rsi_high)
    rsi = float(rb.weight_rsi_zone) if rsi_low <= rsi_value <= rsi_high else 0.0

    if is_short:
        bb_upper = _safe_float(row.get("BB_upper"))
        close = _safe_float(row.get("Close"))
        bb_ok = bool(math.isfinite(bb_upper) and bb_upper > 0 and math.isfinite(close) and close >= bb_upper / float(rb.bb_proximity))
    else:
        bb_ok = bool(is_bb_near_lower(row, proximity=float(rb.bb_proximity)))
    bb = float(rb.weight_bb_near_lower) if bb_ok else 0.0

    volume_ok = bool(is_volume_surge(row, threshold=float(rb.volume_surge_ratio)))
    volume = float(rb.weight_volume_surge) if volume_ok else 0.0

    return {
        "ma_align": ma_align,
        "macd": macd,
        "rsi": rsi,
        "bb": bb,
        "volume": volume,
    }


def _otsu_log_threshold(values: np.ndarray) -> dict[str, float]:
    positive = np.asarray(values, dtype=float)
    positive = positive[np.isfinite(positive) & (positive > 0)]
    if positive.size < 2:
        return {"threshold": math.nan, "log10_threshold": math.nan, "between_variance": math.nan, "n": int(positive.size)}
    x = np.sort(np.log10(positive))
    cumulative = np.cumsum(x)
    total = cumulative[-1]
    n = x.size
    left_n = np.arange(1, n)
    right_n = n - left_n
    left_mean = cumulative[:-1] / left_n
    right_mean = (total - cumulative[:-1]) / right_n
    between = left_n * right_n * (left_mean - right_mean) ** 2
    valid_gap = x[1:] > x[:-1]
    between = np.where(valid_gap, between, -np.inf)
    best = int(np.argmax(between))
    threshold_log = float((x[best] + x[best + 1]) / 2.0)
    return {
        "threshold": float(10.0 ** threshold_log),
        "log10_threshold": threshold_log,
        "between_variance": float(between[best]),
        "n": int(n),
    }


def _aggregate(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "n": 0,
            "candidate_n": 0,
            "avg_pnl_pct": math.nan,
            "median_pnl_pct": math.nan,
            "win_rate_pct": math.nan,
            "avg_mae_pct": math.nan,
            "avg_mfe_pct": math.nan,
            "avg_top1_final_share": math.nan,
            "avg_top2_final_share": math.nan,
            "avg_core_final_share": math.nan,
            "exit_distribution": "{}",
        }
    return {
        "n": int(len(group)),
        "candidate_n": int(group["candidate_id"].nunique()),
        "avg_pnl_pct": float(group["net_pct"].mean()),
        "median_pnl_pct": float(group["net_pct"].median()),
        "win_rate_pct": float((group["net_pct"] > 0).mean() * 100.0),
        "avg_mae_pct": float(group["MAE"].mean()),
        "avg_mfe_pct": float(group["MFE"].mean()),
        "avg_top1_final_share": float(group["top1_final_share"].mean()),
        "avg_top2_final_share": float(group["top2_final_share"].mean()),
        "avg_core_final_share": float(group["core_final_share"].mean()),
        "exit_distribution": json.dumps(group["exit_reason"].value_counts().sort_index().to_dict(), ensure_ascii=False, sort_keys=True),
    }


def _cluster_bootstrap_diff(df: pd.DataFrame, blocked_mask: pd.Series, reps: int = BOOTSTRAP_REPS) -> dict[str, float]:
    work = df[["candidate_id", "net_pct"]].copy()
    work["blocked"] = np.asarray(blocked_mask, dtype=bool)
    work["win"] = work["net_pct"] > 0
    candidates = sorted(work["candidate_id"].unique())
    records = []
    for cid in candidates:
        sub = work[work["candidate_id"].eq(cid)]
        low = sub[sub["blocked"]]
        high = sub[~sub["blocked"]]
        records.append((
            len(low), float(low["net_pct"].sum()), int(low["win"].sum()),
            len(high), float(high["net_pct"].sum()), int(high["win"].sum()),
        ))
    arr = np.asarray(records, dtype=float)
    rng = np.random.default_rng(SEED)
    sample_idx = rng.integers(0, len(candidates), size=(reps, len(candidates)))
    sampled = arr[sample_idx].sum(axis=1)
    low_n, low_sum, low_win, high_n, high_sum, high_win = sampled.T
    valid = (low_n > 0) & (high_n > 0)
    pnl_diff = low_sum[valid] / low_n[valid] - high_sum[valid] / high_n[valid]
    win_diff = low_win[valid] / low_n[valid] * 100.0 - high_win[valid] / high_n[valid] * 100.0

    def ci(values: np.ndarray) -> tuple[float, float]:
        if values.size == 0:
            return math.nan, math.nan
        return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))

    pnl_lo, pnl_hi = ci(pnl_diff)
    win_lo, win_hi = ci(win_diff)
    return {
        "pnl_diff_ci_low": pnl_lo,
        "pnl_diff_ci_high": pnl_hi,
        "win_diff_ci_low": win_lo,
        "win_diff_ci_high": win_hi,
        "bootstrap_valid_reps": int(valid.sum()),
    }


def _boundary_row(df: pd.DataFrame, split: str, max_effective_count: int) -> dict[str, Any]:
    blocked_mask = df["effective_indicator_count"] <= max_effective_count
    blocked = df[blocked_mask]
    kept = df[~blocked_mask]
    all_stats = _aggregate(df)
    blocked_stats = _aggregate(blocked)
    kept_stats = _aggregate(kept)
    boot = _cluster_bootstrap_diff(df, blocked_mask)
    sufficient = bool(
        blocked_stats["n"] >= MIN_ROWS_PER_SIDE
        and kept_stats["n"] >= MIN_ROWS_PER_SIDE
        and blocked_stats["candidate_n"] >= MIN_CANDIDATES_PER_SIDE
        and kept_stats["candidate_n"] >= MIN_CANDIDATES_PER_SIDE
    )
    significant_collapse = bool(
        sufficient
        and boot["pnl_diff_ci_high"] < 0.0
        and boot["win_diff_ci_high"] < 0.0
    )
    return {
        "split": split,
        "block_effective_count_le": int(max_effective_count),
        "sample_sufficient": sufficient,
        "blocked_n": blocked_stats["n"],
        "blocked_candidate_n": blocked_stats["candidate_n"],
        "blocked_avg_pnl_pct": blocked_stats["avg_pnl_pct"],
        "blocked_win_rate_pct": blocked_stats["win_rate_pct"],
        "blocked_avg_mae_pct": blocked_stats["avg_mae_pct"],
        "blocked_avg_mfe_pct": blocked_stats["avg_mfe_pct"],
        "kept_n": kept_stats["n"],
        "kept_candidate_n": kept_stats["candidate_n"],
        "kept_avg_pnl_pct": kept_stats["avg_pnl_pct"],
        "kept_win_rate_pct": kept_stats["win_rate_pct"],
        "kept_avg_mae_pct": kept_stats["avg_mae_pct"],
        "kept_avg_mfe_pct": kept_stats["avg_mfe_pct"],
        "all_avg_pnl_pct": all_stats["avg_pnl_pct"],
        "all_win_rate_pct": all_stats["win_rate_pct"],
        "pnl_diff_blocked_minus_kept_pctp": blocked_stats["avg_pnl_pct"] - kept_stats["avg_pnl_pct"],
        "win_diff_blocked_minus_kept_pctp": blocked_stats["win_rate_pct"] - kept_stats["win_rate_pct"],
        "kept_avg_pnl_delta_vs_all_pctp": kept_stats["avg_pnl_pct"] - all_stats["avg_pnl_pct"],
        "kept_win_delta_vs_all_pctp": kept_stats["win_rate_pct"] - all_stats["win_rate_pct"],
        "significant_collapse": significant_collapse,
        **boot,
    }


def main() -> int:
    trades = pd.read_csv(TRADE_PATH)
    candidates = json.loads(CANDIDATE_PATH.read_text())
    candidate_map = {str(row["candidate_id"]): row for row in candidates}

    rows: list[dict[str, Any]] = []
    parity_samples: list[dict[str, Any]] = []
    rng = np.random.default_rng(SEED)

    for candidate_no, (candidate_id, group) in enumerate(trades.groupby("candidate_id", sort=True), start=1):
        candidate = candidate_map[candidate_id]
        ticker = str(candidate["ticker"]).upper()
        rb_dict = _load_rulebook_for_candidate(candidate)
        if not isinstance(rb_dict, dict) or not rb_dict:
            raise RuntimeError(f"rulebook missing: {candidate_id}")
        rb_dict = dict(rb_dict)
        rb_dict["ticker"] = ticker
        rb = Rulebook.from_dict(rb_dict)
        df = _load_snapshot(ticker)
        date_to_idx = {pd.Timestamp(value).normalize(): idx for idx, value in enumerate(df.index)}

        group_indices = group.index.to_numpy()
        sample_set = set(rng.choice(group_indices, size=min(5, len(group_indices)), replace=False).tolist())
        for trade_index, trade in group.iterrows():
            signal_date = pd.Timestamp(trade["signal_date"]).normalize()
            idx = date_to_idx.get(signal_date)
            if idx is None:
                raise RuntimeError(f"signal date missing: {candidate_id} {signal_date.date()}")
            components = _technical_components(rb, df, idx)
            market_adjustment = _safe_float(trade["entry_market_adjustment"], 1.0)
            final_score = _safe_float(trade["entry_signal_score"])
            adjusted = {key: max(0.0, float(value) * market_adjustment) for key, value in components.items()}
            shares = {
                key: (adjusted[key] / final_score if final_score > 0 else 0.0)
                for key in CORE_COMPONENTS
            }
            ordered = sorted(shares.values(), reverse=True)
            core_share = float(sum(shares.values()))
            row = dict(trade)
            row.update({
                **{f"component_{key}": components[key] for key in CORE_COMPONENTS},
                **{f"final_share_{key}": shares[key] for key in CORE_COMPONENTS},
                "core_final_share": core_share,
                "top1_final_share": ordered[0] if ordered else 0.0,
                "top2_final_share": sum(ordered[:2]),
                "raw_active_indicator_count": int(sum(value > 0 for value in components.values())),
            })
            rows.append(row)

            if trade_index in sample_set:
                replay = evaluate_signal(rb, df.iloc[: idx + 1], market_score=50.0, sector_score=50.0, vix_level=18.0, news_sentiment=0.0, event_flags=None, topic_features=None)
                diffs = {key: abs(float(replay.components.get(key, 0.0)) - components[key]) for key in CORE_COMPONENTS}
                parity_samples.append({
                    "candidate_id": candidate_id,
                    "signal_date": str(signal_date.date()),
                    "max_component_abs_diff": max(diffs.values()),
                })
        print(f"[{candidate_no:03d}/{len(candidate_map):03d}] {candidate_id} rows={len(group)}", flush=True)

    signal_df = pd.DataFrame(rows)
    is_df = signal_df[signal_df["split"].eq("IS")].copy()
    positive_is_shares = []
    for key in CORE_COMPONENTS:
        values = is_df[f"final_share_{key}"].to_numpy(float)
        positive_is_shares.append(values[values > 0])
    cutoff_meta = _otsu_log_threshold(np.concatenate(positive_is_shares))
    cutoff = float(cutoff_meta["threshold"])

    for frame in (signal_df,):
        share_cols = [f"final_share_{key}" for key in CORE_COMPONENTS]
        frame["effective_indicator_count"] = (frame[share_cols] >= cutoff).sum(axis=1).astype(int)

    performance_rows = []
    for split in ("IS", "OOS"):
        split_df = signal_df[signal_df["split"].eq(split)]
        for count in range(0, 6):
            group = split_df[split_df["effective_indicator_count"].eq(count)]
            performance_rows.append({
                "split": split,
                "effective_indicator_count": count,
                "negligible_share_cutoff": cutoff,
                **_aggregate(group),
            })

    boundary_rows = []
    for split in ("IS", "OOS"):
        split_df = signal_df[signal_df["split"].eq(split)].copy()
        for boundary in range(0, 5):
            boundary_rows.append(_boundary_row(split_df, split, boundary))

    boundary_df = pd.DataFrame(boundary_rows)
    is_scan = boundary_df[boundary_df["split"].eq("IS")].sort_values("block_effective_count_le")
    qualifying = is_scan[is_scan["significant_collapse"]]
    selected_boundary = int(qualifying.iloc[0]["block_effective_count_le"]) if not qualifying.empty else None

    if selected_boundary is None:
        verdict = "REJECT_NO_SIGNIFICANT_IS_BOUNDARY"
        oos_validation = {}
    else:
        is_selected = boundary_df[
            boundary_df["split"].eq("IS")
            & boundary_df["block_effective_count_le"].eq(selected_boundary)
        ].iloc[0].to_dict()
        oos_selected = boundary_df[
            boundary_df["split"].eq("OOS")
            & boundary_df["block_effective_count_le"].eq(selected_boundary)
        ].iloc[0].to_dict()
        oos_pass = bool(
            oos_selected["sample_sufficient"]
            and oos_selected["pnl_diff_ci_high"] < 0.0
            and oos_selected["win_diff_ci_high"] < 0.0
            and oos_selected["kept_avg_pnl_delta_vs_all_pctp"] >= 0.0
            and oos_selected["kept_win_delta_vs_all_pctp"] >= 0.0
        )
        verdict = "ACCEPT_OOS_CONFIRMED" if oos_pass else "REJECT_OOS_NOT_CONFIRMED"
        oos_validation = {
            "is_selected": is_selected,
            "oos_selected": oos_selected,
            "oos_pass": oos_pass,
        }

    parity_max = max((row["max_component_abs_diff"] for row in parity_samples), default=math.nan)
    result = {
        "metadata": {
            "seed": SEED,
            "bootstrap_reps": BOOTSTRAP_REPS,
            "trade_rows": int(len(signal_df)),
            "is_rows": int((signal_df["split"] == "IS").sum()),
            "oos_rows": int((signal_df["split"] == "OOS").sum()),
            "candidate_count": int(signal_df["candidate_id"].nunique()),
            "ticker_count": int(signal_df["ticker"].nunique()),
            "period_signal_min": str(signal_df["signal_date"].min()),
            "period_signal_max": str(signal_df["signal_date"].max()),
            "core_components": list(CORE_COMPONENTS),
            "component_parity_sample_n": len(parity_samples),
            "component_parity_max_abs_diff": parity_max,
            "source_trade_path": str(TRADE_PATH.relative_to(ROOT)),
            "source_ohlc_dir": str(OHLC_DIR.relative_to(ROOT)),
        },
        "effective_indicator_definition": {
            "component_share": "max(core_component * logged_market_adjustment, 0) / logged_final_score",
            "negligible_cutoff_method": "Otsu threshold on log10 positive component shares in IS only",
            **cutoff_meta,
        },
        "boundary_selection": {
            "candidate_boundaries": [0, 1, 2, 3, 4],
            "minimum_rows_each_side": MIN_ROWS_PER_SIDE,
            "minimum_candidates_each_side": MIN_CANDIDATES_PER_SIDE,
            "is_rule": "smallest k with cluster-bootstrap 95% upper CI < 0 for blocked-minus-kept avg PnL and win rate",
            "oos_rule": "same direction/significance plus kept avg PnL and win rate >= all OOS",
            "selected_boundary": selected_boundary,
            "verdict": verdict,
        },
        "performance_mapping": performance_rows,
        "boundary_scan": boundary_rows,
        "oos_validation": oos_validation,
    }
    print("@@RESULT_JSON@@")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
