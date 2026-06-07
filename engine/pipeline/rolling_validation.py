"""Rolling validation for the new staged pipeline.

Phase AM implements a single-ticker end-to-end path, verified first with NVDA.
The implementation intentionally evaluates only ga_result.best for OOS in this
first step. A later phase can replace this with top-N ensemble OOS evaluation.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import uuid4

from engine.core.feature_lag import FEATURE_LAG_METADATA
from engine.core.metadata import build_metadata, compute_rulebook_hash
from engine.learning.backtest import run_backtest
from engine.learning.genetic import GAConfig, GAResult, collect_top_rulebooks, run_ga
from engine.pipeline.context import DEFAULT_ROLLING_YEARS, make_year_splits, prepare_ticker_context
from engine.pipeline.scoring import score_stock_from_rolling

# PROVISIONAL — NVDA 연결 검증용 작은 GA. 전체 배치 전 분포/시간 확인 후 확정한다.
DEFAULT_ROLLING_GA_CONFIG = GAConfig(
    population=20,
    generations=15,
    elite_ratio=0.2,
    mutation_rate=0.15,
    mutation_strength=0.2,
    tournament_size=3,
    seed_pattern_ratio=0.33,
    early_stop_no_improve=5,
    random_seed=20260604,
)

DEFAULT_POSITION_LIMIT_KRW = 120_000.0


def _float_or_zero(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _ga_config_to_plain_dict(ga_config: GAConfig | None) -> dict[str, Any]:
    if ga_config is None:
        return asdict(DEFAULT_ROLLING_GA_CONFIG)
    try:
        return asdict(ga_config)
    except Exception:
        return {}


def _rulebook_to_dict(rulebook: Any) -> dict[str, Any]:
    if rulebook is None:
        return {}
    if isinstance(rulebook, dict):
        return dict(rulebook)
    method = getattr(rulebook, "to_dict", None)
    if callable(method):
        try:
            value = method()
            return dict(value) if isinstance(value, dict) else {}
        except Exception:
            return {}
    try:
        return asdict(rulebook)
    except Exception:
        raw = getattr(rulebook, "__dict__", None)
        if isinstance(raw, dict):
            return {str(k): v for k, v in raw.items() if not str(k).startswith("_")}
    return {}


def _ga_summary(ga_result: GAResult | None) -> dict[str, Any]:
    if ga_result is None:
        return {}
    best = getattr(ga_result, "best", None)
    return {
        "generations_run": getattr(ga_result, "generations_run", None),
        "best_fitness": _float_or_zero(getattr(best, "fitness", 0.0)),
        "population_size": len(getattr(ga_result, "final_population", []) or []),
    }


def _topn_candidate_to_oos_row(
    *,
    year: int,
    rank_is: int,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    rulebook: Any,
    result: Any,
    train_fitness: float,
) -> dict[str, Any]:
    rulebook_hash = compute_rulebook_hash(rulebook)
    return {
        "year": int(year),
        "rank_is": int(rank_is),
        "rulebook_hash": rulebook_hash,
        "train_fitness": _float_or_zero(train_fitness),
        "train_period": [train_start, train_end],
        "test_period": [test_start, test_end],
        "oos": {
            "trade_count": int(getattr(result, "trade_count", 0) or 0),
            "win_rate": _float_or_zero(getattr(result, "win_rate", 0.0)),
            "expectancy_pct": _float_or_zero(getattr(result, "expectancy_pct", 0.0)),
            "profit_factor": _float_or_zero(getattr(result, "profit_factor", 0.0)),
            "max_drawdown_pct": _float_or_zero(getattr(result, "max_drawdown_pct", 0.0)),
        },
        "fitness": _float_or_zero(getattr(result, "fitness", 0.0)),
    }


def backtest_result_to_oos_period(
    *,
    year: int,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    result: Any,
    ga_result: GAResult | None = None,
) -> dict[str, Any]:
    """Adapt BacktestResult to score_stock_from_rolling period format."""
    best = getattr(ga_result, "best", None) if ga_result is not None else None
    return {
        "year": int(year),
        "train_period": [train_start, train_end],
        "test_period": [test_start, test_end],
        "oos": {
            "trade_count": int(getattr(result, "trade_count", 0) or 0),
            "win_rate": _float_or_zero(getattr(result, "win_rate", 0.0)),
            "expectancy_pct": _float_or_zero(getattr(result, "expectancy_pct", 0.0)),
            "profit_factor": _float_or_zero(getattr(result, "profit_factor", 0.0)),
            "max_drawdown_pct": _float_or_zero(getattr(result, "max_drawdown_pct", 0.0)),
        },
        "fitness": _float_or_zero(getattr(result, "fitness", 0.0)),
        "trades": list(getattr(result, "trades", []) or []),
        "ga": _ga_summary(ga_result),
        "best_rulebook": _rulebook_to_dict(best),
        "best_rulebook_hash": compute_rulebook_hash(best) if best is not None else "",
    }


def run_rolling_validation(
    ticker: str,
    context: dict[str, Any] | None = None,
    ga_config: GAConfig | None = None,
    fitness_mode: str = "swing",
    top_n: int | None = None,
) -> dict[str, Any]:
    """Run rolling validation for one ticker and return score-ready output.

    Args:
        ticker: symbol to validate.
        context: optional prepared context from screening. Passing it avoids
            loading OHLCV/market/sentiment data twice in the staged pipeline.
    """
    try:
        top_n_value = int(top_n) if top_n is not None else 1
    except Exception:
        top_n_value = 1
    use_top_n_validation = top_n_value > 1

    run_id = str(uuid4())
    ctx = context or prepare_ticker_context(ticker)
    df = ctx["df"]
    base_rulebook = ctx["base_rulebook"]
    ga_cfg = ga_config or DEFAULT_ROLLING_GA_CONFIG
    splits = ctx.get("splits") or make_year_splits(DEFAULT_ROLLING_YEARS, ctx.get("data_min"), ctx.get("data_max"))

    periods: list[dict[str, Any]] = []
    oos_periods: list[list[str]] = []
    ga_by_year: dict[str, Any] = {}
    top_n_periods: list[dict[str, Any]] = []

    base_kwargs = dict(
        position_limit_krw=DEFAULT_POSITION_LIMIT_KRW,
        market_history_df=ctx["market_history_df"],
        sector_name=ctx["sector_name"],
        ticker_sentiment=ctx["ticker_sentiment"],
        fitness_mode=fitness_mode,
    )

    for split in splits:
        year = int(split["year"])
        train_start = split["train_start"]
        train_end = split["train_end"]
        test_start = split["test_start"]
        test_end = split["test_end"]

        def evaluate_fn(rb):
            # Critical: GA selection uses TRAIN period only. OOS is evaluated
            # after GA finishes to avoid leakage.
            result = run_backtest(
                rb,
                df,
                start_date=train_start,
                end_date=train_end,
                **base_kwargs,
            )
            return result.fitness

        year_cfg = GAConfig(**_ga_config_to_plain_dict(ga_cfg))
        if year_cfg.random_seed is not None:
            year_cfg.random_seed = int(year_cfg.random_seed) + year
        ga_result = run_ga(base_rulebook=base_rulebook, evaluate_fn=evaluate_fn, ga_config=year_cfg)

        # First cutover test: OOS uses ga_result.best only. Ensemble top-N is
        # intentionally deferred until the data contract is proven.
        oos_result = run_backtest(
            ga_result.best,
            df,
            start_date=test_start,
            end_date=test_end,
            **base_kwargs,
        )
        period = backtest_result_to_oos_period(
            year=year,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            result=oos_result,
            ga_result=ga_result,
        )
        periods.append(period)
        oos_periods.append([test_start, test_end])
        ga_by_year[str(year)] = period["ga"]

        if use_top_n_validation:
            candidates = collect_top_rulebooks(ga_result, top_n_value)
            candidate_rows: list[dict[str, Any]] = []
            for rank_is, candidate in enumerate(candidates, 1):
                train_fitness = _float_or_zero(getattr(candidate, "fitness", 0.0))
                candidate_oos = run_backtest(
                    candidate,
                    df,
                    start_date=test_start,
                    end_date=test_end,
                    **base_kwargs,
                )
                candidate_rows.append(
                    _topn_candidate_to_oos_row(
                        year=year,
                        rank_is=rank_is,
                        train_start=train_start,
                        train_end=train_end,
                        test_start=test_start,
                        test_end=test_end,
                        rulebook=candidate,
                        result=candidate_oos,
                        train_fitness=train_fitness,
                    )
                )
            top_n_periods.append(
                {
                    "year": year,
                    "train_period": [train_start, train_end],
                    "test_period": [test_start, test_end],
                    "requested_top_n": top_n_value,
                    "candidate_count": len(candidate_rows),
                    "candidates": candidate_rows,
                }
            )

    stock_score = score_stock_from_rolling(periods, ctx["adv_usd_252d"])
    meta = getattr(ctx["meta"], "to_dict", lambda: {})()

    result = {
        "ticker": ticker,
        "stage": "rolling_validation",
        "run_id": run_id,
        "data_start": ctx.get("data_min"),
        "data_end": ctx.get("data_max"),
        "asset_meta": meta,
        "sector_name": ctx["sector_name"],
        "adv_usd_252d": ctx["adv_usd_252d"],
        "sentiment_days": ctx["sentiment_days"],
        "periods": periods,
        "stock_score": stock_score,
    }
    if use_top_n_validation:
        result["top_n_validation"] = {
            "top_n": top_n_value,
            "method": "collect_top_rulebooks_fitness_desc_hash_asc_then_oos",
            "periods": top_n_periods,
        }
    result["_meta"] = build_metadata(
        source="pipeline_v1.rolling_validation",
        ticker=ticker,
        fitness_mode=fitness_mode,
        data_start=ctx.get("data_min"),
        data_end=ctx.get("data_max"),
        oos_periods=oos_periods,
        ga_cfg=ga_cfg,
        ga_result={"by_year": ga_by_year, "splits": splits},
        validation={"stock_score": stock_score, "stage": "rolling_validation"},
        feature_lag=FEATURE_LAG_METADATA,
    )
    # Keep one run_id at top-level and inside _meta for traceability.
    result["_meta"]["run_id"] = run_id
    return result
