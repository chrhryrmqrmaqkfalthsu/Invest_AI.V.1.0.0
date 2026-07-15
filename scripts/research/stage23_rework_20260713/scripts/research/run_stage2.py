#!/usr/bin/env python3
"""Ticker-agnostic Stage 2 research runner.

Usage example:
    venv/bin/python scripts/research/run_stage2.py --ticker MPLX

This runner is intentionally self-contained and does not import any historical
exp_<ticker>_stage2_* run script.  The finalized Stage 2 gate is centralized in
engine.pipeline.stage2_gate and is the only gate implementation used here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import dataclasses
import json
import logging
import math
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Fail closed against accidentally importing the repository-root ``engine`` package.
# Stage2 rework must use the colocated Stage3 rework engine modules.
if str(PROJECT_ROOT) in sys.path:
    sys.path.remove(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from engine.core.metadata import compute_rulebook_hash
from engine.learning.execution_mode_backtest import run_backtest_execution_mode
from engine.learning.genetic import GAConfig, run_ga
from engine.learning.fitness_cache import (
    FitnessCache,
    aggregate_fitness_cache_summaries,
    resolve_fitness_cache_enabled,
    make_cache_key_context,
    make_cached_evaluate_fn,
    resolve_code_commit,
    summarize_fitness_cache,
)
from engine.pipeline.context import prepare_ticker_context
from engine.pipeline.stage2_gate import DEFAULT_STAGE2_GATE, stage2_fail_reasons
from engine.pipeline.topn_survivor import _score_period_candidates
from engine.strategies.evaluator import TECHNICAL_FEATURE_LAG_TRADING_DAYS
from engine.strategies.rulebook import ENTRY_INTERVAL_MIN_FEATURE_SUPPORT, ENTRY_INTERVAL_SPECS, Rulebook

POPULATION = 100
GENERATIONS = 50
PATIENCE = 15
POSITION_LIMIT_KRW = 120_000.0
ENTRY_EXECUTION_MODE = "t_plus_1_open"
EXIT_EXECUTION_MODE = "conservative_core"
FOLD_EXIT_POLICY = "fold_end_mark_to_market"
LIVE_HARD_STOP_GUARD = True
ADD_BUY_RUNTIME_ENABLED = False
ENTRY_PHASE_CACHE_MODE = "entry_provisional_interval_break_d5_v2"
ENTRY_PHASE_MAX_HOLDING_DAYS = 7
TECHNICAL_FEATURE_LAG_MODE = f"strict_entry_d{TECHNICAL_FEATURE_LAG_TRADING_DAYS}_v1"
STAGE2_EXECUTION_SEMANTICS = "stage3_entry_phase_d5_strict_interval_v1"

TRAIN_SPLITS: list[dict[str, str]] = [
    {"label": "train_1", "train_start": "2022-07-01", "train_end": "2023-06-30"},
    {"label": "train_2", "train_start": "2023-07-01", "train_end": "2024-06-30"},
    {"label": "train_3", "train_start": "2024-07-01", "train_end": "2025-06-30"},
]

# early-cut order: ~22.6 -> ③ -> ② -> ① -> 25.7~
PERIODS_TEMPLATE: list[dict[str, Any]] = [
    {"label": "stress_pre_2022h1", "kind": "stress", "start": None, "end": "2022-06-30", "order": 1},
    {"label": "train_3_eval", "kind": "train", "start": "2024-07-01", "end": "2025-06-30", "order": 2},
    {"label": "train_2_eval", "kind": "train", "start": "2023-07-01", "end": "2024-06-30", "order": 3},
    {"label": "train_1_eval", "kind": "train", "start": "2022-07-01", "end": "2023-06-30", "order": 4},
    {"label": "oos_2025h2", "kind": "oos", "start": "2025-07-01", "end": None, "order": 5},
]

HEAVY_TRADE_KEYS = {
    "rulebook_full",
    "entry_context_full",
    "exit_context_full",
    "holding_path_full",
    "backtest_params_full",
}

# Keep this schema identical to scripts/research/run_stage3_aggressive.py.
RL_REPLAY_SCHEMA_VERSION = 1

RL_REPLAY_TRADE_FIELDS: tuple[str, ...] = (
    "rl_replay_schema_version",
    "ticker",
    "source_stage",
    "source_run_dir",
    "rulebook_hash",
    "final_rulebook_hash",
    "entry_rulebook_hash",
    "exit_rank",
    "period_label",
    "period_role",
    "trade_index_in_period",
    "entry_signal_date",
    "entry_fill_date",
    "entry_date",
    "exit_date",
    "entry_execution_mode",
    "exit_execution_mode",
    "fold_exit_policy",
    "entry_price",
    "exit_price",
    "entry_shares",
    "total_shares",
    "avg_cost",
    "add_buys",
    "pnl_pct",
    "pnl_krw",
    "commission",
    "trigger_price",
    "fill_price_base",
    "fill_price_stress",
    "stress_pnl_pct",
    "stress_pnl_krw",
    "exit_reason",
    "holding_days",
    "max_profit_during_hold",
    "max_loss_during_hold",
    "entry_reason",
    "entry_reasons",
    "entry_signal_score",
    "entry_signal_raw_score",
    "entry_signal_threshold",
    "entry_market_adjustment",
    "entry_signal_components",
    "entry_news_sentiment",
    "entry_topic_features",
    "entry_market_score",
    "entry_sector_score",
    "entry_vix_level",
    "entry_event_flags",
    "entry_atr",
    "stop_price_at_entry",
    "target_price_at_entry",
    "trailing_stop_at_entry",
    "trailing_distance_at_entry",
    "trailing_activation_profit_pct",
    "breakeven_enabled",
    "breakeven_trigger_profit_pct",
    "breakeven_floor_profit_pct",
    "sell_omen_enabled",
    "sell_omen_score",
    "sell_omen_threshold",
    "exit_strategy",
)

RL_REPLAY_CRITICAL_FIELDS: tuple[str, ...] = (
    "ticker",
    "rulebook_hash",
    "entry_date",
    "exit_date",
    "pnl_pct",
    "pnl_krw",
    "entry_signal_score",
    "period_role",
)

PERIOD_METRICS_FIELDS = [
    "ticker",
    "rulebook_hash",
    "period_label",
    "period_kind",
    "period_order",
    "period_start",
    "period_end",
    "status",
    "passed_gate",
    "fail_reasons",
    "member_score",
    "trade_count",
    "expectancy_pct",
    "profit_factor",
    "win_rate",
    "max_drawdown_pct",
    "fitness",
    "avg_mfe_pct",
    "avg_mae_pct",
    "worst_mae_pct",
    "mfe_positive_rate",
    "exit_reason_distribution",
    "origin_count",
    "origin_train_labels",
]

EARLY_CUT_FIELDS = [
    "ticker",
    "rulebook_hash",
    "origin_count",
    "origin_train_labels",
    "evaluated_period_count",
    "evaluated_periods",
    "skipped_period_count",
    "skipped_periods",
    "survived_all_5",
    "failed_period_label",
    "failed_period_order",
    "failed_period_kind",
    "fail_reasons",
]

GA_HISTORY_FIELDS = [
    "train_label",
    "train_start",
    "train_end",
    "generation",
    "best_fitness",
    "avg_fitness",
    "best_rulebook_hash",
    "generations_run",
    "early_stop_triggered",
    "train_elapsed_sec",
    "pid",
]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return json_safe({k: v for k, v in vars(value).items() if not str(k).startswith("_")})
    return str(value)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out: dict[str, Any] = {}
            for key in fieldnames:
                value = row.get(key)
                if isinstance(value, (dict, list, tuple, set)):
                    out[key] = json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True)
                else:
                    out[key] = value
            writer.writerow(out)


def write_text_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(json_safe(obj), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def default_seed_base(ticker: str) -> int:
    # Deterministic but ticker-specific.  Keeps repeated runs reproducible while
    # avoiding identical random streams across tickers.
    return 2026061300 + sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker.upper()))


def auto_out_dir(ticker: str, root: Path = PROJECT_ROOT) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"exp_{ticker.lower()}_stage2_{today}_"
    for idx in range(1, 10000):
        candidate = root / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate output directory for {ticker}: exhausted {prefix}NNNN")


def base_kwargs(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_limit_krw": POSITION_LIMIT_KRW,
        "market_history_df": ctx.get("market_history_df"),
        "sector_name": ctx.get("sector_name", "tech"),
        "ticker_sentiment": ctx.get("ticker_sentiment"),
        "fitness_mode": "swing",
        "use_llm_events": False,
    }


def result_metrics(result: Any) -> dict[str, Any]:
    trades = list(getattr(result, "trades", []) or [])
    mfes = [
        safe_float(trade.get("max_profit_during_hold"))
        for trade in trades
        if isinstance(trade, dict) and trade.get("max_profit_during_hold") is not None
    ]
    maes = [
        safe_float(trade.get("max_loss_during_hold"))
        for trade in trades
        if isinstance(trade, dict) and trade.get("max_loss_during_hold") is not None
    ]
    exit_counter = Counter(str(trade.get("exit_reason") or "") for trade in trades if isinstance(trade, dict))
    return {
        "trade_count": safe_int(getattr(result, "trade_count", 0)),
        "win_count": safe_int(getattr(result, "win_count", 0)),
        "loss_count": safe_int(getattr(result, "loss_count", 0)),
        "win_rate": safe_float(getattr(result, "win_rate", 0.0)),
        "expectancy_pct": safe_float(getattr(result, "expectancy_pct", 0.0)),
        "avg_return_pct": safe_float(getattr(result, "avg_return_pct", 0.0)),
        "profit_factor": safe_float(getattr(result, "profit_factor", 0.0)),
        "max_drawdown_pct": safe_float(getattr(result, "max_drawdown_pct", 0.0)),
        "fitness": safe_float(getattr(result, "fitness", 0.0)),
        "avg_mfe_pct": float(mean(mfes)) if mfes else None,
        "avg_mae_pct": float(mean(maes)) if maes else None,
        "worst_mae_pct": float(min(maes)) if maes else None,
        "mfe_positive_rate": float(sum(1 for value in mfes if value > 0) / len(mfes)) if mfes else None,
        "exit_reason_distribution": dict(exit_counter),
    }


def compact_trade(trade: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in trade.items() if key not in HEAVY_TRADE_KEYS}
    out["heavy_trade_keys_omitted"] = sorted([key for key in HEAVY_TRADE_KEYS if key in trade])
    return out


def period_role_for_stage2(period_label: str, period_kind: Any, logger: logging.Logger | None = None) -> str:
    kind = str(period_kind or "").strip().lower()
    if kind in {"train", "oos", "stress"}:
        return kind
    role = "unknown"
    if logger is not None:
        logger.warning("stage2 rl_replay unknown period_role period_label=%s period_kind=%s", period_label, period_kind)
    return role


def _lookup_rl_replay_trade_value(
    *,
    trade: Mapping[str, Any],
    rulebook_dict: Mapping[str, Any],
    context: Mapping[str, Any],
    field: str,
) -> tuple[Any, bool]:
    if field in context:
        return context.get(field), True
    if field in trade:
        return trade.get(field), True
    nested_rulebook = trade.get("rulebook_full")
    if isinstance(nested_rulebook, Mapping) and field in nested_rulebook:
        return nested_rulebook.get(field), True
    if field in rulebook_dict:
        return rulebook_dict.get(field), True
    return None, False


def _rl_replay_trade(
    *,
    trade: Mapping[str, Any],
    rulebook_dict: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    out: dict[str, Any] = {}
    missing: list[str] = []
    for field in RL_REPLAY_TRADE_FIELDS:
        value, found = _lookup_rl_replay_trade_value(
            trade=trade,
            rulebook_dict=rulebook_dict,
            context=context,
            field=field,
        )
        if found:
            out[field] = value
        else:
            out[field] = None
            missing.append(field)
    critical_null = [field for field in RL_REPLAY_CRITICAL_FIELDS if out.get(field) is None]
    return out, missing, critical_null


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _configure_logging(out_dir: Path) -> logging.Logger:
    logger = logging.getLogger("run_stage2")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(out_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _date_series(df: pd.DataFrame) -> pd.Series:
    if "date" in df.columns:
        return pd.Series(pd.to_datetime(df["date"], errors="coerce").to_numpy(), index=df.index)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(df.index, errors="coerce"), index=df.index)
    raise ValueError("Stage2 Stage3 entry feature domain requires a date column or DatetimeIndex")


def build_entry_feature_domain(
    ctx: Mapping[str, Any],
    *,
    start: str | None,
    end: str | None,
) -> dict[str, dict[str, Any]]:
    """Stage3와 같은 D-5 strict-entry feature domain/support를 계산한다.

    Stage2의 rolling 선별 방식은 유지하되, entry gene 진화와 support 검증은
    Stage3 rework와 동일하게 5개 strict feature의 전체 series에 shift(5)를
    먼저 적용한 뒤 fold 구간을 자른다.
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
    bb_lower = numeric("BB_lower")
    bb_upper = numeric("BB_upper")

    frame["ma_trend"] = 0.5 * (((ma5 / ma20) - 1.0) + ((ma20 / ma60) - 1.0)) * 100.0
    frame["macd_hist"] = numeric("MACD_hist") / close * 100.0
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
def _stage3_entry_phase_execution_context():
    """Stage3 entry-phase execution semantics를 Stage2 train/eval 호출에 주입한다.

    - T+1 open 진입은 기존 run_backtest_execution_mode entry plan을 사용한다.
    - 청산은 Stage3와 동일하게 entry_phase_exit=True로 강제한다.
    - 보유 중 매일 D-5 strict signal tape를 재계산한 결과로 interval break를 판단한다.
    - provisional max holding은 7거래일로 제한한다.
    - 컨텍스트 종료 시 monkeypatch를 반드시 원복한다.
    """
    from engine.learning import execution_mode_backtest as _execution_backtest

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
            raise RuntimeError("stage2 Stage3 entry-phase signal tape was not built before simulate_exit")
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


