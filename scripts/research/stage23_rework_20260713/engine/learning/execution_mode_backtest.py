"""Learning backtest execution-mode gate helpers.

This module keeps explicit learning-backtest execution semantics isolated from
``engine.learning.backtest.run_backtest``:

- default close entry + base exit compatibility
- T+1 open entry
- conservative_core exit mode
- fold-end bounded exit scoring with mark-to-market fallback
- all post-start trading days precomputed into a daily signal tape
- entry-scope fitness diagnostics and bounded exit-timing mutation hints

The tape separates signal measurement from trade-index jumps. Holding and
cooldown dates are measured even when the execution loop skips directly to the
next eligible entry date. Rows beyond ``end_date`` are measured as
``entry_eligible=False`` so an unbounded trade can still retain its full holding
and cooldown signal path without reopening entry eligibility.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from engine.learning.backtest import (
    BacktestResult,
    _apply_complexity_penalty,
    _calc_fitness_swing,
    _find_df_index_by_date,
    _lookup_signal_context,
    _news_zscore_window,
    _precompute_topic_feature_map,
    _signal_snapshot,
    _summarize,
)
from engine.strategies.evaluator import calc_position_size_krw, evaluate_signal
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.rulebook import Rulebook

OUT_DIR = Path("data/_system/research/learning_execution_mode_gate")
DAILY_SIGNAL_TAPE_MODE = "all_post_start_days_precomputed_v1"
EXECUTION_SEMANTICS_CACHE_TOKEN = DAILY_SIGNAL_TAPE_MODE

ENTRY_GA_SCOPE_MARKER = "_active_ga_gene_scope"
ENTRY_GA_SCOPE_VALUE = "entry"
ENTRY_FITNESS_MIN_TRADES = 12
ENTRY_FITNESS_MIN_WIN_RATE_PCT = 60.0
ENTRY_FITNESS_WIN_THRESHOLD_PCT = 0.5
ENTRY_FITNESS_MAE_THRESHOLD_PCT = -2.0
ENTRY_FITNESS_MAE_PENALTY_WEIGHT = 1.0
ENTRY_FITNESS_REALIZED_LOSS_THRESHOLD_PCT = -1.0
ENTRY_FITNESS_REALIZED_LOSS_PENALTY_WEIGHT = 1.0
ENTRY_FITNESS_DISQUALIFIED = -1_000_000_000.0
ENTRY_EXIT_LOCAL_SEARCH_MAX_HOLDING_DAYS = 7
ENTRY_EXIT_MUTATION_HINT_ATTR = "_entry_exit_mutation_hint"
ENTRY_FITNESS_DIAGNOSTICS_ATTR = "_entry_fitness_diagnostics"


@dataclass(frozen=True)
class _DailySignalPoint:
    idx: int
    date: str
    entry_eligible: bool
    signal: Any
    market_score: float
    sector_score: float
    vix_level: float
    news_sentiment: float
    event_flags: dict
    topic_features: dict

    def to_public_dict(self, *, role: str = "measured") -> dict[str, Any]:
        sig = self.signal
        return {
            "signal_tape_mode": DAILY_SIGNAL_TAPE_MODE,
            "role": str(role),
            "row_index": int(self.idx),
            "date": self.date,
            "entry_eligible": bool(self.entry_eligible),
            "should_buy": bool(getattr(sig, "should_buy", False)),
            "strict_entry": bool(getattr(sig, "strict_entry", False)),
            "strict_interval_pass": (
                bool(getattr(sig, "should_buy", False))
                if bool(getattr(sig, "strict_entry", False))
                else None
            ),
            "quality_score": float(
                getattr(sig, "quality_score", getattr(sig, "score", 0.0)) or 0.0
            ),
            "score": float(getattr(sig, "score", 0.0) or 0.0),
            "raw_score": float(getattr(sig, "raw_score", 0.0) or 0.0),
            "threshold": float(getattr(sig, "threshold", 0.0) or 0.0),
            "reasons": list(getattr(sig, "reasons", []) or []),
            "market_adjustment": float(getattr(sig, "market_adjustment", 0.0) or 0.0),
            "components": dict(getattr(sig, "components", {}) or {}),
            "entry_features": dict(getattr(sig, "entry_features", {}) or {}),
            "interval_checks": dict(getattr(sig, "interval_checks", {}) or {}),
            "market_score": float(self.market_score),
            "sector_score": float(self.sector_score),
            "vix_level": float(self.vix_level),
            "news_sentiment": float(self.news_sentiment),
            "event_flags": dict(self.event_flags or {}),
            "topic_features": dict(self.topic_features or {}),
        }


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if np.isfinite(number) else float(default)


def _entry_fitness_realized_loss_excess(pnl_pct: Any) -> float:
    """Return the realized-loss excess below the -1% tolerance."""
    return float(
        max(
            0.0,
            ENTRY_FITNESS_REALIZED_LOSS_THRESHOLD_PCT - _safe_float(pnl_pct),
        )
    )


def _entry_fitness_is_win(pnl_pct: Any) -> bool:
    """A win requires net realized trade PnL strictly above +0.5%."""
    return bool(_safe_float(pnl_pct) > ENTRY_FITNESS_WIN_THRESHOLD_PCT)


def _entry_fitness_gate_pass(trade_count: int, win_rate_pct: float) -> bool:
    """Strict support-and-quality gate used only by entry-scope fitness."""
    return bool(
        int(trade_count) >= ENTRY_FITNESS_MIN_TRADES
        and _safe_float(win_rate_pct) >= ENTRY_FITNESS_MIN_WIN_RATE_PCT
    )


def _entry_scope_active(rb: Rulebook) -> bool:
    return str(getattr(rb, ENTRY_GA_SCOPE_MARKER, "") or "") == ENTRY_GA_SCOPE_VALUE


def _date_series_for_df(df: pd.DataFrame) -> Optional[pd.Series]:
    if "date" in df.columns:
        return pd.to_datetime(df["date"])
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, index=range(len(df)))
    return None


def _date_at(date_series: Optional[pd.Series], idx: int) -> Optional[pd.Timestamp]:
    if date_series is None:
        return None
    try:
        value = date_series.iloc[int(idx)] if hasattr(date_series, "iloc") else date_series[int(idx)]
        return pd.Timestamp(value)
    except Exception:
        return None


def _fold_end_index(df: pd.DataFrame, end_ts: Optional[pd.Timestamp]) -> Optional[int]:
    if end_ts is None:
        return None
    date_series = _date_series_for_df(df)
    if date_series is None:
        return None
    try:
        dates = pd.to_datetime(date_series, errors="coerce")
        if hasattr(dates, "iloc"):
            positions = [int(i) for i, value in enumerate(dates) if pd.notna(value) and pd.Timestamp(value) <= end_ts]
        else:
            positions = []
        return max(positions) if positions else None
    except Exception:
        return None


def _bounded_exit_df(
    df: pd.DataFrame,
    *,
    end_ts: Optional[pd.Timestamp],
    fold_exit_policy: str,
) -> tuple[pd.DataFrame, Optional[int]]:
    policy = str(fold_exit_policy or "unbounded")
    if policy == "unbounded" or end_ts is None:
        return df, None
    if policy != "fold_end_mark_to_market":
        raise ValueError(f"unsupported fold_exit_policy={fold_exit_policy}")
    fold_idx = _fold_end_index(df, end_ts)
    if fold_idx is None:
        return df.iloc[:0], None
    return df.iloc[: fold_idx + 1], fold_idx


def _entry_plan(
    df: pd.DataFrame,
    signal_idx: int,
    *,
    entry_execution_mode: str,
    end_ts: Optional[pd.Timestamp],
    date_series: Optional[pd.Series],
) -> Optional[dict[str, Any]]:
    mode = str(entry_execution_mode or "close")
    if mode in {"close", "same_close", "legacy_close"}:
        entry_idx = int(signal_idx)
        fill_ts = _date_at(date_series, entry_idx)
        if end_ts is not None and fill_ts is not None and fill_ts > end_ts:
            return None
        signal_ts = _date_at(date_series, signal_idx)
        return {
            "signal_idx": int(signal_idx),
            "entry_idx": entry_idx,
            "entry_price": float(df.iloc[entry_idx]["Close"]),
            "entry_atr": None,
            "entry_execution_mode": "close",
            "entry_signal_date": str(signal_ts.date()) if signal_ts is not None else "",
            "entry_fill_date": str(fill_ts.date()) if fill_ts is not None else "",
        }
    if mode in {"t_plus_1_open", "next_open"}:
        entry_idx = int(signal_idx) + 1
        if entry_idx >= len(df):
            return None
        fill_ts = _date_at(date_series, entry_idx)
        if end_ts is not None and fill_ts is not None and fill_ts > end_ts:
            return None
        entry_price = float(df.iloc[entry_idx].get("Open", df.iloc[entry_idx]["Close"]))
        try:
            atr = float(df.iloc[int(signal_idx)].get("ATR", entry_price * 0.02))
        except Exception:
            atr = entry_price * 0.02
        if pd.isna(atr) or atr <= 0:
            atr = entry_price * 0.02
        signal_ts = _date_at(date_series, signal_idx)
        return {
            "signal_idx": int(signal_idx),
            "entry_idx": entry_idx,
            "entry_price": entry_price,
            "entry_atr": atr,
            "entry_execution_mode": "t_plus_1_open",
            "entry_signal_date": str(signal_ts.date()) if signal_ts is not None else "",
            "entry_fill_date": str(fill_ts.date()) if fill_ts is not None else "",
        }
    raise ValueError(f"unsupported entry_execution_mode={entry_execution_mode}")


def _maybe_relabel_fold_mtm(
    trade: dict[str, Any],
    *,
    df_exit: pd.DataFrame,
    entry_idx: int,
    rb: Rulebook,
    fold_exit_policy: str,
) -> dict[str, Any]:
    if str(fold_exit_policy or "unbounded") != "fold_end_mark_to_market":
        return trade
    if not isinstance(trade, dict) or str(trade.get("exit_reason")) != "time_out":
        return trade
    exit_idx = _find_df_index_by_date(df_exit, trade.get("exit_date"))
    if exit_idx is None or len(df_exit) <= 0:
        return trade
    max_holding_idx = int(entry_idx) + int(getattr(rb, "max_holding_days", 0) or 0)
    truncated_at_fold_end = int(exit_idx) == len(df_exit) - 1 and int(exit_idx) < max_holding_idx
    if not truncated_at_fold_end:
        return trade
    trade = dict(trade)
    trade["exit_reason"] = "fold_end_mark_to_market"
    trade["exit_signal_reason"] = "fold_end_mark_to_market"
    trade["fold_exit_policy"] = "fold_end_mark_to_market"
    trade["fold_end_mark_to_market"] = True
    return trade


def _net_pnl_pct(entry_price: float, exit_price: float, commission_rate: float) -> float:
    """simulate_exit._build_trade와 같은 왕복 비용 차감 수익률."""
    entry = _safe_float(entry_price)
    exit_value = _safe_float(exit_price)
    if entry <= 0.0 or exit_value <= 0.0:
        return 0.0
    commission_per_share = (entry + exit_value) * (max(float(commission_rate), 0.0) / 2.0)
    return float((exit_value - entry - commission_per_share) / entry * 100.0)


def _row_exit_price(row: pd.Series) -> float | None:
    for key in ("Open", "Close"):
        value = _safe_float(row.get(key), float("nan"))
        if np.isfinite(value) and value > 0.0:
            return float(value)
    return None


def _row_date(df: pd.DataFrame, idx: int) -> str:
    try:
        value = df.index[int(idx)]
        return str(value.date()) if hasattr(value, "date") else str(value)
    except Exception:
        return str(idx)


def _attach_entry_exit_local_search(
    trade: dict[str, Any],
    *,
    df_exit: pd.DataFrame,
    entry_idx: int,
    realized_exit_idx: int,
    commission_rate: float,
) -> dict[str, Any]:
    """진입 다음 날부터 최대 7거래일까지 청산 open을 탐색한다.

    이 결과는 fitness, 승패, 실격 게이트에 사용하지 않고 다음 세대 entry
    interval mutation의 widen/narrow 방향 힌트로만 사용한다.
    """
    trade = dict(trade)
    first_idx = int(entry_idx) + 1
    last_idx = min(
        int(entry_idx) + ENTRY_EXIT_LOCAL_SEARCH_MAX_HOLDING_DAYS,
        len(df_exit) - 1,
    )
    realized_idx = int(realized_exit_idx)
    realized_pnl = _safe_float(trade.get("pnl_pct"))
    entry_price = _safe_float(trade.get("avg_cost", trade.get("entry_price")))
    candidates: list[dict[str, Any]] = []

    if first_idx <= last_idx and entry_price > 0.0:
        for candidate_idx in range(first_idx, last_idx + 1):
            if candidate_idx == realized_idx:
                continue
            exit_price = _row_exit_price(df_exit.iloc[candidate_idx])
            if exit_price is None:
                continue
            alternative_pnl = _net_pnl_pct(entry_price, exit_price, commission_rate)
            improvement = alternative_pnl - realized_pnl
            direction = "earlier" if candidate_idx < realized_idx else "later"
            candidates.append(
                {
                    "direction": direction,
                    "exit_index": int(candidate_idx),
                    "exit_date": _row_date(df_exit, candidate_idx),
                    "exit_price": float(exit_price),
                    "net_pnl_pct": float(alternative_pnl),
                    "improvement_pct_point": float(improvement),
                }
            )

    improving = [row for row in candidates if row["improvement_pct_point"] > 1e-12]
    best = max(improving, key=lambda row: row["improvement_pct_point"]) if improving else None
    search = {
        "policy": "sell_open_within_entry_plus_7_trading_days",
        "fitness_input": False,
        "win_gate_input": False,
        "mutation_hint_only": True,
        "entry_index": int(entry_idx),
        "entry_date": _row_date(df_exit, entry_idx),
        "realized_exit_index": int(realized_idx),
        "realized_exit_date": trade.get("exit_date"),
        "realized_net_pnl_pct": float(realized_pnl),
        "search_first_exit_index": int(first_idx),
        "search_last_exit_index": int(last_idx),
        "search_first_exit_date": _row_date(df_exit, first_idx) if first_idx <= last_idx else None,
        "search_last_exit_date": _row_date(df_exit, last_idx) if first_idx <= last_idx else None,
        "candidate_count": len(candidates),
        "better_direction": best["direction"] if best else "none",
        "best_alternative": dict(best) if best else None,
        "within_7_day_cap": bool(last_idx <= int(entry_idx) + ENTRY_EXIT_LOCAL_SEARCH_MAX_HOLDING_DAYS),
    }
    trade["entry_exit_local_search"] = search
    return trade


def _aggregate_entry_exit_mutation_hint(trades: list[dict[str, Any]]) -> dict[str, Any]:
    direction_counts = {"earlier": 0, "later": 0}
    improvement_totals = {"earlier": 0.0, "later": 0.0}
    for trade in trades:
        search = trade.get("entry_exit_local_search") if isinstance(trade, dict) else None
        if not isinstance(search, dict):
            continue
        best = search.get("best_alternative")
        direction = str(search.get("better_direction") or "none")
        if direction not in direction_counts or not isinstance(best, dict):
            continue
        improvement = max(_safe_float(best.get("improvement_pct_point")), 0.0)
        if improvement <= 0.0:
            continue
        direction_counts[direction] += 1
        improvement_totals[direction] += improvement

    total_improvement = improvement_totals["earlier"] + improvement_totals["later"]
    improving_count = direction_counts["earlier"] + direction_counts["later"]
    if total_improvement <= 0.0 or improving_count <= 0:
        direction = "none"
        confidence = 0.0
        strength = 0.0
    else:
        direction = (
            "later"
            if improvement_totals["later"] >= improvement_totals["earlier"]
            else "earlier"
        )
        dominant = improvement_totals[direction]
        confidence = dominant / total_improvement
        opportunity_rate = improving_count / max(len(trades), 1)
        strength = min(1.0, 0.5 * confidence + 0.5 * opportunity_rate)

    return {
        "direction": direction,
        "strength": float(strength),
        "direction_confidence": float(confidence),
        "trade_count": len(trades),
        "improving_trade_count": int(improving_count),
        "direction_counts": direction_counts,
        "improvement_totals_pct_point": improvement_totals,
        "search_cap_trading_days": ENTRY_EXIT_LOCAL_SEARCH_MAX_HOLDING_DAYS,
        "target_genes": "entry_strict_interval_widths_used_by_provisional_interval_break",
        "fitness_input": False,
        "win_gate_input": False,
    }


def _classify_mdd_risk(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = np.asarray([_safe_float(trade.get("pnl_pct")) for trade in trades], dtype=float)
    if pnl.size == 0:
        return {
            "mdd_type": "NO_TRADES",
            "peak_trade_index": None,
            "trough_trade_index": None,
            "episode_trade_indices": [],
            "episode_loss_count": 0,
        }
    cumulative = np.cumsum(pnl)
    running_max = np.maximum.accumulate(cumulative)
    drawdown = cumulative - running_max
    trough = int(np.argmin(drawdown))
    if float(drawdown[trough]) >= -1e-12:
        return {
            "mdd_type": "NO_DRAWDOWN",
            "peak_trade_index": None,
            "trough_trade_index": None,
            "episode_trade_indices": [],
            "episode_loss_count": 0,
        }
    peak_value = float(running_max[trough])
    peak_candidates = np.flatnonzero(
        np.isclose(cumulative[: trough + 1], peak_value, rtol=0.0, atol=1e-12)
    )
    peak = int(peak_candidates[-1]) if peak_candidates.size else -1
    start = peak + 1
    episode_indices = list(range(max(start, 0), trough + 1))
    losses = [idx for idx in episode_indices if pnl[idx] <= 0.0]
    long_loss = any(
        max(int(_safe_float(trades[idx].get("holding_days"), 0.0)), 0)
        >= ENTRY_EXIT_LOCAL_SEARCH_MAX_HOLDING_DAYS
        for idx in losses
    )
    mdd_type = "TYPE1_ACCIDENT" if len(losses) == 1 and not long_loss else "TYPE2_NEGLECT"
    return {
        "mdd_type": mdd_type,
        "peak_trade_index": peak + 1 if peak >= 0 else 0,
        "trough_trade_index": trough + 1,
        "episode_trade_indices": [idx + 1 for idx in episode_indices],
        "episode_loss_count": len(losses),
    }


def _apply_entry_scope_fitness(
    rb: Rulebook,
    result: BacktestResult,
    *,
    complexity_penalty_per_mask: float,
) -> BacktestResult:
    """Entry GA 전용 fitness 원칙을 적용한다.

    본체는 거래별 ``비용 차감 pnl_pct / max(holding_days, 1)``의 평균이다.
    MAE -2% 이탈분 평균과 비용 차감 실현손실 -1% 초과분 평균을 각각 감산한다.
    거래 수 12건 이상과 비용 차감 실현수익 > 0.5% 기준 승률 60% 이상을
    동시에 만족할 때만 fitness를 인정한다. 두 조건 중 하나라도 실패하면
    점수 합산이 아닌 strict disqualification을 적용한다.
    """
    trades = [trade for trade in list(result.trades or []) if isinstance(trade, dict)]
    per_trade_daily_returns: list[float] = []
    mae_excesses: list[float] = []
    realized_loss_excesses: list[float] = []
    wins = 0

    for trade in trades:
        pnl_pct = _safe_float(trade.get("pnl_pct"))
        holding_days = max(int(_safe_float(trade.get("holding_days"), 0.0)), 1)
        daily_return = pnl_pct / holding_days
        mae_pct = min(_safe_float(trade.get("max_loss_during_hold"), 0.0), 0.0)
        mae_excess = max(0.0, ENTRY_FITNESS_MAE_THRESHOLD_PCT - mae_pct)
        realized_loss_excess = _entry_fitness_realized_loss_excess(pnl_pct)
        win_after_cost = _entry_fitness_is_win(pnl_pct)
        trade["entry_fitness_daily_return_pct"] = float(daily_return)
        trade["mae_pct"] = float(mae_pct)
        trade["mae_threshold_pct"] = ENTRY_FITNESS_MAE_THRESHOLD_PCT
        trade["mae_breach_pct_point"] = float(mae_excess)
        trade["realized_loss_threshold_pct"] = ENTRY_FITNESS_REALIZED_LOSS_THRESHOLD_PCT
        trade["realized_loss_breach_pct_point"] = float(realized_loss_excess)
        trade["fitness_win_threshold_pct"] = ENTRY_FITNESS_WIN_THRESHOLD_PCT
        trade["fitness_win_after_cost"] = bool(win_after_cost)
        per_trade_daily_returns.append(float(daily_return))
        mae_excesses.append(float(mae_excess))
        realized_loss_excesses.append(float(realized_loss_excess))
        if win_after_cost:
            wins += 1

    trade_count = len(trades)
    losses = trade_count - wins
    win_rate = (wins / trade_count * 100.0) if trade_count else 0.0
    primary_objective = (
        float(np.mean(np.asarray(per_trade_daily_returns, dtype=float)))
        if per_trade_daily_returns
        else 0.0
    )
    mean_mae_excess = (
        float(np.mean(np.asarray(mae_excesses, dtype=float)))
        if mae_excesses
        else 0.0
    )
    mae_penalty = mean_mae_excess * ENTRY_FITNESS_MAE_PENALTY_WEIGHT
    mean_realized_loss_excess = (
        float(np.mean(np.asarray(realized_loss_excesses, dtype=float)))
        if realized_loss_excesses
        else 0.0
    )
    realized_loss_penalty = (
        mean_realized_loss_excess * ENTRY_FITNESS_REALIZED_LOSS_PENALTY_WEIGHT
    )
    pre_complexity_fitness = (
        primary_objective - mae_penalty - realized_loss_penalty
    )
    post_complexity_fitness = _apply_complexity_penalty(
        rb,
        pre_complexity_fitness,
        complexity_penalty_per_mask,
    )
    trade_count_gate_pass = trade_count >= ENTRY_FITNESS_MIN_TRADES
    win_rate_threshold_pass = win_rate >= ENTRY_FITNESS_MIN_WIN_RATE_PCT
    entry_gate_pass = _entry_fitness_gate_pass(trade_count, win_rate)
    disqualification_reasons: list[str] = []
    if not trade_count_gate_pass:
        disqualification_reasons.append("trade_count_below_minimum")
    if not win_rate_threshold_pass:
        disqualification_reasons.append("win_rate_below_60_pct")
    disqualified = not entry_gate_pass
    final_fitness = ENTRY_FITNESS_DISQUALIFIED if disqualified else post_complexity_fitness
    mdd_risk = _classify_mdd_risk(trades)
    mutation_hint = _aggregate_entry_exit_mutation_hint(trades)
    diagnostics = {
        "scope": ENTRY_GA_SCOPE_VALUE,
        "primary_objective": "mean(net_realized_pnl_pct / max(holding_days, 1))",
        "primary_objective_trade_count_neutral": True,
        "primary_objective_pct_per_day": float(primary_objective),
        "mae_source": "trade.max_loss_during_hold from holding-period daily lows",
        "mae_threshold_pct": ENTRY_FITNESS_MAE_THRESHOLD_PCT,
        "mae_penalty_method": "mean(max(0, -2.0 - mae_pct)) * 1.0",
        "mae_penalty": float(mae_penalty),
        "mae_breach_trade_count": int(sum(value > 0.0 for value in mae_excesses)),
        "realized_loss_source": "trade.pnl_pct net of commission",
        "realized_loss_threshold_pct": ENTRY_FITNESS_REALIZED_LOSS_THRESHOLD_PCT,
        "realized_loss_penalty_method": "mean(max(0, -1.0 - net_realized_pnl_pct)) * 1.0",
        "realized_loss_penalty": float(realized_loss_penalty),
        "realized_loss_breach_trade_count": int(
            sum(value > 0.0 for value in realized_loss_excesses)
        ),
        "total_risk_penalty": float(mae_penalty + realized_loss_penalty),
        "worst_mae_pct": min(
            (_safe_float(trade.get("max_loss_during_hold"), 0.0) for trade in trades),
            default=0.0,
        ),
        "fitness_before_entry_gate": float(post_complexity_fitness),
        "fitness_before_win_gate": float(post_complexity_fitness),
        "final_fitness": float(final_fitness),
        "trade_count": int(trade_count),
        "min_trade_count": ENTRY_FITNESS_MIN_TRADES,
        "trade_count_gate_pass": bool(trade_count_gate_pass),
        "win_definition": "net realized pnl_pct after commission > 0.5",
        "win_threshold_pct": ENTRY_FITNESS_WIN_THRESHOLD_PCT,
        "win_count": int(wins),
        "loss_count": int(losses),
        "win_rate_pct": float(win_rate),
        "win_rate_gate_pct": ENTRY_FITNESS_MIN_WIN_RATE_PCT,
        "win_rate_threshold_pass": bool(win_rate_threshold_pass),
        "entry_gate_rule": "trade_count >= 12 AND win_rate_pct >= 60.0",
        "entry_gate_pass": bool(entry_gate_pass),
        "win_rate_gate_pass": bool(entry_gate_pass),
        "disqualified": bool(disqualified),
        "disqualification_reasons": disqualification_reasons,
        "disqualified_fitness": ENTRY_FITNESS_DISQUALIFIED,
        "mdd_risk": mdd_risk,
        "exit_mutation_hint": mutation_hint,
    }

    result.win_count = wins
    result.loss_count = losses
    result.win_rate = win_rate
    result.fitness = float(final_fitness)
    result.entry_fitness_diagnostics = diagnostics
    result.entry_exit_mutation_hint = mutation_hint
    rb.win_rate = win_rate
    rb.fitness = float(final_fitness)
    setattr(rb, ENTRY_FITNESS_DIAGNOSTICS_ATTR, diagnostics)
    setattr(rb, ENTRY_EXIT_MUTATION_HINT_ATTR, mutation_hint)
    return result


def _apply_fitness_mode(
    rb: Rulebook,
    result: BacktestResult,
    *,
    fitness_mode: str,
    complexity_penalty_per_mask: float,
) -> BacktestResult:
    mode = str(fitness_mode or "legacy")
    if mode == "legacy":
        return result
    if mode == "swing":
        if _entry_scope_active(rb):
            return _apply_entry_scope_fitness(
                rb,
                result,
                complexity_penalty_per_mask=complexity_penalty_per_mask,
            )
        raw_fitness = _calc_fitness_swing(
            expectancy_pct=result.expectancy_pct,
            win_rate=result.win_rate,
            profit_factor=result.profit_factor,
            max_drawdown_pct=result.max_drawdown_pct,
            trade_count=result.trade_count,
            loss_count=result.loss_count,
            profit_concentration=result.profit_concentration,
        )
        result.fitness = _apply_complexity_penalty(rb, raw_fitness, complexity_penalty_per_mask)
        rb.fitness = result.fitness
        return result
    if mode == "spread":
        raise ValueError("spread fitness_mode is not supported by execution_mode_backtest yet")
    raise ValueError(f"unsupported fitness_mode={fitness_mode}")


def _build_daily_signal_tape(
    *,
    rb: Rulebook,
    df: pd.DataFrame,
    warmup: int,
    start_ts: Optional[pd.Timestamp],
    end_ts: Optional[pd.Timestamp],
    date_series: Optional[pd.Series],
    market_score: float,
    sector_score: float,
    vix_level: float,
    market_history_df: Optional[pd.DataFrame],
    sector_name: str,
    ticker_sentiment: Optional[dict],
    topic_feature_map: Optional[dict],
    use_llm_events: bool,
) -> dict[int, _DailySignalPoint]:
    """Start 이후 모든 거래일을 측정하고 entry eligibility를 별도 표시한다."""
    tape: dict[int, _DailySignalPoint] = {}
    for idx in range(max(int(warmup), 0), len(df)):
        cur_ts = _date_at(date_series, idx)
        if start_ts is not None and cur_ts is not None and cur_ts < start_ts:
            continue
        entry_eligible = not (
            end_ts is not None and cur_ts is not None and cur_ts > end_ts
        )

        cur_market, cur_sector, cur_vix, cur_sentiment, cur_event_flags, cur_topic_features = _lookup_signal_context(
            df=df,
            idx=idx,
            market_score=market_score,
            sector_score=sector_score,
            vix_level=vix_level,
            market_history_df=market_history_df,
            sector_name=sector_name,
            ticker_sentiment=ticker_sentiment,
            topic_feature_map=topic_feature_map,
            use_llm_events=use_llm_events,
        )
        signal = evaluate_signal(
            rb,
            df.iloc[: idx + 1],
            market_score=cur_market,
            sector_score=cur_sector,
            vix_level=cur_vix,
            news_sentiment=cur_sentiment,
            event_flags=cur_event_flags,
            topic_features=cur_topic_features,
        )
        tape[idx] = _DailySignalPoint(
            idx=idx,
            date=str(cur_ts.date()) if cur_ts is not None else str(idx),
            entry_eligible=entry_eligible,
            signal=signal,
            market_score=float(cur_market),
            sector_score=float(cur_sector),
            vix_level=float(cur_vix),
            news_sentiment=float(cur_sentiment),
            event_flags=dict(cur_event_flags or {}),
            topic_features=dict(cur_topic_features or {}),
        )
    return tape


def _signal_tape_slice(
    tape: dict[int, _DailySignalPoint],
    start_idx: int,
    end_idx: int,
    *,
    role: str,
) -> list[dict[str, Any]]:
    if end_idx < start_idx:
        return []
    return [
        tape[idx].to_public_dict(role=role)
        for idx in range(max(int(start_idx), 0), int(end_idx) + 1)
        if idx in tape
    ]


def _attach_signal_tape_to_result(
    result: BacktestResult,
    tape: dict[int, _DailySignalPoint],
) -> BacktestResult:
    """BacktestResult API 변경 없이 runtime diagnostics를 확장한다."""
    public_tape = [tape[idx].to_public_dict() for idx in sorted(tape)]
    result.daily_signal_tape_mode = DAILY_SIGNAL_TAPE_MODE
    result.execution_semantics_cache_token = EXECUTION_SEMANTICS_CACHE_TOKEN
    result.daily_signal_tape = public_tape
    result.daily_signal_tape_count = len(public_tape)
    return result


def run_backtest_execution_mode(
    rb: Rulebook,
    df: pd.DataFrame,
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
    fitness_mode: str = "legacy",
    complexity_penalty_per_mask: float = 0.0,
    use_llm_events: bool = True,
    entry_execution_mode: str = "close",
    exit_execution_mode: str = "base",
    fold_exit_policy: str = "unbounded",
    live_hard_stop_guard: bool = False,
) -> BacktestResult:
    """Run a fold-aware learning backtest with explicit execution semantics."""
    trades: list[dict[str, Any]] = []
    entry_scope_active = _entry_scope_active(rb)
    start_ts = pd.Timestamp(start_date) if start_date else None
    end_ts = pd.Timestamp(end_date) if end_date else None
    date_series = _date_series_for_df(df)
    df_exit, _ = _bounded_exit_df(df, end_ts=end_ts, fold_exit_policy=fold_exit_policy)
    topic_window = _news_zscore_window(rb)
    topic_feature_map = _precompute_topic_feature_map(ticker_sentiment, topic_window)
    signal_tape = _build_daily_signal_tape(
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
    i = max(warmup, 0)
    n = len(df)

    while i < n:
        cur_ts = _date_at(date_series, i)
        if start_ts is not None and cur_ts is not None and cur_ts < start_ts:
            i += 1
            continue
        if end_ts is not None and cur_ts is not None and cur_ts > end_ts:
            break

        point = signal_tape.get(i)
        if point is None or not point.entry_eligible:
            i += 1
            continue
        sig = point.signal
        cur_market = point.market_score
        cur_sector = point.sector_score
        cur_vix = point.vix_level
        cur_sentiment = point.news_sentiment
        cur_event_flags = point.event_flags
        cur_topic_features = point.topic_features

        if not sig.should_buy:
            i += 1
            continue

        plan = _entry_plan(
            df,
            i,
            entry_execution_mode=entry_execution_mode,
            end_ts=end_ts,
            date_series=date_series,
        )
        if plan is None:
            i += 1
            continue
        entry_idx = int(plan["entry_idx"])
        if entry_idx >= len(df_exit):
            i += 1
            continue

        amt_krw = calc_position_size_krw(rb, sig.score, position_limit_krw)
        entry_price = float(plan["entry_price"])
        shares = int(amt_krw / entry_price) if entry_price > 0 else 0
        if shares <= 0:
            i += 1
            continue

        trade_obj = simulate_exit(
            rb,
            df_exit,
            entry_idx,
            shares,
            position_limit_krw,
            commission_rate=commission_rate,
            cur_market_score=cur_market,
            cur_vix_level=cur_vix,
            cur_sector_score=cur_sector,
            live_hard_stop_guard=live_hard_stop_guard,
            entry_price_override=entry_price,
            entry_atr_override=plan.get("entry_atr"),
            exit_execution_mode=exit_execution_mode,
        )
        if trade_obj is None:
            i += 1
            continue

        trade = asdict(trade_obj) if hasattr(trade_obj, "__dataclass_fields__") else dict(trade_obj)
        trade.update(
            _signal_snapshot(
                "entry",
                sig,
                sentiment=cur_sentiment,
                market=cur_market,
                sector=cur_sector,
                vix=cur_vix,
                event_flags=cur_event_flags,
                topic_features=cur_topic_features,
            )
        )
        trade["entry_execution_mode"] = plan["entry_execution_mode"]
        trade["exit_execution_mode"] = str(exit_execution_mode or "base")
        trade["fold_exit_policy"] = str(fold_exit_policy or "unbounded")
        trade["entry_signal_date"] = plan.get("entry_signal_date", "")
        trade["entry_fill_date"] = plan.get("entry_fill_date", "")
        trade["daily_signal_tape_mode"] = DAILY_SIGNAL_TAPE_MODE
        trade["execution_semantics_cache_token"] = EXECUTION_SEMANTICS_CACHE_TOKEN
        trade["entry_signal_tape"] = point.to_public_dict(role="entry_signal")
        trade = _maybe_relabel_fold_mtm(
            trade,
            df_exit=df_exit,
            entry_idx=entry_idx,
            rb=rb,
            fold_exit_policy=fold_exit_policy,
        )

        exit_idx = _find_df_index_by_date(df_exit, trade.get("exit_date"))
        if exit_idx is None:
            exit_idx = entry_idx + 1
        if entry_scope_active:
            trade = _attach_entry_exit_local_search(
                trade,
                df_exit=df_exit,
                entry_idx=entry_idx,
                realized_exit_idx=int(exit_idx),
                commission_rate=commission_rate,
            )
        trade["holding_signal_path"] = _signal_tape_slice(
            signal_tape,
            entry_idx,
            int(exit_idx),
            role="holding",
        )
        trade["holding_signal_path_count"] = len(trade["holding_signal_path"])
        cooldown_start = int(exit_idx) + 1
        cooldown_end = int(exit_idx) + max(int(cooldown_days), 0)
        trade["cooldown_signal_path"] = _signal_tape_slice(
            signal_tape,
            cooldown_start,
            cooldown_end,
            role="cooldown",
        )
        trade["cooldown_signal_path_count"] = len(trade["cooldown_signal_path"])
        trades.append(trade)

        i = max(int(exit_idx) + 1 + cooldown_days, entry_idx + 1)

    result = _summarize(rb, trades)
    result = _apply_fitness_mode(
        rb,
        result,
        fitness_mode=fitness_mode,
        complexity_penalty_per_mask=complexity_penalty_per_mask,
    )
    return _attach_signal_tape_to_result(result, signal_tape)


def _synthetic_df() -> pd.DataFrame:
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    close = np.array([100.0 + i * 0.1 for i in range(n)])
    return pd.DataFrame(
        {
            "Open": close + 1.0,
            "High": close + 2.0,
            "Low": close - 2.0,
            "Close": close,
            "Volume": 1_000_000,
            "ATR": 1.0,
            "Aligned_bull": 1,
            "MACD_golden": 0,
            "RSI": 50,
            "BB_lower": close - 5.0,
            "Volume_ratio": 1.0,
        },
        index=idx,
    )


def _synthetic_rulebook() -> Rulebook:
    return Rulebook(
        ticker="PHASE1",
        signal_threshold=0.5,
        weight_ma_align=1.0,
        weight_macd_golden=0.0,
        weight_rsi_zone=0.0,
        weight_bb_near_lower=0.0,
        weight_volume_surge=0.0,
        weight_news_sentiment=0.0,
        market_score_weight=0.0,
        sector_strength_weight=0.0,
        vix_sensitivity=0.0,
        exit_strategy="hybrid",
        stop_loss_atr=100.0,
        take_profit_atr=100.0,
        trailing_atr=100.0,
        trailing_activation_profit_pct=999.0,
        max_holding_days=5,
        base_position_ratio=1.0,
        position_sizing_strategy="fixed",
    )


def run_learning_execution_mode_gate() -> dict[str, Any]:
    df = _synthetic_df()
    start_date = str(df.index[60].date())
    fold_end_date = str(df.index[62].date())
    rb_close = _synthetic_rulebook()
    close_res = run_backtest_execution_mode(
        rb_close,
        df,
        start_date=start_date,
        end_date=fold_end_date,
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="close",
        exit_execution_mode="base",
        fold_exit_policy="unbounded",
    )
    rb_t1 = _synthetic_rulebook()
    t1_res = run_backtest_execution_mode(
        rb_t1,
        df,
        start_date=start_date,
        end_date=fold_end_date,
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
    )
    rb_skip = _synthetic_rulebook()
    skip_res = run_backtest_execution_mode(
        rb_skip,
        df,
        start_date=start_date,
        end_date=start_date,
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
    )
    rb_swing = _synthetic_rulebook()
    swing_res = run_backtest_execution_mode(
        rb_swing,
        df,
        start_date=start_date,
        end_date=fold_end_date,
        warmup=60,
        position_limit_krw=10_000.0,
        entry_execution_mode="t_plus_1_open",
        exit_execution_mode="conservative_core",
        fold_exit_policy="fold_end_mark_to_market",
        fitness_mode="swing",
    )

    close_trade = close_res.trades[0] if close_res.trades else {}
    t1_trade = t1_res.trades[0] if t1_res.trades else {}
    checks = {
        "close_mode_has_trade": bool(close_res.trades),
        "close_mode_entry_uses_signal_close": bool(close_trade) and float(close_trade.get("entry_price", -1)) == float(df.iloc[60]["Close"]),
        "tplus1_has_trade": bool(t1_res.trades),
        "tplus1_signal_date": t1_trade.get("entry_signal_date"),
        "tplus1_fill_date": t1_trade.get("entry_fill_date"),
        "tplus1_entry_uses_next_open": bool(t1_trade) and float(t1_trade.get("entry_price", -1)) == float(df.iloc[61]["Open"]),
        "fold_end_exit_date": t1_trade.get("exit_date"),
        "fold_end_exit_reason": t1_trade.get("exit_reason"),
        "fold_end_no_future_exit": bool(t1_trade) and str(t1_trade.get("exit_date")) <= fold_end_date,
        "fill_after_fold_end_skipped": skip_res.trade_count == 0,
        "swing_fitness_applied": swing_res.fitness != t1_res.fitness,
        "daily_tape_mode": getattr(t1_res, "daily_signal_tape_mode", ""),
        "daily_tape_count": int(getattr(t1_res, "daily_signal_tape_count", 0) or 0),
    }
    checks["passed"] = (
        checks["close_mode_has_trade"]
        and checks["close_mode_entry_uses_signal_close"]
        and checks["tplus1_has_trade"]
        and checks["tplus1_signal_date"] == str(df.index[60].date())
        and checks["tplus1_fill_date"] == str(df.index[61].date())
        and checks["tplus1_entry_uses_next_open"]
        and checks["fold_end_exit_date"] == fold_end_date
        and checks["fold_end_exit_reason"] == "fold_end_mark_to_market"
        and checks["fold_end_no_future_exit"]
        and checks["fill_after_fold_end_skipped"]
        and checks["swing_fitness_applied"]
        and checks["daily_tape_mode"] == DAILY_SIGNAL_TAPE_MODE
        and checks["daily_tape_count"] == len(df) - 60
    )
    summary = {
        "gate": "learning_execution_mode_gate",
        "entry_execution_mode_under_test": "t_plus_1_open",
        "exit_execution_mode_under_test": "conservative_core",
        "fold_exit_policy_under_test": "fold_end_mark_to_market",
        "daily_signal_tape_mode": DAILY_SIGNAL_TAPE_MODE,
        "execution_semantics_cache_token": EXECUTION_SEMANTICS_CACHE_TOKEN,
        "start_date": start_date,
        "fold_end_date": fold_end_date,
        "close_trade_count": close_res.trade_count,
        "tplus1_trade_count": t1_res.trade_count,
        "skip_trade_count": skip_res.trade_count,
        "legacy_fitness": t1_res.fitness,
        "swing_fitness": swing_res.fitness,
        "checks": checks,
        "passed": bool(checks["passed"]),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "summary.json").write_text(__import__("json").dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
