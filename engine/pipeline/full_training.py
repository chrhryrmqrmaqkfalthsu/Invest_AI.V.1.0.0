"""Full-training stage for the staged pipeline.

Full training learns final live candidates on the full 2020-2025 period, then
re-evaluates each final_population member on the same period to obtain the raw
metrics required by score_full_training_members(). GA fitness is never treated
as a member qualification metric.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from statistics import median
from typing import Any
from uuid import uuid4

from engine.core.feature_lag import FEATURE_LAG_METADATA
from engine.core.metadata import build_metadata, compute_member_hash, compute_rulebook_hash
from engine.learning.backtest import run_backtest
from engine.learning.genetic import GAConfig, GAResult, run_ga
from engine.pipeline.context import prepare_ticker_context
from engine.pipeline.scoring import score_full_training_members

# PROVISIONAL — rolling distribution Task AR에서 확정한 1차 통학습 진입 컷오프.
FULL_TRAINING_STOCK_SCORE_CUTOFF = 60.0

FULL_TRAINING_TRAIN_START = "2020-01-01"
FULL_TRAINING_TRAIN_END = "2025-12-31"
DEFAULT_MAX_MEMBERS = 40
DEFAULT_POSITION_LIMIT_KRW = 120_000.0

# PROVISIONAL — 운영 기본 후보. 전체 분포/비용 재확인 후 확정한다.
DEFAULT_FULL_TRAINING_GA_CONFIG = GAConfig(
    population=40,
    generations=35,
    elite_ratio=0.2,
    mutation_rate=0.15,
    mutation_strength=0.2,
    tournament_size=3,
    seed_pattern_ratio=0.33,
    early_stop_no_improve=8,
    random_seed=20260604,
)

# PROVISIONAL — 소규모 end-to-end 검증용.
SMOKE_FULL_TRAINING_GA_CONFIG = GAConfig(
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def ga_config_to_plain_dict(ga_config: GAConfig | None) -> dict[str, Any]:
    cfg = ga_config or DEFAULT_FULL_TRAINING_GA_CONFIG
    try:
        return dataclasses.asdict(cfg)
    except Exception:
        return {}


def ga_result_summary(ga_result: GAResult | None) -> dict[str, Any]:
    if ga_result is None:
        return {}
    best = getattr(ga_result, "best", None)
    history = []
    for item in getattr(ga_result, "fitness_history", []) or []:
        try:
            gen, best_fitness, avg_fitness = item
            history.append(
                {
                    "generation": int(gen),
                    "best_fitness": _safe_float(best_fitness),
                    "avg_fitness": _safe_float(avg_fitness),
                }
            )
        except Exception:
            history.append(_json_safe(item))
    return {
        "generations_run": getattr(ga_result, "generations_run", None),
        "best_fitness": _safe_float(getattr(best, "fitness", 0.0)),
        "population_size": len(getattr(ga_result, "final_population", []) or []),
        "fitness_history": history,
    }


def full_training_gate_from_rolling(
    rolling: dict[str, Any] | None,
    cutoff: float = FULL_TRAINING_STOCK_SCORE_CUTOFF,
) -> dict[str, Any]:
    """Decide whether rolling result qualifies for full training."""
    rolling = rolling or {}
    score_block = rolling.get("stock_score")
    if isinstance(score_block, dict):
        score = _safe_float(score_block.get("stock_score"), 0.0)
        excluded = bool(score_block.get("excluded", False))
        exclude_reason = score_block.get("exclude_reason", "")
    else:
        score = _safe_float(score_block if score_block is not None else rolling.get("stock_score"), 0.0)
        excluded = bool(rolling.get("excluded", False))
        exclude_reason = rolling.get("exclude_reason", "")

    should_run = (not excluded) and score >= float(cutoff)
    reason = "PASS" if should_run else "BELOW_CUTOFF"
    if rolling and score_block is None and rolling.get("stock_score") is None:
        reason = "NO_ROLLING_SCORE"
    return {
        "should_run": bool(should_run),
        "reason_code": reason,
        "stock_score": score,
        "cutoff": float(cutoff),
        "rolling_excluded": excluded,
        "rolling_exclude_reason": exclude_reason,
    }


def build_member_payload(rulebook: Any, backtest_result: Any, rank: int) -> dict[str, Any]:
    """Build the score_full_training_members input payload for one member."""
    rulebook_dict = rulebook.to_dict() if hasattr(rulebook, "to_dict") and callable(rulebook.to_dict) else _json_safe(rulebook)
    member_hash = compute_member_hash(rulebook)
    return {
        "rank": int(rank),
        "member_hash": member_hash,
        "rulebook_hash": compute_rulebook_hash(rulebook),
        "fitness": _safe_float(getattr(rulebook, "fitness", 0.0)),
        "rulebook": rulebook_dict,
        # Metrics below come from explicit full-period backtest re-evaluation.
        # Do not use GA fitness as a proxy for these values.
        "trade_count": _safe_int(getattr(backtest_result, "trade_count", 0)),
        "win_rate": _safe_float(getattr(backtest_result, "win_rate", 0.0)),
        "expectancy_pct": _safe_float(getattr(backtest_result, "expectancy_pct", 0.0)),
        "avg_return_pct": _safe_float(getattr(backtest_result, "avg_return_pct", 0.0)),
        "profit_factor": _safe_float(getattr(backtest_result, "profit_factor", 0.0)),
        "max_drawdown_pct": _safe_float(getattr(backtest_result, "max_drawdown_pct", 0.0)),
        "sharpe_like": _safe_float(getattr(backtest_result, "sharpe_like", 0.0)),
        "win_count": _safe_int(getattr(backtest_result, "win_count", 0)),
        "loss_count": _safe_int(getattr(backtest_result, "loss_count", 0)),
    }


def attach_member_metadata(
    members: list[dict[str, Any]],
    *,
    ticker: str,
    run_id: str,
    train_period: list[str],
    ga_cfg: GAConfig,
    ga_result: GAResult,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ga_summary = ga_result_summary(ga_result)
    for member in members:
        row = dict(member)
        member_hash = row.get("member_hash") or compute_member_hash(row.get("rulebook"))
        row["member_hash"] = member_hash
        row["_meta"] = build_metadata(
            source="pipeline_v1.full_training.member",
            ticker=ticker,
            fitness_mode="swing",
            train_period=train_period,
            ga_cfg=ga_cfg,
            ga_result=ga_summary,
            member=row.get("rulebook"),
            member_hash=member_hash,
            rulebook_hash=row.get("rulebook_hash"),
            validation={
                "stage": "full_training_member",
                "rank": row.get("rank"),
                "qualified": row.get("qualified"),
                "member_score": row.get("member_score"),
                "train_metrics": row.get("train_metrics"),
            },
            feature_lag=FEATURE_LAG_METADATA,
            run_id=run_id,
        )
        out.append(row)
    return out


def member_score_distribution(members: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [_safe_float(m.get("member_score"), 0.0) for m in members if m.get("member_score") is not None]
    qualified_scores = [_safe_float(m.get("member_score"), 0.0) for m in members if m.get("qualified")]
    if not scores:
        return {"count": 0, "min": None, "median": None, "max": None, "qualified_count": 0}
    return {
        "count": len(scores),
        "min": min(scores),
        "median": float(median(scores)),
        "max": max(scores),
        "qualified_count": len(qualified_scores),
        "qualified_min": min(qualified_scores) if qualified_scores else None,
        "qualified_median": float(median(qualified_scores)) if qualified_scores else None,
        "qualified_max": max(qualified_scores) if qualified_scores else None,
    }


def top_member_summaries(members: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    top = sorted(
        members,
        key=lambda m: (_safe_float(m.get("member_score"), 0.0), bool(m.get("qualified"))),
        reverse=True,
    )[: max(0, int(limit))]
    return [
        {
            "rank": m.get("rank"),
            "member_hash": m.get("member_hash"),
            "qualified": m.get("qualified"),
            "member_score": m.get("member_score"),
            "fitness": m.get("fitness"),
            "trade_count": (m.get("train_metrics", {}) or {}).get("trade_count", m.get("trade_count")),
            "win_rate": (m.get("train_metrics", {}) or {}).get("win_rate", m.get("win_rate")),
            "expectancy_pct": (m.get("train_metrics", {}) or {}).get("expectancy_pct", m.get("expectancy_pct")),
            "profit_factor": (m.get("train_metrics", {}) or {}).get("profit_factor", m.get("profit_factor")),
            "max_drawdown_pct": (m.get("train_metrics", {}) or {}).get("max_drawdown_pct", m.get("max_drawdown_pct")),
        }
        for m in top
    ]


def summarize_full_training_result(result: dict[str, Any], output_paths: dict[str, str] | None = None) -> dict[str, Any]:
    members = result.get("members", []) or []
    return {
        "executed": True,
        "status": "FULL_TRAINING_DONE",
        "ticker": result.get("ticker"),
        "train_period": result.get("train_period"),
        "member_count": result.get("member_count", len(members)),
        "qualified_count": result.get("qualified_count", sum(1 for m in members if m.get("qualified"))),
        "member_score_distribution": result.get("member_score_distribution", member_score_distribution(members)),
        "top_members": result.get("top_members", top_member_summaries(members)),
        "ga": result.get("ga", {}),
        "outputs": output_paths or result.get("outputs", {}),
    }


def run_full_training(
    ticker: str,
    context: dict[str, Any] | None = None,
    ga_config: GAConfig | None = None,
    fitness_mode: str = "swing",
    train_start: str = FULL_TRAINING_TRAIN_START,
    train_end: str = FULL_TRAINING_TRAIN_END,
    max_members: int = DEFAULT_MAX_MEMBERS,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run full-period GA and score every selected final_population member."""
    started = time.time()
    run_id = run_id or str(uuid4())
    ticker = str(ticker or "").upper().strip()
    ctx = context or prepare_ticker_context(ticker)
    df = ctx["df"]
    ga_cfg = ga_config or DEFAULT_FULL_TRAINING_GA_CONFIG
    base_rulebook = ctx["base_rulebook"]
    train_period = [train_start, train_end]

    base_kwargs = dict(
        position_limit_krw=DEFAULT_POSITION_LIMIT_KRW,
        market_history_df=ctx.get("market_history_df"),
        sector_name=ctx.get("sector_name", "tech"),
        ticker_sentiment=ctx.get("ticker_sentiment"),
        fitness_mode=fitness_mode,
    )

    def evaluate_fn(rb):
        result = run_backtest(
            rb,
            df,
            start_date=train_start,
            end_date=train_end,
            **base_kwargs,
        )
        return result.fitness

    ga_result = run_ga(base_rulebook=base_rulebook, evaluate_fn=evaluate_fn, ga_config=ga_cfg)
    sorted_population = sorted(
        list(getattr(ga_result, "final_population", []) or []),
        key=lambda rb: (getattr(rb, "fitness", None) if getattr(rb, "fitness", None) is not None else -1e18),
        reverse=True,
    )[: max(0, int(max_members))]

    member_payloads: list[dict[str, Any]] = []
    for rank, rb in enumerate(sorted_population, 1):
        # Critical: qualification metrics are obtained by explicit full-period
        # re-evaluation, not by reading GA fitness.
        re_eval = run_backtest(
            rb,
            df,
            start_date=train_start,
            end_date=train_end,
            **base_kwargs,
        )
        member_payloads.append(build_member_payload(rb, re_eval, rank))

    scored_members = score_full_training_members(member_payloads)
    scored_members = attach_member_metadata(
        scored_members,
        ticker=ticker,
        run_id=run_id,
        train_period=train_period,
        ga_cfg=ga_cfg,
        ga_result=ga_result,
    )
    qualified_count = sum(1 for m in scored_members if m.get("qualified"))
    ga_summary = ga_result_summary(ga_result)

    result = {
        "ticker": ticker,
        "stage": "full_training",
        "run_id": run_id,
        "train_period": train_period,
        "data_start": ctx.get("data_start") or ctx.get("data_min"),
        "data_end": ctx.get("data_end") or ctx.get("data_max"),
        "adv_usd_252d": ctx.get("adv_usd_252d"),
        "sentiment_days": ctx.get("sentiment_days"),
        "sector_name": ctx.get("sector_name"),
        "ga_config": ga_config_to_plain_dict(ga_cfg),
        "ga": ga_summary,
        "member_count": len(scored_members),
        "qualified_count": qualified_count,
        "member_score_distribution": member_score_distribution(scored_members),
        "top_members": top_member_summaries(scored_members),
        "members": scored_members,
        "elapsed_sec": time.time() - started,
    }
    result["_meta"] = build_metadata(
        source="pipeline_v1.full_training",
        ticker=ticker,
        fitness_mode=fitness_mode,
        data_start=result["data_start"],
        data_end=result["data_end"],
        train_period=train_period,
        ga_cfg=ga_cfg,
        ga_result=ga_summary,
        rulebook=getattr(ga_result, "best", None),
        validation={
            "stage": "full_training",
            "member_count": len(scored_members),
            "qualified_count": qualified_count,
            "member_score_distribution": result["member_score_distribution"],
        },
        feature_lag=FEATURE_LAG_METADATA,
        run_id=run_id,
    )
    return result


def save_full_training_artifacts(result: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Save members.jsonl and a compact full_training.json summary."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    members_path = out_dir / "members.jsonl"
    full_path = out_dir / "full_training.json"

    members = list(result.get("members", []) or [])
    with members_path.open("w", encoding="utf-8") as f:
        for member in members:
            f.write(json.dumps(_json_safe(member), ensure_ascii=False, sort_keys=True) + "\n")

    summary = dict(result)
    summary.pop("members", None)
    summary["members_path"] = str(members_path)
    summary["members_jsonl_count"] = len(members)
    full_path.write_text(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return {"full_training": str(full_path), "members": str(members_path)}


__all__ = [
    "DEFAULT_FULL_TRAINING_GA_CONFIG",
    "SMOKE_FULL_TRAINING_GA_CONFIG",
    "FULL_TRAINING_STOCK_SCORE_CUTOFF",
    "attach_member_metadata",
    "build_member_payload",
    "full_training_gate_from_rolling",
    "member_score_distribution",
    "run_full_training",
    "save_full_training_artifacts",
    "summarize_full_training_result",
    "top_member_summaries",
]
