#!/usr/bin/env python3
"""Read-only Stage 3 OOS/stress probe for existing entry-scope candidates.

This helper does not run GA, qualify, or run_exit_ga.  It loads existing
candidate rulebooks from a source run directory and applies them to the original
Stage 3 validation periods using the current rework entry-scope backtest path.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve()
STAGE = HERE.parents[2]
REPO = HERE.parents[5]
V5_PATH = STAGE / "scripts/research/run_stage3_aap_eec_penalty_v5_host.py"
CACHE_ROOT = REPO / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache"
SELL_OMEN = REPO / "data/_system/ml_sell_omen/sell_omen_scores.csv"

TRAIN_PERIODS = {
    "train_1": {"label": "train_1", "start": "2022-07-01", "end": "2023-06-30", "role": "oos_gate"},
    "train_2": {"label": "train_2", "start": "2023-07-01", "end": "2024-06-30", "role": "oos_gate"},
    "train_3": {"label": "train_3", "start": "2024-07-01", "end": "2025-06-30", "role": "in_sample_reference"},
}
RECENT_1Y = {"label": "recent_1y", "start": "2025-07-01", "end": None, "role": "oos_gate"}
STRESS = {"label": "stress_pre_2022h1", "start": None, "end": "2022-06-30", "role": "stress_reference"}
OOS_GATE_LABELS = ("train_1", "train_2", "recent_1y")
EXPECTANCY_THRESHOLD = 1.0
PROTECTED_PATHS = (
    REPO / ".env",
    REPO / "data/_system/market_history.csv",
    REPO / "data/_system/market_history_v2.csv",
)
MUTATION_HELPER_EXPECTED_SHA = "aab7163f9194cf5f989ad01973e8d2967dad48be53f7d52ee09747eea502077d"

_WORKER: dict[str, Any] = {}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return float(default)
    return number if math.isfinite(number) else float(default)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, default=str) + "\n")


def load_v5() -> Any:
    spec = importlib.util.spec_from_file_location("_stage3_oos_stress_v5", V5_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load v5 host: {V5_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def force_v5_eec(v5: Any) -> None:
    os.environ["KINGMAKER_ENTRY_EEC_TARGET"] = "6"
    os.environ["KINGMAKER_ENTRY_EEC_FLOOR"] = "0.5"
    os.environ["KINGMAKER_ENTRY_EEC_CLUSTER_GAP_TRADING_DAYS"] = "8"
    if hasattr(v5, "eec_v5"):
        v5.eec_v5.ENTRY_FITNESS_EEC_TARGET = 6.0
        v5.eec_v5.ENTRY_FITNESS_EEC_FLOOR = 0.5
    if hasattr(v5, "execution_bt"):
        v5.execution_bt.ENTRY_FITNESS_EEC_TARGET = 6.0
        v5.execution_bt.ENTRY_FITNESS_EEC_FLOOR = 0.5


def patch_for_ticker(v5: Any, ticker: str) -> None:
    ticker = ticker.upper().strip()
    support = v5.runner.support
    from engine.learning.learner import _detect_sector_name

    def load_cache_context(requested: str, market_history_df: pd.DataFrame):
        requested = ticker
        path = CACHE_ROOT / f"{requested}.pkl"
        if not path.is_file():
            raise FileNotFoundError(f"OHLCV cache missing: {path}")
        src = pd.read_pickle(path).copy()
        src.index = pd.to_datetime(src.index, errors="coerce")
        if src.index.isna().any():
            raise RuntimeError(f"invalid OHLCV cache index dates: {requested}")
        src = src.sort_index()
        raw = src[["Open", "High", "Low", "Close", "Volume"]].copy()
        for col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
            if not np.isfinite(raw[col].to_numpy(dtype=float)).all():
                raise RuntimeError(f"OHLCV NaN/Inf: {requested}:{col}")
        df = support.calc_indicators(raw)
        df, sell_omen_info = support.attach_sell_omen_scores(df, requested, score_table_path=SELL_OMEN)
        adapter = support.mod._pipeline_context.get_adapter(requested)
        meta = adapter.meta
        sector_name = _detect_sector_name(meta.name)
        base_rulebook = support.default_rulebook(requested, asset_type=meta.asset_type, direction=meta.direction)
        base_rulebook.sector_name = sector_name
        data_start = str(pd.Timestamp(df.index.min()).date())
        data_end = str(pd.Timestamp(df.index.max()).date())
        context = {
            "ticker": requested,
            "adapter": adapter,
            "meta": meta,
            "df": df,
            "rows": int(len(df)),
            "data_min": data_start,
            "data_max": data_end,
            "data_start": data_start,
            "data_end": data_end,
            "market_history_df": market_history_df.copy(),
            "ticker_sentiment": None,
            "sector_name": sector_name,
            "base_rulebook": base_rulebook,
            "sell_omen_info": sell_omen_info,
        }
        metadata = {
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "rows": int(len(df)),
            "first_date": data_start,
            "last_date": data_end,
            "external_fetch": False,
            "auto_regenerate": False,
            "source": "stage0_ohlcv_cache_pkl_runtime_loader",
            "sell_omen_info": sell_omen_info,
        }
        return context, metadata

    modules = []
    for obj in [v5, getattr(v5, "runner", None), getattr(v5, "base", None), getattr(v5, "v3", None), getattr(v5, "v4", None)]:
        if obj is not None:
            modules.append(obj)
            if getattr(obj, "runner", None) is not None:
                modules.append(obj.runner)
    seen: set[int] = set()
    for mod in modules:
        if id(mod) in seen:
            continue
        seen.add(id(mod))
        if hasattr(mod, "TICKER"):
            setattr(mod, "TICKER", ticker)
    support._load_snapshot_context = load_cache_context


def mutation_helper_ast_sha() -> str:
    source = (STAGE / "engine/learning/genetic.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"mutate", "crossover", "random_rulebook"}:
            selected.append(ast.dump(node, include_attributes=False))
    return hashlib.sha256("\n".join(selected).encode("utf-8")).hexdigest()


def protected_sha() -> dict[str, str]:
    return {str(path.relative_to(REPO)): sha256_file(path) for path in PROTECTED_PATHS}


def source_input_sha(source_dir: Path) -> dict[str, str]:
    names = [
        "qualify_result.json",
        "qualify_cross_fold_matrix.jsonl",
        "qualify_candidate_rulebooks.jsonl",
        "fold_best_summary.json",
        "fold_best_trade_level.jsonl",
        "qualify_gate_bottleneck.json",
    ]
    return {name: sha256_file(source_dir / name) for name in names if (source_dir / name).is_file()}


def select_candidates(source_dir: Path, candidate_source: str) -> list[dict[str, Any]]:
    rule_rows = {row["candidate_hash"]: row for row in read_jsonl(source_dir / "qualify_candidate_rulebooks.jsonl")}
    output: list[dict[str, Any]] = []
    if candidate_source in {"fold_best", "all"}:
        fold_hash: dict[str, str] = {}
        for row in read_jsonl(source_dir / "fold_best_trade_level.jsonl"):
            fold_hash.setdefault(str(row["period_label"]), str(row["candidate_hash"]))
        for fold in ("train_1", "train_2", "train_3"):
            h = fold_hash.get(fold)
            if not h or h not in rule_rows:
                raise RuntimeError(f"missing fold-best rulebook for {fold}: {h}")
            output.append({"candidate_id": f"fold_best_{fold}", "candidate_hash": h, "selection_role": "fold_best", "source_fold": fold, "rulebook": rule_rows[h]["rulebook"]})
    if candidate_source in {"all3", "all"}:
        by_hash: dict[str, dict[str, bool]] = defaultdict(dict)
        for row in read_jsonl(source_dir / "qualify_cross_fold_matrix.jsonl"):
            by_hash[str(row["candidate_hash"])][str(row["period_label"])] = bool(row.get("pass"))
        all3 = sorted(h for h, folds in by_hash.items() if all(folds.get(f) for f in ("train_1", "train_2", "train_3")))
        if not all3:
            raise RuntimeError("candidate_source includes all3 but no all3 candidates found")
        for idx, h in enumerate(all3, 1):
            if h not in rule_rows:
                raise RuntimeError(f"missing all3 rulebook: {h}")
            output.append({"candidate_id": f"all3_{idx}", "candidate_hash": h, "selection_role": "all3", "source_fold": None, "rulebook": rule_rows[h]["rulebook"]})
    if candidate_source not in {"fold_best", "all3", "all"}:
        raise ValueError(f"unsupported candidate-source: {candidate_source}")
    # de-duplicate exact same candidate id is not expected; same hash can appear under different roles and is kept intentionally.
    return output


def period_definitions(data_end: str) -> list[dict[str, Any]]:
    periods = [dict(TRAIN_PERIODS["train_1"]), dict(TRAIN_PERIODS["train_2"]), dict(TRAIN_PERIODS["train_3"]), dict(RECENT_1Y), dict(STRESS)]
    for p in periods:
        if p["label"] == "recent_1y" and p.get("end") is None:
            p["end"] = data_end
    return periods


def trade_gross_pct(trade: Mapping[str, Any]) -> float:
    ep = safe_float(trade.get("entry_price"), float("nan"))
    xp = safe_float(trade.get("exit_price"), float("nan"))
    if not math.isfinite(ep) or ep == 0 or not math.isfinite(xp):
        return safe_float(trade.get("pnl_pct"), 0.0)
    return (xp / ep - 1.0) * 100.0


def equity_mdd(returns: list[float]) -> float:
    equity = 100.0
    peak = 100.0
    worst = 0.0
    for r in returns:
        equity *= 1.0 + r / 100.0
        peak = max(peak, equity)
        worst = min(worst, (equity / peak - 1.0) * 100.0)
    return worst


def summarize_trades(trade_rows: list[dict[str, Any]], metrics: Mapping[str, Any]) -> dict[str, Any]:
    rets = [safe_float(row["gross_return_pct"]) for row in trade_rows]
    holds = [int(safe_float(row.get("holding_days"), 0.0)) for row in trade_rows]
    wins = [r for r in rets if r >= 0.5]
    losses = [r for r in rets if r < 0.0]
    avg_win = statistics.mean(wins) if wins else None
    avg_loss = statistics.mean(losses) if losses else None
    return {
        "trade_count": len(rets),
        "expectancy_pct": safe_float(metrics.get("expectancy_pct"), statistics.mean(rets) if rets else 0.0),
        "avg_trade_return_pct": statistics.mean(rets) if rets else 0.0,
        "median_trade_return_pct": statistics.median(rets) if rets else 0.0,
        "win_rate_pct": (len(wins) / len(rets) * 100.0) if rets else 0.0,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_win is not None and avg_loss not in (None, 0.0) else None,
        "max_loss_pct": min(rets) if rets else 0.0,
        "max_gain_pct": max(rets) if rets else 0.0,
        "total_pct_points": sum(rets),
        "compounded_return_pct": (math.prod([1.0 + r / 100.0 for r in rets]) - 1.0) * 100.0 if rets else 0.0,
        "avg_holding_days": statistics.mean(holds) if holds else 0.0,
        "median_holding_days": statistics.median(holds) if holds else 0.0,
        "min_holding_days": min(holds) if holds else 0,
        "max_holding_days": max(holds) if holds else 0,
        "mdd_pct": safe_float(metrics.get("max_drawdown_pct"), equity_mdd(rets)),
        "mdd_compounded_gross_pct": equity_mdd(rets),
        "exit_reason_counts": dict(Counter(row.get("exit_reason") for row in trade_rows)),
    }


def init_worker(ticker: str, market_cutoff_date: str) -> None:
    v5 = load_v5()
    force_v5_eec(v5)
    patch_for_ticker(v5, ticker)
    if hasattr(v5.base, "_patch_market_cutoff"):
        v5.base._patch_market_cutoff(date.fromisoformat(market_cutoff_date))
    market_frame, market_metadata = v5.runner.support._preflight_market_snapshot()
    ctx, ohlcv_metadata = v5.runner.support._load_snapshot_context(ticker, market_frame)
    _WORKER.clear()
    _WORKER.update({"v5": v5, "ctx": ctx, "market_metadata": market_metadata, "ohlcv_metadata": ohlcv_metadata})


def run_task(task: tuple[str, str, str, dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    candidate_id, candidate_hash, role, rulebook_payload, period = task
    v5 = _WORKER["v5"]
    ctx = _WORKER["ctx"]
    execution_bt = v5.execution_bt
    rb = v5.runner.Rulebook.from_dict(rulebook_payload)
    marker = execution_bt.ENTRY_GA_SCOPE_MARKER
    had_marker = hasattr(rb, marker)
    old_marker = getattr(rb, marker, None)
    setattr(rb, marker, execution_bt.ENTRY_GA_SCOPE_VALUE)
    try:
        result = v5.runner.mod.run_entry_backtest_period(
            rb,
            ctx,
            start=period.get("start"),
            end=period.get("end"),
        )
    finally:
        if had_marker:
            setattr(rb, marker, old_marker)
        else:
            try:
                delattr(rb, marker)
            except AttributeError:
                pass
    metrics = dict(v5.runner.mod._base.result_metrics(result))
    diagnostics = dict(getattr(result, "entry_fitness_diagnostics", {}) or {})
    raw_trades = list(getattr(result, "trades", []) or [])
    trade_rows: list[dict[str, Any]] = []
    for idx, trade in enumerate(raw_trades, 1):
        tape = trade.get("entry_signal_tape") if isinstance(trade.get("entry_signal_tape"), Mapping) else {}
        gross = trade_gross_pct(trade)
        trade_rows.append(
            {
                "candidate_id": candidate_id,
                "candidate_hash": candidate_hash,
                "selection_role": role,
                "period_label": period["label"],
                "period_role": period["role"],
                "trade_index": idx,
                "entry_signal_date": trade.get("entry_signal_date"),
                "entry_date": trade.get("entry_fill_date", trade.get("entry_date")),
                "entry_price": trade.get("entry_price"),
                "exit_date": trade.get("exit_date"),
                "exit_price": trade.get("exit_price"),
                "exit_reason": trade.get("exit_reason"),
                "holding_days": trade.get("holding_days"),
                "gross_return_pct": gross,
                "pnl_pct_from_engine": trade.get("pnl_pct"),
                "mae_pct": safe_float(trade.get("max_loss_during_hold")),
                "win_plus_0_5pct": gross >= 0.5,
                "entry_features": dict(tape.get("entry_features") or {}),
                "interval_checks": dict(tape.get("interval_checks") or {}),
                "strict_interval_pass": tape.get("strict_interval_pass"),
            }
        )
    summary = summarize_trades(trade_rows, metrics)
    gate_pass = None
    if period["label"] in OOS_GATE_LABELS:
        gate_pass = summary["expectancy_pct"] >= EXPECTANCY_THRESHOLD
    return {
        "candidate_id": candidate_id,
        "candidate_hash": candidate_hash,
        "selection_role": role,
        "period_label": period["label"],
        "period_role": period["role"],
        "period_start": period.get("start"),
        "period_end": period.get("end"),
        "gate_included": period["label"] in OOS_GATE_LABELS,
        "expectancy_threshold_pct": EXPECTANCY_THRESHOLD if period["label"] in OOS_GATE_LABELS else None,
        "period_gate_pass": gate_pass,
        "metrics_original": metrics,
        "entry_fitness_diagnostics": diagnostics,
        "summary": summary,
        "trade_rows": trade_rows,
    }


def data_coverage(ticker: str) -> dict[str, Any]:
    path = CACHE_ROOT / f"{ticker}.pkl"
    df = pd.read_pickle(path)
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    data_end = str(df.index.max().date())
    periods = period_definitions(data_end)
    coverage: dict[str, Any] = {
        "path": str(path),
        "sha256": sha256_file(path),
        "rows": int(len(df)),
        "first_date": str(df.index.min().date()),
        "last_date": data_end,
        "periods": {},
    }
    for p in periods:
        sub = df
        if p.get("start"):
            sub = sub.loc[sub.index >= pd.Timestamp(p["start"])]
        if p.get("end"):
            sub = sub.loc[sub.index <= pd.Timestamp(p["end"])]
        coverage["periods"][p["label"]] = {
            "rows": int(len(sub)),
            "first": str(sub.index.min().date()) if len(sub) else None,
            "last": str(sub.index.max().date()) if len(sub) else None,
            "ohlcv_nulls": int(sub[["Open", "High", "Low", "Close", "Volume"]].isna().sum().sum()) if len(sub) else None,
        }
    return coverage


def build_markdown(summary: dict[str, Any], result_rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# ADPT OOS + Stress validation — original Stage 3 rulebook probe")
    lines.append("")
    lines.append("## STEP 4 — final verdict")
    lines.append("")
    lines.append("수익률은 entry/exit price 기준 gross return이며 수수료·슬리피지는 미반영이다. 원본 Stage 3 OOS gate는 `train_1`, `train_2`, `recent_1y` 각각 `expectancy_pct >= 1.0`이다. `train_3`는 in-sample reference, stress는 gate 제외 reference다.")
    lines.append("")
    lines.append("|candidate|role|hash|verdict|train_1 exp|train_2 exp|recent_1y exp|recent trades|recent avg ret|recent win|recent MDD|stress exp|stress trades|stress MDD|")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for cand in summary["candidate_summaries"]:
        p = cand["periods"]
        def exp(label: str) -> float:
            return safe_float(p[label]["summary"]["expectancy_pct"])
        recent = p["recent_1y"]["summary"]
        stress = p["stress_pre_2022h1"]["summary"]
        lines.append(
            f"|{cand['candidate_id']}|{cand['selection_role']}|`{cand['candidate_hash'][:10]}...`|{cand['verdict']}|"
            f"{exp('train_1'):.2f}|{exp('train_2'):.2f}|{exp('recent_1y'):.2f}|"
            f"{recent['trade_count']}|{recent['avg_trade_return_pct']:.2f}|{recent['win_rate_pct']:.1f}%|{recent['mdd_pct']:.2f}|"
            f"{stress['expectancy_pct']:.2f}|{stress['trade_count']}|{stress['mdd_pct']:.2f}|"
        )
    lines.append("")
    lines.append("## STEP 2 — OOS performance")
    lines.append("")
    lines.append("|candidate|period|gate|trades|avg hold|expectancy|avg ret|median ret|win|payoff|MDD|total pct-pts|compounded|exit reasons|")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in result_rows:
        if row["period_label"] not in ("train_1", "train_2", "train_3", "recent_1y"):
            continue
        s = row["summary"]
        gate = "REF" if not row["gate_included"] else ("PASS" if row["period_gate_pass"] else "FAIL")
        payoff = s["payoff_ratio"]
        payoff_txt = "N/A" if payoff is None else f"{payoff:.2f}"
        reasons = ", ".join(f"{k}:{v}" for k, v in sorted(s["exit_reason_counts"].items()))
        lines.append(
            f"|{row['candidate_id']}|{row['period_label']}|{gate}|{s['trade_count']}|{s['avg_holding_days']:.2f}|{s['expectancy_pct']:.2f}|{s['avg_trade_return_pct']:.2f}|{s['median_trade_return_pct']:.2f}|{s['win_rate_pct']:.1f}%|{payoff_txt}|{s['mdd_pct']:.2f}|{s['total_pct_points']:.2f}|{s['compounded_return_pct']:.2f}|{reasons}|"
        )
    lines.append("")
    lines.append("## STEP 3 — stress reference")
    lines.append("")
    lines.append("|candidate|stress period|trades|expectancy|avg ret|win|MDD|total pct-pts|exit reasons|")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for row in result_rows:
        if row["period_label"] != "stress_pre_2022h1":
            continue
        s = row["summary"]
        reasons = ", ".join(f"{k}:{v}" for k, v in sorted(s["exit_reason_counts"].items()))
        lines.append(f"|{row['candidate_id']}|stress_pre_2022h1|{s['trade_count']}|{s['expectancy_pct']:.2f}|{s['avg_trade_return_pct']:.2f}|{s['win_rate_pct']:.1f}%|{s['mdd_pct']:.2f}|{s['total_pct_points']:.2f}|{reasons}|")
    lines.append("")
    lines.append("## STEP 0/1 — audit")
    lines.append("")
    lines.append(f"- source run dir: `{summary['source_run_dir']}`")
    lines.append("- candidates: fold-best 3개 + all3 1개. trend_chop20 후보는 미포함.")
    lines.append("- run_exit_ga / GA / qualify 재학습: 미가동.")
    lines.append(f"- worker mode: VM ProcessPoolExecutor max_workers={summary['workers']}")
    lines.append(f"- py_compile: {summary['static_checks']['py_compile']}")
    lines.append(f"- mutation helper AST SHA: `{summary['static_checks']['mutation_helper_ast_sha']}`")
    lines.append(f"- original OOS criterion: train_1/train_2/recent_1y each expectancy_pct >= {EXPECTANCY_THRESHOLD}")
    lines.append("- stress: stress_pre_2022h1 reference only, gate excluded.")
    lines.append("")
    lines.append("### Data coverage")
    lines.append("")
    dc = summary["data_coverage"]
    lines.append(f"ADPT SHA `{dc['sha256']}`, coverage {dc['first_date']}~{dc['last_date']}, rows {dc['rows']}.")
    lines.append("")
    lines.append("|period|rows|first|last|ohlcv nulls|")
    lines.append("|---|---:|---|---|---:|")
    for label, rec in dc["periods"].items():
        lines.append(f"|{label}|{rec['rows']}|{rec['first']}|{rec['last']}|{rec['ohlcv_nulls']}|")
    lines.append("")
    lines.append("### Input SHA invariant")
    lines.append("")
    for name, digest in summary["source_input_sha_start"].items():
        end = summary["source_input_sha_end"].get(name)
        ok = "OK" if digest == end else "CHANGED"
        lines.append(f"- `{name}`: `{digest}` -> `{end}` {ok}")
    lines.append("")
    lines.append("### Protected SHA")
    lines.append("")
    for name, digest in summary["protected_sha_start"].items():
        end = summary["protected_sha_end"].get(name)
        ok = "OK" if digest == end else "CHANGED"
        lines.append(f"- `{name}`: `{digest}` -> `{end}` {ok}")
    lines.append("")
    lines.append(f"- daemon PID 494330 alive: {summary['daemon_alive_end']}")
    lines.append(f"- source git commit before output: `{summary['source_git_commit']}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="ADPT")
    parser.add_argument("--source-run-dir", required=True)
    parser.add_argument("--candidate-source", default="all", choices=["fold_best", "all3", "all"])
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--market-cutoff-date", default="2026-07-10")
    parser.add_argument("--source-git-commit", default="unknown")
    args = parser.parse_args(argv)

    ticker = args.ticker.upper().strip()
    source_dir = (REPO / args.source_run_dir).resolve() if not Path(args.source_run_dir).is_absolute() else Path(args.source_run_dir)
    out_dir = (REPO / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    protected_start = protected_sha()
    input_sha_start = source_input_sha(source_dir)
    data_cov = data_coverage(ticker)
    periods = period_definitions(data_cov["last_date"])
    candidates = select_candidates(source_dir, args.candidate_source)

    import py_compile
    py_compile.compile(str(HERE), doraise=True)
    static = {
        "py_compile": "PASS",
        "mutation_helper_ast_sha": mutation_helper_ast_sha(),
        "mutation_helper_expected_sha": MUTATION_HELPER_EXPECTED_SHA,
        "mutation_helper_ast_sha_match": mutation_helper_ast_sha() == MUTATION_HELPER_EXPECTED_SHA,
    }
    if not static["mutation_helper_ast_sha_match"]:
        raise RuntimeError("mutation helper AST SHA mismatch")

    tasks: list[tuple[str, str, str, dict[str, Any], dict[str, Any]]] = []
    for cand in candidates:
        for period in periods:
            tasks.append((cand["candidate_id"], cand["candidate_hash"], cand["selection_role"], cand["rulebook"], period))

    with ProcessPoolExecutor(max_workers=int(args.workers), initializer=init_worker, initargs=(ticker, args.market_cutoff_date)) as pool:
        result_rows = list(pool.map(run_task, tasks, chunksize=1))
    result_rows.sort(key=lambda r: (r["candidate_id"], ["train_1", "train_2", "train_3", "recent_1y", "stress_pre_2022h1"].index(r["period_label"])))

    trade_rows: list[dict[str, Any]] = []
    for row in result_rows:
        trade_rows.extend(row.pop("trade_rows"))

    by_candidate: dict[str, dict[str, Any]] = defaultdict(lambda: {"periods": {}})
    for row in result_rows:
        c = by_candidate[row["candidate_id"]]
        c.update({"candidate_id": row["candidate_id"], "candidate_hash": row["candidate_hash"], "selection_role": row["selection_role"]})
        c["periods"][row["period_label"]] = row
    candidate_summaries: list[dict[str, Any]] = []
    for cid in sorted(by_candidate):
        c = by_candidate[cid]
        gates = [bool(c["periods"][label]["period_gate_pass"]) for label in OOS_GATE_LABELS]
        if all(gates):
            verdict = "OOS_PASS"
        elif not c["periods"]["recent_1y"]["period_gate_pass"]:
            verdict = "OOS_FAIL_RECENT"
        else:
            verdict = "OOS_FAIL_OTHER"
        train_exps = [safe_float(c["periods"][label]["summary"]["expectancy_pct"]) for label in ("train_1", "train_2", "train_3")]
        recent_exp = safe_float(c["periods"]["recent_1y"]["summary"]["expectancy_pct"])
        c["verdict"] = verdict
        c["in_sample_mean_expectancy_pct"] = statistics.mean(train_exps)
        c["recent_minus_in_sample_mean_expectancy_pct"] = recent_exp - c["in_sample_mean_expectancy_pct"]
        candidate_summaries.append(c)

    input_sha_end = source_input_sha(source_dir)
    protected_end = protected_sha()
    daemon_alive = Path("/proc/494330").is_dir()
    summary = {
        "run_id": out_dir.name,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "source_run_dir": str(source_dir.relative_to(REPO) if source_dir.is_relative_to(REPO) else source_dir),
        "candidate_source": args.candidate_source,
        "workers": int(args.workers),
        "market_cutoff_date": args.market_cutoff_date,
        "source_git_commit": args.source_git_commit,
        "criteria": {"oos_gate_labels": list(OOS_GATE_LABELS), "expectancy_threshold_pct": EXPECTANCY_THRESHOLD, "stress_gate_included": False},
        "data_coverage": data_cov,
        "static_checks": static,
        "source_input_sha_start": input_sha_start,
        "source_input_sha_end": input_sha_end,
        "protected_sha_start": protected_start,
        "protected_sha_end": protected_end,
        "daemon_alive_end": daemon_alive,
        "candidate_summaries": candidate_summaries,
    }

    write_json(out_dir / "oos_stress_summary.json", summary)
    write_jsonl(out_dir / "oos_stress_results.jsonl", result_rows)
    write_jsonl(out_dir / "oos_stress_trade_level.jsonl", trade_rows)
    (out_dir / "readout.md").write_text(build_markdown(summary, result_rows), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
