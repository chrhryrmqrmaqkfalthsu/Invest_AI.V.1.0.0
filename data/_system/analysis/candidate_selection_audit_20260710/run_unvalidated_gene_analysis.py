from __future__ import annotations

"""Read-only analysis of never-fired technical entry genes.

The script reads frozen/original rulebooks and cached daily bars only. It does not
run GA, retrain, place orders, or modify any source rulebook/configuration.
"""

import csv
import gzip
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SEED = 42
BOOT_REPS = 10000
EPS = 1e-12
MIN_ACTIVE_COUNT = 5
MIN_ACTIVE_RATE = 0.01

ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
EXP_ROOT = ROOT / "exp_batch_stage123_2009_20260616_full"
TICKER_ROOT = EXP_ROOT / "tickers"
CACHE_DIR = ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
FROZEN_DIR = ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707"
TRADE_PATH = FROZEN_DIR / "oos_trades_frozen.csv"
CANDIDATE_PATH = FROZEN_DIR / "candidate_universe.json"
VOL_SUMMARY_PATH = ROOT / "data/_system/analysis/vol_perstock_mae_mfe_20260707/per_candidate_summary.csv"

FULL_ACTIVITY_PATH = OUT_DIR / "unvalidated_gene_rule_activity_full.csv.gz"
UNVALIDATED_RULES_PATH = OUT_DIR / "unvalidated_gene_rules_full.csv.gz"
POOL_SUMMARY_PATH = OUT_DIR / "unvalidated_gene_pool_summary.csv"
COMPONENT_SUMMARY_PATH = OUT_DIR / "unvalidated_gene_component_summary.csv"
CANDIDATE_ACTIVITY_PATH = OUT_DIR / "unvalidated_gene_candidate_activity.csv"
PERFORMANCE_PATH = OUT_DIR / "unvalidated_gene_performance_comparison.csv"
CONTROL_PATH = OUT_DIR / "unvalidated_gene_controlled_comparison.csv"
SUMMARY_PATH = OUT_DIR / "unvalidated_gene_analysis_summary.json"

COMPONENTS = (
    ("ma", "weight_ma_align"),
    ("macd", "weight_macd_golden"),
    ("rsi", "weight_rsi_zone"),
    ("bb", "weight_bb_near_lower"),
    ("volume_surge", "weight_volume_surge"),
)


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def resolve_source(path_text: str) -> Path:
    p = Path(path_text)
    candidates = [ROOT / p, EXP_ROOT / p]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(path_text)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if line.strip():
                row = json.loads(line)
                row["_source_line"] = line_no
                yield row


def load_rule_row(path: Path, rule_hash: str) -> dict[str, Any]:
    for row in iter_jsonl(path):
        if str(row.get("rulebook_hash", "")) == str(rule_hash):
            return row
    raise KeyError(f"rule not found: {path} {rule_hash}")


def load_bars(ticker: str) -> pd.DataFrame:
    path = CACHE_DIR / f"{ticker}.pkl"
    frame = pd.read_pickle(path)
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[~frame.index.isna()].sort_index()
    return frame


def activity_label(count: int, eligible_days: int) -> str:
    if count == 0:
        return "NEVER_FIRED"
    rate = count / eligible_days if eligible_days > 0 else 0.0
    if count < MIN_ACTIVE_COUNT or rate < MIN_ACTIVE_RATE:
        return "RARELY_ACTIVE"
    return "ACTIVE"


def count_le(sorted_values: np.ndarray, threshold: float) -> int:
    if not math.isfinite(threshold) or sorted_values.size == 0:
        return 0
    return int(np.searchsorted(sorted_values, threshold, side="right"))


def count_ge(sorted_values: np.ndarray, threshold: float) -> int:
    if not math.isfinite(threshold) or sorted_values.size == 0:
        return 0
    return int(sorted_values.size - np.searchsorted(sorted_values, threshold, side="left"))


def prepare_window(frame: pd.DataFrame, start: str, end: str, direction: str) -> dict[str, Any]:
    window = frame.loc[(frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))].copy()
    n = int(len(window))
    is_short = str(direction).lower() == "short"

    if is_short:
        ma_mask = (window["MA5"] < window["MA20"]) & (window["MA20"] < window["MA60"])
        prev_macd = frame["MACD"].shift(1).reindex(window.index)
        prev_signal = frame["MACD_signal"].shift(1).reindex(window.index)
        macd_mask = (window["MACD"] < window["MACD_signal"]) & (prev_macd >= prev_signal)
        bb_ratio = (window["BB_upper"] / window["Close"]).where(
            (window["BB_upper"] > 0) & (window["Close"] > 0)
        )
    else:
        ma_mask = window["Aligned_bull"].fillna(0).astype(float) != 0
        macd_mask = window["MACD_golden"].fillna(0).astype(float) != 0
        bb_ratio = (window["Close"] / window["BB_lower"]).where(
            (window["BB_lower"] > 0) & (window["Close"] > 0)
        )

    def finite_sorted(series: pd.Series) -> np.ndarray:
        values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float)
        values = values[np.isfinite(values)]
        values.sort()
        return values

    return {
        "eligible_days": n,
        "ma_count": int(ma_mask.fillna(False).sum()),
        "macd_count": int(macd_mask.fillna(False).sum()),
        "rsi_sorted": finite_sorted(window["RSI"]),
        "bb_ratio_sorted": finite_sorted(bb_ratio),
        "volume_sorted": finite_sorted(window["Volume_ratio"]),
    }


