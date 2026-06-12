from __future__ import annotations

import copy
import csv
import json
import math
import os
import random
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from engine.core.metadata import compute_rulebook_hash
from engine.strategies.rulebook import Rulebook, PARAM_RANGES, CATEGORICAL_PARAMS
from scripts.research.run_honest_stage2_full_ga_4fold import (
    context_from_cache,
    DEFAULT_OHLCV_CACHE,
    run_backtest_cc,
    result_metrics,
    ENTRY_EXECUTION_MODE,
    EXIT_EXECUTION_MODE,
    FOLD_EXIT_POLICY,
    FITNESS_MODE,
)

OUT = Path("exp_lasr_exitga_20260612_1950")
TICKER = "LASR"
POPULATION = 100
GENERATIONS = 40
SEED = 202606121950
MAX_WORKERS = min(6, max(1, (os.cpu_count() or 2) - 1))
ELITE_COUNT = 20
MUTATION_RATE = 0.25
MUTATION_STRENGTH = 0.18
RANDOM_RESET_RATE = 0.08
TOURNAMENT_SIZE = 3
TOP_KEEP = 25

EXIT_NUMERIC_FIELDS = [
    "stop_loss_atr",
    "stop_loss_atr_bear",
    "take_profit_atr",
    "take_profit_atr_bull",
    "trailing_atr",
    "trailing_atr_volatile",
    "trailing_activation_profit_pct",
    "breakeven_trigger_profit_pct",
    "breakeven_floor_profit_pct",
    "sell_omen_threshold",
    "max_holding_days",
]
EXIT_CATEGORICAL_FIELDS = ["exit_strategy", "breakeven_enabled", "sell_omen_enabled"]
EXIT_FIELDS = EXIT_CATEGORICAL_FIELDS + EXIT_NUMERIC_FIELDS
PERIODS = [
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025H2", "2025-06-01", None),
]
SURVIVOR_PREFIXES = ["0707c5f2", "2820575b", "28291859", "89908043", "cd2d26c4", "de9eb672"]
SET_A_HASH_PREFIX = "2820575b"

_G_CTX = None
_G_DATA_END = None
_G_BASE_DICT = None