def run_stage3_entry_phase_backtest(
    rulebook: Rulebook,
    df: pd.DataFrame,
    *,
    start_date: str | None,
    end_date: str | None,
    kwargs: Mapping[str, Any],
) -> Any:
    """Stage2에서 Stage3 entry-phase semantics로 학습/검증을 실행한다."""
    with _stage3_entry_phase_execution_context():
        return run_backtest_execution_mode(
            rulebook,
            df,
            start_date=start_date,
            end_date=end_date,
            **dict(kwargs),
            entry_execution_mode=ENTRY_EXECUTION_MODE,
            exit_execution_mode=EXIT_EXECUTION_MODE,
            fold_exit_policy=FOLD_EXIT_POLICY,
            live_hard_stop_guard=LIVE_HARD_STOP_GUARD,
        )


def _train_one_split_worker(payload: dict[str, Any]) -> dict[str, Any]:
    return train_one_split(
        ticker=payload["ticker"],
        split_idx=payload["split_idx"],
        split=payload["split"],
        seed_base=payload["seed_base"],
        use_fitness_cache=bool(payload.get("use_fitness_cache", False)),
        code_commit=payload.get("code_commit"),
    )


def train_one_split(
    *,
    ticker: str,
    split_idx: int,
    split: dict[str, str],
    seed_base: int,
    use_fitness_cache: bool = False,
    code_commit: str | None = None,
) -> dict[str, Any]:
    pid = os.getpid()
    started = time.time()
    ctx = prepare_ticker_context(ticker)
    df = ctx["df"]
    kwargs = base_kwargs(ctx)
    entry_feature_domain = build_entry_feature_domain(
        ctx,
        start=split["train_start"],
        end=split["train_end"],
    )
    history: list[dict[str, Any]] = []

    def evaluate_fn(rulebook: Rulebook) -> float:
        from engine.learning import execution_mode_backtest as _entry_scope_bt

        class _Stage2EntryScopeFitnessMarker:
            def __init__(self, rb: Rulebook):
                self.rb = rb
                self.marker_attr = _entry_scope_bt.ENTRY_GA_SCOPE_MARKER
                self.marker_value = _entry_scope_bt.ENTRY_GA_SCOPE_VALUE
                self.target_attr = _entry_scope_bt.ENTRY_FITNESS_EEC_TARGET_ATTR
                self.floor_attr = _entry_scope_bt.ENTRY_FITNESS_EEC_FLOOR_ATTR
                self.old_marker_exists = hasattr(rb, self.marker_attr)
                self.old_marker_value = getattr(rb, self.marker_attr, None)
                self.old_target_exists = hasattr(rb, self.target_attr)
                self.old_target_value = getattr(rb, self.target_attr, None)
                self.old_floor_exists = hasattr(rb, self.floor_attr)
                self.old_floor_value = getattr(rb, self.floor_attr, None)

            def __enter__(self) -> Rulebook:
                setattr(self.rb, self.marker_attr, self.marker_value)
                setattr(self.rb, self.target_attr, 6.0)
                setattr(self.rb, self.floor_attr, 0.5)
                return self.rb

            def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
                if self.old_marker_exists:
                    setattr(self.rb, self.marker_attr, self.old_marker_value)
                else:
                    try:
                        delattr(self.rb, self.marker_attr)
                    except AttributeError:
                        pass
                if self.old_target_exists:
                    setattr(self.rb, self.target_attr, self.old_target_value)
                else:
                    try:
                        delattr(self.rb, self.target_attr)
                    except AttributeError:
                        pass
                if self.old_floor_exists:
                    setattr(self.rb, self.floor_attr, self.old_floor_value)
                else:
                    try:
                        delattr(self.rb, self.floor_attr)
                    except AttributeError:
                        pass
                return False

        with _Stage2EntryScopeFitnessMarker(rulebook):
            result = run_stage3_entry_phase_backtest(
                rulebook,
                df,
                start_date=split["train_start"],
                end_date=split["train_end"],
                kwargs=kwargs,
            )
        return result.fitness

    fitness_cache = FitnessCache() if use_fitness_cache else None
    if fitness_cache is not None:
        evaluate_fn = make_cached_evaluate_fn(
            evaluate_fn,
            cache=fitness_cache,
            key_ctx=make_cache_key_context(
                ticker=ticker,
                period_label=f"{split['label']}|{ENTRY_PHASE_CACHE_MODE}",
                start_date=split["train_start"],
                end_date=split["train_end"],
                entry_execution_mode=ENTRY_EXECUTION_MODE,
                exit_execution_mode=ENTRY_PHASE_CACHE_MODE,
                fold_exit_policy=FOLD_EXIT_POLICY,
                fitness_mode=str(kwargs.get("fitness_mode", "swing")),
                code_commit=code_commit or resolve_code_commit(PROJECT_ROOT),
                add_buy_runtime_enabled=ADD_BUY_RUNTIME_ENABLED,
            ),
        )

    def on_generation(generation: int, best: Any, avg: float) -> None:
        history.append(
            {
                "train_label": split["label"],
                "train_start": split["train_start"],
                "train_end": split["train_end"],
                "generation": int(generation),
                "best_fitness": safe_float(getattr(best, "fitness", 0.0)),
                "avg_fitness": safe_float(avg),
                "best_rulebook_hash": compute_rulebook_hash(best),
                "pid": pid,
            }
        )

    ga_config = GAConfig(
        population=POPULATION,
        generations=GENERATIONS,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=PATIENCE,
        random_seed=seed_base + split_idx,
    )
    result = run_ga(
        base_rulebook=ctx["base_rulebook"],
        evaluate_fn=evaluate_fn,
        ga_config=ga_config,
        on_generation=on_generation,
        gene_scope="entry",
        entry_feature_domain=entry_feature_domain,
    )
    elapsed = time.time() - started
    generations_run = safe_int(getattr(result, "generations_run", 0))
    early_stop = generations_run < GENERATIONS

    rows: list[dict[str, Any]] = []
    population = sorted(
        list(getattr(result, "final_population", []) or []),
        key=lambda rulebook: safe_float(getattr(rulebook, "fitness", None), float("-inf")),
        reverse=True,
    )
    for rank, rulebook in enumerate(population, 1):
        rulebook_hash = compute_rulebook_hash(rulebook)
        rows.append(
            {
                "ticker": ticker,
                "train_label": split["label"],
                "train_start": split["train_start"],
                "train_end": split["train_end"],
                "origin_rank": rank,
                "rulebook_hash": rulebook_hash,
                "train_fitness": safe_float(getattr(rulebook, "fitness", 0.0)),
                "rulebook": rulebook.to_dict() if hasattr(rulebook, "to_dict") else json_safe(rulebook),
            }
        )

    for row in history:
        row["generations_run"] = generations_run
        row["early_stop_triggered"] = bool(early_stop)
        row["train_elapsed_sec"] = elapsed

    return {
        "split": split,
        "rows": rows,
        "history": history,
        "generations_run": generations_run,
        "early_stop": early_stop,
        "elapsed": elapsed,
        "pid": pid,
        "fitness_cache": summarize_fitness_cache(fitness_cache),
    }