def evaluate_activity(rb: dict[str, Any], prepared: dict[str, Any]) -> dict[str, Any]:
    n = int(prepared["eligible_days"])
    is_short = str(rb.get("direction", "long")).lower() == "short"
    if is_short:
        rsi_low = max(safe_float(rb.get("rsi_low"), 0.0) + 30.0, 60.0)
        rsi_high = min(safe_float(rb.get("rsi_high"), 100.0) + 10.0, 85.0)
    else:
        rsi_low = safe_float(rb.get("rsi_low"), 0.0)
        rsi_high = safe_float(rb.get("rsi_high"), 100.0)

    rsi_sorted = prepared["rsi_sorted"]
    rsi_count = int(
        np.searchsorted(rsi_sorted, rsi_high, side="right")
        - np.searchsorted(rsi_sorted, rsi_low, side="left")
    ) if rsi_sorted.size else 0
    counts = {
        "ma": int(prepared["ma_count"]),
        "macd": int(prepared["macd_count"]),
        "rsi": rsi_count,
        "bb": count_le(prepared["bb_ratio_sorted"], safe_float(rb.get("bb_proximity"))),
        "volume_surge": count_ge(prepared["volume_sorted"], safe_float(rb.get("volume_surge_ratio"))),
    }

    result: dict[str, Any] = {"eligible_days": n}
    unvalidated: list[str] = []
    zero_never: list[str] = []
    rare_weighted: list[str] = []
    for component, weight_field in COMPONENTS:
        weight = safe_float(rb.get(weight_field), 0.0)
        count = counts[component]
        rate = count / n if n else 0.0
        label = activity_label(count, n)
        weighted = abs(weight) > EPS
        if label == "NEVER_FIRED" and weighted:
            state = "UNVALIDATED_WEIGHTED_NEVER"
            unvalidated.append(component)
        elif label == "NEVER_FIRED":
            state = "ZERO_WEIGHT_NEVER"
            zero_never.append(component)
        elif label == "RARELY_ACTIVE" and weighted:
            state = "WEIGHTED_RARE"
            rare_weighted.append(component)
        elif label == "RARELY_ACTIVE":
            state = "ZERO_WEIGHT_RARE"
        elif weighted:
            state = "WEIGHTED_ACTIVE"
        else:
            state = "ZERO_WEIGHT_ACTIVE"
        result[f"{component}_weight"] = weight
        result[f"{component}_fired_count"] = count
        result[f"{component}_fired_rate"] = rate
        result[f"{component}_activity_label"] = label
        result[f"{component}_validation_state"] = state
    result.update({
        "unvalidated_gene": bool(unvalidated),
        "unvalidated_gene_count": len(unvalidated),
        "unvalidated_components": "|".join(unvalidated),
        "zero_weight_never_count": len(zero_never),
        "zero_weight_never_components": "|".join(zero_never),
        "weighted_rare_count": len(rare_weighted),
        "weighted_rare_components": "|".join(rare_weighted),
        "weakly_validated_gene": bool(unvalidated or rare_weighted),
    })
    return result


def full_activity_fieldnames() -> list[str]:
    fields = [
        "pool_scope", "ticker", "rulebook_hash", "source_file", "source_line",
        "train_label", "train_start", "train_end", "direction", "eligible_days",
    ]
    for component, _ in COMPONENTS:
        fields += [
            f"{component}_weight", f"{component}_fired_count", f"{component}_fired_rate",
            f"{component}_activity_label", f"{component}_validation_state",
        ]
    fields += [
        "unvalidated_gene", "unvalidated_gene_count", "unvalidated_components",
        "zero_weight_never_count", "zero_weight_never_components",
        "weighted_rare_count", "weighted_rare_components", "weakly_validated_gene",
    ]
    return fields


