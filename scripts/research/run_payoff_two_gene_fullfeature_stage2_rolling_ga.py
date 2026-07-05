#!/usr/bin/env python3
"""전체 파라미터를 모두 유전자로 쓰는 Stage2 rolling 2유전자 payoff GA.

핵심 차이:
- 400개 파라미터 중 일부만 조건으로 고르는 방식이 아니다.
- 상방 유전자와 하방 유전자가 각각 전체 파라미터 수만큼 슬롯을 가진다.
- 각 파라미터마다 GA가 아래를 직접 진화시킨다.
  1) 사용할지 말지
  2) 어느 분위수 구간을 조건으로 볼지
  3) 구간 안을 볼지 밖을 볼지
  4) 얼마나 반영할지

흐름:
- 개체 생성/진화: train1, train2, train3
- stress: 생성하지 않고 평가만
- oos: 마지막 확인
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = PROJECT_ROOT / "scripts/research/run_payoff_two_gene_ga.py"
GENERATION_PERIODS = ["train1", "train2", "train3"]
SURVIVAL_PERIODS = ["stress", "train1", "train2", "train3"]
IMPORTANT_PERIODS = ["stress", "oos"]
INVALID_FITNESS = -1e9


@dataclasses.dataclass
class FullGene:
    active: np.ndarray
    q_low: np.ndarray
    q_high: np.ndarray
    inside: np.ndarray
    weight: np.ndarray
    cut: float


@dataclasses.dataclass
class FullIndividual:
    up: FullGene
    low: FullGene
    fitness: float = INVALID_FITNESS


def load_base():
    spec = importlib.util.spec_from_file_location("two_gene_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {BASE_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["two_gene_base"] = mod
    spec.loader.exec_module(mod)
    return mod


def clone_gene(g: FullGene) -> FullGene:
    return FullGene(g.active.copy(), g.q_low.copy(), g.q_high.copy(), g.inside.copy(), g.weight.copy(), float(g.cut))


def clone(ind: FullIndividual) -> FullIndividual:
    return FullIndividual(clone_gene(ind.up), clone_gene(ind.low), float(ind.fitness))


def rand_gene(rng: random.Random, n: int, active_rate: float) -> FullGene:
    active = np.asarray([rng.random() < active_rate for _ in range(n)], dtype=bool)
    q_low = np.asarray([rng.uniform(0.0, 0.82) for _ in range(n)], dtype=np.float32)
    width = np.asarray([rng.uniform(0.08, 0.42) for _ in range(n)], dtype=np.float32)
    q_high = np.minimum(1.0, q_low + width).astype(np.float32)
    q_high = np.maximum(q_high, q_low + 0.03).astype(np.float32)
    inside = np.asarray([rng.random() >= 0.15 for _ in range(n)], dtype=bool)
    weight = np.asarray([rng.uniform(0.05, 1.25) for _ in range(n)], dtype=np.float32)
    weight[~active] = 0.0
    return FullGene(active, q_low, q_high, inside, weight, float(rng.uniform(0.28, 0.72)))


def rand_individual(rng: random.Random, n: int, active_rate: float) -> FullIndividual:
    return FullIndividual(rand_gene(rng, n, active_rate), rand_gene(rng, n, active_rate))


def gene_score(g: FullGene, qmat: np.ndarray, row_idx: np.ndarray) -> np.ndarray:
    weights = np.where(g.active, g.weight, 0.0).astype(np.float32)
    denom = float(weights.sum())
    if denom <= 1e-12:
        return np.zeros(len(row_idx), dtype=np.float32)
    q = qmat[row_idx]
    inside_match = (q >= g.q_low) & (q <= g.q_high)
    match = np.where(g.inside, inside_match, ~inside_match)
    return (match.astype(np.float32) @ weights) / denom


def signal(ind: FullIndividual, qmat: np.ndarray, row_idx: np.ndarray):
    up_score = gene_score(ind.up, qmat, row_idx)
    low_score = gene_score(ind.low, qmat, row_idx)
    sig = (up_score >= ind.up.cut) & (low_score >= ind.low.cut)
    return sig, up_score, low_score


def eval_period(ind: FullIndividual, qmat: np.ndarray, row_idx: np.ndarray, data: Any) -> dict[str, Any]:
    sig, up_score, low_score = signal(ind, qmat, row_idx)
    n = int(len(row_idx))
    s = int(sig.sum())
    period_df = data.iloc[row_idx]
    base_good = float(period_df["GOOD_SIGNAL"].mean() * 100.0)
    base_bad = float(period_df["BAD_RISK"].mean() * 100.0)
    if s <= 0:
        return {
            "구간일수": n, "신호발생일": 0, "신호발생비율": 0.0, "목표달성일": 0, "적중률": 0.0,
            "위험발생일": 0, "위험발생률": 0.0, "전체목표발생률": base_good, "전체위험발생률": base_bad,
            "평균상방폭": 0.0, "평균하방폭": 0.0, "평균보상폭": 0.0, "평균상방유전자점수": 0.0, "평균하방유전자점수": 0.0,
            "날짜": [],
        }
    selected = row_idx[sig]
    sub = data.iloc[selected]
    good = sub["GOOD_SIGNAL"].astype(bool).to_numpy()
    bad = sub["BAD_RISK"].astype(bool).to_numpy()
    return {
        "구간일수": n,
        "신호발생일": s,
        "신호발생비율": float(s / max(1, n) * 100.0),
        "목표달성일": int(good.sum()),
        "적중률": float(good.mean() * 100.0),
        "위험발생일": int(bad.sum()),
        "위험발생률": float(bad.mean() * 100.0),
        "전체목표발생률": base_good,
        "전체위험발생률": base_bad,
        "평균상방폭": float(sub["next_high_atr"].mean()),
        "평균하방폭": float(sub["next_low_atr"].mean()),
        "평균보상폭": float(sub["PAYOFF_SCORE"].mean()),
        "평균상방유전자점수": float(up_score[sig].mean()),
        "평균하방유전자점수": float(low_score[sig].mean()),
        "날짜": [str(x)[:10] for x in sub["date"].tolist()],
    }


def source_fitness(m: dict[str, Any], ind: FullIndividual, args: argparse.Namespace) -> float:
    if float(m["신호발생일"]) < args.min_signal_count:
        return INVALID_FITNESS
    if float(m["신호발생비율"]) < args.min_coverage_pct:
        return INVALID_FITNESS
    active_total = int(ind.up.active.sum() + ind.low.active.sum())
    too_many_penalty = max(0, active_total - args.max_active_total) * args.active_count_penalty
    cover_high = max(0.0, float(m["신호발생비율"]) - args.max_coverage_pct)
    return float(
        float(m["적중률"]) * 3.0
        + float(m["평균보상폭"]) * 22.0
        + float(m["신호발생일"]) * 0.25
        - float(m["위험발생률"]) * 4.0
        - float(m["위험발생일"]) * 18.0
        - cover_high * 2.5
        - too_many_penalty
    )


def eval_source(ind: FullIndividual, qmat: np.ndarray, rows: dict[str, np.ndarray], data: Any, period: str, args: argparse.Namespace) -> float:
    m = eval_period(ind, qmat, rows[period], data)
    ind.fitness = source_fitness(m, ind, args)
    return ind.fitness


def mutate_gene(g: FullGene, rng: random.Random, args: argparse.Namespace) -> FullGene:
    out = clone_gene(g)
    n = len(out.active)
    k = max(1, int(n * args.feature_mutation_frac))
    idxs = np.asarray(rng.sample(range(n), k=min(k, n)), dtype=int)
    for idx in idxs:
        r = rng.random()
        if r < 0.20:
            out.active[idx] = not bool(out.active[idx])
            if out.active[idx] and out.weight[idx] <= 0:
                out.weight[idx] = rng.uniform(0.05, 1.0)
            if not out.active[idx]:
                out.weight[idx] = 0.0
        elif r < 0.45:
            out.weight[idx] = float(min(2.0, max(0.0, float(out.weight[idx]) + rng.gauss(0.0, 0.25))))
            out.active[idx] = out.weight[idx] > args.weight_active_floor
        elif r < 0.70:
            lo = float(out.q_low[idx] + rng.gauss(0.0, 0.08))
            hi = float(out.q_high[idx] + rng.gauss(0.0, 0.08))
            lo = min(0.95, max(0.0, lo))
            hi = min(1.0, max(lo + 0.03, hi))
            out.q_low[idx] = lo
            out.q_high[idx] = hi
        elif r < 0.86:
            width = max(0.03, float(out.q_high[idx] - out.q_low[idx]) + rng.gauss(0.0, 0.06))
            center = (float(out.q_low[idx]) + float(out.q_high[idx])) / 2 + rng.gauss(0.0, 0.05)
            lo = min(0.95, max(0.0, center - width / 2))
            hi = min(1.0, max(lo + 0.03, center + width / 2))
            out.q_low[idx] = lo
            out.q_high[idx] = hi
        else:
            out.inside[idx] = not bool(out.inside[idx])
    if rng.random() < args.mutation_rate:
        out.cut = float(min(0.92, max(0.08, out.cut + rng.gauss(0.0, 0.05))))
    return out


def mutate(ind: FullIndividual, rng: random.Random, args: argparse.Namespace) -> FullIndividual:
    return FullIndividual(mutate_gene(ind.up, rng, args), mutate_gene(ind.low, rng, args))


def crossover_gene(a: FullGene, b: FullGene, rng: random.Random) -> FullGene:
    n = len(a.active)
    mask = np.asarray([rng.random() < 0.5 for _ in range(n)], dtype=bool)
    active = np.where(mask, a.active, b.active).astype(bool)
    q_low = np.where(mask, a.q_low, b.q_low).astype(np.float32)
    q_high = np.where(mask, a.q_high, b.q_high).astype(np.float32)
    inside = np.where(mask, a.inside, b.inside).astype(bool)
    weight = np.where(mask, a.weight, b.weight).astype(np.float32)
    cut = (a.cut + b.cut) / 2.0 if rng.random() < 0.5 else (a.cut if rng.random() < 0.5 else b.cut)
    weight[~active] = 0.0
    return FullGene(active, q_low, q_high, inside, weight, float(cut))


def crossover(a: FullIndividual, b: FullIndividual, rng: random.Random) -> FullIndividual:
    return FullIndividual(crossover_gene(a.up, b.up, rng), crossover_gene(a.low, b.low, rng))


def tournament(pop: list[FullIndividual], rng: random.Random, k: int = 4) -> FullIndividual:
    return max(rng.sample(pop, min(k, len(pop))), key=lambda x: x.fitness)


def evolve_period(period: str, rng: random.Random, n_features: int, qmat: np.ndarray, rows: dict[str, np.ndarray], data: Any, args: argparse.Namespace):
    pop = [rand_individual(rng, n_features, args.initial_active_rate) for _ in range(args.per_period_count)]
    elite_n = max(2, int(args.per_period_count * args.elite_frac))
    trace = []
    for gen in range(args.generations):
        for ind in pop:
            eval_source(ind, qmat, rows, data, period, args)
        pop.sort(key=lambda x: x.fitness, reverse=True)
        valid = [x for x in pop if x.fitness > INVALID_FITNESS / 2]
        if gen == 0 or (gen + 1) % 10 == 0 or gen == args.generations - 1:
            m = eval_period(pop[0], qmat, rows[period], data)
            item = {
                "생성구간": period,
                "세대": gen + 1,
                "전체파라미터슬롯_유전자별": n_features,
                "최소신호발생일_미만무효": args.min_signal_count,
                "유효개체수": len(valid),
                "최고진화점수": float(pop[0].fitness),
                "중앙진화점수": float(np.median([x.fitness for x in pop])),
                "최고개체_상방사용파라미터수": int(pop[0].up.active.sum()),
                "최고개체_하방사용파라미터수": int(pop[0].low.active.sum()),
                "최고개체_신호발생일": m["신호발생일"],
                "최고개체_적중률": m["적중률"],
                "최고개체_위험발생률": m["위험발생률"],
                "최고개체_평균보상폭": m["평균보상폭"],
            }
            trace.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
        pool = valid if len(valid) >= 4 else pop
        new_pop = [clone(e) for e in pop[:elite_n]]
        while len(new_pop) < args.per_period_count:
            child = crossover(tournament(pool, rng), tournament(pool, rng), rng) if rng.random() < 0.75 else clone(tournament(pool, rng))
            new_pop.append(mutate(child, rng, args))
        pop = new_pop
    for ind in pop:
        eval_source(ind, qmat, rows, data, period, args)
    pop.sort(key=lambda x: x.fitness, reverse=True)
    return [clone(x) for x in pop[:args.per_period_count]], trace


def survival_gate(metrics: dict[str, dict[str, Any]], args: argparse.Namespace):
    reasons = []
    for p in SURVIVAL_PERIODS:
        m = metrics[p]
        checks = [
            ("신호발생일", m["신호발생일"], args.min_signal_count, ">="),
            ("적중률", m["적중률"], args.min_precision_pct, ">="),
            ("위험발생률", m["위험발생률"], args.max_bad_rate_pct, "<="),
            ("신호발생비율", m["신호발생비율"], args.max_coverage_pct, "<="),
        ]
        for name, value, threshold, rule in checks:
            if (rule == ">=" and value < threshold) or (rule == "<=" and value > threshold):
                reasons.append({"구간": p, "항목": name, "값": value, "기준": threshold, "조건": rule})
    mean_precision = float(np.mean([metrics[p]["적중률"] for p in SURVIVAL_PERIODS]))
    mean_bad = float(np.mean([metrics[p]["위험발생률"] for p in SURVIVAL_PERIODS]))
    if mean_precision < args.min_mean_precision_pct:
        reasons.append({"구간": "생존평가구간평균", "항목": "적중률", "값": mean_precision, "기준": args.min_mean_precision_pct, "조건": ">="})
    if mean_bad > args.max_mean_bad_rate_pct:
        reasons.append({"구간": "생존평가구간평균", "항목": "위험발생률", "값": mean_bad, "기준": args.max_mean_bad_rate_pct, "조건": "<="})
    return len(reasons) == 0, reasons


def gene_to_summary(g: FullGene, features: list[str], top_n: int = 60) -> dict[str, Any]:
    idx = np.where(g.active & (g.weight > 0))[0]
    idx = sorted(idx.tolist(), key=lambda i: float(g.weight[i]), reverse=True)
    return {
        "통과점수": round(float(g.cut), 6),
        "전체파라미터슬롯수": len(features),
        "사용파라미터수": len(idx),
        "미사용파라미터수": len(features) - len(idx),
        "상위사용파라미터": [
            {
                "파라미터": features[i],
                "사용여부": True,
                "반영강도": round(float(g.weight[i]), 6),
                "분위수하한": round(float(g.q_low[i]), 4),
                "분위수상한": round(float(g.q_high[i]), 4),
                "조건방식": "구간안" if bool(g.inside[i]) else "구간밖",
            }
            for i in idx[:top_n]
        ],
    }


def individual_signature(ind: FullIndividual) -> str:
    payload = {
        "up_cut": round(ind.up.cut, 6), "low_cut": round(ind.low.cut, 6),
        "up_active": ind.up.active.astype(int).tolist(), "low_active": ind.low.active.astype(int).tolist(),
        "up_weight": np.round(ind.up.weight, 4).tolist(), "low_weight": np.round(ind.low.weight, 4).tolist(),
        "up_lo": np.round(ind.up.q_low, 4).tolist(), "up_hi": np.round(ind.up.q_high, 4).tolist(),
        "low_lo": np.round(ind.low.q_low, 4).tolist(), "low_hi": np.round(ind.low.q_high, 4).tolist(),
        "up_inside": ind.up.inside.astype(int).tolist(), "low_inside": ind.low.inside.astype(int).tolist(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def no_dates(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: no_dates(v) for k, v in obj.items() if k not in {"날짜", "dates"}}
    if isinstance(obj, list):
        return [no_dates(x) for x in obj]
    return obj


def make_row(ind: FullIndividual, source: str, features: list[str], qmat: np.ndarray, rows: dict[str, np.ndarray], data: Any, args: argparse.Namespace) -> dict[str, Any]:
    metrics = {p: eval_period(ind, qmat, r, data) for p, r in rows.items()}
    ok, reasons = survival_gate({p: metrics[p] for p in SURVIVAL_PERIODS}, args)
    return {
        "개체서명": individual_signature(ind),
        "생성구간": source,
        "생성구간진화점수": float(ind.fitness),
        "생존평가전체통과": ok,
        "탈락사유": reasons[:30],
        "전체파라미터유전자화": True,
        "상방유전자": gene_to_summary(ind.up, features),
        "하방유전자": gene_to_summary(ind.low, features),
        "구간별성능": metrics,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="MPC")
    ap.add_argument("--good-high-atr", type=float, default=1.0)
    ap.add_argument("--good-max-low-atr", type=float, default=0.7)
    ap.add_argument("--bad-low-atr", type=float, default=1.0)
    ap.add_argument("--per-period-count", type=int, default=100)
    ap.add_argument("--generations", type=int, default=80)
    ap.add_argument("--elite-frac", type=float, default=0.16)
    ap.add_argument("--mutation-rate", type=float, default=0.12)
    ap.add_argument("--feature-mutation-frac", type=float, default=0.04)
    ap.add_argument("--initial-active-rate", type=float, default=0.18)
    ap.add_argument("--weight-active-floor", type=float, default=0.03)
    ap.add_argument("--max-active-total", type=int, default=260)
    ap.add_argument("--active-count-penalty", type=float, default=0.04)
    ap.add_argument("--min-signal-count", type=int, default=5)
    ap.add_argument("--min-coverage-pct", type=float, default=2.0)
    ap.add_argument("--max-coverage-pct", type=float, default=20.0)
    ap.add_argument("--min-precision-pct", type=float, default=45.0)
    ap.add_argument("--min-mean-precision-pct", type=float, default=55.0)
    ap.add_argument("--max-bad-rate-pct", type=float, default=15.0)
    ap.add_argument("--max-mean-bad-rate-pct", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=20260706)
    ap.add_argument("--out-dir", default="exp_mpc_payoff_two_gene_fullfeature_stage2_rolling_ga_20260706_001")
    args = ap.parse_args(argv)

    base = load_base()
    rng = random.Random(args.seed)
    np.random.seed(args.seed % (2**32 - 1))
    runner = base.load_runner()
    raw, _ = runner.L.build_dataset(args.ticker)
    raw_features = [f for f in runner.L.feature_columns(raw) if f in raw.columns]
    data = base.add_targets(raw, args.good_high_atr, args.good_max_low_atr, args.bad_low_atr)
    features, audit = base.safe_features(raw_features, data)
    frames = base.period_frames(runner, data)
    rows = base.index_map(data, frames)
    reference_mask = np.zeros(len(data), dtype=bool)
    for p in SURVIVAL_PERIODS:
        reference_mask[rows[p]] = True
    qmat = base.quantile_matrix(data, reference_mask, features)

    all_candidates: list[tuple[str, FullIndividual]] = []
    trace: list[dict[str, Any]] = []
    generated_by_period: dict[str, int] = {}
    for source in GENERATION_PERIODS:
        candidates, tr = evolve_period(source, rng, len(features), qmat, rows, data, args)
        generated_by_period[source] = len(candidates)
        all_candidates.extend((source, x) for x in candidates)
        trace.extend(tr)

    dedup: dict[str, tuple[str, FullIndividual]] = {}
    for source, ind in all_candidates:
        sig = individual_signature(ind)
        if sig not in dedup or ind.fitness > dedup[sig][1].fitness:
            dedup[sig] = (source, ind)

    out_rows = [make_row(ind, source, features, qmat, rows, data, args) for source, ind in dedup.values()]
    out_rows.sort(
        key=lambda r: (
            int(r["생존평가전체통과"]),
            min(r["구간별성능"]["stress"]["적중률"], r["구간별성능"]["oos"]["적중률"]),
            -max(r["구간별성능"]["stress"]["위험발생률"], r["구간별성능"]["oos"]["위험발생률"]),
            r["구간별성능"]["stress"]["적중률"] + r["구간별성능"]["oos"]["적중률"],
            r["생성구간진화점수"],
        ),
        reverse=True,
    )
    survival_pass = [r for r in out_rows if r["생존평가전체통과"]]
    stress_oos_candidates = [
        r for r in out_rows
        if r["구간별성능"]["stress"]["신호발생일"] >= args.min_signal_count
        and r["구간별성능"]["oos"]["신호발생일"] >= args.min_signal_count
    ]

    out_dir = PROJECT_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "all_candidates.jsonl").open("w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (out_dir / "survival_pass.jsonl").open("w", encoding="utf-8") as f:
        for r in survival_pass:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    summary = {
        "실험방식": "상방/하방 유전자 각각이 전체 파라미터를 모두 슬롯으로 보유하고, 각 파라미터의 사용 여부/반영 강도/조건식을 GA가 진화",
        "종목": args.ticker,
        "특징감사": audit,
        "전체파라미터수": len(features),
        "유전자당파라미터슬롯수": len(features),
        "개체당파라미터슬롯수": len(features) * 2,
        "생성설정": {
            "생성구간": GENERATION_PERIODS,
            "생존평가구간": SURVIVAL_PERIODS,
            "구간별생성개체수": args.per_period_count,
            "세대수": args.generations,
            "초기사용비율": args.initial_active_rate,
            "세대별특징변이비율": args.feature_mutation_frac,
        },
        "구간별전체기준": {p: {"일수": len(frames[p]), "전체목표발생률": float(frames[p]["GOOD_SIGNAL"].mean() * 100.0), "전체위험발생률": float(frames[p]["BAD_RISK"].mean() * 100.0)} for p in frames},
        "생성개체수_구간별": generated_by_period,
        "전체생성개체수": len(all_candidates),
        "중복제거후개체수": len(out_rows),
        "생존평가전체통과개체수": len(survival_pass),
        "스트레스검증_둘다신호충족후보수": len(stress_oos_candidates),
        "진화기록": trace,
        "스트레스검증기준_상위개체미리보기": [no_dates(r) for r in out_rows[:10]],
        "생존평가전체통과개체미리보기": [no_dates(r) for r in survival_pass[:10]],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "출력폴더": str(out_dir),
        "전체파라미터수": len(features),
        "유전자당파라미터슬롯수": len(features),
        "개체당파라미터슬롯수": len(features) * 2,
        "생성개체수_구간별": generated_by_period,
        "전체생성개체수": len(all_candidates),
        "중복제거후개체수": len(out_rows),
        "생존평가전체통과개체수": len(survival_pass),
        "스트레스검증_둘다신호충족후보수": len(stress_oos_candidates),
        "상위개체": no_dates(out_rows[0]) if out_rows else None,
    }, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
