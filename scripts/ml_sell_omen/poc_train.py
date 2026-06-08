"""B-1 sell-omen ML trainer / scorer.

이 스크립트는 하락 전조 ML 파이프라인을 누출 없이 검증하고,
실전 연결용 모델 번들 및 walk-forward score table을 생성한다.

Label:
    향후 10거래일 내 종가 기준 -5% 이하 하락 여부.

Default split:
    train: 2020-2023, 단 label horizon이 2023-12-31을 넘는 row 제외
    valid: 2024, 단 label horizon이 2024-12-31을 넘는 row 제외
    oos:   2025-2026

Leakage guard:
    - feature 이름에 fwd/future/forward/label/target가 포함되면 즉시 중단한다.
    - GPT 시장 이벤트 위험군인 has_*는 기본 제외한다.
    - 종목별 스케일을 먹는 절대 OHLCV 레벨도 기본 제외한다.

Score table policy:
    --write-score-table 사용 시 기본은 walk-forward다.
    예: 2024 score는 2023-12-31까지 label이 닫힌 데이터로만 학습한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT / "data" / "_system" / "condition_db"
DEFAULT_MODEL_PATH = ROOT / "data" / "_system" / "ml_sell_omen" / "sell_omen_model.joblib"
DEFAULT_FEATURE_PATH = ROOT / "data" / "_system" / "ml_sell_omen" / "sell_omen_features.json"
DEFAULT_SCORE_TABLE_PATH = ROOT / "data" / "_system" / "ml_sell_omen" / "sell_omen_scores.csv"
DEFAULT_HORIZON_DAYS = 10
DEFAULT_DROP_THRESHOLD_PCT = -5.0
TRAIN_END = pd.Timestamp("2023-12-31")
VALID_START = pd.Timestamp("2024-01-01")
VALID_END = pd.Timestamp("2024-12-31")
OOS_START = pd.Timestamp("2025-01-01")

LABEL_COLUMN = "label_sell_omen_10d_5pct"
LEAK_KEYWORDS = ("fwd", "future", "forward", "label", "target")
IDENTITY_COLUMNS = {"Date", "ticker"}
BASE_EXCLUDE_COLUMNS = {
    "fwd_5d",
    "fwd_10d",
    "fwd_20d",
    "future_min_10d_close_ret_pct",
    LABEL_COLUMN,
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
    parser = argparse.ArgumentParser(description="B-1 sell-omen ML trainer / scorer")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON_DAYS)
    parser.add_argument("--drop-threshold-pct", type=float, default=DEFAULT_DROP_THRESHOLD_PCT)
    parser.add_argument("--top-pct", type=float, default=0.20)
    parser.add_argument("--permutation-repeats", type=int, default=5)
    parser.add_argument("--include-gpt-market-events", action="store_true", help="위험군 has_* GPT 시장 이벤트 피처를 포함한다. 기본은 제외.")
    parser.add_argument("--include-absolute-levels", action="store_true", help="절대 OHLCV 레벨을 포함한다. 기본은 제외.")
    parser.add_argument("--include-absolute-close", dest="include_absolute_levels", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--write-report", type=Path, default=None)
    parser.add_argument("--save-model", type=Path, default=None, help="최종 live/future용 모델 bundle 저장 경로")
    parser.add_argument("--write-feature-list", type=Path, default=None, help="feature column JSON 저장 경로")
    parser.add_argument("--write-score-table", type=Path, default=None, help="ticker,date,sell_omen_score CSV 저장 경로")
    parser.add_argument("--score-mode", choices=["walk_forward", "final_model"], default="walk_forward")
    parser.add_argument("--walk-forward-start-year", type=int, default=2024)
    parser.add_argument("--min-train-rows", type=int, default=1000)
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
    out[LABEL_COLUMN] = (out[f"future_min_{horizon}d_close_ret_pct"] <= float(threshold_pct)).astype("int8")
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
    y = df[LABEL_COLUMN].astype(int)
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


def _make_model() -> Pipeline:
    return Pipeline(
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


def _fit_model(train: pd.DataFrame, feature_cols: list[str]) -> Pipeline:
    y = train[LABEL_COLUMN].astype(int)
    if len(set(y.tolist())) < 2:
        raise RuntimeError("train split has only one class; cannot train classifier")
    model = _make_model()
    model.fit(train[feature_cols], y)
    return model


def _print_split_reports(reports: Iterable[SplitReport]) -> None:
    print("=== split report ===")
    for r in reports:
        print(f"{r.name}: rows={r.rows} positives={r.positives} positive_rate={r.positive_rate:.4f} dates={r.min_date}..{r.max_date} tickers={r.tickers}")


def _top_permutation_importance(model: Pipeline, x: pd.DataFrame, y: pd.Series, repeats: int) -> list[dict[str, float | str]]:
    if repeats <= 0 or x.empty or len(set(y.astype(int).tolist())) < 2:
        return []
    result = permutation_importance(
        model,
        x,
        y.astype(int),
        n_repeats=int(repeats),
        random_state=42,
        scoring="roc_auc",
    )
    rows = [
        {"feature": str(name), "importance_mean": float(mean_imp), "importance_std": float(std_imp)}
        for name, mean_imp, std_imp in zip(x.columns, result.importances_mean, result.importances_std)
    ]
    rows.sort(key=lambda row: row["importance_mean"], reverse=True)
    return rows[:15]


def _model_bundle(model: Pipeline, feature_cols: list[str], metadata: dict) -> dict:
    return {
        "model": model,
        "feature_columns": list(feature_cols),
        "metadata": dict(metadata),
    }


def _save_model_bundle(path: Path, model: Pipeline, feature_cols: list[str], metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(_model_bundle(model, feature_cols, metadata), path)
    print(f"model_saved: {path}")


def _write_feature_list(path: Path, feature_cols: list[str], metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"feature_columns": list(feature_cols), "metadata": dict(metadata)}
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(f"feature_list_written: {path}")


def _score_rows(model: Pipeline, df: pd.DataFrame, feature_cols: list[str], model_train_end: pd.Timestamp, score_year: int | str) -> pd.DataFrame:
    scores = model.predict_proba(df[feature_cols])[:, 1]
    scores = np.clip(scores.astype(float), 0.0, 1.0)
    return pd.DataFrame(
        {
            "ticker": df["ticker"].astype(str).to_numpy(),
            "Date": df["Date"].dt.strftime("%Y-%m-%d").to_numpy(),
            "sell_omen_score": scores,
            "model_train_end": str(pd.Timestamp(model_train_end).date()),
            "score_year": score_year,
        }
    )


def _write_walk_forward_scores(
    *,
    df: pd.DataFrame,
    feature_cols: list[str],
    output_path: Path,
    start_year: int,
    min_train_rows: int,
) -> list[dict]:
    max_year = int(df["Date"].dt.year.max())
    score_frames: list[pd.DataFrame] = []
    reports: list[dict] = []
    for year in range(int(start_year), max_year + 1):
        train_end = pd.Timestamp(f"{year - 1}-12-31")
        score_start = pd.Timestamp(f"{year}-01-01")
        score_end = pd.Timestamp(f"{year}-12-31")
        train = df[(df["Date"] <= train_end) & (df["label_window_end"] <= train_end)].copy()
        score = df[(df["Date"] >= score_start) & (df["Date"] <= score_end)].copy()
        report = {
            "score_year": int(year),
            "model_train_end": str(train_end.date()),
            "train_rows": int(len(train)),
            "score_rows": int(len(score)),
            "train_positive_rate": float(train[LABEL_COLUMN].mean()) if len(train) else 0.0,
        }
        if len(train) < int(min_train_rows) or score.empty:
            report["skipped"] = True
            reports.append(report)
            continue
        model = _fit_model(train, feature_cols)
        scored = _score_rows(model, score, feature_cols, train_end, year)
        score_frames.append(scored)
        report["skipped"] = False
        report["score_min"] = float(scored["sell_omen_score"].min())
        report["score_mean"] = float(scored["sell_omen_score"].mean())
        report["score_max"] = float(scored["sell_omen_score"].max())
        reports.append(report)
    if not score_frames:
        raise RuntimeError("walk-forward scoring produced no rows")
    out = pd.concat(score_frames, ignore_index=True).sort_values(["ticker", "Date"]).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    print(f"score_table_written: {output_path} rows={len(out)} mode=walk_forward")
    print("=== walk-forward score report ===")
    print(json.dumps(reports, ensure_ascii=False, sort_keys=True, indent=2))
    return reports


def _write_final_model_scores(df: pd.DataFrame, feature_cols: list[str], output_path: Path, model: Pipeline, train_end: pd.Timestamp) -> list[dict]:
    scored = _score_rows(model, df, feature_cols, train_end, "final_model")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output_path, index=False)
    print(f"score_table_written: {output_path} rows={len(scored)} mode=final_model")
    return [
        {
            "score_year": "final_model",
            "model_train_end": str(train_end.date()),
            "train_rows": int(len(df)),
            "score_rows": int(len(scored)),
            "score_min": float(scored["sell_omen_score"].min()),
            "score_mean": float(scored["sell_omen_score"].mean()),
            "score_max": float(scored["sell_omen_score"].max()),
            "warning": "final_model scores are not leakage-safe for historical backtest",
        }
    ]


def _base_metadata(args: argparse.Namespace, raw_rows: int, raw_tickers: int, feature_cols: list[str]) -> dict:
    return {
        "purpose": "B-1 sell-omen ML",
        "data_dir": str(args.data_dir),
        "raw_rows": int(raw_rows),
        "raw_tickers": int(raw_tickers),
        "label": {
            "horizon": int(args.horizon),
            "drop_threshold_pct": float(args.drop_threshold_pct),
            "label_column": LABEL_COLUMN,
        },
        "policy": {
            "include_gpt_market_events": bool(args.include_gpt_market_events),
            "include_absolute_levels": bool(args.include_absolute_levels),
            "default_excludes": sorted(list(ABSOLUTE_LEVEL_COLUMNS)) + ["has_*"],
            "leak_keywords": list(LEAK_KEYWORDS),
        },
        "feature_count": len(feature_cols),
    }


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

    model = _fit_model(train, feature_cols)
    valid_scores = model.predict_proba(valid[feature_cols])[:, 1]
    oos_scores = model.predict_proba(oos[feature_cols])[:, 1]
    if not (np.all(valid_scores >= 0.0) and np.all(valid_scores <= 1.0) and np.all(oos_scores >= 0.0) and np.all(oos_scores <= 1.0)):
        raise RuntimeError("SCORE_RANGE_GUARD_FAILED: sell_omen_score is outside [0, 1]")

    valid_metrics = _metric_report(valid[LABEL_COLUMN].astype(int), valid_scores, args.top_pct)
    oos_metrics = _metric_report(oos[LABEL_COLUMN].astype(int), oos_scores, args.top_pct)
    top_features = _top_permutation_importance(model, valid[feature_cols], valid[LABEL_COLUMN].astype(int), args.permutation_repeats)
    metadata = _base_metadata(args, raw_rows, raw_tickers, feature_cols)

    print("=== B-1 sell-omen trainer ===")
    print("purpose: structure/leakage validation + optional model/score generation")
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

    score_reports: list[dict] = []
    final_model_train_end = pd.Timestamp(df["label_window_end"].max())
    final_model = _fit_model(df[df["label_window_end"] <= final_model_train_end].copy(), feature_cols)

    if args.save_model is not None:
        save_metadata = dict(metadata)
        save_metadata.update({"model_role": "final_live_future_model", "train_label_window_end_max": str(final_model_train_end.date())})
        _save_model_bundle(args.save_model, final_model, feature_cols, save_metadata)
    if args.write_feature_list is not None:
        _write_feature_list(args.write_feature_list, feature_cols, metadata)
    if args.write_score_table is not None:
        if args.score_mode == "walk_forward":
            score_reports = _write_walk_forward_scores(
                df=df,
                feature_cols=feature_cols,
                output_path=args.write_score_table,
                start_year=args.walk_forward_start_year,
                min_train_rows=args.min_train_rows,
            )
        else:
            score_reports = _write_final_model_scores(df, feature_cols, args.write_score_table, final_model, final_model_train_end)

    report = dict(metadata)
    report.update(
        {
            "feature_columns": feature_cols,
            "split": [asdict(r) for r in reports],
            "metrics": {"valid": asdict(valid_metrics), "oos": asdict(oos_metrics)},
            "top_permutation_importance_valid": top_features,
            "score_generation": {
                "score_mode": args.score_mode,
                "write_score_table": str(args.write_score_table) if args.write_score_table else None,
                "walk_forward_start_year": int(args.walk_forward_start_year),
                "reports": score_reports,
            },
        }
    )
    if args.write_report is not None:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        print(f"report_written: {args.write_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
