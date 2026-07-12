#!/usr/bin/env python3
"""Research-only 5-day GA entry filter for a 2-session +3% target.

Inputs are saved rulebook entry_signal_date rows.  Features use only sessions
strictly before the signal date.  GA fitness uses train only; stress and OOS
are frozen validation gates.  No live module imports this file.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.adapters.factory import get_adapter
from engine.live.elite_shadow_report import build_elite_shadow_report

OUT_DIR = Path(__file__).resolve().parent
BATCH_ROOT = PROJECT_ROOT / "exp_batch_stage123_2009_20260616_full"

STRESS_START, STRESS_END = "2020-01-01", "2022-06-30"
TRAIN_START, TRAIN_END = "2022-07-01", "2025-06-30"
OOS_START = "2025-07-01"
TARGET_RETURN = 0.03
HORIZON_SESSIONS = 2

POPULATION = 128
GENERATIONS = 60
ELITE_COUNT = 16
TOURNAMENT = 4
MUTATION_RATE = 0.18
PATIENCE = 18
MIN_ACTIVE_FEATURES = 1
MAX_ACTIVE_FEATURES = 5

NUMERIC_FEATURES = [
    "ret_d5_pct", "ret_d4_pct", "ret_d3_pct", "ret_d2_pct", "ret_d1_pct",
    "cumulative_ret5_pct", "up_days5", "down_days5", "days_since_high5",
    "close_pos5", "pullback_from_high5_pct", "single_up_day5_pct",
    "fade_after_surge_score",
]
BINARY_FEATURE = "recent_turn_down"
QUANTILE_LEVELS = np.array([0.0, 0.02, 0.05, 0.10, 0.20, 1 / 3, 0.50, 2 / 3, 0.80, 0.90, 0.95, 0.98, 1.0])

DATASET_FIELDS = [
    "candidate_id", "stage", "ticker", "rulebook_hash", "signal_date", "regime",
    "signal_price", "future_high_1", "future_high_2", "future_max_high",
    "forward_max_return_pct", "label_2d3pct", "high5", "low5", "close_d1",
    *NUMERIC_FEATURES, BINARY_FEATURE,
]


def safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out: dict[str, Any] = {}
            for key in fields:
                value = row.get(key)
                out[key] = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else value
            writer.writerow(out)


def regime_for_date(date_text: str) -> str | None:
    if date_text < STRESS_START:
        return None
    if date_text <= STRESS_END:
        return "stress"
    if date_text <= TRAIN_END:
        return "train"
    return "oos"


def resolve_rl_replay(candidate: dict[str, Any]) -> Path:
    source = Path(str(candidate.get("source_file") or ""))
    if not source.is_absolute() and not str(source).startswith(str(BATCH_ROOT)):
        source = BATCH_ROOT / source
    return source.parent / "rl_replay_trades.jsonl"


def load_signal_dates(candidate: dict[str, Any]) -> list[str]:
    path = resolve_rl_replay(candidate)
    if not path.exists():
        return []
    target_hash = str(candidate.get("rulebook_hash") or "")
    dates: set[str] = set()
    with path.open("r", encoding="utf-8", errors="ignore") as fp:
        for line in fp:
            try:
                row = json.loads(line)
            except Exception:
                continue
            hashes = {str(row.get("rulebook_hash") or ""), str(row.get("final_rulebook_hash") or "")}
            if target_hash not in hashes:
                continue
            date_text = str(row.get("entry_signal_date") or "")[:10]
            if date_text and date_text >= STRESS_START:
                dates.add(date_text)
    return sorted(dates)


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    idx = pd.to_datetime(out.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    out = out[~out.index.isna()].sort_index()
    return out[~out.index.duplicated(keep="last")]


def extract_features(df: pd.DataFrame, signal_date: str) -> dict[str, Any] | None:
    ts = pd.Timestamp(signal_date)
    if ts not in df.index:
        return None
    loc = df.index.get_loc(ts)
    if isinstance(loc, (slice, np.ndarray)):
        return None
    i = int(loc)
    if i < 6:
        return None
    required = ["Open", "High", "Low", "Close", "Volume"]
    if any(col not in df.columns for col in required):
        return None
    # D-6..D-1. Signal-day OHLC is intentionally excluded.
    prior6 = df.iloc[i - 6 : i]
    path5 = df.iloc[i - 5 : i]
    closes = [safe_float(v) for v in prior6["Close"].tolist()]
    highs = [safe_float(v) for v in path5["High"].tolist()]
    lows = [safe_float(v) for v in path5["Low"].tolist()]
    if len(closes) != 6 or len(highs) != 5 or len(lows) != 5:
        return None
    if any(not math.isfinite(v) or v <= 0 for v in closes) or any(not math.isfinite(v) for v in highs + lows):
        return None
    daily_rets = [100.0 * (closes[j] / closes[j - 1] - 1.0) for j in range(1, 6)]
    high5, low5, close_d1 = max(highs), min(lows), closes[-1]
    span = high5 - low5
    close_pos5 = 0.5 if span <= 0 else min(1.0, max(0.0, (close_d1 - low5) / span))
    first3_max = max(daily_rets[:3])
    last2_ret = 100.0 * (closes[-1] / closes[-3] - 1.0)
    return {
        "high5": high5,
        "low5": low5,
        "close_d1": close_d1,
        "ret_d5_pct": daily_rets[0],
        "ret_d4_pct": daily_rets[1],
        "ret_d3_pct": daily_rets[2],
        "ret_d2_pct": daily_rets[3],
        "ret_d1_pct": daily_rets[4],
        "cumulative_ret5_pct": 100.0 * (closes[-1] / closes[0] - 1.0),
        "up_days5": sum(1 for x in daily_rets if x > 0),
        "down_days5": sum(1 for x in daily_rets if x < 0),
        "days_since_high5": 4 - max(range(5), key=lambda j: highs[j]),
        "close_pos5": close_pos5,
        "pullback_from_high5_pct": max(0.0, 100.0 * (high5 / close_d1 - 1.0)),
        "single_up_day5_pct": max(daily_rets),
        "fade_after_surge_score": max(0.0, first3_max) + max(0.0, -last2_ret),
        BINARY_FEATURE: int(daily_rets[-2] > 0 and daily_rets[-1] < 0),
    }


def build_labeled_row(candidate: dict[str, Any], df: pd.DataFrame, signal_date: str) -> dict[str, Any] | None:
    features = extract_features(df, signal_date)
    if features is None:
        return None
    ts = pd.Timestamp(signal_date)
    i = int(df.index.get_loc(ts))
    if i + HORIZON_SESSIONS >= len(df):
        return None
    future = df.iloc[i + 1 : i + 1 + HORIZON_SESSIONS]
    signal_price = safe_float(df.iloc[i]["Close"])
    future_highs = [safe_float(v) for v in future["High"].tolist()]
    if signal_price <= 0 or len(future_highs) != 2 or any(not math.isfinite(v) for v in future_highs):
        return None
    future_max = max(future_highs)
    forward_pct = 100.0 * (future_max / signal_price - 1.0)
    date_text = ts.strftime("%Y-%m-%d")
    return {
        "candidate_id": candidate["candidate_id"],
        "stage": candidate["stage"],
        "ticker": candidate["ticker"],
        "rulebook_hash": candidate["rulebook_hash"],
        "signal_date": date_text,
        "regime": regime_for_date(date_text),
        "signal_price": signal_price,
        "future_high_1": future_highs[0],
        "future_high_2": future_highs[1],
        "future_max_high": future_max,
        "forward_max_return_pct": forward_pct,
        "label_2d3pct": int(forward_pct >= TARGET_RETURN * 100.0),
        **features,
    }


@dataclass
class Individual:
    active: np.ndarray
    q_low: np.ndarray
    q_high: np.ndarray
    turn_mode: int
    fitness: float = float("-inf")

    def clone(self) -> "Individual":
        return Individual(self.active.copy(), self.q_low.copy(), self.q_high.copy(), int(self.turn_mode), float(self.fitness))


def repair(ind: Individual, rng: np.random.Generator) -> None:
    ind.q_low = np.clip(ind.q_low, 0.0, 0.85)
    ind.q_high = np.clip(ind.q_high, 0.15, 1.0)
    for j in range(len(ind.active)):
        if ind.q_high[j] - ind.q_low[j] < 0.15:
            mid = 0.5 * (ind.q_high[j] + ind.q_low[j])
            ind.q_low[j] = max(0.0, mid - 0.075)
            ind.q_high[j] = min(1.0, mid + 0.075)
    while int(ind.active.sum()) < MIN_ACTIVE_FEATURES:
        ind.active[int(rng.integers(0, len(ind.active)))] = True
    while int(ind.active.sum()) > MAX_ACTIVE_FEATURES:
        ind.active[int(rng.choice(np.flatnonzero(ind.active)))] = False
    ind.turn_mode = int(max(0, min(2, ind.turn_mode)))


def random_individual(rng: np.random.Generator) -> Individual:
    n = len(NUMERIC_FEATURES)
    active = np.zeros(n, dtype=bool)
    active[rng.choice(n, size=int(rng.integers(1, MAX_ACTIVE_FEATURES + 1)), replace=False)] = True
    lows = rng.uniform(0.0, 0.70, n)
    highs = np.minimum(1.0, lows + rng.uniform(0.15, 0.55, n))
    ind = Individual(active, lows, highs, int(rng.choice([0, 0, 0, 1, 2])))
    repair(ind, rng)
    return ind


def individual_mask(ind: Individual, X: np.ndarray, turn: np.ndarray, quantiles: np.ndarray) -> np.ndarray:
    mask = np.ones(len(X), dtype=bool)
    for j in np.flatnonzero(ind.active):
        lo = np.interp(ind.q_low[j], QUANTILE_LEVELS, quantiles[j])
        hi = np.interp(ind.q_high[j], QUANTILE_LEVELS, quantiles[j])
        mask &= np.isfinite(X[:, j]) & (X[:, j] >= lo) & (X[:, j] <= hi)
    if ind.turn_mode == 1:
        mask &= turn == 0
    elif ind.turn_mode == 2:
        mask &= turn == 1
    return mask


def metric_dict(y: np.ndarray, mask: np.ndarray) -> dict[str, float | int]:
    n, positive, passed = len(y), int(y.sum()), int(mask.sum())
    passed_positive = int(y[mask].sum()) if passed else 0
    base = positive / n if n else 0.0
    precision = passed_positive / passed if passed else 0.0
    return {
        "signal_count": int(n), "positive_count": positive, "base_rate": base,
        "passed_count": passed, "passed_positive_count": passed_positive,
        "precision": precision,
        "recall": passed_positive / positive if positive else 0.0,
        "coverage": passed / n if n else 0.0,
        "lift_pp": 100.0 * (precision - base),
    }


def train_min_pass(n: int) -> int:
    return max(8, int(math.ceil(0.12 * n)))


def validation_min_pass(n: int) -> int:
    return max(3, int(math.ceil(0.10 * n)))


def fitness(ind: Individual, X: np.ndarray, turn: np.ndarray, y: np.ndarray, quantiles: np.ndarray) -> float:
    m = metric_dict(y, individual_mask(ind, X, turn, quantiles))
    if int(m["passed_count"]) < train_min_pass(len(y)):
        return -1000.0 + 2.0 * int(m["passed_count"])
    active_count = int(ind.active.sum()) + int(ind.turn_mode != 0)
    score = (
        100.0 * float(m["precision"])
        + 45.0 * (float(m["precision"]) - float(m["base_rate"]))
        + 12.0 * float(m["recall"])
        + 4.0 * min(float(m["coverage"]), 0.35)
        - active_count
    )
    if float(m["precision"]) < max(0.45, float(m["base_rate"]) + 0.05):
        score -= 25.0
    return score


def crossover(a: Individual, b: Individual, rng: np.random.Generator) -> Individual:
    choose = rng.random(len(NUMERIC_FEATURES)) < 0.5
    child = Individual(
        np.where(choose, a.active, b.active),
        np.where(choose, a.q_low, b.q_low),
        np.where(choose, a.q_high, b.q_high),
        a.turn_mode if rng.random() < 0.5 else b.turn_mode,
    )
    repair(child, rng)
    return child


def mutate(ind: Individual, rng: np.random.Generator) -> None:
    for j in range(len(NUMERIC_FEATURES)):
        if rng.random() < MUTATION_RATE:
            ind.active[j] = not bool(ind.active[j])
        if rng.random() < MUTATION_RATE:
            ind.q_low[j] += float(rng.normal(0.0, 0.08))
        if rng.random() < MUTATION_RATE:
            ind.q_high[j] += float(rng.normal(0.0, 0.08))
    if rng.random() < MUTATION_RATE:
        ind.turn_mode = int(rng.integers(0, 3))
    repair(ind, rng)


def tournament(pop: list[Individual], rng: np.random.Generator) -> Individual:
    idx = rng.choice(len(pop), size=min(TOURNAMENT, len(pop)), replace=False)
    return max((pop[int(i)] for i in idx), key=lambda x: x.fitness)


def train_entity(rows: pd.DataFrame, candidate_id: str) -> tuple[Individual | None, np.ndarray | None, list[dict[str, Any]]]:
    train = rows[rows["regime"] == "train"]
    if len(train) < 10:
        return None, None, []
    X = train[NUMERIC_FEATURES].to_numpy(float)
    turn = train[BINARY_FEATURE].to_numpy(int)
    y = train["label_2d3pct"].to_numpy(int)
    quantiles = np.array([np.quantile(X[:, j][np.isfinite(X[:, j])], QUANTILE_LEVELS) for j in range(X.shape[1])])
    rng = np.random.default_rng(int(hashlib.sha256(candidate_id.encode()).hexdigest()[:8], 16))
    pop = [random_individual(rng) for _ in range(POPULATION)]
    history: list[dict[str, Any]] = []
    best_seen, stale = float("-inf"), 0
    for generation in range(1, GENERATIONS + 1):
        for ind in pop:
            ind.fitness = fitness(ind, X, turn, y, quantiles)
        pop.sort(key=lambda z: z.fitness, reverse=True)
        best = pop[0]
        m = metric_dict(y, individual_mask(best, X, turn, quantiles))
        history.append({
            "candidate_id": candidate_id, "generation": generation,
            "best_fitness": best.fitness, "best_precision": m["precision"],
            "best_recall": m["recall"], "best_passed_count": m["passed_count"],
            "best_active_feature_count": int(best.active.sum()) + int(best.turn_mode != 0),
            "population_avg_fitness": float(np.mean([z.fitness for z in pop])),
            "early_stop": False,
        })
        if best.fitness > best_seen + 1e-9:
            best_seen, stale = best.fitness, 0
        else:
            stale += 1
        if stale >= PATIENCE:
            history[-1]["early_stop"] = True
            break
        next_pop = [z.clone() for z in pop[:ELITE_COUNT]]
        while len(next_pop) < POPULATION:
            child = crossover(tournament(pop, rng), tournament(pop, rng), rng)
            mutate(child, rng)
            next_pop.append(child)
        pop = next_pop
    for ind in pop:
        ind.fitness = fitness(ind, X, turn, y, quantiles)
    pop.sort(key=lambda z: z.fitness, reverse=True)
    return pop[0].clone(), quantiles, history


def gene_dict(ind: Individual, quantiles: np.ndarray) -> dict[str, Any]:
    rules = []
    for j in np.flatnonzero(ind.active):
        rules.append({
            "feature": NUMERIC_FEATURES[int(j)],
            "q_low": float(ind.q_low[j]), "q_high": float(ind.q_high[j]),
            "value_low": float(np.interp(ind.q_low[j], QUANTILE_LEVELS, quantiles[j])),
            "value_high": float(np.interp(ind.q_high[j], QUANTILE_LEVELS, quantiles[j])),
        })
    return {"rules": rules, "recent_turn_down_mode": int(ind.turn_mode), "fitness": float(ind.fitness)}


def validation_pass(metrics: dict[str, Any], train_precision: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    n = int(metrics["signal_count"])
    minimum = validation_min_pass(n)
    floor = max(0.45, float(metrics["base_rate"]) + 0.05)
    if n < 5:
        reasons.append("signal_count_lt_5")
    if int(metrics["passed_count"]) < minimum:
        reasons.append(f"passed_count_lt_{minimum}")
    if float(metrics["precision"]) < floor:
        reasons.append(f"precision_lt_{floor:.4f}")
    if train_precision - float(metrics["precision"]) > 0.20:
        reasons.append("train_precision_gap_gt_0.20")
    return not reasons, reasons


def main() -> int:
    started = time.time()
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    candidates = list(report.get("candidates") or [])
    dataset_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    contexts: dict[str, pd.DataFrame] = {}

    for candidate in candidates:
        ticker = str(candidate["ticker"])
        try:
            df = normalize_ohlcv(get_adapter(ticker).load_history(years=7))
            contexts[ticker] = df
        except Exception as exc:
            errors.append({"candidate_id": candidate["candidate_id"], "ticker": ticker, "error": f"history:{exc}"})
            continue
        for signal_date in load_signal_dates(candidate):
            try:
                row = build_labeled_row(candidate, df, signal_date)
            except Exception as exc:
                errors.append({"candidate_id": candidate["candidate_id"], "ticker": ticker, "signal_date": signal_date, "error": str(exc)})
                row = None
            if row and row.get("regime"):
                dataset_rows.append(row)

    dataset_rows.sort(key=lambda r: (r["candidate_id"], r["signal_date"]))
    write_csv(OUT_DIR / "signal_dataset.csv", dataset_rows, DATASET_FIELDS)
    if errors:
        write_csv(OUT_DIR / "data_errors.csv", errors)
    frame = pd.DataFrame(dataset_rows)

    distribution: list[dict[str, Any]] = []
    for scope, cid, ticker, group in [("ALL", "", "", frame)] + [
        ("ENTITY", cid, str(group.iloc[0]["ticker"]), group) for cid, group in frame.groupby("candidate_id")
    ]:
        for regime in ["stress", "train", "oos", "all"]:
            part = group if regime == "all" else group[group["regime"] == regime]
            n = len(part)
            pos = int(part["label_2d3pct"].sum()) if n else 0
            distribution.append({
                "scope": scope, "candidate_id": cid, "ticker": ticker, "regime": regime,
                "signal_count": n, "positive_count": pos,
                "positive_rate": pos / n if n else 0.0,
                "mean_forward_max_return_pct": float(part["forward_max_return_pct"].mean()) if n else None,
                "median_forward_max_return_pct": float(part["forward_max_return_pct"].median()) if n else None,
            })
    write_csv(OUT_DIR / "label_distribution.csv", distribution)

    training_log: list[dict[str, Any]] = []
    regime_rows: list[dict[str, Any]] = []
    survivor_rows: list[dict[str, Any]] = []
    registry: dict[str, tuple[dict[str, Any], np.ndarray, Individual]] = {}
    passed_signals: list[dict[str, Any]] = []

    for candidate_id, group in frame.groupby("candidate_id"):
        group = group.sort_values("signal_date")
        ticker, stage = str(group.iloc[0]["ticker"]), str(group.iloc[0]["stage"])
        champion, quantiles, history = train_entity(group, candidate_id)
        training_log.extend({**row, "ticker": ticker, "stage": stage} for row in history)
        if champion is None or quantiles is None:
            survivor_rows.append({
                "candidate_id": candidate_id, "ticker": ticker, "stage": stage,
                "survivor": False, "status": "INSUFFICIENT_TRAIN_SIGNALS",
                "stress_signal_count": int((group["regime"] == "stress").sum()),
                "train_signal_count": int((group["regime"] == "train").sum()),
                "oos_signal_count": int((group["regime"] == "oos").sum()),
            })
            continue
        metrics: dict[str, dict[str, Any]] = {}
        for regime in ["stress", "train", "oos"]:
            part = group[group["regime"] == regime]
            X = part[NUMERIC_FEATURES].to_numpy(float)
            turn = part[BINARY_FEATURE].to_numpy(int)
            y = part["label_2d3pct"].to_numpy(int)
            mask = individual_mask(champion, X, turn, quantiles) if len(part) else np.zeros(0, bool)
            metrics[regime] = metric_dict(y, mask)
            for mode, mode_mask in [("baseline", np.ones(len(part), bool)), ("filtered", mask)]:
                regime_rows.append({
                    "candidate_id": candidate_id, "ticker": ticker, "stage": stage,
                    "regime": regime, "mode": mode, **metric_dict(y, mode_mask),
                })
            for _, selected in part.loc[mask].iterrows():
                passed_signals.append({
                    "candidate_id": candidate_id, "ticker": ticker, "stage": stage,
                    "regime": regime, "signal_date": selected["signal_date"],
                    "label_2d3pct": int(selected["label_2d3pct"]),
                    "forward_max_return_pct": float(selected["forward_max_return_pct"]),
                })
        train_m = metrics["train"]
        stress_ok, stress_reasons = validation_pass(metrics["stress"], float(train_m["precision"]))
        oos_ok, oos_reasons = validation_pass(metrics["oos"], float(train_m["precision"]))
        train_reasons: list[str] = []
        train_floor = max(0.50, float(train_m["base_rate"]) + 0.10)
        if int(train_m["passed_count"]) < train_min_pass(int(train_m["signal_count"])):
            train_reasons.append(f"passed_count_lt_{train_min_pass(int(train_m['signal_count']))}")
        if float(train_m["precision"]) < train_floor:
            train_reasons.append(f"precision_lt_{train_floor:.4f}")
        survivor = not train_reasons and stress_ok and oos_ok
        row: dict[str, Any] = {
            "candidate_id": candidate_id, "ticker": ticker, "stage": stage,
            "survivor": survivor, "status": "SURVIVOR" if survivor else "FAILED_GATE",
            "train_reasons": train_reasons, "stress_reasons": stress_reasons,
            "oos_reasons": oos_reasons, "train_fitness": champion.fitness,
            "gene": gene_dict(champion, quantiles),
        }
        for regime in ["stress", "train", "oos"]:
            for key, value in metrics[regime].items():
                row[f"{regime}_{key}"] = value
        row["train_stress_precision_gap"] = float(train_m["precision"]) - float(metrics["stress"]["precision"])
        row["train_oos_precision_gap"] = float(train_m["precision"]) - float(metrics["oos"]["precision"])
        survivor_rows.append(row)
        registry[candidate_id] = (row, quantiles, champion)

    write_csv(OUT_DIR / "training_log.csv", training_log)
    write_csv(OUT_DIR / "per_regime_metrics.csv", regime_rows)
    write_csv(OUT_DIR / "survivor_summary.csv", survivor_rows)
    with (OUT_DIR / "survivors.jsonl").open("w", encoding="utf-8") as fp:
        for row in survivor_rows:
            if row.get("survivor"):
                fp.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    overfit: list[dict[str, Any]] = []
    for row in survivor_rows:
        if row.get("status") == "INSUFFICIENT_TRAIN_SIGNALS":
            continue
        selected = [x for x in passed_signals if x["candidate_id"] == row["candidate_id"]]
        positive_returns = sorted((max(0.0, float(x["forward_max_return_pct"])) for x in selected), reverse=True)
        denom = sum(positive_returns)
        top3_share = sum(positive_returns[:3]) / denom if denom > 0 else 0.0
        overfit.append({
            "scope": "ENTITY", "candidate_id": row["candidate_id"], "ticker": row["ticker"],
            "survivor": row["survivor"], "train_precision": row.get("train_precision"),
            "stress_precision": row.get("stress_precision"), "oos_precision": row.get("oos_precision"),
            "train_stress_precision_gap": row.get("train_stress_precision_gap"),
            "train_oos_precision_gap": row.get("train_oos_precision_gap"),
            "all_passed_count": len(selected), "top3_positive_return_share": top3_share,
            "extreme_value_concentration_flag": bool(top3_share > 0.60 and len(selected) >= 5),
        })
    survivor_ids = {str(row["candidate_id"]) for row in survivor_rows if row.get("survivor")}
    pooled = [x for x in passed_signals if x["candidate_id"] in survivor_ids]
    counts: dict[str, int] = {}
    for row in pooled:
        counts[row["ticker"]] = counts.get(row["ticker"], 0) + 1
    total = sum(counts.values())
    top = max(counts.values()) if counts else 0
    overfit.append({
        "scope": "SURVIVOR_POOL", "survivor": bool(survivor_ids),
        "survivor_entity_count": len(survivor_ids), "pooled_passed_count": total,
        "top_ticker_passed_share": top / total if total else 0.0,
        "ticker_hhi": sum((v / total) ** 2 for v in counts.values()) if total else 0.0,
        "ticker_concentration_flag": bool(total and top / total > 0.25),
    })
    write_csv(OUT_DIR / "overfit_check.csv", overfit)

    crs_id = "stage3:CRS:8695c9ce3320"
    crs_result: dict[str, Any] = {
        "candidate_id": crs_id,
        "signal_time_et": "2026-07-09T13:20:33.590054-04:00",
        "forensic_signal_price": 600.8599853515625,
        "feature_boundary": "sessions strictly before 2026-07-09",
        "actual_label_status": "NOT_STORED_SECOND_SESSION_NOT_COMPLETE_AS_OF_2026-07-12",
    }
    reg = registry.get(crs_id)
    features = extract_features(contexts.get("CRS", pd.DataFrame()), "2026-07-09") if "CRS" in contexts else None
    if reg and features:
        row, quantiles, champion = reg
        X = np.array([[features[name] for name in NUMERIC_FEATURES]], float)
        turn = np.array([features[BINARY_FEATURE]], int)
        crs_result.update(features)
        crs_result.update({
            "gene_available": True, "selector_pass": bool(individual_mask(champion, X, turn, quantiles)[0]),
            "survivor_entity": bool(row.get("survivor")), "gene": row.get("gene"),
            "status": "RECOVERED_SELECTOR_DECISION",
        })
    else:
        crs_result.update({"gene_available": bool(reg), "selector_pass": None, "status": "UNRECOVERABLE"})
    write_csv(OUT_DIR / "crs_filter_result.csv", [crs_result])

    summary = {
        "generated_at_unix": time.time(), "elapsed_sec": time.time() - started,
        "universe_entity_count": len(candidates), "dataset_signal_count": len(frame),
        "stress_signal_count": int((frame["regime"] == "stress").sum()),
        "train_signal_count": int((frame["regime"] == "train").sum()),
        "oos_signal_count": int((frame["regime"] == "oos").sum()),
        "trained_entity_count": sum(row.get("status") != "INSUFFICIENT_TRAIN_SIGNALS" for row in survivor_rows),
        "survivor_count": sum(bool(row.get("survivor")) for row in survivor_rows),
        "survivor_ids": sorted(survivor_ids), "data_error_count": len(errors),
        "target": {"horizon_sessions": HORIZON_SESSIONS, "return_threshold": TARGET_RETURN},
        "splits": {"stress": [STRESS_START, STRESS_END], "train": [TRAIN_START, TRAIN_END], "oos": [OOS_START, "latest stored"]},
        "ga": {"population": POPULATION, "generations": GENERATIONS, "elite_count": ELITE_COUNT, "patience": PATIENCE, "max_active_features": MAX_ACTIVE_FEATURES},
        "live_connected": False,
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
