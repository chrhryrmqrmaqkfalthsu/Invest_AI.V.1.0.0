#!/usr/bin/env python3
"""ATR payoff pattern classifier.

This experiment stops predicting HIGH/LOW bins directly.
It trains explicit probability classifiers for:

GOOD_LONG_DAY:
    next_high_from_close_ATR >= good_high_atr
    and next_low_from_close_ATR <= good_max_low_atr

BAD_RISK_DAY:
    next_low_from_close_ATR >= bad_low_atr

A final long signal is:
    P(GOOD_LONG_DAY) >= threshold_good
    and P(BAD_RISK_DAY) <= threshold_bad

Thresholds are selected only on stress/train periods, then reported on OOS.

Leakage guard:
- The feature list is captured BEFORE target columns are added.
- All derived target columns and label columns are force-excluded.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import RobustScaler

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_ROOT / "scripts/research/run_range_predictor_stage2_v3.py"

PERIODS = [
    ("stress", "2020-07-01", "2022-06-30"),
    ("train3", "2024-07-01", "2025-06-30"),
    ("train2", "2023-07-01", "2024-06-30"),
    ("train1", "2022-07-01", "2023-06-30"),
    ("oos", "2025-07-01", "2026-06-30"),
]
TRAIN_NAMES = ["stress", "train3", "train2", "train1"]
TARGET_DERIVED_COLUMNS = {
    "GOOD_LONG_DAY",
    "BAD_RISK_DAY",
    "PAYOFF_SCORE",
    "next_high_atr",
    "next_low_atr",
    "high_pct_label",
    "low_mag_pct_label",
    "high_bin",
    "low_bin",
}
LEAKY_NAME_TOKENS = (
    "good_long_day",
    "bad_risk_day",
    "payoff_score",
    "next_high_atr",
    "next_low_atr",
    "high_pct_label",
    "low_mag_pct_label",
    "high_bin",
    "low_bin",
)


def load_runner():
    spec = importlib.util.spec_from_file_location("range_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load runner: {RUNNER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["range_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def make_targets(df: pd.DataFrame, good_high_atr: float, good_max_low_atr: float, bad_low_atr: float) -> pd.DataFrame:
    out = df.copy()
    atr = out["D1_ATR_pct"].astype(float).clip(lower=1e-9)
    out["next_high_atr"] = out["high_pct_label"].astype(float) / atr
    out["next_low_atr"] = out["low_mag_pct_label"].astype(float) / atr
    out["GOOD_LONG_DAY"] = ((out["next_high_atr"] >= good_high_atr) & (out["next_low_atr"] <= good_max_low_atr)).astype(int)
    out["BAD_RISK_DAY"] = (out["next_low_atr"] >= bad_low_atr).astype(int)
    out["PAYOFF_SCORE"] = out["next_high_atr"] - out["next_low_atr"]
    return out


def safe_feature_list(raw_features: list[str], df: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    features: list[str] = []
    excluded: list[str] = []
    suspicious: list[str] = []
    for f in raw_features:
        fl = str(f).lower()
        if f in TARGET_DERIVED_COLUMNS or any(tok == fl for tok in LEAKY_NAME_TOKENS):
            excluded.append(f)
            continue
        if f not in df.columns:
            excluded.append(f)
            continue
        # Keep legitimate previous/current-day names like D1_close_vs_prev_high_pct.
        # Only exact target-derived names are force-excluded.
        if any(tok in fl for tok in ("good_long", "bad_risk", "payoff", "next_high_atr", "next_low_atr")):
            suspicious.append(f)
            excluded.append(f)
            continue
        features.append(f)
    audit = {
        "feature_source": "runner.L.feature_columns(raw_data_before_target_creation)",
        "feature_count": len(features),
        "excluded_count": len(excluded),
        "excluded_features": excluded,
        "suspicious_features": suspicious,
        "target_columns_present_in_features": sorted(set(features) & TARGET_DERIVED_COLUMNS),
    }
    if audit["target_columns_present_in_features"] or suspicious:
        raise RuntimeError(f"feature leakage audit failed: {audit}")
    return features, audit


def numeric_matrix(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    x = df[features].copy()
    for c in x.columns:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.replace([np.inf, -np.inf], np.nan)


def fit_models(x: pd.DataFrame, y: np.ndarray, random_state: int) -> list[tuple[str, Any]]:
    y = np.asarray(y, dtype=int)
    models: list[tuple[str, Any]] = []
    models.append(
        (
            "hgb",
            make_pipeline(
                SimpleImputer(strategy="median"),
                HistGradientBoostingClassifier(
                    max_iter=240,
                    learning_rate=0.035,
                    max_leaf_nodes=15,
                    min_samples_leaf=20,
                    l2_regularization=0.10,
                    class_weight="balanced",
                    random_state=random_state,
                ),
            ),
        )
    )
    models.append(
        (
            "rf",
            make_pipeline(
                SimpleImputer(strategy="median"),
                RandomForestClassifier(
                    n_estimators=420,
                    max_depth=5,
                    min_samples_leaf=16,
                    max_features="sqrt",
                    class_weight="balanced_subsample",
                    random_state=random_state + 17,
                    n_jobs=-1,
                ),
            ),
        )
    )
    models.append(
        (
            "logit",
            make_pipeline(
                SimpleImputer(strategy="median"),
                RobustScaler(with_centering=True, with_scaling=True),
                LogisticRegression(
                    C=0.30,
                    penalty="l2",
                    solver="liblinear",
                    class_weight="balanced",
                    max_iter=800,
                    random_state=random_state + 31,
                ),
            ),
        )
    )
    fitted: list[tuple[str, Any]] = []
    for name, model in models:
        model.fit(x, y)
        fitted.append((name, model))
    return fitted


def predict_ensemble(models: list[tuple[str, Any]], x: pd.DataFrame) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    probs: dict[str, np.ndarray] = {}
    for name, model in models:
        p = model.predict_proba(x)[:, 1]
        probs[name] = np.asarray(p, dtype=float)
    ens = np.mean(np.vstack(list(probs.values())), axis=0)
    probs["ensemble"] = ens
    return ens, probs


def auc_safe(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    y = np.asarray(y, dtype=int)
    if len(np.unique(y)) < 2:
        return {"roc_auc": None, "avg_precision": None}
    return {
        "roc_auc": float(roc_auc_score(y, p)),
        "avg_precision": float(average_precision_score(y, p)),
    }


def eval_signal(df: pd.DataFrame, signal: np.ndarray) -> dict[str, Any]:
    signal = np.asarray(signal, dtype=bool)
    n = int(len(df))
    s = int(np.sum(signal))
    base_good = float(df["GOOD_LONG_DAY"].mean() * 100.0) if n else 0.0
    base_bad = float(df["BAD_RISK_DAY"].mean() * 100.0) if n else 0.0
    if s <= 0:
        return {
            "period_days": n,
            "signal_count": 0,
            "coverage_pct": 0.0,
            "good_hits": 0,
            "bad_hits": 0,
            "precision_pct": 0.0,
            "bad_rate_pct": 0.0,
            "lift_vs_base_pp": -base_good,
            "base_good_pct": base_good,
            "base_bad_pct": base_bad,
            "avg_high_atr": 0.0,
            "avg_low_atr": 0.0,
            "avg_payoff_score": 0.0,
            "dates": [],
        }
    sub = df.loc[signal].copy()
    good_hits = int(sub["GOOD_LONG_DAY"].sum())
    bad_hits = int(sub["BAD_RISK_DAY"].sum())
    return {
        "period_days": n,
        "signal_count": s,
        "coverage_pct": float(s / max(1, n) * 100.0),
        "good_hits": good_hits,
        "bad_hits": bad_hits,
        "precision_pct": float(good_hits / s * 100.0),
        "bad_rate_pct": float(bad_hits / s * 100.0),
        "lift_vs_base_pp": float(good_hits / s * 100.0 - base_good),
        "base_good_pct": base_good,
        "base_bad_pct": base_bad,
        "avg_high_atr": float(sub["next_high_atr"].mean()),
        "avg_low_atr": float(sub["next_low_atr"].mean()),
        "avg_payoff_score": float(sub["PAYOFF_SCORE"].mean()),
        "dates": [str(x)[:10] for x in sub["date"].tolist()] if "date" in sub.columns else [str(x)[:10] for x in sub.index.tolist()],
    }


def score_threshold(metrics_by_period: dict[str, dict[str, Any]], min_signals: int, target_precision: float, max_bad_rate: float, max_coverage: float) -> float:
    vals = [metrics_by_period[p] for p in TRAIN_NAMES]
    counts = [m["signal_count"] for m in vals]
    precisions = [m["precision_pct"] for m in vals]
    bad_rates = [m["bad_rate_pct"] for m in vals]
    coverages = [m["coverage_pct"] for m in vals]
    mean_precision = float(np.mean(precisions))
    min_precision = float(np.min(precisions))
    mean_bad = float(np.mean(bad_rates))
    max_bad = float(np.max(bad_rates))
    mean_cov = float(np.mean(coverages))
    count_penalty = sum(max(0, min_signals - c) for c in counts) * 12.0
    precision_shortfall = max(0.0, target_precision - mean_precision) * 2.0 + max(0.0, target_precision - min_precision) * 0.8
    bad_penalty = max(0.0, mean_bad - max_bad_rate) * 2.0 + max(0.0, max_bad - max_bad_rate) * 0.6
    coverage_penalty = max(0.0, mean_cov - max_coverage) * 1.5
    no_signal_penalty = 100.0 if sum(counts) == 0 else 0.0
    return float(mean_precision * 2.2 + min_precision * 1.0 - mean_bad * 1.4 - max_bad * 0.5 + mean_cov * 0.15 - count_penalty - precision_shortfall - bad_penalty - coverage_penalty - no_signal_penalty)


def choose_thresholds(frames: dict[str, pd.DataFrame], good_probs: dict[str, np.ndarray], bad_probs: dict[str, np.ndarray], args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    good_grid = np.round(np.arange(args.good_threshold_min, args.good_threshold_max + 1e-9, args.threshold_step), 4)
    bad_grid = np.round(np.arange(args.bad_threshold_min, args.bad_threshold_max + 1e-9, args.threshold_step), 4)
    for gt in good_grid:
        for bt in bad_grid:
            metrics: dict[str, dict[str, Any]] = {}
            for name in TRAIN_NAMES:
                sig = (good_probs[name] >= gt) & (bad_probs[name] <= bt)
                metrics[name] = eval_signal(frames[name], sig)
            score = score_threshold(metrics, args.min_signals_per_period, args.target_precision_pct, args.max_bad_rate_pct, args.max_coverage_pct)
            row = {
                "good_threshold": float(gt),
                "bad_max_threshold": float(bt),
                "score": score,
                "train_mean_precision_pct": float(np.mean([metrics[p]["precision_pct"] for p in TRAIN_NAMES])),
                "train_min_precision_pct": float(np.min([metrics[p]["precision_pct"] for p in TRAIN_NAMES])),
                "train_mean_bad_rate_pct": float(np.mean([metrics[p]["bad_rate_pct"] for p in TRAIN_NAMES])),
                "train_max_bad_rate_pct": float(np.max([metrics[p]["bad_rate_pct"] for p in TRAIN_NAMES])),
                "train_mean_coverage_pct": float(np.mean([metrics[p]["coverage_pct"] for p in TRAIN_NAMES])),
                "train_min_signal_count": int(np.min([metrics[p]["signal_count"] for p in TRAIN_NAMES])),
                "metrics": metrics,
            }
            rows.append(row)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[0], rows[: min(50, len(rows))]


def top_logit_features(model: Any, features: list[str], top_n: int = 30) -> list[dict[str, Any]]:
    try:
        logit = model.named_steps["logisticregression"]
        coef = logit.coef_[0]
        pairs = sorted(zip(features, coef), key=lambda x: abs(float(x[1])), reverse=True)[:top_n]
        return [{"feature": f, "coef": round(float(c), 6)} for f, c in pairs]
    except Exception:
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="FIX")
    ap.add_argument("--good-high-atr", type=float, default=0.80)
    ap.add_argument("--good-max-low-atr", type=float, default=0.60)
    ap.add_argument("--bad-low-atr", type=float, default=1.00)
    ap.add_argument("--target-precision-pct", type=float, default=70.0)
    ap.add_argument("--max-bad-rate-pct", type=float, default=20.0)
    ap.add_argument("--max-coverage-pct", type=float, default=25.0)
    ap.add_argument("--min-signals-per-period", type=int, default=5)
    ap.add_argument("--good-threshold-min", type=float, default=0.15)
    ap.add_argument("--good-threshold-max", type=float, default=0.95)
    ap.add_argument("--bad-threshold-min", type=float, default=0.05)
    ap.add_argument("--bad-threshold-max", type=float, default=0.75)
    ap.add_argument("--threshold-step", type=float, default=0.02)
    ap.add_argument("--random-state", type=int, default=20260705)
    ap.add_argument("--out-dir", default="exp_fix_payoff_pattern_classifier_20260705_001")
    args = ap.parse_args(argv)

    runner = load_runner()
    raw_data, _ = runner.L.build_dataset(args.ticker)
    raw_features = [f for f in runner.L.feature_columns(raw_data) if f in raw_data.columns]
    data = make_targets(raw_data, args.good_high_atr, args.good_max_low_atr, args.bad_low_atr)
    features, feature_audit = safe_feature_list(raw_features, data)
    frames = {name: runner.period_frame_checked(data, start, end, name).copy() for name, start, end in PERIODS}
    train_df = pd.concat([frames["train1"], frames["train2"], frames["train3"]], axis=0).sort_values("date")

    x_train = numeric_matrix(train_df, features)
    y_good = train_df["GOOD_LONG_DAY"].to_numpy(dtype=int)
    y_bad = train_df["BAD_RISK_DAY"].to_numpy(dtype=int)

    good_models = fit_models(x_train, y_good, args.random_state)
    bad_models = fit_models(x_train, y_bad, args.random_state + 1000)

    good_probs: dict[str, np.ndarray] = {}
    bad_probs: dict[str, np.ndarray] = {}
    model_quality: dict[str, Any] = {"GOOD_LONG_DAY": {}, "BAD_RISK_DAY": {}}
    for name, df in frames.items():
        x = numeric_matrix(df, features)
        gp, _ = predict_ensemble(good_models, x)
        bp, _ = predict_ensemble(bad_models, x)
        good_probs[name] = gp
        bad_probs[name] = bp
        model_quality["GOOD_LONG_DAY"][name] = auc_safe(df["GOOD_LONG_DAY"].to_numpy(dtype=int), gp)
        model_quality["BAD_RISK_DAY"][name] = auc_safe(df["BAD_RISK_DAY"].to_numpy(dtype=int), bp)

    best, top_thresholds = choose_thresholds(frames, good_probs, bad_probs, args)
    gt = float(best["good_threshold"])
    bt = float(best["bad_max_threshold"])

    final_metrics: dict[str, Any] = {}
    for name, df in frames.items():
        sig = (good_probs[name] >= gt) & (bad_probs[name] <= bt)
        final_metrics[name] = eval_signal(df, sig)
        final_metrics[name]["good_prob_mean_signal"] = float(np.mean(good_probs[name][sig])) if int(np.sum(sig)) else 0.0
        final_metrics[name]["bad_prob_mean_signal"] = float(np.mean(bad_probs[name][sig])) if int(np.sum(sig)) else 0.0

    oos = frames["oos"]
    oos_sig = (good_probs["oos"] >= gt) & (bad_probs["oos"] <= bt)
    oos_signal_rows = oos.loc[oos_sig, ["date", "high_pct_label", "low_mag_pct_label", "D1_ATR_pct", "next_high_atr", "next_low_atr", "GOOD_LONG_DAY", "BAD_RISK_DAY", "PAYOFF_SCORE"]].copy()
    if len(oos_signal_rows):
        oos_signal_rows["good_prob"] = good_probs["oos"][oos_sig]
        oos_signal_rows["bad_prob"] = bad_probs["oos"][oos_sig]

    out_dir = PROJECT_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "ticker": args.ticker,
        "target": {
            "GOOD_LONG_DAY": f"next_high_atr >= {args.good_high_atr} and next_low_atr <= {args.good_max_low_atr}",
            "BAD_RISK_DAY": f"next_low_atr >= {args.bad_low_atr}",
            "next_high_atr": "high_pct_label / D1_ATR_pct",
            "next_low_atr": "low_mag_pct_label / D1_ATR_pct",
        },
        "features": {"count": len(features), "audit": feature_audit},
        "train_rows": int(len(train_df)),
        "period_base_rates": {
            name: {
                "rows": int(len(df)),
                "good_pct": float(df["GOOD_LONG_DAY"].mean() * 100.0),
                "bad_pct": float(df["BAD_RISK_DAY"].mean() * 100.0),
                "avg_next_high_atr": float(df["next_high_atr"].mean()),
                "avg_next_low_atr": float(df["next_low_atr"].mean()),
            }
            for name, df in frames.items()
        },
        "model_quality": model_quality,
        "chosen_threshold": {
            "good_threshold": gt,
            "bad_max_threshold": bt,
            "selection_score": float(best["score"]),
            "train_mean_precision_pct": float(best["train_mean_precision_pct"]),
            "train_min_precision_pct": float(best["train_min_precision_pct"]),
            "train_mean_bad_rate_pct": float(best["train_mean_bad_rate_pct"]),
            "train_max_bad_rate_pct": float(best["train_max_bad_rate_pct"]),
            "train_mean_coverage_pct": float(best["train_mean_coverage_pct"]),
            "train_min_signal_count": int(best["train_min_signal_count"]),
        },
        "final_metrics": final_metrics,
        "top_thresholds": top_thresholds,
        "top_logit_features": {
            "GOOD_LONG_DAY": top_logit_features(dict(good_models)["logit"], features, 30),
            "BAD_RISK_DAY": top_logit_features(dict(bad_models)["logit"], features, 30),
        },
        "outputs": {
            "summary": str(out_dir / "summary.json"),
            "oos_signals": str(out_dir / "oos_signals.csv"),
            "top_thresholds": str(out_dir / "top_thresholds.json"),
        },
    }

    (out_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "top_thresholds.json").write_text(json.dumps(top_thresholds, indent=2, ensure_ascii=False), encoding="utf-8")
    oos_signal_rows.to_csv(out_dir / "oos_signals.csv", index=False)

    print(json.dumps({
        "ticker": args.ticker,
        "out_dir": str(out_dir),
        "target": result["target"],
        "feature_audit": feature_audit,
        "chosen_threshold": result["chosen_threshold"],
        "model_quality_oos": {
            "GOOD_LONG_DAY": model_quality["GOOD_LONG_DAY"].get("oos"),
            "BAD_RISK_DAY": model_quality["BAD_RISK_DAY"].get("oos"),
        },
        "final_metrics": {k: {kk: vv for kk, vv in v.items() if kk != "dates"} for k, v in final_metrics.items()},
        "oos_signal_dates": final_metrics["oos"]["dates"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
