#!/usr/bin/env python3
"""Leak-safe two-gene GA payoff detector.

Individual structure:
- Gene_UP: one multi-condition interval scorer for upside opportunity.
- Gene_LOW: one multi-condition interval scorer for downside safety.

Each gene learns:
- which feature to use
- quantile interval [q_low, q_high]
- inside/outside condition polarity
- feature weight
- score cut threshold

Final signal:
    UP_score >= up_cut AND LOW_score >= low_cut

Targets are used only for fitness/evaluation, never as features:
    GOOD_SIGNAL = next_high_atr >= good_high_atr AND next_low_atr <= good_max_low_atr
    BAD_RISK    = next_low_atr >= bad_low_atr
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_ROOT / "scripts/research/run_range_predictor_stage2_v3.py"
PERIODS = [
    ("stress", "2020-07-01", "2022-06-30"),
    ("train1", "2022-07-01", "2023-06-30"),
    ("train2", "2023-07-01", "2024-06-30"),
    ("train3", "2024-07-01", "2025-06-30"),
    ("oos", "2025-07-01", "2026-06-30"),
]
TRAIN_PERIODS = ["stress", "train1", "train2", "train3"]
TARGET_COLUMNS = {
    "date",
    "year",
    "high_pct_label",
    "low_mag_pct_label",
    "high_bin",
    "low_bin",
    "next_high_atr",
    "next_low_atr",
    "PAYOFF_SCORE",
    "GOOD_SIGNAL",
    "BAD_RISK",
}
LEAK_TOKENS = (
    "next_high_atr",
    "next_low_atr",
    "payoff",
    "good_signal",
    "good_long",
    "bad_risk",
)


@dataclasses.dataclass
class Condition:
    feature_idx: int
    q_low: float
    q_high: float
    weight: float
    inside: bool = True


@dataclasses.dataclass
class Gene:
    conditions: list[Condition]
    cut: float


@dataclasses.dataclass
class Individual:
    up: Gene
    low: Gene
    fitness: float = -1e18
    train_metrics: dict[str, Any] | None = None


def load_runner():
    spec = importlib.util.spec_from_file_location("range_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load runner: {RUNNER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["range_runner"] = mod
    spec.loader.exec_module(mod)
    return mod


def add_targets(df: pd.DataFrame, good_high_atr: float, good_max_low_atr: float, bad_low_atr: float) -> pd.DataFrame:
    out = df.copy()
    atr = out["D1_ATR_pct"].astype(float).clip(lower=1e-9)
    out["next_high_atr"] = out["high_pct_label"].astype(float) / atr
    out["next_low_atr"] = out["low_mag_pct_label"].astype(float) / atr
    out["PAYOFF_SCORE"] = out["next_high_atr"] - out["next_low_atr"]
    out["GOOD_SIGNAL"] = ((out["next_high_atr"] >= good_high_atr) & (out["next_low_atr"] <= good_max_low_atr)).astype(int)
    out["BAD_RISK"] = (out["next_low_atr"] >= bad_low_atr).astype(int)
    return out


def safe_features(raw_features: Iterable[str], df: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    features: list[str] = []
    excluded: list[str] = []
    suspicious: list[str] = []
    for f in raw_features:
        fl = str(f).lower()
        if f in TARGET_COLUMNS or any(tok in fl for tok in LEAK_TOKENS):
            excluded.append(str(f))
            if f in df.columns:
                suspicious.append(str(f))
            continue
        if f not in df.columns:
            excluded.append(str(f))
            continue
        if pd.api.types.is_numeric_dtype(df[f]):
            features.append(str(f))
        else:
            excluded.append(str(f))
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


def period_frames(runner: Any, data: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {name: runner.period_frame_checked(data, start, end, name).copy() for name, start, end in PERIODS}


def quantile_matrix(data: pd.DataFrame, train_mask: np.ndarray, features: list[str]) -> np.ndarray:
    x = data[features].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    arr = x.to_numpy(dtype=float)
    out = np.full(arr.shape, 0.5, dtype=np.float32)
    train_arr = arr[train_mask]
    for j in range(arr.shape[1]):
        ref = train_arr[:, j]
        ref = ref[np.isfinite(ref)]
        if len(ref) < 20:
            continue
        ref.sort()
        vals = arr[:, j]
        ok = np.isfinite(vals)
        if not np.any(ok):
            continue
        out[ok, j] = np.searchsorted(ref, vals[ok], side="right") / max(1, len(ref))
    return out


def index_map(data: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> dict[str, np.ndarray]:
    date_to_idx = {str(d)[:10]: i for i, d in enumerate(data["date"].tolist())}
    return {name: np.asarray([date_to_idx[str(d)[:10]] for d in df["date"].tolist()], dtype=int) for name, df in frames.items()}


def rand_condition(rng: random.Random, n_features: int) -> Condition:
    lo = rng.uniform(0.0, 0.82)
    width = rng.uniform(0.08, 0.42)
    hi = min(1.0, lo + width)
    if hi - lo < 0.05:
        hi = min(1.0, lo + 0.05)
    return Condition(
        feature_idx=rng.randrange(n_features),
        q_low=float(lo),
        q_high=float(hi),
        weight=float(rng.uniform(0.10, 1.25)),
        inside=bool(rng.random() >= 0.15),
    )


def rand_gene(rng: random.Random, n_features: int, n_conditions: int) -> Gene:
    return Gene(
        conditions=[rand_condition(rng, n_features) for _ in range(n_conditions)],
        cut=float(rng.uniform(0.28, 0.72)),
    )


def rand_individual(rng: random.Random, n_features: int, n_conditions: int) -> Individual:
    return Individual(up=rand_gene(rng, n_features, n_conditions), low=rand_gene(rng, n_features, n_conditions))


def gene_score(gene: Gene, qmat: np.ndarray, rows: np.ndarray) -> np.ndarray:
    q = qmat[rows]
    score = np.zeros(len(rows), dtype=np.float32)
    denom = 0.0
    for cond in gene.conditions:
        vals = q[:, cond.feature_idx]
        inside = (vals >= cond.q_low) & (vals <= cond.q_high)
        match = inside if cond.inside else ~inside
        w = max(0.0, float(cond.weight))
        if w <= 0:
            continue
        score += match.astype(np.float32) * w
        denom += w
    if denom <= 1e-12:
        return score
    return score / denom


def signal(ind: Individual, qmat: np.ndarray, rows: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    up_score = gene_score(ind.up, qmat, rows)
    low_score = gene_score(ind.low, qmat, rows)
    sig = (up_score >= ind.up.cut) & (low_score >= ind.low.cut)
    return sig, up_score, low_score


def eval_period(ind: Individual, qmat: np.ndarray, rows: np.ndarray, data: pd.DataFrame) -> dict[str, Any]:
    sig, up_score, low_score = signal(ind, qmat, rows)
    s = int(sig.sum())
    n = int(len(rows))
    sub_idx = rows[sig]
    base_good = float(data.iloc[rows]["GOOD_SIGNAL"].mean() * 100.0)
    base_bad = float(data.iloc[rows]["BAD_RISK"].mean() * 100.0)
    if s <= 0:
        return {
            "period_days": n,
            "signal_count": 0,
            "coverage_pct": 0.0,
            "good_hits": 0,
            "precision_pct": 0.0,
            "bad_hits": 0,
            "bad_rate_pct": 0.0,
            "base_good_pct": base_good,
            "base_bad_pct": base_bad,
            "avg_high_atr": 0.0,
            "avg_low_atr": 0.0,
            "avg_payoff_atr": 0.0,
            "avg_up_score": 0.0,
            "avg_low_score": 0.0,
            "dates": [],
        }
    sub = data.iloc[sub_idx]
    good = sub["GOOD_SIGNAL"].astype(bool).to_numpy()
    bad = sub["BAD_RISK"].astype(bool).to_numpy()
    return {
        "period_days": n,
        "signal_count": s,
        "coverage_pct": float(s / max(1, n) * 100.0),
        "good_hits": int(good.sum()),
        "precision_pct": float(good.mean() * 100.0),
        "bad_hits": int(bad.sum()),
        "bad_rate_pct": float(bad.mean() * 100.0),
        "base_good_pct": base_good,
        "base_bad_pct": base_bad,
        "avg_high_atr": float(sub["next_high_atr"].mean()),
        "avg_low_atr": float(sub["next_low_atr"].mean()),
        "avg_payoff_atr": float(sub["PAYOFF_SCORE"].mean()),
        "avg_up_score": float(up_score[sig].mean()),
        "avg_low_score": float(low_score[sig].mean()),
        "dates": [str(x)[:10] for x in sub["date"].tolist()],
    }


def fitness_from_metrics(metrics: dict[str, dict[str, Any]], args: argparse.Namespace) -> float:
    vals = [metrics[p] for p in TRAIN_PERIODS]
    counts = np.asarray([m["signal_count"] for m in vals], dtype=float)
    precisions = np.asarray([m["precision_pct"] for m in vals], dtype=float)
    bad_rates = np.asarray([m["bad_rate_pct"] for m in vals], dtype=float)
    bad_counts = np.asarray([m["bad_hits"] for m in vals], dtype=float)
    coverages = np.asarray([m["coverage_pct"] for m in vals], dtype=float)
    payoffs = np.asarray([m["avg_payoff_atr"] for m in vals], dtype=float)
    if counts.sum() <= 0:
        return -1e9
    mean_precision = float(precisions.mean())
    min_precision = float(precisions.min())
    mean_bad = float(bad_rates.mean())
    max_bad = float(bad_rates.max())
    mean_payoff = float(payoffs.mean())
    mean_coverage = float(coverages.mean())
    instability = float(np.std(precisions))
    shortage = float(np.maximum(0.0, args.min_signal_count - counts).sum())
    coverage_low = float(np.maximum(0.0, args.min_coverage_pct - coverages).sum())
    coverage_high = float(np.maximum(0.0, coverages - args.max_coverage_pct).sum())
    return float(
        mean_precision * 2.0
        + min_precision * 0.80
        + mean_payoff * 20.0
        + mean_coverage * 0.10
        - mean_bad * 3.0
        - max_bad * 1.2
        - bad_counts.sum() * 15.0
        - shortage * 12.0
        - coverage_low * 4.0
        - coverage_high * 2.0
        - instability * 0.35
    )


def eval_individual(ind: Individual, qmat: np.ndarray, period_rows: dict[str, np.ndarray], data: pd.DataFrame, args: argparse.Namespace) -> float:
    metrics = {name: eval_period(ind, qmat, rows, data) for name, rows in period_rows.items() if name in TRAIN_PERIODS}
    fit = fitness_from_metrics(metrics, args)
    ind.fitness = fit
    ind.train_metrics = metrics
    return fit


def mutate_condition(cond: Condition, rng: random.Random, n_features: int, rate: float) -> Condition:
    c = dataclasses.replace(cond)
    if rng.random() < rate:
        c.feature_idx = rng.randrange(n_features)
    if rng.random() < rate:
        c.q_low += rng.gauss(0.0, 0.08)
    if rng.random() < rate:
        c.q_high += rng.gauss(0.0, 0.08)
    if rng.random() < rate:
        width = max(0.05, c.q_high - c.q_low + rng.gauss(0.0, 0.06))
        center = (c.q_low + c.q_high) / 2.0 + rng.gauss(0.0, 0.04)
        c.q_low = center - width / 2.0
        c.q_high = center + width / 2.0
    c.q_low = float(min(0.95, max(0.0, c.q_low)))
    c.q_high = float(min(1.0, max(c.q_low + 0.03, c.q_high)))
    if rng.random() < rate:
        c.weight = float(min(2.0, max(0.02, c.weight * math.exp(rng.gauss(0.0, 0.35)))))
    if rng.random() < rate * 0.35:
        c.inside = not c.inside
    return c


def mutate_gene(gene: Gene, rng: random.Random, n_features: int, rate: float) -> Gene:
    conditions = [mutate_condition(c, rng, n_features, rate) for c in gene.conditions]
    if rng.random() < rate * 0.50:
        idx = rng.randrange(len(conditions))
        conditions[idx] = rand_condition(rng, n_features)
    cut = gene.cut
    if rng.random() < rate:
        cut += rng.gauss(0.0, 0.06)
    cut = float(min(0.90, max(0.10, cut)))
    return Gene(conditions=conditions, cut=cut)


def mutate(ind: Individual, rng: random.Random, n_features: int, rate: float) -> Individual:
    return Individual(
        up=mutate_gene(ind.up, rng, n_features, rate),
        low=mutate_gene(ind.low, rng, n_features, rate),
    )


def crossover_gene(a: Gene, b: Gene, rng: random.Random) -> Gene:
    conds = []
    for ca, cb in zip(a.conditions, b.conditions):
        chosen = ca if rng.random() < 0.5 else cb
        conds.append(dataclasses.replace(chosen))
    cut = (a.cut + b.cut) / 2.0 if rng.random() < 0.5 else (a.cut if rng.random() < 0.5 else b.cut)
    return Gene(conditions=conds, cut=float(cut))


def crossover(a: Individual, b: Individual, rng: random.Random) -> Individual:
    return Individual(up=crossover_gene(a.up, b.up, rng), low=crossover_gene(a.low, b.low, rng))


def tournament(pop: list[Individual], rng: random.Random, k: int = 4) -> Individual:
    cand = rng.sample(pop, min(k, len(pop)))
    return max(cand, key=lambda x: x.fitness)


def passes_train_gates(metrics: dict[str, dict[str, Any]], args: argparse.Namespace) -> tuple[bool, list[dict[str, Any]]]:
    reasons: list[dict[str, Any]] = []
    vals = [metrics[p] for p in TRAIN_PERIODS]
    for name in TRAIN_PERIODS:
        m = metrics[name]
        checks = [
            ("signal_count", m["signal_count"], args.min_signal_count, ">="),
            ("precision_pct", m["precision_pct"], args.min_precision_pct, ">="),
            ("bad_rate_pct", m["bad_rate_pct"], args.max_bad_rate_pct, "<="),
            ("coverage_pct", m["coverage_pct"], args.max_coverage_pct, "<="),
        ]
        for metric, value, threshold, rule in checks:
            fail = (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold)
            if fail:
                reasons.append({"period": name, "metric": metric, "value": value, "threshold": threshold, "rule": rule})
    mean_precision = float(np.mean([m["precision_pct"] for m in vals]))
    mean_bad = float(np.mean([m["bad_rate_pct"] for m in vals]))
    if mean_precision < args.min_mean_precision_pct:
        reasons.append({"period": "train_mean", "metric": "precision_pct", "value": mean_precision, "threshold": args.min_mean_precision_pct, "rule": ">="})
    if mean_bad > args.max_mean_bad_rate_pct:
        reasons.append({"period": "train_mean", "metric": "bad_rate_pct", "value": mean_bad, "threshold": args.max_mean_bad_rate_pct, "rule": "<="})
    return len(reasons) == 0, reasons


def condition_to_dict(cond: Condition, features: list[str]) -> dict[str, Any]:
    return {
        "feature": features[cond.feature_idx],
        "feature_idx": int(cond.feature_idx),
        "q_low": round(float(cond.q_low), 4),
        "q_high": round(float(cond.q_high), 4),
        "weight": round(float(cond.weight), 6),
        "mode": "inside" if cond.inside else "outside",
    }


def individual_to_dict(ind: Individual, features: list[str], qmat: np.ndarray, period_rows: dict[str, np.ndarray], data: pd.DataFrame, args: argparse.Namespace) -> dict[str, Any]:
    metrics = {name: eval_period(ind, qmat, rows, data) for name, rows in period_rows.items()}
    train_ok, fail_reasons = passes_train_gates({p: metrics[p] for p in TRAIN_PERIODS}, args)
    payload = {
        "up_cut": round(float(ind.up.cut), 6),
        "low_cut": round(float(ind.low.cut), 6),
        "up_conditions": [condition_to_dict(c, features) for c in ind.up.conditions],
        "low_conditions": [condition_to_dict(c, features) for c in ind.low.conditions],
    }
    sig = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return {
        "signature": sig,
        "fitness": float(ind.fitness),
        "train_gate_pass": bool(train_ok),
        "train_fail_reasons": fail_reasons[:20],
        "gene_count": 2,
        "up_gene": {"cut": payload["up_cut"], "conditions": payload["up_conditions"]},
        "low_gene": {"cut": payload["low_cut"], "conditions": payload["low_conditions"]},
        "metrics": metrics,
    }


def strip_dates(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_dates(v) for k, v in obj.items() if k != "dates"}
    if isinstance(obj, list):
        return [strip_dates(x) for x in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="FIX")
    ap.add_argument("--good-high-atr", type=float, default=1.0)
    ap.add_argument("--good-max-low-atr", type=float, default=0.7)
    ap.add_argument("--bad-low-atr", type=float, default=1.0)
    ap.add_argument("--population", type=int, default=220)
    ap.add_argument("--generations", type=int, default=80)
    ap.add_argument("--conditions-per-gene", type=int, default=24)
    ap.add_argument("--elite-frac", type=float, default=0.16)
    ap.add_argument("--mutation-rate", type=float, default=0.12)
    ap.add_argument("--min-signal-count", type=int, default=5)
    ap.add_argument("--min-coverage-pct", type=float, default=2.0)
    ap.add_argument("--max-coverage-pct", type=float, default=20.0)
    ap.add_argument("--min-precision-pct", type=float, default=45.0)
    ap.add_argument("--min-mean-precision-pct", type=float, default=55.0)
    ap.add_argument("--max-bad-rate-pct", type=float, default=15.0)
    ap.add_argument("--max-mean-bad-rate-pct", type=float, default=10.0)
    ap.add_argument("--survivor-top-n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260706)
    ap.add_argument("--out-dir", default="exp_fix_payoff_two_gene_ga_20260706_001")
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    np.random.seed(args.seed % (2**32 - 1))

    runner = load_runner()
    raw, _meta = runner.L.build_dataset(args.ticker)
    raw_features = [f for f in runner.L.feature_columns(raw) if f in raw.columns]
    data = add_targets(raw, args.good_high_atr, args.good_max_low_atr, args.bad_low_atr)
    features, audit = safe_features(raw_features, data)
    frames = period_frames(runner, data)
    rows_by_period = index_map(data, frames)
    train_mask = np.zeros(len(data), dtype=bool)
    for name in TRAIN_PERIODS:
        train_mask[rows_by_period[name]] = True
    qmat = quantile_matrix(data, train_mask, features)

    pop = [rand_individual(rng, len(features), args.conditions_per_gene) for _ in range(args.population)]
    trace = []
    elite_n = max(2, int(args.population * args.elite_frac))
    for gen in range(args.generations):
        for ind in pop:
            eval_individual(ind, qmat, rows_by_period, data, args)
        pop.sort(key=lambda x: x.fitness, reverse=True)
        best = pop[0]
        pass_count = 0
        for ind in pop[: min(len(pop), args.survivor_top_n)]:
            ok, _ = passes_train_gates(ind.train_metrics or {}, args)
            pass_count += int(ok)
        if gen == 0 or (gen + 1) % 10 == 0 or gen == args.generations - 1:
            trace.append(
                {
                    "generation": gen + 1,
                    "best_fitness": float(best.fitness),
                    "median_fitness": float(np.median([x.fitness for x in pop])),
                    "top_train_pass_count": int(pass_count),
                    "best_train_metrics": strip_dates(best.train_metrics or {}),
                }
            )
            print(json.dumps(trace[-1], ensure_ascii=False), flush=True)
        elites = pop[:elite_n]
        new_pop = [dataclasses.replace(e, up=dataclasses.replace(e.up, conditions=[dataclasses.replace(c) for c in e.up.conditions]), low=dataclasses.replace(e.low, conditions=[dataclasses.replace(c) for c in e.low.conditions])) for e in elites]
        while len(new_pop) < args.population:
            if rng.random() < 0.75:
                child = crossover(tournament(pop, rng), tournament(pop, rng), rng)
            else:
                child = dataclasses.replace(tournament(pop, rng), up=dataclasses.replace(tournament(pop, rng).up), low=dataclasses.replace(tournament(pop, rng).low))
            child = mutate(child, rng, len(features), args.mutation_rate)
            new_pop.append(child)
        pop = new_pop

    for ind in pop:
        eval_individual(ind, qmat, rows_by_period, data, args)
    pop.sort(key=lambda x: x.fitness, reverse=True)
    rows = [individual_to_dict(ind, features, qmat, rows_by_period, data, args) for ind in pop[: args.survivor_top_n]]
    train_survivors = [r for r in rows if r["train_gate_pass"]]
    oos_survivors = [
        r
        for r in train_survivors
        if r["metrics"]["oos"]["signal_count"] >= 5
        and r["metrics"]["oos"]["precision_pct"] >= 55.0
        and r["metrics"]["oos"]["bad_rate_pct"] <= 10.0
        and r["metrics"]["oos"]["avg_payoff_atr"] > 0.5
    ]

    out_dir = PROJECT_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "candidates_top.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "train_survivors.jsonl").open("w", encoding="utf-8") as f:
        for r in train_survivors:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "ticker": args.ticker,
        "target": {
            "GOOD_SIGNAL": f"next_high_atr >= {args.good_high_atr} and next_low_atr <= {args.good_max_low_atr}",
            "BAD_RISK": f"next_low_atr >= {args.bad_low_atr}",
        },
        "feature_audit": audit,
        "feature_count": len(features),
        "rows": int(len(data)),
        "periods": {name: {"rows": int(len(frames[name])), "good_base_pct": float(frames[name]["GOOD_SIGNAL"].mean() * 100.0), "bad_base_pct": float(frames[name]["BAD_RISK"].mean() * 100.0)} for name in frames},
        "ga": {
            "population": args.population,
            "generations": args.generations,
            "gene_count": 2,
            "conditions_per_gene": args.conditions_per_gene,
            "mutation_rate": args.mutation_rate,
            "seed": args.seed,
        },
        "gates": {
            "min_signal_count": args.min_signal_count,
            "min_coverage_pct": args.min_coverage_pct,
            "max_coverage_pct": args.max_coverage_pct,
            "min_precision_pct": args.min_precision_pct,
            "min_mean_precision_pct": args.min_mean_precision_pct,
            "max_bad_rate_pct": args.max_bad_rate_pct,
            "max_mean_bad_rate_pct": args.max_mean_bad_rate_pct,
        },
        "trace": trace,
        "top_count": len(rows),
        "train_survivor_count": len(train_survivors),
        "oos_survivor_count": len(oos_survivors),
        "best": rows[0] if rows else None,
        "train_survivors_preview": train_survivors[:10],
        "oos_survivors_preview": oos_survivors[:10],
        "outputs": {
            "summary": str(out_dir / "summary.json"),
            "candidates_top": str(out_dir / "candidates_top.jsonl"),
            "train_survivors": str(out_dir / "train_survivors.jsonl"),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "out_dir": str(out_dir),
        "feature_count": len(features),
        "train_survivor_count": len(train_survivors),
        "oos_survivor_count": len(oos_survivors),
        "best": strip_dates(rows[0]) if rows else None,
    }, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
