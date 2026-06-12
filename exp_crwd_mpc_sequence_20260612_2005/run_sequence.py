from __future__ import annotations

import copy, csv, json, math, os, random, statistics, time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from engine.core.metadata import compute_rulebook_hash
from engine.learning.genetic import GAConfig, run_ga, collect_top_rulebooks
from engine.pipeline.topn_survivor import score_topn_validation_periods
from engine.strategies.rulebook import Rulebook, PARAM_RANGES, CATEGORICAL_PARAMS
from scripts.research.run_honest_stage2_full_ga_4fold import (
    context_from_cache, DEFAULT_OHLCV_CACHE, run_backtest_cc, result_metrics,
    ENTRY_EXECUTION_MODE, EXIT_EXECUTION_MODE, FOLD_EXIT_POLICY, FITNESS_MODE,
)

RUN_ID = "20260612_2005"
TICKERS = {
    "CRWD": {"seed2022": 21263308, "seed2025H2": 21263311, "live_hash_prefix": "b00e0b2a"},
    "MPC": {"seed2022": 21265708, "seed2025H2": 21265711, "live_hash_prefix": "6f39b3ba"},
}
POP, GEN, TOP_N = 100, 40, 100
STRICT = {"min_trades": 5, "min_member": 10.0, "general_exp": 1.0, "stress_exp": 0.0}
PERIODS = [
    ("2022", 2022, False, "2022-01-01", "2022-12-31"),
    ("2023", 2023, False, "2023-01-01", "2023-12-31"),
    ("2024", 2024, False, "2024-01-01", "2024-12-31"),
    ("2025H2", "2025H2", True, "2025-06-01", None),
]
EXIT_NUMERIC = [
    "stop_loss_atr", "stop_loss_atr_bear", "take_profit_atr", "take_profit_atr_bull",
    "trailing_atr", "trailing_atr_volatile", "trailing_activation_profit_pct",
    "breakeven_trigger_profit_pct", "breakeven_floor_profit_pct", "sell_omen_threshold", "max_holding_days",
]
EXIT_CAT = ["exit_strategy", "breakeven_enabled", "sell_omen_enabled"]
EXIT_FIELDS = EXIT_CAT + EXIT_NUMERIC
EXITGA_WORKERS = 3
EXITGA_SEED_BASE = 202606122005
ELITE_COUNT, MUTATION_RATE, MUTATION_STRENGTH, RESET_RATE, TOURNAMENT_SIZE = 20, 0.25, 0.18, 0.08, 3

_G_CTX = None
_G_DATA_END = None
_G_BASE_DICT = None
_G_TICKER = None


