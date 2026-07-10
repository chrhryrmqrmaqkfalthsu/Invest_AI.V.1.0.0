from __future__ import annotations

"""Research-only core technical signal-concentration analysis."""

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

ROOT = Path(__file__).resolve().parents[4]
ANALYSIS_ROOT = ROOT / "data/_system/analysis"
FROZEN_DIR = ANALYSIS_ROOT / "oos_reproduce_frozen_20260707"
TRADE_PATH = FROZEN_DIR / "oos_trades_frozen.csv"
CANDIDATE_PATH = FROZEN_DIR / "candidate_universe.json"
OHLC_DIR = ANALYSIS_ROOT / "ohlc_snapshot_20260707"


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def load_snapshot(ticker: str) -> pd.DataFrame:
    raw = pd.read_csv(OHLC_DIR / f"{ticker}_ohlcv.csv")
    raw.index = pd.to_datetime(raw.pop("Date"), errors="coerce")
    raw = raw[~raw.index.isna()].sort_index()
    return calc_indicators(raw[["Open", "High", "Low", "Close", "Volume"]])


def technical_components(rb: Rulebook, df: pd.DataFrame, idx: int) -> dict[str, float]:
    row = df.iloc[idx]
    is_short = rb.direction == "short"

    aligned = bool(row.get("Aligned_bull", 0))
    if is_short:
        ma5, ma20, ma60 = row.get("MA5"), row.get("MA20"), row.get("MA60")
        aligned = bool(pd.notna(ma5) and pd.notna(ma20) and pd.notna(ma60) and ma5 < ma20 < ma60)

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

    rsi_value = safe_float(row.get("RSI"), 50.0)
    if is_short:
        rsi_low = max(float(rb.rsi_low) + 30.0, 60.0)
        rsi_high = min(float(rb.rsi_high) + 10.0, 85.0)
    else:
        rsi_low, rsi_high = float(rb.rsi_low), float(rb.rsi_high)

    if is_short:
        bb_upper = safe_float(row.get("BB_upper"))
        close = safe_float(row.get("Close"))
        bb_ok = bool(math.isfinite(bb_upper) and bb_upper > 0 and math.isfinite(close) and close >= bb_upper / float(rb.bb_proximity))
    else:
        bb_ok = bool(is_bb_near_lower(row, proximity=float(rb.bb_proximity)))

    volume_ok = bool(is_volume_surge(row, threshold=float(rb.volume_surge_ratio)))
    return {
        "ma_align": float(rb.weight_ma_align) if aligned else 0.0,
        "macd": float(rb.weight_macd_golden) if macd_event else 0.0,
        "rsi": float(rb.weight_rsi_zone) if rsi_low <= rsi_value <= rsi_high else 0.0,
        "bb": float(rb.weight_bb_near_lower) if bb_ok else 0.0,
        "volume": float(rb.weight_volume_surge) if volume_ok else 0.0,
    }


def otsu_log_threshold(values: np.ndarray) -> dict[str, float]:
    positive = np.asarray(values, dtype=float)
    positive = positive[np.isfinite(positive) & (positive > 0)]
    if positive.size < 2:
        return {"threshold": math.nan, "log10_threshold": math.nan, "between_variance": math.nan, "n": int(positive.size)}
    x = np.sort(np.log10(positive))
    cumulative = np.cumsum(x)
    total = cumulative[-1]
    left_n = np.arange(1, x.size)
    right_n = x.size - left_n
    left_mean = cumulative[:-1] / left_n
    right_mean = (total - cumulative[:-1]) / right_n
    between = left_n * right_n * (left_mean - right_mean) ** 2
    between = np.where(x[1:] > x[:-1], between, -np.inf)
    best = int(np.argmax(between))
    threshold_log = float((x[best] + x[best + 1]) / 2.0)
    return {
        "threshold": float(10.0**threshold_log),
        "log10_threshold": threshold_log,
        "between_variance": float(between[best]),
        "n": int(x.size),
    }


def aggregate(group: pd.DataFrame) -> dict[str, Any]:
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


def cluster_bootstrap_diff(df: pd.DataFrame, blocked_mask: pd.Series) -> dict[str, float]:
    work = df[["candidate_id", "net_pct"]].copy()
    work["blocked"] = np.asarray(blocked_mask, dtype=bool)
    work["win"] = work["net_pct"] > 0
    candidates = sorted(work["candidate_id"].unique())
    records = []
    for candidate_id in candidates:
        sub = work[work["candidate_id"].eq(candidate_id)]
        low = sub[sub["blocked"]]
        high = sub[~sub["blocked"]]
        records.append((len(low), low["net_pct"].sum(), low["win"].sum(), len(high), high["net_pct"].sum(), high["win"].sum()))
    arr = np.asarray(records, dtype=float)
    rng = np.random.default_rng(SEED)
    sample_idx = rng.integers(0, len(candidates), size=(BOOTSTRAP_REPS, len(candidates)))
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


