#!/usr/bin/env python3
"""
Rolling Stage2 + prior-5-day next-day high/low predictor GA.

핵심:
- 기존 b03f39b 버전의 GA/feature/evaluation 로직은 그대로 사용한다.
- 3개 고정 train split 축약을 제거한다.
- 기본값은 252거래일 학습창을 21거래일씩 앞으로 밀며 rolling 학습한다.
- 각 rolling window에서 살아남은 survivor만 다음 window seed population으로 전달한다.
- 목표는 HIGH/LOW bin 동시 예측 + 실제 고저폭 pct MAE 개선이다.

Read/write scope:
- OHLCV/cache/news csv는 read-only로 읽는다.
- 결과는 out_dir 아래 연구 산출물만 생성한다.
- run_live, 실거래, 캐시 갱신 없음.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import random
import subprocess
import sys
import time
import types
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_COMMIT = "b03f39b"
LEGACY_PATH = "scripts/research/run_range_predictor_stage2_v3.py"


def _load_legacy_module() -> types.ModuleType:
    code = subprocess.check_output(
        ["git", "show", f"{LEGACY_COMMIT}:{LEGACY_PATH}"],
        cwd=str(PROJECT_ROOT),
        text=True,
    )
    mod = types.ModuleType("_km_range_predictor_v3_b03f39b")
    mod.__file__ = str(PROJECT_ROOT / LEGACY_PATH)
    mod.__name__ = "_km_range_predictor_v3_b03f39b"
    sys.modules[mod.__name__] = mod
    exec(compile(code, mod.__file__, "exec"), mod.__dict__)
    return mod


L = _load_legacy_module()


def json_safe(value: Any) -> Any:
    return L.json_safe(value)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    L.write_json(path, payload)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    L.write_jsonl(path, rows)


def safe_float(value: Any, default: float = 0.0) -> float:
    return L.safe_float(value, default)


def predictor_signature(ind: Any) -> str:
    return L.predictor_signature(ind)


def auto_out_dir(ticker: str) -> Path:
    prefix = f"exp_{ticker.lower()}_range_predictor_stage2_v3_rolling_hilo_mae_{time.strftime('%Y%m%d')}_"
    for idx in range(1, 10000):
        candidate = PROJECT_ROOT / f"{prefix}{idx:04d}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("cannot allocate output directory")


def build_rolling_splits(data, train_days: int, step_days: int, start: str | None, end: str | None) -> list[dict[str, Any]]:
    dates = list(data["date"].astype(str).drop_duplicates())
    if start:
        dates = [d for d in dates if d >= start]
    if end:
        dates = [d for d in dates if d <= end]
    if len(dates) < train_days:
        raise ValueError(f"not enough dates for rolling train_days={train_days}: available={len(dates)}")
    out: list[dict[str, Any]] = []
    idx = 0
    split_no = 1
    while idx + train_days <= len(dates):
        s = dates[idx]
        e = dates[idx + train_days - 1]
        out.append({
            "label": f"roll_{split_no:03d}_{s}_{e}",
            "train_start": s,
            "train_end": e,
            "roll_index": split_no,
            "train_days": train_days,
            "step_days": step_days,
        })
        idx += step_days
        split_no += 1
    return out


def run_rolling_stage2_predictor(
    ticker: str,
    out_dir: Path,
    seed_base: int,
    survivor_count: int,
    rolling_train_days: int,
    rolling_step_days: int,
    rolling_start: str | None,
    rolling_end: str | None,
) -> dict[str, Any]:
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=False)

    data, feature_meta = L.build_dataset(ticker)
    all_features = L.feature_columns(data)
    rolling_splits = build_rolling_splits(data, rolling_train_days, rolling_step_days, rolling_start, rolling_end)

    seed_pop = None
    all_predictor_rows: list[dict[str, Any]] = []
    all_history_rows: list[dict[str, Any]] = []
    train_gate_rows: list[dict[str, Any]] = []
    stage_survivor_rows: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    features_used_union: set[str] = set()
    final_qspec: dict[str, Any] = {}

    for split_idx, split in enumerate(rolling_splits, 1):
        rng = random.Random(seed_base + split_idx * 1000)
        train_df = L.period_frame(data, split["train_start"], split["train_end"])
        qspec = L.make_quantile_spec(train_df, all_features)
        usable_features = [f for f in all_features if f in qspec]
        features_used_union.update(usable_features)
        baseline_spec = L.make_baseline_spec(train_df)
        init_pop = L.prepare_population_for_split(seed_pop, rng, qspec, baseline_spec)
        pop, history = L.run_ga_on_split(init_pop, train_df, usable_features, qspec, split, seed_base + split_idx)
        scored = L.evaluate_population(pop, train_df, usable_features, qspec, split["label"], "train")
        survivors, selected_rows = L.select_survivors(pop, scored, survivor_count)

        for rank, ind in enumerate(pop, 1):
            all_predictor_rows.append({
                "ticker": ticker,
                "train_label": split["label"],
                "origin_rank": rank,
                "signature": ind.signature or predictor_signature(ind),
                "fitness": safe_float(ind.fitness),
                "metrics": ind.metrics,
                "predictor": L.individual_to_dict(ind),
                "stage": split_idx,
                "rolling_split": split,
            })
        for h in history:
            h["generations_run"] = len(history)
            h["early_stop_triggered"] = len(history) < L.GENERATIONS
            h["rolling_split"] = split
        all_history_rows.extend(history)
        for row in scored:
            train_gate_rows.append({**dict(row), "ticker": ticker, "stage": split_idx, "train_start": split["train_start"], "train_end": split["train_end"], "rolling_split": split})
        for rank, row in enumerate(selected_rows, 1):
            stage_survivor_rows.append({"ticker": ticker, "stage": split_idx, "train_label": split["label"], "survivor_rank": rank, "rolling_split": split, **row})

        gate_passed_count = sum(1 for r in scored if r.get("passed_gate"))
        trace.append({
            "stage": split_idx,
            "train_label": split["label"],
            "train_start": split["train_start"],
            "train_end": split["train_end"],
            "input_seed_count": len(seed_pop or []),
            "population": len(pop),
            "gate_passed_count": gate_passed_count,
            "selected_survivor_count": len(survivors),
            "fallback_used": gate_passed_count == 0,
            "best_fitness": safe_float(pop[0].fitness),
            "best_signature": pop[0].signature,
            "feature_count": len(usable_features),
        })
        seed_pop = survivors
        final_qspec = qspec

    final_pop = seed_pop or []
    final_periods = L.build_final_periods(data)
    final_eval_rows: list[dict[str, Any]] = []
    alive = final_pop
    final_trace: list[dict[str, Any]] = []
    features_final = sorted(f for f in features_used_union if f in final_qspec)

    for period in final_periods:
        pdf = L.period_frame(data, period["start"], period["end"])
        scored = L.evaluate_population(alive, pdf, features_final, final_qspec, period["label"], period["kind"])
        passed_sigs = {str(r.get("signature")) for r in scored if r.get("passed_gate")}
        for row in scored:
            final_eval_rows.append({**dict(row), "ticker": ticker, "period_start": period["start"], "period_end": period["end"]})
        final_trace.append({"period_label": period["label"], "period_kind": period["kind"], "reached": len(alive), "passed": len(passed_sigs), "failed": len(alive) - len(passed_sigs)})
        alive = [ind for ind in alive if (ind.signature or predictor_signature(ind)) in passed_sigs]

    final_survivor_rows = [{"ticker": ticker, "signature": ind.signature or predictor_signature(ind), "predictor": L.individual_to_dict(ind)} for ind in alive]
    distributions = {p["label"]: {"high": L.distribution(L.period_frame(data, p["start"], p["end"])["high_bin"].to_numpy(dtype=int)), "low": L.distribution(L.period_frame(data, p["start"], p["end"])["low_bin"].to_numpy(dtype=int))} for p in final_periods}

    write_jsonl(out_dir / "predictors_all.jsonl", all_predictor_rows)
    write_jsonl(out_dir / "ga_history.jsonl", all_history_rows)
    write_jsonl(out_dir / "train_gate_metrics.jsonl", train_gate_rows)
    write_jsonl(out_dir / "stage_survivors.jsonl", stage_survivor_rows)
    write_jsonl(out_dir / "final_period_metrics.jsonl", final_eval_rows)
    write_jsonl(out_dir / "final_survivors.jsonl", final_survivor_rows)

    source_counts = Counter(m.get("source", "unknown") for m in feature_meta if m.get("feature") in features_final)
    config = {
        "ticker": ticker,
        "runner": "scripts/research/run_range_predictor_stage2_v3.py",
        "legacy_logic_source": f"{LEGACY_COMMIT}:{LEGACY_PATH}",
        "mode": "rolling_stage2_plus_prior5_hilo_bin_and_pct_mae",
        "rolling": {
            "train_days": rolling_train_days,
            "step_days": rolling_step_days,
            "start": rolling_start,
            "end": rolling_end,
            "split_count": len(rolling_splits),
            "splits": rolling_splits,
        },
        "final_periods": final_periods,
        "ga": {
            "population": L.POPULATION,
            "generations": L.GENERATIONS,
            "patience": L.PATIENCE,
            "elite_ratio": L.ELITE_RATIO,
            "mutation_rate": L.MUTATION_RATE,
            "rule_count": L.RULE_COUNT,
            "survivor_count": survivor_count,
            "random_immigrant_ratio": L.RANDOM_IMMIGRANT_RATIO,
            "seed_base": seed_base,
            "min_band_width_q": L.MIN_BAND_WIDTH_Q,
            "max_band_width_q": L.MAX_BAND_WIDTH_Q,
            "softness_range": [L.MIN_SOFTNESS, L.MAX_SOFTNESS],
        },
        "target": {"high": "D-day high pct from D open", "low": "D-day low magnitude pct from D open", "objective": "both HIGH/LOW bin accuracy + actual pct MAE improvement"},
        "gate": dataclasses.asdict(L.DEFAULT_GATE),
        "lookahead_report": {"pass": True, "stock_features": "Stage2 entry components for D-1~D-5 plus D-1 tight swing-style features and D0 open gap", "flow_features": "optional D-1 orderbook/flow columns if cache provides them", "market_features": "ETF D0 gap or D-1 confirmed values only", "news_features": "market_history rows joined from D-1 date only", "final_eval_quantile_reference": "last rolling train qspec only; no final-period distribution fitting", "excluded": ["D0 high/low/close as features", "future trading results"]},
        "feature_count": len(features_final),
        "feature_sources": dict(source_counts),
        "bin_labels": L.BIN_LABELS,
        "default_bin_centers_pct": L.DEFAULT_BIN_CENTERS,
        "distributions": distributions,
    }
    write_json(out_dir / "config.json", config)
    summary = {
        "ticker": ticker,
        "mode": "rolling_stage2_plus_prior5_hilo_bin_and_pct_mae",
        "rolling_split_count": len(rolling_splits),
        "stage_trace": trace,
        "final_trace": final_trace,
        "final_survivor_count": len(alive),
        "final_survivor_signatures": [ind.signature or predictor_signature(ind) for ind in alive],
        "elapsed_sec": time.time() - started,
        "outputs": {"predictors_all": str(out_dir / "predictors_all.jsonl"), "ga_history": str(out_dir / "ga_history.jsonl"), "train_gate_metrics": str(out_dir / "train_gate_metrics.jsonl"), "stage_survivors": str(out_dir / "stage_survivors.jsonl"), "final_period_metrics": str(out_dir / "final_period_metrics.jsonl"), "final_survivors": str(out_dir / "final_survivors.jsonl"), "config": str(out_dir / "config.json"), "summary": str(out_dir / "summary.json")},
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rolling Stage2 + prior 5 days GA for next-day HIGH/LOW bin and pct-MAE prediction")
    p.add_argument("--ticker", required=True)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--seed-base", type=int, default=None)
    p.add_argument("--survivor-count", type=int, default=L.SURVIVOR_COUNT)
    p.add_argument("--rolling-train-days", type=int, default=252)
    p.add_argument("--rolling-step-days", type=int, default=21)
    p.add_argument("--rolling-start", default="2022-07-01")
    p.add_argument("--rolling-end", default="2025-06-30")
    p.add_argument("--parallel", action="store_true", help="accepted for interface parity; not used")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ticker = str(args.ticker).strip().upper()
    if not ticker:
        raise SystemExit("--ticker must not be empty")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else auto_out_dir(ticker)
    seed_base = int(args.seed_base) if args.seed_base is not None else L.default_seed_base(ticker)
    run_rolling_stage2_predictor(
        ticker=ticker,
        out_dir=out_dir,
        seed_base=seed_base,
        survivor_count=max(1, int(args.survivor_count)),
        rolling_train_days=max(50, int(args.rolling_train_days)),
        rolling_step_days=max(1, int(args.rolling_step_days)),
        rolling_start=args.rolling_start,
        rolling_end=args.rolling_end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
