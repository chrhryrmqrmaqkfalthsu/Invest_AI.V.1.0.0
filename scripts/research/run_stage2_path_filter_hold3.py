#!/usr/bin/env python3
"""
Stage2 Path Filter + max_holding_days<=3 Runner.

생성 목적: 2026-07-04, scripts/research/run_stage2_path_filter.py의 5일 경로 필터와
일평균 수익률 fitness는 그대로 유지하되, FIX path-filter v1 실패 원인으로 확인된 긴 보유
(max_holding_days 25~28) 문제만 분리 검증하기 위해 max_holding_days 유전자 범위를 1~3으로
강제합니다.

기존 run_stage2_path_filter.py 대비 차이:
- 변경: max_holding_days GA 탐색 범위를 (1, 3)으로 고정.
- 추가: seed/elite/crossover/deserialize 경로에서도 max_holding_days가 3을 넘지 않도록 runner
  프로세스 내부 monkey patch로 clamp.
- 유지: D-5~D-1 5일 경로 피처셋 전체.
- 유지: daily-return fitness = mean(pnl_pct / max(1, holding_days)).
- 유지: 원본 Stage2 rolling 3분할, population 100, generations 50, patience 15, survivor gate.

금지/보장:
- engine/, scripts/research/run_stage2.py, _calc_fitness_swing은 수정하지 않습니다.
- run_live/실거래/캐시갱신과 무관한 research-only runner입니다.
- look-ahead 차단: 경로 피처는 신호일 기준 D-5~D-1 확정봉만 사용하며 D-day 가격은 사용하지 않습니다.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from engine.strategies.rulebook import Rulebook
from scripts.research import run_stage2_path_filter as path_filter

HOLD3_VERSION = "path_filter_hold3_v1"
MAX_HOLDING_RANGE = (1, 3)
_PATCHED_HOLD3 = False
_ORIGINAL_GENETIC_FINALIZE = None
_ORIGINAL_RULEBOOK_FROM_DICT = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: _json_safe(v) for k, v in vars(value).items() if not str(k).startswith("_")}
    return str(value)


def clamp_max_holding_days(rb: Rulebook) -> Rulebook:
    lo, hi = MAX_HOLDING_RANGE
    try:
        value = int(round(float(getattr(rb, "max_holding_days", hi))))
    except Exception:
        value = hi
    setattr(rb, "max_holding_days", max(lo, min(hi, value)))
    setattr(rb, "path_filter_hold3_version", HOLD3_VERSION)
    return rb


def patch_hold3_runtime() -> None:
    """기존 path-filter runtime patch 후 max_holding_days만 1~3으로 강제한다."""
    global _PATCHED_HOLD3, _ORIGINAL_GENETIC_FINALIZE, _ORIGINAL_RULEBOOK_FROM_DICT
    if _PATCHED_HOLD3:
        return
    path_filter.patch_path_filter_runtime()
    stage2 = importlib.import_module("scripts.research.run_stage2")
    genetic = importlib.import_module("engine.learning.genetic")

    genetic.PARAM_RANGES["max_holding_days"] = MAX_HOLDING_RANGE
    genetic._INT_PARAMS.add("max_holding_days")

    _ORIGINAL_GENETIC_FINALIZE = genetic._finalize_rulebook_genes

    def finalize_with_hold3(rb: Rulebook) -> Rulebook:
        out = _ORIGINAL_GENETIC_FINALIZE(rb)
        return clamp_max_holding_days(out)

    genetic._finalize_rulebook_genes = finalize_with_hold3

    _ORIGINAL_RULEBOOK_FROM_DICT = Rulebook.from_dict.__func__

    def from_dict_hold3(cls: type[Rulebook], payload: dict[str, Any]) -> Rulebook:
        rb = _ORIGINAL_RULEBOOK_FROM_DICT(cls, dict(payload))
        return clamp_max_holding_days(rb)

    Rulebook.from_dict = classmethod(from_dict_hold3)  # type: ignore[method-assign]

    original_prepare = stage2.prepare_ticker_context

    def prepare_with_hold3(ticker: str) -> dict[str, Any]:
        ctx = original_prepare(ticker)
        if "base_rulebook" in ctx:
            clamp_max_holding_days(ctx["base_rulebook"])
        return ctx

    stage2.prepare_ticker_context = prepare_with_hold3
    _PATCHED_HOLD3 = True


def write_hold3_manifest(out_dir: Path, summary: dict[str, Any] | None = None) -> None:
    payload = {
        "runner": "scripts/research/run_stage2_path_filter_hold3.py",
        "hold3_version": HOLD3_VERSION,
        "base_runner": "scripts/research/run_stage2_path_filter.py",
        "only_change_from_path_filter_v1": "max_holding_days GA range forced to 1..3 and clamped in finalize/from_dict/base_rulebook",
        "max_holding_days_range": MAX_HOLDING_RANGE,
        "path_filter_version": path_filter.PATH_FILTER_VERSION,
        "fitness_version": path_filter.FITNESS_VERSION,
        "fitness_objective": "mean(pnl_pct / max(1, holding_days)) per trade",
        "stage2_flow": "Original scripts/research/run_stage2.py flow is reused; engine and _calc_fitness_swing are not modified.",
        "path_features_unchanged": [
            "daily_rets_pct",
            "up_days5",
            "down_days5",
            "recent_turn_down",
            "days_since_high5",
            "close_pos5",
            "pullback_from_high5_pct",
            "single_up_day5_pct",
            "fade_after_surge_score",
        ],
        "lookahead": {
            "allowed": "D-5..D-1 OHLCV and indicators available on signal day D-1",
            "forbidden": ["D-day High", "D-day Low", "D-day Close", "future promotion/performance columns"],
        },
        "summary": summary or {},
    }
    (out_dir / "path_filter_hold3_manifest.json").write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2 path-filter runner with max_holding_days forced to 1..3")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed-base", type=int, default=None)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--fitness-cache", action="store_true")
    parser.add_argument("--no-fitness-cache", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    patch_hold3_runtime()
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
    write_hold3_manifest(out_dir, summary)
    print(json.dumps(_json_safe({"path_filter_hold3_manifest": str(out_dir / "path_filter_hold3_manifest.json"), "summary": summary}), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
