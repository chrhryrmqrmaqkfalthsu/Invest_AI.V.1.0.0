"""B-1a sell-omen ML POC.

이 스크립트는 실전 성능 확인용이 아니라, 하락 전조 ML 파이프라인이
누출 없이 동작하는지 확인하기 위한 구조 검증용 POC다.

Label:
    향후 10거래일 내 종가 기준 -5% 이하 하락 여부.

Split:
    train: 2020-2023, 단 label horizon이 2023-12-31을 넘는 row 제외
    valid: 2024, 단 label horizon이 2024-12-31을 넘는 row 제외
    oos:   2025-2026

Leakage guard:
    feature 이름에 fwd/future/forward/label/target가 포함되면 즉시 중단한다.
    GPT 시장 이벤트 위험군인 has_*는 기본 제외한다.
    종목별 스케일을 먹는 절대 OHLCV 레벨도 기본 제외한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "data" / "_system" / "condition_db"
DEFAULT_HORIZON_DAYS = 10
DEFAULT_DROP_THRESHOLD_PCT = -5.0
TRAIN_END = pd.Timestamp("2023-12-31")
VALID_START = pd.Timestamp("2024-01-01")
VALID_END = pd.Timestamp("2024-12-31")
OOS_START = pd.Timestamp("2025-01-01")

LEAK_KEYWORDS = ("fwd", "future", "forward", "label", "target")
IDENTITY_COLUMNS = {"Date", "ticker"}
BASE_EXCLUDE_COLUMNS = {
    "fwd_5d",
    "fwd_10d",
    "fwd_20d",
    "future_min_10d_close_ret_pct",
    "label_sell_omen_10d_5pct",
    "label_window_end",
}
RISKY_GPT_MARKET_PREFIXES = ("has_",)
ABSOLUTE_LEVEL_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}


@dataclass
class SplitReport:
    name: str
    rows: int
    positives: int
    positive_rate: float
    min_date: str
    max_date: str
    tickers: int


@dataclass
class MetricReport:
    rows: int
    positives: int
    positive_rate: float
    auc: float | None
    average_precision: float | None
    precision_at_050: float | None
    recall_at_050: float | None
    precision_top20pct: float | None
    recall_top20pct: float | None
    score_min: float
    score_mean: float
    score_max: float


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="B-1a sell-omen ML POC trainer")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--drop-threshold-pct", type=float, default=DEFAULT_DROP_THRESHOLD_PCT)
    parser.add_argument("--top-pct", type=float, default=0.20)
    parser.add_argument("--permutation-repeats", type=int, default=5)
    parser.add_argument("--include-gpt-market-events", action="store_true", help="위험군 has_* GPT 시장 이벤트 피처를 포함한다. 기본은 제외.")
    parser.add_argument("--include-absolute-levels", action="store_true", help="절대 OHLCV 레벨을 포함한다. 기본은 제외.")
    parser.add_argument("--include-absolute-close", dest="include_absolute_levels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--write-report", type=Path, default=None)
    return parser.parse_args()


def _load_condition_db(data_dir: Path) -> pd.DataFrame:
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"condition_db csv not found: {data_dir}")
    frames: list[pd.DataFrame] = []
    for path in files:
        df = pd.read_csv(path)
        if "Date" not in df.columns or "Close" not in df.columns:
            raise ValueError(f"required columns missing in {path}")
        df["ticker"] = path.stem
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Close"]).sort_values("Date").reset_index(drop=True)
        frames.append(df)
    return pd.concat(frames, ignore_index=True).sort_values(["ticker", "Date"]).reset_index(drop=True)


def _future_min_close_return_pct(close: pd.Series, horizon: int) -> pd.Series:
    future_closes = pd.concat([close.shift(-i) for i in range(1, horizon + 1)], axis=1)
    return (future_closes.min(axis=1) / close - 1.0) * 100.0


def _add_label(df: pd.DataFrame, horizon: int, threshold_pct: float) -> pd.DataFrame:
    out = df.copy()
    grouped = out.groupby("ticker", group_keys=False)
    out[f"future_min_{horizon}d_close_ret_pct"] = grouped["Close"].apply(lambda s: _future_min_close_return_pct(s, horizon))
    out["label_window_end"] = grouped["Date"].shift(-horizon)
    out["label_sell_omen_10d_5pct"] = (out[f"future_min_{horizon}d_close_ret_pct"] <= float(threshold_pct)).astype("int8")
    return out.dropna(subset=[f"future_min_{horizon}d_close_ret_pct", "label_window_end"]).copy()


def _rolling_prev_mean_by_ticker(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    return df.groupby("ticker", group_keys=False)[col].apply(lambda s: s.shift(1).rolling(window=window, min_periods=1).mean())


def _rolling_std_by_ticker(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    return df.groupby("ticker", group_keys=False)[col].apply(lambda s: s.rolling(window=window, min_periods=2).std())


def _rolling_prev_max_by_ticker(df: pd.DataFrame, col: str, window: int) -> pd.Series:
    return df.groupby("ticker", group_keys=False)[col].apply(lambda s: s.shift(1).rolling(window=window, min_periods=1).max())


def _add_past_only_features(df: pd.DataFrame, include_gpt_market_events: bool) -> pd.DataFrame:
    out = df.copy().sort_values(["ticker", "Date"]).reset_index(drop=True)
    out["ret_1d_pct"] = out.groupby("ticker")["Close"].pct_change(1) * 100.0
    out["ret_5d_pct"] = out.groupby("ticker")["Close"].pct_change(5) * 100.0
    out["ret_10d_pct"] = out.groupby("ticker")["Close"].pct_change(10) * 100.0
    out["volatility_5d"] = _rolling_std_by_ticker(out, "ret_1d_pct", 5)
    out["volatility_10d"] = _rolling_std_by_ticker(out, "ret_1d_pct", 10)

    for col in ("sentiment_avg", "bearish_ratio", "bullish_ratio", "news_count", "vix", "market_score", "Volume_ratio"):
        if col not in out.columns:
            continue
        prev5 = _rolling_prev_mean_by_ticker(out, col, 5)
        out[f"{col}_delta_vs_prev5"] = out[col] - prev5
        if col in {"news_count", "Volume_ratio"}:
            out[f"{col}_ratio_vs_prev5"] = (out[col].fillna(0.0) + 1.0) / (prev5.fillna(0.0) + 1.0)

    if include_gpt_market_events:
        for col in [c for c in out.columns if c.startswith("has_")]:
            prev5_max = _rolling_prev_max_by_ticker(out, col, 5).fillna(0.0)
            out[f"{col}_new_5d"] = ((out[col].fillna(0.0) > 0.0) & (prev5_max <= 0.0)).astype("int8")

    for col in [c for c in out.columns if c.startswith("sent_")]:
        prev5 = _rolling_prev_mean_by_ticker(out, col, 5)
        out[f"{col}_delta_vs_prev5"] = out[col] - prev5
    return out


def _candidate_feature_columns(df: pd.DataFrame, *, include_gpt_market_events: bool, include_absolute_levels: bool) -> list[str]:
    excluded = set(BASE_EXCLUDE_COLUMNS) | IDENTITY_COLUMNS
    if not include_absolute_levels:
        excluded |= ABSOLUTE_LEVEL_COLUMNS
    features: list[str] = []
    for col in df.columns:
        if col in excluded:
            continue
        lower = col.lower()
        if any(keyword in lower for keyword in LEAK_KEYWORDS):
            continue
        if not include_gpt_market_events and any(col.startswith(prefix) for prefix in RISKY_GPT_MARKET_PREFIXES):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            features.append(col)
    return sorted(features)


def _assert_no_leakage(feature_cols: Iterable[str], include_gpt_market_events: bool, include_absolute_levels: bool) -> None:
    leaked = [c for c in feature_cols if any(keyword in c.lower() for keyword in LEAK_KEYWORDS)]
    if leaked:
        raise RuntimeError(f"LEAKAGE_GUARD_FAILED: forbidden future/label columns in features: {leaked}")
    if not include_gpt_market_events:
        risky = [c for c in feature_cols if any(c.startswith(prefix) for prefix in RISKY_GPT_MARKET_PREFIXES)]
        if risky:
            raise RuntimeError(f"GPT_MARKET_GUARD_FAILED: risky GPT market columns in features: {risky}")
    if not include_absolute_levels:
        absolute = [c for c in feature_cols if c in ABSOLUTE_LEVEL_COLUMNS]
        if absolute:
            raise RuntimeError(f"ABSOLUTE_LEVEL_GUARD_FAILED: absolute OHLCV columns in features: {absolute}")


def _split_time(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    train = df[(df["Date"] <= TRAIN_END) & (df["label_window_end"] <= TRAIN_END)].copy()
    valid = df[(df["Date"] >= VALID_START) & (df["Date"] <= VALID_END) & (df["label_window_end"] <= VALID_END)].copy()
    oos = df[df["Date"] >= OOS_START].copy()
    if not train.empty and not valid.empty and not oos.empty:
        if not (train["Date"].max() < valid["Date"].min() < valid["Date"].max() < oos["Date"].min()):
            raise RuntimeError("TIME_SPLIT_GUARD_FAILED: split dates overlap or are not monotonic")
    return train, valid, oos


def _split_report(name: str, df: pd.DataFrame) -> SplitReport:
    if df.empty:
        return SplitReport(name=name, rows=0, positives=0, positive_rate=0.0, min_date="", max_date="", tickers=0)
    y = df["label_sell_omen_10d_5pct"].astype(int)
    return SplitReport(
        name=name,
        rows=int(len(df)),
        positives=int(y.sum()),
        positive_rate=float(y.mean()),
        min_date=str(df["Date"].min().date()),
        max_date=str(df["Date"].max().date()),
        tickers=int(df["ticker"].nunique()),
    )


def _metric_report(y_true: pd.Series, scores: np.ndarray, top_pct: float) -> MetricReport:
    y = y_true.astype(int).to_numpy()
    scores = np.asarray(scores, dtype=float)
    has_both_classes = len(set(y.tolist())) == 2
    auc = float(roc_auc_score(y, scores)) if has_both_classes else None
    ap = float(average_precision_score(y, scores)) if has_both_classes else None
    pred_050 = (scores >= 0.50).astype(int)
    precision_050 = float(precision_score(y, pred_050, zero_division=0)) if has_both_classes else None
    recall_050 = float(recall_score(y, pred_050, zero_division=0)) if has_both_classes else None
    k = max(1, int(round(len(scores) * float(top_pct))))
    pred_top = np.zeros_like(y)
    pred_top[np.argsort(scores)[-k:]] = 1
    precision_top = float(precision_score(y, pred_top, zero_division=0)) if has_both_classes else None
    recall_top = float(recall_score(y, pred_top, zero_division=0)) if has_both_classes else None
    return MetricReport(
        rows=int(len(y)),
        positives=int(y.sum()),
        positive_rate=float(y.mean()) if len(y) else 0.0,
        auc=auc,
        average_precision=ap,
        precision_at_050=precision_050,
        recall_at_050=recall_050,
        precision_top20pct=precision_top,
        recall_top20pct=recall_top,
        score_min=float(scores.min()) if len(scores) else 0.0,
        score_mean=float(scores.mean()) if len(scores) else 0.0,
        score_max=float(scores.max()) if len(scores) else 0.0,
    )


def _print_split_reports(reports: Iterable[SplitReport]) -> None:
    print("=== split report ===")
    for r in reports:
        print(f"{r.name}: rows={r.rows} positives={r.positives} positive_rate={r.positive_rate:.4f} dates={r.min_date}..{r.max_date} tickers={r.tickers}")


def _top_permutation_importance(model: Pipeline, x: pd.DataFrame, y: pd.Series, repeats: int) -> list[dict[str, float | str]]:
    if x.empty or len(set(y.astype(int).tolist())) < 2:
        return []
    result = permutation_importance(
        model,
        x,
        y.astype(int),
        n_repeats=max(1, int(repeats)),
        random_state=42,
        scoring="roc_auc",
    )
    rows = [
        {"feature": str(name), "importance_mean": float(mean_imp), "importance_std": float(std_imp)}
        for name, mean_imp, std_imp in zip(x.columns, result.importances_mean, result.importances_std)
    ]
    rows.sort(key=lambda row: row["importance_mean"], reverse=True)
    return rows[:15]


def main() -> int:
    args = _parse_args()
    df = _load_condition_db(args.data_dir)
    raw_rows = len(df)
    raw_tickers = df["ticker"].nunique()
    include_absolute_levels = bool(args.include_absolute_levels)

    df = _add_label(df, horizon=args.horizon, threshold_pct=args.drop_threshold_pct)
    df = _add_past_only_features(df, include_gpt_market_events=bool(args.include_gpt_market_events))
    feature_cols = _candidate_feature_columns(
        df,
        include_gpt_market_events=bool(args.include_gpt_market_events),
        include_absolute_levels=include_absolute_levels,
    )
    _assert_no_leakage(feature_cols, bool(args.include_gpt_market_events), include_absolute_levels)

    train, valid, oos = _split_time(df)
    reports = [_split_report("train", train), _split_report("valid", valid), _split_report("oos", oos)]
    if train.empty or valid.empty or oos.empty:
        raise RuntimeError("split produced empty train/valid/oos; cannot run POC")

    y_train = train["label_sell_omen_10d_5pct"].astype(int)
    if len(set(y_train.tolist())) < 2:
        raise RuntimeError("train split has only one class; cannot train classifier")

    x_train = train[feature_cols]
    x_valid = valid[feature_cols]
    x_oos = oos[feature_cols]
    y_valid = valid["label_sell_omen_10d_5pct"].astype(int)
    y_oos = oos["label_sell_omen_10d_5pct"].astype(int)

    model = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingClassifier(
                    max_iter=120,
                    learning_rate=0.05,
                    max_leaf_nodes=15,
                    l2_regularization=0.10,
                    min_samples_leaf=25,
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)
    valid_scores = model.predict_proba(x_valid)[:, 1]
    oos_scores = model.predict_proba(x_oos)[:, 1]
    if not (np.all(valid_scores >= 0.0) and np.all(valid_scores <= 1.0) and np.all(oos_scores >= 0.0) and np.all(oos_scores <= 1.0)):
        raise RuntimeError("SCORE_RANGE_GUARD_FAILED: sell_omen_score is outside [0, 1]")

    valid_metrics = _metric_report(y_valid, valid_scores, args.top_pct)
    oos_metrics = _metric_report(y_oos, oos_scores, args.top_pct)
    top_features = _top_permutation_importance(model, x_valid, y_valid, args.permutation_repeats)

    print("=== B-1a sell-omen POC ===")
    print("purpose: structure/leakage validation only; metrics are not production evidence")
    print(f"data_dir: {args.data_dir}")
    print(f"raw_rows={raw_rows} raw_tickers={raw_tickers}")
    print(f"label: future_min_{args.horizon}d_close_ret_pct <= {args.drop_threshold_pct}%")
    print(f"feature_count={len(feature_cols)}")
    print(f"include_gpt_market_events={bool(args.include_gpt_market_events)}")
    print(f"include_absolute_levels={include_absolute_levels}")
    print(f"leakage_guard=PASS forbidden_keywords={LEAK_KEYWORDS}")
    print("gpt_market_guard=PASS" if not args.include_gpt_market_events else "gpt_market_guard=DISABLED_BY_FLAG")
    print("absolute_level_guard=PASS" if not include_absolute_levels else "absolute_level_guard=DISABLED_BY_FLAG")
    print(f"score_range_guard=PASS valid_min={valid_scores.min():.6f} valid_max={valid_scores.max():.6f} oos_min={oos_scores.min():.6f} oos_max={oos_scores.max():.6f}")
    _print_split_reports(reports)
    print("=== metrics: VALID 2024 ===")
    print(json.dumps(asdict(valid_metrics), ensure_ascii=False, sort_keys=True, indent=2))
    print("=== metrics: OOS 2025+ ===")
    print(json.dumps(asdict(oos_metrics), ensure_ascii=False, sort_keys=True, indent=2))
    print("=== top permutation importance on valid ===")
    for idx, row in enumerate(top_features, 1):
        print(f"{idx:02d}. {row['feature']} mean={row['importance_mean']:.6f} std={row['importance_std']:.6f}")

    suspicious = []
    for name, metric in (("valid", valid_metrics), ("oos", oos_metrics)):
        if metric.auc is not None and metric.auc >= 0.95:
            suspicious.append(f"{name}_auc={metric.auc:.4f}")
    if suspicious:
        print("WARNING: unusually high AUC for POC; re-check leakage/overfit:", ", ".join(suspicious), file=sys.stderr)

    report = {
        "purpose": "B-1a sell-omen structure/leakage POC only",
        "raw_rows": raw_rows,
        "raw_tickers": int(raw_tickers),
        "label": {
            "horizon": int(args.horizon),
            "drop_threshold_pct": float(args.drop_threshold_pct),
            "label_column": "label_sell_omen_10d_5pct",
        },
        "policy": {
            "include_gpt_market_events": bool(args.include_gpt_market_events),
            "include_absolute_levels": include_absolute_levels,
            "default_excludes": sorted(list(ABSOLUTE_LEVEL_COLUMNS)) + ["has_*"],
        },
        "feature_count": len(feature_cols),
        "feature_columns": feature_cols,
        "split": [asdict(r) for r in reports],
        "metrics": {"valid": asdict(valid_metrics), "oos": asdict(oos_metrics)},
        "top_permutation_importance_valid": top_features,
    }
    if args.write_report is not None:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        print(f"report_written: {args.write_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