def scan_full_pool() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    fieldnames = full_activity_fieldnames()
    pool_counter: dict[str, Counter] = defaultdict(Counter)
    component_counter: dict[tuple[str, str], Counter] = defaultdict(Counter)
    missing_cache: list[str] = []
    total_rows = 0

    with gzip.open(FULL_ACTIVITY_PATH, "wt", encoding="utf-8", newline="") as full_handle, gzip.open(
        UNVALIDATED_RULES_PATH, "wt", encoding="utf-8", newline=""
    ) as invalid_handle:
        full_writer = csv.DictWriter(full_handle, fieldnames=fieldnames)
        invalid_writer = csv.DictWriter(invalid_handle, fieldnames=fieldnames)
        full_writer.writeheader()
        invalid_writer.writeheader()

        ticker_paths: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for path in sorted(TICKER_ROOT.glob("*/stage2/rulebooks_all.jsonl")):
            ticker_paths[path.parts[-3]].append(("STAGE2_UPSTREAM", path))
        for path in sorted(TICKER_ROOT.glob("*/stage3/entry_rulebooks.jsonl")):
            ticker_paths[path.parts[-3]].append(("STAGE3_ENTRY", path))

        for ticker_no, ticker in enumerate(sorted(ticker_paths), start=1):
            cache_path = CACHE_DIR / f"{ticker}.pkl"
            if not cache_path.exists():
                missing_cache.append(ticker)
                continue
            frame = load_bars(ticker)
            prepared_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
            for pool_scope, path in ticker_paths[ticker]:
                for row in iter_jsonl(path):
                    rb = row.get("rulebook") or {}
                    if pool_scope == "STAGE2_UPSTREAM":
                        train_label = str(row.get("train_label", ""))
                        train_start = str(row.get("train_start", ""))
                        train_end = str(row.get("train_end", ""))
                    else:
                        period = row.get("train_period") or {}
                        train_label = str(period.get("label", "train_3"))
                        train_start = str(period.get("start", "2024-07-01"))
                        train_end = str(period.get("end", "2025-06-30"))
                    direction = str(rb.get("direction", "long"))
                    key = (train_start, train_end, direction)
                    if key not in prepared_cache:
                        prepared_cache[key] = prepare_window(frame, train_start, train_end, direction)
                    activity = evaluate_activity(rb, prepared_cache[key])
                    output = {
                        "pool_scope": pool_scope,
                        "ticker": ticker,
                        "rulebook_hash": str(row.get("rulebook_hash", "")),
                        "source_file": str(path.relative_to(ROOT)),
                        "source_line": int(row.get("_source_line", 0)),
                        "train_label": train_label,
                        "train_start": train_start,
                        "train_end": train_end,
                        "direction": direction,
                        **activity,
                    }
                    full_writer.writerow(output)
                    if activity["unvalidated_gene"]:
                        invalid_writer.writerow(output)
                    total_rows += 1
                    pc = pool_counter[pool_scope]
                    pc["total_rules"] += 1
                    pc["unvalidated_rules"] += int(activity["unvalidated_gene"])
                    pc["weakly_validated_rules"] += int(activity["weakly_validated_gene"])
                    pc[f"unvalidated_count_{activity['unvalidated_gene_count']}"] += 1
                    for component, _ in COMPONENTS:
                        cc = component_counter[(pool_scope, component)]
                        label = activity[f"{component}_activity_label"]
                        state = activity[f"{component}_validation_state"]
                        cc["total_rules"] += 1
                        cc[label] += 1
                        cc[state] += 1
                        cc["fired_count_sum"] += int(activity[f"{component}_fired_count"])
                full_handle.flush()
                invalid_handle.flush()
            if ticker_no % 100 == 0:
                print(f"POOL_SCAN {ticker_no}/{len(ticker_paths)} tickers rows={total_rows}", flush=True)

    pool_rows = []
    for scope, counts in sorted(pool_counter.items()):
        total = counts["total_rules"]
        pool_rows.append({
            "pool_scope": scope,
            "total_rules": total,
            "unvalidated_rules": counts["unvalidated_rules"],
            "unvalidated_rate_pct": counts["unvalidated_rules"] / total * 100 if total else math.nan,
            "weakly_validated_rules": counts["weakly_validated_rules"],
            "weakly_validated_rate_pct": counts["weakly_validated_rules"] / total * 100 if total else math.nan,
            "zero_unvalidated_gene_rules": counts["unvalidated_count_0"],
            "one_unvalidated_gene_rules": counts["unvalidated_count_1"],
            "two_plus_unvalidated_gene_rules": sum(v for k, v in counts.items() if k.startswith("unvalidated_count_") and int(k.rsplit("_", 1)[1]) >= 2),
        })
    pool_df = pd.DataFrame(pool_rows)
    pool_df.to_csv(POOL_SUMMARY_PATH, index=False)

    component_rows = []
    for (scope, component), counts in sorted(component_counter.items()):
        total = counts["total_rules"]
        component_rows.append({
            "pool_scope": scope,
            "component": component,
            "total_rules": total,
            "active_rules": counts["ACTIVE"],
            "rarely_active_rules": counts["RARELY_ACTIVE"],
            "never_fired_rules": counts["NEVER_FIRED"],
            "never_fired_rate_pct": counts["NEVER_FIRED"] / total * 100 if total else math.nan,
            "unvalidated_weighted_never_rules": counts["UNVALIDATED_WEIGHTED_NEVER"],
            "unvalidated_weighted_never_rate_pct": counts["UNVALIDATED_WEIGHTED_NEVER"] / total * 100 if total else math.nan,
            "zero_weight_never_rules": counts["ZERO_WEIGHT_NEVER"],
            "weighted_rare_rules": counts["WEIGHTED_RARE"],
            "mean_fired_count": counts["fired_count_sum"] / total if total else math.nan,
        })
    component_df = pd.DataFrame(component_rows)
    component_df.to_csv(COMPONENT_SUMMARY_PATH, index=False)
    meta = {
        "total_scanned_rules": total_rows,
        "scanned_tickers": len(ticker_paths) - len(missing_cache),
        "missing_cache_tickers": missing_cache,
    }
    return pool_df, component_df, meta