def run_training(
    *,
    ticker: str,
    seed_base: int,
    parallel: bool,
    logger: logging.Logger,
    use_fitness_cache: bool = False,
    code_commit: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    train_results: list[dict[str, Any]] = []
    if parallel:
        max_workers = min(len(TRAIN_SPLITS), max(1, (os.cpu_count() or 2) - 1))
        logger.info("training mode=parallel train_splits=%s max_workers=%s", len(TRAIN_SPLITS), max_workers)
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _train_one_split_worker,
                    {
                        "ticker": ticker,
                        "split_idx": idx,
                        "split": split,
                        "seed_base": seed_base,
                        "use_fitness_cache": use_fitness_cache,
                        "code_commit": code_commit,
                    },
                ): split
                for idx, split in enumerate(TRAIN_SPLITS, 1)
            }
            for future in as_completed(futures):
                result = future.result()
                logger.info(
                    "train done label=%s pid=%s generations=%s early_stop=%s rows=%s elapsed=%.1fs",
                    result["split"]["label"],
                    result["pid"],
                    result["generations_run"],
                    result["early_stop"],
                    len(result["rows"]),
                    result["elapsed"],
                )
                train_results.append(result)
    else:
        logger.info("training mode=sequential train_splits=%s", len(TRAIN_SPLITS))
        for idx, split in enumerate(TRAIN_SPLITS, 1):
            logger.info("train start label=%s", split["label"])
            result = train_one_split(
                ticker=ticker,
                split_idx=idx,
                split=split,
                seed_base=seed_base,
                use_fitness_cache=use_fitness_cache,
                code_commit=code_commit,
            )
            logger.info(
                "train done label=%s pid=%s generations=%s early_stop=%s rows=%s elapsed=%.1fs",
                result["split"]["label"],
                result["pid"],
                result["generations_run"],
                result["early_stop"],
                len(result["rows"]),
                result["elapsed"],
            )
            train_results.append(result)

    train_results.sort(key=lambda row: row["split"]["label"])
    rulebook_rows: list[dict[str, Any]] = []
    ga_history_rows: list[dict[str, Any]] = []
    for result in train_results:
        rulebook_rows.extend(result["rows"])
        ga_history_rows.extend(result["history"])
    ga_history_rows.sort(key=lambda row: (row["train_label"], row["generation"]))
    return train_results, rulebook_rows, ga_history_rows


