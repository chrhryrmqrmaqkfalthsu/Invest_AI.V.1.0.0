#!/usr/bin/env python3
"""
Stage2 Path Filter Runner — 신호 직전 5일 경로 기반 진입 필터 연구 러너.

생성 목적: 2026-07-04, 원본 scripts/research/run_stage2.py의 Stage2 학습·검증 흐름은 그대로 두고,
개체가 should_buy=True를 낸 신호일 기준 D-5~D-1의 가격 경로를 분석해 "먹을 신호"와
"떨어질 신호"를 구분하는 진입 필터를 GA 유전자로 학습합니다.

원본 대비 차이:
- 유지: rolling 3분할 train, split별 독립 GA, population 100/generations 50/patience 15,
  전체 population 수집, rulebook hash 대표화, stress → train3 → train2 → train1 → oos early-cut,
  survivor gate, run_stage2.py의 출력 구조.
- 유지: daily-return fitness. 원본 _calc_fitness_swing은 수정하지 않고, runner 프로세스 내부에서
  backtest 결과 fitness만 거래별 일평균 수익률 중심으로 재계산합니다.
- 교체/확장: 기존 entry_quality의 prev5_ret 두 점 비율 필터는 사용하지 않습니다.
  대신 D-5~D-1 5개 봉 전체 경로에서 고점 위치, 상대 위치, 고점 대비 되돌림, 연속 상승/하락,
  최근 방향 전환, 급등 후 식음 패턴을 계산해 진입 gate로 씁니다.
- 유지: signal_age proxy와 dist_high20는 보조 필터로 유지합니다.

look-ahead 차단:
- 모든 경로 피처는 신호일 D-1까지 확정된 OHLCV만 사용합니다.
- T+1 open 진입 전 D-day High/Low/Close는 절대 사용하지 않습니다.

주의:
- research-only입니다. run_live/실거래/캐시갱신과 무관합니다.
- engine/, run_stage2.py, _calc_fitness_swing은 수정하지 않습니다.
- 동적 유전자는 이 파일 실행 프로세스 안에서만 monkey patch로 주입됩니다.
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
from statistics import mean, median
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from engine.strategies.rulebook import Rulebook

PATH_FILTER_VERSION = "path_filter_v1"
FITNESS_VERSION = "daily_return_v1_path_filter"

# Daily-return fitness stabilizers. Stage2 survivor gate still applies after training.
MIN_TRADES = 5
MIN_EXPECTANCY_PCT = 0.0
MIN_PROFIT_FACTOR = 1.0
MAX_MDD_PCT = -20.0

PATH_FILTER_GENE_RANGES: dict[str, tuple[float, float]] = {
    # 보조: 추세 proxy가 너무 오래 이어진 늦은 신호 차단. 30은 사실상 off.
    "path_filter_max_signal_age_days": (1, 30),
    # 보조: 최근 20일 고점에서 최소 N% 아래에 있어야 함. 0은 off.
    "path_filter_min_dist_high20_pct": (0.0, 20.0),
    # D-5~D-1 중 최고가가 며칠 전인지. 0=D-1, 4=D-5. 4는 off.
    "path_filter_max_days_since_high5": (0, 4),
    # D-1 종가의 5일 range 내 상대 위치. 0~1. 기본 0~1은 off.
    "path_filter_min_close_pos5": (0.0, 1.0),
    "path_filter_max_close_pos5": (0.0, 1.0),
    # 5일 고점 대비 D-1 종가 되돌림. 너무 작거나 너무 크면 차단 가능.
    "path_filter_min_pullback_high5_pct": (0.0, 15.0),
    "path_filter_max_pullback_high5_pct": (0.0, 30.0),
    # 5개 일일 수익률 중 상승/하락일수 제한.
    "path_filter_max_up_days5": (0, 5),
    "path_filter_max_down_days5": (0, 5),
    # 마지막 2일이 상승→하락 전환이면 차단할지 여부. 0=off, 1=block.
    "path_filter_block_recent_turn_down": (0, 1),
    # 초반 급등 후 마지막 2일 약세를 합친 fade score 제한. 50은 off.
    "path_filter_max_fade_after_surge_score": (0.0, 50.0),
    # 5일 내 단일 급등일 제한. 25는 대부분 off.
    "path_filter_max_single_up_day_pct": (0.0, 25.0),
}
PATH_FILTER_INT_GENES = {
    "path_filter_max_signal_age_days",
    "path_filter_max_days_since_high5",
    "path_filter_max_up_days5",
    "path_filter_max_down_days5",
    "path_filter_block_recent_turn_down",
}
PATH_FILTER_DEFAULTS: dict[str, float | int] = {
    "path_filter_max_signal_age_days": 30,
    "path_filter_min_dist_high20_pct": 0.0,
    "path_filter_max_days_since_high5": 4,
    "path_filter_min_close_pos5": 0.0,
    "path_filter_max_close_pos5": 1.0,
    "path_filter_min_pullback_high5_pct": 0.0,
    "path_filter_max_pullback_high5_pct": 30.0,
    "path_filter_max_up_days5": 5,
    "path_filter_max_down_days5": 5,
    "path_filter_block_recent_turn_down": 0,
    "path_filter_max_fade_after_surge_score": 50.0,
    "path_filter_max_single_up_day_pct": 25.0,
}

_ORIGINAL_RULEBOOK_TO_DICT = Rulebook.to_dict
_ORIGINAL_RULEBOOK_FROM_DICT = Rulebook.from_dict.__func__
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


def attach_path_filter_defaults(rb: Rulebook) -> Rulebook:
    for key, default in PATH_FILTER_DEFAULTS.items():
        if not hasattr(rb, key):
            setattr(rb, key, copy.deepcopy(default))
    setattr(rb, "path_filter_version", PATH_FILTER_VERSION)
    return rb


def path_filter_rulebook_dict(rb: Rulebook) -> dict[str, Any]:
    base = dict(_ORIGINAL_RULEBOOK_TO_DICT(rb))
    for key, default in PATH_FILTER_DEFAULTS.items():
        base[key] = getattr(rb, key, default)
    base["path_filter_version"] = getattr(rb, "path_filter_version", PATH_FILTER_VERSION)
    return base


def _patched_rulebook_to_dict(self: Rulebook) -> dict[str, Any]:
    return path_filter_rulebook_dict(self)


def _patched_rulebook_from_dict(cls: type[Rulebook], payload: dict[str, Any]) -> Rulebook:
    rb = _ORIGINAL_RULEBOOK_FROM_DICT(cls, dict(payload))
    attach_path_filter_defaults(rb)
    for key, default in PATH_FILTER_DEFAULTS.items():
        if key in payload:
            value = payload.get(key, default)
            if key in PATH_FILTER_INT_GENES:
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
    rb.path_filter_version = str(payload.get("path_filter_version") or PATH_FILTER_VERSION)
    return rb


def compute_path_filter_hash(rb_or_dict: Any) -> str:
    if isinstance(rb_or_dict, Rulebook):
        payload = path_filter_rulebook_dict(rb_or_dict)
    elif isinstance(rb_or_dict, Mapping):
        payload = dict(rb_or_dict)
    else:
        payload = _json_safe(rb_or_dict)
        if not isinstance(payload, Mapping):
            payload = {}
    payload = dict(payload)
    for key, default in PATH_FILTER_DEFAULTS.items():
        payload.setdefault(key, default)
    payload.setdefault("path_filter_version", PATH_FILTER_VERSION)
    for key in ["fitness", "win_rate", "avg_return_pct", "expectancy_pct", "max_drawdown_pct", "trade_count", "generated_at"]:
        payload.pop(key, None)
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _signal_age_proxy_days(df: pd.DataFrame) -> int:
    """D-1까지의 추세/모멘텀 proxy가 며칠째 이어지는지 계산한다.

    이 값은 개체 자신의 should_buy 첫 발생일이 아니라, Aligned_bull 또는 Close>=MA5 & MACD_hist>=0
    조건의 최근 연속일수다. 원래 entry_quality 보조 필터와 동일한 역할로만 유지한다.
    """
    if df is None or len(df) <= 0:
        return 0
    count = 0
    for _, row in df.tail(20).iloc[::-1].iterrows():
        close = _safe_float(row.get("Close"))
        ma5 = _safe_float(row.get("MA5"), close)
        macd_hist = _safe_float(row.get("MACD_hist"), 0.0)
        aligned = bool(row.get("Aligned_bull", 0))
        if aligned or (close >= ma5 and macd_hist >= 0.0):
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


def _path5_features(df: pd.DataFrame) -> dict[str, Any]:
    """신호일 D-1 기준 직전 5개 봉(D-5~D-1)의 전체 경로 피처를 계산한다.

    daily_rets는 D-6 종가를 기준으로 D-5, ..., D-1의 일별 수익률 5개를 만든다.
    High/Low/Close는 D-5~D-1까지만 사용한다.
    """
    if df is None or len(df) < 6:
        return {
            "available": False,
            "reason": "need at least 6 rows for D-6..D-1 path",
        }
    window = df.tail(5).copy()
    prev_close = _safe_float(df.iloc[-6].get("Close"), 0.0)
    closes = [_safe_float(v, 0.0) for v in window["Close"].tolist()]
    highs = [_safe_float(v, 0.0) for v in window["High"].tolist()]
    lows = [_safe_float(v, 0.0) for v in window["Low"].tolist()]
    rets: list[float] = []
    last = prev_close
    for close in closes:
        if last > 0:
            rets.append((close / last - 1.0) * 100.0)
        else:
            rets.append(0.0)
        last = close
    high5 = max(highs) if highs else 0.0
    low5 = min(lows) if lows else 0.0
    close_d1 = closes[-1] if closes else 0.0
    high_idx = int(max(range(len(highs)), key=lambda i: highs[i])) if highs else 4
    days_since_high = 4 - high_idx  # 0=D-1, 4=D-5
    denom = high5 - low5
    close_pos = (close_d1 - low5) / denom if denom > 0 else 0.5
    close_pos = max(0.0, min(1.0, close_pos))
    pullback = max(0.0, (high5 / close_d1 - 1.0) * 100.0) if close_d1 > 0 else 0.0
    up_days = int(sum(1 for r in rets if r > 0.0))
    down_days = int(sum(1 for r in rets if r < 0.0))
    recent_turn_down = bool(len(rets) >= 2 and rets[-2] > 0.0 and rets[-1] < 0.0)
    single_up_day = max(rets) if rets else 0.0
    first3_up = max(rets[:3]) if len(rets) >= 3 else max(rets or [0.0])
    last2_ret = (closes[-1] / closes[-3] - 1.0) * 100.0 if len(closes) >= 3 and closes[-3] > 0 else 0.0
    fade_after_surge_score = max(0.0, first3_up) + max(0.0, -last2_ret)
    cumulative_ret5 = (close_d1 / prev_close - 1.0) * 100.0 if prev_close > 0 else 0.0
    max_close_idx = int(max(range(len(closes)), key=lambda i: closes[i])) if closes else 4
    days_since_close_high = 4 - max_close_idx
    return {
        "available": True,
        "daily_rets_pct": rets,
        "cumulative_ret5_pct": cumulative_ret5,
        "up_days5": up_days,
        "down_days5": down_days,
        "recent_turn_down": int(recent_turn_down),
        "high5": high5,
        "low5": low5,
        "close_d1": close_d1,
        "days_since_high5": days_since_high,
        "days_since_close_high5": days_since_close_high,
        "close_pos5": close_pos,
        "pullback_from_high5_pct": pullback,
        "single_up_day5_pct": single_up_day,
        "first3_max_up_day_pct": first3_up,
        "last2_ret_pct": last2_ret,
        "fade_after_surge_score": fade_after_surge_score,
    }


def _block_signal_with_path_filter(rb: Rulebook, df: pd.DataFrame) -> tuple[bool, dict[str, Any]]:
    attach_path_filter_defaults(rb)
    signal_age = _signal_age_proxy_days(df)
    dist_high20 = _dist_from_high20_pct(df)
    path = _path5_features(df)
    max_age = int(round(_safe_float(getattr(rb, "path_filter_max_signal_age_days", 30), 30.0)))
    min_dist_high20 = _safe_float(getattr(rb, "path_filter_min_dist_high20_pct", 0.0), 0.0)
    max_days_since_high = int(round(_safe_float(getattr(rb, "path_filter_max_days_since_high5", 4), 4.0)))
    min_close_pos = _safe_float(getattr(rb, "path_filter_min_close_pos5", 0.0), 0.0)
    max_close_pos = _safe_float(getattr(rb, "path_filter_max_close_pos5", 1.0), 1.0)
    min_pullback = _safe_float(getattr(rb, "path_filter_min_pullback_high5_pct", 0.0), 0.0)
    max_pullback = _safe_float(getattr(rb, "path_filter_max_pullback_high5_pct", 30.0), 30.0)
    max_up_days = int(round(_safe_float(getattr(rb, "path_filter_max_up_days5", 5), 5.0)))
    max_down_days = int(round(_safe_float(getattr(rb, "path_filter_max_down_days5", 5), 5.0)))
    block_turn_down = int(round(_safe_float(getattr(rb, "path_filter_block_recent_turn_down", 0), 0.0)))
    max_fade = _safe_float(getattr(rb, "path_filter_max_fade_after_surge_score", 50.0), 50.0)
    max_single_up = _safe_float(getattr(rb, "path_filter_max_single_up_day_pct", 25.0), 25.0)
    failed: list[str] = []
    if signal_age > max_age:
        failed.append(f"signal_age {signal_age} > max {max_age}")
    if dist_high20 < min_dist_high20:
        failed.append(f"dist_high20 {dist_high20:.2f}% < min {min_dist_high20:.2f}%")
    if not bool(path.get("available")):
        failed.append(str(path.get("reason") or "path5_unavailable"))
    else:
        if int(path["days_since_high5"]) > max_days_since_high:
            failed.append(f"days_since_high5 {path['days_since_high5']} > max {max_days_since_high}")
        if float(path["close_pos5"]) < min_close_pos:
            failed.append(f"close_pos5 {path['close_pos5']:.3f} < min {min_close_pos:.3f}")
        if float(path["close_pos5"]) > max_close_pos:
            failed.append(f"close_pos5 {path['close_pos5']:.3f} > max {max_close_pos:.3f}")
        if float(path["pullback_from_high5_pct"]) < min_pullback:
            failed.append(f"pullback5 {path['pullback_from_high5_pct']:.2f}% < min {min_pullback:.2f}%")
        if float(path["pullback_from_high5_pct"]) > max_pullback:
            failed.append(f"pullback5 {path['pullback_from_high5_pct']:.2f}% > max {max_pullback:.2f}%")
        if int(path["up_days5"]) > max_up_days:
            failed.append(f"up_days5 {path['up_days5']} > max {max_up_days}")
        if int(path["down_days5"]) > max_down_days:
            failed.append(f"down_days5 {path['down_days5']} > max {max_down_days}")
        if block_turn_down >= 1 and int(path["recent_turn_down"]) == 1:
            failed.append("recent_turn_down blocked")
        if float(path["fade_after_surge_score"]) > max_fade:
            failed.append(f"fade_after_surge {path['fade_after_surge_score']:.2f} > max {max_fade:.2f}")
        if float(path["single_up_day5_pct"]) > max_single_up:
            failed.append(f"single_up_day5 {path['single_up_day5_pct']:.2f}% > max {max_single_up:.2f}%")
    info = {
        "path_filter_version": PATH_FILTER_VERSION,
        "signal_age_days": signal_age,
        "dist_high20_pct": dist_high20,
        "failed_reasons": failed,
        "genes": {
            "max_signal_age_days": max_age,
            "min_dist_high20_pct": min_dist_high20,
            "max_days_since_high5": max_days_since_high,
            "min_close_pos5": min_close_pos,
            "max_close_pos5": max_close_pos,
            "min_pullback_high5_pct": min_pullback,
            "max_pullback_high5_pct": max_pullback,
            "max_up_days5": max_up_days,
            "max_down_days5": max_down_days,
            "block_recent_turn_down": block_turn_down,
            "max_fade_after_surge_score": max_fade,
            "max_single_up_day_pct": max_single_up,
        },
        "path5": path,
    }
    return bool(failed), info


def _trade_daily_returns(trades: list[dict[str, Any]]) -> list[float]:
    daily: list[float] = []
    for trade in trades:
        pnl = _safe_float(trade.get("pnl_pct"), 0.0)
        holding_days = max(1.0, _safe_float(trade.get("holding_days"), 1.0))
        daily.append(pnl / holding_days)
    return daily


def _calc_fitness_daily_return_result(result: Any) -> tuple[float, dict[str, Any]]:
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
    reward = avg_daily * 100.0
    trade_penalty = max(0, MIN_TRADES - trade_count) * 30.0
    expectancy_penalty = max(0.0, MIN_EXPECTANCY_PCT - expectancy) * 20.0
    pf_penalty = max(0.0, MIN_PROFIT_FACTOR - profit_factor) * 50.0
    mdd_penalty = max(0.0, abs(max_drawdown) - abs(MAX_MDD_PCT)) * 2.0 if max_drawdown < MAX_MDD_PCT else 0.0
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


def patch_path_filter_runtime() -> None:
    """runner 프로세스 내부에서만 Stage2/GA/evaluator/backtest를 patch한다."""
    global _PATCHED, _ORIGINAL_STAGE2_BACKTEST
    if _PATCHED:
        return
    stage2 = importlib.import_module("scripts.research.run_stage2")
    genetic = importlib.import_module("engine.learning.genetic")
    exec_mode = importlib.import_module("engine.learning.execution_mode_backtest")

    genetic.PARAM_RANGES.update(PATH_FILTER_GENE_RANGES)
    genetic._INT_PARAMS.update(PATH_FILTER_INT_GENES)
    Rulebook.to_dict = _patched_rulebook_to_dict  # type: ignore[method-assign]
    Rulebook.from_dict = classmethod(_patched_rulebook_from_dict)  # type: ignore[method-assign]
    stage2.compute_rulebook_hash = compute_path_filter_hash
    genetic.compute_rulebook_hash = compute_path_filter_hash

    original_prepare = stage2.prepare_ticker_context

    def prepare_with_path_filter(ticker: str) -> dict[str, Any]:
        ctx = original_prepare(ticker)
        if "base_rulebook" in ctx:
            attach_path_filter_defaults(ctx["base_rulebook"])
        return ctx

    stage2.prepare_ticker_context = prepare_with_path_filter

    original_evaluate_signal = exec_mode.evaluate_signal

    def evaluate_signal_with_path_filter(rb: Rulebook, df: pd.DataFrame, *args: Any, **kwargs: Any) -> Any:
        sig = original_evaluate_signal(rb, df, *args, **kwargs)
        if not bool(getattr(sig, "should_buy", False)):
            return sig
        blocked, info = _block_signal_with_path_filter(rb, df)
        try:
            sig.components = dict(getattr(sig, "components", {}) or {})
            sig.components["path_filter"] = info
            sig.reasons = list(getattr(sig, "reasons", []) or [])
        except Exception:
            pass
        if blocked:
            try:
                sig.should_buy = False
                sig.reasons.append("경로필터차단(" + "; ".join(info["failed_reasons"]) + ")")
            except Exception:
                pass
        else:
            try:
                pf = info.get("path5", {})
                sig.reasons.append(
                    "경로필터통과(age={age}, hAgo={hago}, pos5={pos:.2f}, pull={pull:.2f}%, up={up}, down={down})".format(
                        age=info.get("signal_age_days"),
                        hago=pf.get("days_since_high5"),
                        pos=float(pf.get("close_pos5", 0.0)),
                        pull=float(pf.get("pullback_from_high5_pct", 0.0)),
                        up=pf.get("up_days5"),
                        down=pf.get("down_days5"),
                    )
                )
            except Exception:
                pass
        return sig

    exec_mode.evaluate_signal = evaluate_signal_with_path_filter

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


def write_path_filter_manifest(out_dir: Path, summary: dict[str, Any] | None = None) -> None:
    payload = {
        "runner": "scripts/research/run_stage2_path_filter.py",
        "path_filter_version": PATH_FILTER_VERSION,
        "fitness_version": FITNESS_VERSION,
        "stage2_flow": "Original scripts/research/run_stage2.py flow is reused; engine and _calc_fitness_swing are not modified.",
        "fitness_objective": "mean(pnl_pct / max(1, holding_days)) per trade",
        "gene_ranges": PATH_FILTER_GENE_RANGES,
        "gene_defaults": PATH_FILTER_DEFAULTS,
        "integer_genes": sorted(PATH_FILTER_INT_GENES),
        "path_features": {
            "signal_age_proxy_days": "consecutive proxy days from Aligned_bull or Close>=MA5 & MACD_hist>=0; not own-rule first signal age",
            "dist_high20_pct": "trailing 20d High / D-1 Close - 1",
            "daily_rets_pct": "five daily returns for D-5..D-1 using D-6 Close as first base",
            "days_since_high5": "0 when D-1 has the 5d high, 4 when D-5 has the 5d high",
            "close_pos5": "range position of D-1 Close inside D-5..D-1 Low/High",
            "pullback_from_high5_pct": "5d High / D-1 Close - 1",
            "recent_turn_down": "last daily return <0 after previous daily return >0",
            "fade_after_surge_score": "max(first3 daily up) + max(0, -last2 cumulative return)",
        },
        "lookahead": {
            "allowed": "D-5..D-1 OHLCV and indicators available on signal day D-1",
            "forbidden": ["D-day High", "D-day Low", "D-day Close", "future promotion/performance columns"],
        },
        "summary": summary or {},
    }
    (out_dir / "path_filter_manifest.json").write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage2 runner with 5-day path filter genes and daily-return fitness")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed-base", type=int, default=None)
    parser.add_argument("--parallel", action="store_true")
    parser.add_argument("--fitness-cache", action="store_true")
    parser.add_argument("--no-fitness-cache", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    patch_path_filter_runtime()
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
    write_path_filter_manifest(out_dir, summary)
    print(json.dumps(_json_safe({"path_filter_manifest": str(out_dir / "path_filter_manifest.json"), "summary": summary}), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
