#!/usr/bin/env python3
"""Phase-3 six-worker AAP/POWI strict-AND interval experiment.

The runner dynamically loads only the redesign workspace versions of Rulebook,
GA, evaluator, and execution.  Production source files remain untouched.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT / "scripts/research/redesign_workspace_20260712"
OUT_DIR = ROOT / "data/_system/analysis/strict_and_interval_2sym_20260712"
MARKET_PATH = ROOT / "data/_system/market_history.csv"
MARKET_SHA256 = "35ad47a86528e5d9e5fae3c9fcf4958b70ee57c6daab61fcc7693915239e8c38"

TRAIN_SPLITS = [
    {"label": "train_1", "start": "2022-07-01", "end": "2023-06-30"},
    {"label": "train_2", "start": "2023-07-01", "end": "2024-06-30"},
    {"label": "train_3", "start": "2024-07-01", "end": "2025-06-30"},
]
STRESS_PERIOD = {"label": "stress_pre_2022h1", "start": None, "end": "2022-06-30"}
OOS_PERIOD = {"label": "oos_2025h2", "start": "2025-07-01", "end": None}
TICKERS = ["AAP", "POWI"]
MAX_WORKERS = 6
POPULATION = 36
GENERATIONS = 12
EARLY_STOP = 5
WARMUP = 200
SELECTION_SEED = 2026071203


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def load_workspace_module(name: str, relative_path: str):
    path = WORKSPACE / relative_path
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def bootstrap_workspace() -> dict[str, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import engine.learning  # noqa: F401
    import engine.strategies  # noqa: F401

    rulebook = load_workspace_module("engine.strategies.rulebook", "engine/strategies/rulebook.py")
    evaluator = load_workspace_module("engine.strategies.evaluator", "engine/strategies/evaluator.py")
    genetic = load_workspace_module("engine.learning.genetic", "engine/learning/genetic.py")
    execution = load_workspace_module(
        "engine.learning.execution_mode_backtest",
        "engine/learning/execution_mode_backtest.py",
    )

    market_context = importlib.import_module("engine.market.context")

    def readonly_get_market_history(years: int = 7):
        actual = sha256(MARKET_PATH)
        if actual != MARKET_SHA256:
            raise RuntimeError(f"market_history SHA mismatch: {actual}")
        frame = pd.read_csv(MARKET_PATH, index_col=0, parse_dates=True)
        if frame.empty or len(frame) != 1759:
            raise RuntimeError(f"invalid market history rows={len(frame)}")
        return market_context._merge_v2_events(frame)

    market_context.get_market_history = readonly_get_market_history
    pipeline_context = importlib.import_module("engine.pipeline.context")
    pipeline_context.get_market_history = readonly_get_market_history

    return {
        "rulebook": rulebook,
        "evaluator": evaluator,
        "genetic": genetic,
        "execution": execution,
        "prepare_ticker_context": pipeline_context.prepare_ticker_context,
    }


def task_seed(ticker: str, split_label: str) -> int:
    raw = f"strict-interval:{SELECTION_SEED}:{ticker}:{split_label}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)


def period_mask(index: pd.DatetimeIndex, start: str | None, end: str | None) -> np.ndarray:
    dates = pd.DatetimeIndex(pd.to_datetime(index, errors="coerce")).tz_localize(None).normalize()
    mask = np.ones(len(dates), dtype=bool)
    if start:
        mask &= dates >= pd.Timestamp(start)
    if end:
        mask &= dates <= pd.Timestamp(end)
    return mask


def classification_diagnostics(
    *,
    rulebook: Any,
    df: pd.DataFrame,
    market_history_df: pd.DataFrame,
    sector_name: str,
    period: dict[str, Any],
    execution: Any,
) -> dict[str, Any]:
    dates = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce")).tz_localize(None).normalize()
    mask = period_mask(dates, period.get("start"), period.get("end"))
    topic_map = execution._precompute_topic_feature_map(None, execution._news_zscore_window(rulebook))
    eligible = 0
    signal_count = 0
    positive_count = 0
    signal_positive_count = 0

    for index in range(max(WARMUP, 65), len(df) - 2):
        if not mask[index]:
            continue
        row = df.iloc[index]
        base_open = float(row.get("Open", row.get("Close")))
        if not math.isfinite(base_open) or base_open <= 0:
            continue
        future_high = max(float(df.iloc[index + 1]["High"]), float(df.iloc[index + 2]["High"]))
        label = future_high >= base_open * 1.03
        signal, _ = execution._context_at(
            rb=rulebook,
            df=df,
            index=index,
            market_score=50.0,
            sector_score=50.0,
            vix_level=18.0,
            market_history_df=market_history_df,
            sector_name=sector_name,
            ticker_sentiment=None,
            topic_feature_map=topic_map,
            use_llm_events=False,
        )
        eligible += 1
        positive_count += int(label)
        if signal.should_buy:
            signal_count += 1
            signal_positive_count += int(label)

    coverage = signal_count / eligible if eligible else 0.0
    precision = signal_positive_count / signal_count if signal_count else 0.0
    base_rate = positive_count / eligible if eligible else 0.0
    return {
        "period_label": period["label"],
        "eligible_days": eligible,
        "signal_count": signal_count,
        "signal_positive_count": signal_positive_count,
        "coverage": coverage,
        "precision": precision,
        "base_rate": base_rate,
        "signal_extinction_warning": coverage <= 0.002,
    }


def backtest_metrics(
    *,
    rulebook: Any,
    df: pd.DataFrame,
    market_history_df: pd.DataFrame,
    sector_name: str,
    period: dict[str, Any],
    execution: Any,
) -> tuple[Any, dict[str, Any]]:
    result = execution.run_backtest_execution_mode(
        rulebook,
        df,
        market_history_df=market_history_df,
        sector_name=sector_name,
        start_date=period.get("start"),
        end_date=period.get("end"),
        warmup=WARMUP,
        position_limit_krw=120_000.0,
        commission_rate=0.0005,
        cooldown_days=1,
        fitness_mode="daily_efficiency",
        use_llm_events=False,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="strict_interval_daily",
        fold_exit_policy="fold_end_mark_to_market",
    )
    metrics = {
        "trade_count": int(result.trade_count),
        "trade_win_rate_pct": float(result.win_rate),
        "expectancy_pct": float(result.expectancy_pct),
        "max_drawdown_pct": float(result.max_drawdown_pct),
        "fitness_daily_efficiency": float(result.fitness),
        "flat_signal_evaluations": int(getattr(result, "flat_signal_evaluations", 0)),
        "flat_signal_passes": int(getattr(result, "flat_signal_passes", 0)),
        "flat_signal_coverage": float(getattr(result, "flat_signal_coverage", 0.0)),
        "holding_signal_evaluations": int(getattr(result, "holding_signal_evaluations", 0)),
        "holding_signal_passes": int(getattr(result, "holding_signal_passes", 0)),
        "total_daily_signal_coverage": float(getattr(result, "total_daily_signal_coverage", 0.0)),
        "exit_reason_distribution": pd.Series(
            [trade.get("exit_reason") for trade in result.trades], dtype="object"
        ).value_counts().to_dict(),
    }
    return result, metrics


def validation_gate(metrics: dict[str, Any], train_precision: float) -> dict[str, Any]:
    minimum = max(8, int(math.ceil(int(metrics["eligible_days"]) * 0.015)))
    precision_floor = max(0.30, float(metrics["base_rate"]) + 0.03, float(train_precision) - 0.15)
    reasons: list[str] = []
    if int(metrics["signal_count"]) < minimum:
        reasons.append(f"signal_count<{minimum}")
    if float(metrics["precision"]) < precision_floor:
        reasons.append(f"precision<{precision_floor:.6f}")
    return {
        "passed": not reasons,
        "minimum_signal_count": minimum,
        "precision_floor": precision_floor,
        "reasons": reasons,
    }


def worker(task: dict[str, Any]) -> dict[str, Any]:
    modules = bootstrap_workspace()
    rbm = modules["rulebook"]
    gam = modules["genetic"]
    execution = modules["execution"]
    context = modules["prepare_ticker_context"](task["ticker"])
    df = context["df"]
    market_history_df = context["market_history_df"]
    sector_name = str(context.get("sector_name") or "tech")
    base = rbm.Rulebook.from_dict(context["base_rulebook"].to_dict())
    base.ticker = task["ticker"]
    base.sector_name = sector_name
    seed = task_seed(task["ticker"], task["split"]["label"])

    def evaluate(rulebook):
        result, _ = backtest_metrics(
            rulebook=rulebook,
            df=df,
            market_history_df=market_history_df,
            sector_name=sector_name,
            period={
                "label": task["split"]["label"],
                "start": task["split"]["start"],
                "end": task["split"]["end"],
            },
            execution=execution,
        )
        minimum_pass = max(20, int(math.ceil(int(getattr(result, "flat_signal_evaluations", 0)) * 0.02)))
        if int(getattr(result, "flat_signal_passes", 0)) < minimum_pass or int(result.trade_count) < 3:
            return -1000.0 + float(getattr(result, "flat_signal_passes", 0)) * 0.01 + float(result.fitness)
        return float(result.fitness)

    ga_result = gam.run_ga(
        base,
        evaluate,
        ga_config=gam.GAConfig(
            population=POPULATION,
            generations=GENERATIONS,
            elite_ratio=0.20,
            mutation_rate=0.25,
            mutation_strength=0.15,
            tournament_size=3,
            seed_pattern_ratio=0.0,
            early_stop_no_improve=EARLY_STOP,
            random_seed=seed,
        ),
    )
    structural_gate = gam.validate_population_intervals(
        [ga_result.best, *list(ga_result.final_population or [])]
    )
    if not structural_gate["passed"]:
        raise RuntimeError(f"post-GA structural gate failed: {structural_gate}")

    best = ga_result.best
    periods = {
        "train": {"label": task["split"]["label"], "start": task["split"]["start"], "end": task["split"]["end"]},
        "stress": STRESS_PERIOD,
        "oos": OOS_PERIOD,
    }
    results: dict[str, Any] = {}
    for role, period in periods.items():
        _, trade_metrics = backtest_metrics(
            rulebook=best,
            df=df,
            market_history_df=market_history_df,
            sector_name=sector_name,
            period=period,
            execution=execution,
        )
        classification = classification_diagnostics(
            rulebook=best,
            df=df,
            market_history_df=market_history_df,
            sector_name=sector_name,
            period=period,
            execution=execution,
        )
        results[role] = {**classification, **trade_metrics}

    train_minimum = max(20, int(math.ceil(results["train"]["eligible_days"] * 0.02)))
    train_gate = {
        "passed": results["train"]["signal_count"] >= train_minimum,
        "minimum_signal_count": train_minimum,
        "reasons": [] if results["train"]["signal_count"] >= train_minimum else [f"signal_count<{train_minimum}"],
    }
    stress_gate = validation_gate(results["stress"], results["train"]["precision"])
    oos_gate = validation_gate(results["oos"], results["train"]["precision"])
    survivor = bool(train_gate["passed"] and stress_gate["passed"] and oos_gate["passed"])

    return {
        "ticker": task["ticker"],
        "split_label": task["split"]["label"],
        "seed": seed,
        "sector_name": sector_name,
        "generations_run": int(ga_result.generations_run),
        "best_train_fitness": float(best.fitness),
        "entry_intervals": best.entry_intervals,
        "structural_gate": structural_gate,
        "train_gate": train_gate,
        "stress_gate": stress_gate,
        "oos_gate": oos_gate,
        "survivor": survivor,
        "metrics": results,
        "fitness_history": ga_result.fitness_history,
    }


def preflight_gate() -> dict[str, Any]:
    modules = bootstrap_workspace()
    rbm = modules["rulebook"]
    gam = modules["genetic"]
    random.seed(SELECTION_SEED)
    base = rbm.default_rulebook("PREFLIGHT")
    population = [gam.random_rulebook(base) for _ in range(1000)]
    initial = gam.validate_population_intervals(population)
    children = []
    for _ in range(1000):
        parent1, parent2 = random.sample(population, 2)
        children.append(gam.mutate(gam.crossover(parent1, parent2), 0.50, 0.20))
    offspring = gam.validate_population_intervals(children)
    return {
        "initial_population": initial,
        "offspring_population": offspring,
        "one_sided_or_nan_count": int(initial["invalid_count"]) + int(offspring["invalid_count"]),
        "passed": bool(initial["passed"] and offspring["passed"]),
    }


def flatten_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    row = {
        "ticker": candidate["ticker"],
        "split_label": candidate["split_label"],
        "seed": candidate["seed"],
        "generations_run": candidate["generations_run"],
        "best_train_fitness": candidate["best_train_fitness"],
        "structural_invalid_count": candidate["structural_gate"]["invalid_count"],
        "train_gate": candidate["train_gate"]["passed"],
        "stress_gate": candidate["stress_gate"]["passed"],
        "oos_gate": candidate["oos_gate"]["passed"],
        "survivor": candidate["survivor"],
    }
    for role in ("train", "stress", "oos"):
        metrics = candidate["metrics"][role]
        for key in (
            "eligible_days", "signal_count", "signal_positive_count", "coverage", "precision", "base_rate",
            "signal_extinction_warning", "trade_count", "trade_win_rate_pct", "expectancy_pct",
            "max_drawdown_pct", "fitness_daily_efficiency", "flat_signal_coverage",
            "holding_signal_evaluations", "holding_signal_passes", "total_daily_signal_coverage",
        ):
            row[f"{role}_{key}"] = metrics.get(key)
    row["stress_precision_floor"] = candidate["stress_gate"]["precision_floor"]
    row["oos_precision_floor"] = candidate["oos_gate"]["precision_floor"]
    return row


def build_readout(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Strict-AND interval 2종목 Phase 3",
        "",
        f"# 판정: **{summary['verdict']}**",
        "",
        "## Structural gate",
        "",
        f"- 초기 1,000개 invalid: `{summary['preflight']['initial_population']['invalid_count']}`",
        f"- 교배·변이 1,000개 invalid: `{summary['preflight']['offspring_population']['invalid_count']}`",
        f"- 편측/NaN interval: `{summary['preflight']['one_sided_or_nan_count']}`",
        "",
        "## 후보별 결과",
        "",
        "| ticker | split | Train coverage | Stress coverage / precision | OOS coverage / precision | Stress gate | OOS gate | Survivor |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {ticker} | {split_label} | {train_coverage:.2%} | {stress_coverage:.2%} / {stress_precision:.2%} | "
            "{oos_coverage:.2%} / {oos_precision:.2%} | {stress_gate} | {oos_gate} | {survivor} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## 집계",
            "",
            f"- 평균 Stress coverage: `{summary['aggregate']['stress_average_coverage']:.4%}`",
            f"- pooled Stress precision: `{summary['aggregate']['stress_pooled_precision']:.4%}`",
            f"- 평균 OOS coverage: `{summary['aggregate']['oos_average_coverage']:.4%}`",
            f"- pooled OOS precision: `{summary['aggregate']['oos_pooled_precision']:.4%}`",
            f"- signal-extinction 후보: `{summary['aggregate']['signal_extinction_candidates']}/6`",
            f"- Survivor: `{summary['aggregate']['survivor_count']}/6`",
            "",
            "## 기존 pilot 기준선",
            "",
            "```text",
            "Stress 평균 precision: 43.26%",
            "선택 pooled OOS precision: 58.06%",
            "선택 OOS coverage: 18.45%",
            "Survivor: 0/6",
            "```",
            "",
            "기술 feature는 D-5 완료봉, 시장 context는 D-1 이하, 진입은 D+1 open을 사용했다.",
            "MDD 사고/방치 분류 임계값은 적용하지 않았고 진단 로그만 기록했다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if OUT_DIR.exists():
        raise RuntimeError(f"output directory already exists: {OUT_DIR}")
    market_sha_before = sha256(MARKET_PATH)
    if market_sha_before != MARKET_SHA256:
        raise RuntimeError(f"market SHA mismatch before run: {market_sha_before}")

    preflight = preflight_gate()
    if not preflight["passed"]:
        raise RuntimeError(f"preflight structural gate failed: {preflight}")

    tasks = [
        {"ticker": ticker, "split": split}
        for ticker in TICKERS
        for split in TRAIN_SPLITS
    ]
    results: list[dict[str, Any]] = []
    context = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=MAX_WORKERS, mp_context=context) as executor:
        futures = {executor.submit(worker, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "completed": f"{task['ticker']}:{task['split']['label']}",
                        "survivor": result["survivor"],
                        "stress_precision": result["metrics"]["stress"]["precision"],
                        "oos_precision": result["metrics"]["oos"]["precision"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    results.sort(key=lambda item: (item["ticker"], item["split_label"]))
    rows = [flatten_candidate(candidate) for candidate in results]
    stress_signal_count = sum(row["stress_signal_count"] for row in rows)
    stress_positive = sum(row["stress_signal_positive_count"] for row in rows)
    oos_signal_count = sum(row["oos_signal_count"] for row in rows)
    oos_positive = sum(row["oos_signal_positive_count"] for row in rows)
    aggregate = {
        "stress_average_coverage": float(np.mean([row["stress_coverage"] for row in rows])),
        "stress_pooled_precision": stress_positive / stress_signal_count if stress_signal_count else 0.0,
        "oos_average_coverage": float(np.mean([row["oos_coverage"] for row in rows])),
        "oos_pooled_precision": oos_positive / oos_signal_count if oos_signal_count else 0.0,
        "signal_extinction_candidates": sum(
            bool(row["stress_signal_extinction_warning"] or row["oos_signal_extinction_warning"])
            for row in rows
        ),
        "survivor_count": sum(bool(row["survivor"]) for row in rows),
    }
    if aggregate["signal_extinction_candidates"] >= 4:
        verdict = "SIGNAL_EXTINCTION"
    elif aggregate["survivor_count"] > 0:
        verdict = "STRICT_AND_HELPS"
    else:
        verdict = "STRICT_AND_NO_SURVIVOR"

    summary = {
        "verdict": verdict,
        "preflight": preflight,
        "configuration": {
            "tickers": TICKERS,
            "train_splits": TRAIN_SPLITS,
            "max_workers": MAX_WORKERS,
            "population": POPULATION,
            "generations": GENERATIONS,
            "feature_lag_days": 5,
            "context_lag_days": 1,
            "entry_execution": "t_plus_1_open",
            "max_holding_days": 7,
            "fitness": "mean(pnl_pct / max(holding_days, 1))",
        },
        "aggregate": aggregate,
        "pilot_baseline": {
            "stress_average_precision": 0.4326,
            "oos_pooled_precision": 0.5806,
            "oos_coverage": 0.1845,
            "survivor_count": 0,
        },
        "candidates": results,
        "market_sha_before": market_sha_before,
        "market_sha_after": sha256(MARKET_PATH),
    }
    if summary["market_sha_after"] != MARKET_SHA256:
        raise RuntimeError("market history changed during Phase 3")

    OUT_DIR.mkdir(parents=True, exist_ok=False)
    pd.DataFrame(rows).to_csv(OUT_DIR / "candidate_metrics.csv", index=False)
    interval_rows = []
    for candidate in results:
        for feature, pair in candidate["entry_intervals"].items():
            interval_rows.append(
                {
                    "ticker": candidate["ticker"],
                    "split_label": candidate["split_label"],
                    "feature": feature,
                    "low": pair["low"],
                    "high": pair["high"],
                    "width": pair["high"] - pair["low"],
                }
            )
    pd.DataFrame(interval_rows).to_csv(OUT_DIR / "learned_intervals.csv", index=False)
    (OUT_DIR / "preflight_gate.json").write_text(json.dumps(json_safe(preflight), indent=2), encoding="utf-8")
    (OUT_DIR / "summary.json").write_text(json.dumps(json_safe(summary), indent=2), encoding="utf-8")
    (OUT_DIR / "readout.md").write_text(build_readout(summary, rows), encoding="utf-8")

    manifest_lines = []
    for path in sorted(OUT_DIR.iterdir()):
        if path.name == "manifest.sha256" or not path.is_file():
            continue
        manifest_lines.append(f"{sha256(path)}  {path.relative_to(ROOT)}")
    (OUT_DIR / "manifest.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    print(json.dumps(json_safe({"verdict": verdict, "aggregate": aggregate}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
