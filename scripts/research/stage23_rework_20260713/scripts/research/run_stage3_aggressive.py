#!/usr/bin/env python3
"""Stage 3 aggressive runner wrapper with safe qualify-eval early stop.

원본 runner는 같은 폴더의
`run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001`에
보존되어 있다. 이 wrapper는 원본 모듈을 그대로 로드한 뒤 `run_qualify`만
최종 qualify 결과를 바꾸지 않는 조기탈락 버전으로 교체한다.

안전성:
- 세 train split의 GA 후보 pool은 원본과 동일하게 모두 만든다.
- 그 다음 cross-period qualify 평가 중 어떤 필수 split의 pass_count가 0이면,
  all3 pass 후보가 존재할 수 없으므로 즉시 qualified=false로 종료한다.
- 따라서 최종 entry/exit/validate로 넘어가는 ticker 집합은 원본과 동일하다.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

_BACKUP_NAME = "run_stage3_aggressive.py.bak.before_qualify_eval_early_stop_20260706_001"
_BACKUP_PATH = Path(__file__).resolve().with_name(_BACKUP_NAME)
_MODULE_NAME = "_kingmaker_stage3_aggressive_original_20260706"


def _load_original_module() -> Any:
    if not _BACKUP_PATH.exists():
        raise FileNotFoundError(f"Stage3 original backup is missing: {_BACKUP_PATH}")
    loader = importlib.machinery.SourceFileLoader(_MODULE_NAME, str(_BACKUP_PATH))
    spec = importlib.util.spec_from_loader(_MODULE_NAME, loader)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Stage3 original backup: {_BACKUP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


_base = _load_original_module()


def run_qualify(
    ticker: str,
    out_dir: Path,
    *,
    seed_base: int,
    use_fitness_cache: bool = False,
    code_commit: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 3 qualify with result-preserving early stop in cross-period eval.

    원본과 동일하게 세 train split의 GA/top rulebook pool을 먼저 모두 만든다.
    이후 모든 후보를 split별로 재평가할 때 어떤 필수 split에서 pass_count가
    0이면 all3_pass_count는 반드시 0이므로 남은 split 평가는 생략한다.
    """
    started = time.time()
    ctx = context if context is not None else _base.prepare_ticker_context(ticker)
    code_commit = code_commit or _base.resolve_code_commit(_base.PROJECT_ROOT)
    candidates_by_hash: dict[str, Any] = {}
    ga_summaries: list[dict[str, Any]] = []

    # 원본과 동일: 세 train split의 GA 후보 pool을 모두 만든다.
    for idx, split in enumerate(_base.TRAIN_SPLITS, 1):
        split_seed = seed_base + idx
        print(json.dumps({"event": "stage3_qualify_ga_start", "ticker": ticker, "split": split["label"], "seed": split_seed}, ensure_ascii=False), flush=True)

        def evaluate_fn(rulebook: Any, s: dict[str, str] = split) -> float:
            result = _base.run_backtest_period(rulebook, ctx, start=s["start"], end=s["end"])
            return _base.safe_float(getattr(result, "fitness", 0.0), -1_000_000.0)

        evaluate_fn_wrapped, fitness_cache = _base._maybe_cached_evaluate_fn(
            evaluate_fn,
            enabled=use_fitness_cache,
            ticker=ticker,
            period_label=split["label"],
            start_date=split["start"],
            end_date=split["end"],
            fitness_mode="swing",
            code_commit=code_commit,
        )

        ga = _base.run_ga(
            base_rulebook=ctx["base_rulebook"],
            evaluate_fn=evaluate_fn_wrapped,
            ga_config=_base.make_ga_config(population=_base.QUALIFY_POPULATION, generations=_base.QUALIFY_GENERATIONS, seed=split_seed),
        )
        top_rulebooks = _base.collect_top_rulebooks(ga, _base.TOP_N_QUALIFY)
        for rb in top_rulebooks:
            h = _base.compute_rulebook_hash(rb)
            current = candidates_by_hash.get(h)
            if current is None or _base.safe_float(getattr(rb, "fitness", 0.0)) > _base.safe_float(getattr(current, "fitness", 0.0)):
                candidates_by_hash[h] = _base.copy.deepcopy(rb)
        ga_summaries.append(
            {
                "split": split,
                "seed": split_seed,
                "generations_run": getattr(ga, "generations_run", None),
                "top_count": len(top_rulebooks),
                "best_fitness": _base.safe_float(getattr(getattr(ga, "best", None), "fitness", 0.0)),
                "best_hash": _base.compute_rulebook_hash(ga.best) if getattr(ga, "best", None) is not None else None,
                "fitness_cache": _base.summarize_fitness_cache(fitness_cache),
            }
        )
        print(json.dumps({"event": "stage3_qualify_ga_done", "ticker": ticker, "split": split["label"], "top_count": len(top_rulebooks)}, ensure_ascii=False), flush=True)

    candidate_hashes = sorted(candidates_by_hash)
    metrics_by_hash: dict[str, dict[str, dict[str, Any]]] = {h: {} for h in candidate_hashes}
    year_pass_counts: dict[str, int] = {}
    member_score_stats: dict[str, dict[str, Any]] = {}
    fail_reason_counter: Counter[str] = Counter()

    def write_result(*, qualified: bool, all3_pass_count: int, all3_pass_hash_samples: list[str], early_stopped: bool, early_stop_reason: dict[str, Any] | None) -> dict[str, Any]:
        result = {
            "ticker": ticker,
            "stage": "qualify",
            "qualified": bool(qualified),
            "config": _base.dataclasses.asdict(_base.DEFAULT_STAGE3_QUALIFY),
            "periods": list(_base.TRAIN_SPLITS),
            "seed_base": seed_base,
            "data_start": ctx.get("data_start"),
            "data_end": ctx.get("data_end"),
            "ga_summaries": ga_summaries,
            "fitness_cache": _base.aggregate_fitness_cache_summaries([row.get("fitness_cache", {}) for row in ga_summaries]),
            "unique_candidate_count": len(candidate_hashes),
            "year_pass_counts": year_pass_counts,
            "member_score_stats": member_score_stats,
            "all3_pass_count": int(all3_pass_count),
            "all3_pass_hash_samples": all3_pass_hash_samples,
            "fail_reason_metric_counts": dict(sorted(fail_reason_counter.items())),
            "early_stopped": bool(early_stopped),
            "early_stop_reason": early_stop_reason,
            "elapsed_seconds": time.time() - started,
            "note": (
                "qualification rulebooks are intentionally discarded; only summary counts are persisted; "
                "early stop is result-preserving because a required split with pass_count=0 makes all3 qualification impossible"
                if early_stopped
                else "qualification rulebooks are intentionally discarded; only summary counts are persisted"
            ),
        }
        _base.write_json(out_dir / "qualify_result.json", result)
        return result

    # 원본과 동일한 cross-period 평가를 하되, 필수 split pass_count=0이면 즉시 탈락 확정.
    for split in _base.TRAIN_SPLITS:
        raw_rows: list[dict[str, Any]] = []
        print(json.dumps({"event": "stage3_qualify_eval_start", "ticker": ticker, "split": split["label"], "candidate_count": len(candidate_hashes)}, ensure_ascii=False), flush=True)
        for rank, h in enumerate(candidate_hashes, 1):
            rb = candidates_by_hash[h]
            result = _base.run_backtest_period(rb, ctx, start=split["start"], end=split["end"])
            raw_rows.append(
                {
                    "ticker": ticker,
                    "label": split["label"],
                    "period_label": split["label"],
                    "rulebook_hash": h,
                    "rank_is": rank,
                    "oos": _base.result_metrics(result),
                }
            )
        scored = _base._score_period_candidates(raw_rows)
        pass_count = 0
        scores: list[float] = []
        for row in scored:
            h = str(row["rulebook_hash"])
            metrics = dict(row.get("oos_metrics") or {})
            metrics["member_score"] = _base.safe_float(row.get("oos_member_score"))
            metrics["fitness"] = _base.safe_float(row.get("fitness"))
            metrics_by_hash[h][split["label"]] = metrics
            scores.append(metrics["member_score"])
            if _base._pass_one_year(metrics):
                pass_count += 1
        year_pass_counts[split["label"]] = pass_count
        member_score_stats[split["label"]] = {
            "count": len(scores),
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "mean": sum(scores) / len(scores) if scores else None,
        }
        print(json.dumps({"event": "stage3_qualify_eval_done", "ticker": ticker, "split": split["label"], "pass_count": pass_count}, ensure_ascii=False), flush=True)

        if pass_count <= 0:
            fail_reason_counter["early_stop_zero_pass_split"] += 1
            early_stop_reason = {
                "split": split["label"],
                "reason": "required_split_has_zero_passing_candidates",
                "evaluated_split_count": len(year_pass_counts),
                "proof": "Stage3 qualification requires at least one candidate to pass all train splits; zero pass candidates in any required split makes all3_pass_count impossible.",
            }
            print(json.dumps({"event": "stage3_qualify_eval_early_stop", "ticker": ticker, **early_stop_reason}, ensure_ascii=False), flush=True)
            return write_result(
                qualified=False,
                all3_pass_count=0,
                all3_pass_hash_samples=[],
                early_stopped=True,
                early_stop_reason=early_stop_reason,
            )

    all3_pass_count = 0
    all3_pass_hash_samples: list[str] = []
    for h in candidate_hashes:
        reasons = _base.stage3_qualify_fail_reasons(metrics_by_hash.get(h, {}), _base.DEFAULT_STAGE3_QUALIFY)
        if not reasons:
            all3_pass_count += 1
            if len(all3_pass_hash_samples) < 10:
                all3_pass_hash_samples.append(h)
        else:
            for reason in reasons:
                fail_reason_counter[str(reason.get("metric") or "unknown")] += 1

    return write_result(
        qualified=all3_pass_count > 0,
        all3_pass_count=all3_pass_count,
        all3_pass_hash_samples=all3_pass_hash_samples,
        early_stopped=False,
        early_stop_reason=None,
    )


# Monkey-patch original module so its main()/run_entry/exit/validate flow remains unchanged.
_base.run_qualify = run_qualify


def main(argv: list[str] | None = None) -> int:
    return _base.main(argv)


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


if __name__ == "__main__":
    raise SystemExit(main())