def f0(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def individual_key(ind: dict) -> str:
    parts = []
    for k in EXIT_FIELDS:
        v = ind[k]
        if isinstance(v, float):
            parts.append(f"{k}={v:.8f}")
        else:
            parts.append(f"{k}={v}")
    return "|".join(parts)


def load_live_rulebook_dict() -> dict:
    with open("data/symbols/LASR/parameters.json", encoding="utf-8") as f:
        payload = json.load(f)
    return payload["rulebook"]


def load_survivor_exit_params() -> dict[str, dict]:
    out = {}
    with open("exp_lasr_reverse_20260612_1856/rulebooks_topn.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            prefix = row["hash"][:8]
            if prefix in SURVIVOR_PREFIXES:
                out[prefix] = {k: row["params"][k] for k in EXIT_FIELDS}
    missing = sorted(set(SURVIVOR_PREFIXES) - set(out))
    if missing:
        raise RuntimeError(f"missing survivor params: {missing}")
    return out


def median_or_majority(params_by_prefix: dict[str, dict]) -> dict:
    out = {}
    for k in EXIT_FIELDS:
        vals = [params_by_prefix[p][k] for p in SURVIVOR_PREFIXES]
        if k in EXIT_CATEGORICAL_FIELDS:
            out[k] = Counter(vals).most_common(1)[0][0]
        elif k == "max_holding_days":
            out[k] = int(round(statistics.median(float(v) for v in vals)))
        else:
            out[k] = float(statistics.median(float(v) for v in vals))
    return normalize_individual(out)


def live_exit_params(base_dict: dict) -> dict:
    return normalize_individual({k: base_dict[k] for k in EXIT_FIELDS})


def normalize_individual(ind: dict) -> dict:
    out = {}
    for k in EXIT_CATEGORICAL_FIELDS:
        allowed = CATEGORICAL_PARAMS[k]
        out[k] = ind[k] if ind.get(k) in allowed else allowed[0]
    for k in EXIT_NUMERIC_FIELDS:
        lo, hi = PARAM_RANGES[k]
        if k == "max_holding_days":
            out[k] = int(round(clamp(float(ind[k]), lo, hi)))
        else:
            out[k] = float(clamp(float(ind[k]), lo, hi))
    return out


def random_individual(rng: random.Random) -> dict:
    ind = {}
    for k in EXIT_CATEGORICAL_FIELDS:
        ind[k] = rng.choice(CATEGORICAL_PARAMS[k])
    for k in EXIT_NUMERIC_FIELDS:
        lo, hi = PARAM_RANGES[k]
        if k == "max_holding_days":
            ind[k] = rng.randint(int(lo), int(hi))
        else:
            ind[k] = rng.uniform(float(lo), float(hi))
    return normalize_individual(ind)


def crossover(a: dict, b: dict, rng: random.Random) -> dict:
    child = {}
    for k in EXIT_FIELDS:
        child[k] = a[k] if rng.random() < 0.5 else b[k]
    return normalize_individual(child)


def mutate(ind: dict, rng: random.Random) -> dict:
    out = dict(ind)
    for k in EXIT_CATEGORICAL_FIELDS:
        if rng.random() < MUTATION_RATE:
            out[k] = rng.choice(CATEGORICAL_PARAMS[k])
    for k in EXIT_NUMERIC_FIELDS:
        if rng.random() < MUTATION_RATE:
            lo, hi = PARAM_RANGES[k]
            if rng.random() < RANDOM_RESET_RATE:
                out[k] = rng.randint(int(lo), int(hi)) if k == "max_holding_days" else rng.uniform(float(lo), float(hi))
            else:
                span = float(hi) - float(lo)
                out[k] = float(out[k]) + rng.gauss(0.0, span * MUTATION_STRENGTH)
    return normalize_individual(out)


def tournament(pop: list[dict], fitness_by_key: dict[str, float], rng: random.Random) -> dict:
    picks = rng.sample(pop, min(TOURNAMENT_SIZE, len(pop)))
    return max(picks, key=lambda x: fitness_by_key.get(individual_key(x), -1e18))


def apply_exit_to_base(base_dict: dict, ind: dict) -> Rulebook:
    d = copy.deepcopy(base_dict)
    for k in EXIT_FIELDS:
        d[k] = ind[k]
    return Rulebook.from_dict(d)


def only_exit_fields_changed(base_dict: dict, rb: Rulebook) -> tuple[bool, list[str]]:
    d = rb.to_dict()
    changed = [k for k in sorted(set(base_dict) | set(d)) if base_dict.get(k) != d.get(k)]
    return all(k in EXIT_FIELDS for k in changed), changed


def _worker_init(base_dict: dict):
    global _G_CTX, _G_DATA_END, _G_BASE_DICT
    _G_BASE_DICT = base_dict
    _G_CTX = context_from_cache(TICKER, DEFAULT_OHLCV_CACHE)
    _G_DATA_END = str(_G_CTX.get("data_end") or _G_CTX.get("data_max") or "2026-06-09")


def _exit_dist(result) -> dict:
    return dict(sorted(Counter(str(t.get("exit_reason", "")) for t in (getattr(result, "trades", []) or [])).items()))


def _evaluate_worker(ind: dict) -> dict:
    rb = apply_exit_to_base(_G_BASE_DICT, ind)
    ok, changed = only_exit_fields_changed(_G_BASE_DICT, rb)
    if not ok:
        raise RuntimeError(f"entry fields changed: {changed}")
    period_rows = []
    for label, start, end in PERIODS:
        result = run_backtest_cc(rb, _G_CTX, start_date=start, end_date=(_G_DATA_END if end is None else end))
        m = result_metrics(result)
        period_rows.append({
            "label": label,
            "expectancy_pct": f0(m.get("expectancy_pct")),
            "max_drawdown_pct": f0(m.get("max_drawdown_pct")),
            "trade_count": int(m.get("trade_count", 0) or 0),
            "win_rate": f0(m.get("win_rate")),
            "profit_factor": f0(m.get("profit_factor")),
            "fitness_swing": f0(m.get("fitness")),
            "exit_dist": _exit_dist(result),
        })
    exps = [r["expectancy_pct"] for r in period_rows]
    dds_abs = [abs(min(0.0, r["max_drawdown_pct"])) for r in period_rows]
    avg_exp = sum(exps) / len(exps)
    min_exp = min(exps)
    neg_count = sum(1 for x in exps if x < 0)
    avg_dd = sum(dds_abs) / len(dds_abs)
    worst_dd = max(dds_abs)
    exp_stdev = statistics.pstdev(exps) if len(exps) > 1 else 0.0
    # 4구간 균형형 fitness. 거래 수는 하드 컷/보상에 넣지 않고 기록만 한다.
    # 최악 expectancy를 강하게 끌어올리고, 평균/분산/DD를 함께 벌한다.
    composite = (
        avg_exp
        + 2.0 * min_exp
        - 0.15 * exp_stdev
        - 0.20 * avg_dd
        - 0.25 * worst_dd
        - 5.0 * neg_count
    )
    return {
        "key": individual_key(ind),
        "individual": ind,
        "rulebook_hash": compute_rulebook_hash(rb),
        "composite_fitness": composite,
        "avg_exp": avg_exp,
        "min_exp": min_exp,
        "neg_count": neg_count,
        "avg_dd_abs": avg_dd,
        "worst_dd_abs": worst_dd,
        "exp_stdev": exp_stdev,
        "total_trades": sum(r["trade_count"] for r in period_rows),
        "periods": period_rows,
        "changed_fields": changed,
    }


def evaluate_population(pop: list[dict], cache: dict[str, dict], executor: ProcessPoolExecutor) -> list[dict]:
    pending = []
    for ind in pop:
        key = individual_key(ind)
        if key not in cache:
            pending.append(ind)
    futures = {executor.submit(_evaluate_worker, ind): individual_key(ind) for ind in pending}
    for fut in as_completed(futures):
        res = fut.result()
        cache[res["key"]] = res
    return [cache[individual_key(ind)] for ind in pop]


def unique_population(candidates: list[dict], rng: random.Random) -> list[dict]:
    out = []
    seen = set()
    for ind in candidates:
        n = normalize_individual(ind)
        key = individual_key(n)
        if key not in seen:
            out.append(n)
            seen.add(key)
    while len(out) < POPULATION:
        ind = random_individual(rng)
        key = individual_key(ind)
        if key not in seen:
            out.append(ind)
            seen.add(key)
    return out[:POPULATION]


def result_row(res: dict, label: str) -> dict:
    p = next(x for x in res["periods"] if x["label"] == label)
    return {
        "variant": res.get("name", ""),
        "rank": res.get("rank", ""),
        "rulebook_hash": res["rulebook_hash"],
        "label": label,
        "composite_fitness": res["composite_fitness"],
        "avg_exp": res["avg_exp"],
        "min_exp": res["min_exp"],
        "neg_count": res["neg_count"],
        "avg_dd_abs": res["avg_dd_abs"],
        "worst_dd_abs": res["worst_dd_abs"],
        "exp_stdev": res["exp_stdev"],
        "total_trades": res["total_trades"],
        **p,
        "exit_dist": json.dumps(p["exit_dist"], ensure_ascii=False, sort_keys=True),
    }


def fmt(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    base_dict = load_live_rulebook_dict()
    survivor_params = load_survivor_exit_params()
    live_ind = live_exit_params(base_dict)
    set_a = normalize_individual(survivor_params[SET_A_HASH_PREFIX])
    set_b = median_or_majority(survivor_params)

    seeds = [live_ind, set_a, set_b] + [normalize_individual(survivor_params[p]) for p in SURVIVOR_PREFIXES]
    while len(seeds) < 20:
        parent = rng.choice(seeds)
        seeds.append(mutate(parent, rng))
    population = unique_population(seeds + [random_individual(rng) for _ in range(POPULATION)], rng)

    cache: dict[str, dict] = {}
    history = []
    started = time.time()
    with ProcessPoolExecutor(max_workers=MAX_WORKERS, initializer=_worker_init, initargs=(base_dict,)) as executor:
        for gen in range(GENERATIONS + 1):
            results = evaluate_population(population, cache, executor)
            ranked = sorted(results, key=lambda r: r["composite_fitness"], reverse=True)
            best = ranked[0]
            history.append({
                "generation": gen,
                "best_fitness": best["composite_fitness"],
                "avg_top10_fitness": sum(r["composite_fitness"] for r in ranked[:10]) / min(10, len(ranked)),
                "cache_size": len(cache),
                "best_hash": best["rulebook_hash"],
                "best_avg_exp": best["avg_exp"],
                "best_min_exp": best["min_exp"],
                "best_worst_dd_abs": best["worst_dd_abs"],
                "best_total_trades": best["total_trades"],
            })
            print(
                json.dumps({
                    "gen": gen,
                    "best": round(best["composite_fitness"], 6),
                    "avg_top10": round(history[-1]["avg_top10_fitness"], 6),
                    "cache": len(cache),
                    "avg_exp": round(best["avg_exp"], 4),
                    "min_exp": round(best["min_exp"], 4),
                    "worst_dd": round(best["worst_dd_abs"], 4),
                    "trades": best["total_trades"],
                }, ensure_ascii=False),
                flush=True,
            )
            if gen >= GENERATIONS:
                break
            elites = [r["individual"] for r in ranked[:ELITE_COUNT]]
            fitness_by_key = {r["key"]: r["composite_fitness"] for r in results}
            next_pop = list(elites)
            while len(next_pop) < POPULATION:
                if rng.random() < 0.12:
                    child = random_individual(rng)
                else:
                    p1 = tournament(population, fitness_by_key, rng)
                    p2 = tournament(population, fitness_by_key, rng)
                    child = mutate(crossover(p1, p2, rng), rng)
                next_pop.append(child)
            population = unique_population(next_pop, rng)

    # 평가 기준 비교군: baseline, Set B, GA top3 unique.
    final_results = sorted(cache.values(), key=lambda r: r["composite_fitness"], reverse=True)
    by_key = {r["key"]: r for r in cache.values()}
    compare = []
    for name, ind in [("baseline_live_42088d4e", live_ind), ("setB_manual_median", set_b)]:
        res = by_key.get(individual_key(ind))
        if res is None:
            # baseline/setB가 진화 중 캐시에 없을 수 있어 직접 평가한다.
            with ProcessPoolExecutor(max_workers=1, initializer=_worker_init, initargs=(base_dict,)) as ex:
                res = evaluate_population([ind], cache, ex)[0]
        res = copy.deepcopy(res)
        res["name"] = name
        res["rank"] = "baseline" if name.startswith("baseline") else "manual"
        compare.append(res)
    top_unique = []
    seen_hashes = set()
    for res in final_results:
        if res["rulebook_hash"] in seen_hashes:
            continue
        seen_hashes.add(res["rulebook_hash"])
        rr = copy.deepcopy(res)
        rr["name"] = f"ga_top{len(top_unique)+1}"
        rr["rank"] = len(top_unique) + 1
        top_unique.append(rr)
        if len(top_unique) >= 3:
            break
    compare.extend(top_unique)

    # Write outputs.
    (OUT / "config.json").write_text(json.dumps({
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ticker": TICKER,
        "population": POPULATION,
        "generations": GENERATIONS,
        "seed": SEED,
        "max_workers": MAX_WORKERS,
        "elite_count": ELITE_COUNT,
        "mutation_rate": MUTATION_RATE,
        "mutation_strength": MUTATION_STRENGTH,
        "random_reset_rate": RANDOM_RESET_RATE,
        "entry_fixed": True,
        "exit_fields": EXIT_FIELDS,
        "fixed_fields": [k for k in sorted(base_dict) if k not in EXIT_FIELDS],
        "execution": {
            "entry_execution_mode": ENTRY_EXECUTION_MODE,
            "exit_execution_mode": EXIT_EXECUTION_MODE,
            "fold_exit_policy": FOLD_EXIT_POLICY,
            "live_hard_stop_guard": True,
            "fitness_mode": FITNESS_MODE,
        },
        "fitness_formula": "avg_exp + 2.0*min_exp - 0.15*stdev_exp - 0.20*avg_abs_dd - 0.25*worst_abs_dd - 5.0*negative_period_count; trades recorded only, no hard min_trades",
        "elapsed_seconds": time.time() - started,
        "cache_size": len(cache),
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with (OUT / "history.csv").open("w", newline="", encoding="utf-8") as f:
        fields = list(history[0].keys())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(history)

    with (OUT / "compare_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "variant", "rank", "rulebook_hash", "label", "composite_fitness", "avg_exp", "min_exp", "neg_count",
            "avg_dd_abs", "worst_dd_abs", "exp_stdev", "total_trades", "expectancy_pct", "max_drawdown_pct",
            "trade_count", "win_rate", "profit_factor", "fitness_swing", "exit_dist",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for res in compare:
            for label, _, _ in PERIODS:
                w.writerow(result_row(res, label))

    with (OUT / "top_results.jsonl").open("w", encoding="utf-8") as f:
        for i, res in enumerate(final_results[:TOP_KEEP], 1):
            rr = copy.deepcopy(res)
            rr["rank"] = i
            f.write(json.dumps(rr, ensure_ascii=False, sort_keys=True) + "\n")

    (OUT / "exit_params_compare.json").write_text(json.dumps({
        res["name"]: {"rank": res["rank"], "hash": res["rulebook_hash"], "params": res["individual"]}
        for res in compare
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Markdown report.
    lines = []
    lines.append("# LASR exit-only GA experiment\n\n")
    lines.append("진입 규칙은 live 42088d4e 그대로 고정하고 청산 파라미터만 GA로 탐색했다. 4구간 종합 fitness에는 2025H2가 포함되어 있으므로 방법론 검증용이며 실거래 OOS가 아니다.\n\n")
    lines.append("## Phase 1 — fixed/searched fields\n\n")
    lines.append("### Searched exit fields\n\n")
    for k in EXIT_FIELDS:
        rng_info = PARAM_RANGES.get(k, CATEGORICAL_PARAMS.get(k))
        lines.append(f"- {k}: {rng_info}\n")
    lines.append("\n진입 잠금 검산: GA 개체는 `Rulebook.from_dict(live)` 복사본에 위 청산 필드만 덮어쓴다. top3 변경 필드도 모두 청산 필드 안에 있다.\n")
    lines.append("\n## Phase 2 — fitness formula\n\n")
    lines.append("```text\nfitness = avg_exp + 2.0*min_exp - 0.15*stdev_exp - 0.20*avg_abs_dd - 0.25*worst_abs_dd - 5.0*negative_period_count\n```\n")
    lines.append("거래 수는 hard cutoff나 보상에 넣지 않고 기록만 했다.\n")
    lines.append(f"\n- population={POPULATION}, generations={GENERATIONS}, seed={SEED}, workers={MAX_WORKERS}, evaluated_unique={len(cache)}, elapsed_sec={time.time()-started:.1f}\n")
    lines.append("\n## Phase 3 — composite summary\n\n")
    lines.append("| variant | comp fitness | avg exp | min exp | neg periods | worst DD abs | total trades |\n|---|---:|---:|---:|---:|---:|---:|\n")
    for res in compare:
        lines.append(f"| {res['name']} | {res['composite_fitness']:.3f} | {res['avg_exp']:.3f} | {res['min_exp']:.3f} | {res['neg_count']} | {res['worst_dd_abs']:.3f} | {res['total_trades']} |\n")
    lines.append("\n## 4-period metrics\n\n")
    lines.append("| period | variant | exp% | maxDD% | trades | exits |\n|---|---|---:|---:|---:|---|\n")
    for label, _, _ in PERIODS:
        for res in compare:
            p = next(x for x in res["periods"] if x["label"] == label)
            lines.append(f"| {label} | {res['name']} | {p['expectancy_pct']:.3f} | {p['max_drawdown_pct']:.3f} | {p['trade_count']} | `{json.dumps(p['exit_dist'], ensure_ascii=False, sort_keys=True)}` |\n")
    lines.append("\n## Exit params\n\n")
    lines.append("| field | baseline | SetB manual | GA top1 | GA top2 | GA top3 |\n|---|---:|---:|---:|---:|---:|\n")
    names = ["baseline_live_42088d4e", "setB_manual_median", "ga_top1", "ga_top2", "ga_top3"]
    by_name = {res["name"]: res for res in compare}
    for k in EXIT_FIELDS:
        vals = [fmt(by_name[n]["individual"][k]) for n in names]
        lines.append(f"| {k} | " + " | ".join(vals) + " |\n")
    (OUT / "REPORT.md").write_text("".join(lines), encoding="utf-8")

    print(json.dumps({
        "out": str(OUT),
        "elapsed_sec": round(time.time() - started, 3),
        "evaluated_unique": len(cache),
        "best": {
            "fitness": final_results[0]["composite_fitness"],
            "avg_exp": final_results[0]["avg_exp"],
            "min_exp": final_results[0]["min_exp"],
            "worst_dd_abs": final_results[0]["worst_dd_abs"],
            "trades": final_results[0]["total_trades"],
            "hash": final_results[0]["rulebook_hash"],
            "individual": final_results[0]["individual"],
        },
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
