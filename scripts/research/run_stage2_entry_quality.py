#!/usr/bin/env python3
"""
Stage2 Entry Quality Runner — 스윙 진입 타이밍 개선 연구용 러너.

생성 목적: 2026-07-03, 기존 scripts/research/run_stage2.py의 검증된 Stage2 학습·검증 흐름은
그대로 계승하고, 스윙 진입 타이밍을 개선하기 위한 진입 품질 유전자 3개만 신규로 추가합니다.

원본 run_stage2.py와의 차이점:
- 유지: rolling 3분할 train, split별 독립 GA, population 100/generations 50/patience 15,
  전체 population 수집, rulebook hash 대표화, stress → train3 → train2 → train1 → oos early-cut,
  survivor gate, _calc_fitness_swing 기반 swing fitness.
- 추가: 신규 러너 프로세스 내부에서만 Rulebook에 동적 진입 품질 유전자 3개를 붙입니다.
  1) entry_quality_max_signal_age_days: 원신호가 오래 끌린 뒤의 늦은 진입을 차단. 1~30으로 열어 OFF 선택지를 보장합니다.
  2) entry_quality_min_dist_high20_pct: D-1 종가가 최근 20일 고점에서 최소 몇 % 떨어져야 하는지.
  3) entry_quality_max_prev5_ret_pct: D-5~D-1 누적 상승률이 과열 상한을 넘으면 차단.
- 변경 금지 준수: engine/, run_stage2.py, _calc_fitness_swing은 수정하지 않습니다.

주의사항:
- research-only입니다. run_live/실거래/캐시갱신과 무관합니다.
- 동적 유전자는 이 파일 실행 프로세스 안에서만 monkey patch로 주입됩니다.
- 추가 피처는 D-1까지 확정된 값만 사용합니다. D일 고가/저가/종가는 사용하지 않습니다.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from engine.strategies.rulebook import Rulebook

ENTRY_QUALITY_GENE_RANGES: dict[str, tuple[float, float]] = {
    # integer gene: 1~30일. 상한 30/default 30은 원본과 거의 같은 OFF 선택지를 보장한다.
    "entry_quality_max_signal_age_days": (1, 30),
    # D-1 종가가 최근 20일 고점에서 최소 N% 아래여야 한다. 0이면 사실상 비활성.
    "entry_quality_min_dist_high20_pct": (0.0, 20.0),
    # D-5~D-1 누적 상승률이 N%를 넘으면 과열로 보고 차단한다.
    "entry_quality_max_prev5_ret_pct": (-10.0, 25.0),
}
ENTRY_QUALITY_INT_GENES = {"entry_quality_max_signal_age_days"}
ENTRY_QUALITY_DEFAULTS: dict[str, float | int] = {
    "entry_quality_max_signal_age_days": 30,
    "entry_quality_min_dist_high20_pct": 0.0,
    "entry_quality_max_prev5_ret_pct": 25.0,
}
ENTRY_QUALITY_VERSION = "entry_quality_v1"

_ORIGINAL_RULEBOOK_TO_DICT = Rulebook.to_dict
_ORIGINAL_RULEBOOK_FROM_DICT = Rulebook.from_dict.__func__
_PATCHED = False


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "__dict__"):
        return {k: _json_safe(v) for k, v in vars(value).items() if not str(k).startswith("_")}
    return str(value)


def attach_entry_quality_defaults(rb: Rulebook) -> Rulebook:
    """Rulebook dataclass를 수정하지 않고 신규 유전자를 동적 attribute로 붙인다."""
    for key, default in ENTRY_QUALITY_DEFAULTS.items():
        if not hasattr(rb, key):
            setattr(rb, key, copy.deepcopy(default))
    setattr(rb, "entry_quality_version", ENTRY_QUALITY_VERSION)
    return rb


def entry_quality_rulebook_dict(rb: Rulebook) -> dict[str, Any]:
    base = dict(_ORIGINAL_RULEBOOK_TO_DICT(rb))
    for key, default in ENTRY_QUALITY_DEFAULTS.items():
        base[key] = getattr(rb, key, default)
    base["entry_quality_version"] = getattr(rb, "entry_quality_version", ENTRY_QUALITY_VERSION)
    return base


def _patched_rulebook_to_dict(self: Rulebook) -> dict[str, Any]:
    return entry_quality_rulebook_dict(self)


def _patched_rulebook_from_dict(cls: type[Rulebook], payload: dict[str, Any]) -> Rulebook:
    rb = _ORIGINAL_RULEBOOK_FROM_DICT(cls, dict(payload))
    attach_entry_quality_defaults(rb)
    for key, default in ENTRY_QUALITY_DEFAULTS.items():
        if key in payload:
            value = payload.get(key, default)
            if key in ENTRY_QUALITY_INT_GENES:
                try:
                    value = int(round(float(value)))
                except Exception:
                    value = int(default)
            else:
                try:
                    value = float(value)
                except Exception:
                    value = float(default)
            setattr(rb, key, value)
    rb.entry_quality_version = str(payload.get("entry_quality_version") or ENTRY_QUALITY_VERSION)
    return rb


def compute_entry_quality_hash(rb_or_dict: Any) -> str:
    """원본 compute_rulebook_hash가 dataclass.asdict를 우선하기 때문에 동적 유전자를 포함한 해시를 별도 계산한다."""
    if isinstance(rb_or_dict, Rulebook):
        payload = entry_quality_rulebook_dict(rb_or_dict)
    elif isinstance(rb_or_dict, Mapping):
        payload = dict(rb_or_dict)
    else:
        payload = _json_safe(rb_or_dict)
        if not isinstance(payload, Mapping):
            payload = {}
    payload = dict(payload)
    for key, default in ENTRY_QUALITY_DEFAULTS.items():
        payload.setdefault(key, default)
    payload.setdefault("entry_quality_version", ENTRY_QUALITY_VERSION)
    for key in ["fitness", "win_rate", "avg_return_pct", "expectancy_pct", "max_drawdown_pct", "trade_count", "generated_at"]:
        payload.pop(key, None)
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _signal_age_proxy_days(df: pd.DataFrame) -> int:
    """D-1까지의 추세/모멘텀 원신호가 며칠째 이어지는지 proxy로 계산한다.

    원신호 평가 함수를 과거 n일마다 재귀 호출하면 GA 비용이 폭증하므로, D-1까지 확정된
    Aligned_bull / MACD_hist / Close>=MA5 조합으로 연속 신호 경과일수를 계산한다.
    """
    if df is None or len(df) <= 0:
        return 0
    count = 0
    tail = df.tail(20)
    for _, row in tail.iloc[::-1].iterrows():
        close = _safe_float(row.get("Close"))
        ma5 = _safe_float(row.get("MA5"), close)
        macd_hist = _safe_float(row.get("MACD_hist"), 0.0)
        aligned = bool(row.get("Aligned_bull", 0))
        trend_like_signal = aligned or (close >= ma5 and macd_hist >= 0.0)
        if trend_like_signal:
            count += 1
        else:
            break
    return count


def _dist_from_high20_pct(df: pd.DataFrame) -> float:
    if df is None or len(df) <= 0:
        return 0.0
    close = _safe_float(df.iloc[-1].get("Close"), 0.0)
    if close <= 0:
        return 0.0
    high20 = _safe_float(df["High"].tail(20).max(), close)
    return max(0.0, (high20 / close - 1.0) * 100.0)


def _prev5_ret_pct(df: pd.DataFrame) -> float:
    if df is None or len(df) < 6:
        return 0.0
    c0 = _safe_float(df.iloc[-6].get("Close"), 0.0)
    c1 = _safe_float(df.iloc[-1].get("Close"), 0.0)
    if c0 <= 0:
        return 0.0
    return (c1 / c0 - 1.0) * 100.0


def _block_signal_with_entry_quality(rb: Rulebook, df: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    attach_entry_quality_defaults(rb)
    signal_age = _signal_age_proxy_days(df)
    dist_high20 = _dist_from_high20_pct(df)
    prev5_ret = _prev5_ret_pct(df)
    max_age = int(round(_safe_float(getattr(rb, "entry_quality_max_signal_age_days", 30), 30.0)))
    min_dist_high20 = _safe_float(getattr(rb, "entry_quality_min_dist_high20_pct", 0.0), 0.0)
    max_prev5_ret = _safe_float(getattr(rb, "entry_quality_max_prev5_ret_pct", 25.0), 25.0)
    failed: list[str] = []
    if signal_age > max_age:
        failed.append(f"signal_age {signal_age} > max {max_age}")
    if dist_high20 < min_dist_high20:
        failed.append(f"dist_high20 {dist_high20:.2f}% < min {min_dist_high20:.2f}%")
    if prev5_ret > max_prev5_ret:
        failed.append(f"prev5_ret {prev5_ret:.2f}% > max {max_prev5_ret:.2f}%")
    return bool(failed), {
        "signal_age_days": signal_age,
        "dist_high20_pct": dist_high20,
        "prev5_ret_pct": prev5_ret,
        "max_signal_age_days": max_age,
        "min_dist_high20_pct": min_dist_high20,
        "max_prev5_ret_pct": max_prev5_ret,
        "failed_reasons": failed,
    }


def patch_entry_quality_runtime() -> None:
    """신규 러너 프로세스 내부에서만 Stage2/GA/evaluator 연결부를 patch한다."""
    global _PATCHED
    if _PATCHED:
        return

    stage2 = importlib.import_module("scripts.research.run_stage2")
    genetic = importlib.import_module("engine.learning.genetic")
    exec_mode = importlib.import_module("engine.learning.execution_mode_backtest")

    # 1) GA numeric gene space에 유전자 3개를 append한다. engine 파일은 수정하지 않는다.
    genetic.PARAM_RANGES.update(ENTRY_QUALITY_GENE_RANGES)
    genetic._INT_PARAMS.update(ENTRY_QUALITY_INT_GENES)

    # 2) Rulebook 직렬화/역직렬화가 동적 유전자를 보존하도록 runner 프로세스 안에서만 patch한다.
    Rulebook.to_dict = _patched_rulebook_to_dict  # type: ignore[method-assign]
    Rulebook.from_dict = classmethod(_patched_rulebook_from_dict)  # type: ignore[method-assign]

    # 3) Stage2가 쓰는 hash 함수가 동적 유전자를 포함하도록 patch한다.
    stage2.compute_rulebook_hash = compute_entry_quality_hash
    genetic.compute_rulebook_hash = compute_entry_quality_hash

    # 4) prepare_ticker_context가 반환하는 base_rulebook에 동적 유전자를 붙인다.
    original_prepare = stage2.prepare_ticker_context

    def prepare_with_entry_quality(ticker: str) -> dict[str, Any]:
        ctx = original_prepare(ticker)
        if "base_rulebook" in ctx:
            attach_entry_quality_defaults(ctx["base_rulebook"])
        return ctx

    stage2.prepare_ticker_context = prepare_with_entry_quality

    # 5) run_backtest_execution_mode 내부 evaluate_signal에 entry quality gate를 덧씌운다.
    original_evaluate_signal = exec_mode.evaluate_signal

    def evaluate_signal_with_entry_quality(rb: Rulebook, df: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        sig = original_evaluate_signal(rb, df, *args, **kwargs)
        if not bool(getattr(sig, "should_buy", False)):
            return sig
        blocked, info = _block_signal_with_entry_quality(rb, df)
        try:
            sig.components = dict(getattr(sig, "components", {}) or {})
            sig.components["entry_quality"] = info
            sig.reasons = list(getattr(sig, "reasons", []) or [])
        except Exception:
            pass
        if blocked:
            try:
                sig.should_buy = False
                sig.reasons.append("진입품질차단(" + "; ".join(info["failed_reasons"]) + ")")
            except Exception:
                pass
        else:
            try:
                sig.reasons.append(
                    "진입품질통과(age={signal_age_days}, distH20={dist_high20_pct:.2f}%, prev5={prev5_ret_pct:.2f}%)".format(**info)
                )
            except Exception:
                pass
        return sig

    exec_mode.evaluate_signal = evaluate_signal_with_entry_quality
    _PATCHED = True


def write_entry_quality_manifest(out_dir: Path, summary: dict[str, Any] | None = None) -> None:
    payload = {
        "runner": "scripts/research/run_stage2_entry_quality.py",
        "entry_quality_version": ENTRY_QUALITY_VERSION,
        "gene_ranges": ENTRY_QUALITY_GENE_RANGES,
        "gene_defaults": ENTRY_QUALITY_DEFAULTS,
        "integer_genes": sorted(ENTRY_QUALITY_INT_GENES),
        "fitness_unchanged": "Uses original run_stage2.py flow and execution_mode_backtest swing fitness path; _calc_fitness_swing is not modified.",
        "lookahead": {
            "signal_age_proxy_days": "computed from df up to current signal day D-1 before T+1 open entry",
            "dist_high20_pct": "uses trailing 20d High and D-1 Close only",
            "prev5_ret_pct": "uses D-6 Close to D-1 Close only",
            "forbidden": ["D-day High", "D-day Low", "D-day Close", "future promotion/performance columns"],
        },
        "summary": summary or {},
    }
    (out_dir / "entry_quality_manifest.json").write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2 swing runner with three entry-quality genes")
    parser.add_argument("--ticker", required=True, help="Ticker symbol, e.g. FIX")
    parser.add_argument("--out-dir", default=None, help="Output directory. Default follows run_stage2.py naming")
    parser.add_argument("--seed-base", type=int, default=None)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--fitness-cache", action="store_true")
    parser.add_argument("--no-fitness-cache", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    patch_entry_quality_runtime()
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
    write_entry_quality_manifest(out_dir, summary)
    print(json.dumps(_json_safe({"entry_quality_manifest": str(out_dir / "entry_quality_manifest.json"), "summary": summary}), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
