#!/usr/bin/env python3
"""Stage 3 strict-entry wiring wrapper.

원본 구현의 단계 순서와 exit/validate 경로는 그대로 유지하고 qualify와
entry만 strict entry scope에 연결한다. Strict entry 기술 feature는
신호일 D 기준 D-5 거래일 값으로 학습·생성·평가·interval-break를 정렬한다.
"""
from __future__ import annotations

import copy
import importlib.machinery
import importlib.util
import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np
import pandas as pd

_BACKUP_NAME = "run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001"
_BACKUP_PATH = Path(__file__).resolve().with_name(_BACKUP_NAME)
_MODULE_NAME = "_kingmaker_stage3_aggressive_original_20260706"
ENTRY_PHASE_CACHE_MODE = "entry_provisional_interval_break_d5_v2"
ENTRY_PHASE_MAX_HOLDING_DAYS = 7


def _load_original_module() -> Any:
    if not _BACKUP_PATH.exists():
        raise FileNotFoundError(f"Stage3 original backup is missing: {_BACKUP_PATH}")
    loader = importlib.machinery.SourceFileLoader(_MODULE_NAME, str(_BACKUP_PATH))
    spec = importlib.util.spec_from_loader(_MODULE_NAME, loader)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Stage3 original backup: {_BACKUP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_base = _load_original_module()

from engine.learning import execution_mode_backtest as _execution_backtest  # noqa: E402
from engine.strategies.evaluator import TECHNICAL_FEATURE_LAG_TRADING_DAYS  # noqa: E402
from engine.strategies.rulebook import (  # noqa: E402
    ENTRY_INTERVAL_MIN_FEATURE_SUPPORT,
    ENTRY_INTERVAL_SPECS,
)

TECHNICAL_FEATURE_LAG_MODE = f"strict_entry_d{TECHNICAL_FEATURE_LAG_TRADING_DAYS}_v1"


def _date_series(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        return pd.Series(pd.to_datetime(df["date"], errors="coerce").to_numpy(), index=df.index)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(df.index, errors="coerce"), index=df.index)
    raise ValueError("Stage3 entry feature domain requires a date column or DatetimeIndex")


def build_entry_feature_domain(
    ctx: Mapping[str, Any],
    *,
    start: str | None,
    end: str | None,
) -> dict[str, dict[str, Any]]:
    """한 train fold의 D-5 정렬 feature domain과 raw support values를 계산한다.

    원시 지표로 5개 feature series를 만든 뒤 전체 시계열에서 ``shift(5)``를
    적용하고 그 다음 fold 날짜를 자른다. 따라서 신호일 D의 domain/support
    값은 evaluator와 동일하게 D-5 거래일 행을 참조한다.
    """
    df = ctx.get("df")
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("ticker context df is missing or empty")

    frame = pd.DataFrame(index=df.index)
    frame["date"] = _date_series(df)

    def numeric(column: str) -> pd.Series:
        if column not in df.columns:
            return pd.Series(np.nan, index=df.index, dtype=float)
        return pd.to_numeric(df[column], errors="coerce")

    ma5 = numeric("MA5")
    ma20 = numeric("MA20")
    ma60 = numeric("MA60")
    close = numeric("Close")
    macd_hist = numeric("MACD_hist")
    bb_lower = numeric("BB_lower")
    bb_upper = numeric("BB_upper")

    frame["ma_trend"] = 0.5 * (((ma5 / ma20) - 1.0) + ((ma20 / ma60) - 1.0)) * 100.0
    frame["macd_hist"] = macd_hist / close * 100.0
    frame["rsi"] = numeric("RSI")
    frame["bb_position"] = (close - bb_lower) / (bb_upper - bb_lower)
    frame["volume_ratio"] = numeric("Volume_ratio")

    feature_names = tuple(ENTRY_INTERVAL_SPECS)
    frame.loc[:, list(feature_names)] = frame.loc[:, list(feature_names)].shift(
        TECHNICAL_FEATURE_LAG_TRADING_DAYS
    )

    if start is not None:
        frame = frame.loc[frame["date"] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame.loc[frame["date"] <= pd.Timestamp(end)]

    finite_mask = np.ones(len(frame), dtype=bool)
    for feature_name in feature_names:
        finite_mask &= np.isfinite(frame[feature_name].to_numpy(dtype=float))
    frame = frame.loc[finite_mask, ["date", *feature_names]].copy()
    if len(frame) < ENTRY_INTERVAL_MIN_FEATURE_SUPPORT:
        raise ValueError(
            f"entry feature fold has only {len(frame)} finite aligned rows; "
            f"minimum is {ENTRY_INTERVAL_MIN_FEATURE_SUPPORT}"
        )

    domain: dict[str, dict[str, Any]] = {}
    for feature_name in feature_names:
        values = frame[feature_name].to_numpy(dtype=float)
        domain[feature_name] = {
            "train_min": float(np.min(values)),
            "train_max": float(np.max(values)),
            "q01": float(np.quantile(values, 0.01)),
            "q99": float(np.quantile(values, 0.99)),
            "iqr": float(np.quantile(values, 0.75) - np.quantile(values, 0.25)),
            "sample_count": int(len(values)),
            "values": values.tolist(),
        }
    return domain


@contextmanager
def _entry_phase_execution_context() -> Iterator[None]:
    """D-5 strict daily tape를 entry-phase simulate_exit 호출에 주입한다."""
    original_builder = _execution_backtest._build_daily_signal_tape
    original_simulate_exit = _execution_backtest.simulate_exit
    state: dict[str, Any] = {}

    def build_tape(*args: Any, **kwargs: Any) -> Any:
        tape = original_builder(*args, **kwargs)
        state["signal_tape"] = tape
        return tape

    def simulate_entry_exit(*args: Any, **kwargs: Any) -> Any:
        tape = state.get("signal_tape")
        if tape is None:
            raise RuntimeError("entry-phase daily signal tape was not built before simulate_exit")
        kwargs["entry_phase_exit"] = True
        kwargs["entry_phase_signal_tape"] = tape
        kwargs["entry_phase_max_holding_days"] = ENTRY_PHASE_MAX_HOLDING_DAYS
        return original_simulate_exit(*args, **kwargs)

    _execution_backtest._build_daily_signal_tape = build_tape
    _execution_backtest.simulate_exit = simulate_entry_exit
    try:
        yield
    finally:
        _execution_backtest._build_daily_signal_tape = original_builder
        _execution_backtest.simulate_exit = original_simulate_exit


def run_entry_backtest_period(
    rulebook: Any,
    ctx: dict[str, Any],
    *,
    start: str | None,
    end: str | None,
) -> Any:
    """Qualify/entry 전용 D-5 strict provisional-exit backtest."""
    with _entry_phase_execution_context():
        return _base.run_backtest_execution_mode(
            rulebook,
            ctx["df"],
            start_date=start,
            end_date=end,
            **_base.base_backtest_kwargs(ctx),
            entry_execution_mode=_base.ENTRY_EXECUTION_MODE,
            exit_execution_mode=_base.EXIT_EXECUTION_MODE,
            fold_exit_policy=_base.FOLD_EXIT_POLICY,
            live_hard_stop_guard=_base.LIVE_HARD_STOP_GUARD,
        )


def _maybe_cached_entry_evaluate_fn(
    raw_evaluate_fn: Any,
    *,
    enabled: bool,
    ticker: str,
    period_label: str,
    start_date: Any,
    end_date: Any,
    fitness_mode: str,
    code_commit: str,
) -> tuple[Any, Any]:
    if not enabled:
        return raw_evaluate_fn, None
    cache = _base.FitnessCache()
    key_ctx = _base.make_cache_key_context(
        ticker=ticker,
        period_label=f"{period_label}|{ENTRY_PHASE_CACHE_MODE}",
        start_date=start_date,
        end_date=end_date,
        entry_execution_mode=_base.ENTRY_EXECUTION_MODE,
        exit_execution_mode=ENTRY_PHASE_CACHE_MODE,
        fold_exit_policy=_base.FOLD_EXIT_POLICY,
        fitness_mode=fitness_mode,
        code_commit=code_commit,
        add_buy_runtime_enabled=_base.ADD_BUY_RUNTIME_ENABLED,
    )
    return _base.make_cached_evaluate_fn(raw_evaluate_fn, cache=cache, key_ctx=key_ctx), cache


def run_qualify(
    ticker: str,
    out_dir: Path,
    *,
    seed_base: int,
    use_fitness_cache: bool = False,
    code_commit: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """D-5 entry-scope qualify with result-preserving cross-period early stop."""
    started = time.time()
    ctx = context if context is not None else _base.prepare_ticker_context(ticker)
    code_commit = code_commit or _base.resolve_code_commit(_base.PROJECT_ROOT)
    candidates_by_hash: dict[str, Any] = {}
    ga_summaries: list[dict[str, Any]] = []

    for idx, split in enumerate(_base.TRAIN_SPLITS, 1):
        split_seed = seed_base + idx
        entry_feature_domain = build_entry_feature_domain(ctx, start=split["start"], end=split["end"])

        def evaluate_fn(rulebook: Any, s: dict[str, str] = split) -> float:
            result = run_entry_backtest_period(rulebook, ctx, start=s["start"], end=s["end"])
            return _base.safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

        wrapped, fitness_cache = _maybe_cached_entry_evaluate_fn(
            evaluate_fn,
            enabled=use_fitness_cache,
            ticker=ticker,
            period_label=split["label"],
            start_date=split["start"],
            end_date=split["end"],
            fitness_mode="swing",
            code_commit=code_commit,
        )
        ga = _base.run_ga(
            base_rulebook=ctx["base_rulebook"],
            evaluate_fn=wrapped,
            ga_config=_base.make_ga_config(
                population=_base.QUALIFY_POPULATION,
                generations=_base.QUALIFY_GENERATIONS,
                seed=split_seed,
            ),
            gene_scope="entry",
            entry_feature_domain=entry_feature_domain,
        )
        top_rulebooks = _base.collect_top_rulebooks(ga, _base.TOP_N_QUALIFY)
        for rb in top_rulebooks:
            rulebook_hash = _base.compute_rulebook_hash(rb)
            current = candidates_by_hash.get(rulebook_hash)
            if current is None or _base.safe_float(getattr(rb, "fitness", 0.0)) > _base.safe_float(getattr(current, "fitness", 0.0)):
                candidates_by_hash[rulebook_hash] = copy.deepcopy(rb)
        ga_summaries.append({
            "split": split,
            "seed": split_seed,
            "gene_scope": "entry",
            "technical_feature_lag_mode": TECHNICAL_FEATURE_LAG_MODE,
            "technical_feature_lag_trading_days": TECHNICAL_FEATURE_LAG_TRADING_DAYS,
            "entry_domain_sample_count": min(int(v["sample_count"]) for v in entry_feature_domain.values()),
            "generations_run": getattr(ga, "generations_run", None),
            "top_count": len(top_rulebooks),
            "best_fitness": _base.safe_float(getattr(getattr(ga, "best", None), "fitness", 0.0)),
            "best_hash": _base.compute_rulebook_hash(ga.best) if getattr(ga, "best", None) is not None else None,
            "fitness_cache": _base.summarize_fitness_cache(fitness_cache),
        })

    candidate_hashes = sorted(candidates_by_hash)
    metrics_by_hash: dict[str, dict[str, dict[str, Any]]] = {h: {} for h in candidate_hashes}
    year_pass_counts: dict[str, int] = {}
    member_score_stats: dict[str, dict[str, Any]] = {}
    fail_reason_counter: Counter[str] = Counter()

    def write_result(*, qualified: bool, all3_pass_count: int, all3_pass_hash_samples: list[str], early_stopped: bool, early_stop_reason: dict[str, Any] | None) -> dict[str, Any]:
        result = {
            "ticker": ticker,
            "stage": "qualify",
            "qualified": bool(qualified),
            "config": _base.dataclasses.asdict(_base.DEFAULT_STAGE3_QUALIFY),
            "periods": list(_base.TRAIN_SPLITS),
            "seed_base": seed_base,
            "entry_execution_semantics": ENTRY_PHASE_CACHE_MODE,
            "technical_feature_lag_mode": TECHNICAL_FEATURE_LAG_MODE,
            "technical_feature_lag_trading_days": TECHNICAL_FEATURE_LAG_TRADING_DAYS,
            "market_context_lag_days": int(getattr(_base, "FEATURE_LAG_DAYS", 1)),
            "data_start": ctx.get("data_start"),
            "data_end": ctx.get("data_end"),
            "ga_summaries": ga_summaries,
            "fitness_cache": _base.aggregate_fitness_cache_summaries([row.get("fitness_cache", {}) for row in ga_summaries]),
            "unique_candidate_count": len(candidate_hashes),
            "year_pass_counts": year_pass_counts,
            "member_score_stats": member_score_stats,
            "all3_pass_count": int(all3_pass_count),
            "all3_pass_hash_samples": all3_pass_hash_samples,
            "fail_reason_metric_counts": dict(sorted(fail_reason_counter.items())),
            "early_stopped": bool(early_stopped),
            "early_stop_reason": early_stop_reason,
            "elapsed_seconds": time.time() - started,
            "note": "D-5 entry-scope qualify with shifted fold empirical domains and provisional entry exits",
        }
        _base.write_json(out_dir / "qualify_result.json", result)
        return result

    for split in _base.TRAIN_SPLITS:
        raw_rows: list[dict[str, Any]] = []
        for rank, rulebook_hash in enumerate(candidate_hashes, 1):
            result = run_entry_backtest_period(
                candidates_by_hash[rulebook_hash],
                ctx,
                start=split["start"],
                end=split["end"],
            )
            raw_rows.append({
                "ticker": ticker,
                "label": split["label"],
                "period_label": split["label"],
                "rulebook_hash": rulebook_hash,
                "rank_is": rank,
                "oos": _base.result_metrics(result),
            })
        scored = _base._score_period_candidates(raw_rows)
        pass_count = 0
        scores: list[float] = []
        for row in scored:
            rulebook_hash = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            metrics["member_score"] = _base.safe_float(row.get("oos_member_score"))
            metrics["fitness"] = _base.safe_float(row.get("fitness"))
            metrics_by_hash[rulebook_hash][split["label"]] = metrics
            scores.append(metrics["member_score"])
            if _base._pass_one_year(metrics):
                pass_count += 1
        year_pass_counts[split["label"]] = pass_count
        member_score_stats[split["label"]] = {
            "count": len(scores),
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "mean": sum(scores) / len(scores) if scores else None,
        }
        if pass_count <= 0:
            fail_reason_counter["early_stop_zero_pass_split"] += 1
            return write_result(
                qualified=False,
                all3_pass_count=0,
                all3_pass_hash_samples=[],
                early_stopped=True,
                early_stop_reason={
                    "split": split["label"],
                    "reason": "required_split_has_zero_passing_candidates",
                    "evaluated_split_count": len(year_pass_counts),
                },
            )

    all3_pass_count = 0
    samples: list[str] = []
    for rulebook_hash in candidate_hashes:
        reasons = _base.stage3_qualify_fail_reasons(metrics_by_hash.get(rulebook_hash, {}), _base.DEFAULT_STAGE3_QUALIFY)
        if not reasons:
            all3_pass_count += 1
            if len(samples) < 10:
                samples.append(rulebook_hash)
        else:
            for reason in reasons:
                fail_reason_counter[str(reason.get("metric") or "unknown")] += 1
    return write_result(
        qualified=all3_pass_count > 0,
        all3_pass_count=all3_pass_count,
        all3_pass_hash_samples=samples,
        early_stopped=False,
        early_stop_reason=None,
    )


def run_entry_ga(
    ticker: str,
    out_dir: Path,
    *,
    seed_base: int,
    use_fitness_cache: bool = False,
    code_commit: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 3 entry GA wired to D-5 strict entry scope and provisional exits."""
    qualify_path = out_dir / "qualify_result.json"
    if not qualify_path.exists():
        raise FileNotFoundError(f"missing prerequisite: {qualify_path}")
    qualify = json.loads(qualify_path.read_text(encoding="utf-8"))
    if not bool(qualify.get("qualified")):
        raise RuntimeError(f"ticker {ticker} did not pass Stage 3 qualification")

    started = time.time()
    ctx = context if context is not None else _base.prepare_ticker_context(ticker)
    code_commit = code_commit or _base.resolve_code_commit(_base.PROJECT_ROOT)
    train_3 = next(split for split in _base.TRAIN_SPLITS if split["label"] == "train_3")
    seed = seed_base + 100
    entry_feature_domain = build_entry_feature_domain(ctx, start=train_3["start"], end=train_3["end"])

    def evaluate_fn(rulebook: Any) -> float:
        result = run_entry_backtest_period(rulebook, ctx, start=train_3["start"], end=train_3["end"])
        return _base.safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

    wrapped, fitness_cache = _maybe_cached_entry_evaluate_fn(
        evaluate_fn,
        enabled=use_fitness_cache,
        ticker=ticker,
        period_label=train_3["label"],
        start_date=train_3["start"],
        end_date=train_3["end"],
        fitness_mode="swing",
        code_commit=code_commit,
    )
    ga = _base.run_ga(
        base_rulebook=ctx["base_rulebook"],
        evaluate_fn=wrapped,
        ga_config=_base.make_ga_config(
            population=_base.ENTRY_POPULATION,
            generations=_base.ENTRY_GENERATIONS,
            seed=seed,
        ),
        gene_scope="entry",
        entry_feature_domain=entry_feature_domain,
    )
    top_rulebooks = _base.collect_top_rulebooks(ga, _base.TOP_N_ENTRY_POOL)

    evaluated_rows: list[dict[str, Any]] = []
    for pool_rank, rb in enumerate(top_rulebooks, 1):
        result = run_entry_backtest_period(rb, ctx, start=train_3["start"], end=train_3["end"])
        metrics = _base.result_metrics(result)
        entry_dates = sorted(_base.entry_dates_from_trades(list(getattr(result, "trades", []) or [])))
        evaluated_rows.append({
            "ticker": ticker,
            "pool_rank": pool_rank,
            "rulebook_hash": _base.compute_rulebook_hash(rb),
            "train_period": train_3,
            "gene_scope": "entry",
            "technical_feature_lag_mode": TECHNICAL_FEATURE_LAG_MODE,
            "technical_feature_lag_trading_days": TECHNICAL_FEATURE_LAG_TRADING_DAYS,
            "entry_execution_semantics": ENTRY_PHASE_CACHE_MODE,
            "train_fitness": _base.safe_float(metrics.get("fitness")),
            "expectancy_pct": _base.safe_float(metrics.get("expectancy_pct")),
            "trade_count": _base.safe_int(metrics.get("trade_count")),
            "win_rate": _base.safe_float(metrics.get("win_rate")),
            "profit_factor": _base.safe_float(metrics.get("profit_factor")),
            "max_drawdown_pct": _base.safe_float(metrics.get("max_drawdown_pct")),
            "entry_date_count": len(entry_dates),
            "entry_dates": entry_dates,
            "rulebook": rb.to_dict(),
        })

    selected, rejected = _base._select_diverse_entry_rows(evaluated_rows, _base.DEFAULT_STAGE3_ENTRY_SELECTION)
    output_rows = []
    for rank, row in enumerate(selected, 1):
        output = dict(row)
        output["rank"] = rank
        output_rows.append(output)

    _base.append_jsonl(out_dir / "entry_rulebooks.jsonl", output_rows)
    _base.write_json(out_dir / "entry_rejected_overlap.json", rejected)
    summary = {
        "ticker": ticker,
        "stage": "entry",
        "seed": seed,
        "train_period": train_3,
        "gene_scope": "entry",
        "technical_feature_lag_mode": TECHNICAL_FEATURE_LAG_MODE,
        "technical_feature_lag_trading_days": TECHNICAL_FEATURE_LAG_TRADING_DAYS,
        "market_context_lag_days": int(getattr(_base, "FEATURE_LAG_DAYS", 1)),
        "entry_execution_semantics": ENTRY_PHASE_CACHE_MODE,
        "entry_domain_sample_count": min(int(v["sample_count"]) for v in entry_feature_domain.values()),
        "selection_config": _base.dataclasses.asdict(_base.DEFAULT_STAGE3_ENTRY_SELECTION),
        "pool_count": len(evaluated_rows),
        "absolute_pass_count": sum(1 for row in evaluated_rows if _base.safe_float(row.get("expectancy_pct")) >= _base.DEFAULT_STAGE3_ENTRY_SELECTION.entry_min_expectancy_pct),
        "selected_count": len(output_rows),
        "overlap_rejected_count": len(rejected),
        "fitness_cache": _base.summarize_fitness_cache(fitness_cache),
        "best_fitness": output_rows[0]["train_fitness"] if output_rows else None,
        "best_hash": output_rows[0]["rulebook_hash"] if output_rows else None,
        "elapsed_seconds": time.time() - started,
    }
    _base.write_json(out_dir / "entry_result.json", summary)
    return summary


# Qualify/entry만 교체한다. 원본 run_backtest_period, exit GA, validate는 불변이다.
_base.run_qualify = run_qualify
_base.run_entry_ga = run_entry_ga
_base.run_entry_backtest_period = run_entry_backtest_period


def main(argv: list[str] | None = None) -> int:
    return _base.main(argv)


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


if __name__ == "__main__":
    raise SystemExit(main())