def candidate_activity() -> pd.DataFrame:
    candidates = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    vol = pd.read_csv(VOL_SUMMARY_PATH)
    vol_is = vol[vol["split"].eq("IS")].set_index("candidate_id")
    rows: list[dict[str, Any]] = []
    frame_cache: dict[str, pd.DataFrame] = {}
    prepared_cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        ticker = str(candidate["ticker"])
        source = resolve_source(str(candidate["source_file"]))
        rule_row = load_rule_row(source, str(candidate["rulebook_hash"]))
        rb = rule_row.get("rulebook") or {}
        stage = str(candidate["stage"])
        if stage == "stage3":
            entry_hash = str(candidate.get("entry_rulebook_hash") or rule_row.get("entry_rulebook_hash"))
            entry_path = TICKER_ROOT / ticker / "stage3/entry_rulebooks.jsonl"
            entry_row = load_rule_row(entry_path, entry_hash)
            period = entry_row.get("train_period") or {}
            train_label = str(period.get("label", "train_3"))
            train_start = str(period.get("start", "2024-07-01"))
            train_end = str(period.get("end", "2025-06-30"))
            activity_rule_hash = entry_hash
        else:
            origins = rule_row.get("origins") or []
            if len(origins) != 1:
                raise RuntimeError(f"unexpected stage2 origins: {candidate_id} {len(origins)}")
            origin = origins[0]
            train_label = str(origin["train_label"])
            train_start = str(origin["train_start"])
            train_end = str(origin["train_end"])
            activity_rule_hash = str(candidate["rulebook_hash"])

        if ticker not in frame_cache:
            frame_cache[ticker] = load_bars(ticker)
        direction = str(rb.get("direction", "long"))
        key = (ticker, train_start, train_end, direction)
        if key not in prepared_cache:
            prepared_cache[key] = prepare_window(frame_cache[ticker], train_start, train_end, direction)
        activity = evaluate_activity(rb, prepared_cache[key])
        vol_row = vol_is.loc[candidate_id]
        rows.append({
            "candidate_id": candidate_id,
            "ticker": ticker,
            "stage": stage,
            "bucket": str(candidate.get("bucket", "")),
            "rulebook_hash": str(candidate["rulebook_hash"]),
            "activity_rule_hash": activity_rule_hash,
            "train_label": train_label,
            "train_start": train_start,
            "train_end": train_end,
            "direction": direction,
            "exit_strategy": str(rb.get("exit_strategy", candidate.get("rulebook", {}).get("exit_strategy", ""))),
            "max_holding_days": int(safe_float(rb.get("max_holding_days"), 0)),
            "vol_group": str(vol_row["vol_group"]),
            "is_avg_std20_ann": safe_float(vol_row["avg_std20_ann"]),
            "is_avg_atr14_pct": safe_float(vol_row["avg_atr14_pct"]),
            **activity,
        })
    out = pd.DataFrame(rows).sort_values(["unvalidated_gene", "ticker", "candidate_id"], ascending=[False, True, True])
    out.to_csv(CANDIDATE_ACTIVITY_PATH, index=False)
    return out


