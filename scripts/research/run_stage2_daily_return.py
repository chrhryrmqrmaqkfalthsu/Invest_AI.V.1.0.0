#!/usr/bin/env python3
"""
Stage2 Daily Return Fitness Runner — 일평균 수익률 중심 스윙 연구 러너.

생성 목적: 2026-07-03, 원본 scripts/research/run_stage2.py의 Stage2 학습·검증 흐름과
scripts/research/run_stage2_entry_quality.py의 진입 품질 유전자 3개를 유지하면서,
GA가 "빨리 먹고 나오는" 자본 효율 좋은 개체를 우선 선택하도록 fitness만 교체합니다.

원본 대비 차이:
- 유지: rolling 3분할 train, split별 독립 GA, population 100/generations 50/patience 15,
  전체 population 수집, rulebook hash 대표화, stress → train3 → train2 → train1 → oos early-cut,
  survivor gate, run_stage2.py의 출력 구조.
- 유지: entry_quality 유전자 3개
  1) entry_quality_max_signal_age_days: 1~30
  2) entry_quality_min_dist_high20_pct: 0~20
  3) entry_quality_max_prev5_ret_pct: -10~25
- 교체: 원본 _calc_fitness_swing은 수정하지 않고, run_stage2.py가 받은 backtest 결과의 fitness만
  _calc_fitness_daily_return_result()로 재계산합니다.

새 fitness 핵심:
- 각 거래 daily_return_pct = pnl_pct / max(1, holding_days)
- 주 보상 = 평균 daily_return_pct
- 최소 거래 수, profit factor, MDD, 거래당 expectancy 안전장치는 penalty로 반영합니다.

주의:
- research-only입니다. run_live/실거래/캐시갱신과 무관합니다.
- engine/, run_stage2.py, _calc_fitness_swing은 수정하지 않습니다.
- 진입 품질 피처는 D-1까지 확정된 값만 사용합니다.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.research import run_stage2_entry_quality as entry_quality

MIN_TRADES = 5
MIN_EXPECTANCY_PCT = 0.0
MIN_PROFIT_FACTOR = 1.0
MAX_MDD_PCT = -20.0
FITNESS_VERSION = "daily_return_v1"

_PATCHED = False
_ORIGINAL_STAGE2_BACKTEST = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _trade_daily_returns(trades: list[dict[str, Any]]) -> list[float]:
    daily: list[float] = []
    for trade in trades:
        pnl = _safe_float(trade.get("pnl_pct"), 0.0)
        holding_days = max(1.0, _safe_float(trade.get("holding_days"), 1.0))
        daily.append(pnl / holding_days)
    return daily


def _calc_fitness_daily_return_result(result: Any) -> tuple[float, dict[str, Any]]:
    """BacktestResult의 거래별 일평균 수익률을 주 보상으로 fitness를 재계산한다.

    기존 Stage2 gate가 최종 survivor 단계에서 trade_count/member_score/expectancy/MDD를 다시 보지만,
    GA 학습 중에도 너무 얇은 거래·PF<1·큰 MDD·음수 expectancy 개체가 상위로 올라오지 않도록
    최소 penalty를 적용한다.
    """
    trades = list(getattr(result, "trades", []) or [])
    trade_count = int(getattr(result, "trade_count", len(trades)) or len(trades))
    daily_returns = _trade_daily_returns(trades)
    avg_daily = mean(daily_returns) if daily_returns else 0.0
    med_daily = median(daily_returns) if daily_returns else 0.0
    holding_days = [max(1.0, _safe_float(t.get("holding_days"), 1.0)) for t in trades]
    avg_holding = mean(holding_days) if holding_days else 0.0
    expectancy = _safe_float(getattr(result, "expectancy_pct", 0.0), 0.0)
    profit_factor = _safe_float(getattr(result, "profit_factor", 0.0), 0.0)
    max_drawdown = _safe_float(getattr(result, "max_drawdown_pct", 0.0), 0.0)
    win_rate = _safe_float(getattr(result, "win_rate", 0.0), 0.0)

    # Scale daily pct to the same rough order as 기존 swing fitness.
    reward = avg_daily * 100.0

    trade_penalty = max(0, MIN_TRADES - trade_count) * 30.0
    expectancy_penalty = max(0.0, MIN_EXPECTANCY_PCT - expectancy) * 20.0
    pf_penalty = max(0.0, MIN_PROFIT_FACTOR - profit_factor) * 50.0
    mdd_penalty = max(0.0, abs(max_drawdown) - abs(MAX_MDD_PCT)) * 2.0 if max_drawdown < MAX_MDD_PCT else 0.0

    # Profit factor and nonzero trade count are mild stabilizers, not the main objective.
    pf_bonus = min(25.0, max(0.0, profit_factor - 1.0) * 8.0)
    trade_factor = min(1.0, trade_count / max(1.0, float(MIN_TRADES)))
    win_bonus = min(10.0, max(0.0, win_rate - 50.0) * 0.10)

    fitness = (reward + pf_bonus + win_bonus - trade_penalty - expectancy_penalty - pf_penalty - mdd_penalty) * trade_factor
    stats = {
        "fitness_version": FITNESS_VERSION,
        "avg_daily_return_pct": avg_daily,
        "median_daily_return_pct": med_daily,
        "avg_holding_days": avg_holding,
        "trade_count": trade_count,
        "expectancy_pct": expectancy,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown,
        "win_rate": win_rate,
        "reward_daily_scaled": reward,
        "pf_bonus": pf_bonus,
        "win_bonus": win_bonus,
        "trade_factor": trade_factor,
        "trade_penalty": trade_penalty,
        "expectancy_penalty": expectancy_penalty,
        "pf_penalty": pf_penalty,
        "mdd_penalty": mdd_penalty,
        "fitness": fitness,
    }
    return float(fitness), stats


def patch_daily_return_fitness_runtime() -> None:
    """run_stage2.py 흐름은 그대로 두고 backtest 결과 fitness만 daily-return 기준으로 patch한다."""
    global _PATCHED, _ORIGINAL_STAGE2_BACKTEST
    if _PATCHED:
        return
    entry_quality.patch_entry_quality_runtime()
    stage2 = importlib.import_module("scripts.research.run_stage2")
    _ORIGINAL_STAGE2_BACKTEST = stage2.run_backtest_execution_mode

    def run_backtest_daily_return_fitness(*args: Any, **kwargs: Any) -> Any:
        result = _ORIGINAL_STAGE2_BACKTEST(*args, **kwargs)
        fitness, stats = _calc_fitness_daily_return_result(result)
        try:
            result.fitness = fitness
            result.daily_return_fitness = stats
            result.avg_daily_return_pct = stats["avg_daily_return_pct"]
            result.median_daily_return_pct = stats["median_daily_return_pct"]
            result.avg_holding_days = stats["avg_holding_days"]
        except Exception:
            pass
        return result

    stage2.run_backtest_execution_mode = run_backtest_daily_return_fitness
    _PATCHED = True


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: _json_safe(v) for k, v in vars(value).items() if not str(k).startswith("_")}
    return str(value)


def write_daily_return_manifest(out_dir: Path, summary: dict[str, Any] | None = None) -> None:
    payload = {
        "runner": "scripts/research/run_stage2_daily_return.py",
        "fitness_version": FITNESS_VERSION,
        "fitness_objective": "mean(pnl_pct / max(1, holding_days)) per trade",
        "fitness_formula": {
            "reward": "avg_daily_return_pct * 100",
            "stabilizers": "profit_factor bonus, win_rate bonus, min_trades factor",
            "penalties": "min_trades, expectancy<0, profit_factor<1, max_drawdown<-20",
        },
        "gate_minimums": {
            "min_trades": MIN_TRADES,
            "min_expectancy_pct": MIN_EXPECTANCY_PCT,
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "max_drawdown_pct": MAX_MDD_PCT,
        },
        "entry_quality_genes": {
            "ranges": entry_quality.ENTRY_QUALITY_GENE_RANGES,
            "defaults": entry_quality.ENTRY_QUALITY_DEFAULTS,
        },
        "stage2_flow": "Original scripts/research/run_stage2.py flow is reused; engine and _calc_fitness_swing are not modified.",
        "lookahead": {
            "entry_quality": "Same as run_stage2_entry_quality.py: D-1 confirmed data only",
            "daily_return_fitness": "Uses completed backtest trades inside the evaluated train/eval period; no future data outside the period is used for fitness.",
        },
        "summary": summary or {},
    }
    (out_dir / "daily_return_manifest.json").write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2 runner with entry-quality genes and daily-return fitness")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed-base", type=int, default=None)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--fitness-cache", action="store_true")
    parser.add_argument("--no-fitness-cache", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    patch_daily_return_fitness_runtime()
    stage2 = importlib.import_module("scripts.research.run_stage2")
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else stage2.auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else stage2.default_seed_base(ticker)
    use_fitness_cache = stage2.resolve_fitness_cache_enabled(cli_enabled=bool(args.fitness_cache))
    summary = stage2.run_stage2(
        ticker=ticker,
        out_dir=out_dir,
        seed_base=seed_base,
        parallel=bool(args.parallel),
        use_fitness_cache=use_fitness_cache,
    )
    write_daily_return_manifest(out_dir, summary)
    print(json.dumps(_json_safe({"daily_return_manifest": str(out_dir / "daily_return_manifest.json"), "summary": summary}), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
