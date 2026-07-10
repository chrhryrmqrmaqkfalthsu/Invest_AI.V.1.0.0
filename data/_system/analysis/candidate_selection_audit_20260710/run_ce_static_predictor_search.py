from __future__ import annotations

"""CE형 실패를 사전에 거를 수 있는 정적 룰북 특징 탐색.

- discovery: 기존 정적 게이트 통과 후보의 내부 holdout
- external validation: frozen live93 OOS
- 동적 realized component/current market 상태는 사용하지 않는다.
- 원본·라이브·운영 코드·재학습·주문·삭제를 변경하지 않는다.
"""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, spearmanr
from sklearn.metrics import balanced_accuracy_score, precision_score, recall_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "data/_system/analysis/candidate_selection_audit_20260710"
SEED = 20260711
BOOTSTRAP_REPS = 1_000
CORE = ("ma", "macd", "rsi", "bb", "volume")
WEIGHT_KEYS = {
    "ma": "weight_ma_align",
    "macd": "weight_macd_golden",
    "rsi": "weight_rsi_zone",
    "bb": "weight_bb_near_lower",
    "volume": "weight_volume_surge",
}

TARGET_OUT = OUT / "ce_static_target_labels.csv.gz"
FEATURE_MATRIX_OUT = OUT / "ce_static_feature_matrix.csv.gz"
FEATURE_POWER_OUT = OUT / "ce_static_feature_predictive_power.csv"
PAIR_POWER_OUT = OUT / "ce_static_pair_predictive_power.csv"
CE7_OUT = OUT / "ce_static_ce7_capture.csv"
CURVE_FIT_OUT = OUT / "ce_static_curve_fit_notes.csv"
SUMMARY_OUT = OUT / "ce_static_predictor_summary.json"
READOUT_OUT = OUT / "ce_static_predictor_readout.md"


@dataclass(frozen=True)
class Boundary:
    mode: str
    direction: str
    percentile_threshold: float | None
    raw_thresholds: dict[str, float]


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
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                yield row
    after = (path.stat().st_size, path.stat().st_mtime_ns)
    if before != after:
        raise RuntimeError(f"source changed while reading: {path}")


