#!/usr/bin/env python3
"""Stage 3 공격형 개체 생성 파이프라인 정식 runner.

이 파일은 Stage 3 공격형 개체 생성 파이프라인 정식 runner입니다.
--ticker로 종목을 지정해 호출하며, 4단계 흐름을 수행합니다.

1. 자격심사(qualify): train_1/train_2/train_3에서 strict_k3 방식으로 종목 자격을 판정합니다.
2. 진입학습(entry): train_3에서 코어 run_ga로 진입+청산 결합 개체를 만들고, 진입 시점 다양성으로 후보군을 남깁니다.
3. 청산재학습(exit): 각 진입 후보를 base로 고정하고 청산 14개 필드만 exit_gene 래퍼로 재학습합니다.
4. 프로파일(validate): 순수 OOS 3구간의 최소 적격선(expectancy≥1.0)을 확인한 뒤, 적격 개체를 보유·리스크·수익 라벨 카탈로그로 저장합니다.

산출물은 프로젝트 루트의 exp_<ticker>_stage3_<YYYYMMDD>_<NNNN>/ 폴더에 저장됩니다.
각 단계는 중간 산출물을 저장하며, --stage qualify|entry|exit|validate|all로 특정 단계부터 재개할 수 있습니다.
이 runner는 기존 Stage 2 파일과 engine.learning / engine.strategies 코어 파일을 수정하지 않습니다.

호출 예:
    venv/bin/python scripts/research/run_stage3_aggressive.py --ticker CRWD --stage qualify
    venv/bin/python scripts/research/run_stage3_aggressive.py --ticker CRWD --stage entry --out-dir exp_crwd_stage3_20260613_0001
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import math
import random
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.core.metadata import compute_rulebook_hash
from engine.learning.execution_mode_backtest import run_backtest_execution_mode
from engine.learning.genetic import GAConfig, collect_top_rulebooks, run_ga
from engine.learning.fitness_cache import (
    FitnessCache,
    aggregate_fitness_cache_summaries,
    fitness_cache_disabled_by_env,
    make_cache_key_context,
    make_cached_evaluate_fn,
    resolve_code_commit,
    summarize_fitness_cache,
)
from engine.pipeline.context import prepare_ticker_context
from engine.pipeline.exit_gene import (
    DEFAULT_EXIT_FITNESS_WEIGHTS,
    EXIT_CATEGORICAL,
    EXIT_FIELDS,
    EXIT_NUMERIC,
    ExitFitnessWeights,
    apply_exit,
    composite_exit_fitness,
    holding_days_summary,
)
from engine.pipeline.stage3_gate import (
    DEFAULT_STAGE3_PROFILE,
    DEFAULT_STAGE3_QUALIFY,
    STAGE3_FINAL_OOS_PERIODS,
    Stage3QualifyConfig,
    stage3_basic_eligibility,
    stage3_profile,
    stage3_qualify_fail_reasons,
)
from engine.pipeline.topn_survivor import _score_period_candidates
from engine.strategies.rulebook import CATEGORICAL_PARAMS, PARAM_RANGES, Rulebook


# Stage 3 기간 정의. Stage 2 신버전 기간과 맞춰 train_1/2/3 및 2025H2 검증을 사용한다.
TRAIN_SPLITS: tuple[dict[str, str], ...] = (
    {"label": "train_1", "start": "2022-07-01", "end": "2023-06-30"},
    {"label": "train_2", "start": "2023-07-01", "end": "2024-06-30"},
    {"label": "train_3", "start": "2024-07-01", "end": "2025-06-30"},
)
STRESS_PERIOD = {"label": "stress_pre_2022h1", "start": None, "end": "2022-06-30"}
BULL_PERIOD = {"label": "train_3", "start": "2024-07-01", "end": "2025-06-30"}
RECENT_1Y_PERIOD = {"label": "recent_1y", "start": "2025-07-01", "end": None}
PURE_OOS_VALIDATION_PERIODS: tuple[dict[str, str | None], ...] = (
    {"label": "train_1", "start": "2022-07-01", "end": "2023-06-30"},
    {"label": "train_2", "start": "2023-07-01", "end": "2024-06-30"},
    RECENT_1Y_PERIOD,
)
EXIT_CHECK_PERIOD = {"label": "stress_pre_2022h1", "start": None, "end": "2022-06-30", "role": "exit_check"}
VALIDATION_PERIOD = RECENT_1Y_PERIOD  # 이전 호출부 호환용 별칭. Stage 4 profile은 PURE_OOS_VALIDATION_PERIODS를 사용한다.
ENTRY_EXECUTION_MODE = "t_plus_1_open"
EXIT_EXECUTION_MODE = "conservative_core"
FOLD_EXIT_POLICY = "fold_end_mark_to_market"
LIVE_HARD_STOP_GUARD = True
ADD_BUY_RUNTIME_ENABLED = False

# 첫 Stage 3 실험용 임시 크기. 실제 계수와 population은 첫 결과 이후 튜닝한다.
QUALIFY_POPULATION = 100
QUALIFY_GENERATIONS = 40
ENTRY_POPULATION = 100
ENTRY_GENERATIONS = 50
EXIT_POPULATION = 60
EXIT_GENERATIONS = 25
TOP_N_QUALIFY = 100
TOP_N_ENTRY_POOL = 100
TOP_N_EXIT_PER_ENTRY = 3
POSITION_LIMIT_KRW = 120_000.0


@dataclasses.dataclass(frozen=True)
class Stage3EntrySelectionConfig:
    """Stage 3 단계2 진입 후보군 선별 기준.

    아래 값들은 첫 실험 전 임시값이다. 첫 CRWD/WELL/CW 실험 결과를 본 뒤
    entry_min_expectancy_pct, entry_overlap_threshold, max_entry_candidates를 튜닝한다.
    """

    entry_min_expectancy_pct: float = 2.0
    entry_overlap_threshold: float = 0.7
    max_entry_candidates: int = 20


DEFAULT_STAGE3_ENTRY_SELECTION = Stage3EntrySelectionConfig()


# ---------- 공통 유틸 ----------
def json_safe(value: Any) -> Any:
    """runner 산출물을 JSON으로 안전하게 저장하기 위한 변환 함수."""
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict())
        except Exception:
            return str(value)
    return str(value)


def write_json(path: Path, obj: Any) -> None:
    """dict/list 산출물을 사람이 읽을 수 있는 JSON 파일로 저장한다."""
    path.write_text(json.dumps(json_safe(obj), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """단계별 후보 목록을 JSONL로 저장한다."""
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(json_safe(row), ensure_ascii=False, sort_keys=True) + "\n")


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


def default_seed_base(ticker: str) -> int:
    """종목별 재현 가능한 기본 seed를 만든다."""
    return 2026061300 + sum((idx + 1) * ord(ch) for idx, ch in enumerate(ticker.upper()))


def auto_out_dir(ticker: str) -> Path:
    """exp_<ticker>_stage3_<YYYYMMDD>_<NNNN> 형식의 새 산출물 폴더를 배정한다."""
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"exp_{ticker.lower()}_stage3_{today}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"output directory exhausted: {prefix}NNNN")


def latest_out_dir(ticker: str) -> Path | None:
    """--out-dir 없이 재개할 때 가장 최근 Stage 3 산출물 폴더를 찾는다."""
    matches = sorted(PROJECT_ROOT.glob(f"exp_{ticker.lower()}_stage3_*"), key=lambda p: (p.stat().st_mtime, p.name))
    return matches[-1] if matches else None


def resolve_out_dir(ticker: str, stage: str, out_dir_arg: str | None) -> Path:
    """CLI 인자를 바탕으로 사용할 산출물 폴더를 결정한다."""
    if out_dir_arg:
        p = Path(out_dir_arg)
        return p if p.is_absolute() else PROJECT_ROOT / p
    if stage in {"qualify", "all"}:
        return auto_out_dir(ticker)
    found = latest_out_dir(ticker)
    if found is None:
        raise FileNotFoundError("--out-dir was not provided and no previous Stage 3 output directory was found")
    return found


def ensure_experiment_header(out_dir: Path, *, ticker: str, seed_base: int, stage: str) -> None:
    """Stage 3 산출물 폴더의 README/manifest를 생성 또는 갱신한다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    readme = out_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Stage 3 aggressive experiment output\n\n"
            "이 폴더는 Stage 3 공격형 파이프라인 실험 산출물입니다.\n"
            "단계는 qualify → entry → exit → validate 순서이며, 각 단계 산출물로 재개할 수 있습니다.\n"
            "validate 단계는 최소 적격선 통과 개체를 보유·리스크·수익 라벨 카탈로그로 저장합니다.\n"
            "재현 가능성을 위해 manifest, seed, 기간 정의, 설정값을 함께 저장합니다.\n"
            "정리 시 삭제 가능한 실험 산출물 폴더입니다.\n"
            "runner: scripts/research/run_stage3_aggressive.py\n",
            encoding="utf-8",
        )
    manifest_path = out_dir / "manifest.json"
    previous: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            previous = {}
    manifest = {
        **previous,
        "ticker": ticker,
        "runner": "scripts/research/run_stage3_aggressive.py",
        "description": "Stage 3 aggressive pipeline output; validate creates a profile catalog for eligible candidates instead of hard-killing by holding/MDD labels.",
        "seed_base": seed_base,
        "last_requested_stage": stage,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "periods": {
            "train_splits": TRAIN_SPLITS,
            "stress_period": STRESS_PERIOD,
            "bull_period": BULL_PERIOD,
            "pure_oos_validation_periods": PURE_OOS_VALIDATION_PERIODS,
            "exit_check_period": EXIT_CHECK_PERIOD,
        },
        "entry_selection_config": dataclasses.asdict(DEFAULT_STAGE3_ENTRY_SELECTION),
        "stage3_qualify_config": dataclasses.asdict(DEFAULT_STAGE3_QUALIFY),
        "stage3_profile_config": dataclasses.asdict(DEFAULT_STAGE3_PROFILE),
        "stage3_final_oos_periods": STAGE3_FINAL_OOS_PERIODS,
    }
    write_json(manifest_path, manifest)