def boundary_row(df: pd.DataFrame, split: str, boundary: int) -> dict[str, Any]:
    blocked_mask = df["effective_indicator_count"] <= boundary
    blocked = aggregate(df[blocked_mask])
    kept = aggregate(df[~blocked_mask])
    overall = aggregate(df)
    bootstrap = cluster_bootstrap_diff(df, blocked_mask)
    sufficient = bool(
        blocked["n"] >= MIN_ROWS_PER_SIDE
        and kept["n"] >= MIN_ROWS_PER_SIDE
        and blocked["candidate_n"] >= MIN_CANDIDATES_PER_SIDE
        and kept["candidate_n"] >= MIN_CANDIDATES_PER_SIDE
    )
    significant = bool(sufficient and bootstrap["pnl_diff_ci_high"] < 0 and bootstrap["win_diff_ci_high"] < 0)
    return {
        "split": split,
        "block_effective_count_le": boundary,
        "sample_sufficient": sufficient,
        "blocked_n": blocked["n"],
        "blocked_candidate_n": blocked["candidate_n"],
        "blocked_avg_pnl_pct": blocked["avg_pnl_pct"],
        "blocked_win_rate_pct": blocked["win_rate_pct"],
        "blocked_avg_mae_pct": blocked["avg_mae_pct"],
        "blocked_avg_mfe_pct": blocked["avg_mfe_pct"],
        "kept_n": kept["n"],
        "kept_candidate_n": kept["candidate_n"],
        "kept_avg_pnl_pct": kept["avg_pnl_pct"],
        "kept_win_rate_pct": kept["win_rate_pct"],
        "kept_avg_mae_pct": kept["avg_mae_pct"],
        "kept_avg_mfe_pct": kept["avg_mfe_pct"],
        "all_avg_pnl_pct": overall["avg_pnl_pct"],
        "all_win_rate_pct": overall["win_rate_pct"],
        "pnl_diff_blocked_minus_kept_pctp": blocked["avg_pnl_pct"] - kept["avg_pnl_pct"],
        "win_diff_blocked_minus_kept_pctp": blocked["win_rate_pct"] - kept["win_rate_pct"],
        "kept_avg_pnl_delta_vs_all_pctp": kept["avg_pnl_pct"] - overall["avg_pnl_pct"],
        "kept_win_delta_vs_all_pctp": kept["win_rate_pct"] - overall["win_rate_pct"],
        "significant_collapse": significant,
        **bootstrap,
    }