def build_representatives(rulebook_rows: list[dict[str, Any]]) -> tuple[dict[str, Rulebook], dict[str, list[dict[str, Any]]]]:
    representative_by_hash: dict[str, Rulebook] = {}
    origin_rows_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rulebook_rows:
        rulebook_hash = str(row["rulebook_hash"])
        rulebook = Rulebook.from_dict(dict(row["rulebook"]))
        origin = {
            key: row[key]
            for key in ["train_label", "train_start", "train_end", "origin_rank", "train_fitness"]
        }
        origin_rows_by_hash[rulebook_hash].append(origin)
        current = representative_by_hash.get(rulebook_hash)
        if current is None or safe_float(row.get("train_fitness")) > safe_float(getattr(current, "fitness", 0.0)):
            representative_by_hash[rulebook_hash] = rulebook
            try:
                setattr(representative_by_hash[rulebook_hash], "fitness", safe_float(row.get("train_fitness")))
            except Exception:
                pass
    return representative_by_hash, origin_rows_by_hash


def evaluate_periods(
    *,
    ticker: str,
    ctx: dict[str, Any],
    periods: list[dict[str, Any]],
    representative_by_hash: dict[str, Rulebook],
    origin_rows_by_hash: dict[str, list[dict[str, Any]]],
    logger: logging.Logger,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    df = ctx["df"]
    kwargs = base_kwargs(ctx)
    unique_hashes = sorted(representative_by_hash)
    alive = set(unique_hashes)
    period_metrics_rows: list[dict[str, Any]] = []
    early_cut_rows: list[dict[str, Any]] = []
    survivor_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    rl_replay_trade_rows: list[dict[str, Any]] = []
    rl_replay_missing_counter: Counter[str] = Counter()
    rl_replay_critical_null_counter: Counter[str] = Counter()
    eval_count = 0
    max_eval_count = len(unique_hashes) * len(periods)
    first_fail_by_hash: dict[str, dict[str, Any]] = {}
    evaluated_periods_by_hash: dict[str, list[str]] = defaultdict(list)
    metrics_by_hash_period: dict[tuple[str, str], dict[str, Any]] = {}

    for period in periods:
        reached_hashes = sorted(alive)
        logger.info("eval start period=%s reached=%s", period["label"], len(reached_hashes))
        raw_candidates: list[dict[str, Any]] = []
        results_by_hash: dict[str, Any] = {}
        for rank_is, rulebook_hash in enumerate(reached_hashes, 1):
            rulebook = representative_by_hash[rulebook_hash]
            result = run_stage3_entry_phase_backtest(
                rulebook,
                df,
                start_date=period["start"],
                end_date=period["end"],
                kwargs=kwargs,
            )
            eval_count += 1
            evaluated_periods_by_hash[rulebook_hash].append(period["label"])
            results_by_hash[rulebook_hash] = result
            raw_candidates.append(
                {
                    "ticker": ticker,
                    "label": period["label"],
                    "period_kind": period["kind"],
                    "period_order": period["order"],
                    "rulebook_hash": rulebook_hash,
                    "rank_is": rank_is,
                    "train_fitness": safe_float(getattr(rulebook, "fitness", 0.0)),
                    **result_metrics(result),
                }
            )

        scored_candidates = _score_period_candidates(raw_candidates)
        next_alive: set[str] = set()
        for row in scored_candidates:
            rulebook_hash = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            metrics["member_score"] = safe_float(row.get("oos_member_score"))
            metrics["fitness"] = safe_float(row.get("fitness"))
            raw_extra = result_metrics(results_by_hash[rulebook_hash])
            for key in ["avg_mfe_pct", "avg_mae_pct", "worst_mae_pct", "mfe_positive_rate", "exit_reason_distribution"]:
                metrics[key] = raw_extra.get(key)

            reasons = stage2_fail_reasons(metrics, str(period["kind"]), DEFAULT_STAGE2_GATE)
            passed = not reasons
            if passed:
                next_alive.add(rulebook_hash)
            else:
                first_fail_by_hash[rulebook_hash] = {
                    "rulebook_hash": rulebook_hash,
                    "failed_period_label": period["label"],
                    "failed_period_order": period["order"],
                    "failed_period_kind": period["kind"],
                    "fail_reasons": reasons,
                }
            metrics_by_hash_period[(rulebook_hash, period["label"])] = metrics

            origin_labels = sorted({origin["train_label"] for origin in origin_rows_by_hash[rulebook_hash]})
            period_metrics_rows.append(
                {
                    "ticker": ticker,
                    "rulebook_hash": rulebook_hash,
                    "period_label": period["label"],
                    "period_kind": period["kind"],
                    "period_order": period["order"],
                    "period_start": period["start"],
                    "period_end": period["end"],
                    "status": "evaluated",
                    "passed_gate": bool(passed),
                    "fail_reasons": reasons,
                    "member_score": metrics.get("member_score"),
                    "trade_count": metrics.get("trade_count"),
                    "expectancy_pct": metrics.get("expectancy_pct"),
                    "profit_factor": metrics.get("profit_factor"),
                    "win_rate": metrics.get("win_rate"),
                    "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                    "fitness": metrics.get("fitness"),
                    "avg_mfe_pct": metrics.get("avg_mfe_pct"),
                    "avg_mae_pct": metrics.get("avg_mae_pct"),
                    "worst_mae_pct": metrics.get("worst_mae_pct"),
                    "mfe_positive_rate": metrics.get("mfe_positive_rate"),
                    "exit_reason_distribution": metrics.get("exit_reason_distribution"),
                    "origin_count": len(origin_rows_by_hash[rulebook_hash]),
                    "origin_train_labels": origin_labels,
                }
            )

            period_role = period_role_for_stage2(str(period["label"]), period.get("kind"), logger)
            rulebook_dict = representative_by_hash[rulebook_hash].to_dict() if hasattr(representative_by_hash[rulebook_hash], "to_dict") else {}
            for trade_idx, trade in enumerate(list(getattr(results_by_hash[rulebook_hash], "trades", []) or []), 1):
                if isinstance(trade, dict):
                    raw_trade = trade
                    trade_row = compact_trade(trade)
                else:
                    converted = json_safe(trade)
                    raw_trade = converted if isinstance(converted, dict) else {}
                    trade_row = compact_trade(converted) if isinstance(converted, dict) else {"trade_repr": str(trade)}
                trade_context = {
                    "rl_replay_schema_version": RL_REPLAY_SCHEMA_VERSION,
                    "ticker": ticker,
                    "source_stage": "stage2",
                    "source_run_dir": str(out_dir) if out_dir is not None else None,
                    "rulebook_hash": rulebook_hash,
                    "period_label": period["label"],
                    "period_role": period_role,
                    "trade_index_in_period": trade_idx,
                }
                replay_row, replay_missing, replay_critical_null = _rl_replay_trade(
                    trade=raw_trade,
                    rulebook_dict=rulebook_dict,
                    context=trade_context,
                )
                rl_replay_trade_rows.append(replay_row)
                rl_replay_missing_counter.update(replay_missing)
                rl_replay_critical_null_counter.update(replay_critical_null)
                trade_row.update(
                    {
                        "ticker": ticker,
                        "rulebook_hash": rulebook_hash,
                        "period_label": period["label"],
                        "period_kind": period["kind"],
                        "period_start": period["start"],
                        "period_end": period["end"],
                        "trade_index_in_period": trade_idx,
                        "origin_count": len(origin_rows_by_hash[rulebook_hash]),
                        "origin_train_labels": origin_labels,
                    }
                )
                trade_rows.append(trade_row)
        alive = next_alive
        logger.info("eval done period=%s pass=%s", period["label"], len(alive))

    survivors = sorted(alive)
    for rulebook_hash in unique_hashes:
        failed = first_fail_by_hash.get(rulebook_hash)
        reached = evaluated_periods_by_hash.get(rulebook_hash, [])
        skipped_periods = [period["label"] for period in periods if period["label"] not in reached]
        origin_labels = sorted({origin["train_label"] for origin in origin_rows_by_hash[rulebook_hash]})
        early_cut_rows.append(
            {
                "ticker": ticker,
                "rulebook_hash": rulebook_hash,
                "origin_count": len(origin_rows_by_hash[rulebook_hash]),
                "origin_train_labels": origin_labels,
                "evaluated_period_count": len(reached),
                "evaluated_periods": reached,
                "skipped_period_count": len(skipped_periods),
                "skipped_periods": skipped_periods,
                "survived_all_5": rulebook_hash in survivors,
                "failed_period_label": failed.get("failed_period_label") if failed else None,
                "failed_period_order": failed.get("failed_period_order") if failed else None,
                "failed_period_kind": failed.get("failed_period_kind") if failed else None,
                "fail_reasons": failed.get("fail_reasons") if failed else [],
            }
        )
        if failed:
            for period in periods:
                if period["label"] in skipped_periods:
                    period_metrics_rows.append(
                        {
                            "ticker": ticker,
                            "rulebook_hash": rulebook_hash,
                            "period_label": period["label"],
                            "period_kind": period["kind"],
                            "period_order": period["order"],
                            "period_start": period["start"],
                            "period_end": period["end"],
                            "status": "skipped_after_early_cut",
                            "passed_gate": False,
                            "fail_reasons": [],
                            "member_score": None,
                            "trade_count": None,
                            "expectancy_pct": None,
                            "profit_factor": None,
                            "win_rate": None,
                            "max_drawdown_pct": None,
                            "fitness": None,
                            "avg_mfe_pct": None,
                            "avg_mae_pct": None,
                            "worst_mae_pct": None,
                            "mfe_positive_rate": None,
                            "exit_reason_distribution": {},
                            "origin_count": len(origin_rows_by_hash[rulebook_hash]),
                            "origin_train_labels": origin_labels,
                        }
                    )

    for rulebook_hash in survivors:
        survivor_row = {
            "ticker": ticker,
            "rulebook_hash": rulebook_hash,
            "origin_count": len(origin_rows_by_hash[rulebook_hash]),
            "origin_train_labels": sorted({origin["train_label"] for origin in origin_rows_by_hash[rulebook_hash]}),
            "origins": origin_rows_by_hash[rulebook_hash],
            "rulebook": representative_by_hash[rulebook_hash].to_dict()
            if hasattr(representative_by_hash[rulebook_hash], "to_dict")
            else json_safe(representative_by_hash[rulebook_hash]),
            "periods": [],
        }
        for period in periods:
            survivor_row["periods"].append(
                {
                    "period_label": period["label"],
                    "period_kind": period["kind"],
                    **metrics_by_hash_period.get((rulebook_hash, period["label"]), {}),
                }
            )
        survivor_rows.append(survivor_row)

    return {
        "unique_hashes": unique_hashes,
        "survivors": survivors,
        "period_metrics_rows": period_metrics_rows,
        "early_cut_rows": early_cut_rows,
        "survivor_rows": survivor_rows,
        "trade_rows": trade_rows,
        "rl_replay_trade_rows": rl_replay_trade_rows,
        "rl_replay_missing_counts": _counter_dict(rl_replay_missing_counter),
        "rl_replay_critical_null_counts": _counter_dict(rl_replay_critical_null_counter),
        "eval_count": eval_count,
        "max_eval_count": max_eval_count,
    }


def build_config(
    *,
    ticker: str,
    out_dir: Path,
    seed_base: int,
    parallel: bool,
    ctx: dict[str, Any],
    periods: list[dict[str, Any]],
    started: float,
    use_fitness_cache: bool,
    code_commit: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "purpose": f"{ticker} ticker-agnostic Stage 2 definition validation; research-only; not live trading",
        "created_at_unix": started,
        "output_dir": str(out_dir),
        "constraints": {
            "live_engine_modified": False,
            "parameters_manifest_positions_trade_log_market_state_modified": False,
            "code_body_modified": False,
        },
        "data": {
            "data_start": ctx.get("data_start") or ctx.get("data_min"),
            "data_end": ctx.get("data_end") or ctx.get("data_max"),
            "rows": int(ctx.get("rows", 0) or 0),
            "adv_usd_252d": safe_float(ctx.get("adv_usd_252d")),
            "sentiment_days": safe_int(ctx.get("sentiment_days")),
            "sell_omen_score": json_safe(ctx.get("sell_omen_score")),
        },
        "ga": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "early_stop_no_improve": PATIENCE,
            "elite_ratio": 0.2,
            "mutation_rate": 0.15,
            "mutation_strength": 0.2,
            "tournament_size": 3,
            "seed_pattern_ratio": 0.33,
            "random_seed_base": seed_base,
            "gene_scope": "entry",
            "entry_feature_domain": "Stage3 D-5 strict interval domain per train fold",
        },
        "execution_semantics": {
            "source": "Stage3 rework",
            "mode": STAGE2_EXECUTION_SEMANTICS,
            "entry_execution_mode": ENTRY_EXECUTION_MODE,
            "exit_execution_mode_runtime": EXIT_EXECUTION_MODE,
            "entry_phase_cache_mode": ENTRY_PHASE_CACHE_MODE,
            "entry_phase_exit": True,
            "entry_phase_max_holding_days": ENTRY_PHASE_MAX_HOLDING_DAYS,
            "technical_feature_lag_mode": TECHNICAL_FEATURE_LAG_MODE,
            "technical_feature_lag_trading_days": TECHNICAL_FEATURE_LAG_TRADING_DAYS,
            "rolling_gate_uses_same_entry_phase_execution": True,
            "interval_break_fill": "strict interval break observed at day close, filled next trading day open",
            "atr_stop_fill": "intraday low touch, gap-down open or stop_price per exit_simulator",
        },
        "parallel": {
            "enabled": bool(parallel),
            "system_cpu": os.cpu_count(),
            "live_runner_reserved_cores": 1,
            "train_splits_parallel": len(TRAIN_SPLITS) if parallel else 1,
            "note": "Parallel mode runs independent train splits in separate processes; GA engine has no internal worker parameter.",
        },
        "fitness_cache": {
            "enabled": bool(use_fitness_cache),
            "type": "in_memory_process_local",
            "code_commit": code_commit,
            "add_buy_runtime_enabled": ADD_BUY_RUNTIME_ENABLED,
            "cache_schema_version": 1,
            "toggle": "default off; enable with --fitness-cache or KINGMAKER_FITNESS_CACHE=1; --no-fitness-cache is accepted as a no-op compatibility flag",
        },
        "train_splits": TRAIN_SPLITS,
        "evaluation_periods": periods,
        "early_cut_order": [period["label"] for period in periods],
        "gate": {
            "module": "engine.pipeline.stage2_gate",
            "function": "stage2_fail_reasons",
            "config": dataclasses.asdict(DEFAULT_STAGE2_GATE),
            "period_kind_policy": {period["label"]: period["kind"] for period in periods},
            "member_score_policy": "topn_survivor._score_period_candidates: expectancy 0.70 + profit_factor 0.20 + drawdown 0.10, win_rate excluded; scored over entities that reached the period",
            "hash_unit": "rulebook_hash",
        },
        "trade_persistence": {
            "trade_rows_compacted": True,
            "heavy_trade_keys_omitted": sorted(HEAVY_TRADE_KEYS),
            "reason": "trades.jsonl preserves trade-level MFE/MAE and scalar diagnostics while avoiding very large full context dumps; full rulebook params are in rulebooks_all.jsonl",
        },
    }