def base_backtest_kwargs(ctx: dict[str, Any]) -> dict[str, Any]:
    """Stage 3 runner가 모든 backtest 호출에 공통으로 넘기는 인자."""
    return {
        "position_limit_krw": POSITION_LIMIT_KRW,
        "market_history_df": ctx.get("market_history_df"),
        "sector_name": ctx.get("sector_name", "tech"),
        "ticker_sentiment": ctx.get("ticker_sentiment"),
        "fitness_mode": "swing",
        "use_llm_events": False,
    }


def result_metrics(result: Any) -> dict[str, Any]:
    """BacktestResult에서 Stage 3 단계 판정에 필요한 metric만 추출한다."""
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
    }


def exit_reason_distribution(result: Any) -> dict[str, int]:
    """진단용 청산 사유 분포를 만든다."""
    return dict(sorted(Counter(str(t.get("exit_reason", "")) for t in (getattr(result, "trades", []) or []) if isinstance(t, dict)).items()))


def entry_dates_from_trades(trades: list[Any]) -> set[str]:
    """trade 목록에서 entry_date 집합을 추출한다."""
    dates: set[str] = set()
    for trade in trades or []:
        if not isinstance(trade, Mapping):
            continue
        raw = trade.get("entry_date") or trade.get("entry_time") or trade.get("entry_dt")
        if raw is None:
            continue
        dates.add(str(raw)[:10])
    return dates


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """두 개체의 진입일 겹침 정도를 Jaccard similarity로 계산한다."""
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return float(len(a & b) / len(union))


def run_backtest_period(rulebook: Rulebook, ctx: dict[str, Any], *, start: str | None, end: str | None) -> Any:
    """하나의 기간에 대해 honest Stage2와 같은 t+1 open + conservative_core backtest를 호출한다."""
    return run_backtest_execution_mode(
        rulebook,
        ctx["df"],
        start_date=start,
        end_date=end,
        **base_backtest_kwargs(ctx),
        entry_execution_mode=ENTRY_EXECUTION_MODE,
        exit_execution_mode=EXIT_EXECUTION_MODE,
        fold_exit_policy=FOLD_EXIT_POLICY,
        live_hard_stop_guard=LIVE_HARD_STOP_GUARD,
    )


def make_ga_config(*, population: int, generations: int, seed: int) -> GAConfig:
    """Stage 3용 GAConfig를 한 곳에서 만든다."""
    return GAConfig(
        population=population,
        generations=generations,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=generations,
        random_seed=seed,
    )


def _maybe_cached_evaluate_fn(
    raw_evaluate_fn: Any,
    *,
    enabled: bool,
    ticker: str,
    period_label: str,
    start_date: Any,
    end_date: Any,
    fitness_mode: str,
    code_commit: str,
) -> tuple[Any, FitnessCache | None]:
    """Stage 3 GA evaluate_fn에만 process-local fitness cache를 선택적으로 감싼다."""
    if not enabled:
        return raw_evaluate_fn, None
    cache = FitnessCache()
    return (
        make_cached_evaluate_fn(
            raw_evaluate_fn,
            cache=cache,
            key_ctx=make_cache_key_context(
                ticker=ticker,
                period_label=period_label,
                start_date=start_date,
                end_date=end_date,
                entry_execution_mode=ENTRY_EXECUTION_MODE,
                exit_execution_mode=EXIT_EXECUTION_MODE,
                fold_exit_policy=FOLD_EXIT_POLICY,
                fitness_mode=fitness_mode,
                code_commit=code_commit,
                add_buy_runtime_enabled=ADD_BUY_RUNTIME_ENABLED,
            ),
        ),
        cache,
    )


def _pass_one_year(metrics: Mapping[str, Any], config: Stage3QualifyConfig = DEFAULT_STAGE3_QUALIFY) -> bool:
    return (
        safe_int(metrics.get("trade_count")) >= config.min_trades
        and safe_float(metrics.get("member_score")) >= config.min_member_score
        and safe_float(metrics.get("expectancy_pct")) >= config.qualify_min_expectancy_pct
    )


