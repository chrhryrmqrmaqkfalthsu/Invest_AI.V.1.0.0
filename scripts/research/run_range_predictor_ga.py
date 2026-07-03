#!/usr/bin/env python3
"""
다음날 고저 구간 예측 전용 GA 러너.

목적: LASR 등 단일 종목의 D일 시가 대비 당일 고가/저가가 어느 구간에 들어갈지
D일 시가 이전에 관측 가능한 광범위 feature만으로 예측하는 research-only 파일입니다.

중요한 차이점:
- 이 파일은 매수판단(should_buy)이나 Rulebook 매매로직을 사용하지 않습니다.
- 기존 run_stage2.py의 GA 하이퍼파라미터(pop100/gen50/patience15 등)만 계승합니다.
- 개체는 Rulebook이 아니라 예측 룰 투표 모델입니다: (target, feature, operator, threshold, bin, weight).
- 고가/저가 각각 12구간을 독립 예측하되 하나의 개체가 HIGH/LOW 룰을 모두 가집니다.
- GEN(2020~2022)에서 진화, SELECT(2023)에서 동결, VAL(2024)과 TEST(2025)는 1회 평가만 수행합니다.

Look-ahead 금지:
- feature는 D-5~D-1 일봉 파생값, D일 시가 gap, D-1 확정 시장/뉴스 값만 사용합니다.
- D일 고가/저가/종가 및 그 파생값은 label/평가 외 feature에 절대 사용하지 않습니다.
- 스윙 파라미터의 2024~2025/2025H2 성과·promotion 정보는 사용하지 않습니다.

주의사항:
- 실전 배포 전 검증 전용입니다.
- 단일 종목 파일럿 결과는 실전 근거로 쓰면 안 됩니다.
- 원본 run_stage2.py와 engine/ 하위 파일을 수정하지 않습니다.
- run_live, 실거래, 캐시 갱신, 원격 push와 무관합니다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass, asdict
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
BIN_LABELS = [
    "0.0_0.5", "0.5_1.0", "1.0_1.5", "1.5_2.0", "2.0_2.5", "2.5_3.0",
    "3.0_3.5", "3.5_4.0", "4.0_4.5", "4.5_5.0", "5.0_6.0", "6.0_plus",
]


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
    if value_pct < 1.5:
        return 2
    if value_pct < 2.0:
        return 3
    if value_pct < 2.5:
        return 4
    if value_pct < 3.0:
        return 5
    if value_pct < 3.5:
        return 6
    if value_pct < 4.0:
        return 7
    if value_pct < 4.5:
        return 8
    if value_pct < 5.0:
        return 9
    if value_pct < 6.0:
        return 10
    return 11


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
            features = {
                f"MKT_{sym}_gap_d0": (O[i] / C[i - 1] - 1.0) * 100.0,
                f"MKT_{sym}_prev_ret1": (C[i - 1] / C[i - 2] - 1.0) * 100.0,
                f"MKT_{sym}_ret5": (C[i - 1] / C[i - 6] - 1.0) * 100.0,
                f"MKT_{sym}_vol5": float(np.nanmean(rng[i - 5 : i])),
                f"MKT_{sym}_vol20": float(np.nanmean(rng[i - 20 : i])),
            }
            by_date[d].update(features)
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
            add(row, f"STK_lag{lag}_volratio20", V[j] / np.nanmean(V[max(0, j - 20) : j]) if np.nanmean(V[max(0, j - 20) : j]) else np.nan, "daily_stock", f"D-{lag} volume vs prior 20d avg")
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
    counts = np.bincount(y.astype(int), minlength=12)
    return int(np.argmax(counts))


def random_rule(rng: random.Random, q: dict[str, list[float]]) -> RuleGene:
    f = rng.choice(list(q.keys()))
    return RuleGene(
        target=rng.choice(["HIGH", "LOW"]),
        feature=f,
        op=rng.choice(["<=", ">="]),
        threshold=float(rng.choice(q[f])),
        bin=int(rng.randrange(12)),
        weight=float(rng.uniform(0.5, 3.0)),
    )


def random_individual(rng: random.Random, q: dict[str, list[float]], default_high: int, default_low: int) -> Individual:
    return Individual(rules=[random_rule(rng, q) for _ in range(RULE_COUNT)], default_high_bin=default_high, default_low_bin=default_low)


def predict(ind: Individual, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    n = len(X)
    high_scores = np.zeros((n, 12), dtype=float)
    low_scores = np.zeros((n, 12), dtype=float)
    high_scores[:, ind.default_high_bin] = 1.0
    low_scores[:, ind.default_low_bin] = 1.0
    for rule in ind.rules:
        if rule.feature not in X.columns:
            continue
        vals = X[rule.feature].to_numpy(dtype=float)
        valid = np.isfinite(vals)
        mask = valid & (vals <= rule.threshold if rule.op == "<=" else vals >= rule.threshold)
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


def distribution(y: np.ndarray) -> dict[str, Any]:
    counts = np.bincount(y.astype(int), minlength=12)
    total = int(counts.sum())
    return {
        BIN_LABELS[i]: {"count": int(counts[i]), "pct": float(counts[i] / total * 100.0) if total else 0.0}
        for i in range(12)
    }


def baseline_metrics(df: pd.DataFrame, default_high: int, default_low: int) -> dict[str, float]:
    yh = df["high_bin"].to_numpy(dtype=int)
    yl = df["low_bin"].to_numpy(dtype=int)
    ph = np.full(len(df), default_high, dtype=int)
    pl = np.full(len(df), default_low, dtype=int)
    return score_predictions(yh, yl, ph, pl)


def evaluate_individual(ind: Individual, df: pd.DataFrame, features: list[str], baseline: dict[str, float]) -> dict[str, Any]:
    yh = df["high_bin"].to_numpy(dtype=int)
    yl = df["low_bin"].to_numpy(dtype=int)
    ph, pl = predict(ind, df[features])
    m = score_predictions(yh, yl, ph, pl)
    m["n"] = int(len(df))
    m["combined_exact_lift_pp"] = m["combined_exact_acc_pct"] - baseline["combined_exact_acc_pct"]
    m["combined_adjacent_lift_pp"] = m["combined_adjacent_acc_pct"] - baseline["combined_adjacent_acc_pct"]
    m["high_exact_lift_pp"] = m["high_exact_acc_pct"] - baseline["high_exact_acc_pct"]
    m["low_exact_lift_pp"] = m["low_exact_acc_pct"] - baseline["low_exact_acc_pct"]
    # Rare-bin overprediction penalty: predicted bins with <2% actual GEN share are risky.
    used = sorted(set(ph.tolist() + pl.tolist()))
    m["used_bins"] = used
    return m


def fitness_from_metrics(m: dict[str, Any]) -> float:
    return (
        m["combined_adjacent_lift_pp"] * 1.00
        + m["combined_exact_lift_pp"] * 0.70
        + m["high_exact_lift_pp"] * 0.15
        + m["low_exact_lift_pp"] * 0.15
    )


def mutate(ind: Individual, rng: random.Random, q: dict[str, list[float]]) -> Individual:
    child = Individual(rules=[RuleGene(**asdict(r)) for r in ind.rules], default_high_bin=ind.default_high_bin, default_low_bin=ind.default_low_bin)
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
            rule.bin = int(max(0, min(11, rule.bin + rng.choice([-2, -1, 1, 2]))))
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
    return Individual(
        rules=rules,
        default_high_bin=a.default_high_bin if rng.random() < 0.5 else b.default_high_bin,
        default_low_bin=a.default_low_bin if rng.random() < 0.5 else b.default_low_bin,
    )


def tournament(pop: list[Individual], rng: random.Random) -> Individual:
    competitors = rng.sample(pop, min(TOURNAMENT_SIZE, len(pop)))
    return max(competitors, key=lambda x: x.fitness)


def run_ga_predictor(gen_df: pd.DataFrame, features: list[str], q: dict[str, list[float]], seed: int) -> tuple[Individual, list[dict[str, Any]], dict[str, float]]:
    rng = random.Random(seed)
    yh = gen_df["high_bin"].to_numpy(dtype=int)
    yl = gen_df["low_bin"].to_numpy(dtype=int)
    default_high = mode_bin(yh)
    default_low = mode_bin(yl)
    baseline = baseline_metrics(gen_df, default_high, default_low)
    pop = [random_individual(rng, q, default_high, default_low) for _ in range(POPULATION)]
    best: Individual | None = None
    history: list[dict[str, Any]] = []
    no_improve = 0
    for gen in range(1, GENERATIONS + 1):
        for ind in pop:
            m = evaluate_individual(ind, gen_df, features, baseline)
            ind.metrics = m
            ind.fitness = fitness_from_metrics(m)
        pop.sort(key=lambda x: x.fitness, reverse=True)
        if best is None or pop[0].fitness > best.fitness:
            best = Individual(rules=[RuleGene(**asdict(r)) for r in pop[0].rules], default_high_bin=pop[0].default_high_bin, default_low_bin=pop[0].default_low_bin, fitness=pop[0].fitness, metrics=pop[0].metrics)
            no_improve = 0
        else:
            no_improve += 1
        history.append({"generation": gen, "best_fitness": pop[0].fitness, "avg_fitness": float(np.mean([p.fitness for p in pop])), "best_metrics": pop[0].metrics})
        if no_improve >= PATIENCE:
            break
        elite_n = max(1, int(POPULATION * ELITE_RATIO))
        next_pop = pop[:elite_n]
        while len(next_pop) < POPULATION:
            if rng.random() < SEED_PATTERN_RATIO:
                next_pop.append(random_individual(rng, q, default_high, default_low))
            else:
                child = crossover(tournament(pop, rng), tournament(pop, rng), rng)
                next_pop.append(mutate(child, rng, q))
        pop = next_pop
    assert best is not None
    return best, history, baseline


def individual_to_dict(ind: Individual) -> dict[str, Any]:
    return {
        "default_high_bin": ind.default_high_bin,
        "default_low_bin": ind.default_low_bin,
        "fitness": ind.fitness,
        "metrics": ind.metrics,
        "rules": [asdict(r) for r in ind.rules],
    }


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


def run_range_predictor_ga(ticker: str, out_dir: Path, seed: int) -> dict[str, Any]:
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    data, feature_meta = build_dataset(ticker)
    features = feature_columns(data)
    frames = {p: period_frame(data, p) for p in PERIODS}
    gen_df = frames["GEN"]
    q = make_quantiles(gen_df, features)
    features = [f for f in features if f in q]
    best, history, gen_baseline = run_ga_predictor(gen_df, features, q, seed)
    # SELECT freeze: this file evolves one best on GEN, then reports SELECT/VAL/TEST once. No TEST selection.
    baselines = {p: baseline_metrics(frames[p], best.default_high_bin, best.default_low_bin) for p in PERIODS}
    period_metrics = {p: evaluate_individual(best, frames[p], features, baselines[p]) for p in PERIODS}
    distributions = {
        p: {
            "high": distribution(frames[p]["high_bin"].to_numpy(dtype=int)),
            "low": distribution(frames[p]["low_bin"].to_numpy(dtype=int)),
            "n": int(len(frames[p])),
        }
        for p in PERIODS
    }
    used = used_feature_summary(best, feature_meta)
    summary = {
        "ticker": ticker,
        "runner": "scripts/research/run_range_predictor_ga.py",
        "research_only": True,
        "single_ticker_pilot": True,
        "not_live_basis": True,
        "ga": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "early_stop_no_improve": PATIENCE,
            "elite_ratio": ELITE_RATIO,
            "mutation_rate": MUTATION_RATE,
            "mutation_strength": MUTATION_STRENGTH,
            "tournament_size": TOURNAMENT_SIZE,
            "seed_pattern_ratio": SEED_PATTERN_RATIO,
            "individual": "rule-vote predictor: (target, feature, op, threshold, bin, weight)",
            "rule_count": RULE_COUNT,
            "generations_run": len(history),
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
        "used_features": used,
        "bin_labels": BIN_LABELS,
        "distributions": distributions,
        "baselines": baselines,
        "period_metrics": period_metrics,
        "selected_individual": individual_to_dict(best),
        "history": history,
        "elapsed_sec": time.time() - started,
        "outputs": {
            "summary": str(out_dir / "summary.json"),
            "history": str(out_dir / "ga_history.jsonl"),
            "selected_individual": str(out_dir / "selected_individual.json"),
            "feature_meta": str(out_dir / "feature_meta.jsonl"),
        },
    }
    write_json(out_dir / "summary.json", summary)
    write_json(out_dir / "selected_individual.json", individual_to_dict(best))
    write_jsonl(out_dir / "ga_history.jsonl", history)
    write_jsonl(out_dir / "feature_meta.jsonl", [m for m in feature_meta if m.get("feature") in features])
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Next-day high/low range-bin predictor GA")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=2026070301)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else PROJECT_ROOT / f"exp_{ticker.lower()}_range_predictor_ga_{time.strftime('%Y%m%d_%H%M%S')}"
    run_range_predictor_ga(ticker, out_dir, int(args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
