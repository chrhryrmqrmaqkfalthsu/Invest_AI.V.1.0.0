#!/usr/bin/env python3
"""
run_stage2.py 복제/파생본: 당일 반전 전용 GA 러너.

생성 목적: 2026-07-03, 기존 Stage2 스윙 GA 러너의 개체 구조와 GA 엔진을 계승하되
당일 시가 진입 후 +1.0% 익절 / -0.5% 손절 / 종가청산 기준 EV fitness를 검증하기 위한
research-only 파일입니다.

원본과의 핵심 차이점:
- 원본 run_stage2.py: swing fitness + 여러 train split + Stage2 survivor gate.
- 변경: intraday reversal EV fitness + GEN(2020~2022) / SELECT(2023) / VAL(2024) / TEST(2025).
- 원본: run_backtest_execution_mode 기반 며칠 보유 스윙 평가.
- 변경: T+1 시가 진입, 당일 고가/저가/종가만 label/청산에 사용.
- 원본: max_holding_days 유전자가 스윙 보유기간에 영향.
- 변경: Rulebook 66개 유전자는 그대로 진화시키되, 평가 시 max_holding_days=1로 강제해 당일 청산 전제로 계산.

주의사항:
- 스윙 파이프라인과 무관한 검증 전용 runner입니다.
- 실전 배포 전 검증 전용이며, 단일 종목 결과는 실전 근거로 쓰면 안 됩니다.
- 원본 run_stage2.py 및 engine/ 하위 파일을 수정하지 않습니다.
- run_live, 실거래, 캐시 갱신, 원격 push와 무관합니다.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from engine.core.metadata import compute_rulebook_hash
from engine.learning.backtest import (
    _lookup_signal_context,
    _news_zscore_window,
    _precompute_topic_feature_map,
)
from engine.learning.genetic import GAConfig, collect_top_rulebooks, run_ga
from engine.pipeline.context import prepare_ticker_context
from engine.strategies.evaluator import evaluate_signal
from engine.strategies.rulebook import CATEGORICAL_PARAMS, PARAM_RANGES, Rulebook

# 원본 run_stage2.py: POPULATION=100 / GENERATIONS=50 / PATIENCE=15.
# 변경 없음: Stage2 GA 뼈대를 그대로 계승합니다.
POPULATION = 100
GENERATIONS = 50
PATIENCE = 15
ELITE_RATIO = 0.2
MUTATION_RATE = 0.15
MUTATION_STRENGTH = 0.2
TOURNAMENT_SIZE = 3
SEED_PATTERN_RATIO = 0.33

# 원본 run_stage2.py: ENTRY_EXECUTION_MODE="t_plus_1_open".
# 변경 없음: 신호일 다음 거래일 시가 진입을 유지합니다.
ENTRY_EXECUTION_MODE = "t_plus_1_open"

# 원본 run_stage2.py: 스윙 청산 엔진 conservative_core.
# 변경: 아래 당일 반전 청산 로직에서 직접 +1%/-0.5%/종가청산을 계산합니다.
TARGET_PCT = 1.0
STOP_PCT = 0.5
ROUND_TRIP_FEE_PCT = 0.05
WARMUP = 200

# 원본 run_stage2.py: 2022-07~2025-06 rolling train split.
# 변경: look-ahead 차단용 4중 분할을 연도 경계로 강제합니다.
PERIODS: dict[str, tuple[str, str]] = {
    "GEN": ("2020-01-01", "2022-12-31"),
    "SELECT": ("2023-01-01", "2023-12-31"),
    "VAL": ("2024-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2025-12-31"),
}

A2B_DEFAULT_THRESHOLDS = {
    "prev_intr1_le": -2.4269,
    "lag2_range_ge": 4.3822,
    "gap_le": -0.6862,
    "qqq_ret5_le": 1.9622,
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        v = float(value)
        return v if math.isfinite(v) else default
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


def default_seed_base(ticker: str) -> int:
    # 원본 run_stage2.py: ticker별 deterministic seed base.
    # 변경 없음: 재현 가능한 GA 실행을 유지합니다.
    return 2026070300 + sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker.upper()))


def auto_out_dir(ticker: str, root: Path = PROJECT_ROOT) -> Path:
    today = time.strftime("%Y%m%d")
    prefix = f"exp_{ticker.lower()}_intraday_reversal_ga_{today}_"
    for idx in range(1, 10000):
        candidate = root / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate output directory for {ticker}: exhausted {prefix}NNNN")


def configure_logging(out_dir: Path) -> logging.Logger:
    logger = logging.getLogger("run_intraday_reversal_ga")
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


def _rulebook_as_intraday(rulebook: Rulebook) -> Rulebook:
    # 원본: max_holding_days는 Rulebook 유전자 범위에서 진화.
    # 변경: 유전자 공간에는 남겨두되 평가 시 당일 청산 목적상 1로 강제합니다.
    rb = copy.deepcopy(rulebook)
    rb.max_holding_days = 1
    return rb


class IntradayReversalEvaluator:
    """당일 반전 전용 evaluator.

    원본 run_stage2.py는 run_backtest_execution_mode().fitness를 사용합니다.
    변경: 신호는 기존 evaluate_signal()로 만들고, 청산/fitness만 당일 EV로 계산합니다.
    """

    def __init__(self, *, ticker: str, ctx: dict[str, Any], logger: logging.Logger | None = None):
        self.ticker = ticker
        self.ctx = ctx
        self.logger = logger or logging.getLogger("run_intraday_reversal_ga")
        self.df = ctx["df"].copy().sort_index()
        self.df.index = pd.to_datetime(self.df.index)
        self.market_history_df = ctx.get("market_history_df")
        self.sector_name = ctx.get("sector_name", "tech")
        self.ticker_sentiment = ctx.get("ticker_sentiment")
        self._context_cache: dict[tuple[int, int], tuple[Any, ...]] = {}
        self._metric_cache: dict[tuple[str, str], dict[str, Any]] = {}

        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col not in self.df.columns:
                raise ValueError(f"{ticker} OHLCV missing required column: {col}")
        self.open = self.df["Open"].astype(float).to_numpy()
        self.high = self.df["High"].astype(float).to_numpy()
        self.low = self.df["Low"].astype(float).to_numpy()
        self.close = self.df["Close"].astype(float).to_numpy()
        self.index = self.df.index
        self.range_pct = (self.high - self.low) / self.close * 100.0
        self.intraday_pct = (self.close - self.open) / self.open * 100.0
        self.eligible_indices = self._build_eligible_indices()
        self.qqq_ret5_by_date = self._load_qqq_ret5()

    def _build_eligible_indices(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        start_i = max(WARMUP + 1, 21)
        for label, (start, end) in PERIODS.items():
            s = pd.Timestamp(start)
            e = pd.Timestamp(end)
            out[label] = [i for i in range(start_i, len(self.df)) if s <= self.index[i] <= e]
        return out

    def _load_qqq_ret5(self) -> dict[str, float]:
        qqq_path = PROJECT_ROOT / "data/_system/research/honest_full_6174_20260616_stage01_full/stage0/ohlcv_cache/QQQ.pkl"
        if not qqq_path.exists():
            return {}
        qqq = pd.read_pickle(qqq_path).sort_index()
        if isinstance(qqq.columns, pd.MultiIndex):
            qqq.columns = qqq.columns.get_level_values(0)
        qqq.index = pd.to_datetime(qqq.index)
        close = qqq["Close"].astype(float).to_numpy()
        out: dict[str, float] = {}
        for i in range(6, len(qqq)):
            out[qqq.index[i].strftime("%Y-%m-%d")] = (close[i - 1] / close[i - 6] - 1.0) * 100.0
        return out

    def _signal_context(self, signal_idx: int, rb: Rulebook) -> tuple[Any, ...]:
        # 원본/engine: FEATURE_LAG_DAYS 기반 _lookup_signal_context 사용.
        # 변경 없음: market/news는 D-1 확정분 lookup 로직을 유지합니다.
        topic_window = int(_news_zscore_window(rb))
        key = (signal_idx, topic_window)
        if key not in self._context_cache:
            topic_map = _precompute_topic_feature_map(self.ticker_sentiment, topic_window)
            self._context_cache[key] = _lookup_signal_context(
                df=self.df,
                idx=signal_idx,
                market_score=50.0,
                sector_score=50.0,
                vix_level=18.0,
                market_history_df=self.market_history_df,
                sector_name=self.sector_name,
                ticker_sentiment=self.ticker_sentiment,
                topic_feature_map=topic_map,
                use_llm_events=False,
            )
        return self._context_cache[key]

    def _trade_outcome(self, entry_idx: int) -> dict[str, Any]:
        entry = float(self.open[entry_idx])
        high = float(self.high[entry_idx])
        low = float(self.low[entry_idx])
        close = float(self.close[entry_idx])
        target_price = entry * (1.0 + TARGET_PCT / 100.0)
        stop_price = entry * (1.0 - STOP_PCT / 100.0)
        target_hit = high >= target_price
        stop_hit = low <= stop_price
        close_ret = (close / entry - 1.0) * 100.0

        # 원본: simulate_exit()가 스윙 청산을 수행.
        # 변경: 일봉 path ambiguity 때문에 비관적 fitness는 stop-first로 계산합니다.
        if stop_hit:
            pess_ret = -STOP_PCT
            pess_reason = "STOP"
        elif target_hit:
            pess_ret = TARGET_PCT
            pess_reason = "TAKE_PROFIT"
        else:
            pess_ret = close_ret
            pess_reason = "CLOSE"

        if target_hit:
            opt_ret = TARGET_PCT
            opt_reason = "TAKE_PROFIT"
        elif stop_hit:
            opt_ret = -STOP_PCT
            opt_reason = "STOP"
        else:
            opt_ret = close_ret
            opt_reason = "CLOSE"

        return {
            "entry_date": str(self.index[entry_idx].date()),
            "entry_price": entry,
            "day_high": high,
            "day_low": low,
            "day_close": close,
            "target_hit": bool(target_hit),
            "stop_hit": bool(stop_hit),
            "pnl_pess_pct": pess_ret - ROUND_TRIP_FEE_PCT,
            "pnl_opt_pct": opt_ret - ROUND_TRIP_FEE_PCT,
            "pess_exit_reason": pess_reason,
            "opt_exit_reason": opt_reason,
            "close_ret_net_pct": close_ret - ROUND_TRIP_FEE_PCT,
        }

    def evaluate_rulebook(self, rulebook: Rulebook, period: str) -> dict[str, Any]:
        rb = _rulebook_as_intraday(rulebook)
        rb_hash = compute_rulebook_hash(rb)
        key = (rb_hash, period)
        if key in self._metric_cache:
            return self._metric_cache[key]

        trades: list[dict[str, Any]] = []
        indices = self.eligible_indices.get(period, [])
        for entry_idx in indices:
            signal_idx = entry_idx - 1
            window = self.df.iloc[max(0, signal_idx - 80) : signal_idx + 1]
            market_score, sector_score, vix_level, sentiment, event_flags, topic_features = self._signal_context(signal_idx, rb)
            try:
                signal = evaluate_signal(
                    rb,
                    window,
                    market_score=market_score,
                    sector_score=sector_score,
                    vix_level=vix_level,
                    news_sentiment=sentiment,
                    event_flags=event_flags,
                    topic_features=topic_features,
                )
            except Exception:
                continue
            if not bool(getattr(signal, "should_buy", False)):
                continue
            trade = self._trade_outcome(entry_idx)
            trade.update(
                {
                    "signal_score": safe_float(getattr(signal, "score", 0.0)),
                    "signal_threshold": safe_float(getattr(signal, "threshold", 0.0)),
                    "signal_reasons": list(getattr(signal, "reasons", []) or []),
                }
            )
            trades.append(trade)
        metrics = _summarize_intraday_reversal(trades, len(indices))
        metrics["period"] = period
        metrics["rulebook_hash"] = rb_hash
        metrics["trades"] = trades
        self._metric_cache[key] = metrics
        return metrics

    def evaluate_a2b_baseline(self, period: str, *, with_qqq: bool = False) -> dict[str, Any]:
        trades: list[dict[str, Any]] = []
        indices = self.eligible_indices.get(period, [])
        for entry_idx in indices:
            date_key = self.index[entry_idx].strftime("%Y-%m-%d")
            gap = (self.open[entry_idx] / self.close[entry_idx - 1] - 1.0) * 100.0
            if not (
                self.intraday_pct[entry_idx - 1] <= A2B_DEFAULT_THRESHOLDS["prev_intr1_le"]
                and self.range_pct[entry_idx - 2] >= A2B_DEFAULT_THRESHOLDS["lag2_range_ge"]
                and gap <= A2B_DEFAULT_THRESHOLDS["gap_le"]
            ):
                continue
            if with_qqq:
                qqq_ret5 = self.qqq_ret5_by_date.get(date_key)
                if qqq_ret5 is None or qqq_ret5 > A2B_DEFAULT_THRESHOLDS["qqq_ret5_le"]:
                    continue
            trades.append(self._trade_outcome(entry_idx))
        metrics = _summarize_intraday_reversal(trades, len(indices))
        metrics["period"] = period
        metrics["rulebook_hash"] = "A2B_PLUS_QQQ" if with_qqq else "A2B"
        metrics["trades"] = trades
        return metrics


def _calc_fitness_intraday_reversal(metrics: dict[str, Any]) -> float:
    """당일 반전 EV 전용 fitness.

    원본 _calc_fitness_swing은 수정/호출하지 않습니다.
    이 함수는 신규 runner 내부 전용입니다.
    """
    n = int(metrics.get("trade_count", 0) or 0)
    if n <= 0:
        return -999.0
    ev = safe_float(metrics.get("ev_pess_pct"))
    coverage = safe_float(metrics.get("coverage_pct"))
    stop_rate = safe_float(metrics.get("stop_rate_pess_pct"))

    # 원본 swing fitness: trade_count factor로 작은 표본을 완화/처벌.
    # 변경: 최소 30건 미만은 강한 penalty, coverage 1% 미만도 penalty.
    if n < 30:
        trade_factor = max(0.05, n / 30.0 * 0.25)
        sample_penalty = 20.0 * (1.0 - n / 30.0)
    else:
        trade_factor = 1.0
        sample_penalty = 0.0
    coverage_factor = 1.0 if coverage >= 1.0 else max(0.10, coverage / 1.0)

    # STOP 비율 과다 방지: 70% 초과부터 선형 벌점.
    stop_penalty = max(0.0, stop_rate - 70.0) * 0.40
    return float(ev * 100.0 * trade_factor * coverage_factor - sample_penalty - stop_penalty)


def _summarize_intraday_reversal(trades: list[dict[str, Any]], eligible_days: int) -> dict[str, Any]:
    n = len(trades)
    if n <= 0:
        metrics = {
            "trade_count": 0,
            "eligible_days": int(eligible_days),
            "coverage_pct": 0.0,
            "win_rate_pess_pct": 0.0,
            "ev_pess_pct": 0.0,
            "ev_opt_pct": 0.0,
            "tp_rate_pess_pct": 0.0,
            "stop_rate_pess_pct": 0.0,
            "close_exit_rate_pess_pct": 0.0,
            "avg_take_profit_ret_pct": 0.0,
            "avg_stop_loss_ret_pct": 0.0,
            "avg_close_exit_ret_pct": 0.0,
            "insufficient_sample": True,
        }
        metrics["fitness"] = _calc_fitness_intraday_reversal(metrics)
        return metrics

    pess = np.array([safe_float(t.get("pnl_pess_pct")) for t in trades], dtype=float)
    opt = np.array([safe_float(t.get("pnl_opt_pct")) for t in trades], dtype=float)
    reasons = [str(t.get("pess_exit_reason") or "") for t in trades]
    tp_vals = [safe_float(t.get("pnl_pess_pct")) for t in trades if t.get("pess_exit_reason") == "TAKE_PROFIT"]
    stop_vals = [safe_float(t.get("pnl_pess_pct")) for t in trades if t.get("pess_exit_reason") == "STOP"]
    close_vals = [safe_float(t.get("pnl_pess_pct")) for t in trades if t.get("pess_exit_reason") == "CLOSE"]
    metrics = {
        "trade_count": int(n),
        "eligible_days": int(eligible_days),
        "coverage_pct": float(n / max(1, eligible_days) * 100.0),
        "win_rate_pess_pct": float((pess > 0).mean() * 100.0),
        "ev_pess_pct": float(pess.mean()),
        "ev_opt_pct": float(opt.mean()),
        "tp_rate_pess_pct": float(reasons.count("TAKE_PROFIT") / n * 100.0),
        "stop_rate_pess_pct": float(reasons.count("STOP") / n * 100.0),
        "close_exit_rate_pess_pct": float(reasons.count("CLOSE") / n * 100.0),
        "avg_take_profit_ret_pct": float(mean(tp_vals)) if tp_vals else 0.0,
        "avg_stop_loss_ret_pct": float(mean(stop_vals)) if stop_vals else 0.0,
        "avg_close_exit_ret_pct": float(mean(close_vals)) if close_vals else 0.0,
        "insufficient_sample": n < 30,
    }
    metrics["fitness"] = _calc_fitness_intraday_reversal(metrics)
    return metrics


def metric_brief(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if k != "trades"}


def dump_gene_delta(base_rulebook: Rulebook, final_rulebook: Rulebook) -> dict[str, Any]:
    base = base_rulebook.to_dict() if hasattr(base_rulebook, "to_dict") else {}
    final = final_rulebook.to_dict() if hasattr(final_rulebook, "to_dict") else {}
    keys = list(PARAM_RANGES.keys()) + list(CATEGORICAL_PARAMS.keys())
    rows = []
    moved = []
    for key in keys:
        b = base.get(key)
        f = final.get(key)
        changed = b != f
        if changed:
            moved.append(key)
        rows.append(
            {
                "gene": key,
                "initial": b,
                "final": f,
                "changed_from_initial": bool(changed),
                "gene_type": "numeric" if key in PARAM_RANGES else "categorical",
            }
        )
    watched = [
        "market_score_weight",
        "sector_strength_weight",
        "vix_sensitivity",
        "weight_news_sentiment",
        "use_event_block",
        "use_market_entry_adjustment",
        "use_news_global",
        "event_strength_multiplier",
    ]
    return {
        "param_ranges_count": len(PARAM_RANGES),
        "categorical_params_count": len(CATEGORICAL_PARAMS),
        "total_gene_count": len(keys),
        "changed_gene_count": len(moved),
        "changed_genes": moved,
        "watched_gene_deltas": [row for row in rows if row["gene"] in watched],
        "all_gene_values": rows,
    }


def run_intraday_reversal_ga(
    *,
    ticker: str,
    out_dir: Path,
    seed_base: int,
    top_n: int = 100,
) -> dict[str, Any]:
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)
    logger = configure_logging(out_dir)
    logger.info("intraday reversal GA start ticker=%s out_dir=%s", ticker, out_dir)
    ctx = prepare_ticker_context(ticker)
    evaluator = IntradayReversalEvaluator(ticker=ticker, ctx=ctx, logger=logger)
    base_rulebook = ctx["base_rulebook"]

    config = {
        "ticker": ticker,
        "runner": "scripts/research/run_intraday_reversal_ga.py",
        "source_runner": "scripts/research/run_stage2.py",
        "research_only": True,
        "not_live_basis": True,
        "single_ticker_pilot": True,
        "survivorship_bias_tag": True,
        "low_coverage_tag": True,
        "ga": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "early_stop_no_improve": PATIENCE,
            "elite_ratio": ELITE_RATIO,
            "mutation_rate": MUTATION_RATE,
            "mutation_strength": MUTATION_STRENGTH,
            "tournament_size": TOURNAMENT_SIZE,
            "seed_pattern_ratio": SEED_PATTERN_RATIO,
            "random_seed_base": seed_base,
            "individual": "Rulebook parameter vector",
            "numeric_genes": len(PARAM_RANGES),
            "categorical_genes": len(CATEGORICAL_PARAMS),
            "total_genes": len(PARAM_RANGES) + len(CATEGORICAL_PARAMS),
        },
        "intraday_reversal": {
            "entry_execution_mode": ENTRY_EXECUTION_MODE,
            "max_holding_days_for_evaluation": 1,
            "target_pct": TARGET_PCT,
            "stop_pct": STOP_PCT,
            "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
            "pessimistic_path_rule": "if high hits target and low hits stop on same daily bar, count STOP first",
        },
        "periods": PERIODS,
        "eligible_days": {period: len(indices) for period, indices in evaluator.eligible_indices.items()},
        "lookahead_controls": [
            "signal uses df up to D-1 only",
            "entry uses T+1 open",
            "D high/low/close are used only for intraday label/exit result",
            "_lookup_signal_context keeps D-1 lagged market/news feature lookup",
        ],
    }
    write_json(out_dir / "config.json", config)

    baseline0 = {period: evaluator.evaluate_a2b_baseline(period, with_qqq=False) for period in PERIODS}
    baseline1 = {period: evaluator.evaluate_a2b_baseline(period, with_qqq=True) for period in PERIODS}
    logger.info("baseline A2B TEST=%s", metric_brief(baseline0["TEST"]))
    logger.info("baseline A2B+QQQ TEST=%s", metric_brief(baseline1["TEST"]))

    history: list[dict[str, Any]] = []

    def evaluate_fn(rulebook: Rulebook) -> float:
        return evaluator.evaluate_rulebook(rulebook, "GEN")["fitness"]

    def on_generation(generation: int, best: Any, avg: float) -> None:
        history.append(
            {
                "generation": int(generation),
                "best_fitness": safe_float(getattr(best, "fitness", 0.0)),
                "avg_fitness": safe_float(avg),
                "best_rulebook_hash": compute_rulebook_hash(_rulebook_as_intraday(best)),
            }
        )
        if generation == 1 or generation % 10 == 0:
            logger.info("GA gen=%s best=%.6f avg=%.6f", generation, safe_float(getattr(best, "fitness", 0.0)), safe_float(avg))

    ga_config = GAConfig(
        population=POPULATION,
        generations=GENERATIONS,
        elite_ratio=ELITE_RATIO,
        mutation_rate=MUTATION_RATE,
        mutation_strength=MUTATION_STRENGTH,
        tournament_size=TOURNAMENT_SIZE,
        seed_pattern_ratio=SEED_PATTERN_RATIO,
        early_stop_no_improve=PATIENCE,
        random_seed=seed_base,
    )
    ga_result = run_ga(base_rulebook=base_rulebook, evaluate_fn=evaluate_fn, ga_config=ga_config, on_generation=on_generation)
    generations_run = int(getattr(ga_result, "generations_run", 0) or 0)
    early_stop = generations_run < GENERATIONS

    candidates = collect_top_rulebooks(ga_result, top_n)
    candidate_rows: list[dict[str, Any]] = []
    for rank, rb in enumerate(candidates, 1):
        fixed = _rulebook_as_intraday(rb)
        rb_hash = compute_rulebook_hash(fixed)
        period_metrics = {period: evaluator.evaluate_rulebook(fixed, period) for period in PERIODS}
        row = {
            "rank_by_gen": rank,
            "rulebook_hash": rb_hash,
            "gen_fitness": period_metrics["GEN"]["fitness"],
            "select_fitness": period_metrics["SELECT"]["fitness"],
            "select_ev_pess_pct": period_metrics["SELECT"]["ev_pess_pct"],
            "select_trade_count": period_metrics["SELECT"]["trade_count"],
            "period_metrics": {period: metric_brief(period_metrics[period]) for period in PERIODS},
            "rulebook": fixed.to_dict() if hasattr(fixed, "to_dict") else json_safe(fixed),
        }
        candidate_rows.append(row)

    # 원본 run_stage2.py: Stage2 gate로 survivor 선별.
    # 변경: SELECT(2023) fitness/EV/trade_count만 사용해 freeze합니다. VAL/TEST는 선택에 쓰지 않습니다.
    candidate_rows.sort(
        key=lambda row: (
            safe_float(row.get("select_fitness")),
            safe_float(row.get("select_ev_pess_pct")),
            int(row.get("select_trade_count", 0) or 0),
            safe_float(row.get("gen_fitness")),
        ),
        reverse=True,
    )
    selected = candidate_rows[0] if candidate_rows else None
    if selected is None:
        raise RuntimeError("GA produced no candidates")
    selected_rb = Rulebook.from_dict(dict(selected["rulebook"]))
    selected_metrics = selected["period_metrics"]
    gene_delta = dump_gene_delta(_rulebook_as_intraday(base_rulebook), selected_rb)

    summary = {
        "ticker": ticker,
        "elapsed_sec": time.time() - started,
        "generations_run": generations_run,
        "early_stop_triggered": early_stop,
        "candidate_count": len(candidate_rows),
        "selected_rulebook_hash": selected["rulebook_hash"],
        "selected_by": "SELECT fitness only; VAL/TEST not used for selection",
        "baseline_a2b": {period: metric_brief(baseline0[period]) for period in PERIODS},
        "baseline_a2b_plus_qqq_ret5": {period: metric_brief(baseline1[period]) for period in PERIODS},
        "selected_period_metrics": selected_metrics,
        "ev_curve_pess_pct": {period: selected_metrics[period]["ev_pess_pct"] for period in PERIODS},
        "ev_drop_test_minus_gen_pct_points": selected_metrics["TEST"]["ev_pess_pct"] - selected_metrics["GEN"]["ev_pess_pct"],
        "gene_delta": gene_delta,
        "pilot_tags": {
            "single_ticker_pilot": True,
            "not_live_basis": True,
            "survivorship_bias": True,
            "low_coverage_expected": True,
            "project_files_modified_by_run": False,
            "cache_updated": False,
            "run_live": False,
        },
        "outputs": {
            "config": str(out_dir / "config.json"),
            "summary": str(out_dir / "summary.json"),
            "ga_history": str(out_dir / "ga_history.jsonl"),
            "candidates": str(out_dir / "candidates.jsonl"),
            "selected_rulebook": str(out_dir / "selected_rulebook.json"),
            "run_log": str(out_dir / "run.log"),
        },
    }
    write_jsonl(out_dir / "ga_history.jsonl", history)
    write_jsonl(out_dir / "candidates.jsonl", candidate_rows)
    write_json(out_dir / "selected_rulebook.json", selected["rulebook"])
    write_json(out_dir / "summary.json", summary)
    logger.info("intraday reversal GA done summary=%s", {k: summary[k] for k in ["ticker", "generations_run", "selected_rulebook_hash", "ev_drop_test_minus_gen_pct_points"]})
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Intraday reversal GA runner copied from Stage2 GA skeleton")
    parser.add_argument("--ticker", required=True, help="Ticker symbol, e.g. LASR")
    parser.add_argument("--out-dir", default=None, help="Output directory. Default: exp_<ticker>_intraday_reversal_ga_<YYYYMMDD>_NNNN")
    parser.add_argument("--seed-base", type=int, default=None, help="Deterministic GA seed base")
    parser.add_argument("--top-n", type=int, default=100, help="Number of final GA rulebooks evaluated on SELECT/VAL/TEST")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    run_intraday_reversal_ga(ticker=ticker, out_dir=out_dir, seed_base=seed_base, top_n=max(1, int(args.top_n)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