# ---------- 단계 1: 자격심사 ----------
def run_qualify(ticker: str, out_dir: Path, *, seed_base: int, use_fitness_cache: bool = True, code_commit: str | None = None) -> dict[str, Any]:
    """Stage 3 단계1: 자격심사."""
    started = time.time()
    ctx = prepare_ticker_context(ticker)
    code_commit = code_commit or resolve_code_commit(PROJECT_ROOT)
    candidates_by_hash: dict[str, Rulebook] = {}
    ga_summaries: list[dict[str, Any]] = []

    for idx, split in enumerate(TRAIN_SPLITS, 1):
        split_seed = seed_base + idx
        print(json.dumps({"event": "stage3_qualify_ga_start", "ticker": ticker, "split": split["label"], "seed": split_seed}, ensure_ascii=False), flush=True)

        def evaluate_fn(rulebook: Rulebook, s: dict[str, str] = split) -> float:
            result = run_backtest_period(rulebook, ctx, start=s["start"], end=s["end"])
            return safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

        evaluate_fn, fitness_cache = _maybe_cached_evaluate_fn(
            evaluate_fn,
            enabled=use_fitness_cache,
            ticker=ticker,
            period_label=split["label"],
            start_date=split["start"],
            end_date=split["end"],
            fitness_mode="swing",
            code_commit=code_commit,
        )

        ga = run_ga(
            base_rulebook=ctx["base_rulebook"],
            evaluate_fn=evaluate_fn,
            ga_config=make_ga_config(population=QUALIFY_POPULATION, generations=QUALIFY_GENERATIONS, seed=split_seed),
        )
        top_rulebooks = collect_top_rulebooks(ga, TOP_N_QUALIFY)
        for rb in top_rulebooks:
            h = compute_rulebook_hash(rb)
            current = candidates_by_hash.get(h)
            if current is None or safe_float(getattr(rb, "fitness", 0.0)) > safe_float(getattr(current, "fitness", 0.0)):
                candidates_by_hash[h] = copy.deepcopy(rb)
        ga_summaries.append(
            {
                "split": split,
                "seed": split_seed,
                "generations_run": getattr(ga, "generations_run", None),
                "top_count": len(top_rulebooks),
                "best_fitness": safe_float(getattr(getattr(ga, "best", None), "fitness", 0.0)),
                "best_hash": compute_rulebook_hash(ga.best) if getattr(ga, "best", None) is not None else None,
                "fitness_cache": summarize_fitness_cache(fitness_cache),
            }
        )
        print(json.dumps({"event": "stage3_qualify_ga_done", "ticker": ticker, "split": split["label"], "top_count": len(top_rulebooks)}, ensure_ascii=False), flush=True)

    candidate_hashes = sorted(candidates_by_hash)
    metrics_by_hash: dict[str, dict[str, dict[str, Any]]] = {h: {} for h in candidate_hashes}
    year_pass_counts: dict[str, int] = {}
    member_score_stats: dict[str, dict[str, Any]] = {}

    for split in TRAIN_SPLITS:
        raw_rows: list[dict[str, Any]] = []
        print(json.dumps({"event": "stage3_qualify_eval_start", "ticker": ticker, "split": split["label"], "candidate_count": len(candidate_hashes)}, ensure_ascii=False), flush=True)
        for rank, h in enumerate(candidate_hashes, 1):
            rb = candidates_by_hash[h]
            result = run_backtest_period(rb, ctx, start=split["start"], end=split["end"])
            raw_rows.append(
                {
                    "ticker": ticker,
                    "label": split["label"],
                    "period_label": split["label"],
                    "rulebook_hash": h,
                    "rank_is": rank,
                    "oos": result_metrics(result),
                }
            )
        scored = _score_period_candidates(raw_rows)
        pass_count = 0
        scores = []
        for row in scored:
            h = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            metrics["member_score"] = safe_float(row.get("oos_member_score"))
            metrics["fitness"] = safe_float(row.get("fitness"))
            metrics_by_hash[h][split["label"]] = metrics
            scores.append(metrics["member_score"])
            if _pass_one_year(metrics):
                pass_count += 1
        year_pass_counts[split["label"]] = pass_count
        member_score_stats[split["label"]] = {
            "count": len(scores),
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "mean": sum(scores) / len(scores) if scores else None,
        }
        print(json.dumps({"event": "stage3_qualify_eval_done", "ticker": ticker, "split": split["label"], "pass_count": pass_count}, ensure_ascii=False), flush=True)

    fail_reason_counter: Counter[str] = Counter()
    all3_pass_count = 0
    all3_pass_hash_samples: list[str] = []
    for h in candidate_hashes:
        reasons = stage3_qualify_fail_reasons(metrics_by_hash.get(h, {}), DEFAULT_STAGE3_QUALIFY)
        if not reasons:
            all3_pass_count += 1
            if len(all3_pass_hash_samples) < 10:
                all3_pass_hash_samples.append(h)
        else:
            for reason in reasons:
                fail_reason_counter[str(reason.get("metric") or "unknown")] += 1

    result = {
        "ticker": ticker,
        "stage": "qualify",
        "qualified": all3_pass_count > 0,
        "config": dataclasses.asdict(DEFAULT_STAGE3_QUALIFY),
        "periods": list(TRAIN_SPLITS),
        "seed_base": seed_base,
        "data_start": ctx.get("data_start"),
        "data_end": ctx.get("data_end"),
        "ga_summaries": ga_summaries,
        "fitness_cache": aggregate_fitness_cache_summaries([row.get("fitness_cache", {}) for row in ga_summaries]),
        "unique_candidate_count": len(candidate_hashes),
        "year_pass_counts": year_pass_counts,
        "member_score_stats": member_score_stats,
        "all3_pass_count": all3_pass_count,
        "all3_pass_hash_samples": all3_pass_hash_samples,
        "fail_reason_metric_counts": dict(sorted(fail_reason_counter.items())),
        "elapsed_seconds": time.time() - started,
        "note": "qualification rulebooks are intentionally discarded; only summary counts are persisted",
    }
    write_json(out_dir / "qualify_result.json", result)
    return result