def run_stage2(*, ticker: str, out_dir: Path, seed_base: int, parallel: bool, use_fitness_cache: bool = False) -> dict[str, Any]:
    started = time.time()
    code_commit = resolve_code_commit(PROJECT_ROOT)
    out_dir.mkdir(parents=True, exist_ok=False)
    logger = _configure_logging(out_dir)
    logger.info("stage2 start ticker=%s out_dir=%s seed_base=%s parallel=%s", ticker, out_dir, seed_base, parallel)

    ctx = prepare_ticker_context(ticker)
    data_start = ctx.get("data_start") or ctx.get("data_min")
    data_end = ctx.get("data_end") or ctx.get("data_max")
    periods: list[dict[str, Any]] = []
    for period in PERIODS_TEMPLATE:
        row = dict(period)
        row["start"] = row["start"] or data_start
        row["end"] = row["end"] or data_end
        periods.append(row)

    config = build_config(
        ticker=ticker,
        out_dir=out_dir,
        seed_base=seed_base,
        parallel=parallel,
        ctx=ctx,
        periods=periods,
        started=started,
        use_fitness_cache=use_fitness_cache,
        code_commit=code_commit,
    )
    write_text_json(out_dir / "config.json", config)

    train_results, rulebook_rows, ga_history_rows = run_training(
        ticker=ticker,
        seed_base=seed_base,
        parallel=parallel,
        logger=logger,
        use_fitness_cache=use_fitness_cache,
        code_commit=code_commit,
    )
    write_jsonl(out_dir / "rulebooks_all.jsonl", rulebook_rows)
    write_csv(out_dir / "ga_history.csv", ga_history_rows, GA_HISTORY_FIELDS)

    representative_by_hash, origin_rows_by_hash = build_representatives(rulebook_rows)
    eval_result = evaluate_periods(
        ticker=ticker,
        ctx=ctx,
        periods=periods,
        representative_by_hash=representative_by_hash,
        origin_rows_by_hash=origin_rows_by_hash,
        out_dir=out_dir,
        logger=logger,
    )

    write_csv(out_dir / "period_metrics_all.csv", eval_result["period_metrics_rows"], PERIOD_METRICS_FIELDS)
    write_csv(out_dir / "early_cut_log.csv", eval_result["early_cut_rows"], EARLY_CUT_FIELDS)
    write_jsonl(out_dir / "survivors.jsonl", eval_result["survivor_rows"])
    write_jsonl(out_dir / "trades.jsonl", eval_result["trade_rows"])
    write_jsonl(out_dir / "rl_replay_trades.jsonl", eval_result["rl_replay_trade_rows"])
    logger.info(
        "stage2 rl_replay_trades written path=%s rows=%s missing_field_counts=%s critical_null_counts=%s",
        out_dir / "rl_replay_trades.jsonl",
        len(eval_result["rl_replay_trade_rows"]),
        eval_result["rl_replay_missing_counts"],
        eval_result["rl_replay_critical_null_counts"],
    )
    if eval_result["rl_replay_critical_null_counts"]:
        logger.warning("stage2 rl_replay critical null counts: %s", eval_result["rl_replay_critical_null_counts"])

    fail_counts = Counter(str(row.get("failed_period_label") or "SURVIVED") for row in eval_result["early_cut_rows"])
    generations = [safe_int(row["generations_run"]) for row in train_results]
    actual_eval_ratio = float(eval_result["eval_count"] / eval_result["max_eval_count"]) if eval_result["max_eval_count"] else 0.0
    fitness_cache_by_train = {row["split"]["label"]: row.get("fitness_cache", {}) for row in train_results}
    fitness_cache_summary = aggregate_fitness_cache_summaries(list(fitness_cache_by_train.values()))
    summary = {
        "ticker": ticker,
        "generated_rulebook_rows": len(rulebook_rows),
        "unique_rulebook_hashes": len(eval_result["unique_hashes"]),
        "survivor_count": len(eval_result["survivors"]),
        "survivor_hashes": eval_result["survivors"],
        "max_period_evaluations": eval_result["max_eval_count"],
        "actual_period_evaluations": eval_result["eval_count"],
        "actual_eval_ratio": actual_eval_ratio,
        "period_eval_saved_ratio": 1.0 - actual_eval_ratio,
        "fail_counts_by_first_failed_period": dict(fail_counts),
        "ga_generations_run_by_train": {row["split"]["label"]: row["generations_run"] for row in train_results},
        "ga_early_stop_triggered_by_train": {row["split"]["label"]: row["early_stop"] for row in train_results},
        "ga_early_stop_triggered_count": sum(1 for row in train_results if row["early_stop"]),
        "ga_average_generations_run": float(mean(generations)) if generations else None,
        "parallel": config["parallel"],
        "fitness_cache": fitness_cache_summary,
        "fitness_cache_by_train": fitness_cache_by_train,
        "elapsed_sec": time.time() - started,
        "outputs": {
            "rulebooks_all": str(out_dir / "rulebooks_all.jsonl"),
            "period_metrics_all": str(out_dir / "period_metrics_all.csv"),
            "early_cut_log": str(out_dir / "early_cut_log.csv"),
            "survivors": str(out_dir / "survivors.jsonl"),
            "trades": str(out_dir / "trades.jsonl"),
            "ga_history": str(out_dir / "ga_history.csv"),
            "config": str(out_dir / "config.json"),
            "summary": str(out_dir / "summary.json"),
            "run_log": str(out_dir / "run.log"),
        },
    }
    config["summary"] = summary
    write_text_json(out_dir / "config.json", config)
    write_text_json(out_dir / "summary.json", summary)
    logger.info("stage2 done ticker=%s survivors=%s elapsed=%.1fs", ticker, len(eval_result["survivors"]), summary["elapsed_sec"])
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ticker-agnostic Stage 2 research runner")
    parser.add_argument("--ticker", required=True, help="Ticker symbol to run, e.g. MPLX")
    parser.add_argument("--out-dir", default=None, help="Output directory. Default: exp_<ticker>_stage2_<YYYYMMDD>_NNNN")
    parser.add_argument("--seed-base", type=int, default=None, help="Deterministic GA seed base. Default: ticker-specific deterministic offset")
    parser.add_argument("--parallel", action="store_true", help="Run the three train splits in parallel processes. Default: sequential")
    parser.add_argument("--fitness-cache", action="store_true", help="Explicitly enable GA evaluate_fn in-memory fitness cache. Default: disabled")
    parser.add_argument("--no-fitness-cache", action="store_true", help="Deprecated compatibility flag; fitness cache is already disabled by default")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    use_fitness_cache = resolve_fitness_cache_enabled(cli_enabled=bool(args.fitness_cache))
    run_stage2(ticker=ticker, out_dir=out_dir, seed_base=seed_base, parallel=bool(args.parallel), use_fitness_cache=use_fitness_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
