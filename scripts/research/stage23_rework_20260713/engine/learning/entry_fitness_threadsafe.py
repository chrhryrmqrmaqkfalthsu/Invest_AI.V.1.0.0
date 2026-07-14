"""Thread-safe Stage3 entry-fitness backtest.

The legacy research runner activated provisional entry exits by temporarily
patching module globals.  That is deterministic in one process but unsafe when
a Dask worker evaluates many candidates concurrently in threads.  This module
executes the same backtest flow and passes the provisional-exit arguments
explicitly to ``simulate_exit``.  No module globals are mutated.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

import pandas as pd

from engine.learning import execution_mode_backtest as execution_bt
from engine.strategies.evaluator import calc_position_size_krw
from engine.strategies.rulebook import Rulebook


def run_entry_backtest_threadsafe(
    rb: Rulebook,
    df: pd.DataFrame,
    *,
    market_score: float = 50.0,
    sector_score: float = 50.0,
    vix_level: float = 18.0,
    position_limit_krw: float = 120000.0,
    commission_rate: float = 0.0005,
    cooldown_days: int = 1,
    warmup: int = 200,
    market_history_df: Optional[pd.DataFrame] = None,
    sector_name: str = "tech",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    ticker_sentiment: Optional[dict] = None,
    complexity_penalty_per_mask: float = 0.0,
    use_llm_events: bool = False,
    entry_execution_mode: str = "t_plus_1_open",
    exit_execution_mode: str = "conservative_core",
    fold_exit_policy: str = "fold_end_mark_to_market",
    live_hard_stop_guard: bool = True,
    entry_phase_max_holding_days: int = 7,
) -> Any:
    """Run the entry-scope swing fitness without shared mutable patch state."""
    trades: list[dict[str, Any]] = []
    entry_scope_active = execution_bt._entry_scope_active(rb)
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    date_series = execution_bt._date_series_for_df(df)
    df_exit, _ = execution_bt._bounded_exit_df(
        df,
        end_ts=end_ts,
        fold_exit_policy=fold_exit_policy,
    )
    topic_window = execution_bt._news_zscore_window(rb)
    topic_feature_map = execution_bt._precompute_topic_feature_map(
        ticker_sentiment,
        topic_window,
    )
    signal_tape = execution_bt._build_daily_signal_tape(
        rb=rb,
        df=df,
        warmup=warmup,
        start_ts=start_ts,
        end_ts=end_ts,
        date_series=date_series,
        market_score=market_score,
        sector_score=sector_score,
        vix_level=vix_level,
        market_history_df=market_history_df,
        sector_name=sector_name,
        ticker_sentiment=ticker_sentiment,
        topic_feature_map=topic_feature_map,
        use_llm_events=use_llm_events,
    )
    index = max(warmup, 0)
    row_count = len(df)

    while index < row_count:
        current_ts = execution_bt._date_at(date_series, index)
        if start_ts is not None and current_ts is not None and current_ts < start_ts:
            index += 1
            continue
        if end_ts is not None and current_ts is not None and current_ts > end_ts:
            break

        point = signal_tape.get(index)
        if point is None or not point.entry_eligible:
            index += 1
            continue
        signal = point.signal
        if not signal.should_buy:
            index += 1
            continue

        plan = execution_bt._entry_plan(
            df,
            index,
            entry_execution_mode=entry_execution_mode,
            end_ts=end_ts,
            date_series=date_series,
        )
        if plan is None:
            index += 1
            continue
        entry_idx = int(plan["entry_idx"])
        if entry_idx >= len(df_exit):
            index += 1
            continue

        amount_krw = calc_position_size_krw(rb, signal.score, position_limit_krw)
        entry_price = float(plan["entry_price"])
        shares = int(amount_krw / entry_price) if entry_price > 0 else 0
        if shares <= 0:
            index += 1
            continue

        trade_object = execution_bt.simulate_exit(
            rb,
            df_exit,
            entry_idx,
            shares,
            position_limit_krw,
            commission_rate=commission_rate,
            cur_market_score=point.market_score,
            cur_vix_level=point.vix_level,
            cur_sector_score=point.sector_score,
            live_hard_stop_guard=live_hard_stop_guard,
            entry_price_override=entry_price,
            entry_atr_override=plan.get("entry_atr"),
            exit_execution_mode=exit_execution_mode,
            entry_phase_exit=True,
            entry_phase_signal_tape=signal_tape,
            entry_phase_max_holding_days=entry_phase_max_holding_days,
        )
        if trade_object is None:
            index += 1
            continue

        trade = (
            asdict(trade_object)
            if hasattr(trade_object, "__dataclass_fields__")
            else dict(trade_object)
        )
        trade.update(
            execution_bt._signal_snapshot(
                "entry",
                signal,
                sentiment=point.news_sentiment,
                market=point.market_score,
                sector=point.sector_score,
                vix=point.vix_level,
                event_flags=point.event_flags,
                topic_features=point.topic_features,
            )
        )
        trade["entry_execution_mode"] = plan["entry_execution_mode"]
        trade["exit_execution_mode"] = str(exit_execution_mode or "base")
        trade["fold_exit_policy"] = str(fold_exit_policy or "unbounded")
        trade["entry_signal_date"] = plan.get("entry_signal_date", "")
        trade["entry_fill_date"] = plan.get("entry_fill_date", "")
        trade["daily_signal_tape_mode"] = execution_bt.DAILY_SIGNAL_TAPE_MODE
        trade["execution_semantics_cache_token"] = execution_bt.EXECUTION_SEMANTICS_CACHE_TOKEN
        trade["entry_signal_tape"] = point.to_public_dict(role="entry_signal")
        trade = execution_bt._maybe_relabel_fold_mtm(
            trade,
            df_exit=df_exit,
            entry_idx=entry_idx,
            rb=rb,
            fold_exit_policy=fold_exit_policy,
        )

        exit_idx = execution_bt._find_df_index_by_date(df_exit, trade.get("exit_date"))
        if exit_idx is None:
            exit_idx = entry_idx + 1
        if entry_scope_active:
            trade = execution_bt._attach_entry_exit_local_search(
                trade,
                df_exit=df_exit,
                entry_idx=entry_idx,
                realized_exit_idx=int(exit_idx),
                commission_rate=commission_rate,
            )
        trade["holding_signal_path"] = execution_bt._signal_tape_slice(
            signal_tape,
            entry_idx,
            int(exit_idx),
            role="holding",
        )
        trade["holding_signal_path_count"] = len(trade["holding_signal_path"])
        cooldown_start = int(exit_idx) + 1
        cooldown_end = int(exit_idx) + max(int(cooldown_days), 0)
        trade["cooldown_signal_path"] = execution_bt._signal_tape_slice(
            signal_tape,
            cooldown_start,
            cooldown_end,
            role="cooldown",
        )
        trade["cooldown_signal_path_count"] = len(trade["cooldown_signal_path"])
        trades.append(trade)
        index = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)

    result = execution_bt._summarize(rb, trades)
    result = execution_bt._apply_fitness_mode(
        rb,
        result,
        fitness_mode="swing",
        complexity_penalty_per_mask=complexity_penalty_per_mask,
    )
    return execution_bt._attach_signal_tape_to_result(result, signal_tape)
