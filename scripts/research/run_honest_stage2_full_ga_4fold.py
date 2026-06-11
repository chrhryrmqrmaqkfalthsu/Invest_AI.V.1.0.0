"""Honest 6174 Stage 2 runner: full GA 4-fold batch.

This runner consumes a ticker batch file produced after Stage 0/1 cheap screening
and evaluates each ticker with execution-mode-aware GA/backtests.

Strict exclusions:
    - no stock_score gate
    - no rolling_validation OOS score gate
    - no oos_member_score / MIN_MEMBER_SCORE gate
    - no promoted parameters.json rulebook
    - no load_live_universe()
    - no member_score-based final selection

OHLCV must come from the Stage 0 disk cache. The runner does not call
prepare_ticker_context() and does not call yfinance/download paths for ticker
OHLCV.
"""
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import math
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.adapters.factory import get_adapter
from engine.core.metadata import compute_rulebook_hash
from engine.learning.execution_mode_backtest import run_backtest_execution_mode
from engine.learning.genetic import GAConfig, run_ga
from engine.learning.learner import _detect_sector_name
from engine.market.context import get_market_history
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.pipeline.context import (
    DEFAULT_MARKET_HISTORY_YEARS,
    calculate_adv_usd_252d,
    make_year_splits,
)
from engine.pipeline.full_training import (
    attach_member_metadata,
    build_member_payload,
    ga_config_to_plain_dict,
    ga_result_summary,
    member_score_distribution,
)
from engine.pipeline.scoring import score_full_training_members
from engine.strategies.rulebook import Rulebook, default_rulebook
from scripts.research.honest_run_notifications import HonestRunNotifier

DEFAULT_OUTPUT_ROOT = Path("data/_system/research/honest_full_6174_20260610")
DEFAULT_OHLCV_CACHE = DEFAULT_OUTPUT_ROOT / "stage0" / "ohlcv_cache"
TERMINAL_STATUSES = {"DONE", "FAILED", "ERROR"}
SELECTION_RULE_ID = "train_internal_stability_v0"
ENTRY_EXECUTION_MODE = "t_plus_1_open"
EXIT_EXECUTION_MODE = "conservative_core"
FOLD_EXIT_POLICY = "fold_end_mark_to_market"
FITNESS_MODE = "swing"
POSITION_LIMIT_KRW = 120_000.0
COMMISSION_RATE = 0.0005
WARMUP = 200
MAX_MEMBERS = 100
CACHE_FORMAT = "pkl"
STRESS_LABEL = "2025H2"

FOLDS = (
    {"label": "2022", "run_key_label": "2022", "year": 2022, "train_end": "2021-12-31", "test_start": "2022-01-01", "test_end": "2022-12-31", "is_stress": False},
    {"label": "2023", "run_key_label": "2023", "year": 2023, "train_end": "2022-12-31", "test_start": "2023-01-01", "test_end": "2023-12-31", "is_stress": False},
    {"label": "2024", "run_key_label": "2024", "year": 2024, "train_end": "2023-12-31", "test_start": "2024-01-01", "test_end": "2024-12-31", "is_stress": False},
    {"label": STRESS_LABEL, "run_key_label": "2025H2_STRESS", "year": STRESS_LABEL, "train_end": "2025-05-31", "test_start": "2025-06-01", "test_end": None, "is_stress": True},
)

