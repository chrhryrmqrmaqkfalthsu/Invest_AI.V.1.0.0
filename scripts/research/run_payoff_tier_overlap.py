#!/usr/bin/env python3
"""Leak-safe ATR payoff tier overlap experiment.

Separate detectors:
- UP tier: next_high_atr >= level
- LOW_SAFE tier: next_low_atr <= level
- BAD_RISK: next_low_atr >= bad_low_atr

No target-derived columns are allowed in features.
Model fit split:
- train: stress + train1 + train2
- validation/threshold selection: train3
- final test: oos
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
    ("train1", "2022-07-01", "2023-06-30"),
    ("train2", "2023-07-01", "2024-06-30"),
    ("train3", "2024-07-01", "2025-06-30"),
    ("oos", "2025-07-01", "2026-06-30"),
]
TRAIN_FIT = ["stress", "train1", "train2"]
VALIDATION = "train3"
TARGET_COLUMNS = {
    "high_pct_label",
    "low_mag_pct_label",
    "high_bin",
    "low_bin",
    "next_high_atr",
    "next_low_atr",
    "PAYOFF_SCORE",
    "BAD_RISK_DAY",
}


def load_runner():
    spec = importlib.util.spec_from_file_location("range_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load runner: {RUNNER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["range_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


def add_targets(df: pd.DataFrame, bad_low_atr: float) -> pd.DataFrame:
    out = df.copy()
    atr = out["D1_ATR_pct"].astype(float).clip(lower=1e-9)
    out["next_high_atr"] = out["high_pct_label"].astype(float) / atr
    out["next_low_atr"] = out["low_mag_pct_label"].astype(float) / atr
    out["PAYOFF_SCORE"] = out["next_high_atr"] - out["next_low_atr"]
    out["BAD_RISK_DAY"] = (out["next_low_atr"] >= bad_low_atr).astype(int)
    return out


def safe_features(raw_features: list[str], df: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    features: list[str] = []
    excluded: list[str] = []
    suspicious: list[str] = []
    for f in raw_features:
        fl = f.lower()
        if f in TARGET_COLUMNS or any(tok in fl for tok in ["next_high_atr", "next_low_atr", "payoff", "bad_risk", "good_long"]):
            excluded.append(f)
            if f in df.columns:
                suspicious.append(f)
            continue
        if f not in df.columns:
            excluded.append(f)
            continue
        features.append(f)
    audit = {
        "feature_source": "runner.L.feature_columns(raw_data_before_target_creation)",
        "feature_count": len(features),
        "excluded_count": len(excluded),
        "excluded_features": excluded,
        "suspicious_features": suspicious,
        "target_columns_present_in_features": sorted(set(features) & TARGET_COLUMNS),
    }
    if audit["target_columns_present_in_features"] or suspicious:
        raise RuntimeError(f"feature leakage audit failed: {audit}")
    return features, audit


def X(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    out = df[features].copy()
    for c in out.columns:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.replace([np.inf, -np.inf], np.nan)


def fit_models(x: pd.DataFrame, y: np.ndarray, seed: int) -> list[tuple[str, Any]]:
    models: list[tuple[str, Any]] = [
        (
            "hgb",
            make_pipeline(
                SimpleImputer(strategy="median"),
                HistGradientBoostingClassifier(
                    max_iter=220,
                    learning_rate=0.035,
                    max_leaf_nodes=15,
                    min_samples_leaf=20,
                    l2_regularization=0.1,
                    class_weight="balanced",
                    random_state=seed,
                ),
            ),
        ),
        (
            "rf",
            make_pipeline(
                SimpleImputer(strategy="median"),
                RandomForestClassifier(
                    n_estimators=360,
                    max_depth=5,
                    min_samples_leaf=16,
                    max_features="sqrt",
                    class_weight="balanced_subsample",
                    random_state=seed + 17,
                    n_jobs=-1,
                ),
            ),
        ),
        (
            "logit",
            make_pipeline(
                SimpleImputer(strategy="median"),
                RobustScaler(),
                LogisticRegression(C=0.30, solver="liblinear", class_weight="balanced", max_iter=800, random_state=seed + 31),
            ),
        ),
    ]
    for _name, model in models:
        model.fit(x, np.asarray(y, dtype=int))
    return models


def predict(models: list[tuple[str, Any]], x: pd.DataFrame) -> np.ndarray:
    return np.mean(np.vstack([m.predict_proba(x)[:, 1] for _name, m in models]), axis=0)


def auc_safe(y: np.ndarray, p: np.ndarray) -> dict[str, float | None]:
    y = np.asarray(y, dtype=int)
    if len(np.unique(y)) < 2:
        return {"roc_auc": None, "avg_precision": None}
    return {"roc_auc": float(roc_auc_score(y, p)), "avg_precision": float(average_precision_score(y, p))}


def eval_up(df: pd.DataFrame, sig: np.ndarray, level: float) -> dict[str, Any]:
    sig = np.asarray(sig, dtype=bool)
    n = len(df)
    s = int(sig.sum())
    base = float((df["next_high_atr"] >= level).mean() * 100.0)
    if s == 0:
        return {"days": n, "signal_count": 0, "coverage_pct": 0.0, "hit": 0, "precision_pct": 0.0, "base_pct": base, "bad1_count": 0, "bad1_rate_pct": 0.0, "avg_high_atr": 0.0, "avg_low_atr": 0.0, "dates": []}
    sub = df.loc[sig]
    hit = (sub["next_high_atr"] >= level)
    bad = sub["BAD_RISK_DAY"].astype(bool)
    return {
        "days": n,
        "signal_count": s,
        "coverage_pct": float(s / max(1, n) * 100.0),
        "hit": int(hit.sum()),
        "precision_pct": float(hit.mean() * 100.0),
        "base_pct": base,
        "bad1_count": int(bad.sum()),
        "bad1_rate_pct": float(bad.mean() * 100.0),
        "avg_high_atr": float(sub["next_high_atr"].mean()),
        "avg_low_atr": float(sub["next_low_atr"].mean()),
        "dates": [str(x)[:10] for x in sub["date"].tolist()],
    }


def eval_low(df: pd.DataFrame, sig: np.ndarray, level: float) -> dict[str, Any]:
    sig = np.asarray(sig, dtype=bool)
    n = len(df)
    s = int(sig.sum())
    base = float((df["next_low_atr"] <= level).mean() * 100.0)
    if s == 0:
        return {"days": n, "signal_count": 0, "coverage_pct": 0.0, "hit": 0, "precision_pct": 0.0, "base_pct": base, "bad1_count": 0, "bad1_rate_pct": 0.0, "avg_high_atr": 0.0, "avg_low_atr": 0.0, "dates": []}
    sub = df.loc[sig]
    hit = (sub["next_low_atr"] <= level)
    bad = sub["BAD_RISK_DAY"].astype(bool)
    return {
        "days": n,
        "signal_count": s,
        "coverage_pct": float(s / max(1, n) * 100.0),
        "hit": int(hit.sum()),
        "precision_pct": float(hit.mean() * 100.0),
        "base_pct": base,
        "bad1_count": int(bad.sum()),
        "bad1_rate_pct": float(bad.mean() * 100.0),
        "avg_high_atr": float(sub["next_high_atr"].mean()),
        "avg_low_atr": float(sub["next_low_atr"].mean()),
        "dates": [str(x)[:10] for x in sub["date"].tolist()],
    }


def eval_combo(df: pd.DataFrame, sig: np.ndarray, up_level: float, low_level: float) -> dict[str, Any]:
    sig = np.asarray(sig, dtype=bool)
    n = len(df)
    s = int(sig.sum())
    if s == 0:
        return {"days": n, "signal_count": 0, "coverage_pct": 0.0, "up_hit": 0, "up_precision_pct": 0.0, "low_hit": 0, "low_precision_pct": 0.0, "both_hit": 0, "both_precision_pct": 0.0, "bad1_count": 0, "bad1_rate_pct": 0.0, "avg_high_atr": 0.0, "avg_low_atr": 0.0, "dates": []}
    sub = df.loc[sig]
    up = sub["next_high_atr"] >= up_level
    low = sub["next_low_atr"] <= low_level
    both = up & low
    bad = sub["BAD_RISK_DAY"].astype(bool)
    return {
        "days": n,
        "signal_count": s,
        "coverage_pct": float(s / max(1, n) * 100.0),
        "up_hit": int(up.sum()),
        "up_precision_pct": float(up.mean() * 100.0),
        "low_hit": int(low.sum()),
        "low_precision_pct": float(low.mean() * 100.0),
        "both_hit": int(both.sum()),
        "both_precision_pct": float(both.mean() * 100.0),
        "bad1_count": int(bad.sum()),
        "bad1_rate_pct": float(bad.mean() * 100.0),
        "avg_high_atr": float(sub["next_high_atr"].mean()),
        "avg_low_atr": float(sub["next_low_atr"].mean()),
        "dates": [str(x)[:10] for x in sub["date"].tolist()],
    }


def choose_threshold(df: pd.DataFrame, prob: np.ndarray, eval_fn, level: float, min_signals: int, max_coverage: float) -> dict[str, Any]:
    rows = []
    for t in np.arange(0.05, 0.96, 0.02):
        sig = prob >= t
        m = eval_fn(df, sig, level)
        count_pen = max(0, min_signals - m["signal_count"]) * 10.0
        cov_pen = max(0.0, m["coverage_pct"] - max_coverage) * 1.0
        score = m["precision_pct"] * 2.0 + m["signal_count"] * 0.10 - m["bad1_rate_pct"] * 1.5 - count_pen - cov_pen
        rows.append({"threshold": round(float(t), 4), "score": float(score), **{k: v for k, v in m.items() if k != "dates"}})
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[0]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="FIX")
    ap.add_argument("--up-levels", default="1.0,1.5,2.0")
    ap.add_argument("--low-levels", default="0.3,0.5,0.7,1.0")
    ap.add_argument("--bad-low-atr", type=float, default=1.0)
    ap.add_argument("--bad-safe-threshold", type=float, default=0.25)
    ap.add_argument("--min-signals", type=int, default=3)
    ap.add_argument("--max-up-coverage", type=float, default=25.0)
    ap.add_argument("--max-low-coverage", type=float, default=75.0)
    ap.add_argument("--random-state", type=int, default=20260706)
    ap.add_argument("--out-dir", default="exp_fix_payoff_tier_overlap_noleak_20260705_001")
    args = ap.parse_args(argv)

    up_levels = [float(x) for x in args.up_levels.split(",") if x.strip()]
    low_levels = [float(x) for x in args.low_levels.split(",") if x.strip()]

    runner = load_runner()
    raw, _ = runner.L.build_dataset(args.ticker)
    raw_features = [f for f in runner.L.feature_columns(raw) if f in raw.columns]
    data = add_targets(raw, args.bad_low_atr)
    features, audit = safe_features(raw_features, data)
    frames = {name: runner.period_frame_checked(data, start, end, name).copy() for name, start, end in PERIODS}
    fit_df = pd.concat([frames[n] for n in TRAIN_FIT], axis=0).sort_values("date")
    val_df = frames[VALIDATION]
    x_fit = X(fit_df, features)

    up_models: dict[str, list[tuple[str, Any]]] = {}
    low_models: dict[str, list[tuple[str, Any]]] = {}
    for level in up_levels:
        up_models[str(level)] = fit_models(x_fit, (fit_df["next_high_atr"] >= level).astype(int), args.random_state + int(level * 100))
    for level in low_levels:
        low_models[str(level)] = fit_models(x_fit, (fit_df["next_low_atr"] <= level).astype(int), args.random_state + 1000 + int(level * 100))
    bad_models = fit_models(x_fit, fit_df["BAD_RISK_DAY"].astype(int), args.random_state + 2000)

    probs: dict[str, Any] = {"up": {}, "low": {}, "bad": {}}
    quality: dict[str, Any] = {"up": {}, "low": {}, "bad": {}}
    for name, df in frames.items():
        x = X(df, features)
        probs["bad"][name] = predict(bad_models, x)
        quality["bad"][name] = auc_safe(df["BAD_RISK_DAY"].astype(int), probs["bad"][name])
        for level in up_levels:
            key = str(level)
            probs["up"].setdefault(key, {})[name] = predict(up_models[key], x)
            quality["up"].setdefault(key, {})[name] = auc_safe((df["next_high_atr"] >= level).astype(int), probs["up"][key][name])
        for level in low_levels:
            key = str(level)
            probs["low"].setdefault(key, {})[name] = predict(low_models[key], x)
            quality["low"].setdefault(key, {})[name] = auc_safe((df["next_low_atr"] <= level).astype(int), probs["low"][key][name])

    selections = {"up": {}, "low": {}, "bad_safe_threshold": args.bad_safe_threshold}
    for level in up_levels:
        key = str(level)
        selections["up"][key] = choose_threshold(val_df, probs["up"][key][VALIDATION], eval_up, level, args.min_signals, args.max_up_coverage)
    for level in low_levels:
        key = str(level)
        selections["low"][key] = choose_threshold(val_df, probs["low"][key][VALIDATION], eval_low, level, args.min_signals, args.max_low_coverage)

    base_rates = {}
    for name, df in frames.items():
        base_rates[name] = {
            "days": int(len(df)),
            "bad_ge_1atr_pct": float(df["BAD_RISK_DAY"].mean() * 100.0),
            "up": {str(level): float((df["next_high_atr"] >= level).mean() * 100.0) for level in up_levels},
            "low_safe": {str(level): float((df["next_low_atr"] <= level).mean() * 100.0) for level in low_levels},
        }

    individual = {"up": {}, "low": {}, "bad_safe": {}}
    for level in up_levels:
        key = str(level)
        t = selections["up"][key]["threshold"]
        individual["up"][key] = {name: eval_up(df, probs["up"][key][name] >= t, level) for name, df in frames.items()}
    for level in low_levels:
        key = str(level)
        t = selections["low"][key]["threshold"]
        individual["low"][key] = {name: eval_low(df, probs["low"][key][name] >= t, level) for name, df in frames.items()}
    for name, df in frames.items():
        safe = probs["bad"][name] <= args.bad_safe_threshold
        individual["bad_safe"][name] = eval_low(df, safe, args.bad_low_atr)

    combos = []
    for up in up_levels:
        uk = str(up)
        usel = selections["up"][uk]["threshold"]
        for low in low_levels:
            lk = str(low)
            lsel = selections["low"][lk]["threshold"]
            row = {
                "up_level": up,
                "low_safe_level": low,
                "up_threshold": usel,
                "low_threshold": lsel,
                "bad_safe_threshold": args.bad_safe_threshold,
                "periods": {},
            }
            for name, df in frames.items():
                sig = (probs["up"][uk][name] >= usel) & (probs["low"][lk][name] >= lsel) & (probs["bad"][name] <= args.bad_safe_threshold)
                row["periods"][name] = eval_combo(df, sig, up, low)
            combos.append(row)

    out_dir = PROJECT_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "ticker": args.ticker,
        "split": {"fit": TRAIN_FIT, "validation_threshold_selection": VALIDATION, "final_oos": "oos"},
        "feature_audit": audit,
        "levels": {"up": up_levels, "low_safe": low_levels, "bad_low_atr": args.bad_low_atr},
        "base_rates": base_rates,
        "quality": quality,
        "selections": selections,
        "individual": individual,
        "combos": combos,
        "outputs": {"summary": str(out_dir / "summary.json")},
    }
    (out_dir / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(
        {
            "ticker": args.ticker,
            "out_dir": str(out_dir),
            "split": result["split"],
            "feature_audit": audit,
            "base_rates_oos": base_rates["oos"],
            "selections": selections,
            "quality_oos": {
                "up": {k: quality["up"][k]["oos"] for k in quality["up"]},
                "low": {k: quality["low"][k]["oos"] for k in quality["low"]},
                "bad": quality["bad"]["oos"],
            },
            "individual_oos": {
                "up": {k: {kk: vv for kk, vv in individual["up"][k]["oos"].items() if kk != "dates"} for k in individual["up"]},
                "low": {k: {kk: vv for kk, vv in individual["low"][k]["oos"].items() if kk != "dates"} for k in individual["low"]},
                "bad_safe": {kk: vv for kk, vv in individual["bad_safe"]["oos"].items() if kk != "dates"},
            },
            "combo_oos": [
                {
                    "up_level": c["up_level"],
                    "low_safe_level": c["low_safe_level"],
                    **{kk: vv for kk, vv in c["periods"]["oos"].items() if kk != "dates"},
                }
                for c in combos
            ],
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
