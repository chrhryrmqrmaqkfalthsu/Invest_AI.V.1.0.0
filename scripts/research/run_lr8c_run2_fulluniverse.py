#!/usr/bin/env python3
"""LR8C-RUN2 full-universe qualified Top-N survivor runner.

This script is intentionally research-only:
- no promote apply
- no parameters.json write
- no live rulebook mutation
- append-only ignored research artifacts under data/_system/research

Parallel mode:
- use --shard-count N --shard-index I to split live universe by ticker index
- all shards append to the same JSONL with fcntl.flock protection
- run_key based resume prevents reprocessing completed ticker/period rows
"""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from engine.core.metadata import compute_rulebook_hash
from engine.learning.backtest import run_backtest
from engine.learning.genetic import GAConfig, collect_top_rulebooks, run_ga
from engine.live.universe import LiveUniverseConfig, load_live_universe
from engine.pipeline.context import prepare_ticker_context
from engine.pipeline.rolling_validation import DEFAULT_POSITION_LIMIT_KRW
from engine.pipeline.topn_survivor import evaluate_survivors, score_topn_validation_periods
from scripts.research.rulebook_persist import collect_rulebook_rows

OUT_DIR = Path("data/_system/research/lr8c_run2_20260607")
TIMING_PATH = OUT_DIR / "lr8c_run2_timing.txt"
TOPN_PATH = OUT_DIR / "lr8c_run2_topn.jsonl"
RULEBOOKS_PATH = OUT_DIR / "lr8c_run2_topn_rulebooks.jsonl"
SURVIVORS_PATH = OUT_DIR / "lr8c_run2_survivors.jsonl"
REPORT_PATH = OUT_DIR / "LR8C_RUN2_REPORT.md"

POPULATION = 40
GENERATIONS = 50
QUALIFIED_COLLECT_N = 9999
MAX_CANDIDATES_PER_PERIOD = 50
MIN_TRADES = 5
MIN_MEMBER_SCORE = 10.0
BALANCED_K = 2
STRICT_K = 3
GENERAL_YEARS = (2022, 2023, 2024)
STRESS_LABEL = "2025H2"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON row with an inter-process file lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            lines = f.read().splitlines()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def completed_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for row in read_jsonl(path):
        key = row.get("run_key")
        if key:
            keys.add(str(key))
    return keys


def make_ga_config(seed: int) -> GAConfig:
    return GAConfig(
        population=POPULATION,
        generations=GENERATIONS,
        elite_ratio=0.2,
        mutation_rate=0.15,
        mutation_strength=0.2,
        tournament_size=3,
        seed_pattern_ratio=0.33,
        early_stop_no_improve=GENERATIONS,
        random_seed=seed,
    )


def build_splits(data_min: str | None, data_max: str | None) -> list[dict[str, Any]]:
    start = str(data_min or "2020-01-01")
    end = str(data_max or "2026-06-05")
    return [
        {"label": "2022", "year": 2022, "train_start": start, "train_end": "2021-12-31", "test_start": "2022-01-01", "test_end": "2022-12-31", "is_stress": False},
        {"label": "2023", "year": 2023, "train_start": start, "train_end": "2022-12-31", "test_start": "2023-01-01", "test_end": "2023-12-31", "is_stress": False},
        {"label": "2024", "year": 2024, "train_start": start, "train_end": "2023-12-31", "test_start": "2024-01-01", "test_end": "2024-12-31", "is_stress": False},
        {"label": STRESS_LABEL, "year": STRESS_LABEL, "train_start": start, "train_end": "2025-05-31", "test_start": "2025-06-01", "test_end": end, "is_stress": True},
    ]


def base_kwargs(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_limit_krw": DEFAULT_POSITION_LIMIT_KRW,
        "market_history_df": ctx["market_history_df"],
        "sector_name": ctx["sector_name"],
        "ticker_sentiment": ctx["ticker_sentiment"],
        "fitness_mode": "swing",
    }