# ---------- 단계 2: 진입학습 ----------
def _select_diverse_entry_rows(
    rows: list[dict[str, Any]],
    config: Stage3EntrySelectionConfig = DEFAULT_STAGE3_ENTRY_SELECTION,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """절대 expectancy 기준과 진입일 Jaccard 유사도로 다양한 entry 후보만 남긴다."""
    eligible = [row for row in rows if safe_float(row.get("expectancy_pct")) >= config.entry_min_expectancy_pct]
    eligible.sort(key=lambda row: (safe_float(row.get("train_fitness")), safe_float(row.get("expectancy_pct"))), reverse=True)

    selected: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in eligible:
        row_dates = set(row.get("entry_dates") or [])
        duplicate_of: dict[str, Any] | None = None
        duplicate_overlap = 0.0
        for kept in selected:
            overlap = jaccard_similarity(row_dates, set(kept.get("entry_dates") or []))
            if overlap >= config.entry_overlap_threshold:
                duplicate_of = kept
                duplicate_overlap = overlap
                break
        if duplicate_of is not None:
            rejected.append(
                {
                    "rulebook_hash": row.get("rulebook_hash"),
                    "reason": "entry_date_overlap",
                    "overlap": duplicate_overlap,
                    "duplicate_of": duplicate_of.get("rulebook_hash"),
                }
            )
            continue
        selected.append(row)
        if len(selected) >= int(config.max_entry_candidates):
            break
    return selected, rejected


def run_entry_ga(ticker: str, out_dir: Path, *, seed_base: int, use_fitness_cache: bool = True, code_commit: str | None = None) -> dict[str, Any]:
    """Stage 3 단계2: 진입학습."""
    qualify_path = out_dir / "qualify_result.json"
    if not qualify_path.exists():
        raise FileNotFoundError(f"missing prerequisite: {qualify_path}")
    qualify = json.loads(qualify_path.read_text(encoding="utf-8"))
    if not bool(qualify.get("qualified")):
        raise RuntimeError(f"ticker {ticker} did not pass Stage 3 qualification")

    started = time.time()
    ctx = prepare_ticker_context(ticker)
    code_commit = code_commit or resolve_code_commit(PROJECT_ROOT)
    train_3 = next(split for split in TRAIN_SPLITS if split["label"] == "train_3")
    seed = seed_base + 100

    def evaluate_fn(rulebook: Rulebook) -> float:
        result = run_backtest_period(rulebook, ctx, start=train_3["start"], end=train_3["end"])
        return safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

    evaluate_fn, fitness_cache = _maybe_cached_evaluate_fn(
        evaluate_fn,
        enabled=use_fitness_cache,
        ticker=ticker,
        period_label=train_3["label"],
        start_date=train_3["start"],
        end_date=train_3["end"],
        fitness_mode="swing",
        code_commit=code_commit,
    )

    ga = run_ga(
        base_rulebook=ctx["base_rulebook"],
        evaluate_fn=evaluate_fn,
        ga_config=make_ga_config(population=ENTRY_POPULATION, generations=ENTRY_GENERATIONS, seed=seed),
    )
    top_rulebooks = collect_top_rulebooks(ga, TOP_N_ENTRY_POOL)

    evaluated_rows: list[dict[str, Any]] = []
    for pool_rank, rb in enumerate(top_rulebooks, 1):
        result = run_backtest_period(rb, ctx, start=train_3["start"], end=train_3["end"])
        metrics = result_metrics(result)
        entry_dates = sorted(entry_dates_from_trades(list(getattr(result, "trades", []) or [])))
        evaluated_rows.append(
            {
                "ticker": ticker,
                "pool_rank": pool_rank,
                "rulebook_hash": compute_rulebook_hash(rb),
                "train_period": train_3,
                "train_fitness": safe_float(metrics.get("fitness")),
                "expectancy_pct": safe_float(metrics.get("expectancy_pct")),
                "trade_count": safe_int(metrics.get("trade_count")),
                "win_rate": safe_float(metrics.get("win_rate")),
                "profit_factor": safe_float(metrics.get("profit_factor")),
                "max_drawdown_pct": safe_float(metrics.get("max_drawdown_pct")),
                "entry_date_count": len(entry_dates),
                "entry_dates": entry_dates,
                "rulebook": rb.to_dict(),
            }
        )

    selected, rejected = _select_diverse_entry_rows(evaluated_rows, DEFAULT_STAGE3_ENTRY_SELECTION)
    output_rows: list[dict[str, Any]] = []
    for rank, row in enumerate(selected, 1):
        out = dict(row)
        out["rank"] = rank
        output_rows.append(out)

    append_jsonl(out_dir / "entry_rulebooks.jsonl", output_rows)
    write_json(out_dir / "entry_rejected_overlap.json", rejected)
    summary = {
        "ticker": ticker,
        "stage": "entry",
        "seed": seed,
        "train_period": train_3,
        "selection_config": dataclasses.asdict(DEFAULT_STAGE3_ENTRY_SELECTION),
        "pool_count": len(evaluated_rows),
        "absolute_pass_count": sum(1 for row in evaluated_rows if safe_float(row.get("expectancy_pct")) >= DEFAULT_STAGE3_ENTRY_SELECTION.entry_min_expectancy_pct),
        "selected_count": len(output_rows),
        "overlap_rejected_count": len(rejected),
        "fitness_cache": summarize_fitness_cache(fitness_cache),
        "best_fitness": output_rows[0]["train_fitness"] if output_rows else None,
        "best_hash": output_rows[0]["rulebook_hash"] if output_rows else None,
        "elapsed_seconds": time.time() - started,
    }
    write_json(out_dir / "entry_result.json", summary)
    return summary


# ---------- 단계 3: 청산재학습 ----------
def _normalize_exit_gene(gene: Mapping[str, Any]) -> dict[str, Any]:
    """청산 gene 값을 Rulebook 범위 안으로 정규화한다."""
    out: dict[str, Any] = {}
    for field in EXIT_CATEGORICAL:
        choices = list(CATEGORICAL_PARAMS[field])
        value = gene.get(field, choices[0])
        out[field] = value if value in choices else choices[0]
    for field in EXIT_NUMERIC:
        lo, hi = PARAM_RANGES[field]
        value = safe_float(gene.get(field), float(lo))
        value = max(float(lo), min(float(hi), value))
        out[field] = int(round(value)) if field == "max_holding_days" else float(value)
    return out


def _exit_gene_from_rulebook_dict(rulebook: Mapping[str, Any]) -> dict[str, Any]:
    """entry rulebook에 들어 있던 기존 청산값을 exit gene seed로 변환한다."""
    return _normalize_exit_gene({field: rulebook.get(field) for field in EXIT_FIELDS})


def _random_exit_gene(rng: random.Random) -> dict[str, Any]:
    """청산 14개 필드만 가진 랜덤 gene을 만든다."""
    gene: dict[str, Any] = {}
    for field in EXIT_CATEGORICAL:
        gene[field] = rng.choice(list(CATEGORICAL_PARAMS[field]))
    for field in EXIT_NUMERIC:
        lo, hi = PARAM_RANGES[field]
        gene[field] = rng.randint(int(lo), int(hi)) if field == "max_holding_days" else rng.uniform(float(lo), float(hi))
    return _normalize_exit_gene(gene)


def _mutate_exit_gene(gene: Mapping[str, Any], rng: random.Random, *, mutation_rate: float = 0.25, strength: float = 0.18) -> dict[str, Any]:
    """청산 14개 필드만 변이한다."""
    out = _normalize_exit_gene(gene)
    for field in EXIT_CATEGORICAL:
        if rng.random() < mutation_rate:
            out[field] = rng.choice(list(CATEGORICAL_PARAMS[field]))
    for field in EXIT_NUMERIC:
        if rng.random() < mutation_rate:
            lo, hi = PARAM_RANGES[field]
            if rng.random() < 0.08:
                out[field] = rng.randint(int(lo), int(hi)) if field == "max_holding_days" else rng.uniform(float(lo), float(hi))
            else:
                sigma = (float(hi) - float(lo)) * strength
                out[field] = safe_float(out[field]) + rng.gauss(0.0, sigma)
    return _normalize_exit_gene(out)


def _crossover_exit_gene(a: Mapping[str, Any], b: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    """청산 14개 필드만 균등 교차한다."""
    return _normalize_exit_gene({field: (a if rng.random() < 0.5 else b).get(field) for field in EXIT_FIELDS})


def _exit_gene_key(gene: Mapping[str, Any]) -> str:
    norm = _normalize_exit_gene(gene)
    return "|".join(f"{field}={norm[field]:.8f}" if isinstance(norm[field], float) else f"{field}={norm[field]}" for field in EXIT_FIELDS)


def _unique_exit_population(candidates: list[dict[str, Any]], rng: random.Random, size: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for gene in candidates:
        norm = _normalize_exit_gene(gene)
        key = _exit_gene_key(norm)
        if key not in seen:
            seen.add(key)
            out.append(norm)
    while len(out) < size:
        gene = _random_exit_gene(rng)
        key = _exit_gene_key(gene)
        if key not in seen:
            seen.add(key)
            out.append(gene)
    return out[:size]


def _tournament_exit(population: list[dict[str, Any]], fitness: dict[str, float], rng: random.Random, k: int = 3) -> dict[str, Any]:
    contenders = rng.sample(population, min(k, len(population)))
    return max(contenders, key=lambda gene: fitness.get(_exit_gene_key(gene), float("-inf")))


def _evaluate_exit_gene(
    *,
    base_rulebook_dict: dict[str, Any],
    gene: dict[str, Any],
    ctx: dict[str, Any],
    weights: ExitFitnessWeights = DEFAULT_EXIT_FITNESS_WEIGHTS,
) -> dict[str, Any]:
    """하나의 entry rulebook에 청산 gene을 붙여 stress+bull 결합 fitness를 계산한다."""
    rb_dict = apply_exit(base_rulebook_dict, gene)
    rb = Rulebook.from_dict(rb_dict)
    stress_result = run_backtest_period(rb, ctx, start=STRESS_PERIOD["start"], end=STRESS_PERIOD["end"])
    bull_result = run_backtest_period(rb, ctx, start=BULL_PERIOD["start"], end=BULL_PERIOD["end"])
    stress_trades = [t for t in list(getattr(stress_result, "trades", []) or []) if isinstance(t, dict)]
    bull_trades = [t for t in list(getattr(bull_result, "trades", []) or []) if isinstance(t, dict)]
    trades = stress_trades + bull_trades
    holding = holding_days_summary(trades)
    stress_metrics = result_metrics(stress_result)
    bull_metrics = result_metrics(bull_result)
    fitness = composite_exit_fitness(
        stress_metrics,
        bull_metrics,
        holding,
        weights,
        stress_trades=stress_trades,
        bull_trades=bull_trades,
    )
    return {
        "key": _exit_gene_key(gene),
        "exit_gene": _normalize_exit_gene(gene),
        "rulebook_hash": compute_rulebook_hash(rb),
        "rulebook": rb.to_dict(),
        "composite_fitness": fitness,
        "stress_metrics": {**stress_metrics, "exit_reason_distribution": exit_reason_distribution(stress_result)},
        "bull_metrics": {**bull_metrics, "exit_reason_distribution": exit_reason_distribution(bull_result)},
        "holding_summary": holding,
    }


def _run_exit_ga_for_entry(
    *,
    entry_row: dict[str, Any],
    ctx: dict[str, Any],
    seed: int,
    weights: ExitFitnessWeights = DEFAULT_EXIT_FITNESS_WEIGHTS,
) -> list[dict[str, Any]]:
    """entry rulebook 하나를 base로 삼아 청산 14필드 전용 GA를 실행한다."""
    rng = random.Random(seed)
    base = dict(entry_row["rulebook"])
    seed_gene = _exit_gene_from_rulebook_dict(base)
    population = _unique_exit_population(
        [seed_gene] + [_mutate_exit_gene(seed_gene, rng) for _ in range(10)] + [_random_exit_gene(rng) for _ in range(EXIT_POPULATION)],
        rng,
        EXIT_POPULATION,
    )
    cache: dict[str, dict[str, Any]] = {}
    for generation in range(EXIT_GENERATIONS + 1):
        for gene in population:
            key = _exit_gene_key(gene)
            if key not in cache:
                cache[key] = _evaluate_exit_gene(base_rulebook_dict=base, gene=gene, ctx=ctx, weights=weights)
        ranked = sorted((cache[_exit_gene_key(gene)] for gene in population), key=lambda r: r["composite_fitness"], reverse=True)
        print(
            json.dumps(
                {
                    "event": "stage3_exit_ga_gen",
                    "entry_hash": entry_row.get("rulebook_hash"),
                    "generation": generation,
                    "best": round(safe_float(ranked[0].get("composite_fitness")), 6) if ranked else None,
                    "cache_size": len(cache),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if generation >= EXIT_GENERATIONS:
            break
        fitness = {row["key"]: safe_float(row.get("composite_fitness"), float("-inf")) for row in cache.values()}
        elites = [row["exit_gene"] for row in ranked[: max(1, EXIT_POPULATION // 5)]]
        next_population = list(elites)
        while len(next_population) < EXIT_POPULATION:
            if rng.random() < 0.12:
                child = _random_exit_gene(rng)
            else:
                a = _tournament_exit(population, fitness, rng)
                b = _tournament_exit(population, fitness, rng)
                child = _mutate_exit_gene(_crossover_exit_gene(a, b, rng), rng)
            next_population.append(child)
        population = _unique_exit_population(next_population, rng, EXIT_POPULATION)
    final_ranked = sorted(cache.values(), key=lambda r: r["composite_fitness"], reverse=True)
    out: list[dict[str, Any]] = []
    for rank, row in enumerate(final_ranked[:TOP_N_EXIT_PER_ENTRY], 1):
        out.append(
            {
                "ticker": entry_row.get("ticker"),
                "entry_rank": entry_row.get("rank"),
                "entry_rulebook_hash": entry_row.get("rulebook_hash"),
                "exit_rank": rank,
                **row,
            }
        )
    return out


def run_exit_ga(
    ticker: str,
    out_dir: Path,
    *,
    seed_base: int,
    weights: ExitFitnessWeights = DEFAULT_EXIT_FITNESS_WEIGHTS,
) -> dict[str, Any]:
    """Stage 3 단계3: 청산재학습."""
    entry_path = out_dir / "entry_rulebooks.jsonl"
    if not entry_path.exists():
        raise FileNotFoundError(f"missing prerequisite: {entry_path}")
    entries = [json.loads(line) for line in entry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    started = time.time()
    ctx = prepare_ticker_context(ticker)
    final_rows: list[dict[str, Any]] = []
    for idx, entry_row in enumerate(entries, 1):
        seed = seed_base + 1000 + idx
        final_rows.extend(_run_exit_ga_for_entry(entry_row=entry_row, ctx=ctx, seed=seed, weights=weights))
    final_rows.sort(key=lambda row: safe_float(row.get("composite_fitness"), float("-inf")), reverse=True)
    append_jsonl(out_dir / "final_rulebooks.jsonl", final_rows)
    summary = {
        "ticker": ticker,
        "stage": "exit",
        "entry_count": len(entries),
        "final_rulebook_count": len(final_rows),
        "weights": dataclasses.asdict(weights),
        "best_composite_fitness": safe_float(final_rows[0].get("composite_fitness")) if final_rows else None,
        "best_hash": final_rows[0].get("rulebook_hash") if final_rows else None,
        "elapsed_seconds": time.time() - started,
    }
    write_json(out_dir / "exit_result.json", summary)
    return summary


# ---------- 단계 4: 프로파일 카탈로그 ----------
def _validate_one_period(
    *,
    rulebook: Rulebook,
    ctx: dict[str, Any],
    period: Mapping[str, Any],
    end_override: str | None = None,
) -> dict[str, Any]:
    """Stage 4의 한 구간 백테스트 결과를 metrics + holding summary로 정리한다."""
    end = end_override if end_override is not None else period.get("end")
    result = run_backtest_period(rulebook, ctx, start=period.get("start"), end=end)
    trades = [t for t in list(getattr(result, "trades", []) or []) if isinstance(t, dict)]
    holding = holding_days_summary(trades)
    metrics = result_metrics(result)
    metrics["median_holding_days"] = holding.get("median")
    return {
        "label": period.get("label"),
        "period": {"start": period.get("start"), "end": end},
        "metrics": metrics,
        "holding_summary": holding,
        "exit_reason_distribution": exit_reason_distribution(result),
        "trades": trades,
    }


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (kv[0], kv[1])))


EXIT_TRADE_OUTPUT_FIELDS: tuple[str, ...] = (
    "final_rulebook_hash",
    "entry_rulebook_hash",
    "exit_rank",
    "period_label",
    "entry_date",
    "exit_date",
    "holding_days",
    "pnl_pct",
    "exit_reason",
    "max_profit_during_hold",
    "max_loss_during_hold",
    "entry_price",
    "exit_price",
    "stop_price_at_entry",
    "target_price_at_entry",
    "trailing_stop_at_entry",
    "breakeven_enabled",
    "breakeven_trigger_profit_pct",
    "sell_omen_enabled",
    "stop_loss_atr",
    "stop_loss_atr_bear",
    "max_holding_days",
)


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

_EXIT_TRADE_CONTEXT_FIELDS = {"final_rulebook_hash", "entry_rulebook_hash", "exit_rank", "period_label"}


def _lookup_exit_trade_value(trade: Mapping[str, Any], rulebook_dict: Mapping[str, Any], field: str) -> tuple[Any, bool]:
    """거래 저장용 필드를 기존 trade dict 또는 rulebook dump에서만 조회한다."""
    if field in trade:
        return trade.get(field), True
    nested_rulebook = trade.get("rulebook_full")
    if isinstance(nested_rulebook, Mapping) and field in nested_rulebook:
        return nested_rulebook.get(field), True
    if field in rulebook_dict:
        return rulebook_dict.get(field), True
    return None, False


def _compact_exit_trade(
    *,
    trade: Mapping[str, Any],
    rulebook_dict: Mapping[str, Any],
    final_rulebook_hash: str,
    entry_rulebook_hash: Any,
    exit_rank: Any,
    period_label: str,
) -> tuple[dict[str, Any], list[str]]:
    """Stage 3 validate 거래를 긴 손실 감사에 필요한 얇은 JSONL row로 축약한다."""
    out: dict[str, Any] = {
        "final_rulebook_hash": final_rulebook_hash,
        "entry_rulebook_hash": entry_rulebook_hash,
        "exit_rank": exit_rank,
        "period_label": period_label,
    }
    missing: list[str] = []
    for field in EXIT_TRADE_OUTPUT_FIELDS:
        if field in _EXIT_TRADE_CONTEXT_FIELDS:
            continue
        value, found = _lookup_exit_trade_value(trade, rulebook_dict, field)
        if found:
            out[field] = value
        else:
            missing.append(field)
    return out, missing


def _exit_trade_rows_for_period(
    *,
    trades: list[dict[str, Any]] | None,
    rulebook_dict: Mapping[str, Any],
    final_rulebook_hash: str,
    entry_rulebook_hash: Any,
    exit_rank: Any,
    period_label: str,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """한 validate 기간의 trade 배열을 exit_trades.jsonl row 목록으로 변환한다."""
    rows: list[dict[str, Any]] = []
    missing_counter: Counter[str] = Counter()
    for trade in trades or []:
        if not isinstance(trade, Mapping):
            continue
        compacted, missing = _compact_exit_trade(
            trade=trade,
            rulebook_dict=rulebook_dict,
            final_rulebook_hash=final_rulebook_hash,
            entry_rulebook_hash=entry_rulebook_hash,
            exit_rank=exit_rank,
            period_label=period_label,
        )
        rows.append(compacted)
        missing_counter.update(missing)
    return rows, missing_counter


def _lookup_rl_replay_trade_value(
    *,
    trade: Mapping[str, Any],
    rulebook_dict: Mapping[str, Any],
    context: Mapping[str, Any],
    field: str,
) -> tuple[Any, bool]:
    """RL replay row 필드를 runner context, trade dict, rulebook에서 조회한다."""
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
    """Stage 3 validate 거래를 RL replay용 풍부한 JSONL row로 변환한다."""
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


def _rl_replay_rows_for_period(
    *,
    trades: list[dict[str, Any]] | None,
    rulebook_dict: Mapping[str, Any],
    ticker: str,
    out_dir: Path,
    final_rulebook_hash: str,
    entry_rulebook_hash: Any,
    exit_rank: Any,
    period_label: str,
    period_role: str,
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    """한 validate 기간의 trade 배열을 rl_replay_trades.jsonl row 목록으로 변환한다."""
    rows: list[dict[str, Any]] = []
    missing_counter: Counter[str] = Counter()
    critical_null_counter: Counter[str] = Counter()
    for trade_index, trade in enumerate(trades or [], 1):
        if not isinstance(trade, Mapping):
            continue
        context = {
            "rl_replay_schema_version": RL_REPLAY_SCHEMA_VERSION,
            "ticker": ticker,
            "source_stage": "stage3",
            "source_run_dir": str(out_dir),
            "rulebook_hash": final_rulebook_hash,
            "final_rulebook_hash": final_rulebook_hash,
            "entry_rulebook_hash": entry_rulebook_hash,
            "exit_rank": exit_rank,
            "period_label": period_label,
            "period_role": period_role,
            "trade_index_in_period": trade_index,
        }
        row, missing, critical_null = _rl_replay_trade(
            trade=trade,
            rulebook_dict=rulebook_dict,
            context=context,
        )
        rows.append(row)
        missing_counter.update(missing)
        critical_null_counter.update(critical_null)
    return rows, missing_counter, critical_null_counter


def run_validate(ticker: str, out_dir: Path, *, seed_base: int) -> dict[str, Any]:
    """Stage 3 단계4: 최소 적격선 + 프로파일 카탈로그.

    final_rulebooks.jsonl의 최종 개체를 순수 OOS 3구간(train_1, train_2, recent_1y)에서
    모두 재검증한다. 단계4는 더 이상 보유일/MDD로 개체를 죽이지 않는다. 최소 적격선은
    모든 순수 OOS 구간 expectancy_pct >= 1.0이며, 이 선을 통과한 개체만
    stage3_profile_catalog.jsonl에 보유·리스크·수익 라벨과 함께 저장한다.

    투자 성격 선택은 사후에 카탈로그의 holding_class/risk_class/return_class/composite_tag로
    필터링한다. validated_survivors.jsonl은 기존 hard gate 의미와 충돌하므로 더 이상 생성하지
    않으며, 기존 파일이 있으면 stale 혼동 방지를 위해 제거한다.
    """
    del seed_base  # validate 단계는 난수 없이 재현된다.
    final_path = out_dir / "final_rulebooks.jsonl"
    if not final_path.exists():
        raise FileNotFoundError(f"missing prerequisite: {final_path}")
    final_rows = [json.loads(line) for line in final_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    started = time.time()
    ctx = prepare_ticker_context(ticker)
    data_end = str(ctx.get("data_end") or ctx.get("data_max") or "") or None
    validation_rows: list[dict[str, Any]] = []
    catalog_rows: list[dict[str, Any]] = []
    ineligible_rows: list[dict[str, Any]] = []
    holding_counter: Counter[str] = Counter()
    risk_counter: Counter[str] = Counter()
    return_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    ineligible_reason_counter: Counter[str] = Counter()
    exit_trade_rows: list[dict[str, Any]] = []
    exit_trade_missing_counter: Counter[str] = Counter()
    rl_replay_trade_rows: list[dict[str, Any]] = []
    rl_replay_missing_counter: Counter[str] = Counter()
    rl_replay_critical_null_counter: Counter[str] = Counter()

    for rank, row in enumerate(final_rows, 1):
        rb = Rulebook.from_dict(dict(row["rulebook"]))
        rulebook_hash = compute_rulebook_hash(rb)
        rulebook_dict = rb.to_dict()
        per_period_metrics: dict[str, dict[str, Any]] = {}
        period_results: dict[str, dict[str, Any]] = {}
        all_oos_trades: list[dict[str, Any]] = []

        for period in PURE_OOS_VALIDATION_PERIODS:
            label = str(period["label"])
            end_override = data_end if label == "recent_1y" else None
            one = _validate_one_period(rulebook=rb, ctx=ctx, period=period, end_override=end_override)
            per_period_metrics[label] = dict(one["metrics"])
            trades = one.pop("trades", [])
            trade_rows, missing = _exit_trade_rows_for_period(
                trades=trades,
                rulebook_dict=rulebook_dict,
                final_rulebook_hash=rulebook_hash,
                entry_rulebook_hash=row.get("entry_rulebook_hash"),
                exit_rank=row.get("exit_rank"),
                period_label=label,
            )
            exit_trade_rows.extend(trade_rows)
            exit_trade_missing_counter.update(missing)
            replay_rows, replay_missing, replay_critical_null = _rl_replay_rows_for_period(
                trades=trades,
                rulebook_dict=rulebook_dict,
                ticker=ticker,
                out_dir=out_dir,
                final_rulebook_hash=rulebook_hash,
                entry_rulebook_hash=row.get("entry_rulebook_hash"),
                exit_rank=row.get("exit_rank"),
                period_label=label,
                period_role="oos",
            )
            rl_replay_trade_rows.extend(replay_rows)
            rl_replay_missing_counter.update(replay_missing)
            rl_replay_critical_null_counter.update(replay_critical_null)
            all_oos_trades.extend(trades)
            period_results[label] = {**one, "gate_included": True, "role": "pure_oos"}

        stress_check = _validate_one_period(rulebook=rb, ctx=ctx, period=EXIT_CHECK_PERIOD)
        stress_trades = stress_check.pop("trades", [])
        stress_label = str(EXIT_CHECK_PERIOD["label"])
        stress_trade_rows, stress_missing = _exit_trade_rows_for_period(
            trades=stress_trades,
            rulebook_dict=rulebook_dict,
            final_rulebook_hash=rulebook_hash,
            entry_rulebook_hash=row.get("entry_rulebook_hash"),
            exit_rank=row.get("exit_rank"),
            period_label=stress_label,
        )
        exit_trade_rows.extend(stress_trade_rows)
        exit_trade_missing_counter.update(stress_missing)
        stress_replay_rows, stress_replay_missing, stress_replay_critical_null = _rl_replay_rows_for_period(
            trades=stress_trades,
            rulebook_dict=rulebook_dict,
            ticker=ticker,
            out_dir=out_dir,
            final_rulebook_hash=rulebook_hash,
            entry_rulebook_hash=row.get("entry_rulebook_hash"),
            exit_rank=row.get("exit_rank"),
            period_label=stress_label,
            period_role="stress",
        )
        rl_replay_trade_rows.extend(stress_replay_rows)
        rl_replay_missing_counter.update(stress_replay_missing)
        rl_replay_critical_null_counter.update(stress_replay_critical_null)
        period_results[stress_label] = {
            **stress_check,
            "gate_included": False,
            "role": "exit_check",
            "note": "stress is used in Stage 3 exit learning, so it is excluded from eligibility and recorded only as down-market exit behavior reference",
        }

        all_oos_holding = holding_days_summary(all_oos_trades)
        eligibility_fail_reasons = stage3_basic_eligibility(per_period_metrics, DEFAULT_STAGE3_PROFILE)
        is_eligible = not eligibility_fail_reasons
        profile: dict[str, Any] | None = None
        if is_eligible:
            profile = stage3_profile(per_period_metrics, DEFAULT_STAGE3_PROFILE)
            holding_counter[str(profile["holding_class"])] += 1
            risk_counter[str(profile["risk_class"])] += 1
            return_counter[str(profile["return_class"])] += 1
            tag_counter[str(profile["composite_tag"])] += 1
        else:
            for reason in eligibility_fail_reasons:
                key = f"{reason.get('period')}|{reason.get('metric')}"
                ineligible_reason_counter[key] += 1

        common = {
            "ticker": ticker,
            "rank": rank,
            "rulebook_hash": rulebook_hash,
            "entry_rank": row.get("entry_rank"),
            "entry_rulebook_hash": row.get("entry_rulebook_hash"),
            "exit_rank": row.get("exit_rank"),
            "source_composite_fitness": row.get("composite_fitness"),
            "pure_oos_validation_periods": [
                {"label": p["label"], "start": p.get("start"), "end": data_end if p["label"] == "recent_1y" else p.get("end")}
                for p in PURE_OOS_VALIDATION_PERIODS
            ],
            "exit_check_period": EXIT_CHECK_PERIOD,
            "per_period_metrics": per_period_metrics,
            "period_results": period_results,
            "stress_reference_metrics": period_results[stress_label]["metrics"],
            "all_oos_holding_summary": all_oos_holding,
            "eligible_stage3_basic": is_eligible,
            "eligibility_fail_reasons": eligibility_fail_reasons,
            "rulebook": rulebook_dict,
        }
        validation_rows.append({**common, "profile": profile})
        if is_eligible and profile is not None:
            catalog_rows.append(
                {
                    **common,
                    "holding_class": profile["holding_class"],
                    "risk_class": profile["risk_class"],
                    "return_class": profile["return_class"],
                    "composite_tag": profile["composite_tag"],
                    "profile_period_metrics": profile["period_metrics"],
                    "profile_config": profile["config"],
                    "note": "eligible catalog row; investment style is selected later by filtering labels, not by Stage 4 hard gate",
                }
            )
        else:
            ineligible_rows.append(common)

    append_jsonl(out_dir / "exit_trades.jsonl", exit_trade_rows)
    print(
        json.dumps(
            {
                "event": "stage3_validate_exit_trades_written",
                "ticker": ticker,
                "path": str(out_dir / "exit_trades.jsonl"),
                "trade_count": len(exit_trade_rows),
                "missing_field_counts": _counter_dict(exit_trade_missing_counter),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    append_jsonl(out_dir / "rl_replay_trades.jsonl", rl_replay_trade_rows)
    print(
        json.dumps(
            {
                "event": "stage3_validate_rl_replay_trades_written",
                "ticker": ticker,
                "path": str(out_dir / "rl_replay_trades.jsonl"),
                "trade_count": len(rl_replay_trade_rows),
                "missing_field_counts": _counter_dict(rl_replay_missing_counter),
                "critical_null_counts": _counter_dict(rl_replay_critical_null_counter),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    append_jsonl(out_dir / "validation_results.jsonl", validation_rows)
    append_jsonl(out_dir / "stage3_profile_catalog.jsonl", catalog_rows)
    append_jsonl(out_dir / "stage3_ineligible.jsonl", ineligible_rows)
    legacy_survivors = out_dir / "validated_survivors.jsonl"
    if legacy_survivors.exists():
        legacy_survivors.unlink()

    summary = {
        "ticker": ticker,
        "stage": "validate",
        "mode": "basic_eligibility_plus_profile_catalog",
        "pure_oos_validation_periods": [
            {"label": p["label"], "start": p.get("start"), "end": data_end if p["label"] == "recent_1y" else p.get("end")}
            for p in PURE_OOS_VALIDATION_PERIODS
        ],
        "exit_check_period": EXIT_CHECK_PERIOD,
        "candidate_count": len(validation_rows),
        "eligible_count": len(catalog_rows),
        "ineligible_count": len(ineligible_rows),
        "profile_config": dataclasses.asdict(DEFAULT_STAGE3_PROFILE),
        "label_distribution": {
            "holding_class": _counter_dict(holding_counter),
            "risk_class": _counter_dict(risk_counter),
            "return_class": _counter_dict(return_counter),
            "composite_tag": _counter_dict(tag_counter),
        },
        "ineligible_reason_counts": _counter_dict(ineligible_reason_counter),
        "outputs": {
            "profile_catalog": "stage3_profile_catalog.jsonl",
            "all_validation_rows": "validation_results.jsonl",
            "ineligible_rows": "stage3_ineligible.jsonl",
            "legacy_validated_survivors": "not_created; removed if stale because Stage 4 is now a profile catalog, not a hard pass/fail survivor gate",
        },
        "elapsed_seconds": time.time() - started,
        "note": "Stage 4 checks only minimum pure-OOS expectancy eligibility. MDD and holding days are profile labels for later investment-style selection.",
    }
    write_json(out_dir / "validate_result.json", summary)
    return summary


# ---------- CLI ----------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage 3 aggressive pipeline runner")
    parser.add_argument("--ticker", required=True, help="Ticker to run, e.g. CRWD")
    parser.add_argument("--stage", default="all", choices=["qualify", "entry", "exit", "validate", "all"], help="Stage to run. all runs qualify→entry→exit→validate.")
    parser.add_argument("--out-dir", default=None, help="Existing or new output directory. Defaults to exp_<ticker>_stage3_<YYYYMMDD>_<NNNN> for qualify/all; latest dir for resume stages.")
    parser.add_argument("--seed-base", type=int, default=None, help="Optional deterministic seed base")
    parser.add_argument("--exit-w-timeout-loss", type=float, default=None, help="Optional Stage 3 exit-GA penalty weight for loss-making time_out exits. Default keeps configured baseline.")
    parser.add_argument("--exit-w-deep-stop", type=float, default=None, help="Optional Stage 3 exit-GA penalty weight for deep stop_loss exits. Default keeps configured baseline.")
    parser.add_argument("--exit-deep-stop-threshold-pct", type=float, default=None, help="Optional threshold for deep stop_loss penalty in percentage points. Default keeps configured baseline.")
    parser.add_argument("--no-fitness-cache", action="store_true", help="Disable Stage 3 qualify/entry GA evaluate_fn in-memory fitness cache for smoke comparison")
    return parser.parse_args(argv)


def exit_fitness_weights_from_args(args: argparse.Namespace) -> ExitFitnessWeights:
    """Build exit-fitness weights from CLI overrides without changing defaults."""
    updates: dict[str, float] = {}
    if args.exit_w_timeout_loss is not None:
        updates["w_timeout_loss"] = float(args.exit_w_timeout_loss)
    if args.exit_w_deep_stop is not None:
        updates["w_deep_stop"] = float(args.exit_w_deep_stop)
    if args.exit_deep_stop_threshold_pct is not None:
        updates["deep_stop_threshold_pct"] = float(args.exit_deep_stop_threshold_pct)
    if not updates:
        return DEFAULT_EXIT_FITNESS_WEIGHTS
    return dataclasses.replace(DEFAULT_EXIT_FITNESS_WEIGHTS, **updates)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).upper().strip()
    seed_base = int(args.seed_base) if args.seed_base is not None else default_seed_base(ticker)
    exit_weights = exit_fitness_weights_from_args(args)
    use_fitness_cache = not (bool(args.no_fitness_cache) or fitness_cache_disabled_by_env())
    code_commit = resolve_code_commit(PROJECT_ROOT)
    out_dir = resolve_out_dir(ticker, str(args.stage), args.out_dir)
    ensure_experiment_header(out_dir, ticker=ticker, seed_base=seed_base, stage=str(args.stage))
    print(json.dumps({"event": "stage3_start", "ticker": ticker, "stage": args.stage, "out_dir": str(out_dir), "seed_base": seed_base}, ensure_ascii=False), flush=True)

    summaries: list[dict[str, Any]] = []
    if args.stage == "qualify":
        summaries.append(run_qualify(ticker, out_dir, seed_base=seed_base, use_fitness_cache=use_fitness_cache, code_commit=code_commit))
    elif args.stage == "entry":
        summaries.append(run_entry_ga(ticker, out_dir, seed_base=seed_base, use_fitness_cache=use_fitness_cache, code_commit=code_commit))
    elif args.stage == "exit":
        summaries.append(run_exit_ga(ticker, out_dir, seed_base=seed_base, weights=exit_weights))
    elif args.stage == "validate":
        summaries.append(run_validate(ticker, out_dir, seed_base=seed_base))
    elif args.stage == "all":
        qualify = run_qualify(ticker, out_dir, seed_base=seed_base, use_fitness_cache=use_fitness_cache, code_commit=code_commit)
        summaries.append(qualify)
        if qualify.get("qualified"):
            summaries.append(run_entry_ga(ticker, out_dir, seed_base=seed_base, use_fitness_cache=use_fitness_cache, code_commit=code_commit))
            summaries.append(run_exit_ga(ticker, out_dir, seed_base=seed_base, weights=exit_weights))
            summaries.append(run_validate(ticker, out_dir, seed_base=seed_base))
        else:
            print(json.dumps({"event": "stage3_stop_after_qualify", "ticker": ticker, "qualified": False}, ensure_ascii=False), flush=True)
    else:
        raise ValueError(f"unsupported stage: {args.stage}")

    write_json(out_dir / "last_run_summary.json", {"ticker": ticker, "stage": args.stage, "out_dir": str(out_dir), "summaries": summaries})
    print(json.dumps({"event": "stage3_done", "ticker": ticker, "stage": args.stage, "out_dir": str(out_dir), "summary_count": len(summaries)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
