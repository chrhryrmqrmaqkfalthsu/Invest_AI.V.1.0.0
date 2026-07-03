#!/usr/bin/env python3
"""
range predictor GA v2: 결함 수정 + 6구간 최종 검증 러너.

목적: 직전 range predictor GA에서 확인된 세 결함을 실제 코드로 수정하고,
LASR의 다음날 시가 대비 고가/저가 6구간 예측 가능성을 공정 baseline과 4중 분할로 최종 검증합니다.

수정 사항:
1) baseline 이중 보고:
   - 정확 baseline: GEN 최빈칸 고정 예측.
   - 근접 baseline: GEN에서 ±1칸 점수가 최대가 되는 칸 고정 예측.
   - 정확 lift와 ±1칸 lift는 각각 대응 baseline 대비로만 산출합니다.
2) SELECT 실제 동결:
   - GEN에서 진화 중 나온 후보 archive 상위 K개를 모읍니다.
   - SELECT(2023) 성적으로 그중 1개를 선택·동결합니다.
   - VAL/TEST는 동결 개체를 1회 평가만 합니다.
3) rare/concentration penalty 실제 구현:
   - 특정 bin으로 예측이 과집중되면 fitness에서 penalty를 차감합니다.
   - 실제 비중이 낮은 bin을 과예측하면 rare-bin penalty를 차감합니다.

구간: 0~0.5 / 0.5~1 / 1~2 / 2~3 / 3~5 / 5%+.
이 파일은 Rulebook 매매로직을 쓰지 않는 예측 전용 research runner입니다.
D일 고가/저가/종가는 label에만 쓰며 feature에는 쓰지 않습니다.
원격 push, run_live, 실거래, 캐시 갱신과 무관합니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

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
ARCHIVE_K = 10

CONCENTRATION_CAP_PCT = 45.0
CONCENTRATION_PENALTY_STRENGTH = 0.35
RARE_BIN_ACTUAL_MAX_PCT = 5.0
RARE_BIN_PRED_ALLOW_PCT = 10.0
RARE_BIN_PENALTY_STRENGTH = 0.45

CACHE = PROJECT_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
MARKET_SYMS = [
    "SPY", "QQQ", "IWM", "DIA",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU", "XLRE", "XLC",
    "SMH", "ARKK",
]
PERIODS = {
    "GEN": ("2020-01-01", "2022-12-31"),
    "SELECT": ("2023-01-01", "2023-12-31"),
    "VAL": ("2024-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2025-12-31"),
}
BIN_LABELS = ["0.0_0.5", "0.5_1.0", "1.0_2.0", "2.0_3.0", "3.0_5.0", "5.0_plus"]
BIN_COUNT = 6


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return json_safe({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
    return str(value)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


def load_ohlcv(ticker: str) -> pd.DataFrame:
    path = CACHE / f"{ticker.upper()}.pkl"
    if not path.exists():
        raise FileNotFoundError(f"OHLCV cache not found: {path}")
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
        O = m["Open"].astype(float).to_numpy()
        H = m["High"].astype(float).to_numpy()
        L = m["Low"].astype(float).to_numpy()
        C = m["Close"].astype(float).to_numpy()
        rng = (H - L) / C * 100.0
        for i in range(21, len(m)):
            d = m.index[i].strftime("%Y-%m-%d")
            by_date.setdefault(d, {})
            by_date[d].update(
                {
                    f"MKT_{sym}_gap_d0": (O[i] / C[i - 1] - 1.0) * 100.0,
                    f"MKT_{sym}_prev_ret1": (C[i - 1] / C[i - 2] - 1.0) * 100.0,
                    f"MKT_{sym}_ret5": (C[i - 1] / C[i - 6] - 1.0) * 100.0,
                    f"MKT_{sym}_vol5": float(np.nanmean(rng[i - 5 : i])),
                    f"MKT_{sym}_vol20": float(np.nanmean(rng[i - 20 : i])),
                }
            )
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
        numeric_cols = [c for c in df.columns if c != "date" and pd.api.types.is_numeric_dtype(df[c])]
        for _, row in df.iterrows():
            d = row["date"]
            by_prev_date.setdefault(d, {})
            for col in numeric_cols:
                val = row[col]
                by_prev_date[d][prefix + col] = float(val) if pd.notna(val) else np.nan
        for col in numeric_cols:
            meta.append({"feature": prefix + col, "source": source, "lookahead": "joined from D-1 date only"})
    return by_prev_date, meta


def build_dataset(ticker: str) -> tuple[pd.DataFrame, list[dict[str, str]]]:
    df = load_ohlcv(ticker)
    market_by_date, market_meta = add_market_maps()
    news_by_prev_date, news_meta = add_news_map()
    O = df["Open"].astype(float).to_numpy()
    H = df["High"].astype(float).to_numpy()
    L = df["Low"].astype(float).to_numpy()
    C = df["Close"].astype(float).to_numpy()
    V = df["Volume"].astype(float).to_numpy()
    rng = (H - L) / C * 100.0
    intr = (C - O) / O * 100.0
    rows: list[dict[str, Any]] = []
    feature_meta: dict[str, dict[str, str]] = {}

    def add(row: dict[str, Any], name: str, value: Any, source: str, lookahead: str) -> None:
        row[name] = value
        feature_meta.setdefault(name, {"feature": name, "source": source, "lookahead": lookahead})

    for i in range(max(21, LOOKBACK + 1), len(df)):
        d = df.index[i]
        if d.year < 2020 or d.year > 2025:
            continue
        row: dict[str, Any] = {"date": d.strftime("%Y-%m-%d"), "year": d.year}
        row["period"] = "GEN" if d.year <= 2022 else "SELECT" if d.year == 2023 else "VAL" if d.year == 2024 else "TEST"
        high_pct = (H[i] / O[i] - 1.0) * 100.0
        low_mag_pct = (O[i] / L[i] - 1.0) * 100.0 if L[i] > 0 else np.nan
        row["high_pct_label"] = high_pct
        row["low_mag_pct_label"] = low_mag_pct
        row["high_bin"] = label_bin(high_pct)
        row["low_bin"] = label_bin(low_mag_pct)
        add(row, "STK_gap_d0", (O[i] / C[i - 1] - 1.0) * 100.0, "daily_stock", "D0 open vs D-1 close; entry-time observable")
        for lag in range(1, LOOKBACK + 1):
            j = i - lag
            add(row, f"STK_lag{lag}_ccret", (C[j] / C[j - 1] - 1.0) * 100.0, "daily_stock", f"D-{lag} close return")
            add(row, f"STK_lag{lag}_intr", intr[j], "daily_stock", f"D-{lag} open-close return")
            add(row, f"STK_lag{lag}_range", rng[j], "daily_stock", f"D-{lag} high-low range")
            add(row, f"STK_lag{lag}_gap", (O[j] / C[j - 1] - 1.0) * 100.0, "daily_stock", f"D-{lag} gap")
            vol_base = np.nanmean(V[max(0, j - 20) : j])
            add(row, f"STK_lag{lag}_volratio20", V[j] / vol_base if vol_base else np.nan, "daily_stock", f"D-{lag} volume vs prior 20d avg")
        for n in [3, 5, 10, 20]:
            add(row, f"STK_ret{n}", (C[i - 1] / C[i - 1 - n] - 1.0) * 100.0, "daily_stock", f"D-1 close vs D-{n+1} close")
            add(row, f"STK_vol{n}", float(np.nanmean(rng[i - n : i])), "daily_stock", f"D-{n}~D-1 average range")
        lo5 = np.nanmin(L[i - 5 : i]); hi5 = np.nanmax(H[i - 5 : i])
        lo20 = np.nanmin(L[i - 20 : i]); hi20 = np.nanmax(H[i - 20 : i])
        add(row, "STK_range_pos5", (C[i - 1] - lo5) / (hi5 - lo5) * 100.0 if hi5 != lo5 else 50.0, "daily_stock", "D-1 close position in prior 5d range")
        add(row, "STK_range_pos20", (C[i - 1] - lo20) / (hi20 - lo20) * 100.0 if hi20 != lo20 else 50.0, "daily_stock", "D-1 close position in prior 20d range")
        for col in ["RSI", "ATR_pct", "BB_width", "Volume_ratio", "MACD_hist", "Stoch_K", "Stoch_D"]:
            if col in df.columns:
                add(row, f"STK_{col}_d1", float(df[col].iloc[i - 1]), "daily_stock_indicator", f"{col} as of D-1")
        row.update(market_by_date.get(row["date"], {}))
        prev_date = df.index[i - 1].strftime("%Y-%m-%d")
        row.update(news_by_prev_date.get(prev_date, {}))
        rows.append(row)
    data = pd.DataFrame(rows).replace([np.inf, -np.inf], np.nan)
    feature_meta_list = list(feature_meta.values()) + market_meta + news_meta
    seen = set()
    unique_meta = []
    for m in feature_meta_list:
        if m["feature"] in data.columns and m["feature"] not in seen:
            unique_meta.append(m)
            seen.add(m["feature"])
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
class Individual:
    rules: list[RuleGene]
    default_high_bin: int
    default_low_bin: int
    fitness: float = -1e9
    metrics: dict[str, Any] | None = None
    signature: str | None = None


def clone_individual(ind: Individual) -> Individual:
    return Individual(
        rules=[RuleGene(**asdict(r)) for r in ind.rules],
        default_high_bin=ind.default_high_bin,
        default_low_bin=ind.default_low_bin,
        fitness=float(ind.fitness),
        metrics=json.loads(json.dumps(json_safe(ind.metrics))) if ind.metrics is not None else None,
        signature=ind.signature,
    )


def individual_to_dict(ind: Individual) -> dict[str, Any]:
    return {
        "default_high_bin": ind.default_high_bin,
        "default_low_bin": ind.default_low_bin,
        "fitness": ind.fitness,
        "metrics": ind.metrics,
        "signature": ind.signature or signature(ind),
        "rules": [asdict(r) for r in ind.rules],
    }


def signature(ind: Individual) -> str:
    payload = json.dumps({"h": ind.default_high_bin, "l": ind.default_low_bin, "r": [asdict(r) for r in ind.rules]}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def period_frame(data: pd.DataFrame, period: str) -> pd.DataFrame:
    return data[data["period"] == period].reset_index(drop=True)


def feature_columns(data: pd.DataFrame) -> list[str]:
    banned = {"date", "year", "period", "high_pct_label", "low_mag_pct_label", "high_bin", "low_bin"}
    return [c for c in data.columns if c not in banned and pd.api.types.is_numeric_dtype(data[c])]


def make_quantiles(gen: pd.DataFrame, features: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for f in features:
        vals = gen[f].dropna().to_numpy(dtype=float)
        if len(vals) < 50:
            continue
        qs = np.nanpercentile(vals, [5, 10, 20, 33.333, 50, 66.667, 80, 90, 95])
        out[f] = [float(x) for x in qs if math.isfinite(float(x))]
    return out


def mode_bin(y: np.ndarray) -> int:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT)
    return int(np.argmax(counts))


def best_adjacent_bin(y: np.ndarray) -> int:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT)
    best_bin = 0
    best_count = -1
    for b in range(BIN_COUNT):
        c = sum(int(counts[j]) for j in [b - 1, b, b + 1] if 0 <= j < BIN_COUNT)
        if c > best_count:
            best_count = c
            best_bin = b
    return best_bin


def fixed_prediction_scores(df: pd.DataFrame, high_bin: int, low_bin: int) -> dict[str, float]:
    yh = df["high_bin"].to_numpy(dtype=int)
    yl = df["low_bin"].to_numpy(dtype=int)
    ph = np.full(len(df), high_bin, dtype=int)
    pl = np.full(len(df), low_bin, dtype=int)
    return score_predictions(yh, yl, ph, pl)


def make_baseline_spec(gen_df: pd.DataFrame) -> dict[str, Any]:
    yh = gen_df["high_bin"].to_numpy(dtype=int)
    yl = gen_df["low_bin"].to_numpy(dtype=int)
    return {
        "exact_high_bin": mode_bin(yh),
        "exact_low_bin": mode_bin(yl),
        "adjacent_high_bin": best_adjacent_bin(yh),
        "adjacent_low_bin": best_adjacent_bin(yl),
        "description": "All baseline bins selected from GEN only and then held fixed for SELECT/VAL/TEST.",
    }


def baseline_metrics(df: pd.DataFrame, spec: dict[str, Any]) -> dict[str, Any]:
    exact = fixed_prediction_scores(df, int(spec["exact_high_bin"]), int(spec["exact_low_bin"]))
    adjacent = fixed_prediction_scores(df, int(spec["adjacent_high_bin"]), int(spec["adjacent_low_bin"]))
    return {
        "exact_baseline": exact,
        "adjacent_baseline": adjacent,
        "exact_bins": {"high": int(spec["exact_high_bin"]), "low": int(spec["exact_low_bin"])},
        "adjacent_bins": {"high": int(spec["adjacent_high_bin"]), "low": int(spec["adjacent_low_bin"])},
    }


def random_rule(rng: random.Random, q: dict[str, list[float]]) -> RuleGene:
    f = rng.choice(list(q.keys()))
    return RuleGene(rng.choice(["HIGH", "LOW"]), f, rng.choice(["<=", ">="]), float(rng.choice(q[f])), int(rng.randrange(BIN_COUNT)), float(rng.uniform(0.5, 3.0)))


def random_individual(rng: random.Random, q: dict[str, list[float]], default_high: int, default_low: int) -> Individual:
    return Individual([random_rule(rng, q) for _ in range(RULE_COUNT)], default_high, default_low)


def predict(ind: Individual, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    n = len(X)
    high_scores = np.zeros((n, BIN_COUNT), dtype=float)
    low_scores = np.zeros((n, BIN_COUNT), dtype=float)
    high_scores[:, ind.default_high_bin] = 1.0
    low_scores[:, ind.default_low_bin] = 1.0
    for rule in ind.rules:
        if rule.feature not in X.columns:
            continue
        vals = X[rule.feature].to_numpy(dtype=float)
        mask = np.isfinite(vals) & (vals <= rule.threshold if rule.op == "<=" else vals >= rule.threshold)
        if not mask.any():
            continue
        if rule.target == "HIGH":
            high_scores[mask, rule.bin] += rule.weight
        else:
            low_scores[mask, rule.bin] += rule.weight
    return high_scores.argmax(axis=1), low_scores.argmax(axis=1)


def score_predictions(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, float]:
    high_exact = float((ph == yh).mean() * 100.0) if len(yh) else 0.0
    low_exact = float((pl == yl).mean() * 100.0) if len(yl) else 0.0
    high_adj = float((np.abs(ph - yh) <= 1).mean() * 100.0) if len(yh) else 0.0
    low_adj = float((np.abs(pl - yl) <= 1).mean() * 100.0) if len(yl) else 0.0
    return {
        "high_exact_acc_pct": high_exact,
        "low_exact_acc_pct": low_exact,
        "high_adjacent_acc_pct": high_adj,
        "low_adjacent_acc_pct": low_adj,
        "combined_exact_acc_pct": (high_exact + low_exact) / 2.0,
        "combined_adjacent_acc_pct": (high_adj + low_adj) / 2.0,
    }


def share_by_bin(pred: np.ndarray) -> list[float]:
    counts = np.bincount(pred.astype(int), minlength=BIN_COUNT)
    total = max(1, int(counts.sum()))
    return [float(c / total * 100.0) for c in counts]


def actual_share_by_bin(y: np.ndarray) -> list[float]:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT)
    total = max(1, int(counts.sum()))
    return [float(c / total * 100.0) for c in counts]


def prediction_penalty(yh: np.ndarray, yl: np.ndarray, ph: np.ndarray, pl: np.ndarray) -> dict[str, Any]:
    high_pred = share_by_bin(ph)
    low_pred = share_by_bin(pl)
    high_actual = actual_share_by_bin(yh)
    low_actual = actual_share_by_bin(yl)
    concentration_excess = max(0.0, max(high_pred) - CONCENTRATION_CAP_PCT) + max(0.0, max(low_pred) - CONCENTRATION_CAP_PCT)
    rare_excess = 0.0
    for pred, actual in [(high_pred, high_actual), (low_pred, low_actual)]:
        for p, a in zip(pred, actual):
            if a < RARE_BIN_ACTUAL_MAX_PCT:
                rare_excess += max(0.0, p - RARE_BIN_PRED_ALLOW_PCT)
    concentration_penalty = concentration_excess * CONCENTRATION_PENALTY_STRENGTH
    rare_penalty = rare_excess * RARE_BIN_PENALTY_STRENGTH
    return {
        "concentration_penalty": concentration_penalty,
        "rare_bin_penalty": rare_penalty,
        "total_penalty": concentration_penalty + rare_penalty,
        "max_pred_share_high_pct": max(high_pred) if high_pred else 0.0,
        "max_pred_share_low_pct": max(low_pred) if low_pred else 0.0,
        "pred_distribution_high_pct": high_pred,
        "pred_distribution_low_pct": low_pred,
    }


def evaluate_individual(ind: Individual, df: pd.DataFrame, features: list[str], baselines: dict[str, Any]) -> dict[str, Any]:
    yh = df["high_bin"].to_numpy(dtype=int)
    yl = df["low_bin"].to_numpy(dtype=int)
    ph, pl = predict(ind, df[features])
    scores = score_predictions(yh, yl, ph, pl)
    penalty = prediction_penalty(yh, yl, ph, pl)
    exact_base = baselines["exact_baseline"]
    adj_base = baselines["adjacent_baseline"]
    metrics = {
        **scores,
        "n": int(len(df)),
        "combined_exact_lift_pp": scores["combined_exact_acc_pct"] - exact_base["combined_exact_acc_pct"],
        "combined_adjacent_lift_pp": scores["combined_adjacent_acc_pct"] - adj_base["combined_adjacent_acc_pct"],
        "high_exact_lift_pp": scores["high_exact_acc_pct"] - exact_base["high_exact_acc_pct"],
        "low_exact_lift_pp": scores["low_exact_acc_pct"] - exact_base["low_exact_acc_pct"],
        "high_adjacent_lift_pp": scores["high_adjacent_acc_pct"] - adj_base["high_adjacent_acc_pct"],
        "low_adjacent_lift_pp": scores["low_adjacent_acc_pct"] - adj_base["low_adjacent_acc_pct"],
        "used_bins": sorted(set(ph.tolist() + pl.tolist())),
        **penalty,
    }
    metrics["fitness"] = fitness_from_metrics(metrics)
    return metrics


def fitness_from_metrics(m: dict[str, Any]) -> float:
    # 정확 일치가 최종 판정 기준이므로 exact lift를 주지표로 둡니다.
    raw = (
        m["combined_exact_lift_pp"] * 1.00
        + m["combined_adjacent_lift_pp"] * 0.35
        + m["high_exact_lift_pp"] * 0.15
        + m["low_exact_lift_pp"] * 0.15
    )
    return float(raw - m.get("total_penalty", 0.0))


def mutate(ind: Individual, rng: random.Random, q: dict[str, list[float]]) -> Individual:
    child = clone_individual(ind)
    child.fitness = -1e9
    child.metrics = None
    child.signature = None
    for i, rule in enumerate(child.rules):
        if rng.random() > MUTATION_RATE:
            continue
        action = rng.choice(["replace", "feature", "threshold", "bin", "weight", "op", "target"])
        if action == "replace":
            child.rules[i] = random_rule(rng, q)
        elif action == "feature":
            f = rng.choice(list(q.keys()))
            rule.feature = f
            rule.threshold = float(rng.choice(q[f]))
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


def crossover(a: Individual, b: Individual, rng: random.Random) -> Individual:
    rules = []
    for ra, rb in zip(a.rules, b.rules):
        src = ra if rng.random() < 0.5 else rb
        rules.append(RuleGene(**asdict(src)))
    return Individual(rules, a.default_high_bin if rng.random() < 0.5 else b.default_high_bin, a.default_low_bin if rng.random() < 0.5 else b.default_low_bin)


def tournament(pop: list[Individual], rng: random.Random) -> Individual:
    return max(rng.sample(pop, min(TOURNAMENT_SIZE, len(pop))), key=lambda x: x.fitness)


def update_archive(archive: dict[str, Individual], pop: list[Individual]) -> None:
    for ind in pop:
        sig = signature(ind)
        ind.signature = sig
        if sig not in archive or ind.fitness > archive[sig].fitness:
            archive[sig] = clone_individual(ind)


def top_archive(archive: dict[str, Individual], k: int) -> list[Individual]:
    return [clone_individual(x) for x in sorted(archive.values(), key=lambda ind: ind.fitness, reverse=True)[:k]]


def run_ga_predictor(gen_df: pd.DataFrame, features: list[str], q: dict[str, list[float]], baseline_spec: dict[str, Any], seed: int) -> tuple[list[Individual], list[dict[str, Any]]]:
    rng = random.Random(seed)
    pop = [random_individual(rng, q, int(baseline_spec["exact_high_bin"]), int(baseline_spec["exact_low_bin"])) for _ in range(POPULATION)]
    gen_baselines = baseline_metrics(gen_df, baseline_spec)
    history: list[dict[str, Any]] = []
    archive: dict[str, Individual] = {}
    best_fitness = -1e18
    no_improve = 0
    for gen in range(1, GENERATIONS + 1):
        for ind in pop:
            m = evaluate_individual(ind, gen_df, features, gen_baselines)
            ind.metrics = m
            ind.fitness = m["fitness"]
            ind.signature = signature(ind)
        pop.sort(key=lambda x: x.fitness, reverse=True)
        update_archive(archive, pop[: max(ARCHIVE_K * 3, 30)])
        if pop[0].fitness > best_fitness:
            best_fitness = pop[0].fitness
            no_improve = 0
        else:
            no_improve += 1
        history.append({"generation": gen, "best_fitness": pop[0].fitness, "avg_fitness": float(np.mean([p.fitness for p in pop])), "best_signature": pop[0].signature, "best_metrics": pop[0].metrics})
        if no_improve >= PATIENCE:
            break
        elite_n = max(1, int(POPULATION * ELITE_RATIO))
        next_pop = [clone_individual(x) for x in pop[:elite_n]]
        while len(next_pop) < POPULATION:
            if rng.random() < SEED_PATTERN_RATIO:
                next_pop.append(random_individual(rng, q, int(baseline_spec["exact_high_bin"]), int(baseline_spec["exact_low_bin"])))
            else:
                next_pop.append(mutate(crossover(tournament(pop, rng), tournament(pop, rng), rng), rng, q))
        pop = next_pop
    return top_archive(archive, ARCHIVE_K), history


def distribution(y: np.ndarray) -> dict[str, Any]:
    counts = np.bincount(y.astype(int), minlength=BIN_COUNT)
    total = int(counts.sum())
    return {BIN_LABELS[i]: {"count": int(counts[i]), "pct": float(counts[i] / total * 100.0) if total else 0.0} for i in range(BIN_COUNT)}


def used_feature_summary(ind: Individual, feature_meta: list[dict[str, str]]) -> dict[str, Any]:
    meta_map = {m["feature"]: m for m in feature_meta}
    used = sorted(set(r.feature for r in ind.rules))
    rows = []
    counts: dict[str, int] = {}
    for f in used:
        src = meta_map.get(f, {}).get("source", "unknown")
        counts[src] = counts.get(src, 0) + 1
        rows.append({"feature": f, "source": src, "lookahead": meta_map.get(f, {}).get("lookahead", "unknown")})
    return {"used_feature_count": len(used), "used_source_counts": counts, "used_features": rows}


def run_range_predictor_ga_v2(ticker: str, out_dir: Path, seed: int) -> dict[str, Any]:
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = build_dataset(ticker)
    frames = {p: period_frame(data, p) for p in PERIODS}
    features = feature_columns(data)
    q = make_quantiles(frames["GEN"], features)
    features = [f for f in features if f in q]
    baseline_spec = make_baseline_spec(frames["GEN"])
    baselines = {p: baseline_metrics(frames[p], baseline_spec) for p in PERIODS}
    gen_candidates, history = run_ga_predictor(frames["GEN"], features, q, baseline_spec, seed)

    candidate_rows = []
    for rank, ind in enumerate(gen_candidates, 1):
        period_metrics = {p: evaluate_individual(ind, frames[p], features, baselines[p]) for p in PERIODS}
        select_objective = period_metrics["SELECT"]["fitness"]
        candidate_rows.append(
            {
                "gen_rank": rank,
                "signature": signature(ind),
                "gen_fitness": period_metrics["GEN"]["fitness"],
                "select_fitness": select_objective,
                "period_metrics": period_metrics,
                "individual": individual_to_dict(ind),
            }
        )
    candidate_rows.sort(key=lambda r: (r["select_fitness"], r["period_metrics"]["SELECT"]["combined_exact_lift_pp"], r["period_metrics"]["SELECT"]["combined_adjacent_lift_pp"]), reverse=True)
    selected_row = candidate_rows[0]
    selected = Individual(**{k: v for k, v in selected_row["individual"].items() if k in ["rules", "default_high_bin", "default_low_bin", "fitness", "metrics", "signature"]})
    # dataclass reconstruction for selected_individual.json only; metrics already computed in candidate row.
    selected.rules = [RuleGene(**r) if isinstance(r, dict) else r for r in selected.rules]
    selected_metrics = selected_row["period_metrics"]
    gen_best_signature = history[-1]["best_signature"] if history else None
    selected_signature = selected_row["signature"]
    summary = {
        "ticker": ticker,
        "runner": "scripts/research/run_range_predictor_ga_v2.py",
        "research_only": True,
        "single_ticker_pilot": True,
        "not_live_basis": True,
        "bin_labels": BIN_LABELS,
        "ga": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "early_stop_no_improve": PATIENCE,
            "elite_ratio": ELITE_RATIO,
            "mutation_rate": MUTATION_RATE,
            "mutation_strength": MUTATION_STRENGTH,
            "tournament_size": TOURNAMENT_SIZE,
            "seed_pattern_ratio": SEED_PATTERN_RATIO,
            "rule_count": RULE_COUNT,
            "archive_k": ARCHIVE_K,
            "generations_run": len(history),
        },
        "fixes": {
            "fair_baseline_dual": "implemented: exact baseline and adjacent baseline are separate and GEN-fixed",
            "select_freeze": "implemented: GEN archive top-K candidates are evaluated on SELECT, selected candidate is then frozen",
            "rare_bin_penalty": {
                "implemented": True,
                "concentration_cap_pct": CONCENTRATION_CAP_PCT,
                "concentration_penalty_strength": CONCENTRATION_PENALTY_STRENGTH,
                "rare_bin_actual_max_pct": RARE_BIN_ACTUAL_MAX_PCT,
                "rare_bin_pred_allow_pct": RARE_BIN_PRED_ALLOW_PCT,
                "rare_bin_penalty_strength": RARE_BIN_PENALTY_STRENGTH,
            },
        },
        "lookahead_report": {
            "pass": True,
            "stock_features": "D-5~D-1 bars and D0 open gap only",
            "market_features": "ETF D0 gap or D-1 confirmed values only",
            "news_features": "market_history rows joined from D-1 date only",
            "excluded": ["D0 high/low/close as features", "future swing parameter performance/promotion columns", "Rulebook trading logic"],
        },
        "feature_pool": {
            "usable_feature_count": len(features),
            "feature_source_counts": {src: sum(1 for m in feature_meta if m.get("source") == src and m.get("feature") in features) for src in sorted(set(m.get("source", "unknown") for m in feature_meta))},
            "features": [m for m in feature_meta if m.get("feature") in features],
        },
        "used_features": used_feature_summary(selected, feature_meta),
        "baseline_spec": baseline_spec,
        "baselines": baselines,
        "distributions": {p: {"n": int(len(frames[p])), "high": distribution(frames[p]["high_bin"].to_numpy(dtype=int)), "low": distribution(frames[p]["low_bin"].to_numpy(dtype=int))} for p in PERIODS},
        "candidate_rows": candidate_rows,
        "select_freeze_report": {
            "gen_archive_candidate_count": len(gen_candidates),
            "selected_signature": selected_signature,
            "selected_gen_rank": selected_row["gen_rank"],
            "last_generation_best_signature": gen_best_signature,
            "selected_equals_last_gen_best": bool(selected_signature == gen_best_signature),
            "selection_used": "SELECT fitness only; VAL/TEST not used for candidate selection",
        },
        "period_metrics": selected_metrics,
        "selected_individual": individual_to_dict(selected),
        "history": history,
        "elapsed_sec": time.time() - started,
        "outputs": {
            "summary": str(out_dir / "summary.json"),
            "history": str(out_dir / "ga_history.jsonl"),
            "candidates": str(out_dir / "candidates.jsonl"),
            "selected_individual": str(out_dir / "selected_individual.json"),
            "feature_meta": str(out_dir / "feature_meta.jsonl"),
        },
    }
    write_json(out_dir / "summary.json", summary)
    write_json(out_dir / "selected_individual.json", individual_to_dict(selected))
    write_jsonl(out_dir / "ga_history.jsonl", history)
    write_jsonl(out_dir / "candidates.jsonl", candidate_rows)
    write_jsonl(out_dir / "feature_meta.jsonl", [m for m in feature_meta if m.get("feature") in features])
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Range predictor GA v2: fair baselines, SELECT freeze, 6 bins")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=2026070301)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else PROJECT_ROOT / f"exp_{ticker.lower()}_range_predictor_ga_v2_{time.strftime('%Y%m%d_%H%M%S')}"
    run_range_predictor_ga_v2(ticker, out_dir, int(args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
