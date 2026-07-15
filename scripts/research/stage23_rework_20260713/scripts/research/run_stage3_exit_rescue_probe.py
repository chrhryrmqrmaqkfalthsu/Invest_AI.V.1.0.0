#!/usr/bin/env python3
"""Isolated exit-GA rescue probe for existing ADPT entry candidates.

This helper intentionally does not run entry GA or qualify. It reads existing
entry-scope candidate rulebooks, keeps entry fields fixed, applies the original
Stage 3 exit-GA helper to mutate only EXIT_FIELDS, and then validates the
resulting final rulebooks on train_1/train_2/recent_1y/stress.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve()
STAGE = HERE.parents[2]
REPO = HERE.parents[5]
sys.path.insert(0, str(HERE.parent))

import run_stage3_oos_stress_probe as probe  # noqa: E402

PERIOD_ORDER = ["train_1", "train_2", "train_3", "recent_1y", "stress_pre_2022h1"]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return float(default)
    return number if math.isfinite(number) else float(default)


def trade_gross_pct(trade: Mapping[str, Any]) -> float:
    ep = safe_float(trade.get("entry_price"), float("nan"))
    xp = safe_float(trade.get("exit_price"), float("nan"))
    if math.isfinite(ep) and ep != 0.0 and math.isfinite(xp):
        return (xp / ep - 1.0) * 100.0
    return safe_float(trade.get("pnl_pct"), 0.0)


def equity_mdd(returns: list[float]) -> float:
    equity = 100.0
    peak = 100.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + value / 100.0
        peak = max(peak, equity)
        worst = min(worst, (equity / peak - 1.0) * 100.0)
    return worst


def summarize_trades(trades: list[dict[str, Any]], metrics: Mapping[str, Any]) -> dict[str, Any]:
    returns = [safe_float(row["gross_return_pct"]) for row in trades]
    holds = [int(safe_float(row.get("holding_days"), 0.0)) for row in trades]
    wins = [r for r in returns if r >= 0.5]
    losses = [r for r in returns if r < 0.0]
    avg_win = statistics.mean(wins) if wins else None
    avg_loss = statistics.mean(losses) if losses else None
    return {
        "trade_count": len(returns),
        "expectancy_pct": safe_float(metrics.get("expectancy_pct"), statistics.mean(returns) if returns else 0.0),
        "avg_trade_return_pct": statistics.mean(returns) if returns else 0.0,
        "median_trade_return_pct": statistics.median(returns) if returns else 0.0,
        "win_rate_pct": (len(wins) / len(returns) * 100.0) if returns else 0.0,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss not in (None, 0.0) else None,
        "max_loss_pct": min(returns) if returns else 0.0,
        "max_gain_pct": max(returns) if returns else 0.0,
        "total_pct_points": sum(returns),
        "compounded_return_pct": (math.prod([1.0 + r / 100.0 for r in returns]) - 1.0) * 100.0 if returns else 0.0,
        "avg_holding_days": statistics.mean(holds) if holds else 0.0,
        "median_holding_days": statistics.median(holds) if holds else 0.0,
        "mdd_pct": safe_float(metrics.get("max_drawdown_pct"), equity_mdd(returns)),
        "mdd_compounded_gross_pct": equity_mdd(returns),
        "exit_reason_counts": dict(Counter(row.get("exit_reason") for row in trades)),
    }


def candidate_to_entry_row(candidate: Mapping[str, Any], rank: int) -> dict[str, Any]:
    return {
        "ticker": "ADPT",
        "rank": rank,
        "rulebook_hash": candidate["candidate_hash"],
        "candidate_id": candidate["candidate_id"],
        "selection_role": candidate["selection_role"],
        "source_fold": candidate.get("source_fold"),
        "rulebook": candidate["rulebook"],
    }


def exit_ga_worker(payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str(payload["ticker"])
    entry_row = dict(payload["entry_row"])
    seed = int(payload["seed"])
    market_cutoff_date = str(payload["market_cutoff_date"])
    worker_log = Path(payload["worker_log"])
    worker_log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    v5 = probe.load_v5()
    probe.force_v5_eec(v5)
    probe.patch_for_ticker(v5, ticker)
    if hasattr(v5.base, "_patch_market_cutoff"):
        from datetime import date
        v5.base._patch_market_cutoff(date.fromisoformat(market_cutoff_date))
    market, _ = v5.runner.support._preflight_market_snapshot()
    ctx, _ = v5.runner.support._load_snapshot_context(ticker, market)
    with worker_log.open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        final_rows = v5.runner.mod._base._run_exit_ga_for_entry(
            entry_row=entry_row,
            ctx=ctx,
            seed=seed,
            weights=v5.runner.mod._base.DEFAULT_EXIT_FITNESS_WEIGHTS,
        )
    for row in final_rows:
        row["candidate_id"] = entry_row.get("candidate_id")
        row["candidate_hash"] = entry_row.get("rulebook_hash")
        row["selection_role"] = entry_row.get("selection_role")
        row["source_fold"] = entry_row.get("source_fold")
    return {
        "candidate_id": entry_row.get("candidate_id"),
        "candidate_hash": entry_row.get("rulebook_hash"),
        "selection_role": entry_row.get("selection_role"),
        "source_fold": entry_row.get("source_fold"),
        "seed": seed,
        "elapsed_seconds": time.time() - started,
        "worker_log": str(worker_log),
        "final_rows": final_rows,
    }


def evaluate_final_worker(payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str(payload["ticker"])
    final_row = dict(payload["final_row"])
    period = dict(payload["period"])
    market_cutoff_date = str(payload["market_cutoff_date"])
    v5 = probe.load_v5()
    probe.force_v5_eec(v5)
    probe.patch_for_ticker(v5, ticker)
    if hasattr(v5.base, "_patch_market_cutoff"):
        from datetime import date
        v5.base._patch_market_cutoff(date.fromisoformat(market_cutoff_date))
    market, _ = v5.runner.support._preflight_market_snapshot()
    ctx, _ = v5.runner.support._load_snapshot_context(ticker, market)
    rb = v5.runner.Rulebook.from_dict(final_row["rulebook"])
    result = v5.runner.mod._base.run_backtest_period(rb, ctx, start=period.get("start"), end=period.get("end"))
    metrics = dict(v5.runner.mod._base.result_metrics(result))
    raw_trades = [t for t in list(getattr(result, "trades", []) or []) if isinstance(t, Mapping)]
    trade_rows: list[dict[str, Any]] = []
    for index, trade in enumerate(raw_trades, 1):
        gross = trade_gross_pct(trade)
        trade_rows.append({
            "candidate_id": final_row.get("candidate_id"),
            "candidate_hash": final_row.get("candidate_hash"),
            "selection_role": final_row.get("selection_role"),
            "source_fold": final_row.get("source_fold"),
            "entry_rulebook_hash": final_row.get("entry_rulebook_hash"),
            "final_rulebook_hash": final_row.get("rulebook_hash"),
            "exit_rank": final_row.get("exit_rank"),
            "period_label": period["label"],
            "period_role": period["role"],
            "trade_index": index,
            "entry_date": trade.get("entry_date"),
            "entry_price": trade.get("entry_price"),
            "exit_date": trade.get("exit_date"),
            "exit_price": trade.get("exit_price"),
            "exit_reason": trade.get("exit_reason"),
            "holding_days": trade.get("holding_days"),
            "gross_return_pct": gross,
            "pnl_pct_from_engine": trade.get("pnl_pct"),
            "mae_pct": safe_float(trade.get("max_loss_during_hold")),
            "win_plus_0_5pct": gross >= 0.5,
        })
    summary = summarize_trades(trade_rows, metrics)
    gate_included = period["label"] in probe.OOS_GATE_LABELS
    period_gate_pass = summary["expectancy_pct"] >= probe.EXPECTANCY_THRESHOLD if gate_included else None
    return {
        "candidate_id": final_row.get("candidate_id"),
        "candidate_hash": final_row.get("candidate_hash"),
        "selection_role": final_row.get("selection_role"),
        "source_fold": final_row.get("source_fold"),
        "entry_rulebook_hash": final_row.get("entry_rulebook_hash"),
        "final_rulebook_hash": final_row.get("rulebook_hash"),
        "exit_rank": final_row.get("exit_rank"),
        "composite_fitness": final_row.get("composite_fitness"),
        "period_label": period["label"],
        "period_role": period["role"],
        "period_start": period.get("start"),
        "period_end": period.get("end"),
        "gate_included": gate_included,
        "period_gate_pass": period_gate_pass,
        "metrics_original": metrics,
        "summary": summary,
        "trade_rows": trade_rows,
    }


def load_phase2_baseline(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in probe.read_jsonl(path):
        out[(str(row["candidate_id"]), str(row["period_label"]))] = row
    return out


def build_candidate_summaries(result_rows: list[dict[str, Any]], baseline: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    by_final: dict[tuple[str, int], dict[str, Any]] = defaultdict(lambda: {"periods": {}})
    for row in result_rows:
        key = (str(row["candidate_id"]), int(row["exit_rank"]))
        item = by_final[key]
        item.update({
            "candidate_id": row["candidate_id"],
            "candidate_hash": row["candidate_hash"],
            "selection_role": row["selection_role"],
            "source_fold": row.get("source_fold"),
            "entry_rulebook_hash": row.get("entry_rulebook_hash"),
            "final_rulebook_hash": row.get("final_rulebook_hash"),
            "exit_rank": int(row["exit_rank"]),
            "composite_fitness": row.get("composite_fitness"),
        })
        item["periods"][row["period_label"]] = row
    finals: list[dict[str, Any]] = []
    for item in by_final.values():
        gate_passes = [bool(item["periods"][label]["period_gate_pass"]) for label in probe.OOS_GATE_LABELS]
        if all(gate_passes):
            verdict = "OOS_PASS"
        elif not item["periods"]["recent_1y"]["period_gate_pass"]:
            verdict = "OOS_FAIL_RECENT"
        else:
            verdict = "OOS_FAIL_OTHER"
        item["verdict"] = verdict
        recent_exp = safe_float(item["periods"]["recent_1y"]["summary"]["expectancy_pct"])
        base_recent = baseline.get((item["candidate_id"], "recent_1y"), {}).get("summary", {})
        item["baseline_recent_expectancy_pct"] = safe_float(base_recent.get("expectancy_pct"), float("nan"))
        item["recent_expectancy_delta_vs_fixed_exit"] = recent_exp - item["baseline_recent_expectancy_pct"] if math.isfinite(item["baseline_recent_expectancy_pct"]) else None
        finals.append(item)
    finals.sort(key=lambda x: (str(x["candidate_id"]), int(x["exit_rank"])))
    return finals


def build_readout(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Stage 3 exit-rescue probe — ADPT")
    lines.append("")
    lines.append("## STEP 4 — verdict table")
    lines.append("")
    lines.append("판정 기준: 원본 Stage 3 OOS gate는 `train_1`, `train_2`, `recent_1y` 각각 `expectancy_pct >= 1.0`이다. Stress는 gate 제외 reference다. 수수료·슬리피지 미반영 gross return이다.")
    lines.append("")
    lines.append("|entry candidate|entry role|exit rescue verdict|best exit rank|fixed recent exp|best recent exp|delta|train_1 exp|train_2 exp|recent exp|stress exp|stress MDD|")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for cand in summary["entry_level_verdicts"]:
        best = cand["best_by_recent_expectancy"]
        p = best["periods"]
        lines.append(
            f"|{cand['candidate_id']}|{cand['selection_role']}|{cand['exit_rescue_verdict']}|{best['exit_rank']}|"
            f"{cand['fixed_recent_expectancy_pct']:.2f}|{best['periods']['recent_1y']['summary']['expectancy_pct']:.2f}|{best['recent_expectancy_delta_vs_fixed_exit']:.2f}|"
            f"{p['train_1']['summary']['expectancy_pct']:.2f}|{p['train_2']['summary']['expectancy_pct']:.2f}|{p['recent_1y']['summary']['expectancy_pct']:.2f}|"
            f"{p['stress_pre_2022h1']['summary']['expectancy_pct']:.2f}|{p['stress_pre_2022h1']['summary']['mdd_pct']:.2f}|"
        )
    lines.append("")
    lines.append("## STEP 3 — exit-rank detail")
    lines.append("")
    lines.append("|candidate|exit rank|verdict|composite fitness|train_1|train_2|recent_1y|stress|recent trades|recent avg hold|recent exit reasons|")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for final in summary["final_rulebook_summaries"]:
        p = final["periods"]
        recent = p["recent_1y"]["summary"]
        reasons = ", ".join(f"{k}:{v}" for k, v in sorted(recent["exit_reason_counts"].items()))
        lines.append(
            f"|{final['candidate_id']}|{final['exit_rank']}|{final['verdict']}|{safe_float(final.get('composite_fitness')):.3f}|"
            f"{p['train_1']['summary']['expectancy_pct']:.2f}|{p['train_2']['summary']['expectancy_pct']:.2f}|{p['recent_1y']['summary']['expectancy_pct']:.2f}|{p['stress_pre_2022h1']['summary']['expectancy_pct']:.2f}|"
            f"{recent['trade_count']}|{recent['avg_holding_days']:.2f}|{reasons}|"
        )
    lines.append("")
    lines.append("## STEP 0 — original exit-GA mechanism")
    lines.append("")
    lines.append("원본 `run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001` 기준:")
    lines.append("- lines 752-787: `_evaluate_exit_gene()`는 고정 entry rulebook에 exit gene만 `apply_exit()`로 덮어쓰고 stress_pre_2022h1 + bull(train_3)을 backtest해 `composite_exit_fitness()`를 계산한다.")
    lines.append("- lines 790-852: `_run_exit_ga_for_entry()`는 entry rulebook 하나를 base로 삼아 청산 14필드 전용 GA를 실행한다.")
    lines.append("- lines 855-887: `run_exit_ga()`는 `entry_rulebooks.jsonl`을 읽어 entry별 exit GA를 실행하고 `final_rulebooks.jsonl`을 만든다.")
    lines.append("- `engine/pipeline/exit_gene.py:68-82`: `apply_exit()`는 `EXIT_FIELDS`만 overwrite하고 entry/position/metadata는 copy한다.")
    lines.append("- `engine/pipeline/exit_gene.py:184-245`: composite fitness는 bull expectancy + downside term + bull floor penalty + stress MDD penalty + holding penalty로 구성된다.")
    lines.append("")
    lines.append("Phase 2 fixed-exit probe와의 차이: Phase 2는 기존 entry-scope provisional exit/interval-break를 그대로 적용했다. 이번 probe는 동일 entry rulebook을 고정한 뒤 원본 exit GA로 청산 필드만 진화시킨 final rulebook을 OOS/stress에 다시 적용했다.")
    lines.append("")
    lines.append("## STEP 1/2 — audit")
    lines.append("")
    lines.append(f"- actual host: `{summary['actual_host']}`")
    lines.append(f"- workers: {summary['workers']}")
    lines.append("- entry GA / qualify: not run")
    lines.append("- exit GA: isolated per entry candidate only")
    lines.append(f"- source run dir: `{summary['source_run_dir']}`")
    lines.append(f"- py_compile: {summary['static_checks']['py_compile']}")
    lines.append(f"- mutation helper AST SHA: `{summary['static_checks']['mutation_helper_ast_sha']}`")
    lines.append("")
    lines.append("### source artifact SHA invariant")
    for name, digest in summary["source_input_sha_start"].items():
        end = summary["source_input_sha_end"].get(name)
        lines.append(f"- `{name}`: `{digest}` -> `{end}` {'OK' if digest == end else 'CHANGED'}")
    lines.append("")
    lines.append("### protected SHA")
    for name, digest in summary["protected_sha_start"].items():
        end = summary["protected_sha_end"].get(name)
        lines.append(f"- `{name}`: `{digest}` -> `{end}` {'OK' if digest == end else 'CHANGED'}")
    lines.append("")
    lines.append(f"- daemon PID 494330 alive: {summary['daemon_alive_end']}")
    lines.append(f"- pre-output backup commit: `{summary['source_git_commit']}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="ADPT")
    parser.add_argument("--source-run-dir", required=True)
    parser.add_argument("--phase2-fixed-results", required=True)
    parser.add_argument("--candidate-source", default="all", choices=["fold_best", "all3", "all"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed-base", type=int, default=2026071401)
    parser.add_argument("--market-cutoff-date", default="2026-07-10")
    parser.add_argument("--source-git-commit", default="unknown")
    args = parser.parse_args(argv)

    ticker = args.ticker.upper().strip()
    source_dir = (REPO / args.source_run_dir).resolve() if not Path(args.source_run_dir).is_absolute() else Path(args.source_run_dir)
    fixed_path = (REPO / args.phase2_fixed_results).resolve() if not Path(args.phase2_fixed_results).is_absolute() else Path(args.phase2_fixed_results)
    out_dir = (REPO / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    protected_start = probe.protected_sha()
    source_sha_start = probe.source_input_sha(source_dir)
    candidates = probe.select_candidates(source_dir, args.candidate_source)
    baseline = load_phase2_baseline(fixed_path)
    data_cov = probe.data_coverage(ticker)
    periods = probe.period_definitions(data_cov["last_date"])

    import py_compile
    py_compile.compile(str(HERE), doraise=True)
    mutation_sha = probe.mutation_helper_ast_sha()
    if mutation_sha != probe.MUTATION_HELPER_EXPECTED_SHA:
        raise RuntimeError(f"mutation helper AST SHA mismatch: {mutation_sha}")
    static = {"py_compile": "PASS", "mutation_helper_ast_sha": mutation_sha, "mutation_helper_expected_sha": probe.MUTATION_HELPER_EXPECTED_SHA}

    logs_dir = out_dir / "exit_ga_logs"
    entry_rows = [candidate_to_entry_row(c, idx) for idx, c in enumerate(candidates, 1)]
    exit_payloads = [
        {
            "ticker": ticker,
            "entry_row": row,
            "seed": int(args.seed_base) + 1000 + idx,
            "market_cutoff_date": args.market_cutoff_date,
            "worker_log": str(logs_dir / f"{row['candidate_id']}.log"),
        }
        for idx, row in enumerate(entry_rows, 1)
    ]
    started = time.time()
    exit_ga_results: list[dict[str, Any]] = []
    workers = max(1, min(int(args.workers), len(exit_payloads)))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(exit_ga_worker, payload) for payload in exit_payloads]
        for future in as_completed(futures):
            exit_ga_results.append(future.result())
    exit_ga_results.sort(key=lambda r: str(r["candidate_id"]))
    final_rows: list[dict[str, Any]] = []
    for item in exit_ga_results:
        final_rows.extend(item["final_rows"])
    final_rows.sort(key=lambda r: (str(r["candidate_id"]), int(r["exit_rank"])))

    eval_payloads = []
    for final in final_rows:
        for period in periods:
            eval_payloads.append({"ticker": ticker, "final_row": final, "period": period, "market_cutoff_date": args.market_cutoff_date})
    result_rows: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, min(int(args.workers), len(eval_payloads)))) as pool:
        futures = [pool.submit(evaluate_final_worker, payload) for payload in eval_payloads]
        for future in as_completed(futures):
            result_rows.append(future.result())
    result_rows.sort(key=lambda r: (str(r["candidate_id"]), int(r["exit_rank"]), PERIOD_ORDER.index(r["period_label"])))
    trade_rows: list[dict[str, Any]] = []
    for row in result_rows:
        trade_rows.extend(row.pop("trade_rows"))

    final_summaries = build_candidate_summaries(result_rows, baseline)
    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for final in final_summaries:
        by_entry[str(final["candidate_id"])].append(final)
    entry_level = []
    for cid, finals in sorted(by_entry.items()):
        any_pass = any(f["verdict"] == "OOS_PASS" for f in finals)
        best_recent = max(finals, key=lambda f: safe_float(f["periods"]["recent_1y"]["summary"]["expectancy_pct"], -1e9))
        rank1 = next((f for f in finals if int(f["exit_rank"]) == 1), finals[0])
        fixed_recent = safe_float(baseline.get((cid, "recent_1y"), {}).get("summary", {}).get("expectancy_pct"), float("nan"))
        if any_pass:
            verdict = "EXIT_RESCUES_OOS"
        elif safe_float(best_recent["periods"]["recent_1y"]["summary"]["expectancy_pct"]) >= probe.EXPECTANCY_THRESHOLD:
            verdict = "EXIT_PARTIAL"
        else:
            verdict = "EXIT_DOES_NOT_RESCUE"
        entry_level.append({
            "candidate_id": cid,
            "candidate_hash": finals[0]["candidate_hash"],
            "selection_role": finals[0]["selection_role"],
            "source_fold": finals[0].get("source_fold"),
            "exit_rescue_verdict": verdict,
            "any_exit_rank_oos_pass": any_pass,
            "fixed_recent_expectancy_pct": fixed_recent,
            "rank1_summary": rank1,
            "best_by_recent_expectancy": best_recent,
        })

    source_sha_end = probe.source_input_sha(source_dir)
    protected_end = probe.protected_sha()
    summary = {
        "run_id": out_dir.name,
        "created_at_utc": datetime.now().astimezone().isoformat(),
        "actual_host": os.uname().nodename,
        "ticker": ticker,
        "source_run_dir": str(source_dir.relative_to(REPO) if source_dir.is_relative_to(REPO) else source_dir),
        "phase2_fixed_results": str(fixed_path.relative_to(REPO) if fixed_path.is_relative_to(REPO) else fixed_path),
        "workers": workers,
        "seed_base": int(args.seed_base),
        "market_cutoff_date": args.market_cutoff_date,
        "source_git_commit": args.source_git_commit,
        "exit_ga_mechanism": {
            "source": "original _run_exit_ga_for_entry",
            "exit_population": 60,
            "exit_generations": 25,
            "top_n_exit_per_entry": 3,
            "fitness_periods": ["stress_pre_2022h1", "train_3(bull)"],
            "entry_fields_fixed": True,
            "exit_fields_only_mutated": True,
        },
        "data_coverage": data_cov,
        "static_checks": static,
        "source_input_sha_start": source_sha_start,
        "source_input_sha_end": source_sha_end,
        "protected_sha_start": protected_start,
        "protected_sha_end": protected_end,
        "daemon_alive_end": Path("/proc/494330").is_dir(),
        "elapsed_seconds": time.time() - started,
        "exit_ga_results": exit_ga_results,
        "final_rulebook_summaries": final_summaries,
        "entry_level_verdicts": entry_level,
    }

    write_json(out_dir / "exit_rescue_summary.json", summary)
    write_jsonl(out_dir / "exit_rescue_final_rulebooks.jsonl", final_rows)
    write_jsonl(out_dir / "exit_rescue_results.jsonl", result_rows)
    write_jsonl(out_dir / "exit_rescue_trade_level.jsonl", trade_rows)
    (out_dir / "readout.md").write_text(build_readout(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