def candidate_metrics(trades: pd.DataFrame, activity: pd.DataFrame, window: str) -> pd.DataFrame:
    work = trades.copy()
    work["signal_date"] = pd.to_datetime(work["signal_date"], errors="coerce")
    if window == "FULL_5Y":
        pass
    elif window == "FROZEN_OOS":
        work = work[work["split"].eq("OOS")]
    elif window == "POST_GENE_TRAIN_OOS":
        train_end = activity.set_index("candidate_id")["train_end"].map(pd.Timestamp)
        work = work[work["split"].eq("OOS")].copy()
        work["train_end"] = work["candidate_id"].map(train_end)
        work = work[work["signal_date"] > work["train_end"]]
    else:
        raise ValueError(window)

    grouped = work.groupby("candidate_id", sort=True)
    rows = []
    for candidate_id, group in grouped:
        rows.append({
            "candidate_id": candidate_id,
            "trade_n": len(group),
            "avg_pnl_pct": float(group["net_pct"].mean()),
            "median_pnl_pct": float(group["net_pct"].median()),
            "win_rate_pct": float((group["net_pct"] > 0).mean() * 100),
            "avg_mae_pct": float(group["MAE"].mean()),
            "avg_mfe_pct": float(group["MFE"].mean()),
            "worst_mae_pct": float(group["MAE"].min()),
            "exit_distribution": json.dumps(group["exit_reason"].fillna("").value_counts().sort_index().to_dict(), ensure_ascii=False, sort_keys=True),
        })
    metrics = pd.DataFrame(rows)
    return activity.merge(metrics, on="candidate_id", how="inner")


def bootstrap_group_diff(data: pd.DataFrame, metric: str, reps: int = BOOT_REPS) -> dict[str, Any]:
    bad = data[data["unvalidated_gene"]][metric].dropna().to_numpy(float)
    good = data[~data["unvalidated_gene"]][metric].dropna().to_numpy(float)
    point = float(bad.mean() - good.mean()) if bad.size and good.size else math.nan
    if bad.size < 2 or good.size < 2:
        return {"diff": point, "ci_low": math.nan, "ci_high": math.nan, "n_unvalidated": int(bad.size), "n_validated": int(good.size)}
    rng = np.random.default_rng(SEED + sum(ord(c) for c in metric) + len(data))
    b_idx = rng.integers(0, bad.size, size=(reps, bad.size))
    g_idx = rng.integers(0, good.size, size=(reps, good.size))
    diffs = bad[b_idx].mean(axis=1) - good[g_idx].mean(axis=1)
    return {
        "diff": point,
        "ci_low": float(np.quantile(diffs, 0.025)),
        "ci_high": float(np.quantile(diffs, 0.975)),
        "n_unvalidated": int(bad.size),
        "n_validated": int(good.size),
    }


def group_summary(data: pd.DataFrame, window: str, slice_name: str, slice_value: str) -> list[dict[str, Any]]:
    rows = []
    for label, group in data.groupby("unvalidated_gene"):
        exit_counts = Counter()
        for text in group["exit_distribution"]:
            exit_counts.update(json.loads(text))
        rows.append({
            "window": window,
            "slice_name": slice_name,
            "slice_value": slice_value,
            "unvalidated_gene": bool(label),
            "candidate_n": int(group["candidate_id"].nunique()),
            "trade_n": int(group["trade_n"].sum()),
            "candidate_equal_avg_pnl_pct": float(group["avg_pnl_pct"].mean()),
            "candidate_equal_win_rate_pct": float(group["win_rate_pct"].mean()),
            "candidate_equal_avg_mae_pct": float(group["avg_mae_pct"].mean()),
            "candidate_equal_avg_mfe_pct": float(group["avg_mfe_pct"].mean()),
            "candidate_equal_worst_mae_pct": float(group["worst_mae_pct"].min()),
            "exit_distribution": json.dumps(dict(sorted(exit_counts.items())), ensure_ascii=False, sort_keys=True),
        })
    for metric in ["avg_pnl_pct", "win_rate_pct", "avg_mae_pct", "avg_mfe_pct"]:
        diff = bootstrap_group_diff(data, metric)
        rows.append({
            "window": window,
            "slice_name": slice_name,
            "slice_value": slice_value,
            "unvalidated_gene": "DIFF_UNVALIDATED_MINUS_VALIDATED",
            "metric": metric,
            "candidate_n": diff["n_unvalidated"] + diff["n_validated"],
            "n_unvalidated": diff["n_unvalidated"],
            "n_validated": diff["n_validated"],
            "difference": diff["diff"],
            "bootstrap_ci_low": diff["ci_low"],
            "bootstrap_ci_high": diff["ci_high"],
        })
    return rows


