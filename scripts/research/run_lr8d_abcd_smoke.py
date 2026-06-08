#!/usr/bin/env python3
"""LR-8D A+B+C+D integrated smoke.

Checks before the long integrated GA run:
A. survivor stress average expectancy gate
B. swing fitness win-rate bonus and concentration penalty
C. breakeven enabled categorical normalization / exit behavior
D. walk-forward sell_omen score merge and exit behavior
E. tiny integrated GA/backtest persistence shape

This script is research-only and writes no promote/live artifacts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from engine.core.exit_policy import ExitExecutionConfig, MarketContext, PositionState, PriceSnapshot, evaluate_exit
from engine.core.metadata import compute_rulebook_hash
from engine.learning.backtest import _calc_concentration_penalty, _calc_fitness_swing, run_backtest
from engine.learning.genetic import GAConfig, collect_top_rulebooks, random_rulebook, run_ga
from engine.pipeline.context import attach_sell_omen_scores, prepare_ticker_context
from engine.pipeline.rolling_validation import DEFAULT_POSITION_LIMIT_KRW
from engine.pipeline.topn_survivor import evaluate_survivors, score_topn_validation_periods
from engine.strategies.exit_simulator import simulate_exit
from engine.strategies.rulebook import Rulebook, default_rulebook
from scripts.research.rulebook_persist import collect_rulebook_rows
from scripts.research.trade_persist import collect_trade_rows


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _candidate(ticker: str, label: str, year: Any, rulebook_hash: str, exp: float, pf: float = 1.5, trades: int = 8) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "label": label,
        "year": year,
        "rank_is": 1,
        "rulebook_hash": rulebook_hash,
        "train_fitness": 50.0,
        "oos": {
            "trade_count": trades,
            "win_rate": 55.0,
            "expectancy_pct": exp,
            "profit_factor": pf,
            "max_drawdown_pct": -5.0,
        },
    }


def check_a_survivor_stress_gate() -> dict[str, Any]:
    topn = {
        "periods": [
            {"ticker": "GOOD", "label": "2022", "year": 2022, "candidates": [_candidate("GOOD", "2022", 2022, "good_2022", 1.4)]},
            {"ticker": "GOOD", "label": "2023", "year": 2023, "candidates": [_candidate("GOOD", "2023", 2023, "good_2023", 1.5)]},
            {"ticker": "GOOD", "label": "2025H2", "year": "2025H2", "candidates": [_candidate("GOOD", "2025H2", "2025H2", "good_stress", 0.2)]},
            {"ticker": "BAD", "label": "2022", "year": 2022, "candidates": [_candidate("BAD", "2022", 2022, "bad_2022", 1.4)]},
            {"ticker": "BAD", "label": "2023", "year": 2023, "candidates": [_candidate("BAD", "2023", 2023, "bad_2023", 1.5)]},
            {"ticker": "BAD", "label": "2025H2", "year": "2025H2", "candidates": [_candidate("BAD", "2025H2", "2025H2", "bad_stress", -0.2)]},
        ]
    }
    scored = score_topn_validation_periods(topn, general_years=(2022, 2023), stress_labels=("2025H2",))
    survivors = evaluate_survivors(scored, survivor_k=2, min_trades=5, min_member_score=0.0, min_expectancy_pct=1.0, min_stress_expectancy_pct=0.0)
    tickers = {row["ticker"] for row in survivors}
    _assert("GOOD" in tickers, "A: non-negative stress ticker must survive")
    _assert("BAD" not in tickers, "A: negative average stress expectancy ticker must not survive")
    _assert(all(_float(row.get("stress_avg_expectancy_pct"), -1.0) >= 0.0 for row in survivors), "A: survivor stress_avg_expectancy_pct must be >= 0")
    return {"survivor_tickers": sorted(tickers), "stress_avg_expectancies": [row.get("stress_avg_expectancy_pct") for row in survivors], "passed": True}


def check_b_fitness_bias_and_concentration() -> dict[str, Any]:
    base = dict(expectancy_pct=2.0, profit_factor=1.5, max_drawdown_pct=-5.0, trade_count=20, loss_count=8)
    fit_wr40 = _calc_fitness_swing(win_rate=40.0, profit_concentration=0.0, **base)
    fit_wr50 = _calc_fitness_swing(win_rate=50.0, profit_concentration=0.0, **base)
    fit_wr100 = _calc_fitness_swing(win_rate=100.0, profit_concentration=0.0, **base)
    fit_c50 = _calc_fitness_swing(win_rate=50.0, profit_concentration=0.50, **base)
    fit_c75 = _calc_fitness_swing(win_rate=50.0, profit_concentration=0.75, **base)
    _assert(abs(fit_wr40 - fit_wr50) < 1e-9, "B: win-rate below 50 must not be penalized")
    _assert(abs((fit_wr100 - fit_wr50) - 5.0) < 1e-9, "B: win-rate bonus must cap at +5")
    _assert(_calc_concentration_penalty(0.50) == 0.0, "B: concentration <= 50% must have no penalty")
    _assert(_calc_concentration_penalty(0.75) == 20.0, "B: concentration >= 75% must cap at -20")
    _assert(abs((fit_c50 - fit_c75) - 20.0) < 1e-9, "B: concentration penalty must reduce final fitness by 20 at 75%")
    return {
        "fit_wr40": fit_wr40,
        "fit_wr50": fit_wr50,
        "fit_wr100": fit_wr100,
        "fit_concentration_50": fit_c50,
        "fit_concentration_75": fit_c75,
        "passed": True,
    }


def check_c_breakeven() -> dict[str, Any]:
    base = default_rulebook("T")
    seen = {True: 0, False: 0}
    violations = 0
    for _ in range(100):
        rb = random_rulebook(base)
        seen[bool(rb.breakeven_enabled)] += 1
        if rb.breakeven_enabled:
            if not (4.0 <= rb.breakeven_trigger_profit_pct <= 8.0 and 1.0 <= rb.breakeven_floor_profit_pct <= 3.0):
                violations += 1
        else:
            if rb.breakeven_trigger_profit_pct != 0.0 or rb.breakeven_floor_profit_pct != 0.0:
                violations += 1
    _assert(violations == 0, "C: breakeven dependent params must normalize by enabled flag")

    pos = PositionState(
        ticker="T",
        direction="long",
        entry_date="2026-01-01",
        entry_price=100.0,
        avg_cost=100.0,
        shares=1,
        atr_at_entry=2.0,
        stop_price=90.0,
        target_price=130.0,
        trailing_stop=90.0,
        trailing_distance=5.0,
        highest_price=106.0,
        max_holding_days=20,
        exit_strategy="hybrid",
    )
    price = PriceSnapshot(date="2026-01-02", high=106.0, low=101.0, close=102.0, next_open=102.0)
    rb_disabled = Rulebook(ticker="T", breakeven_enabled=False, breakeven_trigger_profit_pct=5.0, breakeven_floor_profit_pct=1.0, exit_strategy="hybrid")
    disabled = evaluate_exit(pos, price, rb_disabled, MarketContext(holding_trading_days=3), ExitExecutionConfig(use_next_open=False))
    rb_enabled = Rulebook(ticker="T", breakeven_enabled=True, breakeven_trigger_profit_pct=5.0, breakeven_floor_profit_pct=1.0, exit_strategy="hybrid")
    enabled = evaluate_exit(pos, price, rb_enabled, MarketContext(holding_trading_days=3), ExitExecutionConfig(use_next_open=False))
    _assert(disabled.should_exit is False and disabled.diagnostics["breakeven_enabled"] is False, "C: disabled breakeven must not fire")
    _assert(enabled.should_exit is True and enabled.reason == "breakeven_stop", "C: enabled breakeven must fire in controlled setup")
    return {"random_seen": {str(k): v for k, v in seen.items()}, "violations": violations, "disabled_exit": disabled.reason, "enabled_exit": enabled.reason, "passed": True}


def check_d_sell_omen_merge_and_exit() -> dict[str, Any]:
    base = pd.DataFrame(
        {
            "Open": [100, 101, 102, 103],
            "High": [100, 102, 103, 104],
            "Low": [100, 100.5, 101.5, 102.5],
            "Close": [100, 101, 102, 103],
            "Volume": [1000, 1100, 1200, 1300],
            "ATR": [2, 2, 2, 2],
        },
        index=pd.to_datetime(["2023-12-29", "2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    merged, info = attach_sell_omen_scores(base, "A")
    _assert(pd.isna(merged.iloc[0]["sell_omen_score"]), "D: 2023 row must have no walk-forward score")
    _assert(pd.notna(merged.iloc[1]["sell_omen_score"]), "D: 2024 row must have walk-forward score")

    rb = Rulebook(ticker="A", exit_strategy="hybrid", max_holding_days=3, sell_omen_enabled=True, sell_omen_threshold=0.10)
    trade = simulate_exit(rb, merged, 0, 1, 10000, cur_market_score=50, cur_vix_level=18, cur_sector_score=50)
    _assert(trade is not None and trade.exit_reason == "sell_omen" and trade.exit_date == "2024-01-02", "D: sell_omen must fire on 2024 scored row")

    early = merged.iloc[[0]].copy()
    rb_early = Rulebook(ticker="A", exit_strategy="hybrid", max_holding_days=1, sell_omen_enabled=True, sell_omen_threshold=0.0)
    no_trade = simulate_exit(rb_early, early, 0, 1, 10000, cur_market_score=50, cur_vix_level=18, cur_sector_score=50)
    _assert(no_trade is None, "D: 2023-only unscored rows must not trigger sell_omen")
    return {"merge_info": info, "sell_omen_exit_date": trade.exit_date, "sell_omen_score": trade.sell_omen_score, "passed": True}


def check_e_integrated_ga_backtest_shape() -> dict[str, Any]:
    ctx = prepare_ticker_context("AAPL")
    df = ctx["df"]
    _assert("sell_omen_score" in df.columns, "E: prepared context df must include sell_omen_score")
    _assert(int(df["sell_omen_score"].notna().sum()) > 0, "E: prepared context must have non-null walk-forward scores")

    base = ctx["base_rulebook"]
    base.sell_omen_enabled = True
    base.sell_omen_threshold = 0.20
    base.breakeven_enabled = True
    base.breakeven_trigger_profit_pct = 5.0
    base.breakeven_floor_profit_pct = 1.0
    base.max_holding_days = 15

    kwargs = {
        "position_limit_krw": DEFAULT_POSITION_LIMIT_KRW,
        "market_history_df": ctx["market_history_df"],
        "sector_name": ctx["sector_name"],
        "ticker_sentiment": ctx["ticker_sentiment"],
        "fitness_mode": "swing",
        "use_llm_events": False,
        "start_date": "2024-01-01",
        "end_date": "2024-06-30",
    }

    forced = run_backtest(base, df, **kwargs)
    sell_omen_count = sum(1 for tr in forced.trades if tr.get("exit_reason") == "sell_omen")
    _assert(forced.trade_count > 0, "E: forced integrated backtest must produce trades")
    _assert(sell_omen_count > 0, "E: forced sell_omen-enabled backtest must produce sell_omen exits")
    _assert(any("breakeven_enabled" in tr and "sell_omen_score" in tr for tr in forced.trades), "E: trade snapshot must include breakeven/sell_omen fields")

    ga_cfg = GAConfig(
        population=6,
        generations=2,
        elite_ratio=0.34,
        mutation_rate=0.20,
        mutation_strength=0.20,
        tournament_size=2,
        seed_pattern_ratio=0.0,
        early_stop_no_improve=2,
        random_seed=20260608,
    )

    def evaluate_fn(rb: Rulebook) -> float:
        res = run_backtest(rb, df, **kwargs)
        return res.fitness

    ga_result = run_ga(base_rulebook=base, evaluate_fn=evaluate_fn, ga_config=ga_cfg)
    candidates = collect_top_rulebooks(ga_result, 6)
    _assert(len(candidates) > 0, "E: tiny GA must produce candidates")

    selected_rows: list[dict[str, Any]] = []
    trade_map: dict[str, dict[str, Any]] = {}
    for rank, rb in enumerate(candidates[:3], 1):
        res = run_backtest(rb, df, **kwargs)
        h = compute_rulebook_hash(rb)
        selected_rows.append(
            {
                "ticker": "AAPL",
                "year": 2024,
                "label": "2024H1_SMOKE",
                "rank_is": rank,
                "rulebook_hash": h,
                "train_fitness": rb.fitness,
                "oos": {
                    "trade_count": res.trade_count,
                    "win_rate": res.win_rate,
                    "expectancy_pct": res.expectancy_pct,
                    "profit_factor": res.profit_factor,
                    "max_drawdown_pct": res.max_drawdown_pct,
                },
            }
        )
        trade_map[h] = {
            "run_key": "AAPL|2024H1_SMOKE",
            "ticker": "AAPL",
            "year": 2024,
            "label": "2024H1_SMOKE",
            "rank_is": rank,
            "rulebook_hash": h,
            "trade_count": res.trade_count,
            "trades": res.trades,
        }

    rulebook_rows = collect_rulebook_rows("AAPL|2024H1_SMOKE", "AAPL", 2024, candidates[:3], selected_rows)
    trade_rows = collect_trade_rows(trade_map, selected_rows)
    _assert(len(rulebook_rows) > 0, "E: rulebook persistence rows must be created")
    _assert(len(trade_rows) > 0, "E: trade persistence rows must be created")
    snapshots = [tr for row in trade_rows for tr in row.get("trades", [])]
    _assert(any("breakeven_enabled" in tr and "sell_omen_score" in tr for tr in snapshots), "E: persisted trade snapshots must include new fields")

    return {
        "ctx_score_info": ctx.get("sell_omen_score"),
        "forced_trade_count": forced.trade_count,
        "forced_sell_omen_count": sell_omen_count,
        "forced_profit_concentration": forced.profit_concentration,
        "tiny_ga_generations": ga_result.generations_run,
        "candidate_count": len(candidates),
        "rulebook_rows": len(rulebook_rows),
        "trade_rows": len(trade_rows),
        "passed": True,
    }


def main() -> int:
    results = {
        "A_survivor_stress_gate": check_a_survivor_stress_gate(),
        "B_fitness_bias_and_concentration": check_b_fitness_bias_and_concentration(),
        "C_breakeven": check_c_breakeven(),
        "D_sell_omen": check_d_sell_omen_merge_and_exit(),
        "E_integrated_ga_backtest_shape": check_e_integrated_ga_backtest_shape(),
    }
    print(json.dumps(results, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    print("LR8D_ABCD_SMOKE_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