def main() -> int:
    trades = pd.read_csv(TRADE_PATH)
    candidates = json.loads(CANDIDATE_PATH.read_text())
    candidate_map = {str(row["candidate_id"]): row for row in candidates}
    output_rows: list[dict[str, Any]] = []
    parity_diffs: list[float] = []
    rng = np.random.default_rng(SEED)

    grouped = list(trades.groupby("candidate_id", sort=True))
    for candidate_no, (candidate_id, group) in enumerate(grouped, start=1):
        candidate = candidate_map[candidate_id]
        ticker = str(candidate["ticker"]).upper()
        rb_dict = _load_rulebook_for_candidate(candidate)
        if not isinstance(rb_dict, dict) or not rb_dict:
            raise RuntimeError(f"rulebook missing: {candidate_id}")
        rb_dict = dict(rb_dict)
        rb_dict["ticker"] = ticker
        rb = Rulebook.from_dict(rb_dict)
        df = load_snapshot(ticker)
        date_to_idx = {pd.Timestamp(value).normalize(): idx for idx, value in enumerate(df.index)}
        sample_indices = set(rng.choice(group.index.to_numpy(), size=min(5, len(group)), replace=False).tolist())

        for trade_index, trade in group.iterrows():
            signal_date = pd.Timestamp(trade["signal_date"]).normalize()
            idx = date_to_idx.get(signal_date)
            if idx is None:
                raise RuntimeError(f"signal date missing: {candidate_id} {signal_date.date()}")
            components = technical_components(rb, df, idx)
            market_adjustment = safe_float(trade["entry_market_adjustment"], 1.0)
            final_score = safe_float(trade["entry_signal_score"])
            adjusted = {key: max(0.0, value * market_adjustment) for key, value in components.items()}
            shares = {key: adjusted[key] / final_score if final_score > 0 else 0.0 for key in CORE_COMPONENTS}
            ordered = sorted(shares.values(), reverse=True)
            row = dict(trade)
            row.update({f"component_{key}": components[key] for key in CORE_COMPONENTS})
            row.update({f"final_share_{key}": shares[key] for key in CORE_COMPONENTS})
            row.update({
                "core_final_share": float(sum(shares.values())),
                "top1_final_share": ordered[0] if ordered else 0.0,
                "top2_final_share": sum(ordered[:2]),
                "raw_active_indicator_count": int(sum(value > 0 for value in components.values())),
            })
            output_rows.append(row)

            if trade_index in sample_indices:
                replay = evaluate_signal(rb, df.iloc[: idx + 1], market_score=50, sector_score=50, vix_level=18, news_sentiment=0, event_flags=None, topic_features=None)
                parity_diffs.append(max(abs(float(replay.components.get(key, 0.0)) - components[key]) for key in CORE_COMPONENTS))
        print(f"[{candidate_no:03d}/{len(grouped):03d}] {candidate_id} rows={len(group)}", flush=True)

    signal_df = pd.DataFrame(output_rows)
    is_df = signal_df[signal_df["split"].eq("IS")]
    positive_shares = np.concatenate([
        is_df[f"final_share_{key}"].to_numpy(float)[is_df[f"final_share_{key}"].to_numpy(float) > 0]
        for key in CORE_COMPONENTS
    ])
    cutoff_meta = otsu_log_threshold(positive_shares)
    cutoff = float(cutoff_meta["threshold"])
    share_columns = [f"final_share_{key}" for key in CORE_COMPONENTS]
    signal_df["effective_indicator_count"] = (signal_df[share_columns] >= cutoff).sum(axis=1).astype(int)

    performance_rows = []
    for split in ("IS", "OOS"):
        split_df = signal_df[signal_df["split"].eq(split)]
        for count in range(6):
            performance_rows.append({
                "split": split,
                "effective_indicator_count": count,
                "negligible_share_cutoff": cutoff,
                **aggregate(split_df[split_df["effective_indicator_count"].eq(count)]),
            })

    boundary_rows = [
        boundary_row(signal_df[signal_df["split"].eq(split)].copy(), split, boundary)
        for split in ("IS", "OOS")
        for boundary in range(5)
    ]
    boundary_df = pd.DataFrame(boundary_rows)
    qualifying = boundary_df[(boundary_df["split"].eq("IS")) & (boundary_df["significant_collapse"])].sort_values("block_effective_count_le")
    selected_boundary = int(qualifying.iloc[0]["block_effective_count_le"]) if not qualifying.empty else None

    if selected_boundary is None:
        verdict = "REJECT_NO_SIGNIFICANT_IS_BOUNDARY"
        validation = {}
    else:
        is_selected = boundary_df[(boundary_df["split"].eq("IS")) & (boundary_df["block_effective_count_le"].eq(selected_boundary))].iloc[0].to_dict()
        oos_selected = boundary_df[(boundary_df["split"].eq("OOS")) & (boundary_df["block_effective_count_le"].eq(selected_boundary))].iloc[0].to_dict()
        oos_pass = bool(
            oos_selected["sample_sufficient"]
            and oos_selected["pnl_diff_ci_high"] < 0
            and oos_selected["win_diff_ci_high"] < 0
            and oos_selected["kept_avg_pnl_delta_vs_all_pctp"] >= 0
            and oos_selected["kept_win_delta_vs_all_pctp"] >= 0
        )
        verdict = "ACCEPT_OOS_CONFIRMED" if oos_pass else "REJECT_OOS_NOT_CONFIRMED"
        validation = {"is_selected": is_selected, "oos_selected": oos_selected, "oos_pass": oos_pass}

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
            "component_parity_sample_n": len(parity_diffs),
            "component_parity_max_abs_diff": max(parity_diffs) if parity_diffs else math.nan,
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
            "is_rule": "smallest k with candidate-cluster-bootstrap 95% upper CI < 0 for blocked-minus-kept avg PnL and win rate",
            "oos_rule": "same significance plus kept avg PnL and win rate >= all OOS",
            "selected_boundary": selected_boundary,
            "verdict": verdict,
        },
        "performance_mapping": performance_rows,
        "boundary_scan": boundary_rows,
        "oos_validation": validation,
    }
    print("@@RESULT_JSON@@")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
