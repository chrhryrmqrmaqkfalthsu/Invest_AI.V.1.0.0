#!/usr/bin/env python3
"""구간별 100개 생성 후 다른 구간 생존 평가를 하는 2유전자 payoff GA wrapper."""
from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_ga.py"
TRAIN_PERIODS = ["stress", "train1", "train2", "train3"]


def load_base():
    spec = importlib.util.spec_from_file_location("two_gene_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["two_gene_base"] = mod
    spec.loader.exec_module(mod)
    return mod


def clone_ind(base: Any, ind: Any) -> Any:
    return base.Individual(
        up=base.Gene([dataclasses.replace(c) for c in ind.up.conditions], float(ind.up.cut)),
        low=base.Gene([dataclasses.replace(c) for c in ind.low.conditions], float(ind.low.cut)),
        fitness=float(getattr(ind, "fitness", -1e18)),
        train_metrics=getattr(ind, "train_metrics", None),
    )


def source_fitness(m: dict[str, Any], args: argparse.Namespace) -> float:
    count = float(m.get("signal_count", 0.0))
    if count <= 0:
        return -1e9
    shortage = max(0.0, args.min_signal_count - count)
    cover_low = max(0.0, args.min_coverage_pct - float(m.get("coverage_pct", 0.0)))
    cover_high = max(0.0, float(m.get("coverage_pct", 0.0)) - args.max_coverage_pct)
    return float(
        float(m.get("precision_pct", 0.0)) * 3.0
        + float(m.get("avg_payoff_atr", 0.0)) * 22.0
        + count * 0.25
        - float(m.get("bad_rate_pct", 0.0)) * 4.0
        - float(m.get("bad_hits", 0.0)) * 18.0
        - shortage * 18.0
        - cover_low * 5.0
        - cover_high * 2.5
    )


def eval_on_source(base: Any, ind: Any, qmat: np.ndarray, period_rows: dict[str, np.ndarray], data: Any, source: str, args: argparse.Namespace) -> float:
    m = base.eval_period(ind, qmat, period_rows[source], data)
    ind.fitness = source_fitness(m, args)
    ind.train_metrics = {source: m}
    return ind.fitness


def evolve_one_period(base: Any, source: str, rng: random.Random, qmat: np.ndarray, period_rows: dict[str, np.ndarray], data: Any, n_features: int, args: argparse.Namespace) -> tuple[list[Any], list[dict[str, Any]]]:
    pop = [base.rand_individual(rng, n_features, args.conditions_per_gene) for _ in range(args.per_period_count)]
    elite_n = max(2, int(args.per_period_count * args.elite_frac))
    trace: list[dict[str, Any]] = []
    for gen in range(args.generations):
        for ind in pop:
            eval_on_source(base, ind, qmat, period_rows, data, source, args)
        pop.sort(key=lambda x: x.fitness, reverse=True)
        if gen == 0 or (gen + 1) % 10 == 0 or gen == args.generations - 1:
            best_m = base.eval_period(pop[0], qmat, period_rows[source], data)
            trace.append({
                "생성구간": source,
                "세대": gen + 1,
                "최고진화점수": float(pop[0].fitness),
                "중앙진화점수": float(np.median([x.fitness for x in pop])),
                "최고개체_신호발생일": best_m["signal_count"],
                "최고개체_적중률": best_m["precision_pct"],
                "최고개체_위험발생률": best_m["bad_rate_pct"],
                "최고개체_평균보상폭": best_m["avg_payoff_score"] if "avg_payoff_score" in best_m else best_m.get("avg_payoff_atr", 0.0),
            })
            print(json.dumps(trace[-1], ensure_ascii=False), flush=True)
        elites = [clone_ind(base, e) for e in pop[:elite_n]]
        new_pop = elites
        while len(new_pop) < args.per_period_count:
            if rng.random() < 0.75:
                child = base.crossover(base.tournament(pop, rng), base.tournament(pop, rng), rng)
            else:
                child = clone_ind(base, base.tournament(pop, rng))
            new_pop.append(base.mutate(child, rng, n_features, args.mutation_rate))
        pop = new_pop
    for ind in pop:
        eval_on_source(base, ind, qmat, period_rows, data, source, args)
    pop.sort(key=lambda x: x.fitness, reverse=True)
    return [clone_ind(base, x) for x in pop[:args.per_period_count]], trace


def metric_ko(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "구간일수": m.get("period_days"),
        "신호발생일": m.get("signal_count"),
        "신호발생비율": m.get("coverage_pct"),
        "목표달성일": m.get("good_hits"),
        "적중률": m.get("precision_pct"),
        "위험발생일": m.get("bad_hits"),
        "위험발생률": m.get("bad_rate_pct"),
        "전체목표발생률": m.get("base_good_pct"),
        "전체위험발생률": m.get("base_bad_pct"),
        "평균상방폭": m.get("avg_high_atr"),
        "평균하방폭": m.get("avg_low_atr"),
        "평균보상폭": m.get("avg_payoff_score", m.get("avg_payoff_atr")),
        "날짜": m.get("dates", []),
    }


def train_gate(metrics: dict[str, dict[str, Any]], args: argparse.Namespace) -> tuple[bool, list[dict[str, Any]]]:
    reasons = []
    for p in TRAIN_PERIODS:
        m = metrics[p]
        checks = [
            ("신호발생일", m["signal_count"], args.min_signal_count, ">="),
            ("적중률", m["precision_pct"], args.min_precision_pct, ">="),
            ("위험발생률", m["bad_rate_pct"], args.max_bad_rate_pct, "<="),
            ("신호발생비율", m["coverage_pct"], args.max_coverage_pct, "<="),
        ]
        for name, value, threshold, rule in checks:
            failed = (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold)
            if failed:
                reasons.append({"구간": p, "항목": name, "값": value, "기준": threshold, "조건": rule})
    mean_precision = float(np.mean([metrics[p]["precision_pct"] for p in TRAIN_PERIODS]))
    mean_bad = float(np.mean([metrics[p]["bad_rate_pct"] for p in TRAIN_PERIODS]))
    if mean_precision < args.min_mean_precision_pct:
        reasons.append({"구간": "학습구간평균", "항목": "적중률", "값": mean_precision, "기준": args.min_mean_precision_pct, "조건": ">="})
    if mean_bad > args.max_mean_bad_rate_pct:
        reasons.append({"구간": "학습구간평균", "항목": "위험발생률", "값": mean_bad, "기준": args.max_mean_bad_rate_pct, "조건": "<="})
    return len(reasons) == 0, reasons


def row_for(base: Any, ind: Any, source: str, features: list[str], qmat: np.ndarray, period_rows: dict[str, np.ndarray], data: Any, args: argparse.Namespace) -> dict[str, Any]:
    metrics_raw = {p: base.eval_period(ind, qmat, rows, data) for p, rows in period_rows.items()}
    ok, reasons = train_gate({p: metrics_raw[p] for p in TRAIN_PERIODS}, args)
    old = base.individual_to_dict(ind, features, qmat, period_rows, data, args)
    return {
        "개체서명": old["signature"],
        "생성구간": source,
        "생성구간진화점수": float(ind.fitness),
        "학습전체통과": ok,
        "탈락사유": reasons[:30],
        "상방유전자": old["up_gene"],
        "하방유전자": old["low_gene"],
        "구간별성능": {p: metric_ko(m) for p, m in metrics_raw.items()},
    }


def no_dates(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: no_dates(v) for k, v in obj.items() if k != "날짜" and k != "dates"}
    if isinstance(obj, list):
        return [no_dates(x) for x in obj]
    return obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="FIX")
    ap.add_argument("--good-high-atr", type=float, default=1.0)
    ap.add_argument("--good-max-low-atr", type=float, default=0.7)
    ap.add_argument("--bad-low-atr", type=float, default=1.0)
    ap.add_argument("--per-period-count", type=int, default=100)
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
    ap.add_argument("--seed", type=int, default=20260706)
    ap.add_argument("--out-dir", default="exp_fix_payoff_two_gene_stage2_rolling_ga_20260706_001")
    args = ap.parse_args(argv)

    base = load_base()
    rng = random.Random(args.seed)
    runner = base.load_runner()
    raw, _ = runner.L.build_dataset(args.ticker)
    raw_features = [f for f in runner.L.feature_columns(raw) if f in raw.columns]
    data = base.add_targets(raw, args.good_high_atr, args.good_max_low_atr, args.bad_low_atr)
    features, audit = base.safe_features(raw_features, data)
    frames = base.period_frames(runner, data)
    period_rows = base.index_map(data, frames)
    train_mask = np.zeros(len(data), dtype=bool)
    for p in TRAIN_PERIODS:
        train_mask[period_rows[p]] = True
    qmat = base.quantile_matrix(data, train_mask, features)

    all_candidates = []
    trace = []
    generated_by_period = {}
    for source in TRAIN_PERIODS:
        candidates, tr = evolve_one_period(base, source, rng, qmat, period_rows, data, len(features), args)
        generated_by_period[source] = len(candidates)
        all_candidates.extend([(source, x) for x in candidates])
        trace.extend(tr)

    dedup = {}
    for source, ind in all_candidates:
        sig = base.individual_to_dict(ind, features, qmat, period_rows, data, args)["signature"]
        if sig not in dedup or ind.fitness > dedup[sig][1].fitness:
            dedup[sig] = (source, ind)

    rows = [row_for(base, ind, source, features, qmat, period_rows, data, args) for source, ind in dedup.values()]
    rows.sort(key=lambda r: (
        int(r["학습전체통과"]),
        np.mean([r["구간별성능"][p]["적중률"] for p in TRAIN_PERIODS]),
        -np.mean([r["구간별성능"][p]["위험발생률"] for p in TRAIN_PERIODS]),
        r["생성구간진화점수"],
    ), reverse=True)
    train_survivors = [r for r in rows if r["학습전체통과"]]
    oos_survivors = [r for r in train_survivors if r["구간별성능"]["oos"]["신호발생일"] >= 5 and r["구간별성능"]["oos"]["적중률"] >= 55.0 and r["구간별성능"]["oos"]["위험발생률"] <= 10.0 and r["구간별성능"]["oos"]["평균보상폭"] > 0.5]

    out_dir = PROJECT_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "all_candidates.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "train_survivors.jsonl").open("w", encoding="utf-8") as f:
        for r in train_survivors:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "실험방식": "각 학습 구간에서 100개씩 따로 생성/진화한 뒤, 다른 학습 구간에서도 살아남는 개체만 생존 처리",
        "종목": args.ticker,
        "특징감사": audit,
        "파라미터수": len(features),
        "구간별전체기준": {p: {"일수": len(frames[p]), "전체목표발생률": float(frames[p]["GOOD_SIGNAL"].mean() * 100.0), "전체위험발생률": float(frames[p]["BAD_RISK"].mean() * 100.0)} for p in frames},
        "생성설정": {"구간별생성개체수": args.per_period_count, "생성구간": TRAIN_PERIODS, "세대수": args.generations, "유전자수": 2, "유전자별조건수": args.conditions_per_gene},
        "생성개체수_구간별": generated_by_period,
        "전체생성개체수": len(all_candidates),
        "중복제거후개체수": len(rows),
        "학습전체통과개체수": len(train_survivors),
        "검증구간까지통과개체수": len(oos_survivors),
        "진화기록": trace,
        "상위개체미리보기": [no_dates(r) for r in rows[:10]],
        "학습전체통과개체미리보기": [no_dates(r) for r in train_survivors[:10]],
        "검증구간통과개체미리보기": [no_dates(r) for r in oos_survivors[:10]],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "출력폴더": str(out_dir),
        "파라미터수": len(features),
        "생성개체수_구간별": generated_by_period,
        "전체생성개체수": len(all_candidates),
        "중복제거후개체수": len(rows),
        "학습전체통과개체수": len(train_survivors),
        "검증구간까지통과개체수": len(oos_survivors),
        "상위개체": no_dates(rows[0]) if rows else None,
    }, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