def f0(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def i0(x):
    try:
        return int(float(x or 0))
    except Exception:
        return 0


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def exit_dist(result):
    return dict(sorted(Counter(str(t.get("exit_reason", "")) for t in (getattr(result, "trades", []) or [])).items()))


def context_for(ticker):
    return context_from_cache(ticker, DEFAULT_OHLCV_CACHE)


def result_to_metrics(result):
    m = result_metrics(result)
    return {k: m[k] for k in ["trade_count", "win_rate", "expectancy_pct", "profit_factor", "max_drawdown_pct"]} | {"fitness": f0(m.get("fitness"))}


def rb_params(rb):
    return {k: getattr(rb, k, None) for k in EXIT_FIELDS}


def run_ga_candidates(ticker, ctx, train_start, train_end, seed):
    cfg = GAConfig(population=POP, generations=GEN, elite_ratio=0.2, mutation_rate=0.15, mutation_strength=0.2,
                   tournament_size=3, seed_pattern_ratio=0.33, early_stop_no_improve=GEN, random_seed=seed)
    def evaluate_fn(rb):
        return f0(getattr(run_backtest_cc(rb, ctx, start_date=train_start, end_date=train_end), "fitness", -1_000_000.0))
    started = time.time()
    ga = run_ga(base_rulebook=ctx["base_rulebook"], evaluate_fn=evaluate_fn, ga_config=cfg)
    return collect_top_rulebooks(ga, TOP_N), time.time() - started, ga


def candidate_period_row(ticker, rb, rank, split, result, train_start, train_end):
    label, year, is_stress, start, end = split
    return {
        "ticker": ticker, "year": year, "label": label, "is_stress": is_stress,
        "rank_is": rank, "rulebook_hash": compute_rulebook_hash(rb), "train_fitness": f0(getattr(rb, "fitness", 0.0)),
        "train_period": [train_start, train_end], "test_period": [start, end],
        "oos": result_to_metrics(result), "fitness": f0(getattr(result, "fitness", 0.0)),
    }


def evaluate_candidate_set(ticker, ctx, candidates, train_start, train_end, out_dir):
    data_end = str(ctx.get("data_end") or ctx.get("data_max") or "2026-06-09")
    periods, raw_by_hash, trade_rows = [], {}, []
    for split in PERIODS:
        label, year, is_stress, start, end = split
        end_date = data_end if end is None else end
        cand_rows = []
        print(json.dumps({"event":"eval_period", "ticker":ticker, "label":label, "n":len(candidates)}, ensure_ascii=False), flush=True)
        for rank, rb in enumerate(candidates, 1):
            result = run_backtest_cc(rb, ctx, start_date=start, end_date=end_date)
            row = candidate_period_row(ticker, rb, rank, (label, year, is_stress, start, end_date), result, train_start, train_end)
            h = row["rulebook_hash"]
            if h not in raw_by_hash:
                raw_by_hash[h] = {"hash": h, "rank_train": rank, "train_fitness": f0(getattr(rb, "fitness", 0.0)), "rulebook": rb.to_dict(), "params": rb_params(rb), "periods_raw": {}, "periods_scored": {}}
            raw_by_hash[h]["periods_raw"][label] = {**row["oos"], "exit_dist": exit_dist(result)}
            trade_rows.append({"hash": h, "label": label, "rank_train": rank, "exit_dist": exit_dist(result), "trades": list(getattr(result, "trades", []) or [])})
            cand_rows.append(row)
        periods.append({"ticker": ticker, "year": year, "label": label, "is_stress": is_stress, "train_period": [train_start, train_end], "test_period": [start, end_date], "candidate_count": len(cand_rows), "candidates": cand_rows})
    scored = score_topn_validation_periods({"periods": periods}, general_years=(2022, 2023, 2024), stress_labels=("2025H2",))
    for bucket in ["general_periods", "stress_periods"]:
        for p in scored.get(bucket, []):
            label = p["label"]
            for c in p.get("candidates", []):
                h = c["rulebook_hash"]; m = c.get("oos_metrics") or {}
                raw_by_hash[h]["periods_scored"][label] = {
                    "trade_count": i0(m.get("trade_count")), "win_rate": f0(m.get("win_rate")), "expectancy_pct": f0(m.get("expectancy_pct")),
                    "profit_factor": f0(m.get("profit_factor")), "max_drawdown_pct": f0(m.get("max_drawdown_pct")), "oos_member_score": f0(c.get("oos_member_score")), "rank_is": i0(c.get("rank_is")),
                }
    summary_rows, survivors = [], []
    for h, item in raw_by_hash.items():
        ps = item["periods_scored"]
        passes = {}
        for label in ["2022", "2023", "2024"]:
            p = ps.get(label, {})
            passes[label] = bool(i0(p.get("trade_count")) >= STRICT["min_trades"] and f0(p.get("oos_member_score")) >= STRICT["min_member"] and f0(p.get("expectancy_pct")) >= STRICT["general_exp"])
        p = ps.get("2025H2", {})
        passes["2025H2"] = bool(i0(p.get("trade_count")) >= STRICT["min_trades"] and f0(p.get("oos_member_score")) >= STRICT["min_member"] and f0(p.get("expectancy_pct")) >= STRICT["stress_exp"])
        gp = sum(1 for lab in ["2022", "2023", "2024"] if passes[lab])
        all4 = bool(gp >= 3 and passes["2025H2"])
        row = {"hash": h, "rank_train": item["rank_train"], "train_fitness": item["train_fitness"], **{f"pass_{k}": v for k, v in passes.items()}, "general_pass_count": gp, "survives_all4": all4}
        for label in ["2022", "2023", "2024", "2025H2"]:
            p = ps.get(label, {})
            row[f"{label}_exp"] = f0(p.get("expectancy_pct")); row[f"{label}_dd"] = f0(p.get("max_drawdown_pct")); row[f"{label}_trades"] = i0(p.get("trade_count")); row[f"{label}_member"] = f0(p.get("oos_member_score"))
        summary_rows.append(row)
        if all4:
            survivors.append({**row, "params": item["params"], "rulebook": item["rulebook"], "periods": ps})
    summary_rows.sort(key=lambda r: (not r["survives_all4"], -r["general_pass_count"], not r["pass_2025H2"], not r["pass_2022"], r["rank_train"]))
    survivors.sort(key=lambda r: r["rank_train"])
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "period_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["hash", "rank_train", "label", "trade_count", "win_rate", "expectancy_pct", "profit_factor", "max_drawdown_pct", "oos_member_score", "exit_dist"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for h, item in sorted(raw_by_hash.items(), key=lambda kv: kv[1]["rank_train"]):
            for label in ["2022", "2023", "2024", "2025H2"]:
                p = item["periods_scored"].get(label, {}); raw = item["periods_raw"].get(label, {})
                w.writerow({"hash": h, "rank_train": item["rank_train"], "label": label, "trade_count": i0(p.get("trade_count")), "win_rate": f0(p.get("win_rate")), "expectancy_pct": f0(p.get("expectancy_pct")), "profit_factor": f0(p.get("profit_factor")), "max_drawdown_pct": f0(p.get("max_drawdown_pct")), "oos_member_score": f0(p.get("oos_member_score")), "exit_dist": json.dumps(raw.get("exit_dist", {}), ensure_ascii=False, sort_keys=True)})
    with (out_dir / "survival_summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys())); w.writeheader(); w.writerows(summary_rows)
    (out_dir / "survivors.json").write_text(json.dumps(survivors, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with (out_dir / "rulebooks_topn.jsonl").open("w", encoding="utf-8") as f:
        for h, item in sorted(raw_by_hash.items(), key=lambda kv: kv[1]["rank_train"]):
            f.write(json.dumps({"hash": h, "rank_train": item["rank_train"], "train_fitness": item["train_fitness"], "params": item["params"], "rulebook": item["rulebook"]}, ensure_ascii=False, sort_keys=True) + "\n")
    with (out_dir / "trades.jsonl").open("w", encoding="utf-8") as f:
        for tr in trade_rows:
            f.write(json.dumps(tr, ensure_ascii=False, sort_keys=True) + "\n")
    dist = Counter(str(r["general_pass_count"]) for r in summary_rows)
    result = {"candidate_count": len(summary_rows), "pass_2022": sum(1 for r in summary_rows if r["pass_2022"]), "pass_2023": sum(1 for r in summary_rows if r["pass_2023"]), "pass_2024": sum(1 for r in summary_rows if r["pass_2024"]), "stress_pass": sum(1 for r in summary_rows if r["pass_2025H2"]), "general3": sum(1 for r in summary_rows if r["general_pass_count"] >= 3), "all4": sum(1 for r in summary_rows if r["survives_all4"]), "general_pass_dist": dict(sorted(dist.items()))}
    (out_dir / "result_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def run_multiyear_or_reverse(ticker, mode, seed):
    ctx = context_for(ticker)
    data_start = str(ctx.get("data_start") or ctx.get("data_min") or "2020-01-01")
    train_end = "2021-12-31" if mode == "multiyear" else "2025-05-31"
    out_dir = Path(f"exp_{ticker.lower()}_{mode}_{RUN_ID}")
    print(json.dumps({"event":"start_ga", "ticker":ticker, "mode":mode, "seed":seed, "train":[data_start, train_end]}, ensure_ascii=False), flush=True)
    candidates, ga_sec, ga = run_ga_candidates(ticker, ctx, data_start, train_end, seed)
    result = evaluate_candidate_set(ticker, ctx, candidates, data_start, train_end, out_dir)
    config = {"ticker": ticker, "mode": mode, "population": POP, "generations": GEN, "seed": seed, "train_period": [data_start, train_end], "ga_seconds": ga_sec, "generations_run": getattr(ga, "generations_run", None), "best_train_fitness": f0(getattr(getattr(ga, "best", None), "fitness", 0.0)), "strict": STRICT, "execution": {"entry": ENTRY_EXECUTION_MODE, "exit": EXIT_EXECUTION_MODE, "fold_exit_policy": FOLD_EXIT_POLICY, "live_hard_stop_guard": True, "fitness_mode": FITNESS_MODE}, "result": result}
    (out_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "REPORT.md").write_text(f"# {ticker} {mode}\n\n{json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True)}\n", encoding="utf-8")
    print(json.dumps({"event":"done_ga", "ticker":ticker, "mode":mode, **result}, ensure_ascii=False), flush=True)
    return result


def normalize_ind(ind):
    out = {}
    for k in EXIT_CAT:
        allowed = CATEGORICAL_PARAMS[k]
        out[k] = ind[k] if ind.get(k) in allowed else allowed[0]
    for k in EXIT_NUMERIC:
        lo, hi = PARAM_RANGES[k]
        out[k] = int(round(clamp(float(ind[k]), lo, hi))) if k == "max_holding_days" else float(clamp(float(ind[k]), lo, hi))
    return out


def ind_key(ind):
    return "|".join(f"{k}={ind[k]:.8f}" if isinstance(ind[k], float) else f"{k}={ind[k]}" for k in EXIT_FIELDS)


def live_rb_dict(ticker):
    return json.load(open(f"data/symbols/{ticker}/parameters.json", encoding="utf-8"))["rulebook"]


def live_ind(base):
    return normalize_ind({k: base[k] for k in EXIT_FIELDS})


def random_ind(rng):
    d = {}
    for k in EXIT_CAT: d[k] = rng.choice(CATEGORICAL_PARAMS[k])
    for k in EXIT_NUMERIC:
        lo, hi = PARAM_RANGES[k]
        d[k] = rng.randint(int(lo), int(hi)) if k == "max_holding_days" else rng.uniform(float(lo), float(hi))
    return normalize_ind(d)


def crossover(a, b, rng):
    return normalize_ind({k: a[k] if rng.random() < 0.5 else b[k] for k in EXIT_FIELDS})


def mutate(ind, rng):
    out = dict(ind)
    for k in EXIT_CAT:
        if rng.random() < MUTATION_RATE: out[k] = rng.choice(CATEGORICAL_PARAMS[k])
    for k in EXIT_NUMERIC:
        if rng.random() < MUTATION_RATE:
            lo, hi = PARAM_RANGES[k]
            if rng.random() < RESET_RATE: out[k] = rng.randint(int(lo), int(hi)) if k == "max_holding_days" else rng.uniform(float(lo), float(hi))
            else: out[k] = float(out[k]) + rng.gauss(0, (float(hi)-float(lo))*MUTATION_STRENGTH)
    return normalize_ind(out)


def unique_pop(cands, rng):
    out, seen = [], set()
    for ind in cands:
        n = normalize_ind(ind); k = ind_key(n)
        if k not in seen: out.append(n); seen.add(k)
    while len(out) < POP:
        n = random_ind(rng); k = ind_key(n)
        if k not in seen: out.append(n); seen.add(k)
    return out[:POP]


def apply_exit(base, ind):
    d = copy.deepcopy(base)
    for k in EXIT_FIELDS: d[k] = ind[k]
    return Rulebook.from_dict(d)


def worker_init(ticker, base):
    global _G_CTX, _G_DATA_END, _G_BASE_DICT, _G_TICKER
    _G_TICKER = ticker; _G_BASE_DICT = base; _G_CTX = context_for(ticker); _G_DATA_END = str(_G_CTX.get("data_end") or _G_CTX.get("data_max") or "2026-06-09")


def eval_exit_worker(ind):
    rb = apply_exit(_G_BASE_DICT, ind)
    period_rows = []
    for label, year, is_stress, start, end in PERIODS:
        result = run_backtest_cc(rb, _G_CTX, start_date=start, end_date=(_G_DATA_END if end is None else end))
        m = result_to_metrics(result)
        period_rows.append({"label": label, **m, "exit_dist": exit_dist(result)})
    exps = [p["expectancy_pct"] for p in period_rows]
    dd_abs = [abs(min(0, p["max_drawdown_pct"])) for p in period_rows]
    avg_exp, min_exp = sum(exps)/4, min(exps)
    neg_count = sum(1 for x in exps if x < 0)
    avg_dd, worst_dd = sum(dd_abs)/4, max(dd_abs)
    stdev = statistics.pstdev(exps)
    fitness = avg_exp + 2.0*min_exp - 0.15*stdev - 0.20*avg_dd - 0.25*worst_dd - 5.0*neg_count
    return {"key": ind_key(ind), "individual": ind, "rulebook_hash": compute_rulebook_hash(rb), "composite_fitness": fitness, "avg_exp": avg_exp, "min_exp": min_exp, "neg_count": neg_count, "avg_dd_abs": avg_dd, "worst_dd_abs": worst_dd, "exp_stdev": stdev, "total_trades": sum(p["trade_count"] for p in period_rows), "periods": period_rows}


def eval_exit_pop(pop, cache, executor):
    futures = {executor.submit(eval_exit_worker, ind): ind_key(ind) for ind in pop if ind_key(ind) not in cache}
    for fut in as_completed(futures): cache[fut.result()["key"]] = fut.result()
    return [cache[ind_key(ind)] for ind in pop]


def tournament(pop, fit, rng):
    picks = rng.sample(pop, min(TOURNAMENT_SIZE, len(pop)))
    return max(picks, key=lambda x: fit.get(ind_key(x), -1e18))


def reverse_seed_exits(ticker):
    p = Path(f"exp_{ticker.lower()}_reverse_{RUN_ID}/rulebooks_topn.jsonl")
    s = Path(f"exp_{ticker.lower()}_reverse_{RUN_ID}/survival_summary.csv")
    if not p.exists() or not s.exists(): return []
    by_hash = {}
    for line in p.open(encoding="utf-8"):
        r = json.loads(line); by_hash[r["hash"]] = normalize_ind({k: r["params"][k] for k in EXIT_FIELDS})
    out = []
    for r in csv.DictReader(s.open(encoding="utf-8")):
        if r.get("survives_all4") == "True" or r.get("general_pass_count") == "3" or r.get("pass_2022") == "True":
            if r["hash"] in by_hash: out.append(by_hash[r["hash"]])
    return out[:20]


def run_exitga(ticker, seed):
    out_dir = Path(f"exp_{ticker.lower()}_exitga_{RUN_ID}"); out_dir.mkdir(parents=True, exist_ok=True)
    base = live_rb_dict(ticker); rng = random.Random(seed)
    seeds = [live_ind(base)] + reverse_seed_exits(ticker)
    while len(seeds) < 20: seeds.append(mutate(rng.choice(seeds), rng) if seeds else random_ind(rng))
    pop = unique_pop(seeds + [random_ind(rng) for _ in range(POP)], rng)
    cache, history = {}, []
    started = time.time()
    with ProcessPoolExecutor(max_workers=EXITGA_WORKERS, initializer=worker_init, initargs=(ticker, base)) as ex:
        for gen in range(GEN+1):
            results = eval_exit_pop(pop, cache, ex)
            ranked = sorted(results, key=lambda r: r["composite_fitness"], reverse=True)
            best = ranked[0]
            history.append({"generation": gen, "best_fitness": best["composite_fitness"], "avg_top10_fitness": sum(r["composite_fitness"] for r in ranked[:10])/10, "cache_size": len(cache), "best_hash": best["rulebook_hash"], "best_avg_exp": best["avg_exp"], "best_min_exp": best["min_exp"], "best_worst_dd_abs": best["worst_dd_abs"], "best_total_trades": best["total_trades"]})
            print(json.dumps({"event":"exitga_gen", "ticker":ticker, "gen":gen, "best":round(best["composite_fitness"], 6), "avg_exp":round(best["avg_exp"], 4), "min_exp":round(best["min_exp"], 4), "worst_dd":round(best["worst_dd_abs"], 4), "trades":best["total_trades"], "cache":len(cache)}, ensure_ascii=False), flush=True)
            if gen >= GEN: break
            fit = {r["key"]: r["composite_fitness"] for r in results}
            elites = [r["individual"] for r in ranked[:ELITE_COUNT]]
            nxt = list(elites)
            while len(nxt) < POP:
                child = random_ind(rng) if rng.random() < 0.12 else mutate(crossover(tournament(pop, fit, rng), tournament(pop, fit, rng), rng), rng)
                nxt.append(child)
            pop = unique_pop(nxt, rng)
    final = sorted(cache.values(), key=lambda r: r["composite_fitness"], reverse=True)
    base_ind = live_ind(base)
    with ProcessPoolExecutor(max_workers=1, initializer=worker_init, initargs=(ticker, base)) as ex:
        baseline = eval_exit_pop([base_ind], cache, ex)[0]
    baseline = copy.deepcopy(baseline); baseline["name"] = "baseline_live"; baseline["rank"] = "baseline"
    top = []
    seen = set()
    for r in final:
        if r["rulebook_hash"] in seen: continue
        rr = copy.deepcopy(r); rr["name"] = f"ga_top{len(top)+1}"; rr["rank"] = len(top)+1
        top.append(rr); seen.add(r["rulebook_hash"])
        if len(top) >= 3: break
    compare = [baseline] + top
    with (out_dir/"compare_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["variant", "rank", "rulebook_hash", "label", "composite_fitness", "avg_exp", "min_exp", "neg_count", "avg_dd_abs", "worst_dd_abs", "exp_stdev", "total_trades", "expectancy_pct", "max_drawdown_pct", "trade_count", "win_rate", "profit_factor", "fitness", "exit_dist"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in compare:
            for p in r["periods"]:
                w.writerow({"variant": r["name"], "rank": r["rank"], "rulebook_hash": r["rulebook_hash"], "label": p["label"], "composite_fitness": r["composite_fitness"], "avg_exp": r["avg_exp"], "min_exp": r["min_exp"], "neg_count": r["neg_count"], "avg_dd_abs": r["avg_dd_abs"], "worst_dd_abs": r["worst_dd_abs"], "exp_stdev": r["exp_stdev"], "total_trades": r["total_trades"], "expectancy_pct": p["expectancy_pct"], "max_drawdown_pct": p["max_drawdown_pct"], "trade_count": p["trade_count"], "win_rate": p["win_rate"], "profit_factor": p["profit_factor"], "fitness": p["fitness"], "exit_dist": json.dumps(p["exit_dist"], ensure_ascii=False, sort_keys=True)})
    with (out_dir/"history.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(history[0].keys())); w.writeheader(); w.writerows(history)
    with (out_dir/"top_results.jsonl").open("w", encoding="utf-8") as f:
        for i, r in enumerate(final[:25], 1):
            rr = copy.deepcopy(r); rr["rank"] = i; f.write(json.dumps(rr, ensure_ascii=False, sort_keys=True)+"\n")
    (out_dir/"exit_params_compare.json").write_text(json.dumps({r["name"]: {"rank": r["rank"], "hash": r["rulebook_hash"], "params": r["individual"]} for r in compare}, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    cfg = {"ticker": ticker, "population": POP, "generations": GEN, "seed": seed, "workers": EXITGA_WORKERS, "fitness_formula": "avg_exp + 2*min_exp - .15*stdev - .20*avg_abs_dd - .25*worst_abs_dd - 5*neg_count", "elapsed_seconds": time.time()-started, "evaluated_unique": len(cache), "best": {k: final[0][k] for k in ["composite_fitness", "avg_exp", "min_exp", "worst_dd_abs", "total_trades", "rulebook_hash"]}, "execution": {"entry": ENTRY_EXECUTION_MODE, "exit": EXIT_EXECUTION_MODE, "fold_exit_policy": FOLD_EXIT_POLICY, "live_hard_stop_guard": True, "fitness_mode": FITNESS_MODE}}
    (out_dir/"config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    (out_dir/"REPORT.md").write_text(f"# {ticker} exitga\n\n{json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True)}\n", encoding="utf-8")
    print(json.dumps({"event":"done_exitga", "ticker":ticker, **cfg["best"]}, ensure_ascii=False), flush=True)
    return cfg


def main():
    started_all = time.time()
    global_report = {"created_at": datetime.now(timezone.utc).isoformat(), "run_id": RUN_ID, "tickers": {}, "notes": "CRWD/MPC LASR reproduction. Reverse and exitga are diagnostic, not live OOS."}
    for ticker, info in TICKERS.items():
        global_report["tickers"][ticker] = {"live_hash_prefix": info["live_hash_prefix"]}
        global_report["tickers"][ticker]["multiyear"] = run_multiyear_or_reverse(ticker, "multiyear", info["seed2022"])
        global_report["tickers"][ticker]["reverse"] = run_multiyear_or_reverse(ticker, "reverse", info["seed2025H2"])
        global_report["tickers"][ticker]["exitga"] = run_exitga(ticker, EXITGA_SEED_BASE + (1 if ticker == "CRWD" else 2))
    global_report["elapsed_seconds"] = time.time() - started_all
    Path(f"exp_crwd_mpc_sequence_{RUN_ID}/summary.json").write_text(json.dumps(global_report, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps({"event":"ALL_DONE", "elapsed_seconds": global_report["elapsed_seconds"]}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