def safe_float(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else math.nan
    except Exception:
        return math.nan


def safe_ratio(numerator: Any, denominator: Any) -> float:
    a, b = safe_float(numerator), safe_float(denominator)
    if not math.isfinite(a) or not math.isfinite(b) or abs(b) <= 1e-12:
        return math.nan
    return a / b


def history_metrics(candidates: pd.DataFrame) -> pd.DataFrame:
    targets_by_file: dict[tuple[str, Path], dict[str, str]] = defaultdict(dict)
    for row in candidates.itertuples(index=False):
        parent = (ROOT / str(row.source_file)).parent
        path = parent / ("trades.jsonl" if row.stage == "stage2" else "exit_trades.jsonl")
        targets_by_file[(str(row.stage), path)][str(row.rulebook_hash)] = str(row.candidate_id)

    pnl_values: dict[str, list[float]] = defaultdict(list)
    mae_values: dict[str, list[float]] = defaultdict(list)
    mfe_values: dict[str, list[float]] = defaultdict(list)
    for index, ((stage, path), target_map) in enumerate(targets_by_file.items(), 1):
        marker = "rulebook_hash" if stage == "stage2" else "final_rulebook_hash"
        holdout_label = "oos_2025h2" if stage == "stage2" else "recent_1y"
        for row in jsonl_rows(path):
            if str(row.get("period_label") or "") != holdout_label:
                continue
            cid = target_map.get(str(row.get(marker) or ""))
            if cid is None:
                continue
            pnl_values[cid].append(float(row.get("pnl_pct") or 0.0))
            mae_values[cid].append(float(row.get("max_loss_during_hold") or 0.0))
            mfe_values[cid].append(float(row.get("max_profit_during_hold") or 0.0))
        if index % 100 == 0:
            print(f"history progress {index}/{len(targets_by_file)}", flush=True)

    rows = []
    for cid in candidates["candidate_id"].astype(str):
        pnl = np.asarray(pnl_values.get(cid, []), dtype=float)
        mae = np.asarray(mae_values.get(cid, []), dtype=float)
        mfe = np.asarray(mfe_values.get(cid, []), dtype=float)
        if len(pnl) == 0:
            rows.append({"candidate_id": cid})
            continue
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        loss_sum = abs(losses.sum()) if len(losses) else 0.0
        median_win = float(np.median(wins)) if len(wins) else math.nan
        rows.append(
            {
                "candidate_id": cid,
                "eval_n": len(pnl),
                "eval_avg_pnl_pct": float(pnl.mean()),
                "eval_win_rate_pct": float((pnl > 0).mean() * 100.0),
                "eval_min_pnl_pct": float(pnl.min()),
                "eval_p05_pnl_pct": float(np.quantile(pnl, 0.05)),
                "eval_avg_mae_pct": float(mae.mean()),
                "eval_worst_mae_pct": float(mae.min()),
                "eval_avg_mfe_pct": float(mfe.mean()),
                "eval_median_win_pct": median_win,
                "eval_worst_to_median_win": abs(float(pnl.min())) / median_win if median_win > 0 else math.nan,
                "eval_top3_loss_share": abs(float(np.sort(losses)[: min(3, len(losses))].sum())) / loss_sum if loss_sum > 0 else 0.0,
                "eval_max_loss_share": abs(float(pnl.min())) / loss_sum if loss_sum > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows)


def frozen_metrics() -> pd.DataFrame:
    trades = stable_csv(ROOT / "data/_system/analysis/oos_reproduce_frozen_20260707/oos_trades_frozen.csv", low_memory=False)
    trades = trades[trades["split"].astype(str).str.upper().eq("OOS")]
    rows = []
    for cid, group in trades.groupby("candidate_id"):
        pnl = group["pnl_pct"].to_numpy(float)
        mae = group["MAE"].to_numpy(float)
        mfe = group["MFE"].to_numpy(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        loss_sum = abs(losses.sum()) if len(losses) else 0.0
        median_win = float(np.median(wins)) if len(wins) else math.nan
        rows.append(
            {
                "candidate_id": str(cid),
                "eval_n": len(pnl),
                "eval_avg_pnl_pct": float(pnl.mean()),
                "eval_win_rate_pct": float((pnl > 0).mean() * 100.0),
                "eval_min_pnl_pct": float(pnl.min()),
                "eval_p05_pnl_pct": float(np.quantile(pnl, 0.05)),
                "eval_avg_mae_pct": float(mae.mean()),
                "eval_worst_mae_pct": float(mae.min()),
                "eval_avg_mfe_pct": float(mfe.mean()),
                "eval_median_win_pct": median_win,
                "eval_worst_to_median_win": abs(float(pnl.min())) / median_win if median_win > 0 else math.nan,
                "eval_top3_loss_share": abs(float(np.sort(losses)[: min(3, len(losses))].sum())) / loss_sum if loss_sum > 0 else 0.0,
                "eval_max_loss_share": abs(float(pnl.min())) / loss_sum if loss_sum > 0 else 0.0,
            }
        )
    return pd.DataFrame(rows)


def source_features(candidates: pd.DataFrame) -> pd.DataFrame:
    cache: dict[Path, list[dict[str, Any]]] = {}
    rows = []
    for row in candidates.itertuples(index=False):
        path = ROOT / str(row.source_file)
        if path not in cache:
            cache[path] = list(jsonl_rows(path))
        source = cache[path][int(row.source_row_index) - 1]
        rulebook = source.get("rulebook") or {}
        weights = {component: max(0.0, safe_float(rulebook.get(key))) for component, key in WEIGHT_KEYS.items()}
        positive = [value for value in weights.values() if value > 0]
        total = sum(positive)
        sorted_weights = sorted(positive, reverse=True)
        shares = [value / total for value in positive] if total > 0 else []
        hhi = sum(value * value for value in shares) if shares else math.nan
        entropy = -sum(value * math.log(value) for value in shares) / math.log(len(shares)) if len(shares) > 1 else 0.0
        threshold = safe_float(rulebook.get("signal_threshold"))
        cumulative = 0.0
        min_count = 0
        for value in sorted_weights:
            cumulative += value
            min_count += 1
            if cumulative + 1e-12 >= threshold:
                break
        if not sorted_weights or cumulative + 1e-12 < threshold:
            min_count = len(sorted_weights) + 1

        if row.stage == "stage2":
            periods = list(source.get("periods") or [])
            train_labels = [str(x) for x in source.get("origin_train_labels") or []]
            selected_train = None
            for label in train_labels:
                selected_train = next((p for p in periods if str(p.get("period_label")) == f"{label}_eval"), None)
                if selected_train is not None:
                    break
            train_periods = [p for p in periods if str(p.get("period_kind")) == "train"]
            if selected_train is None and train_periods:
                selected_train = max(train_periods, key=lambda p: safe_float(p.get("fitness")))
            validation = next((p for p in periods if str(p.get("period_kind")) == "oos"), None)
            is_fitness = safe_float((selected_train or {}).get("fitness"))
            validation_fitness = safe_float((validation or {}).get("fitness"))
            is_expectancy = safe_float((selected_train or {}).get("expectancy_pct"))
            validation_expectancy = safe_float((validation or {}).get("expectancy_pct"))
            is_trade_count = safe_float((selected_train or {}).get("trade_count"))
            validation_trade_count = safe_float((validation or {}).get("trade_count"))
            origin_count = safe_float(source.get("origin_count"))
            composite_fitness = math.nan
            entry_rank = math.nan
            exit_rank = math.nan
        else:
            bull = source.get("bull_metrics") or {}
            stress = source.get("stress_metrics") or {}
            is_fitness = safe_float(bull.get("fitness"))
            validation_fitness = safe_float(stress.get("fitness"))
            is_expectancy = safe_float(bull.get("expectancy_pct"))
            validation_expectancy = safe_float(stress.get("expectancy_pct"))
            is_trade_count = safe_float(bull.get("trade_count"))
            validation_trade_count = safe_float(stress.get("trade_count"))
            origin_count = math.nan
            composite_fitness = safe_float(source.get("composite_fitness"))
            entry_rank = safe_float(source.get("entry_rank"))
            exit_rank = safe_float(source.get("exit_rank"))

        dominant = max(weights, key=weights.get) if total > 0 else "none"
        result = {
            "candidate_id": str(row.candidate_id),
            "active_core_count": sum(value > 0 for value in weights.values()),
            "zero_core_count": sum(value <= 0 for value in weights.values()),
            "positive_core_weight_sum": total,
            "core_top1_share_pct": sorted_weights[0] / total * 100 if total > 0 else math.nan,
            "core_top2_share_pct": sum(sorted_weights[:2]) / total * 100 if total > 0 else math.nan,
            "core_weight_hhi": hhi,
            "core_weight_entropy": entropy,
            "signal_threshold_static": threshold,
            "core_sum_to_threshold": safe_ratio(total, threshold),
            "core_top1_to_threshold": safe_ratio(sorted_weights[0] if sorted_weights else math.nan, threshold),
            "core_top2_to_threshold": safe_ratio(sum(sorted_weights[:2]), threshold),
            "min_core_conditions_to_threshold": min_count,
            "single_core_reaches_threshold": int(bool(sorted_weights and sorted_weights[0] >= threshold)),
            "top2_core_reaches_threshold": int(bool(sorted_weights and sum(sorted_weights[:2]) >= threshold)),
            "weight_volume_surge_abs": abs(safe_float(rulebook.get("weight_volume_surge"))),
            "volume_weight_share_pct": weights["volume"] / total * 100 if total > 0 else math.nan,
            "dominant_is_ma": int(dominant == "ma"),
            "dominant_is_macd": int(dominant == "macd"),
            "dominant_is_rsi": int(dominant == "rsi"),
            "dominant_is_bb": int(dominant == "bb"),
            "dominant_is_volume": int(dominant == "volume"),
            "stored_is_fitness": is_fitness,
            "stored_validation_fitness": validation_fitness,
            "stored_is_validation_fitness_ratio": safe_ratio(is_fitness, validation_fitness),
            "stored_is_validation_fitness_gap": is_fitness - validation_fitness if math.isfinite(is_fitness) and math.isfinite(validation_fitness) else math.nan,
            "stored_is_expectancy_pct": is_expectancy,
            "stored_validation_expectancy_pct": validation_expectancy,
            "stored_is_validation_expectancy_ratio": safe_ratio(is_expectancy, validation_expectancy),
            "stored_is_trade_count": is_trade_count,
            "stored_validation_trade_count": validation_trade_count,
            "is_expectancy_per_sqrt_trade": is_expectancy / math.sqrt(is_trade_count) if is_trade_count > 0 else math.nan,
            "is_fitness_per_sqrt_trade": is_fitness / math.sqrt(is_trade_count) if is_trade_count > 0 else math.nan,
            "origin_count": origin_count,
            "composite_fitness": composite_fitness,
            "entry_rank": entry_rank,
            "exit_rank": exit_rank,
            "stop_loss_atr": safe_float(rulebook.get("stop_loss_atr")),
            "take_profit_atr": safe_float(rulebook.get("take_profit_atr")),
            "stop_to_take_ratio": safe_ratio(rulebook.get("stop_loss_atr"), rulebook.get("take_profit_atr")),
            "max_holding_days": safe_float(rulebook.get("max_holding_days")),
            "trailing_activation_profit_pct": safe_float(rulebook.get("trailing_activation_profit_pct")),
            "trailing_atr": safe_float(rulebook.get("trailing_atr")),
            "breakeven_enabled": int(bool(rulebook.get("breakeven_enabled"))),
            "use_news_global": int(bool(rulebook.get("use_news_global"))),
            "use_market_entry_adjustment": int(bool(rulebook.get("use_market_entry_adjustment"))),
            "use_event_block": int(bool(rulebook.get("use_event_block"))),
            "event_strength_multiplier": safe_float(rulebook.get("event_strength_multiplier")),
            "market_adjustment_strength": safe_float(rulebook.get("market_adjustment_strength")),
            "vix_sensitivity": safe_float(rulebook.get("vix_sensitivity")),
            "exit_is_fixed": int(str(rulebook.get("exit_strategy")) == "fixed"),
            "exit_is_trailing": int(str(rulebook.get("exit_strategy")) == "trailing"),
            "exit_is_hybrid": int(str(rulebook.get("exit_strategy")) == "hybrid"),
        }
        rows.append(result)
    return pd.DataFrame(rows)


def threshold_features(candidate_ids: set[str]) -> pd.DataFrame:
    detail = stable_csv(OUT / "threshold_p99_weightless_block_indicator_labels.csv.gz", low_memory=False)
    detail = detail[detail["candidate_id"].isin(candidate_ids)].copy()
    detail["fire_rate"] = detail["fired_count"] / detail["eligible_days"].replace(0, np.nan)
    detail["active_for_tightness"] = detail["weight"].gt(0)
    detail["scalar_activation_percentile"] = np.nan
    volume = detail["component"].eq("volume")
    bb = detail["component"].eq("bb")
    detail.loc[volume, "scalar_activation_percentile"] = detail.loc[volume, "threshold_percentile_pct"]
    detail.loc[bb, "scalar_activation_percentile"] = 100.0 - detail.loc[bb, "threshold_percentile_pct"]
    rows = []
    for cid, group in detail.groupby("candidate_id"):
        active = group[group["active_for_tightness"]]
        scalar = group[group["component"].isin(["bb", "volume"])]["scalar_activation_percentile"].dropna()
        fire = active["fire_rate"].dropna()
        component_rates = dict(zip(group["component"], group["fire_rate"]))
        rows.append(
            {
                "candidate_id": cid,
                "max_scalar_activation_percentile": float(scalar.max()) if len(scalar) else math.nan,
                "mean_scalar_activation_percentile": float(scalar.mean()) if len(scalar) else math.nan,
                "min_active_condition_fire_rate": float(fire.min()) if len(fire) else math.nan,
                "mean_active_condition_fire_rate": float(fire.mean()) if len(fire) else math.nan,
                "active_condition_below_5pct_count": int((fire < 0.05).sum()),
                "active_condition_below_1pct_count": int((fire < 0.01).sum()),
                "ma_fire_rate": safe_float(component_rates.get("ma")),
                "macd_fire_rate": safe_float(component_rates.get("macd")),
                "rsi_fire_rate": safe_float(component_rates.get("rsi")),
                "bb_fire_rate": safe_float(component_rates.get("bb")),
                "volume_fire_rate": safe_float(component_rates.get("volume")),
            }
        )
    return pd.DataFrame(rows)


def derive_target_thresholds(discovery: pd.DataFrame) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for stage, group in discovery.groupby("stage"):
        positive_gap = (group["base_avg_pnl_pct"] - group["eval_avg_pnl_pct"]).dropna()
        positive_eval = group[group["eval_avg_pnl_pct"].gt(0)]
        output[stage] = {
            "collapse_gap_q90_pp": float(positive_gap.quantile(0.90)),
            "tail_worst_mae_q10_pct": float(positive_eval["eval_worst_mae_pct"].quantile(0.10)),
            "high_win_q75_pct": float(positive_eval["eval_win_rate_pct"].quantile(0.75)),
            "worst_to_median_win_q75": float(positive_eval["eval_worst_to_median_win"].quantile(0.75)),
            "top3_loss_share_q50": float(positive_eval["eval_top3_loss_share"].quantile(0.50)),
        }
    return output


def apply_target(frame: pd.DataFrame, thresholds: dict[str, dict[str, float]]) -> pd.DataFrame:
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
        lambda r: "|".join(
            name for name, flag in (
                ("IS_OOS_COLLAPSE", r.target_is_oos_collapse),
                ("POSITIVE_MEAN_EXTREME_TAIL", r.target_positive_tail_risk),
                ("HIGH_WIN_LARGE_LOSS", r.target_high_win_large_loss),
            ) if flag
        ),
        axis=1,
    )
    return result


def empirical_percentile(train: pd.Series, values: pd.Series) -> np.ndarray:
    train_values = np.sort(train.dropna().to_numpy(float))
    if len(train_values) == 0:
        return np.full(len(values), np.nan)
    result = np.full(len(values), np.nan)
    valid = values.notna().to_numpy()
    result[valid] = np.searchsorted(train_values, values[valid].to_numpy(float), side="right") / len(train_values)
    return result


def transformed_feature(discovery: pd.DataFrame, validation: pd.DataFrame, feature: str) -> tuple[np.ndarray, np.ndarray, str, dict[str, list[float]]]:
    unique = discovery[feature].dropna().unique()
    boundaries: dict[str, list[float]] = {}
    if len(unique) <= 2 and set(np.round(unique, 12)).issubset({0.0, 1.0}):
        return discovery[feature].to_numpy(float), validation[feature].to_numpy(float), "binary", boundaries
    d_values = np.full(len(discovery), np.nan)
    v_values = np.full(len(validation), np.nan)
    for stage in ("stage2", "stage3"):
        train = discovery.loc[discovery["stage"].eq(stage), feature]
        d_mask = discovery["stage"].eq(stage)
        v_mask = validation["stage"].eq(stage)
        d_values[d_mask.to_numpy()] = empirical_percentile(train, discovery.loc[d_mask, feature])
        v_values[v_mask.to_numpy()] = empirical_percentile(train, validation.loc[v_mask, feature])
        boundaries[stage] = [float(train.quantile(q)) for q in np.arange(0.05, 1.0, 0.05)] if train.notna().any() else []
    return d_values, v_values, "stage_percentile", boundaries


def classification_metrics(y: np.ndarray, score: np.ndarray, flag: np.ndarray) -> dict[str, float]:
    valid = np.isfinite(score) & pd.notna(y) & pd.notna(flag)
    yv = y[valid].astype(int)
    sv = score[valid].astype(float)
    fv = flag[valid].astype(bool)
    if len(np.unique(yv)) < 2 or fv.sum() == 0 or (~fv).sum() == 0:
        return {
            "n": len(yv), "bad_n": int(yv.sum()), "flagged_n": int(fv.sum()), "flagged_rate": float(fv.mean()) if len(fv) else math.nan,
            "auc": math.nan, "balanced_accuracy": math.nan, "precision": math.nan, "recall": math.nan,
            "bad_rate_flagged": math.nan, "bad_rate_unflagged": math.nan, "risk_difference": math.nan,
        }
    return {
        "n": len(yv),
        "bad_n": int(yv.sum()),
        "flagged_n": int(fv.sum()),
        "flagged_rate": float(fv.mean()),
        "auc": float(roc_auc_score(yv, sv)),
        "balanced_accuracy": float(balanced_accuracy_score(yv, fv)),
        "precision": float(precision_score(yv, fv, zero_division=0)),
        "recall": float(recall_score(yv, fv, zero_division=0)),
        "bad_rate_flagged": float(yv[fv].mean()),
        "bad_rate_unflagged": float(yv[~fv].mean()),
        "risk_difference": float(yv[fv].mean() - yv[~fv].mean()),
    }


def bootstrap_ci(frame: pd.DataFrame, y_col: str, score: np.ndarray, flag: np.ndarray, seed: int) -> dict[str, float]:
    work = frame[["ticker", y_col]].copy().reset_index(drop=True)
    work["score"] = score
    work["flag"] = flag
    work = work.dropna(subset=[y_col, "score", "flag"])
    groups = {ticker: group.index.to_numpy() for ticker, group in work.groupby("ticker")}
    tickers = np.array(list(groups), dtype=object)
    rng = np.random.default_rng(seed)
    aucs: list[float] = []
    rds: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        sampled = rng.choice(tickers, len(tickers), replace=True)
        indices = np.concatenate([groups[ticker] for ticker in sampled])
        sample = work.loc[indices]
        y = sample[y_col].to_numpy(int)
        s = sample["score"].to_numpy(float)
        f = sample["flag"].to_numpy(bool)
        if len(np.unique(y)) < 2 or f.sum() == 0 or (~f).sum() == 0:
            continue
        aucs.append(float(roc_auc_score(y, s)))
        rds.append(float(y[f].mean() - y[~f].mean()))
    def quantile(values: list[float], q: float) -> float:
        return float(np.quantile(values, q)) if values else math.nan
    return {
        "auc_ci_low": quantile(aucs, 0.025),
        "auc_ci_high": quantile(aucs, 0.975),
        "risk_difference_ci_low": quantile(rds, 0.025),
        "risk_difference_ci_high": quantile(rds, 0.975),
        "bootstrap_valid_reps": min(len(aucs), len(rds)),
    }


def fit_boundary(discovery: pd.DataFrame, validation: pd.DataFrame, feature: str) -> tuple[Boundary, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    d_score_raw, v_score_raw, mode, quantile_values = transformed_feature(discovery, validation, feature)
    y = discovery["target_bad"].to_numpy(int)
    if mode == "binary":
        candidates = [(">=", 0.5), ("<=", 0.5)]
    else:
        candidates = [(direction, float(threshold)) for direction in (">=", "<=") for threshold in np.arange(0.05, 1.0, 0.05)]
    best: tuple[float, float, str, float, np.ndarray] | None = None
    for direction, threshold in candidates:
        score = d_score_raw if direction == ">=" else -d_score_raw
        flag = d_score_raw >= threshold if direction == ">=" else d_score_raw <= threshold
        valid = np.isfinite(score)
        if valid.sum() < 50:
            continue
        prevalence = flag[valid].mean()
        if prevalence < 0.05 or prevalence > 0.40:
            continue
        metrics = classification_metrics(y[valid], score[valid], flag[valid])
        if not math.isfinite(metrics["balanced_accuracy"]):
            continue
        objective = metrics["balanced_accuracy"]
        tie_precision = metrics["precision"]
        candidate = (objective, tie_precision, direction, threshold, flag)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        raise RuntimeError(f"no boundary for {feature}")
    _, _, direction, threshold, d_flag = best
    d_score = d_score_raw if direction == ">=" else -d_score_raw
    v_score = v_score_raw if direction == ">=" else -v_score_raw
    v_flag = v_score_raw >= threshold if direction == ">=" else v_score_raw <= threshold
    raw_thresholds: dict[str, float] = {}
    if mode == "binary":
        raw_thresholds = {"stage2": threshold, "stage3": threshold}
    else:
        q = threshold if direction == ">=" else threshold
        for stage in ("stage2", "stage3"):
            train = discovery.loc[discovery["stage"].eq(stage), feature].dropna()
            raw_thresholds[stage] = float(train.quantile(q)) if len(train) else math.nan
    return Boundary(mode, direction, None if mode == "binary" else threshold, raw_thresholds), d_score, v_score, d_flag, v_flag


def bh_adjust(p_values: pd.Series) -> pd.Series:
    p = p_values.to_numpy(float)
    order = np.argsort(p)
    adjusted = np.empty(len(p), dtype=float)
    running = 1.0
    for rank in range(len(p) - 1, -1, -1):
        idx = order[rank]
        value = p[idx] * len(p) / (rank + 1)
        running = min(running, value)
        adjusted[idx] = min(1.0, running)
    return pd.Series(adjusted, index=p_values.index)


def univariate_analysis(discovery: pd.DataFrame, validation: pd.DataFrame, feature_columns: list[str]) -> tuple[pd.DataFrame, dict[str, tuple[Boundary, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]]:
    rows = []
    fitted = {}
    for index, feature in enumerate(feature_columns):
        try:
            boundary, d_score, v_score, d_flag, v_flag = fit_boundary(discovery, validation, feature)
        except Exception:
            continue
        fitted[feature] = (boundary, d_score, v_score, d_flag, v_flag)
        d_metrics = classification_metrics(discovery["target_bad"].to_numpy(int), d_score, d_flag)
        v_metrics = classification_metrics(validation["target_bad"].to_numpy(int), v_score, v_flag)
        d_ci = bootstrap_ci(discovery, "target_bad", d_score, d_flag, SEED + index * 2)
        v_ci = bootstrap_ci(validation, "target_bad", v_score, v_flag, SEED + index * 2 + 1)
        valid_d = np.isfinite(d_score) & discovery[feature].notna().to_numpy()
        try:
            correlation, corr_p = spearmanr(discovery.loc[valid_d, feature], discovery.loc[valid_d, "target_bad"].astype(int))
        except Exception:
            correlation, corr_p = math.nan, 1.0
        bad_values = discovery.loc[discovery["target_bad"] & discovery[feature].notna(), feature]
        good_values = discovery.loc[~discovery["target_bad"] & discovery[feature].notna(), feature]
        try:
            mw_p = float(mannwhitneyu(bad_values, good_values, alternative="two-sided").pvalue)
        except Exception:
            mw_p = 1.0
        rows.append(
            {
                "feature": feature,
                "boundary_mode": boundary.mode,
                "risk_direction": boundary.direction,
                "percentile_boundary": boundary.percentile_threshold,
                "raw_boundary_stage2": boundary.raw_thresholds.get("stage2"),
                "raw_boundary_stage3": boundary.raw_thresholds.get("stage3"),
                "discovery_n": d_metrics["n"],
                "discovery_bad_n": d_metrics["bad_n"],
                "discovery_flagged_n": d_metrics["flagged_n"],
                "discovery_flagged_rate": d_metrics["flagged_rate"],
                "discovery_auc": d_metrics["auc"],
                "discovery_auc_ci_low": d_ci["auc_ci_low"],
                "discovery_auc_ci_high": d_ci["auc_ci_high"],
                "discovery_balanced_accuracy": d_metrics["balanced_accuracy"],
                "discovery_precision": d_metrics["precision"],
                "discovery_recall": d_metrics["recall"],
                "discovery_bad_rate_flagged": d_metrics["bad_rate_flagged"],
                "discovery_bad_rate_unflagged": d_metrics["bad_rate_unflagged"],
                "discovery_risk_difference": d_metrics["risk_difference"],
                "discovery_risk_difference_ci_low": d_ci["risk_difference_ci_low"],
                "discovery_risk_difference_ci_high": d_ci["risk_difference_ci_high"],
                "discovery_spearman": correlation,
                "discovery_spearman_p": corr_p,
                "discovery_mannwhitney_p": mw_p,
                "validation_n": v_metrics["n"],
                "validation_bad_n": v_metrics["bad_n"],
                "validation_flagged_n": v_metrics["flagged_n"],
                "validation_flagged_rate": v_metrics["flagged_rate"],
                "validation_auc": v_metrics["auc"],
                "validation_auc_ci_low": v_ci["auc_ci_low"],
                "validation_auc_ci_high": v_ci["auc_ci_high"],
                "validation_balanced_accuracy": v_metrics["balanced_accuracy"],
                "validation_precision": v_metrics["precision"],
                "validation_recall": v_metrics["recall"],
                "validation_bad_rate_flagged": v_metrics["bad_rate_flagged"],
                "validation_bad_rate_unflagged": v_metrics["bad_rate_unflagged"],
                "validation_risk_difference": v_metrics["risk_difference"],
                "validation_risk_difference_ci_low": v_ci["risk_difference_ci_low"],
                "validation_risk_difference_ci_high": v_ci["risk_difference_ci_high"],
            }
        )
    result = pd.DataFrame(rows)
    result["discovery_fdr_q"] = bh_adjust(result["discovery_mannwhitney_p"])
    result["discovery_supported"] = (
        result["discovery_auc_ci_low"].gt(0.5)
        & result["discovery_risk_difference_ci_low"].gt(0)
        & result["discovery_fdr_q"].lt(0.05)
    )
    result["validation_supported"] = (
        result["validation_auc_ci_low"].gt(0.5)
        & result["validation_risk_difference_ci_low"].gt(0)
        & result["validation_flagged_n"].ge(5)
        & (result["validation_n"] - result["validation_flagged_n"]).ge(5)
    )
    result["replicated_static_predictor"] = result["discovery_supported"] & result["validation_supported"]
    return result.sort_values(
        ["replicated_static_predictor", "validation_auc", "discovery_balanced_accuracy"], ascending=False
    ), fitted


def pair_analysis(discovery: pd.DataFrame, validation: pd.DataFrame, univariate: pd.DataFrame, fitted: dict[str, tuple[Boundary, np.ndarray, np.ndarray, np.ndarray, np.ndarray]]) -> pd.DataFrame:
    candidates = univariate.sort_values(
        ["discovery_supported", "discovery_balanced_accuracy", "discovery_auc"], ascending=False
    )["feature"].head(5).tolist()
    rows = []
    pair_index = 0
    for i, left in enumerate(candidates):
        for right in candidates[i + 1 :]:
            left_fit, right_fit = fitted[left], fitted[right]
            for operator in ("AND", "OR"):
                d_flag = left_fit[3] & right_fit[3] if operator == "AND" else left_fit[3] | right_fit[3]
                v_flag = left_fit[4] & right_fit[4] if operator == "AND" else left_fit[4] | right_fit[4]
                d_score = d_flag.astype(float)
                v_score = v_flag.astype(float)
                d_metrics = classification_metrics(discovery["target_bad"].to_numpy(int), d_score, d_flag)
                v_metrics = classification_metrics(validation["target_bad"].to_numpy(int), v_score, v_flag)
                if not math.isfinite(d_metrics["balanced_accuracy"]):
                    continue
                d_ci = bootstrap_ci(discovery, "target_bad", d_score, d_flag, SEED + 500 + pair_index * 2)
                v_ci = bootstrap_ci(validation, "target_bad", v_score, v_flag, SEED + 501 + pair_index * 2)
                pair_index += 1
                rows.append(
                    {
                        "left_feature": left,
                        "operator": operator,
                        "right_feature": right,
                        "discovery_flagged_n": d_metrics["flagged_n"],
                        "discovery_balanced_accuracy": d_metrics["balanced_accuracy"],
                        "discovery_precision": d_metrics["precision"],
                        "discovery_recall": d_metrics["recall"],
                        "discovery_risk_difference": d_metrics["risk_difference"],
                        "discovery_risk_difference_ci_low": d_ci["risk_difference_ci_low"],
                        "discovery_risk_difference_ci_high": d_ci["risk_difference_ci_high"],
                        "validation_flagged_n": v_metrics["flagged_n"],
                        "validation_balanced_accuracy": v_metrics["balanced_accuracy"],
                        "validation_precision": v_metrics["precision"],
                        "validation_recall": v_metrics["recall"],
                        "validation_risk_difference": v_metrics["risk_difference"],
                        "validation_risk_difference_ci_low": v_ci["risk_difference_ci_low"],
                        "validation_risk_difference_ci_high": v_ci["risk_difference_ci_high"],
                        "validation_supported": bool(
                            v_ci["risk_difference_ci_low"] > 0
                            and v_metrics["flagged_n"] >= 5
                            and v_metrics["n"] - v_metrics["flagged_n"] >= 5
                        ),
                    }
                )
    result = pd.DataFrame(rows)
    if len(result):
        result["replicated_pair"] = result["discovery_risk_difference_ci_low"].gt(0) & result["validation_supported"]
        result = result.sort_values(["replicated_pair", "validation_balanced_accuracy", "discovery_balanced_accuracy"], ascending=False)
    return result


def main() -> int:
    base = stable_csv(OUT / "integrated_gate_candidate_dryrun.csv", low_memory=False)
    v3 = stable_csv(
        OUT / "threshold_p99_weightless_block_candidate_decisions.csv",
        usecols=["candidate_id", "final_p99_weightless_block_status"],
    )
    boil = stable_csv(OUT / "boil_block_exclusive_targets.csv", usecols=["candidate_id"])
    activity = stable_csv(
        OUT / "threshold_reachability_stage3_full_indicator_detail.csv.gz",
        usecols=["candidate_id", "activity_rule_hash"], low_memory=False,
    ).drop_duplicates("candidate_id")
    ce7 = stable_csv(OUT / "ce_origin_fail_rejudged.csv", usecols=["candidate_id", "stage", "ticker"])

    base = base.merge(v3, on="candidate_id", validate="one_to_one").merge(activity, on="candidate_id", validate="one_to_one")
    base["boil_block"] = base["candidate_id"].isin(set(boil["candidate_id"]))
    complete = base[base["origin_complete"].fillna(False)].copy()
    internal = history_metrics(complete)
    frozen = frozen_metrics()
    all_feature_candidates = base[base["candidate_id"].isin(set(complete["candidate_id"]) | set(frozen["candidate_id"]))].copy()
    static = source_features(all_feature_candidates)
    threshold = threshold_features(set(all_feature_candidates["candidate_id"]))
    features = all_feature_candidates.merge(static, on="candidate_id", validate="one_to_one").merge(
        threshold, on="candidate_id", validate="one_to_one"
    )

    discovery_all = complete.merge(internal, on="candidate_id", how="left", validate="one_to_one")
    discovery_all["evaluation_split"] = "INTERNAL_DISCOVERY"
    discovery_all["existing_history_negative_excluded"] = discovery_all["base_avg_pnl_pct"].lt(0)
    discovery_all["analysis_cohort"] = (
        discovery_all["recommended_static_status"].eq("PASS")
        & discovery_all["final_p99_weightless_block_status"].eq("PASS")
        & ~discovery_all["boil_block"]
        & discovery_all["eval_n"].ge(8)
    )
    discovery_cohort = discovery_all[discovery_all["analysis_cohort"]].copy()
    target_thresholds = derive_target_thresholds(discovery_cohort)
    discovery_all = apply_target(discovery_all, target_thresholds)
    discovery_cohort = discovery_all[discovery_all["analysis_cohort"]].copy()

    validation_all = base[base["candidate_id"].isin(set(frozen["candidate_id"]))].merge(
        frozen, on="candidate_id", validate="one_to_one"
    )
    validation_all["evaluation_split"] = "FROZEN_OOS_VALIDATION"
    validation_all["existing_history_negative_excluded"] = validation_all["base_avg_pnl_pct"].lt(0)
    validation_all["analysis_cohort"] = (
        validation_all["recommended_static_status"].eq("PASS")
        & validation_all["final_p99_weightless_block_status"].eq("PASS")
        & ~validation_all["boil_block"]
        & validation_all["eval_n"].ge(8)
    )
    validation_all = apply_target(validation_all, target_thresholds)
    validation_cohort = validation_all[validation_all["analysis_cohort"]].copy()

    target_columns = [
        "evaluation_split", "candidate_id", "stage", "ticker", "rulebook_hash", "activity_rule_hash",
        "recommended_static_status", "final_p99_weightless_block_status", "boil_block", "analysis_cohort",
        "existing_history_negative_excluded", "base_n", "base_avg_pnl_pct", "base_win_rate_pct",
        "eval_n", "eval_avg_pnl_pct", "eval_win_rate_pct", "eval_min_pnl_pct", "eval_p05_pnl_pct",
        "eval_avg_mae_pct", "eval_worst_mae_pct", "eval_avg_mfe_pct", "eval_worst_to_median_win",
        "eval_top3_loss_share", "eval_max_loss_share", "is_oos_gap_pp", "oos_to_is_pnl_ratio",
        "target_is_oos_collapse", "target_positive_tail_risk", "target_high_win_large_loss",
        "target_bad", "target_reason",
    ]
    targets = pd.concat([discovery_all[target_columns], validation_all[target_columns]], ignore_index=True)
    targets.to_csv(TARGET_OUT, index=False, compression="gzip")

    discovery = discovery_cohort.merge(features, on=[c for c in base.columns if c in features.columns and c != "candidate_id"], how="left") if False else discovery_cohort.merge(
        features.drop(columns=[c for c in features.columns if c in discovery_cohort.columns and c != "candidate_id"]),
        on="candidate_id", how="left", validate="one_to_one"
    )
    validation = validation_cohort.merge(
        features.drop(columns=[c for c in features.columns if c in validation_cohort.columns and c != "candidate_id"]),
        on="candidate_id", how="left", validate="one_to_one"
    )
    discovery["matrix_split"] = "INTERNAL_DISCOVERY"
    validation["matrix_split"] = "FROZEN_OOS_VALIDATION"
    feature_matrix = pd.concat([discovery, validation], ignore_index=True)
    feature_matrix.to_csv(FEATURE_MATRIX_OUT, index=False, compression="gzip")

    excluded = {
        "candidate_id", "stage", "ticker", "rulebook_hash", "source_file", "source_row_index", "done_marker",
        "profile_eligible", "origin_complete", "period_count", "all_history_n", "all_history_avg_pnl_pct",
        "all_history_win_rate_pct", "base_n", "base_avg_pnl_pct", "base_win_rate_pct", "holdout_n",
        "holdout_avg_pnl_pct", "holdout_win_rate_pct", "history_avg_atr_pct", "vol_group", "weight_volume_surge",
        "check_complete", "check_history", "check_boil", "check_ce", "ce_ratio", "ce_top2_share_pct",
        "static_status", "static_fail_reasons", "static_hold_reasons", "elite_static_pass", "elite_filter_reason",
        "elite_score", "denylisted", "selected_static", "selected_stage_rank", "oos_expectancy_pct", "oos_fitness",
        "oos_win_rate", "oos_trade_count", "worst_drawdown_pct", "signal_threshold", "volume_surge_ratio",
        "recommended_static_status", "history_win_monitor", "boil_monitor", "ce_monitor",
        "final_p99_weightless_block_status", "activity_rule_hash", "boil_block", "evaluation_split", "analysis_cohort",
        "existing_history_negative_excluded", "eval_n", "eval_avg_pnl_pct", "eval_win_rate_pct", "eval_min_pnl_pct",
        "eval_p05_pnl_pct", "eval_avg_mae_pct", "eval_worst_mae_pct", "eval_avg_mfe_pct", "eval_median_win_pct",
        "eval_worst_to_median_win", "eval_top3_loss_share", "eval_max_loss_share", "is_oos_gap_pp",
        "oos_to_is_pnl_ratio", "target_is_oos_collapse", "target_positive_tail_risk",
        "target_high_win_large_loss", "target_bad", "target_reason", "matrix_split",
    }
    feature_columns = [
        column for column in discovery.columns
        if column not in excluded
        and pd.api.types.is_numeric_dtype(discovery[column])
        and discovery[column].notna().mean() >= 0.85
        and validation[column].notna().mean() >= 0.85
        and discovery[column].nunique(dropna=True) > 1
    ]
    power, fitted = univariate_analysis(discovery, validation, feature_columns)
    power.to_csv(FEATURE_POWER_OUT, index=False)
    pairs = pair_analysis(discovery, validation, power, fitted)
    pairs.to_csv(PAIR_POWER_OUT, index=False)

    replicated_features = power[power["replicated_static_predictor"]]
    replicated_pairs = pairs[pairs.get("replicated_pair", pd.Series(False, index=pairs.index))] if len(pairs) else pairs
    best = power.iloc[0] if len(power) else None
    if len(replicated_features) or len(replicated_pairs):
        verdict = "STATIC_PREDICTOR_FOUND"
    elif best is not None and best["validation_auc"] >= 0.55 and best["validation_risk_difference"] > 0:
        verdict = "WEAK"
    else:
        verdict = "NO_STATIC_PREDICTOR"

    ce7_result = ce7.merge(features, on=["candidate_id", "stage", "ticker"], how="left", validate="one_to_one")
    ce7_result = ce7_result.merge(
        validation_all[["candidate_id", "target_bad", "target_reason"]], on="candidate_id", how="left", validate="one_to_one"
    )
    ce7_result["frozen_target_available"] = ce7_result["target_bad"].notna()
    for feature in power["feature"].head(10):
        if feature not in fitted:
            continue
        boundary = fitted[feature][0]
        raw = ce7_result[feature]
        flags = []
        for row in ce7_result.itertuples(index=False):
            value = getattr(row, feature)
            threshold_value = boundary.raw_thresholds.get(str(row.stage), math.nan)
            if not math.isfinite(safe_float(value)) or not math.isfinite(safe_float(threshold_value)):
                flags.append(False)
            elif boundary.direction == ">=":
                flags.append(float(value) >= threshold_value)
            else:
                flags.append(float(value) <= threshold_value)
        ce7_result[f"flag_{feature}"] = flags
    ce7_result.to_csv(CE7_OUT, index=False)

    curve_rows = []
    for row in power.itertuples(index=False):
        if row.discovery_supported and not row.validation_supported:
            curve_rows.append(
                {
                    "type": "IS_ONLY_FEATURE",
                    "feature_or_pair": row.feature,
                    "discovery_auc": row.discovery_auc,
                    "discovery_risk_difference": row.discovery_risk_difference,
                    "validation_auc": row.validation_auc,
                    "validation_risk_difference": row.validation_risk_difference,
                    "validation_ci_low": row.validation_risk_difference_ci_low,
                    "note": "internal discovery separation did not survive frozen OOS",
                }
            )
    if len(pairs):
        for row in pairs.itertuples(index=False):
            if row.discovery_risk_difference_ci_low > 0 and not row.validation_supported:
                curve_rows.append(
                    {
                        "type": "IS_ONLY_PAIR",
                        "feature_or_pair": f"{row.left_feature} {row.operator} {row.right_feature}",
                        "discovery_auc": math.nan,
                        "discovery_risk_difference": row.discovery_risk_difference,
                        "validation_auc": math.nan,
                        "validation_risk_difference": row.validation_risk_difference,
                        "validation_ci_low": row.validation_risk_difference_ci_low,
                        "note": "two-feature combination failed external frozen validation",
                    }
                )
    curve = pd.DataFrame(curve_rows)
    curve.to_csv(CURVE_FIT_OUT, index=False)

    frozen_ce = ce7_result[ce7_result["frozen_target_available"]]
    ce_target_capture = {
        "ce7_total": 7,
        "frozen_available": int(frozen_ce["frozen_target_available"].sum()),
        "frozen_target_bad": int(frozen_ce["target_bad"].sum()),
        "frozen_target_good": int((~frozen_ce["target_bad"].astype(bool)).sum()),
        "missing_frozen_ids": ce7_result.loc[~ce7_result["frozen_target_available"], "candidate_id"].tolist(),
        "target_bad_ids": frozen_ce.loc[frozen_ce["target_bad"].astype(bool), "candidate_id"].tolist(),
    }
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "method": {
            "discovery": "complete current-gate survivors; internal holdout labels",
            "validation": "frozen live93 OOS; untouched during threshold selection",
            "existing_gate_exclusions": ["base/history negative or non-PASS", "v3 FAIL", "BOIL BLOCK"],
            "single_feature_boundary": "stage-normalized empirical percentile selected on discovery only",
            "pair_limit": 2,
            "bootstrap": f"ticker-cluster {BOOTSTRAP_REPS} reps",
        },
        "target_definition": target_thresholds,
        "cohorts": {
            "discovery_n": len(discovery),
            "discovery_bad_n": int(discovery["target_bad"].sum()),
            "validation_n": len(validation),
            "validation_bad_n": int(validation["target_bad"].sum()),
            "frozen_total_n": len(validation_all),
        },
        "features_tested": len(power),
        "pairs_tested": len(pairs),
        "replicated_feature_n": len(replicated_features),
        "replicated_pair_n": len(replicated_pairs),
        "best_feature": None if best is None else best.to_dict(),
        "ce7_target_validation": ce_target_capture,
        "conclusion": (
            "A static gate candidate replicated on frozen OOS. Review leakage and multiplicity before gate consideration."
            if verdict == "STATIC_PREDICTOR_FOUND"
            else "Some direction replicated but confidence intervals or multiplicity are insufficient for a gate."
            if verdict == "WEAK"
            else "No rulebook-static feature or two-feature combination survived frozen OOS validation; dynamic observation logging remains necessary."
        ),
        "no_design_change": True,
        "operational_implementation": False,
        "source_rule_mutation": False,
        "live_change": False,
        "retraining": False,
        "order": False,
        "delete": False,
    }
    SUMMARY_OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)), encoding="utf-8")

    top_rows = power.head(10)
    lines = [
        "# CE형 개체 정적 예측 특징 탐색",
        "",
        f"- 판정: **{verdict}**",
        "- 데이터: 룰북·내부 holdout·frozen OOS read-only",
        "- 동적 realized component/current market 입력: 사용하지 않음",
        "- 설계·운영 구현 변경: 없음",
        "",
        "## 1. 타깃 정의",
        "",
        "기존 history 평균 PnL 음수/비PASS, v3 FAIL, BOIL BLOCK은 증분 분석에서 제외했다. 나머지 후보 중 내부 discovery 분포로 stage별 결과 임계를 고정하고 frozen OOS에 그대로 적용했다.",
        "",
        "- IS→OOS 붕괴: PnL 격차가 stage discovery 상위 10%이고 OOS/IS PnL 비율이 0.5 이하",
        "- 양의 평균 극단 tail: 평균 PnL>0이지만 worst MAE가 stage discovery 하위 10% 이하",
        "- 고승률 대형손실: 승률 상위 25%, worst/median-win 비율 상위 25%, top3 loss share가 중앙값 이상",
        "",
        f"- discovery: {len(discovery):,}개 중 bad {int(discovery['target_bad'].sum()):,}개",
        f"- frozen validation: {len(validation):,}개 중 bad {int(validation['target_bad'].sum()):,}개",
        "",
        "## 2. CE 7개 타깃 타당성",
        "",
        f"- frozen 결과 존재: {ce_target_capture['frozen_available']}/7",
        f"- 정의상 bad: {ce_target_capture['frozen_target_bad']}/{ce_target_capture['frozen_available']}",
        f"- frozen 미존재: {', '.join(ce_target_capture['missing_frozen_ids']) or '없음'}",
        "",
        "CE7은 동적 CE 조건으로 선정된 집합이지 모두 frozen 결과상 붕괴한 집합은 아니다. 정적 예측 탐색은 실제 결과 타깃과 CE 동적 라벨을 구분한다.",
        "",
        "## 3. 단일 특징 상위 결과",
        "",
        "| 특징 | IS AUC | IS RD | frozen AUC | frozen RD | frozen RD 95% CI | 재현 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in top_rows.itertuples(index=False):
        lines.append(
            f"| {row.feature} | {row.discovery_auc:.3f} | {row.discovery_risk_difference:.3f} | "
            f"{row.validation_auc:.3f} | {row.validation_risk_difference:.3f} | "
            f"[{row.validation_risk_difference_ci_low:.3f}, {row.validation_risk_difference_ci_high:.3f}] | "
            f"{'YES' if row.replicated_static_predictor else 'NO'} |"
        )
    lines += [
        "",
        "## 4. 2개 특징 조합",
        "",
        f"- 탐색 조합 수: {len(pairs):,}",
        f"- frozen 재현 조합: {len(replicated_pairs):,}",
        "- 조합은 discovery 상위 5개 특징에서 AND/OR만 허용해 2개 이하로 제한했다.",
        "",
        "## 5. 최종 판정",
        "",
    ]
    if verdict == "STATIC_PREDICTOR_FOUND":
        lines += [
            "**STATIC_PREDICTOR_FOUND**", "",
            "frozen OOS에서도 CI가 0을 배제한 정적 특징이 확인됐다. 다중검정·누수 재점검 후 네 번째 정적 게이트 후보로만 검토한다.",
        ]
    elif verdict == "WEAK":
        lines += [
            "**WEAK**", "",
            "일부 방향성은 frozen에서도 유지됐지만 bootstrap CI 또는 표본 규모가 게이트 기준을 충족하지 못했다. MONITOR 후보 이상으로 올리면 안 된다.",
        ]
    else:
        lines += [
            "**NO_STATIC_PREDICTOR**", "",
            "IS에서 보이던 분리력이 frozen OOS에서 깨졌거나 CI가 0을 포함했다. 룰북 정적 특징만으로 CE형 실패를 사전에 안정적으로 거를 근거가 없다.",
            "동적 observation logging 경로가 유일한 검증 방향으로 남는다.",
        ]
    lines += [
        "",
        "## 6. 커브피팅 점검",
        "",
        f"- IS에서만 유효하고 frozen에서 실패한 단일/조합: {len(curve):,}개",
        "- 모든 경계는 discovery에서만 선택했고 frozen은 한 번만 적용했다.",
        "- stage별 척도 차이는 discovery empirical percentile로 정규화했다.",
        "- bootstrap은 ticker cluster 단위로 수행했다.",
        "",
        "## 7. 산출물",
        "",
        f"- `{TARGET_OUT.name}`",
        f"- `{FEATURE_MATRIX_OUT.name}`",
        f"- `{FEATURE_POWER_OUT.name}`",
        f"- `{PAIR_POWER_OUT.name}`",
        f"- `{CE7_OUT.name}`",
        f"- `{CURVE_FIT_OUT.name}`",
        f"- `{SUMMARY_OUT.name}`",
    ]
    READOUT_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
