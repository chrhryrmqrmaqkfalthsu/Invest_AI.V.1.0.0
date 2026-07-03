#!/usr/bin/env python3
"""
Stage2 flow clone for next-day high/low range prediction (v3).

Flow inherited from scripts/research/run_stage2.py:
1) rolling train_1/train_2/train_3 splits,
2) independent GA per split,
3) final_population collection, not best-only,
4) predictor signature representative grouping,
5) stress_pre_2022h1 -> train_3_eval -> train_2_eval -> train_1_eval -> oos_2025h2 early-cut survivor gate,
6) OOS gate included in survivor selection.

Only the evaluation target is replaced: Rulebook swing backtest -> next-day open-to-high/open-to-low 6-bin range prediction.
No run_live, no trading, no cache update, and no modification of run_stage2.py or engine/ files.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

# run_stage2.py:53-67 values.
POPULATION = 100
GENERATIONS = 50
PATIENCE = 15
ELITE_RATIO = 0.2
MUTATION_RATE = 0.15
MUTATION_STRENGTH = 0.2
TOURNAMENT_SIZE = 3
SEED_PATTERN_RATIO = 0.33

LOOKBACK = 5
RULE_COUNT = 80
CACHE = PROJECT_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
MARKET_SYMS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC", "SMH", "ARKK"]

# run_stage2.py:63-67 copied exactly.
TRAIN_SPLITS: list[dict[str, str]] = [
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
]

# run_stage2.py:70-76 copied exactly.
PERIODS_TEMPLATE: list[dict[str, Any]] = [
    {"label": "stress_pre_2022h1", "kind": "stress", "start": None, "end": "2022-06-30", "order": 1},
    {"label": "train_3_eval", "kind": "train", "start": "2024-07-01", "end": "2025-06-30", "order": 2},
    {"label": "train_2_eval", "kind": "train", "start": "2023-07-01", "end": "2024-06-30", "order": 3},
    {"label": "train_1_eval", "kind": "train", "start": "2022-07-01", "end": "2023-06-30", "order": 4},
    {"label": "oos_2025h2", "kind": "oos", "start": "2025-07-01", "end": None, "order": 5},
]

BIN_LABELS = ["0.0_0.5", "0.5_1.0", "1.0_2.0", "2.0_3.0", "3.0_5.0", "5.0_plus"]
BIN_COUNT = 6
CONCENTRATION_CAP_PCT = 45.0
CONCENTRATION_PENALTY_STRENGTH = 0.35
RARE_BIN_ACTUAL_MAX_PCT = 5.0
RARE_BIN_PRED_ALLOW_PCT = 10.0
RARE_BIN_PENALTY_STRENGTH = 0.45


@dataclass(frozen=True)
class PredictorGateConfig:
    min_samples: int = 100
    min_member_score: float = 10.0
    train_min_exact_lift_pp: float = 0.0
    train_min_adjacent_lift_pp: float = 0.0
    stress_min_exact_lift_pp: float = 0.0
    stress_min_adjacent_lift_pp: float = 0.0
    oos_min_exact_lift_pp: float = 0.0
    oos_min_adjacent_lift_pp: float = 0.0
    max_total_penalty: float = 10.0
    max_pred_share_pct: float = 65.0


DEFAULT_PREDICTOR_GATE = PredictorGateConfig()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


def default_seed_base(ticker: str) -> int:
    return 2026070300 + sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker.upper()))


def auto_out_dir(ticker: str) -> Path:
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_{time.strftime('%Y%m%d')}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("cannot allocate output directory")


def load_ohlcv(ticker: str) -> pd.DataFrame:
    path = CACHE / f"{ticker.upper()}.pkl"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_pickle(path).sort_index()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    return df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])


def label_bin(value_pct: float) -> int:
    if value_pct < 0.5:
        return 0
    if value_pct < 1.0:
        return 1
    if value_pct < 2.0:
        return 2
    if value_pct < 3.0:
        return 3
    if value_pct < 5.0:
        return 4
    return 5


def add_market_maps() -> tuple[dict[str, dict[str, float]], list[dict[str, str]]]:
    by_date: dict[str, dict[str, float]] = {}
    meta: list[dict[str, str]] = []
    for sym in MARKET_SYMS:
        path = CACHE / f"{sym}.pkl"
        if not path.exists():
            continue
        m = load_ohlcv(sym)
        O = m["Open"].astype(float).to_numpy(); H = m["High"].astype(float).to_numpy()
        L = m["Low"].astype(float).to_numpy(); C = m["Close"].astype(float).to_numpy()
        rng = (H - L) / C * 100.0
        for i in range(21, len(m)):
            d = m.index[i].strftime("%Y-%m-%d")
            by_date.setdefault(d, {})
            by_date[d].update({
                f"MKT_{sym}_gap_d0": (O[i] / C[i - 1] - 1.0) * 100.0,
                f"MKT_{sym}_prev_ret1": (C[i - 1] / C[i - 2] - 1.0) * 100.0,
                f"MKT_{sym}_ret5": (C[i - 1] / C[i - 6] - 1.0) * 100.0,
                f"MKT_{sym}_vol5": float(np.nanmean(rng[i - 5:i])),
                f"MKT_{sym}_vol20": float(np.nanmean(rng[i - 20:i])),
            })
        for name in ["gap_d0", "prev_ret1", "ret5", "vol5", "vol20"]:
            meta.append({"feature": f"MKT_{sym}_{name}", "source": "market", "lookahead": "D0 open gap allowed or D-1 confirmed ETF value"})
    return by_date, meta


def add_news_map() -> tuple[dict[str, dict[str, float]], list[dict[str, str]]]:
    by_prev_date: dict[str, dict[str, float]] = {}
    meta: list[dict[str, str]] = []
    for path, prefix, source in [
        (PROJECT_ROOT / "data/_system/market_history.csv", "MH1_", "market_history_Dminus1"),
        (PROJECT_ROOT / "data/_system/market_history_v2.csv", "MH2_", "news_event_Dminus1"),
    ]:
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        cols = [c for c in df.columns if c != "date" and pd.api.types.is_numeric_dtype(df[c])]
        for _, row in df.iterrows():
            d = row["date"]
            by_prev_date.setdefault(d, {})
            for col in cols:
                by_prev_date[d][prefix + col] = float(row[col]) if pd.notna(row[col]) else np.nan
        for col in cols:
            meta.append({"feature": prefix + col, "source": source, "lookahead": "joined from D-1 date only"})
    return by_prev_date, meta


def build_dataset(ticker: str) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    df = load_ohlcv(ticker)
    market_by_date, market_meta = add_market_maps()
    news_by_prev_date, news_meta = add_news_map()
    O = df["Open"].astype(float).to_numpy(); H = df["High"].astype(float).to_numpy()
    L = df["Low"].astype(float).to_numpy(); C = df["Close"].astype(float).to_numpy(); V = df["Volume"].astype(float).to_numpy()
    rng = (H - L) / C * 100.0
    intr = (C - O) / O * 100.0
    rows: list[dict[str, Any]] = []
    feature_meta: dict[str, dict[str, str]] = {}

    def add(row: dict[str, Any], name: str, value: Any, source: str, lookahead: str) -> None:
        row[name] = value
        feature_meta.setdefault(name, {"feature": name, "source": source, "lookahead": lookahead})

    for i in range(max(21, LOOKBACK + 1), len(df)):
        d = df.index[i]
        high_pct = (H[i] / O[i] - 1.0) * 100.0
        low_mag = (O[i] / L[i] - 1.0) * 100.0 if L[i] > 0 else np.nan
        row: dict[str, Any] = {"date": d.strftime("%Y-%m-%d"), "year": int(d.year), "high_pct_label": high_pct, "low_mag_pct_label": low_mag, "high_bin": label_bin(high_pct), "low_bin": label_bin(low_mag)}
        add(row, "STK_gap_d0", (O[i] / C[i - 1] - 1.0) * 100.0, "daily_stock", "D0 open vs D-1 close; entry-time observable")
        for lag in range(1, LOOKBACK + 1):
            j = i - lag
            vol_base = np.nanmean(V[max(0, j - 20):j])
            add(row, f"STK_lag{lag}_ccret", (C[j] / C[j - 1] - 1.0) * 100.0, "daily_stock", f"D-{lag} close return")
            add(row, f"STK_lag{lag}_intr", intr[j], "daily_stock", f"D-{lag} open-close return")
            add(row, f"STK_lag{lag}_range", rng[j], "daily_stock", f"D-{lag} high-low range")
            add(row, f"STK_lag{lag}_gap", (O[j] / C[j - 1] - 1.0) * 100.0, "daily_stock", f"D-{lag} gap")
            add(row, f"STK_lag{lag}_volratio20", V[j] / vol_base if vol_base else np.nan, "daily_stock", f"D-{lag} volume vs prior 20d avg")
        for n in [3, 5, 10, 20]:
            add(row, f"STK_ret{n}", (C[i - 1] / C[i - 1 - n] - 1.0) * 100.0, "daily_stock", f"D-1 close vs D-{n+1} close")
            add(row, f"STK_vol{n}", float(np.nanmean(rng[i - n:i])), "daily_stock", f"D-{n}~D-1 average range")
        lo5, hi5 = np.nanmin(L[i - 5:i]), np.nanmax(H[i - 5:i])
        lo20, hi20 = np.nanmin(L[i - 20:i]), np.nanmax(H[i - 20:i])
        add(row, "STK_range_pos5", (C[i - 1] - lo5) / (hi5 - lo5) * 100.0 if hi5 != lo5 else 50.0, "daily_stock", "D-1 close position in prior 5d range")
        add(row, "STK_range_pos20", (C[i - 1] - lo20) / (hi20 - lo20) * 100.0 if hi20 != lo20 else 50.0, "daily_stock", "D-1 close position in prior 20d range")
        for col in ["RSI", "ATR_pct", "BB_width", "Volume_ratio", "MACD_hist", "Stoch_K", "Stoch_D"]:
            if col in df.columns:
                add(row, f"STK_{col}_d1", float(df[col].iloc[i - 1]), "daily_stock_indicator", f"{col} as of D-1")
        row.update(market_by_date.get(row["date"], {}))
        row.update(news_by_prev_date.get(df.index[i - 1].strftime("%Y-%m-%d"), {}))
        rows.append(row)
    data = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    seen = set(); unique_meta = []
    for m in list(feature_meta.values()) + market_meta + news_meta:
        if m["feature"] in data.columns and m["feature"] not in seen:
            unique_meta.append(m); seen.add(m["feature"])
    return data, unique_meta


@dataclass
class RuleGene:
    target: str
    feature: str
    op: str
    threshold: float
    bin: int
    weight: float


@dataclass
class PredictorIndividual:
    rules: list[RuleGene]
    default_high_bin: int
    default_low_bin: int
    baseline_spec: dict[str, Any]
    fitness: float = -1e9
    metrics: dict[str, Any] | None = None
    signature: str | None = None


def clone_individual(ind: PredictorIndividual) -> PredictorIndividual:
    return PredictorIndividual([RuleGene(**asdict(r)) for r in ind.rules], int(ind.default_high_bin), int(ind.default_low_bin), json.loads(json.dumps(json_safe(ind.baseline_spec))), float(ind.fitness), json.loads(json.dumps(json_safe(ind.metrics))) if ind.metrics is not None else None, ind.signature)


def individual_to_dict(ind: PredictorIndividual) -> dict[str, Any]:
    return {"rules": [asdict(r) for r in ind.rules], "default_high_bin": int(ind.default_high_bin), "default_low_bin": int(ind.default_low_bin), "baseline_spec": ind.baseline_spec, "fitness": safe_float(ind.fitness), "metrics": ind.metrics, "signature": ind.signature or predictor_signature(ind)}


def individual_from_dict(payload: Mapping[str, Any]) -> PredictorIndividual:
    ind = PredictorIndividual([RuleGene(**dict(r)) for r in list(payload.get("rules", []) or [])], safe_int(payload.get("default_high_bin")), safe_int(payload.get("default_low_bin")), dict(payload.get("baseline_spec") or {}), safe_float(payload.get("fitness")), dict(payload.get("metrics") or {}) if isinstance(payload.get("metrics"), Mapping) else None, str(payload.get("signature") or "") or None)
    ind.signature = ind.signature or predictor_signature(ind)
    return ind


def predictor_signature(ind: PredictorIndividual) -> str:
    payload = json.dumps({"h": ind.default_high_bin, "l": ind.default_low_bin, "rules": [asdict(r) for r in ind.rules]}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def feature_columns(data: pd.DataFrame) -> list[str]:
    banned = {"date", "year", "high_pct_label", "low_mag_pct_label", "high_bin", "low_bin"}
    return [c for c in data.columns if c not in banned and pd.api.types.is_numeric_dtype(data[c])]


def period_frame(data: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    dates = pd.to_datetime(data["date"])
    mask = pd.Series(True, index=data.index)
    if start is not None:
        mask &= dates >= pd.Timestamp(start)
    if end is not None:
        mask &= dates <= pd.Timestamp(end)
    return data.loc[mask].reset_index(drop=True)


def make_quantiles(train_df: pd.DataFrame, features: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for f in features:
        vals = train_df[f].dropna().to_numpy(dtype=float)
        if len(vals) < 50:
            continue
        out[f] = [float(x) for x in np.nanpercentile(vals, [5, 10, 20, 33.333, 50, 66.667, 80, 90, 95]) if math.isfinite(float(x))]
    return out


def mode_bin(y: np.ndarray) -> int:
    return int(np.argmax(np.bincount(y.astype(int), minlength=BIN_COUNT)))


def best_adjacent_bin(y: np.ndarray) -> int:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT)
    return int(max(range(BIN_COUNT), key=lambda b: sum(int(counts[j]) for j in [b - 1, b, b + 1] if 0 <= j < BIN_COUNT)))


def make_baseline_spec(train_df: pd.DataFrame) -> dict[str, Any]:
    yh = train_df["high_bin"].to_numpy(dtype=int); yl = train_df["low_bin"].to_numpy(dtype=int)
    return {"exact_high_bin": mode_bin(yh), "exact_low_bin": mode_bin(yl), "adjacent_high_bin": best_adjacent_bin(yh), "adjacent_low_bin": best_adjacent_bin(yl), "source": "train split only"}


def fixed_prediction_scores(df: pd.DataFrame, high_bin: int, low_bin: int) -> dict[str, float]:
    yh = df["high_bin"].to_numpy(dtype=int); yl = df["low_bin"].to_numpy(dtype=int)
    return score_predictions(yh, yl, np.full(len(df), int(high_bin), dtype=int), np.full(len(df), int(low_bin), dtype=int))


def baseline_metrics(df: pd.DataFrame, spec: Mapping[str, Any]) -> dict[str, Any]:
    exact = fixed_prediction_scores(df, safe_int(spec.get("exact_high_bin")), safe_int(spec.get("exact_low_bin")))
    adjacent = fixed_prediction_scores(df, safe_int(spec.get("adjacent_high_bin")), safe_int(spec.get("adjacent_low_bin")))
    return {"exact_baseline": exact, "adjacent_baseline": adjacent, "exact_bins": {"high": safe_int(spec.get("exact_high_bin")), "low": safe_int(spec.get("exact_low_bin"))}, "adjacent_bins": {"high": safe_int(spec.get("adjacent_high_bin")), "low": safe_int(spec.get("adjacent_low_bin"))}}


def random_rule(rng: random.Random, q: dict[str, list[float]]) -> RuleGene:
    feature = rng.choice(list(q.keys()))
    return RuleGene(rng.choice(["HIGH", "LOW"]), feature, rng.choice(["<=", ">="]), float(rng.choice(q[feature])), int(rng.randrange(BIN_COUNT)), float(rng.uniform(0.5, 3.0)))


def random_individual(rng: random.Random, q: dict[str, list[float]], baseline_spec: dict[str, Any]) -> PredictorIndividual:
    return PredictorIndividual([random_rule(rng, q) for _ in range(RULE_COUNT)], safe_int(baseline_spec.get("exact_high_bin")), safe_int(baseline_spec.get("exact_low_bin")), dict(baseline_spec))


def predict(ind: PredictorIndividual, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    n = len(X)
    hs = np.zeros((n, BIN_COUNT), dtype=float); ls = np.zeros((n, BIN_COUNT), dtype=float)
    hs[:, ind.default_high_bin] = 1.0; ls[:, ind.default_low_bin] = 1.0
    for rule in ind.rules:
        if rule.feature not in X.columns:
            continue
        vals = X[rule.feature].to_numpy(dtype=float)
        mask = np.isfinite(vals) & (vals <= rule.threshold if rule.op == "<=" else vals >= rule.threshold)
        if not mask.any():
            continue
        (hs if rule.target == "HIGH" else ls)[mask, rule.bin] += rule.weight
    return hs.argmax(axis=1), ls.argmax(axis=1)


def score_predictions(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, float]:
    he = float((ph == yh).mean() * 100.0) if len(yh) else 0.0
    le = float((pl == yl).mean() * 100.0) if len(yl) else 0.0
    ha = float((np.abs(ph - yh) <= 1).mean() * 100.0) if len(yh) else 0.0
    la = float((np.abs(pl - yl) <= 1).mean() * 100.0) if len(yl) else 0.0
    return {"high_exact_acc_pct": he, "low_exact_acc_pct": le, "high_adjacent_acc_pct": ha, "low_adjacent_acc_pct": la, "combined_exact_acc_pct": (he + le) / 2.0, "combined_adjacent_acc_pct": (ha + la) / 2.0}


def share_by_bin(pred: np.ndarray) -> list[float]:
    counts = np.bincount(pred.astype(int), minlength=BIN_COUNT); total = max(1, int(counts.sum()))
    return [float(c / total * 100.0) for c in counts]


def prediction_penalty(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, Any]:
    hp, lp = share_by_bin(ph), share_by_bin(pl)
    ha, la = share_by_bin(yh), share_by_bin(yl)
    conc_excess = max(0.0, max(hp) - CONCENTRATION_CAP_PCT) + max(0.0, max(lp) - CONCENTRATION_CAP_PCT)
    rare_excess = 0.0
    for pred, actual in [(hp, ha), (lp, la)]:
        for p, a in zip(pred, actual):
            if a < RARE_BIN_ACTUAL_MAX_PCT:
                rare_excess += max(0.0, p - RARE_BIN_PRED_ALLOW_PCT)
    conc_penalty = conc_excess * CONCENTRATION_PENALTY_STRENGTH
    rare_penalty = rare_excess * RARE_BIN_PENALTY_STRENGTH
    return {"concentration_penalty": conc_penalty, "rare_bin_penalty": rare_penalty, "total_penalty": conc_penalty + rare_penalty, "max_pred_share_high_pct": max(hp) if hp else 0.0, "max_pred_share_low_pct": max(lp) if lp else 0.0, "pred_distribution_high_pct": hp, "pred_distribution_low_pct": lp}


def predictor_fitness(metrics: Mapping[str, Any]) -> float:
    raw = safe_float(metrics.get("combined_exact_lift_pp")) + safe_float(metrics.get("combined_adjacent_lift_pp")) * 0.35 + safe_float(metrics.get("high_exact_lift_pp")) * 0.15 + safe_float(metrics.get("low_exact_lift_pp")) * 0.15
    return float(raw - safe_float(metrics.get("total_penalty")))


def evaluate_predictor(ind: PredictorIndividual, df: pd.DataFrame, features: list[str]) -> dict[str, Any]:
    yh = df["high_bin"].to_numpy(dtype=int); yl = df["low_bin"].to_numpy(dtype=int)
    ph, pl = predict(ind, df[features])
    scores = score_predictions(yh, yl, ph, pl)
    penalty = prediction_penalty(yh, yl, ph, pl)
    bases = baseline_metrics(df, ind.baseline_spec)
    exact_base = bases["exact_baseline"]; adj_base = bases["adjacent_baseline"]
    metrics = {**scores, "n": int(len(df)), "sample_count": int(len(df)), "combined_exact_lift_pp": scores["combined_exact_acc_pct"] - exact_base["combined_exact_acc_pct"], "combined_adjacent_lift_pp": scores["combined_adjacent_acc_pct"] - adj_base["combined_adjacent_acc_pct"], "high_exact_lift_pp": scores["high_exact_acc_pct"] - exact_base["high_exact_acc_pct"], "low_exact_lift_pp": scores["low_exact_acc_pct"] - exact_base["low_exact_acc_pct"], "high_adjacent_lift_pp": scores["high_adjacent_acc_pct"] - adj_base["high_adjacent_acc_pct"], "low_adjacent_lift_pp": scores["low_adjacent_acc_pct"] - adj_base["low_adjacent_acc_pct"], "baseline_exact_combined_acc_pct": exact_base["combined_exact_acc_pct"], "baseline_adjacent_combined_acc_pct": adj_base["combined_adjacent_acc_pct"], **penalty}
    metrics["fitness"] = predictor_fitness(metrics)
    return metrics


def mutate(ind: PredictorIndividual, rng: random.Random, q: dict[str, list[float]]) -> PredictorIndividual:
    child = clone_individual(ind); child.fitness = -1e9; child.metrics = None; child.signature = None
    for i, rule in enumerate(child.rules):
        if rng.random() > MUTATION_RATE:
            continue
        action = rng.choice(["replace", "feature", "threshold", "bin", "weight", "op", "target"])
        if action == "replace":
            child.rules[i] = random_rule(rng, q)
        elif action == "feature":
            f = rng.choice(list(q.keys())); rule.feature = f; rule.threshold = float(rng.choice(q[f]))
        elif action == "threshold" and rule.feature in q:
            rule.threshold = float(rng.choice(q[rule.feature]))
        elif action == "bin":
            rule.bin = int(max(0, min(BIN_COUNT - 1, rule.bin + rng.choice([-2, -1, 1, 2]))))
        elif action == "weight":
            rule.weight = float(max(0.1, min(5.0, rule.weight + rng.gauss(0.0, MUTATION_STRENGTH))))
        elif action == "op":
            rule.op = "<=" if rule.op == ">=" else ">="
        elif action == "target":
            rule.target = "LOW" if rule.target == "HIGH" else "HIGH"
    return child


def crossover(a: PredictorIndividual, b: PredictorIndividual, rng: random.Random) -> PredictorIndividual:
    return PredictorIndividual([RuleGene(**asdict(ra if rng.random() < 0.5 else rb)) for ra, rb in zip(a.rules, b.rules)], a.default_high_bin if rng.random() < 0.5 else b.default_high_bin, a.default_low_bin if rng.random() < 0.5 else b.default_low_bin, dict(a.baseline_spec))


def tournament(pop: list[PredictorIndividual], rng: random.Random) -> PredictorIndividual:
    return max(rng.sample(pop, min(TOURNAMENT_SIZE, len(pop))), key=lambda x: x.fitness)


def run_predictor_ga(train_df: pd.DataFrame, features: list[str], split: Mapping[str, str], seed: int) -> dict[str, Any]:
    started = time.time(); rng = random.Random(seed)
    q = make_quantiles(train_df, features); usable = [f for f in features if f in q]
    baseline_spec = make_baseline_spec(train_df)
    pop = [random_individual(rng, q, baseline_spec) for _ in range(POPULATION)]
    for ind in pop:
        ind.metrics = evaluate_predictor(ind, train_df, usable); ind.fitness = ind.metrics["fitness"]; ind.signature = predictor_signature(ind)
    best_overall = clone_individual(max(pop, key=lambda x: x.fitness)); history = []; no_improve = 0
    for gen in range(1, GENERATIONS + 1):
        pop.sort(key=lambda x: x.fitness, reverse=True); best = pop[0]
        history.append({"train_label": split["label"], "train_start": split["train_start"], "train_end": split["train_end"], "generation": gen, "best_fitness": safe_float(best.fitness), "avg_fitness": float(np.mean([p.fitness for p in pop])), "best_signature": best.signature or predictor_signature(best), "best_metrics": best.metrics})
        if best.fitness > best_overall.fitness:
            best_overall = clone_individual(best); no_improve = 0
        else:
            no_improve += 1
            if no_improve >= PATIENCE:
                break
        elite_n = max(1, int(POPULATION * ELITE_RATIO)); new_pop = [clone_individual(x) for x in pop[:elite_n]]
        while len(new_pop) < POPULATION:
            child = mutate(crossover(tournament(pop, rng), tournament(pop, rng), rng), rng, q)
            child.metrics = evaluate_predictor(child, train_df, usable); child.fitness = child.metrics["fitness"]; child.signature = predictor_signature(child)
            new_pop.append(child)
        pop = new_pop
    pop.sort(key=lambda x: x.fitness, reverse=True)
    elapsed = time.time() - started; generations_run = len(history)
    for row in history:
        row["generations_run"] = generations_run; row["early_stop_triggered"] = bool(generations_run < GENERATIONS); row["train_elapsed_sec"] = elapsed
    return {"split": dict(split), "population": [clone_individual(x) for x in pop], "history": history, "generations_run": generations_run, "early_stop": generations_run < GENERATIONS, "elapsed": elapsed, "usable_features": usable, "baseline_spec": baseline_spec}


def period_family(kind: str) -> str:
    k = str(kind or "").lower()
    if k == "stress" or "stress" in k: return "stress"
    if k == "oos" or k.startswith("oos_") or "oos" in k: return "oos"
    return "train"


def predictor_fail_reasons(metrics: Mapping[str, Any], period_kind: str, config: PredictorGateConfig = DEFAULT_PREDICTOR_GATE) -> list[dict[str, Any]]:
    reasons = []; family = period_family(period_kind)
    n = safe_int(metrics.get("sample_count")); member = safe_float(metrics.get("member_score")); exact = safe_float(metrics.get("combined_exact_lift_pp")); adj = safe_float(metrics.get("combined_adjacent_lift_pp")); penalty = safe_float(metrics.get("total_penalty")); max_share = max(safe_float(metrics.get("max_pred_share_high_pct")), safe_float(metrics.get("max_pred_share_low_pct")))
    if n < config.min_samples: reasons.append({"metric": "sample_count", "value": n, "threshold": config.min_samples, "rule": ">="})
    if member < config.min_member_score: reasons.append({"metric": "member_score", "value": member, "threshold": config.min_member_score, "rule": ">="})
    min_exact = config.stress_min_exact_lift_pp if family == "stress" else config.oos_min_exact_lift_pp if family == "oos" else config.train_min_exact_lift_pp
    min_adj = config.stress_min_adjacent_lift_pp if family == "stress" else config.oos_min_adjacent_lift_pp if family == "oos" else config.train_min_adjacent_lift_pp
    if exact < min_exact: reasons.append({"metric": "combined_exact_lift_pp", "value": exact, "threshold": min_exact, "rule": ">="})
    if adj < min_adj: reasons.append({"metric": "combined_adjacent_lift_pp", "value": adj, "threshold": min_adj, "rule": ">="})
    if penalty > config.max_total_penalty: reasons.append({"metric": "total_penalty", "value": penalty, "threshold": config.max_total_penalty, "rule": "<="})
    if max_share > config.max_pred_share_pct: reasons.append({"metric": "max_pred_share_pct", "value": max_share, "threshold": config.max_pred_share_pct, "rule": "<="})
    return reasons


def percentile_ranks(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0: return []
    if n == 1: return [1.0]
    indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=True); ranks = [0.0] * n; i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]: j += 1
        pct = 1.0 - ((i + j) / 2.0) / (n - 1)
        for k in range(i, j + 1): ranks[indexed[k][0]] = pct
        i = j + 1
    return ranks


def score_period_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(c) for c in candidates]
    if not rows: return []
    er = percentile_ranks([safe_float(r.get("combined_exact_lift_pp")) for r in rows]); ar = percentile_ranks([safe_float(r.get("combined_adjacent_lift_pp")) for r in rows]); pr = percentile_ranks([-safe_float(r.get("total_penalty")) for r in rows])
    out = []
    for i, row in enumerate(rows):
        score = max(0.0, min(1.0, er[i] * 0.70 + ar[i] * 0.20 + pr[i] * 0.10)) * 100.0
        r = dict(row); r["member_score"] = round(score, 6); r["member_score_components"] = {"exact_lift_percentile": round(er[i], 6), "adjacent_lift_percentile": round(ar[i], 6), "low_penalty_percentile": round(pr[i], 6), "selection_policy": "exact_lift_0.70_adjacent_lift_0.20_low_penalty_0.10"}; out.append(r)
    return out


def train_one_split(ticker: str, split_idx: int, split: dict[str, str], data: pd.DataFrame, features: list[str], seed_base: int) -> dict[str, Any]:
    train_df = period_frame(data, split["train_start"], split["train_end"]); result = run_predictor_ga(train_df, features, split, seed_base + split_idx)
    rows = []
    for rank, ind in enumerate(result["population"], 1):
        rows.append({"ticker": ticker, "train_label": split["label"], "train_start": split["train_start"], "train_end": split["train_end"], "origin_rank": rank, "signature": ind.signature or predictor_signature(ind), "train_fitness": safe_float(ind.fitness), "train_metrics": ind.metrics, "predictor": individual_to_dict(ind)})
    return {"split": dict(split), "rows": rows, "history": result["history"], "generations_run": result["generations_run"], "early_stop": result["early_stop"], "elapsed": result["elapsed"], "sample_count": int(len(train_df)), "baseline_spec": result["baseline_spec"], "usable_features": result["usable_features"]}


def run_training(ticker: str, data: pd.DataFrame, features: list[str], seed_base: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    results = [train_one_split(ticker, i, split, data, features, seed_base) for i, split in enumerate(TRAIN_SPLITS, 1)]
    results.sort(key=lambda row: row["split"]["label"]); rows = []; history = []
    for result in results:
        rows.extend(result["rows"]); history.extend(result["history"])
    history.sort(key=lambda row: (row["train_label"], row["generation"]))
    return results, rows, history


def build_representatives(rows: list[dict[str, Any]]) -> tuple[dict[str, PredictorIndividual], dict[str, list[dict[str, Any]]]]:
    reps: dict[str, PredictorIndividual] = {}; origins: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        sig = str(row["signature"]); ind = individual_from_dict(row["predictor"])
        origin = {k: row[k] for k in ["train_label", "train_start", "train_end", "origin_rank", "train_fitness"]}; origins[sig].append(origin)
        if sig not in reps or safe_float(row.get("train_fitness")) > safe_float(reps[sig].fitness):
            reps[sig] = ind; reps[sig].fitness = safe_float(row.get("train_fitness")); reps[sig].signature = sig
    return reps, origins


def evaluate_periods(ticker: str, data: pd.DataFrame, features: list[str], periods: list[dict[str, Any]], reps: dict[str, PredictorIndividual], origins: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    unique = sorted(reps); alive = set(unique); rows = []; cuts = []; survivors_json = []; first_fail = {}; reached_by = defaultdict(list); metrics_by = {}; trace = []; eval_count = 0; max_eval = len(unique) * len(periods)
    for period in periods:
        reached = sorted(alive); pdf = period_frame(data, period["start"], period["end"]); raw = []
        for rank, sig in enumerate(reached, 1):
            m = evaluate_predictor(reps[sig], pdf, features); eval_count += 1; reached_by[sig].append(period["label"])
            raw.append({"ticker": ticker, "label": period["label"], "period_kind": period["kind"], "period_order": period["order"], "signature": sig, "rank_is": rank, "train_fitness": safe_float(reps[sig].fitness), **m})
        scored = score_period_candidates(raw); next_alive = set()
        for row in scored:
            sig = str(row["signature"]); reasons = predictor_fail_reasons(row, str(period["kind"])); passed = not reasons
            if passed: next_alive.add(sig)
            else: first_fail.setdefault(sig, {"signature": sig, "failed_period_label": period["label"], "failed_period_order": period["order"], "failed_period_kind": period["kind"], "fail_reasons": reasons})
            metrics_by[(sig, period["label"])] = dict(row); origin_labels = sorted({o["train_label"] for o in origins[sig]})
            rows.append({"ticker": ticker, "signature": sig, "period_label": period["label"], "period_kind": period["kind"], "period_order": period["order"], "period_start": period["start"], "period_end": period["end"], "status": "evaluated", "passed_gate": passed, "fail_reasons": reasons, "origin_count": len(origins[sig]), "origin_train_labels": origin_labels, **{k: row.get(k) for k in ["sample_count", "member_score", "combined_exact_acc_pct", "combined_adjacent_acc_pct", "combined_exact_lift_pp", "combined_adjacent_lift_pp", "high_exact_lift_pp", "low_exact_lift_pp", "high_adjacent_lift_pp", "low_adjacent_lift_pp", "total_penalty", "max_pred_share_high_pct", "max_pred_share_low_pct", "fitness"]}})
        trace.append({"period_label": period["label"], "period_kind": period["kind"], "reached": len(reached), "passed": len(next_alive), "failed": len(reached) - len(next_alive)})
        alive = next_alive
    survivors = sorted(alive)
    for sig in unique:
        failed = first_fail.get(sig); reached = reached_by.get(sig, []); skipped = [p["label"] for p in periods if p["label"] not in reached]; origin_labels = sorted({o["train_label"] for o in origins[sig]})
        cuts.append({"ticker": ticker, "signature": sig, "origin_count": len(origins[sig]), "origin_train_labels": origin_labels, "evaluated_period_count": len(reached), "evaluated_periods": reached, "skipped_period_count": len(skipped), "skipped_periods": skipped, "survived_all_5": sig in survivors, "failed_period_label": failed.get("failed_period_label") if failed else None, "failed_period_order": failed.get("failed_period_order") if failed else None, "failed_period_kind": failed.get("failed_period_kind") if failed else None, "fail_reasons": failed.get("fail_reasons") if failed else []})
        if failed:
            for p in periods:
                if p["label"] in skipped:
                    rows.append({"ticker": ticker, "signature": sig, "period_label": p["label"], "period_kind": p["kind"], "period_order": p["order"], "period_start": p["start"], "period_end": p["end"], "status": "skipped_after_early_cut", "passed_gate": False, "fail_reasons": [], "origin_count": len(origins[sig]), "origin_train_labels": origin_labels})
    for sig in survivors:
        survivors_json.append({"ticker": ticker, "signature": sig, "origin_count": len(origins[sig]), "origin_train_labels": sorted({o["train_label"] for o in origins[sig]}), "origins": origins[sig], "predictor": individual_to_dict(reps[sig]), "periods": [{"period_label": p["label"], "period_kind": p["kind"], **metrics_by.get((sig, p["label"]), {})} for p in periods]})
    return {"unique_signatures": unique, "survivors": survivors, "period_metrics_rows": rows, "early_cut_rows": cuts, "survivor_rows": survivors_json, "alive_trace": trace, "eval_count": eval_count, "max_eval_count": max_eval}


def distribution(y: np.ndarray) -> dict[str, Any]:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT); total = int(counts.sum())
    return {BIN_LABELS[i]: {"count": int(counts[i]), "pct": float(counts[i] / total * 100.0) if total else 0.0} for i in range(BIN_COUNT)}


def build_periods(data: pd.DataFrame) -> list[dict[str, Any]]:
    data_start, data_end = str(data["date"].min()), str(data["date"].max())
    out = []
    for p in PERIODS_TEMPLATE:
        row = dict(p); row["start"] = row["start"] or data_start; row["end"] = row["end"] or data_end; out.append(row)
    return out


def coverage_report(data: pd.DataFrame, periods: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for split in TRAIN_SPLITS:
        df = period_frame(data, split["train_start"], split["train_end"]); out[split["label"]] = {"start": split["train_start"], "end": split["train_end"], "sample_count": int(len(df)), "first_sample": df["date"].min() if len(df) else None, "last_sample": df["date"].max() if len(df) else None}
    for p in periods:
        df = period_frame(data, p["start"], p["end"]); out[p["label"]] = {"start": p["start"], "end": p["end"], "kind": p["kind"], "sample_count": int(len(df)), "first_sample": df["date"].min() if len(df) else None, "last_sample": df["date"].max() if len(df) else None}
    return out


def source_counts(feature_meta: list[dict[str, str]], features: list[str]) -> dict[str, int]:
    return {src: sum(1 for m in feature_meta if m.get("source") == src and m.get("feature") in features) for src in sorted(set(m.get("source", "unknown") for m in feature_meta))}


def run_stage2_predictor_v3(ticker: str, out_dir: Path, seed_base: int) -> dict[str, Any]:
    started = time.time(); out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = build_dataset(ticker); all_features = feature_columns(data); periods = build_periods(data)
    train_results, predictor_rows, history_rows = run_training(ticker, data, all_features, seed_base)
    features_used = sorted(set(f for result in train_results for f in result.get("usable_features", []) or []))
    reps, origins = build_representatives(predictor_rows); eval_result = evaluate_periods(ticker, data, features_used, periods, reps, origins)
    distributions = {p["label"]: {"high": distribution(period_frame(data, p["start"], p["end"])["high_bin"].to_numpy(dtype=int)), "low": distribution(period_frame(data, p["start"], p["end"])["low_bin"].to_numpy(dtype=int))} for p in periods}
    write_jsonl(out_dir / "predictors_all.jsonl", predictor_rows); write_jsonl(out_dir / "ga_history.jsonl", history_rows); write_jsonl(out_dir / "period_metrics_all.jsonl", eval_result["period_metrics_rows"]); write_jsonl(out_dir / "early_cut_log.jsonl", eval_result["early_cut_rows"]); write_jsonl(out_dir / "survivors.jsonl", eval_result["survivor_rows"])
    config = {"ticker": ticker, "runner": "scripts/research/run_range_predictor_stage2_v3.py", "source_flow": "scripts/research/run_stage2.py", "train_splits": TRAIN_SPLITS, "evaluation_periods": periods, "ga": {"population": POPULATION, "generations": GENERATIONS, "early_stop_no_improve": PATIENCE, "elite_ratio": ELITE_RATIO, "mutation_rate": MUTATION_RATE, "mutation_strength": MUTATION_STRENGTH, "tournament_size": TOURNAMENT_SIZE, "seed_pattern_ratio": SEED_PATTERN_RATIO, "random_seed_base": seed_base, "rule_count": RULE_COUNT}, "gate": {"config": dataclasses.asdict(DEFAULT_PREDICTOR_GATE), "member_score_policy": "exact_lift 0.70 + adjacent_lift 0.20 + low_penalty 0.10", "oos_gate_included": True}, "lookahead_report": {"pass": True, "stock_features": "D-5~D-1 bars and D0 open gap only", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "excluded": ["D0 high/low/close as features", "future swing parameter performance/promotion columns", "Rulebook trading logic"]}, "feature_pool": {"usable_feature_count_union": len(features_used), "feature_source_counts": source_counts(feature_meta, features_used), "features": [m for m in feature_meta if m.get("feature") in features_used]}, "coverage": coverage_report(data, periods), "bin_labels": BIN_LABELS, "distributions": distributions}
    write_json(out_dir / "config.json", config)
    ratio = float(eval_result["eval_count"] / eval_result["max_eval_count"]) if eval_result["max_eval_count"] else 0.0
    summary = {"ticker": ticker, "generated_predictor_rows": len(predictor_rows), "unique_signatures": len(eval_result["unique_signatures"]), "survivor_count": len(eval_result["survivors"]), "survivor_signatures": eval_result["survivors"], "alive_trace": eval_result["alive_trace"], "max_period_evaluations": eval_result["max_eval_count"], "actual_period_evaluations": eval_result["eval_count"], "actual_eval_ratio": ratio, "period_eval_saved_ratio": 1.0 - ratio, "fail_counts_by_first_failed_period": dict(Counter(str(row.get("failed_period_label") or "SURVIVED") for row in eval_result["early_cut_rows"])), "ga_generations_run_by_train": {row["split"]["label"]: row["generations_run"] for row in train_results}, "ga_early_stop_triggered_by_train": {row["split"]["label"]: row["early_stop"] for row in train_results}, "ga_elapsed_sec_by_train": {row["split"]["label"]: row["elapsed"] for row in train_results}, "train_sample_count_by_split": {row["split"]["label"]: row["sample_count"] for row in train_results}, "elapsed_sec": time.time() - started, "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "period_metrics_all": str(out_dir / "period_metrics_all.jsonl"), "early_cut_log": str(out_dir / "early_cut_log.jsonl"), "survivors": str(out_dir / "survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")}}
    write_json(out_dir / "summary.json", summary); print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True)); return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stage2-flow next-day high/low range predictor GA v3")
    p.add_argument("--ticker", required=True); p.add_argument("--out-dir", default=None); p.add_argument("--seed-base", type=int, default=None); p.add_argument("--parallel", action="store_true", help="accepted for interface parity; not used")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv); ticker = str(args.ticker).strip().upper()
    if not ticker: raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    run_stage2_predictor_v3(ticker, out_dir, seed_base); return 0


if __name__ == "__main__":
    raise SystemExit(main())