BASE_STABILITY_WINDOWS = (
    ("2021", "2021-01-01", "2021-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025H1_TRAIN", "2025-01-01", "2025-05-31"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ticker(ticker: str) -> str:
    return str(ticker or "").upper().strip()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def load_tickers(path: Path) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [normalize_ticker(x) for x in data if normalize_ticker(x)]
        raise ValueError(f"unsupported json ticker file: {path}")
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        ticker = normalize_ticker(line.split(",")[0].strip())
        if ticker and not ticker.startswith("#"):
            out.append(ticker)
    return out


def atomic_json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def acquire_parent_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError(f"another parent process holds lock: {lock_path}") from exc
    fh.write(f"pid={os.getpid()} acquired_at={utc_now()}\n")
    fh.flush()
    return fh


def _date_bounds(df: pd.DataFrame) -> tuple[str | None, str | None]:
    if df is None or len(df) == 0:
        return None, None
    if isinstance(df.index, pd.DatetimeIndex):
        dates = pd.to_datetime(df.index, errors="coerce")
    elif "date" in df.columns:
        dates = pd.to_datetime(df["date"], errors="coerce")
    elif "Date" in df.columns:
        dates = pd.to_datetime(df["Date"], errors="coerce")
    else:
        dates = pd.to_datetime(pd.Series(df.index), errors="coerce")
    dates = pd.Series(dates).dropna()
    if dates.empty:
        return None, None
    return pd.Timestamp(dates.min()).strftime("%Y-%m-%d"), pd.Timestamp(dates.max()).strftime("%Y-%m-%d")


def _valid_ratio(series: pd.Series | None, *, positive: bool = False, non_negative: bool = False) -> float:
    if series is None or len(series) == 0:
        return 0.0
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.notna()
    if positive:
        valid = valid & (numeric > 0)
    if non_negative:
        valid = valid & (numeric >= 0)
    return float(valid.sum() / len(numeric)) if len(numeric) else 0.0


def _invalid_price_volume_ratio(df: pd.DataFrame) -> float:
    if df is None or len(df) == 0 or "Close" not in df.columns or "Volume" not in df.columns:
        return 1.0
    close = pd.to_numeric(df["Close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")
    invalid = close.isna() | volume.isna() | (close <= 0) | (volume <= 0)
    return float(invalid.sum() / len(df)) if len(df) else 1.0


def cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / f"{normalize_ticker(ticker)}.{CACHE_FORMAT}"


def context_from_cache(ticker: str, cache_dir: Path) -> dict[str, Any]:
    ticker = normalize_ticker(ticker)
    path = cache_path(cache_dir, ticker)
    if not path.exists():
        raise FileNotFoundError(f"missing Stage 0 OHLCV cache: {path}")
    df = pd.read_pickle(path)
    adapter = get_adapter(ticker)
    meta = adapter.meta
    data_start, data_end = _date_bounds(df)
    market_history_df = get_market_history(years=DEFAULT_MARKET_HISTORY_YEARS)
    ticker_sentiment = load_ticker_sentiment(ticker)
    sector_name = _detect_sector_name(meta.name)
    base_rulebook = default_rulebook(ticker, asset_type=meta.asset_type, direction=meta.direction)
    base_rulebook.sector_name = sector_name
    close_series = df["Close"] if df is not None and "Close" in df.columns else None
    volume_series = df["Volume"] if df is not None and "Volume" in df.columns else None
    splits = make_year_splits(data_min=data_start, data_max=data_end)
    return {
        "ticker": ticker,
        "adapter": adapter,
        "meta": meta,
        "df": df,
        "rows": int(len(df) if df is not None else 0),
        "data_min": data_start,
        "data_max": data_end,
        "data_start": data_start,
        "data_end": data_end,
        "valid_close_ratio": _valid_ratio(close_series, positive=True),
        "valid_volume_ratio": _valid_ratio(volume_series, non_negative=True),
        "invalid_price_volume_ratio": _invalid_price_volume_ratio(df),
        "splits": splits,
        "split_count": len(splits),
        "market_history_df": market_history_df,
        "ticker_sentiment": ticker_sentiment,
        "sentiment_days": len(ticker_sentiment or {}),
        "sector_name": sector_name,
        "base_rulebook": base_rulebook,
        "adv_usd_252d": calculate_adv_usd_252d(df),
        "sell_omen_score": {"available": "stage0_cached_df"},
    }


def make_ga_config(population: int, generations: int, ticker: str, fold_label: str) -> GAConfig:
    seed = 20260610 + sum(ord(ch) for ch in f"{ticker}|{fold_label}")
    return GAConfig(
        population=int(population),
        generations=int(generations),
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=8,
        random_seed=seed,
    )


def result_metrics(result: Any) -> dict[str, Any]:
    return {
        "trade_count": safe_int(getattr(result, "trade_count", 0)),
        "win_rate": safe_float(getattr(result, "win_rate", 0.0)),
        "expectancy_pct": safe_float(getattr(result, "expectancy_pct", 0.0)),
        "avg_return_pct": safe_float(getattr(result, "avg_return_pct", 0.0)),
        "profit_factor": safe_float(getattr(result, "profit_factor", 0.0)),
        "max_drawdown_pct": safe_float(getattr(result, "max_drawdown_pct", 0.0)),
        "sharpe_like": safe_float(getattr(result, "sharpe_like", 0.0)),
        "fitness": safe_float(getattr(result, "fitness", 0.0)),
        "win_count": safe_int(getattr(result, "win_count", 0)),
        "loss_count": safe_int(getattr(result, "loss_count", 0)),
    }


def run_backtest_cc(rb: Rulebook, ctx: Mapping[str, Any], *, start_date: str, end_date: str) -> Any:
    return run_backtest_execution_mode(
        rb,
        ctx["df"],
        start_date=start_date,
        end_date=end_date,
        position_limit_krw=POSITION_LIMIT_KRW,
        commission_rate=COMMISSION_RATE,
        warmup=WARMUP,
        market_history_df=ctx.get("market_history_df"),
        sector_name=ctx.get("sector_name", "tech"),
        ticker_sentiment=ctx.get("ticker_sentiment"),
        fitness_mode=FITNESS_MODE,
        use_llm_events=False,
        entry_execution_mode=ENTRY_EXECUTION_MODE,
        exit_execution_mode=EXIT_EXECUTION_MODE,
        fold_exit_policy=FOLD_EXIT_POLICY,
        live_hard_stop_guard=True,
    )


def run_fold_training_execution_mode(
    ticker: str,
    ctx: Mapping[str, Any],
    split: Mapping[str, Any],
    ga_config: GAConfig,
    max_members: int = MAX_MEMBERS,
    run_id: str | None = None,
) -> dict[str, Any]:
    started = time.time()
    ticker = normalize_ticker(ticker)
    train_start = str(ctx.get("data_start") or ctx.get("data_min") or "2020-01-01")
    train_end = str(split["train_end"])
    train_period = [train_start, train_end]
    base_rulebook = ctx["base_rulebook"]

    def evaluate_fn(rb: Rulebook) -> float:
        result = run_backtest_cc(rb, ctx, start_date=train_start, end_date=train_end)
        return safe_float(getattr(result, "fitness", -1_000_000.0), -1_000_000.0)

    ga_result = run_ga(base_rulebook=base_rulebook, evaluate_fn=evaluate_fn, ga_config=ga_config)
    sorted_population = sorted(
        list(getattr(ga_result, "final_population", []) or []),
        key=lambda rb: getattr(rb, "fitness", None) if getattr(rb, "fitness", None) is not None else -1e18,
        reverse=True,
    )[: max(0, int(max_members))]

    member_payloads: list[dict[str, Any]] = []
    member_results: dict[str, Any] = {}
    for rank, rb in enumerate(sorted_population, 1):
        re_eval = run_backtest_cc(rb, ctx, start_date=train_start, end_date=train_end)
        payload = build_member_payload(rb, re_eval, rank)
        member_payloads.append(payload)
        member_results[payload["rulebook_hash"]] = re_eval

    scored_members = score_full_training_members(member_payloads)
    scored_members = attach_member_metadata(
        scored_members,
        ticker=ticker,
        run_id=run_id or f"{ticker}|{split['run_key_label']}",
        train_period=train_period,
        ga_cfg=ga_config,
        ga_result=ga_result,
    )
    return {
        "ticker": ticker,
        "stage": "honest_stage2_fold_training_execution_mode",
        "run_id": run_id or f"{ticker}|{split['run_key_label']}",
        "fold_label": split["label"],
        "is_stress": bool(split.get("is_stress")),
        "train_period": train_period,
        "data_start": ctx.get("data_start"),
        "data_end": ctx.get("data_end"),
        "adv_usd_252d": ctx.get("adv_usd_252d"),
        "sentiment_days": ctx.get("sentiment_days"),
        "sector_name": ctx.get("sector_name"),
        "ga_config": ga_config_to_plain_dict(ga_config),
        "ga": ga_result_summary(ga_result),
        "member_count": len(scored_members),
        "qualified_count": sum(1 for m in scored_members if m.get("qualified")),
        "member_score_distribution": member_score_distribution(scored_members),
        "members": scored_members,
        "member_train_results": member_results,
        "elapsed_sec": time.time() - started,
    }


def rulebook_from_member(member: Mapping[str, Any]) -> Rulebook:
    rb = member.get("rulebook")
    if not isinstance(rb, Mapping):
        raise ValueError(f"member has no rulebook dict: rank={member.get('rank')}")
    return Rulebook.from_dict(dict(rb))


def stability_windows_for_train(train_end: str) -> list[tuple[str, str, str]]:
    train_end_ts = pd.Timestamp(train_end)
    return [(label, start, end) for label, start, end in BASE_STABILITY_WINDOWS if pd.Timestamp(end) <= train_end_ts]


def evaluate_member_stability(member: Mapping[str, Any], ctx: Mapping[str, Any], *, train_end: str) -> dict[str, Any]:
    rb = rulebook_from_member(member)
    windows = stability_windows_for_train(train_end)
    metrics_rows: list[dict[str, Any]] = []
    positive_expectancy_count = 0
    positive_pf_count = 0
    total_trades = 0
    expectancies: list[float] = []
    pfs: list[float] = []
    drawdowns: list[float] = []
    for label, start, end in windows:
        result = run_backtest_cc(rb, ctx, start_date=start, end_date=end)
        metrics = result_metrics(result)
        metrics.update({"label": label, "start_date": start, "end_date": end})
        metrics_rows.append(metrics)
        trades = safe_int(metrics.get("trade_count"))
        exp = safe_float(metrics.get("expectancy_pct"))
        pf = safe_float(metrics.get("profit_factor"))
        dd = safe_float(metrics.get("max_drawdown_pct"))
        total_trades += trades
        expectancies.append(exp)
        pfs.append(pf)
        drawdowns.append(dd)
        if trades > 0 and exp > 0:
            positive_expectancy_count += 1
        if trades > 0 and pf > 1.0:
            positive_pf_count += 1
    full = member.get("train_metrics") or {}
    return {
        "rank": member.get("rank"),
        "member_hash": member.get("member_hash"),
        "rulebook_hash": member.get("rulebook_hash"),
        "member_score_present_but_not_used": member.get("member_score"),
        "fitness": member.get("fitness"),
        "train_metrics_full": full,
        "windows": metrics_rows,
        "stability_window_count": len(metrics_rows),
        "positive_expectancy_window_count": positive_expectancy_count,
        "positive_pf_window_count": positive_pf_count,
        "total_window_trades": total_trades,
        "min_expectancy_pct": round(min(expectancies), 6) if expectancies else 0.0,
        "avg_expectancy_pct": round(sum(expectancies) / len(expectancies), 6) if expectancies else 0.0,
        "min_profit_factor": round(min(pfs), 6) if pfs else 0.0,
        "avg_profit_factor": round(sum(pfs) / len(pfs), 6) if pfs else 0.0,
        "worst_drawdown_pct": round(min(drawdowns), 6) if drawdowns else 0.0,
    }


def stability_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    full = row.get("train_metrics_full") or {}
    return (
        safe_int(row.get("positive_expectancy_window_count")),
        safe_int(row.get("positive_pf_window_count")),
        safe_int(row.get("total_window_trades")),
        safe_float(row.get("min_expectancy_pct")),
        safe_float(row.get("min_profit_factor")),
        safe_float(row.get("worst_drawdown_pct")),
        safe_float(full.get("profit_factor")),
        safe_float(full.get("expectancy_pct")),
        -safe_int(row.get("rank"), 999999),
    )


def select_stable_member(members: list[dict[str, Any]], ctx: Mapping[str, Any], *, train_end: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    stability_rows = [evaluate_member_stability(member, ctx, train_end=train_end) for member in members]
    if not stability_rows:
        return None, []
    ordered = sorted(stability_rows, key=stability_sort_key, reverse=True)
    selected = dict(ordered[0])
    selected["selection_rule_id"] = SELECTION_RULE_ID
    selected["selection_note"] = "selected_by_train_internal_stability_not_member_score"
    return selected, ordered


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if dataclasses.is_dataclass(value):
        try:
            return _json_safe(dataclasses.asdict(value))
        except Exception:
            return str(value)
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return _json_safe({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
    return str(value)


def run_key_for(ticker: str, split: Mapping[str, Any]) -> str:
    return f"{normalize_ticker(ticker)}|{split['run_key_label']}"


def append_fold_outputs(batch_dir: Path, topn_row: dict[str, Any], rulebook_rows: list[dict[str, Any]], trade_rows: list[dict[str, Any]], selected_row: dict[str, Any] | None) -> None:
    append_jsonl(batch_dir / "topn.jsonl", topn_row)
    for row in rulebook_rows:
        append_jsonl(batch_dir / "topn_rulebooks.jsonl", row)
    for row in trade_rows:
        append_jsonl(batch_dir / "trades.jsonl", row)
    if selected_row:
        append_jsonl(batch_dir / "selected.jsonl", selected_row)


def build_rulebook_rows(run_key: str, ticker: str, split: Mapping[str, Any], members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for member in members:
        rb = member.get("rulebook") or {}
        rows.append(
            {
                "run_key": run_key,
                "ticker": ticker,
                "label": split["label"],
                "fold_label": split["label"],
                "is_stress": bool(split.get("is_stress")),
                "rank": member.get("rank"),
                "member_hash": member.get("member_hash"),
                "rulebook_hash": member.get("rulebook_hash") or compute_rulebook_hash(rb),
                "rulebook": rb,
                "train_metrics": member.get("train_metrics"),
                "member_score_present_but_not_used": member.get("member_score"),
            }
        )
    return rows


def build_trade_rows(run_key: str, ticker: str, split: Mapping[str, Any], candidate_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidate_results:
        for trade in candidate.get("trades", []) or []:
            row = dict(trade)
            row.update(
                {
                    "run_key": run_key,
                    "ticker": ticker,
                    "label": split["label"],
                    "fold_label": split["label"],
                    "is_stress": bool(split.get("is_stress")),
                    "fold_end": split["test_end"],
                    "rank": candidate.get("rank"),
                    "rulebook_hash": candidate.get("rulebook_hash"),
                }
            )
            out.append(row)
    return out


def process_ticker(ticker: str, args_dict: dict[str, Any], batch_dir_str: str) -> dict[str, Any]:
    started = time.time()
    ticker = normalize_ticker(ticker)
    batch_dir = Path(batch_dir_str)
    cache_dir = Path(args_dict["ohlcv_cache"])
    population = int(args_dict["population"])
    generations = int(args_dict["generations"])
    try:
        ctx = context_from_cache(ticker, cache_dir)
        data_end = str(ctx.get("data_end") or ctx.get("data_max") or "")
        fold_summaries: list[dict[str, Any]] = []
        for split_base in FOLDS:
            split = dict(split_base)
            split["train_start"] = ctx.get("data_start") or ctx.get("data_min") or "2020-01-01"
            if split.get("is_stress"):
                split["test_end"] = data_end
            run_key = run_key_for(ticker, split)
            fold_started = time.time()
            ga_cfg = make_ga_config(population, generations, ticker, str(split["run_key_label"]))
            training = run_fold_training_execution_mode(
                ticker,
                ctx,
                split,
                ga_config=ga_cfg,
                max_members=MAX_MEMBERS,
                run_id=f"{args_dict['run_id']}|{run_key}",
            )
            members = list(training.get("members", []) or [])
            selected, stability_ranked = select_stable_member(members, ctx, train_end=str(split["train_end"]))
            candidate_results: list[dict[str, Any]] = []
            for member in members:
                rb = rulebook_from_member(member)
                result = run_backtest_cc(rb, ctx, start_date=str(split["test_start"]), end_date=str(split["test_end"]))
                candidate_results.append(
                    {
                        "rank": member.get("rank"),
                        "member_hash": member.get("member_hash"),
                        "rulebook_hash": member.get("rulebook_hash"),
                        "oos_metrics": result_metrics(result),
                        "trades": list(getattr(result, "trades", []) or []),
                    }
                )
            topn_row = {
                "run_key": run_key,
                "created_at": utc_now(),
                "ticker": ticker,
                "label": split["label"],
                "fold_label": split["label"],
                "is_stress": bool(split.get("is_stress")),
                "split": split,
                "config": {
                    "population": population,
                    "generations": generations,
                    "fitness_mode": FITNESS_MODE,
                    "entry_execution_mode": ENTRY_EXECUTION_MODE,
                    "exit_execution_mode": EXIT_EXECUTION_MODE,
                    "fold_exit_policy": FOLD_EXIT_POLICY,
                    "selection_rule_id": SELECTION_RULE_ID,
                    "stock_score_gate_used": False,
                    "rolling_oos_score_used": False,
                    "uses_member_score": False,
                },
                "ga": training.get("ga"),
                "member_count": training.get("member_count"),
                "qualified_count": training.get("qualified_count"),
                "member_score_distribution_diagnostic_only": training.get("member_score_distribution"),
                "candidate_results": [{k: v for k, v in row.items() if k != "trades"} for row in candidate_results],
                "selected": selected,
                "stability_ranked": stability_ranked,
                "elapsed_sec": time.time() - fold_started,
            }
            selected_row = None
            if selected:
                selected_row = {
                    "run_key": run_key,
                    "ticker": ticker,
                    "label": split["label"],
                    "fold_label": split["label"],
                    "is_stress": bool(split.get("is_stress")),
                    "selection_rule_id": SELECTION_RULE_ID,
                    "uses_member_score": False,
                    "selected": selected,
                }
            append_fold_outputs(
                batch_dir,
                topn_row,
                build_rulebook_rows(run_key, ticker, split, members),
                build_trade_rows(run_key, ticker, split, candidate_results),
                selected_row,
            )
            fold_summaries.append(
                {
                    "run_key": run_key,
                    "label": split["label"],
                    "is_stress": bool(split.get("is_stress")),
                    "member_count": training.get("member_count"),
                    "selected": bool(selected),
                    "candidate_trade_count": sum(safe_int(c.get("oos_metrics", {}).get("trade_count")) for c in candidate_results),
                    "fold_end": split["test_end"],
                    "elapsed_sec": time.time() - fold_started,
                }
            )
        return {
            "ticker": ticker,
            "status": "DONE",
            "data_start": ctx.get("data_start"),
            "data_end": ctx.get("data_end"),
            "rows": ctx.get("rows"),
            "folds": fold_summaries,
            "elapsed_sec": time.time() - started,
        }
    except Exception as exc:
        return {
            "ticker": ticker,
            "status": "ERROR",
            "error": {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(limit=8)},
            "elapsed_sec": time.time() - started,
        }


def infer_batch_index(run_id: str, tickers_path: Path, explicit: str | None) -> str:
    if explicit:
        return str(explicit).zfill(3) if str(explicit).isdigit() else str(explicit)
    for text in [run_id, tickers_path.name]:
        m = re.search(r"(?:batch[_-]?|b)(\d+)", text, re.IGNORECASE)
        if m:
            return str(int(m.group(1))).zfill(3)
    return "000"


def load_progress(progress_path: Path, tickers: list[str]) -> dict[str, dict[str, Any]]:
    if progress_path.exists():
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {normalize_ticker(k): dict(v) for k, v in data.items() if normalize_ticker(k)}
        except Exception:
            pass
    return {ticker: {"status": "PENDING"} for ticker in tickers}


def save_progress(progress_path: Path, progress: dict[str, dict[str, Any]]) -> None:
    atomic_json_write(progress_path, progress)


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def build_summary(batch_dir: Path, progress: dict[str, dict[str, Any]], rows: list[dict[str, Any]], args: argparse.Namespace, batch_index: str) -> dict[str, Any]:
    topn_rows = 0
    labels = set()
    if (batch_dir / "topn.jsonl").exists():
        for line in (batch_dir / "topn.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                topn_rows += 1
                try:
                    labels.add(json.loads(line).get("fold_label"))
                except Exception:
                    pass
    done = sum(1 for item in progress.values() if item.get("status") == "DONE")
    errors = sum(1 for item in progress.values() if item.get("status") == "ERROR")
    return {
        "run_id": args.run_id,
        "batch_index": batch_index,
        "created_at": utc_now(),
        "input_ticker_source": str(args.tickers),
        "batch_size": len(progress),
        "max_workers": args.max_workers,
        "resume": bool(args.resume),
        "population": args.population,
        "generations": args.generations,
        "ohlcv_cache": str(args.ohlcv_cache),
        "counts": {
            "done": done,
            "errors": errors,
            "terminal": done + errors,
            "input_tickers": len(progress),
            "topn_rows": topn_rows,
            "selected_rows": count_jsonl(batch_dir / "selected.jsonl"),
            "expected_topn_rows_if_complete": len(progress) * len(FOLDS),
        },
        "labels_seen": sorted([str(x) for x in labels if x]),
        "honesty_flags": {
            "stock_score_gate_used": False,
            "stock_score_cutoff_used": False,
            "rolling_oos_score_used": False,
            "uses_member_score": False,
            "candidate_filter_score_used": False,
            "uses_train_internal_windows_only": True,
            "selection_rule_id": SELECTION_RULE_ID,
            "promoted_rulebook_used": False,
            "parameters_json_rulebook_used": False,
            "load_live_universe_used": False,
            "oos_member_score_gate_used": False,
        },
        "outputs": {
            "batch_dir": str(batch_dir),
            "topn": str(batch_dir / "topn.jsonl"),
            "rulebooks": str(batch_dir / "topn_rulebooks.jsonl"),
            "trades": str(batch_dir / "trades.jsonl"),
            "selected": str(batch_dir / "selected.jsonl"),
            "summary": str(batch_dir / "summary.json"),
            "progress": str(batch_dir / "progress.json"),
        },
        "rows": rows,
        "passed_batch_smoke_or_complete": (done + errors) == len(progress) and errors == 0,
    }


def honesty_flags_ok(summary: dict[str, Any]) -> bool:
    flags = summary.get("honesty_flags") or {}
    return (
        flags.get("stock_score_gate_used") is False
        and flags.get("stock_score_cutoff_used") is False
        and flags.get("rolling_oos_score_used") is False
        and flags.get("uses_member_score") is False
        and flags.get("candidate_filter_score_used") is False
        and flags.get("uses_train_internal_windows_only") is True
        and flags.get("selection_rule_id") == SELECTION_RULE_ID
        and flags.get("promoted_rulebook_used") is False
        and flags.get("parameters_json_rulebook_used") is False
        and flags.get("load_live_universe_used") is False
        and flags.get("oos_member_score_gate_used") is False
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--population", type=int, default=100)
    parser.add_argument("--generations", type=int, default=50)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--ohlcv-cache", type=Path, default=DEFAULT_OHLCV_CACHE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--batch-index", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--notify-every", type=int, default=100, help="telegram progress interval by completed ticker count")
    parser.add_argument("--notify-pct", type=float, default=10.0, help="telegram progress interval by percent bucket")
    parser.add_argument("--notify-error-threshold", type=int, default=50, help="send one aggregate error alert after this many ticker errors")
    return parser.parse_args()


def maybe_send_error_threshold(notifier: HonestRunNotifier | None, *, error_count: int, threshold: int, sent: bool, context: str) -> bool:
    if sent:
        return True
    if threshold <= 0:
        return False
    if error_count >= threshold:
        if notifier:
            notifier.error(
                ticker="-",
                error=f"aggregate_error_count={error_count} reached threshold={threshold}; see ticker_results.jsonl for ticker-level details",
                context=context,
            )
        return True
    return False


def main() -> int:
    args = parse_args()
    batch_index = infer_batch_index(args.run_id, args.tickers, args.batch_index)
    batch_dir = Path(args.output_root) / f"stage2_batch_{batch_index}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    notifier: HonestRunNotifier | None = None
    lock_fh = acquire_parent_lock(batch_dir / ".stage2_parent.lock")
    try:
        tickers = load_tickers(args.tickers)
        if args.limit and args.limit > 0:
            tickers = tickers[: args.limit]
        tickers = list(dict.fromkeys(tickers))
        notifier = HonestRunNotifier(
            run_id=args.run_id,
            stage="stage2_full_ga_4fold",
            batch_index=batch_index,
            total=len(tickers),
            notify_every=args.notify_every,
            notify_pct=args.notify_pct,
        )
        notifier.start(
            total=len(tickers),
            batch_index=batch_index,
            extra={
                "population": args.population,
                "generations": args.generations,
                "max_workers": args.max_workers,
                "resume": bool(args.resume),
                "ohlcv_cache": str(args.ohlcv_cache),
                "progress_interval": f"{args.notify_every} tickers or {args.notify_pct:g}%",
                "ticker_error_alert": f"aggregate only at {args.notify_error_threshold} errors",
            },
        )
        progress_path = batch_dir / "progress.json"
        progress = load_progress(progress_path, tickers)
        for ticker in tickers:
            progress.setdefault(ticker, {"status": "PENDING"})
        pending = [ticker for ticker in tickers if progress.get(ticker, {}).get("status") not in TERMINAL_STATUSES]
        for ticker in pending:
            progress[ticker] = {"status": "RUNNING", "claimed_at": utc_now(), "run_id": args.run_id}
        save_progress(progress_path, progress)

        completed_count = len(tickers) - len(pending)
        error_count = sum(1 for item in progress.values() if item.get("status") == "ERROR")
        selected_count = count_jsonl(batch_dir / "selected.jsonl")
        error_threshold_sent = maybe_send_error_threshold(
            notifier,
            error_count=error_count,
            threshold=args.notify_error_threshold,
            sent=False,
            context="stage2_error_threshold_resume",
        )
        if notifier and completed_count:
            notifier.progress(done=completed_count, selected=selected_count, errors=error_count)

        rows: list[dict[str, Any]] = []
        args_dict = {
            "run_id": args.run_id,
            "population": args.population,
            "generations": args.generations,
            "ohlcv_cache": str(args.ohlcv_cache),
        }
        with ProcessPoolExecutor(max_workers=max(1, int(args.max_workers))) as executor:
            futures = {executor.submit(process_ticker, ticker, args_dict, str(batch_dir)): ticker for ticker in pending}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = {
                        "ticker": ticker,
                        "status": "ERROR",
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    }
                append_jsonl(batch_dir / "ticker_results.jsonl", row)
                progress[ticker] = {
                    "status": "DONE" if row.get("status") == "DONE" else "ERROR",
                    "finished_at": utc_now(),
                    "elapsed_sec": row.get("elapsed_sec"),
                }
                if row.get("status") != "DONE":
                    progress[ticker]["error"] = row.get("error")
                save_progress(progress_path, progress)
                rows.append(row)
                completed_count += 1
                if row.get("status") != "DONE":
                    error_count += 1
                    error_threshold_sent = maybe_send_error_threshold(
                        notifier,
                        error_count=error_count,
                        threshold=args.notify_error_threshold,
                        sent=error_threshold_sent,
                        context="stage2_error_threshold",
                    )
                selected_count = count_jsonl(batch_dir / "selected.jsonl")
                if notifier:
                    notifier.progress(done=completed_count, selected=selected_count, errors=error_count)
        if (batch_dir / "ticker_results.jsonl").exists():
            all_rows = []
            for line in (batch_dir / "ticker_results.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        all_rows.append(json.loads(line))
                    except Exception:
                        pass
        else:
            all_rows = rows
        summary = build_summary(batch_dir, progress, all_rows, args, batch_index)
        atomic_json_write(batch_dir / "summary.json", summary)
        if notifier:
            notifier.complete(
                total=summary["counts"]["input_tickers"],
                selected=summary["counts"].get("selected_rows", 0),
                errors=summary["counts"].get("errors", 0),
                elapsed_sec=time.time() - started_at,
                honesty_ok=honesty_flags_ok(summary),
                extra={
                    "topn_rows": summary["counts"].get("topn_rows"),
                    "labels_seen": ",".join(summary.get("labels_seen") or []),
                },
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
        return 0 if summary["counts"]["terminal"] == summary["counts"]["input_tickers"] and summary["counts"]["errors"] == 0 else 1
    except Exception as exc:
        if notifier:
            notifier.error(ticker="-", error=f"{type(exc).__name__}: {exc}", context="stage2_main_crash")
        raise
    finally:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            lock_fh.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
