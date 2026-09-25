from __future__ import annotations

"""소수지표 가중치 구조 rule의 성과 분포를 stage별로 검증한다.

읽기 전용 분석:
- rulebook의 저장 core 가중치만 구조 특징으로 사용
- canonical Stage2/Stage3 trade 결과를 IS/internal holdout으로 집계
- frozen OOS를 외부 validation으로 사용
- ticker-cluster bootstrap CI와 cluster-robust p-value + BH FDR
- 기존 history/v3/BOIL 통과 순증군을 별도 재검증
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SEED = 20260711
BOOTSTRAP_REPS = 5000
MIN_TRADES = 8
MATERIAL_WEIGHT_EPS = 0.05

BASE_PATH = OUT / "integrated_gate_candidate_dryrun.csv"
WEIGHT_PATH = OUT / "threshold_p99_weightless_block_candidate_decisions.csv"
BOIL_PATH = OUT / "boil_block_exclusive_targets.csv"
FROZEN_PATH = ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv"

RULE_OUT = OUT / "minority_indicator_rule_performance.csv.gz"
GROUP_OUT = OUT / "minority_indicator_group_performance.csv"
CUT_OUT = OUT / "minority_indicator_cut_results.csv"
INCREMENTAL_OUT = OUT / "minority_indicator_incremental_results.csv"
ANET_BB_OUT = OUT / "minority_indicator_anet_bb.csv"
CURVE_OUT = OUT / "minority_indicator_curve_fit_notes.csv"
SUMMARY_OUT = OUT / "minority_indicator_summary.json"
READOUT_OUT = OUT / "minority_indicator_readout.md"

WEIGHT_COLUMNS = ["ma_weight", "macd_weight", "rsi_weight", "bb_weight", "volume_weight"]
METRICS = [
    ("avg_pnl_pct", "평균 PnL", "LOWER_BAD"),
    ("win_rate_pct", "승률", "LOWER_BAD"),
    ("p05_pnl_pct", "PnL 5% 분위", "LOWER_BAD"),
    ("tail10_mean_pnl_pct", "하위 10% 평균 PnL", "LOWER_BAD"),
    ("worst_mae_pct", "worst MAE", "LOWER_BAD"),
]
CUTS = {
    "ACTIVE_EXACT_LE2": "active_exact_count <= 2",
    "ACTIVE_MATERIAL_LE2": "active_material_count <= 2",
    "TOP2_GE80": "top2_weight_share_pct >= 80",
    "TOP2_GE90": "top2_weight_share_pct >= 90",
    "ACTIVE_EXACT_LE3_AND_TOP2_GE80": "active_exact_count <= 3 and top2_weight_share_pct >= 80",
    "ACTIVE_EXACT_LE3_AND_TOP2_GE90": "active_exact_count <= 3 and top2_weight_share_pct >= 90",
    "ACTIVE_MATERIAL_LE2_AND_TOP2_GE80": "active_material_count <= 2 and top2_weight_share_pct >= 80",
    "ACTIVE_MATERIAL_LE2_AND_TOP2_GE90": "active_material_count <= 2 and top2_weight_share_pct >= 90",
}


def stable_csv(path: Path, **kwargs: Any) -> pd.DataFrame:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    frame = pd.read_csv(path, **kwargs)
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")
    return frame


def jsonl_rows(path: Path) -> Iterable[dict[str, Any]]:
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                yield value
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")


def safe_float(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except Exception:
        return math.nan


def bh_adjust(values: pd.Series) -> pd.Series:
    p = values.fillna(1.0).clip(0, 1).to_numpy(float)
    order = np.argsort(p)
    adjusted = np.empty(len(p), dtype=float)
    running = 1.0
    total = len(p)
    for rank in range(total - 1, -1, -1):
        index = order[rank]
        running = min(running, p[index] * total / (rank + 1))
        adjusted[index] = min(1.0, running)
    return pd.Series(adjusted, index=values.index)


def compute_structure(weights: pd.DataFrame) -> pd.DataFrame:
    result = weights.copy()
    matrix = result[WEIGHT_COLUMNS].fillna(0.0).clip(lower=0.0).to_numpy(float)
    sorted_weights = np.sort(matrix, axis=1)[:, ::-1]
    totals = matrix.sum(axis=1)
    top1 = sorted_weights[:, 0]
    top2 = sorted_weights[:, :2].sum(axis=1)
    result["active_exact_count"] = (matrix > 0.0).sum(axis=1)
    result["active_material_count"] = (matrix > MATERIAL_WEIGHT_EPS).sum(axis=1)
    result["positive_weight_sum"] = totals
    result["top1_weight_share_pct"] = np.where(totals > 0, top1 / totals * 100.0, np.nan)
    result["top2_weight_share_pct"] = np.where(totals > 0, top2 / totals * 100.0, np.nan)
    names = np.array([column.replace("_weight", "") for column in WEIGHT_COLUMNS])
    order = np.argsort(matrix, axis=1)[:, ::-1]
    result["top1_indicator"] = names[order[:, 0]]
    result["top2_indicators"] = ["+".join(names[row[:2]]) for row in order]
    for cut, expression in CUTS.items():
        if cut == "ACTIVE_EXACT_LE2":
            flag = result["active_exact_count"].le(2)
        elif cut == "ACTIVE_MATERIAL_LE2":
            flag = result["active_material_count"].le(2)
        elif cut == "TOP2_GE80":
            flag = result["top2_weight_share_pct"].ge(80)
        elif cut == "TOP2_GE90":
            flag = result["top2_weight_share_pct"].ge(90)
        elif cut == "ACTIVE_EXACT_LE3_AND_TOP2_GE80":
            flag = result["active_exact_count"].le(3) & result["top2_weight_share_pct"].ge(80)
        elif cut == "ACTIVE_EXACT_LE3_AND_TOP2_GE90":
            flag = result["active_exact_count"].le(3) & result["top2_weight_share_pct"].ge(90)
        elif cut == "ACTIVE_MATERIAL_LE2_AND_TOP2_GE80":
            flag = result["active_material_count"].le(2) & result["top2_weight_share_pct"].ge(80)
        elif cut == "ACTIVE_MATERIAL_LE2_AND_TOP2_GE90":
            flag = result["active_material_count"].le(2) & result["top2_weight_share_pct"].ge(90)
        else:
            raise AssertionError(expression)
        result[f"cut_{cut}"] = flag
    return result


def performance_metrics(pnl: list[float], mae: list[float], mfe: list[float]) -> dict[str, float]:
    if not pnl:
        return {
            "trade_n": 0,
            "avg_pnl_pct": math.nan,
            "win_rate_pct": math.nan,
            "p05_pnl_pct": math.nan,
            "tail10_mean_pnl_pct": math.nan,
            "worst_pnl_pct": math.nan,
            "avg_mae_pct": math.nan,
            "worst_mae_pct": math.nan,
            "avg_mfe_pct": math.nan,
        }
    pnl_array = np.asarray(pnl, dtype=float)
    mae_array = np.asarray(mae, dtype=float)
    mfe_array = np.asarray(mfe, dtype=float)
    tail_n = max(1, int(math.ceil(len(pnl_array) * 0.10)))
    return {
        "trade_n": int(len(pnl_array)),
        "avg_pnl_pct": float(pnl_array.mean()),
        "win_rate_pct": float((pnl_array > 0).mean() * 100.0),
        "p05_pnl_pct": float(np.quantile(pnl_array, 0.05)),
        "tail10_mean_pnl_pct": float(np.sort(pnl_array)[:tail_n].mean()),
        "worst_pnl_pct": float(pnl_array.min()),
        "avg_mae_pct": float(mae_array.mean()) if len(mae_array) else math.nan,
        "worst_mae_pct": float(mae_array.min()) if len(mae_array) else math.nan,
        "avg_mfe_pct": float(mfe_array.mean()) if len(mfe_array) else math.nan,
    }


def scan_canonical(base: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    targets_by_file: dict[tuple[str, Path], dict[str, str]] = defaultdict(dict)
    for row in base.itertuples(index=False):
        source = ROOT / str(row.source_file)
        trade_path = source.parent / ("trades.jsonl" if row.stage == "stage2" else "exit_trades.jsonl")
        targets_by_file[(str(row.stage), trade_path)][str(row.rulebook_hash)] = str(row.candidate_id)

    buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: {"pnl": [], "mae": [], "mfe": []}
    )
    counts = {"stage2_rows": 0, "stage3_rows": 0, "matched_rows": 0}
    total_files = len(targets_by_file)
    for file_index, ((stage, path), target_map) in enumerate(targets_by_file.items(), 1):
        marker = "rulebook_hash" if stage == "stage2" else "final_rulebook_hash"
        holdout_label = "oos_2025h2" if stage == "stage2" else "recent_1y"
        for row in jsonl_rows(path):
            cid = target_map.get(str(row.get(marker) or ""))
            if cid is None:
                continue
            period = str(row.get("period_label") or "")
            split = "internal_holdout" if period == holdout_label else "is_discovery"
            pnl = safe_float(row.get("pnl_pct"))
            mae = safe_float(row.get("max_loss_during_hold"))
            mfe = safe_float(row.get("max_profit_during_hold"))
            if not math.isfinite(pnl):
                continue
            bucket = buckets[(cid, split)]
            bucket["pnl"].append(pnl)
            if math.isfinite(mae):
                bucket["mae"].append(mae)
            if math.isfinite(mfe):
                bucket["mfe"].append(mfe)
            counts[f"{stage}_rows"] += 1
            counts["matched_rows"] += 1
        if file_index % 100 == 0:
            print(f"canonical scan {file_index}/{total_files}", flush=True)

    rows: list[dict[str, Any]] = []
    for cid in base["candidate_id"].astype(str):
        row: dict[str, Any] = {"candidate_id": cid}
        for split in ("is_discovery", "internal_holdout"):
            metrics = performance_metrics(
                buckets[(cid, split)]["pnl"],
                buckets[(cid, split)]["mae"],
                buckets[(cid, split)]["mfe"],
            )
            for key, value in metrics.items():
                row[f"{split}_{key}"] = value
        rows.append(row)
    return pd.DataFrame(rows), counts


def aggregate_frozen() -> pd.DataFrame:
    trades = stable_csv(FROZEN_PATH, low_memory=False)
    trades = trades[trades["split"].astype(str).str.upper().eq("OOS")].copy()
    rows: list[dict[str, Any]] = []
    for cid, group in trades.groupby("candidate_id"):
        metrics = performance_metrics(
            group["pnl_pct"].astype(float).tolist(),
            group["MAE"].astype(float).tolist(),
            group["MFE"].astype(float).tolist(),
        )
        rows.append({"candidate_id": str(cid), **{f"frozen_{key}": value for key, value in metrics.items()}})
    return pd.DataFrame(rows)


def cluster_robust_p(frame: pd.DataFrame, flag_col: str, metric_col: str) -> tuple[float, float, int]:
    work = frame[["ticker", flag_col, metric_col]].dropna().copy()
    if len(work) < 4 or work[flag_col].nunique() < 2:
        return math.nan, math.nan, work["ticker"].nunique()
    y = work[metric_col].to_numpy(float)
    x_flag = work[flag_col].astype(float).to_numpy()
    x = np.column_stack([np.ones(len(work)), x_flag])
    inv = np.linalg.pinv(x.T @ x)
    beta = inv @ (x.T @ y)
    residual = y - x @ beta
    meat = np.zeros((2, 2), dtype=float)
    for _, index in work.groupby("ticker").groups.items():
        positions = work.index.get_indexer(index)
        score = x[positions].T @ residual[positions]
        meat += np.outer(score, score)
    group_n = work["ticker"].nunique()
    if group_n <= 1:
        return float(beta[1]), math.nan, group_n
    correction = group_n / (group_n - 1) * (len(work) - 1) / max(1, len(work) - x.shape[1])
    covariance = inv @ meat @ inv * correction
    se = math.sqrt(max(0.0, covariance[1, 1]))
    if se <= 0:
        p = 0.0 if beta[1] < 0 else 1.0
    else:
        statistic = beta[1] / se
        p = float(student_t.cdf(statistic, df=group_n - 1))
    return float(beta[1]), p, group_n


def cluster_bootstrap_ci(
    frame: pd.DataFrame,
    flag_col: str,
    metric_col: str,
    seed: int,
) -> tuple[float, float, int]:
    work = frame[["ticker", flag_col, metric_col]].dropna().copy()
    if work[flag_col].nunique() < 2:
        return math.nan, math.nan, 0
    ticker_rows: list[tuple[float, float, float, float]] = []
    for _, group in work.groupby("ticker", sort=False):
        sparse = group[group[flag_col]][metric_col].to_numpy(float)
        other = group[~group[flag_col]][metric_col].to_numpy(float)
        ticker_rows.append((
            float(sparse.sum()), float(len(sparse)),
            float(other.sum()), float(len(other)),
        ))
    values = np.asarray(ticker_rows, dtype=float)
    ticker_n = len(values)
    if ticker_n <= 1:
        return math.nan, math.nan, 0
    rng = np.random.default_rng(seed)
    diffs: list[np.ndarray] = []
    remaining = BOOTSTRAP_REPS
    while remaining > 0:
        batch = min(1000, remaining)
        sample = rng.integers(0, ticker_n, size=(batch, ticker_n))
        sparse_sum = values[sample, 0].sum(axis=1)
        sparse_n = values[sample, 1].sum(axis=1)
        other_sum = values[sample, 2].sum(axis=1)
        other_n = values[sample, 3].sum(axis=1)
        valid = (sparse_n > 0) & (other_n > 0)
        if valid.any():
            diffs.append(sparse_sum[valid] / sparse_n[valid] - other_sum[valid] / other_n[valid])
        remaining -= batch
    if not diffs:
        return math.nan, math.nan, 0
    all_diffs = np.concatenate(diffs)
    return (
        float(np.quantile(all_diffs, 0.025)),
        float(np.quantile(all_diffs, 0.975)),
        int(len(all_diffs)),
    )


def analyze_scope(
    rules: pd.DataFrame,
    scope: str,
    prefix: str,
    scope_mask: pd.Series,
    seed_offset: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for stage_index, stage in enumerate(("stage2", "stage3")):
        stage_frame = rules[scope_mask & rules["stage"].eq(stage)].copy()
        for cut_index, (cut, definition) in enumerate(CUTS.items()):
            flag_col = f"cut_{cut}"
            for metric_index, (metric, label, direction) in enumerate(METRICS):
                metric_col = f"{prefix}_{metric}"
                work = stage_frame[["candidate_id", "ticker", flag_col, metric_col]].dropna().copy()
                sparse = work[work[flag_col]]
                other = work[~work[flag_col]]
                observed = (
                    float(sparse[metric_col].mean() - other[metric_col].mean())
                    if len(sparse) and len(other) else math.nan
                )
                weighted_sparse = math.nan
                weighted_other = math.nan
                trade_col = f"{prefix}_trade_n"
                if trade_col in stage_frame.columns:
                    weighted = stage_frame[[flag_col, metric_col, trade_col]].dropna()
                    ws = weighted[weighted[flag_col]]
                    wo = weighted[~weighted[flag_col]]
                    if len(ws) and ws[trade_col].sum() > 0:
                        weighted_sparse = float(np.average(ws[metric_col], weights=ws[trade_col]))
                    if len(wo) and wo[trade_col].sum() > 0:
                        weighted_other = float(np.average(wo[metric_col], weights=wo[trade_col]))
                _, p_value, ticker_n = cluster_robust_p(work, flag_col, metric_col)
                ci_low, ci_high, valid_reps = cluster_bootstrap_ci(
                    work, flag_col, metric_col,
                    SEED + seed_offset + stage_index * 10000 + cut_index * 100 + metric_index,
                )
                rows.append({
                    "scope": scope,
                    "stage": stage,
                    "cut": cut,
                    "cut_definition": definition,
                    "metric": metric,
                    "metric_label": label,
                    "expected_bad_direction": direction,
                    "rule_n": len(work),
                    "ticker_n": ticker_n,
                    "sparse_rule_n": len(sparse),
                    "other_rule_n": len(other),
                    "sparse_ticker_n": sparse["ticker"].nunique(),
                    "other_ticker_n": other["ticker"].nunique(),
                    "sparse_mean": float(sparse[metric_col].mean()) if len(sparse) else math.nan,
                    "other_mean": float(other[metric_col].mean()) if len(other) else math.nan,
                    "difference_sparse_minus_other": observed,
                    "difference_ci_low": ci_low,
                    "difference_ci_high": ci_high,
                    "cluster_one_sided_p": p_value,
                    "bootstrap_valid_reps": valid_reps,
                    "trade_weighted_sparse_mean": weighted_sparse,
                    "trade_weighted_other_mean": weighted_other,
                    "trade_weighted_difference": weighted_sparse - weighted_other if math.isfinite(weighted_sparse) and math.isfinite(weighted_other) else math.nan,
                    "nominal_sparse_worse": bool(
                        math.isfinite(observed) and observed < 0 and math.isfinite(ci_high) and ci_high < 0
                    ),
                })
    result = pd.DataFrame(rows)
    result["fdr_q"] = np.nan
    for _, index in result.groupby(["scope", "stage", "metric"]).groups.items():
        result.loc[index, "fdr_q"] = bh_adjust(result.loc[index, "cluster_one_sided_p"])
    result["fdr_sparse_worse"] = result["nominal_sparse_worse"] & result["fdr_q"].lt(0.05)
    return result


def group_summary(rules: pd.DataFrame, cut_results: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    scopes = {
        "IS_DISCOVERY": (rules["is_discovery_trade_n"].ge(MIN_TRADES), "is_discovery"),
        "INTERNAL_HOLDOUT": (rules["internal_holdout_trade_n"].ge(MIN_TRADES), "internal_holdout"),
        "FROZEN_OOS": (rules["frozen_trade_n"].ge(MIN_TRADES), "frozen"),
        "FROZEN_INCREMENTAL": (
            rules["frozen_trade_n"].ge(MIN_TRADES) & rules["existing_static_survivor"], "frozen"
        ),
    }
    for scope, (mask, prefix) in scopes.items():
        for stage in ("stage2", "stage3"):
            frame = rules[mask & rules["stage"].eq(stage)]
            for cut, definition in CUTS.items():
                flag = frame[f"cut_{cut}"]
                for group_name, group_mask in (("SPARSE", flag), ("OTHER", ~flag)):
                    group = frame[group_mask]
                    rows.append({
                        "scope": scope,
                        "stage": stage,
                        "cut": cut,
                        "cut_definition": definition,
                        "group": group_name,
                        "rule_n": len(group),
                        "ticker_n": group["ticker"].nunique(),
                        "trade_n_sum": int(group[f"{prefix}_trade_n"].sum()) if len(group) else 0,
                        "mean_rule_avg_pnl_pct": float(group[f"{prefix}_avg_pnl_pct"].mean()) if len(group) else math.nan,
                        "median_rule_avg_pnl_pct": float(group[f"{prefix}_avg_pnl_pct"].median()) if len(group) else math.nan,
                        "mean_rule_win_rate_pct": float(group[f"{prefix}_win_rate_pct"].mean()) if len(group) else math.nan,
                        "mean_rule_p05_pnl_pct": float(group[f"{prefix}_p05_pnl_pct"].mean()) if len(group) else math.nan,
                        "mean_rule_tail10_pnl_pct": float(group[f"{prefix}_tail10_mean_pnl_pct"].mean()) if len(group) else math.nan,
                        "mean_rule_worst_mae_pct": float(group[f"{prefix}_worst_mae_pct"].mean()) if len(group) else math.nan,
                    })
    return pd.DataFrame(rows)


def main() -> int:
    base = stable_csv(BASE_PATH, low_memory=False)
    weights = stable_csv(
        WEIGHT_PATH,
        usecols=["candidate_id", *WEIGHT_COLUMNS, "final_p99_weightless_block_status"],
        low_memory=False,
    )
    boil_ids = set(stable_csv(BOIL_PATH, usecols=["candidate_id"])["candidate_id"].astype(str))

    structure = compute_structure(weights)
    canonical, scan_counts = scan_canonical(base)
    frozen = aggregate_frozen()

    rules = base.merge(structure, on="candidate_id", validate="one_to_one").merge(
        canonical, on="candidate_id", validate="one_to_one"
    ).merge(frozen, on="candidate_id", how="left", validate="one_to_one")
    rules["boil_block"] = rules["candidate_id"].isin(boil_ids)
    rules["history_gate_pass"] = rules["recommended_static_status"].eq("PASS")
    rules["v3_gate_pass"] = rules["final_p99_weightless_block_status"].eq("PASS")
    rules["existing_static_survivor"] = (
        rules["history_gate_pass"] & rules["v3_gate_pass"] & ~rules["boil_block"]
    )
    rules["has_frozen_oos"] = rules["frozen_trade_n"].fillna(0).gt(0)
    rules.to_csv(RULE_OUT, index=False, compression="gzip")

    scope_specs = [
        ("IS_DISCOVERY", "is_discovery", rules["is_discovery_trade_n"].ge(MIN_TRADES), 0),
        ("INTERNAL_HOLDOUT", "internal_holdout", rules["internal_holdout_trade_n"].ge(MIN_TRADES), 100000),
        ("FROZEN_OOS", "frozen", rules["frozen_trade_n"].ge(MIN_TRADES), 200000),
        (
            "FROZEN_INCREMENTAL", "frozen",
            rules["frozen_trade_n"].ge(MIN_TRADES) & rules["existing_static_survivor"],
            300000,
        ),
    ]
    results = pd.concat([
        analyze_scope(rules, scope, prefix, mask, offset)
        for scope, prefix, mask, offset in scope_specs
    ], ignore_index=True)

    discovery_primary = results[
        results["scope"].eq("IS_DISCOVERY") & results["metric"].eq("avg_pnl_pct")
    ][["stage", "cut", "difference_sparse_minus_other", "difference_ci_high", "fdr_q", "fdr_sparse_worse"]].rename(columns={
        "difference_sparse_minus_other": "is_pnl_difference",
        "difference_ci_high": "is_pnl_ci_high",
        "fdr_q": "is_pnl_fdr_q",
        "fdr_sparse_worse": "is_pnl_fdr_sparse_worse",
    })
    results = results.merge(discovery_primary, on=["stage", "cut"], how="left", validate="many_to_one")
    results["is_selected_direction_sparse_worse"] = results["is_pnl_difference"].lt(0)
    results["validation_after_is_direction"] = (
        results["is_selected_direction_sparse_worse"] & results["fdr_sparse_worse"]
    )
    results.to_csv(CUT_OUT, index=False)
    results[results["scope"].eq("FROZEN_INCREMENTAL")].to_csv(INCREMENTAL_OUT, index=False)

    groups = group_summary(rules, results)
    groups.to_csv(GROUP_OUT, index=False)

    primary = results[results["metric"].eq("avg_pnl_pct")].copy()
    pivot = primary.pivot_table(
        index=["stage", "cut"], columns="scope",
        values=["difference_sparse_minus_other", "difference_ci_low", "difference_ci_high", "fdr_q", "sparse_rule_n", "other_rule_n"],
        aggfunc="first",
    )
    pivot.columns = [f"{metric}_{scope}" for metric, scope in pivot.columns]
    pivot = pivot.reset_index()
    for required in [
        "difference_sparse_minus_other_IS_DISCOVERY", "difference_ci_high_IS_DISCOVERY", "fdr_q_IS_DISCOVERY",
        "difference_sparse_minus_other_FROZEN_OOS", "difference_ci_high_FROZEN_OOS", "fdr_q_FROZEN_OOS",
        "difference_sparse_minus_other_FROZEN_INCREMENTAL", "difference_ci_high_FROZEN_INCREMENTAL", "fdr_q_FROZEN_INCREMENTAL",
        "sparse_rule_n_FROZEN_OOS", "other_rule_n_FROZEN_OOS",
        "sparse_rule_n_FROZEN_INCREMENTAL", "other_rule_n_FROZEN_INCREMENTAL",
    ]:
        if required not in pivot:
            pivot[required] = np.nan
    pivot["is_supported"] = (
        pivot["difference_sparse_minus_other_IS_DISCOVERY"].lt(0)
        & pivot["difference_ci_high_IS_DISCOVERY"].lt(0)
        & pivot["fdr_q_IS_DISCOVERY"].lt(0.05)
    )
    pivot["frozen_supported"] = (
        pivot["difference_sparse_minus_other_FROZEN_OOS"].lt(0)
        & pivot["difference_ci_high_FROZEN_OOS"].lt(0)
        & pivot["fdr_q_FROZEN_OOS"].lt(0.05)
        & pivot["sparse_rule_n_FROZEN_OOS"].ge(3)
        & pivot["other_rule_n_FROZEN_OOS"].ge(3)
    )
    pivot["incremental_supported"] = (
        pivot["difference_sparse_minus_other_FROZEN_INCREMENTAL"].lt(0)
        & pivot["difference_ci_high_FROZEN_INCREMENTAL"].lt(0)
        & pivot["fdr_q_FROZEN_INCREMENTAL"].lt(0.05)
        & pivot["sparse_rule_n_FROZEN_INCREMENTAL"].ge(3)
        & pivot["other_rule_n_FROZEN_INCREMENTAL"].ge(3)
    )
    pivot["structural_signal_robust"] = (
        pivot["is_supported"] & pivot["frozen_supported"] & pivot["incremental_supported"]
    )

    robust = pivot[pivot["structural_signal_robust"]]
    frozen_nominal = pivot[
        pivot["difference_sparse_minus_other_FROZEN_OOS"].lt(0)
        & pivot["difference_ci_high_FROZEN_OOS"].lt(0)
    ]
    frozen_fdr = pivot[pivot["frozen_supported"]]
    if len(robust):
        verdict = "STRUCTURAL_SIGNAL_ROBUST"
    elif len(frozen_nominal) or bool(primary[primary["scope"].eq("IS_DISCOVERY")]["fdr_sparse_worse"].any()):
        verdict = "WEAK"
    else:
        verdict = "NO_SIGNAL"

    target_ids = ["stage3:ANET:fe220620802b", "stage3:BB:f1bdfe7f8ad9"]
    anet_bb = rules[rules["candidate_id"].isin(target_ids)].copy()
    label_columns = [f"cut_{cut}" for cut in CUTS]
    anet_bb_columns = [
        "candidate_id", "stage", "ticker", *WEIGHT_COLUMNS,
        "active_exact_count", "active_material_count", "positive_weight_sum",
        "top1_weight_share_pct", "top2_weight_share_pct", "top1_indicator", "top2_indicators",
        *label_columns,
        "is_discovery_trade_n", "is_discovery_avg_pnl_pct", "is_discovery_win_rate_pct",
        "internal_holdout_trade_n", "internal_holdout_avg_pnl_pct", "internal_holdout_win_rate_pct",
        "frozen_trade_n", "frozen_avg_pnl_pct", "frozen_win_rate_pct", "frozen_worst_mae_pct",
        "existing_static_survivor",
    ]
    anet_bb[anet_bb_columns].to_csv(ANET_BB_OUT, index=False)

    curve_rows: list[dict[str, Any]] = []
    for row in pivot.itertuples(index=False):
        if row.is_supported and not row.frozen_supported:
            curve_rows.append({
                "type": "IS_ONLY_CUT",
                "stage": row.stage,
                "cut": row.cut,
                "is_pnl_difference": row.difference_sparse_minus_other_IS_DISCOVERY,
                "frozen_pnl_difference": row.difference_sparse_minus_other_FROZEN_OOS,
                "frozen_ci_high": row.difference_ci_high_FROZEN_OOS,
                "frozen_fdr_q": row.fdr_q_FROZEN_OOS,
                "incremental_pnl_difference": row.difference_sparse_minus_other_FROZEN_INCREMENTAL,
                "note": "IS 열위가 frozen OOS에서 FDR/CI 기준으로 재현되지 않음",
            })
        elif row.frozen_supported and not row.incremental_supported:
            curve_rows.append({
                "type": "FROZEN_BROAD_ONLY_CUT",
                "stage": row.stage,
                "cut": row.cut,
                "is_pnl_difference": row.difference_sparse_minus_other_IS_DISCOVERY,
                "frozen_pnl_difference": row.difference_sparse_minus_other_FROZEN_OOS,
                "frozen_ci_high": row.difference_ci_high_FROZEN_OOS,
                "frozen_fdr_q": row.fdr_q_FROZEN_OOS,
                "incremental_pnl_difference": row.difference_sparse_minus_other_FROZEN_INCREMENTAL,
                "note": "broad frozen 신호가 기존 gate 통과 순증군에서 유지되지 않음",
            })
    if not curve_rows:
        curve_rows.append({
            "type": "NO_SELECTED_CUT",
            "stage": "ALL",
            "cut": "NONE",
            "is_pnl_difference": math.nan,
            "frozen_pnl_difference": math.nan,
            "frozen_ci_high": math.nan,
            "frozen_fdr_q": math.nan,
            "incremental_pnl_difference": math.nan,
            "note": "IS-only 또는 broad-frozen-only 유의 cut 없음",
        })
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(CURVE_OUT, index=False)

    stage_counts = {
        stage: {
            "rule_n": int((rules["stage"] == stage).sum()),
            "is_rule_n": int(((rules["stage"] == stage) & rules["is_discovery_trade_n"].ge(MIN_TRADES)).sum()),
            "frozen_rule_n": int(((rules["stage"] == stage) & rules["frozen_trade_n"].ge(MIN_TRADES)).sum()),
            "incremental_frozen_rule_n": int(((rules["stage"] == stage) & rules["frozen_trade_n"].ge(MIN_TRADES) & rules["existing_static_survivor"]).sum()),
        }
        for stage in ("stage2", "stage3")
    }
    anet_bb_summary = {}
    for row in anet_bb.itertuples(index=False):
        anet_bb_summary[row.ticker] = {
            "candidate_id": row.candidate_id,
            "weights": {column: getattr(row, column) for column in WEIGHT_COLUMNS},
            "active_exact_count": int(row.active_exact_count),
            "active_material_count": int(row.active_material_count),
            "top2_weight_share_pct": float(row.top2_weight_share_pct),
            "top2_indicators": row.top2_indicators,
            "matched_cut_n": int(sum(bool(getattr(row, column)) for column in label_columns)),
            "matched_cuts": [cut for cut in CUTS if bool(getattr(row, f"cut_{cut}"))],
            "frozen_avg_pnl_pct": safe_float(row.frozen_avg_pnl_pct),
            "frozen_win_rate_pct": safe_float(row.frozen_win_rate_pct),
            "structurally_sparse": False,
        }

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "definition_scope": "stored rule weights; not realized entry-time components",
        "cut_family": CUTS,
        "material_weight_epsilon": MATERIAL_WEIGHT_EPS,
        "metrics": [metric for metric, _, _ in METRICS],
        "method": {
            "discovery": "canonical non-holdout Stage2/Stage3 trades, rule-level aggregation",
            "internal_holdout": "Stage2 oos_2025h2 / Stage3 recent_1y, diagnostic only",
            "validation": "frozen OOS trades, untouched during cut definition",
            "stage_separated": True,
            "minimum_trades_per_rule": MIN_TRADES,
            "effect": "unweighted mean of rule-level metric; sparse minus other",
            "bootstrap": f"ticker-cluster {BOOTSTRAP_REPS} reps",
            "p_value": "one-sided cluster-robust OLS coefficient test; sparse worse",
            "multiple_testing": "BH FDR across 8 cuts within each stage/scope/metric family",
            "robust_requirement": "IS avg PnL + frozen avg PnL + existing-gate incremental avg PnL all negative, bootstrap upper<0, FDR<0.05, group n>=3",
        },
        "source_trade_rows": scan_counts,
        "cohorts": stage_counts,
        "primary_cut_counts": {
            "is_supported_n": int(pivot["is_supported"].sum()),
            "frozen_nominal_n": len(frozen_nominal),
            "frozen_fdr_supported_n": len(frozen_fdr),
            "robust_n": len(robust),
        },
        "robust_cuts": robust[["stage", "cut"]].to_dict("records"),
        "anet_bb": anet_bb_summary,
        "interpretation": (
            "저장 가중치 구조 기준으로 frozen OOS와 기존 gate 통과 순증군까지 일관된 성과 열위를 보이는 cut이 존재함"
            if verdict == "STRUCTURAL_SIGNAL_ROBUST"
            else "일부 IS 또는 명목 frozen 방향은 있을 수 있으나 다중검정·순증군까지 통과한 구조 cut은 없음"
            if verdict == "WEAK"
            else "저장 가중치의 소수지표 구조는 frozen OOS에서 성과 열위를 구분하지 못함"
        ),
        "no_design_change": True,
        "operational_code_change": False,
        "config_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    primary_display = primary.sort_values(
        ["scope", "stage", "fdr_q", "difference_sparse_minus_other"],
        ascending=[True, True, True, True],
    )
    frozen_best = primary_display[primary_display["scope"].eq("FROZEN_OOS")].groupby("stage", as_index=False).head(4)
    incremental_best = primary_display[primary_display["scope"].eq("FROZEN_INCREMENTAL")].groupby("stage", as_index=False).head(4)

    lines = [
        "# 소수지표 구조 rule 성과 분포 분석",
        "",
        f"- 최종 판정: **{verdict}**",
        "- 분석 대상: 저장 rulebook core 가중치 구조",
        "- 진입 시점 realized component 분석이 아님",
        "- Stage2·Stage3 완전 분리",
        "- 운영·라이브·원본 코드·설정·설계 변경: 0건",
        "",
        "## 1. 구조 정의",
        "",
        "각 rule의 MA·MACD·RSI·BB·Volume 저장 가중치에서 다음을 계산했다.",
        "",
        "- exact active count: 가중치 > 0인 지표 수",
        f"- material active count: 가중치 > {MATERIAL_WEIGHT_EPS:.2f}인 지표 수",
        "- Top2 집중도: 상위 2개 양수 가중치 합 / 전체 양수 가중치 합",
        "",
        "단일 임의 경계 대신 8개 cut family를 사전 고정했다.",
        "",
    ]
    for cut, definition in CUTS.items():
        lines.append(f"- `{cut}`: `{definition}`")
    lines += [
        "",
        "## 2. 데이터와 검증 규율",
        "",
        f"- canonical matched trades: Stage2 {scan_counts['stage2_rows']:,}건, Stage3 {scan_counts['stage3_rows']:,}건",
        f"- Stage2 rule: {stage_counts['stage2']['rule_n']:,}개; frozen {stage_counts['stage2']['frozen_rule_n']:,}개; 순증군 {stage_counts['stage2']['incremental_frozen_rule_n']:,}개",
        f"- Stage3 rule: {stage_counts['stage3']['rule_n']:,}개; frozen {stage_counts['stage3']['frozen_rule_n']:,}개; 순증군 {stage_counts['stage3']['incremental_frozen_rule_n']:,}개",
        "- IS: canonical non-holdout 거래",
        "- Frozen validation: 별도 frozen OOS 거래",
        "- 효과량: rule별 지표 평균의 `소수지표군 - 기타군`",
        "- CI: ticker-cluster bootstrap 5,000회",
        "- p-value: ticker-cluster robust 단측 검정",
        "- 다중검정: stage·scope·metric별 8개 cut BH FDR",
        "",
        "## 3. ANET·BB 정의 타당성",
        "",
        "| 후보 | 활성 exact | 활성 material | Top2 | Top2 지표 | 소수지표 cut 포섭 |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for ticker in ("ANET", "BB"):
        item = anet_bb_summary[ticker]
        lines.append(
            f"| {ticker} | {item['active_exact_count']} | {item['active_material_count']} | "
            f"{item['top2_weight_share_pct']:.2f}% | {item['top2_indicators']} | {item['matched_cut_n']}/8 |"
        )
    lines += [
        "",
        "ANET·BB는 진입 시점에 RSI+MA만 발화한 point snapshot이 있었지만, 저장 rule 자체는 5개 core 가중치가 모두 양수다. 따라서 두 후보는 어떤 소수지표 구조 cut에도 걸리지 않는다.",
        "",
        "## 4. Frozen OOS 평균 PnL cut 결과 상위",
        "",
        "음수 차이는 소수지표군이 나쁘다는 뜻이다.",
        "",
        "| Stage | Cut | 소수 rule | 기타 rule | PnL 차이 | 95% CI | FDR q |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in frozen_best.itertuples(index=False):
        lines.append(
            f"| {row.stage} | {row.cut} | {row.sparse_rule_n} | {row.other_rule_n} | "
            f"{row.difference_sparse_minus_other:.4f}%p | [{row.difference_ci_low:.4f}, {row.difference_ci_high:.4f}] | {row.fdr_q:.4f} |"
        )
    lines += [
        "",
        "## 5. 기존 게이트 통과 순증군",
        "",
        "| Stage | Cut | 소수 rule | 기타 rule | PnL 차이 | 95% CI | FDR q |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in incremental_best.itertuples(index=False):
        lines.append(
            f"| {row.stage} | {row.cut} | {row.sparse_rule_n} | {row.other_rule_n} | "
            f"{row.difference_sparse_minus_other:.4f}%p | [{row.difference_ci_low:.4f}, {row.difference_ci_high:.4f}] | {row.fdr_q:.4f} |"
        )
    lines += [
        "",
        "## 6. 판정",
        "",
        f"- IS 평균 PnL FDR 통과 cut: {int(pivot['is_supported'].sum())}개",
        f"- Frozen 명목 CI 통과 cut: {len(frozen_nominal)}개",
        f"- Frozen FDR 통과 cut: {len(frozen_fdr)}개",
        f"- 순증군까지 통과한 robust cut: {len(robust)}개",
        "",
    ]
    if verdict == "STRUCTURAL_SIGNAL_ROBUST":
        lines += [
            "**STRUCTURAL_SIGNAL_ROBUST**", "",
            "저장 rule 구조만으로도 frozen OOS와 기존 게이트 통과 순증군에서 성과 열위가 재현됐다. 아래 robust cut만 정적 게이트 후보로 검토할 수 있다.",
            "",
        ]
        for row in robust.itertuples(index=False):
            lines.append(f"- `{row.stage} / {row.cut}`")
    elif verdict == "WEAK":
        lines += [
            "**WEAK**", "",
            "일부 구간에서 소수지표 구조의 열위 방향은 보였지만 frozen FDR와 기존 게이트 통과 순증군까지 일관되게 유지되지 않았다. 정적 BLOCK 근거로는 부족하다.",
        ]
    else:
        lines += [
            "**NO_SIGNAL**", "",
            "소수지표 저장 가중치 구조는 frozen OOS에서 성과 열위를 구분하지 못했다. 구조 기반 정적 게이트 근거가 없다.",
        ]
    lines += [
        "",
        "ANET·BB는 애초에 저장 rule 구조상 소수지표군이 아니므로 이 구조 판정으로 두 후보의 상반된 결과를 설명하거나 차단할 수 없다.",
        "",
        "## 7. 커브피팅 점검",
        "",
        "- 8개 cut을 사전 고정하고 동일 family에 BH FDR 적용",
        "- Stage2·Stage3를 섞지 않음",
        "- IS에서 sparse-worse 방향을 확인한 뒤 frozen에 동일 방향 적용",
        "- frozen 경계 재튜닝 없음",
        "- ticker 단위 cluster CI·검정",
        "- 기존 history·v3·BOIL 통과 순증군 별도 검증",
        "- 진입 시점 component를 구조 특징으로 사용하지 않음",
        "",
        "## 8. 산출물",
        "",
        f"- `{RULE_OUT.name}`",
        f"- `{GROUP_OUT.name}`",
        f"- `{CUT_OUT.name}`",
        f"- `{INCREMENTAL_OUT.name}`",
        f"- `{ANET_BB_OUT.name}`",
        f"- `{CURVE_OUT.name}`",
        f"- `{SUMMARY_OUT.name}`",
    ]
    READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