def float0(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def backtest_oos_row(ticker: str, split: dict[str, Any], rank_is: int, rb: Any, result: Any) -> dict[str, Any]:
    rulebook_hash = compute_rulebook_hash(rb)
    return {
        "ticker": ticker,
        "year": split["year"],
        "label": split["label"],
        "is_stress": bool(split.get("is_stress")),
        "rank_is": int(rank_is),
        "rulebook_hash": rulebook_hash,
        "train_fitness": float0(getattr(rb, "fitness", 0.0)),
        "train_period": [split["train_start"], split["train_end"]],
        "test_period": [split["test_start"], split["test_end"]],
        "oos": {
            "trade_count": int(getattr(result, "trade_count", 0) or 0),
            "win_rate": float0(getattr(result, "win_rate", 0.0)),
            "expectancy_pct": float0(getattr(result, "expectancy_pct", 0.0)),
            "profit_factor": float0(getattr(result, "profit_factor", 0.0)),
            "max_drawdown_pct": float0(getattr(result, "max_drawdown_pct", 0.0)),
        },
        "fitness": float0(getattr(result, "fitness", 0.0)),
    }


def run_one_period(ticker: str, ctx: dict[str, Any], split: dict[str, Any], seed: int) -> dict[str, Any]:
    df = ctx["df"]
    kwargs = base_kwargs(ctx)
    ga_cfg = make_ga_config(seed)

    def evaluate_fn(rb):
        result = run_backtest(
            rb,
            df,
            start_date=split["train_start"],
            end_date=split["train_end"],
            **kwargs,
        )
        return result.fitness

    start = time.perf_counter()
    ga_result = run_ga(base_rulebook=ctx["base_rulebook"], evaluate_fn=evaluate_fn, ga_config=ga_cfg)
    ga_elapsed = time.perf_counter() - start

    candidates = collect_top_rulebooks(ga_result, QUALIFIED_COLLECT_N)
    candidate_rows: list[dict[str, Any]] = []
    oos_start = time.perf_counter()
    for rank_is, rb in enumerate(candidates, 1):
        oos_result = run_backtest(
            rb,
            df,
            start_date=split["test_start"],
            end_date=split["test_end"],
            **kwargs,
        )
        candidate_rows.append(backtest_oos_row(ticker, split, rank_is, rb, oos_result))
    oos_elapsed = time.perf_counter() - oos_start

    scored = score_topn_validation_periods(
        {"periods": [{"year": split["year"], "label": split["label"], "candidates": candidate_rows}]},
        general_years=GENERAL_YEARS,
        stress_labels=(STRESS_LABEL,),
    )
    scored_periods = scored["stress_periods"] if split.get("is_stress") else scored["general_periods"]
    scored_candidates = scored_periods[0]["candidates"] if scored_periods else []
    qualified = [
        row
        for row in scored_candidates
        if int(row.get("oos_metrics", {}).get("trade_count", 0) or 0) >= MIN_TRADES
        and float(row.get("oos_member_score", 0.0) or 0.0) >= MIN_MEMBER_SCORE
    ]
    qualified.sort(key=lambda row: (-float(row.get("train_fitness", 0.0) or 0.0), str(row.get("rulebook_hash", ""))))
    cap_triggered = len(qualified) > MAX_CANDIDATES_PER_PERIOD
    qualified_capped = qualified[:MAX_CANDIDATES_PER_PERIOD]

    elapsed = time.perf_counter() - start
    return {
        "run_key": f"{ticker}|{split['label']}",
        "created_at": utc_now(),
        "ticker": ticker,
        "year": split["year"],
        "label": split["label"],
        "is_stress": bool(split.get("is_stress")),
        "split": dict(split),
        "config": {
            "population": POPULATION,
            "generations": GENERATIONS,
            "candidate_mode": "qualified_all",
            "qualified_collect_n": QUALIFIED_COLLECT_N,
            "min_trades": MIN_TRADES,
            "min_member_score": MIN_MEMBER_SCORE,
            "max_candidates_per_period": MAX_CANDIDATES_PER_PERIOD,
        },
        "timing": {
            "ga_seconds": round(ga_elapsed, 6),
            "oos_candidates_seconds": round(oos_elapsed, 6),
            "elapsed_seconds": round(elapsed, 6),
        },
        "ga": {
            "generations_run": getattr(ga_result, "generations_run", None),
            "best_fitness": float0(getattr(getattr(ga_result, "best", None), "fitness", 0.0)),
            "final_population_size": len(getattr(ga_result, "final_population", []) or []),
            "unique_candidate_pool_count": len(candidates),
        },
        "candidate_pool_count": len(candidates),
        "qualified_count_before_cap": len(qualified),
        "qualified_count": len(qualified_capped),
        "cap_triggered": bool(cap_triggered),
        "candidates": qualified_capped,
        "_rulebooks": collect_rulebook_rows(
            f"{ticker}|{split['label']}",
            ticker,
            split["year"],
            candidates,
            qualified_capped,
        ),
    }


def load_topn_validation_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    periods: list[dict[str, Any]] = []
    for row in rows:
        periods.append(
            {
                "ticker": row.get("ticker"),
                "year": row.get("year"),
                "label": row.get("label"),
                "is_stress": row.get("is_stress"),
                "train_period": [row.get("split", {}).get("train_start"), row.get("split", {}).get("train_end")],
                "test_period": [row.get("split", {}).get("test_start"), row.get("split", {}).get("test_end")],
                "candidate_count": row.get("qualified_count"),
                "candidates": row.get("candidates", []),
            }
        )
    return {"method": "qualified_all_min_trades_member_score", "periods": periods}


def survivor_rows_with_ticker(topn_validation: dict[str, Any], survivor_k: int) -> list[dict[str, Any]]:
    scored = score_topn_validation_periods(topn_validation, general_years=GENERAL_YEARS, stress_labels=(STRESS_LABEL,))
    survivors = evaluate_survivors(scored, survivor_k=survivor_k, min_trades=MIN_TRADES, min_member_score=MIN_MEMBER_SCORE)
    ticker_by_hash: dict[str, str] = {}
    for period in topn_validation.get("periods", []):
        for candidate in period.get("candidates", []) or []:
            h = str(candidate.get("rulebook_hash") or "")
            if h and h not in ticker_by_hash:
                ticker_by_hash[h] = str(candidate.get("ticker") or period.get("ticker") or "")
    out = []
    for row in survivors:
        enriched = dict(row)
        enriched["ticker"] = ticker_by_hash.get(str(row.get("rulebook_hash") or ""), "")
        enriched["combo_id"] = "balanced_k2" if survivor_k == BALANCED_K else "strict_k3"
        out.append(enriched)
    return out


def write_survivors_and_report(universe_symbols: tuple[str, ...], timing: dict[str, Any] | None) -> None:
    rows = read_jsonl(TOPN_PATH)
    topn_validation = load_topn_validation_from_rows(rows)
    balanced = survivor_rows_with_ticker(topn_validation, BALANCED_K)
    strict = survivor_rows_with_ticker(topn_validation, STRICT_K)

    SURVIVORS_PATH.write_text("", encoding="utf-8")
    for row in balanced + strict:
        append_jsonl(SURVIVORS_PATH, row)

    cap_rows = [r for r in rows if r.get("cap_triggered")]
    actual_symbols = sorted({str(r.get("ticker")) for r in rows if r.get("ticker")})
    general_rows = [r for r in rows if not r.get("is_stress")]
    stress_rows = [r for r in rows if r.get("is_stress")]
    q_counts = [int(r.get("qualified_count", 0) or 0) for r in rows]
    elapsed = [float(r.get("timing", {}).get("elapsed_seconds", 0.0) or 0.0) for r in rows]

    r1 = bool((balanced or strict) and all(row.get("ticker") and "steady_" not in str(row.get("rulebook_hash")) for row in balanced + strict))
    r3 = bool(stress_rows and all(str(r.get("label")) == STRESS_LABEL for r in stress_rows) and all(str(r.get("label")) != STRESS_LABEL for r in general_rows))

    report = f"""# LR8C-RUN2 — Full Universe Qualified Survivor 실행 보고서

- 날짜: 2026-06-07
- 브랜치: `lr8c-run2-fulluniverse-20260607`
- population: {POPULATION}
- generations: {GENERATIONS}
- 후보 선정: qualified 기준 통과 전부, 종목·구간당 최대 {MAX_CANDIDATES_PER_PERIOD}개
- qualified 기준: min_trades={MIN_TRADES}, min_member_score={MIN_MEMBER_SCORE}
- universe: live promoted {len(universe_symbols)} symbols
- periods: 2022 / 2023 / 2024 + {STRESS_LABEL}

## 0. STEP 0 시간 측정

```text
{json.dumps(timing or {}, ensure_ascii=False, indent=2, sort_keys=True)}
```

**예상 전체시간: {(timing or {}).get('estimated_total_hours_85x4', 'unknown')} hours.**

## 1. 풀 실행 요약

```text
completed_period_rows = {len(rows)}
expected_period_rows = {len(universe_symbols) * 4}
completed_symbols = {len(actual_symbols)}
general_rows = {len(general_rows)}
stress_rows = {len(stress_rows)}
qualified_count_min = {min(q_counts) if q_counts else 0}
qualified_count_avg = {round(mean(q_counts), 6) if q_counts else 0.0}
qualified_count_max = {max(q_counts) if q_counts else 0}
elapsed_seconds_avg = {round(mean(elapsed), 6) if elapsed else 0.0}
```

## 2. Survivor 요약

```text
balanced_k2_survivors = {len(balanced)}
strict_k3_survivors = {len(strict)}
```

상세:

```text
{SURVIVORS_PATH}
```

## 3. 2025H2 stress 집계

2025H2는 일반 3개 연도와 동급 합격 gate로 쓰지 않았다. `topn_survivor.evaluate_survivors()`에는 일반 연도 2022/2023/2024만 survivor gate로 들어가고, 2025H2는 `stress_avg_member_score`, `stress_worst_member_score`로만 붙는다.

## 4. Qualified cap 현황

```text
cap_triggered_count = {len(cap_rows)}
```

{json.dumps([{'ticker': r.get('ticker'), 'label': r.get('label'), 'qualified_count_before_cap': r.get('qualified_count_before_cap')} for r in cap_rows[:50]], ensure_ascii=False, indent=2, sort_keys=True)}

## 5. 검증 R1~R4

```text
R1_real_ticker_rulebook_survivors = {r1}
R2_top_n_1_compatibility = NOT_RECHECKED_IN_RUN2_CODE_PATH_UNCHANGED_FROM_LR8B
R3_2025H2_stress_separate = {r3}
R4_cap_rows_listed = true
```

## 6. 금지 사항 준수

```text
promote 실행 없음
parameters.json 수정 없음
live 룰북 저장 없음
기존 85종목 룰북 hash 변경 없음
top_n=1 하위호환 경로 변경 없음
compute_rulebook_hash 변경 없음
2025H2를 일반 gate로 사용하지 않음
```
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-after-step0", action="store_true")
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("--shard-index must satisfy 0 <= index < shard-count")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TOPN_PATH.touch(exist_ok=True)
    RULEBOOKS_PATH.touch(exist_ok=True)
    SURVIVORS_PATH.touch(exist_ok=True)

    universe = load_live_universe(LiveUniverseConfig())
    all_symbols = tuple(universe.symbols)
    symbols = all_symbols[args.shard_index :: args.shard_count]
    done = completed_keys(TOPN_PATH)
    total_periods = len(all_symbols) * 4

    timing: dict[str, Any] | None = None
    if TIMING_PATH.exists() and TIMING_PATH.read_text(encoding="utf-8").strip():
        timing = json.loads(TIMING_PATH.read_text(encoding="utf-8"))

    if not symbols:
        print(f"LR8C_RUN2 shard {args.shard_index}/{args.shard_count}: no symbols assigned", flush=True)
        return 0

    first_symbol = all_symbols[0]
    first_ctx = prepare_ticker_context(first_symbol) if args.shard_index == 0 or timing is None else None
    first_split = build_splits(first_ctx.get("data_min"), first_ctx.get("data_max"))[0] if first_ctx is not None else None
    first_key = f"{first_symbol}|{first_split['label']}" if first_split is not None else ""

    if timing is None and args.shard_index == 0 and first_ctx is not None and first_split is not None:
        start = time.perf_counter()
        if first_key not in done:
            row = run_one_period(first_symbol, first_ctx, first_split, seed=20260607 + 2022)
            rulebook_rows = row.pop("_rulebooks", [])
            append_jsonl(TOPN_PATH, row)
            for rr in rulebook_rows:
                append_jsonl(RULEBOOKS_PATH, rr)
            done.add(first_key)
        elapsed = time.perf_counter() - start
        measured_rows = [r for r in read_jsonl(TOPN_PATH) if r.get("run_key") == first_key]
        measured = measured_rows[-1] if measured_rows else {}
        measured_elapsed = float(measured.get("timing", {}).get("elapsed_seconds", elapsed) or elapsed)
        estimated = measured_elapsed * len(all_symbols) * 4
        timing = {
            "step": "STEP0_TIMING",
            "created_at": utc_now(),
            "ticker": first_symbol,
            "universe_count": len(all_symbols),
            "population": POPULATION,
            "generations": GENERATIONS,
            "candidate_mode": "qualified_all",
            "min_trades": MIN_TRADES,
            "min_member_score": MIN_MEMBER_SCORE,
            "max_candidates_per_period": MAX_CANDIDATES_PER_PERIOD,
            "split": first_split,
            "elapsed_seconds_one_ticker_one_period_total": round(measured_elapsed, 6),
            "qualified_count": measured.get("qualified_count"),
            "qualified_count_before_cap": measured.get("qualified_count_before_cap"),
            "candidate_pool_count": measured.get("candidate_pool_count"),
            "estimated_total_seconds_85x4": round(estimated, 6),
            "estimated_total_hours_85x4": round(estimated / 3600.0, 6),
            "decision": "PROCEED_NO_TIME_GATE",
        }
        TIMING_PATH.write_text(json.dumps(timing, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(timing, ensure_ascii=False, sort_keys=True), flush=True)

    if args.stop_after_step0:
        write_survivors_and_report(all_symbols, timing)
        return 0

    print(
        f"LR8C_RUN2 shard {args.shard_index}/{args.shard_count}: assigned_symbols={len(symbols)} total_completed={len(done)}/{total_periods}",
        flush=True,
    )
    for local_idx, ticker in enumerate(symbols, 1):
        ctx = first_ctx if ticker == first_symbol and first_ctx is not None else prepare_ticker_context(ticker)
        splits = build_splits(ctx.get("data_min"), ctx.get("data_max"))
        for split_idx, split in enumerate(splits, 1):
            key = f"{ticker}|{split['label']}"
            done = completed_keys(TOPN_PATH)
            if key in done:
                continue
            seed = 20260607 + (args.shard_index + 1) * 1_000_000 + local_idx * 100 + split_idx
            print(
                f"LR8C_RUN2 shard {args.shard_index}/{args.shard_count} progress {len(done) + 1}/{total_periods}: {ticker} {split['label']}",
                flush=True,
            )
            row = run_one_period(ticker, ctx, split, seed=seed)
            rulebook_rows = row.pop("_rulebooks", [])
            append_jsonl(TOPN_PATH, row)
            for rr in rulebook_rows:
                append_jsonl(RULEBOOKS_PATH, rr)

    write_survivors_and_report(all_symbols, timing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