def design_matrix(data: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    columns = [np.ones(len(data)), data["unvalidated_gene"].astype(float).to_numpy()]
    names = ["intercept", "unvalidated_gene"]
    for field in ["stage", "vol_group", "exit_strategy", "direction"]:
        levels = sorted(data[field].astype(str).unique())
        for level in levels[1:]:
            columns.append((data[field].astype(str) == level).astype(float).to_numpy())
            names.append(f"{field}={level}")
    for field in ["max_holding_days", "is_avg_std20_ann", "is_avg_atr14_pct", "trade_n"]:
        values = pd.to_numeric(data[field], errors="coerce").astype(float).to_numpy()
        if field == "trade_n":
            values = np.log1p(values)
        mean = np.nanmean(values)
        std = np.nanstd(values)
        values = np.where(np.isfinite(values), values, mean)
        values = (values - mean) / std if std > 0 else np.zeros_like(values)
        columns.append(values)
        names.append(field)
    return np.column_stack(columns), names


def adjusted_ols(data: pd.DataFrame, metric: str, reps: int = BOOT_REPS) -> dict[str, Any]:
    clean = data.dropna(subset=[metric]).reset_index(drop=True)
    x, names = design_matrix(clean)
    y = clean[metric].to_numpy(float)
    coef = np.linalg.lstsq(x, y, rcond=None)[0]
    target_idx = names.index("unvalidated_gene")
    rng = np.random.default_rng(SEED + 7000 + sum(ord(c) for c in metric) + len(clean))
    boot = []
    for _ in range(reps):
        idx = rng.integers(0, len(clean), size=len(clean))
        xb, yb = x[idx], y[idx]
        if len(np.unique(xb[:, target_idx])) < 2:
            continue
        boot.append(float(np.linalg.lstsq(xb, yb, rcond=None)[0][target_idx]))
    arr = np.asarray(boot, dtype=float)
    return {
        "method": "candidate_level_ols_bootstrap",
        "metric": metric,
        "estimate": float(coef[target_idx]),
        "ci_low": float(np.quantile(arr, 0.025)) if arr.size else math.nan,
        "ci_high": float(np.quantile(arr, 0.975)) if arr.size else math.nan,
        "candidate_n": len(clean),
        "bootstrap_valid_reps": int(arr.size),
        "controls": "stage|vol_group|exit_strategy|direction|max_holding_days|IS_volatility|IS_ATR|log_trade_n",
    }


def exact_strata_effect(data: pd.DataFrame, metric: str, reps: int = BOOT_REPS) -> dict[str, Any]:
    strata_fields = ["stage", "vol_group", "exit_strategy", "direction"]
    usable = []
    for key, group in data.groupby(strata_fields, dropna=False):
        bad = group[group["unvalidated_gene"]][metric].dropna().to_numpy(float)
        good = group[~group["unvalidated_gene"]][metric].dropna().to_numpy(float)
        if bad.size and good.size:
            usable.append((key, bad, good, min(bad.size, good.size)))
    if not usable:
        return {"method": "exact_strata", "metric": metric, "estimate": math.nan, "ci_low": math.nan, "ci_high": math.nan, "candidate_coverage": 0, "strata_n": 0}
    weights = np.asarray([item[3] for item in usable], dtype=float)
    points = np.asarray([item[1].mean() - item[2].mean() for item in usable], dtype=float)
    estimate = float(np.average(points, weights=weights))
    rng = np.random.default_rng(SEED + 9000 + sum(ord(c) for c in metric) + len(data))
    boot = np.empty(reps, dtype=float)
    for rep in range(reps):
        diffs = []
        for _, bad, good, _ in usable:
            diffs.append(rng.choice(bad, size=bad.size, replace=True).mean() - rng.choice(good, size=good.size, replace=True).mean())
        boot[rep] = np.average(np.asarray(diffs), weights=weights)
    covered_ids = set()
    for key, _, _, _ in usable:
        mask = np.ones(len(data), dtype=bool)
        for field, value in zip(strata_fields, key if isinstance(key, tuple) else (key,)):
            mask &= data[field].astype(str).to_numpy() == str(value)
        covered_ids.update(data.loc[mask, "candidate_id"].tolist())
    return {
        "method": "exact_strata",
        "metric": metric,
        "estimate": estimate,
        "ci_low": float(np.quantile(boot, 0.025)),
        "ci_high": float(np.quantile(boot, 0.975)),
        "candidate_coverage": len(covered_ids),
        "strata_n": len(usable),
        "strata": "stage|vol_group|exit_strategy|direction",
    }


def performance_analysis(activity: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    trades = pd.read_csv(TRADE_PATH)
    perf_rows: list[dict[str, Any]] = []
    control_rows: list[dict[str, Any]] = []
    candidate_windows: dict[str, pd.DataFrame] = {}

    for window in ["FULL_5Y", "FROZEN_OOS", "POST_GENE_TRAIN_OOS"]:
        metrics = candidate_metrics(trades, activity, window)
        candidate_windows[window] = metrics
        perf_rows.extend(group_summary(metrics, window, "ALL", "ALL"))
        for field in ["vol_group", "stage", "exit_strategy"]:
            for value, group in metrics.groupby(field):
                if group["unvalidated_gene"].nunique() == 2:
                    perf_rows.extend(group_summary(group, window, field, str(value)))
        for metric in ["avg_pnl_pct", "win_rate_pct", "avg_mae_pct", "avg_mfe_pct"]:
            adjusted = adjusted_ols(metrics, metric)
            adjusted.update({"window": window})
            control_rows.append(adjusted)
            exact = exact_strata_effect(metrics, metric)
            exact.update({"window": window})
            control_rows.append(exact)

    # Realized exit-reason comparison is descriptive because exit reason is post-entry.
    oos = trades[trades["split"].eq("OOS")].merge(activity[["candidate_id", "unvalidated_gene"]], on="candidate_id", how="inner")
    for reason, group in oos.groupby("exit_reason"):
        candidate_reason = group.groupby(["candidate_id", "unvalidated_gene"]).agg(
            trade_n=("net_pct", "size"), avg_pnl_pct=("net_pct", "mean"),
            win_rate_pct=("net_pct", lambda s: float((s > 0).mean() * 100)),
            avg_mae_pct=("MAE", "mean"), avg_mfe_pct=("MFE", "mean"),
        ).reset_index()
        if candidate_reason["unvalidated_gene"].nunique() == 2:
            for metric in ["avg_pnl_pct", "win_rate_pct", "avg_mae_pct", "avg_mfe_pct"]:
                diff = bootstrap_group_diff(candidate_reason, metric)
                control_rows.append({
                    "window": "FROZEN_OOS",
                    "method": "realized_exit_reason_descriptive",
                    "stratum": str(reason),
                    "metric": metric,
                    "estimate": diff["diff"],
                    "ci_low": diff["ci_low"],
                    "ci_high": diff["ci_high"],
                    "candidate_coverage": diff["n_unvalidated"] + diff["n_validated"],
                    "note": "post-entry outcome stratum; not causal control",
                })

    perf_df = pd.DataFrame(perf_rows)
    control_df = pd.DataFrame(control_rows)
    perf_df.to_csv(PERFORMANCE_PATH, index=False)
    control_df.to_csv(CONTROL_PATH, index=False)

    def lookup_diff(window: str, metric: str, slice_name: str = "ALL", slice_value: str = "ALL") -> dict[str, float]:
        row = perf_df[
            perf_df["window"].eq(window)
            & perf_df["slice_name"].eq(slice_name)
            & perf_df["slice_value"].eq(slice_value)
            & perf_df["unvalidated_gene"].eq("DIFF_UNVALIDATED_MINUS_VALIDATED")
            & perf_df["metric"].eq(metric)
        ]
        if row.empty:
            return {}
        item = row.iloc[0]
        return {k: safe_float(item.get(k)) for k in ["difference", "bootstrap_ci_low", "bootstrap_ci_high", "n_unvalidated", "n_validated"]}

    def lookup_control(window: str, method: str, metric: str) -> dict[str, float]:
        row = control_df[
            control_df["window"].eq(window)
            & control_df["method"].eq(method)
            & control_df["metric"].eq(metric)
        ]
        if row.empty:
            return {}
        item = row.iloc[0]
        return {k: safe_float(item.get(k)) for k in ["estimate", "ci_low", "ci_high", "candidate_coverage", "strata_n"]}

    primary = lookup_diff("FROZEN_OOS", "avg_pnl_pct")
    adjusted = lookup_control("FROZEN_OOS", "candidate_level_ols_bootstrap", "avg_pnl_pct")
    exact = lookup_control("FROZEN_OOS", "exact_strata", "avg_pnl_pct")
    strict = lookup_diff("POST_GENE_TRAIN_OOS", "avg_pnl_pct")
    high_vol = lookup_diff("FROZEN_OOS", "avg_pnl_pct", "vol_group", "HIGH_VOL")

    sufficient = primary.get("n_unvalidated", 0) >= 10 and primary.get("n_validated", 0) >= 20
    primary_negative = sufficient and primary.get("bootstrap_ci_high", math.inf) < 0
    adjusted_negative = adjusted.get("ci_high", math.inf) < 0
    exact_negative = exact.get("ci_high", math.inf) < 0
    replication_negative = (
        strict.get("bootstrap_ci_high", math.inf) < 0
        or high_vol.get("bootstrap_ci_high", math.inf) < 0
        or exact_negative
    )
    if primary_negative and adjusted_negative and replication_negative:
        verdict = "PREDICTIVE"
    elif sufficient and primary.get("bootstrap_ci_low", -math.inf) > -0.5 and adjusted.get("ci_low", -math.inf) > -0.5:
        verdict = "NOT_PREDICTIVE"
    else:
        verdict = "INCONCLUSIVE"

    meta = {
        "verdict": verdict,
        "predeclared_rule": {
            "PREDICTIVE": "FROZEN_OOS candidate-equal PnL 95% CI<0, adjusted OLS-bootstrap CI<0, and at least one of post-train/HIGH_VOL/exact-strata CI<0; n_unvalidated>=10,n_validated>=20",
            "NOT_PREDICTIVE": "sample sufficient and both primary/adjusted lower CI > -0.5 percentage points",
            "otherwise": "INCONCLUSIVE",
        },
        "primary_oos": primary,
        "adjusted_oos": adjusted,
        "exact_strata_oos": exact,
        "post_gene_train_oos": strict,
        "high_vol_oos": high_vol,
        "window_candidate_counts": {name: int(frame["candidate_id"].nunique()) for name, frame in candidate_windows.items()},
        "window_trade_counts": {name: int(frame["trade_n"].sum()) for name, frame in candidate_windows.items()},
    }
    return perf_df, control_df, meta


def main() -> int:
    pool_df, component_df, pool_meta = scan_full_pool()
    activity = candidate_activity()
    perf_df, control_df, performance_meta = performance_analysis(activity)

    boil = activity[activity["ticker"].eq("BOIL")].iloc[0]
    parity = {
        "candidate_id": str(boil["candidate_id"]),
        "train_start": str(boil["train_start"]),
        "train_end": str(boil["train_end"]),
        "eligible_days": int(boil["eligible_days"]),
        "volume_surge_ratio": safe_float(boil["volume_surge_weight"]),
        "volume_surge_fired_count": int(boil["volume_surge_fired_count"]),
        "volume_surge_activity_label": str(boil["volume_surge_activity_label"]),
        "expected_count": 0,
        "pass": int(boil["volume_surge_fired_count"]) == 0,
    }

    summary = {
        "metadata": {
            "seed": SEED,
            "bootstrap_reps": BOOT_REPS,
            "activity_components": [name for name, _ in COMPONENTS],
            "activity_thresholds": {
                "NEVER_FIRED": "count == 0",
                "RARELY_ACTIVE": f"count 1..{MIN_ACTIVE_COUNT - 1} or rate < {MIN_ACTIVE_RATE}",
                "ACTIVE": f"count >= {MIN_ACTIVE_COUNT} and rate >= {MIN_ACTIVE_RATE}",
                "UNVALIDATED_GENE": f"any NEVER_FIRED component with abs(weight) > {EPS}",
                "ZERO_WEIGHT_NEVER": f"NEVER_FIRED and abs(weight) <= {EPS}; separated as harmless dormant term",
            },
            "full_pool_sources": [
                "exp_batch_stage123_2009_20260616_full/tickers/*/stage2/rulebooks_all.jsonl",
                "exp_batch_stage123_2009_20260616_full/tickers/*/stage3/entry_rulebooks.jsonl",
            ],
            "daily_bar_source": str(CACHE_DIR.relative_to(ROOT)),
            "frozen_trade_source": str(TRADE_PATH.relative_to(ROOT)),
        },
        "pool_scan": pool_meta,
        "pool_summary": pool_df.to_dict(orient="records"),
        "component_summary": component_df.to_dict(orient="records"),
        "candidate_summary": {
            "candidate_n": int(len(activity)),
            "unvalidated_candidate_n": int(activity["unvalidated_gene"].sum()),
            "unvalidated_candidate_rate_pct": float(activity["unvalidated_gene"].mean() * 100),
            "weakly_validated_candidate_n": int(activity["weakly_validated_gene"].sum()),
            "unvalidated_component_counts": Counter(
                component for text in activity.loc[activity["unvalidated_gene"], "unvalidated_components"] for component in str(text).split("|") if component
            ),
        },
        "boil_parity": parity,
        "performance": performance_meta,
        "limitations": [
            "FULL_5Y includes periods used for training/selection and is descriptive only.",
            "Frozen OOS is calendar 2025-01-01..2026-07-02 and overlaps Stage3 train_3 through 2025-06-30.",
            "POST_GENE_TRAIN_OOS removes dates on/before each gene training end but candidate/exit selection leakage can remain.",
            "Exact ticker fixed effects are not identifiable because 91 of 93 tickers have one candidate.",
            "Realized exit-reason strata are post-entry outcomes and are descriptive, not causal controls.",
        ],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("@@SUMMARY@@")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
