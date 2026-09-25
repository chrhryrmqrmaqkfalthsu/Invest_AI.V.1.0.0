"""Historical pullback replay dataset builder.

현재의 "첫 BUY 이후 N일 지난 눌림"을 과거의 같은 상태와 비교하기 위해
룰북을 과거 OHLCV에 다시 적용한다.

산출 단위:
- first BUY 발생일
- first BUY 이후 D일째 snapshot
- snapshot의 가격/ratio 유지율/반등품질 Q
- 이후 10거래일 안 target/stop 먼저 도달 여부

주의:
- market/sector context는 현재 값을 고정해 재생한다. 과거 macro context의 완전 replay가 아니라
  signal-shape replay다.
- 실제 주문, live state, positions 파일은 수정하지 않는다.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from engine.core.indicators import calc_indicators
from engine.live.elite_shadow_report import build_elite_shadow_report
from engine.live.elite_shadow_trader import _event_flags, _load_rulebook_for_candidate, _news_context
from engine.live.elite_strategy_sim import strategy_sim_payload
from engine.market.context import get_market_context
from engine.strategies.evaluator import evaluate_signal, get_dynamic_exit_params
from engine.strategies.rulebook import Rulebook

OUTPUT_DIR = Path("data/_system/research/pullback_replay")
CACHE_TTL_SEC = 300.0
_REPLAY_OHLCV_YEARS = 5
_cache: dict[str, tuple[dict[str, Any], float]] = {}
_ohlcv_cache: dict[str, tuple[pd.DataFrame, float]] = {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return default if math.isnan(out) else out
    except Exception:
        return default


def _round(value: Any, digits: int = 3) -> float | None:
    try:
        v = float(value)
        return None if math.isnan(v) else round(v, digits)
    except Exception:
        return None


def _date_text(value: Any) -> str:
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d")
        return str(value)[:10]
    except Exception:
        return ""


def _pctile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(float(v)))
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    pos = (len(vals) - 1) * max(0.0, min(1.0, q))
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return float(vals[lo])
    return float(vals[lo] * (hi - pos) + vals[hi] * (pos - lo))


def _candidate_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or f"{candidate.get('stage')}:{candidate.get('ticker')}:{candidate.get('rulebook_hash_short')}")


def _candidate_index() -> dict[str, dict[str, Any]]:
    report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
    out: dict[str, dict[str, Any]] = {}
    for c in report.get("candidates") or []:
        key = _candidate_key(c)
        ticker = str(c.get("ticker") or "").upper().strip()
        stage = str(c.get("stage") or "").lower().strip()
        short = str(c.get("rulebook_hash_short") or "")[:12]
        out[key] = c
        if ticker:
            out.setdefault(ticker, c)
            out.setdefault(f"{stage}:{ticker}", c)
            if short:
                out.setdefault(f"{stage}:{ticker}:{short}", c)
    return out


def _find_candidate(position_or_query: dict[str, Any]) -> dict[str, Any] | None:
    idx = _candidate_index()
    cid = str(position_or_query.get("candidate_id") or "")
    if cid and cid in idx:
        return idx[cid]
    ticker = str(position_or_query.get("ticker") or "").upper().strip()
    stage = str(position_or_query.get("stage") or "").lower().strip()
    short = str(position_or_query.get("rulebook_hash_short") or "")[:12]
    for key in (f"{stage}:{ticker}:{short}", f"{stage}:{ticker}", ticker):
        if key and key in idx:
            return idx[key]
    return None


def _load_replay_ohlcv(ticker: str) -> pd.DataFrame | None:
    """Replay 전용 장기 OHLCV 로더.

    기존 live 로더는 1년만 로드하므로 현재와 유사한 D+10~D+20 눌림 표본이 너무 부족하다.
    여기서는 adapter 5년 history를 우선 사용하고 실패 시 yfinance 5y로 fallback한다.
    """
    ticker = ticker.upper().strip()
    now = time.time()
    cached = _ohlcv_cache.get(ticker)
    if cached and now - cached[1] < 1800.0:
        return cached[0]
    try:
        from engine.adapters.factory import get_adapter

        df = get_adapter(ticker).load_history(years=_REPLAY_OHLCV_YEARS)
    except Exception:
        try:
            df = yf.Ticker(ticker).history(period=f"{_REPLAY_OHLCV_YEARS}y", interval="1d", auto_adjust=False)
        except Exception:
            return None
    if df is None or df.empty or len(df) < 180:
        return None
    try:
        df = calc_indicators(df.copy())
    except Exception:
        return None
    _ohlcv_cache[ticker] = (df, now)
    return df


def _context_values(ctx: Any, rb: Rulebook) -> tuple[float, float, float]:
    if ctx is None:
        return 50.0, 50.0, 18.0
    try:
        market_score = float(getattr(ctx, "score", 50.0))
        sector_strength = getattr(ctx, "sector_strength", {}) or {}
        sector_score = float(sector_strength.get(rb.sector_name, 50.0))
        vix_level = float(getattr(ctx, "vix_level", 18.0))
        return market_score, sector_score, vix_level
    except Exception:
        return 50.0, 50.0, 18.0


def _price_at(df: pd.DataFrame, idx: int, col: str = "Close") -> float:
    try:
        return _num(df[col].iloc[idx], 0.0)
    except Exception:
        return 0.0


def _row_date(df: pd.DataFrame, idx: int) -> str:
    try:
        if "Date" in df.columns:
            return _date_text(df["Date"].iloc[idx])
        if "date" in df.columns:
            return _date_text(df["date"].iloc[idx])
        return _date_text(df.index[idx])
    except Exception:
        return ""


def _quality_label(score: float) -> str:
    if score >= 75:
        return "STRONG_REBOUND"
    if score >= 60:
        return "HEALTHY_REBOUND"
    if score >= 45:
        return "WEAK_REBOUND"
    return "FAILED_REBOUND"


def _historical_rebound_quality(df: pd.DataFrame, idx: int, current_price: float) -> dict[str, Any]:
    if idx < 7:
        return {"ok": False, "score": None, "label": "INSUFFICIENT_WINDOW"}
    w = df.iloc[idx - 7 : idx + 1]
    opens = [_num(x) for x in w.get("Open", []).tolist()]
    highs = [_num(x) for x in w.get("High", []).tolist()]
    lows = [_num(x) for x in w.get("Low", []).tolist()]
    closes = [_num(x) for x in w.get("Close", []).tolist()]
    volumes = [_num(x) for x in w.get("Volume", []).tolist()]
    if len(closes) < 8 or min(closes[-5:]) <= 0 or min(lows[-5:]) <= 0:
        return {"ok": False, "score": None, "label": "BAD_WINDOW"}
    current = current_price if current_price > 0 else closes[-1]
    c1, c2, c3 = closes[-1], closes[-2], closes[-3]
    l1, l2, l3 = lows[-1], lows[-2], lows[-3]
    h1, h2 = highs[-1], highs[-2]
    o1 = opens[-1] if opens else c1
    recent_low_5 = min(lows[-5:])
    prior_low_3 = min(lows[-5:-2])
    ma3 = sum(closes[-3:]) / 3.0
    ma5 = sum(closes[-5:]) / 5.0
    bounce = (current / recent_low_5 - 1.0) * 100.0 if recent_low_5 > 0 else 0.0
    close_pos = (c1 - l1) / max(h1 - l1, 0.0001) if h1 > 0 and l1 > 0 else 0.0
    vol_base = [v for v in volumes[-6:-1] if v > 0]
    vol_ratio = volumes[-1] / (sum(vol_base) / len(vol_base)) if volumes and volumes[-1] > 0 and vol_base else None
    up_day = c1 >= o1
    higher_close = bool(c1 > c2 >= c3 or current > c1 >= c2)
    higher_low = bool(l1 > l2 >= l3 or min(lows[-2:]) > prior_low_3 * 1.005)
    ma3_ok = current >= ma3
    ma5_ok = current >= ma5
    prev_high = current > h2
    score = 0.0
    score += 22 if bounce >= 4 else 18 if bounce >= 3 else 14 if bounce >= 2 else 8 if bounce >= 1 else 0
    score += 16 if higher_close else 0
    score += 14 if higher_low else 0
    score += 8 if ma3_ok else 0
    score += 8 if ma5_ok else 0
    score += 10 if close_pos >= 0.70 else 6 if close_pos >= 0.50 else 0
    score += 10 if prev_high else 0
    score += 12 if (vol_ratio is not None and up_day and vol_ratio >= 1.20) else 8 if (vol_ratio is not None and up_day and vol_ratio >= 0.90) else 4 if up_day else 0
    penalties: list[str] = []
    if c1 < c2 < c3:
        score -= 15
        penalties.append("recent_3_close_down")
    if current < recent_low_5 * 1.005:
        score = min(score, 35)
        penalties.append("near_5d_low")
    score = round(max(0.0, min(100.0, score)), 2)
    return {
        "ok": True,
        "score": score,
        "label": _quality_label(score),
        "metrics": {
            "bounce_from_low_pct": bounce,
            "higher_close": higher_close,
            "higher_low": higher_low,
            "reclaim_ma3": ma3_ok,
            "reclaim_ma5": ma5_ok,
            "above_prev_high": prev_high,
            "volume_ratio": vol_ratio,
            "up_day": up_day,
            "close_position": close_pos,
        },
        "penalties": penalties,
    }


def _rebound_confirmed(signal_rows: list[dict[str, Any]], idx: int, current_price: float) -> bool:
    ok = [r for r in signal_rows[: idx + 1] if r.get("ok")]
    if len(ok) < 3:
        return False
    c1, c2, c3 = _num(ok[-1].get("close")), _num(ok[-2].get("close")), _num(ok[-3].get("close"))
    recent_low = min(c1, c2, c3)
    return (current_price > c1 and current_price > recent_low * 1.01) or (c1 > c2 and c2 >= c3)


def _future_outcome(df: pd.DataFrame, idx: int, close_price: float, target_price: float, stop_price: float, horizon_days: int) -> dict[str, Any]:
    end = min(len(df) - 1, idx + max(1, int(horizon_days)))
    if idx >= end or close_price <= 0:
        return {"label": "NO_FUTURE", "horizon_days": 0}
    max_high, min_low = close_price, close_price
    first_hit, first_hit_day = None, None
    for j in range(idx + 1, end + 1):
        high = _price_at(df, j, "High") or _price_at(df, j, "Close")
        low = _price_at(df, j, "Low") or _price_at(df, j, "Close")
        max_high, min_low = max(max_high, high), min(min_low, low)
        hit_target = target_price > 0 and high >= target_price
        hit_stop = stop_price > 0 and low <= stop_price
        if hit_target and hit_stop:
            first_hit, first_hit_day = "DROP_MORE", j - idx
            break
        if hit_target:
            first_hit, first_hit_day = "REBOUND_TO_TARGET", j - idx
            break
        if hit_stop:
            first_hit, first_hit_day = "DROP_MORE", j - idx
            break
    final_close = _price_at(df, end, "Close")
    return {
        "label": first_hit or "BASE_HOLD",
        "first_hit_day": first_hit_day,
        "horizon_days": end - idx,
        "final_return_pct": (final_close / close_price - 1.0) * 100.0 if final_close > 0 else 0.0,
        "mfe_pct": (max_high / close_price - 1.0) * 100.0,
        "mae_pct": (min_low / close_price - 1.0) * 100.0,
        "final_close": final_close,
    }


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"n": 0}
    labels = Counter(str(r.get("future_label") or "UNKNOWN") for r in records)
    q_scores = [_num(r.get("rebound_quality_score")) for r in records if r.get("rebound_quality_score") is not None]
    returns = [_num(r.get("future_final_return_pct")) for r in records]
    mfes = [_num(r.get("future_mfe_pct")) for r in records]
    maes = [_num(r.get("future_mae_pct")) for r in records]
    n = len(records)
    return {
        "n": n,
        "label_counts": dict(labels),
        "rebound_to_target_rate": round(labels.get("REBOUND_TO_TARGET", 0) / n * 100.0, 2),
        "drop_more_rate": round(labels.get("DROP_MORE", 0) / n * 100.0, 2),
        "base_hold_rate": round(labels.get("BASE_HOLD", 0) / n * 100.0, 2),
        "avg_future_return_pct": _round(sum(returns) / len(returns), 3),
        "median_future_return_pct": _round(_pctile(returns, 0.5), 3),
        "avg_mfe_pct": _round(sum(mfes) / len(mfes), 3),
        "avg_mae_pct": _round(sum(maes) / len(maes), 3),
        "avg_rebound_quality_score": _round(sum(q_scores) / len(q_scores), 2) if q_scores else None,
    }


def build_candidate_replay_dataset(candidate: dict[str, Any], *, max_offset_days: int = 20, horizon_days: int = 10, min_history_days: int = 80) -> dict[str, Any]:
    ticker = str(candidate.get("ticker") or "").upper().strip()
    rb_dict = _load_rulebook_for_candidate(candidate)
    if not ticker or not rb_dict:
        return {"ok": False, "reason": "candidate_or_rulebook_missing", "candidate": candidate}
    rb_dict = dict(rb_dict)
    rb_dict["ticker"] = ticker
    rb = Rulebook.from_dict(rb_dict)
    df = _load_replay_ohlcv(ticker)
    if df is None or len(df) < min_history_days + horizon_days + 5:
        return {"ok": False, "reason": "ohlcv_insufficient", "ticker": ticker, "rows": len(df) if df is not None else 0}
    try:
        ctx = get_market_context()
    except Exception:
        ctx = None
    market_score, sector_score, vix_level = _context_values(ctx, rb)
    event_flags = _event_flags(ctx)
    start = max(60, min_history_days)
    end = len(df) - horizon_days - 1
    signal_rows: list[dict[str, Any]] = []
    for idx in range(len(df)):
        date = _row_date(df, idx)
        close = _price_at(df, idx, "Close")
        if idx < start or idx > end:
            signal_rows.append({"ok": False, "idx": idx, "date": date, "close": close, "should_buy": False, "reason": "outside_replay_window"})
            continue
        try:
            news_sentiment, topic = _news_context(ticker, rb, date)
            res = evaluate_signal(
                rb=rb,
                df=df.iloc[: idx + 1].copy(),
                market_score=market_score,
                sector_score=sector_score,
                vix_level=vix_level,
                news_sentiment=news_sentiment,
                event_flags=event_flags,
                topic_features=topic,
            )
            signal_rows.append(
                {
                    "ok": True,
                    "idx": idx,
                    "date": date,
                    "close": close,
                    "should_buy": bool(res.should_buy),
                    "score": float(res.score),
                    "raw_score": float(res.raw_score),
                    "threshold": float(res.threshold),
                    "ratio": float(res.score) / max(float(res.threshold), 0.0001),
                    "news_sentiment": news_sentiment,
                    "topic_count": len(topic),
                }
            )
        except Exception as exc:
            signal_rows.append({"ok": False, "idx": idx, "date": date, "close": close, "should_buy": False, "reason": f"eval:{type(exc).__name__}"})

    sl_atr, tp_atr, _ = get_dynamic_exit_params(rb, market_score=market_score, vix_level=vix_level)
    records: list[dict[str, Any]] = []
    first_idx: int | None = None
    first_price = 0.0
    first_ratio = 0.0
    for idx, row in enumerate(signal_rows):
        if not row.get("ok") or not row.get("should_buy"):
            first_idx, first_price, first_ratio = None, 0.0, 0.0
            continue
        if first_idx is None:
            first_idx = idx
            first_price = _num(row.get("close"))
            first_ratio = _num(row.get("ratio"))
        if first_idx is None or first_price <= 0 or first_ratio <= 0:
            continue
        day_after = idx - first_idx
        if day_after > max_offset_days:
            continue
        close = _num(row.get("close"))
        atr = max(_price_at(df, idx, "ATR"), close * 0.01)
        target_price = close + tp_atr * atr
        stop_price = max(0.01, close - sl_atr * atr)
        target_up = (target_price / close - 1.0) * 100.0 if close > 0 else 0.0
        stop_down = (close - stop_price) / close * 100.0 if close > 0 else 0.0
        rr = target_up / stop_down if stop_down > 0 else 0.0
        q = _historical_rebound_quality(df, idx, close)
        future = _future_outcome(df, idx, close, target_price, stop_price, horizon_days)
        records.append(
            {
                "ticker": ticker,
                "candidate_id": _candidate_key(candidate),
                "stage": candidate.get("stage"),
                "bucket": candidate.get("bucket"),
                "rulebook_hash_short": candidate.get("rulebook_hash_short"),
                "first_buy_date": signal_rows[first_idx].get("date"),
                "snapshot_date": row.get("date"),
                "day_after_buy": day_after,
                "first_buy_price": first_price,
                "snapshot_price": close,
                "price_vs_first_buy_pct": (close / first_price - 1.0) * 100.0,
                "score": row.get("score"),
                "threshold": row.get("threshold"),
                "ratio": row.get("ratio"),
                "ratio_retention": _num(row.get("ratio")) / first_ratio if first_ratio > 0 else None,
                "rebound_confirmed": _rebound_confirmed(signal_rows, idx, close),
                "rebound_quality_score": q.get("score"),
                "rebound_quality_label": q.get("label"),
                "rebound_quality_metrics": q.get("metrics"),
                "target_upside_pct": target_up,
                "stop_downside_pct": stop_down,
                "risk_reward_to_target": rr,
                "future_label": future.get("label"),
                "future_first_hit_day": future.get("first_hit_day"),
                "future_final_return_pct": future.get("final_return_pct"),
                "future_mfe_pct": future.get("mfe_pct"),
                "future_mae_pct": future.get("mae_pct"),
                "future_horizon_days": future.get("horizon_days"),
            }
        )
    return {
        "ok": True,
        "ticker": ticker,
        "candidate_id": _candidate_key(candidate),
        "stage": candidate.get("stage"),
        "bucket": candidate.get("bucket"),
        "rulebook_hash_short": candidate.get("rulebook_hash_short"),
        "context_mode": f"current_market_context_signal_shape_replay_{_REPLAY_OHLCV_YEARS}y_ohlcv",
        "row_count": len(signal_rows),
        "record_count": len(records),
        "max_offset_days": max_offset_days,
        "horizon_days": horizon_days,
        "summary": _summarize_records(records),
        "records": records,
    }


def _filter_records(records: list[dict[str, Any]], current: dict[str, Any], mode: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    depth = abs(_num(current.get("price_vs_first_buy_pct")))
    signed_depth = _num(current.get("price_vs_first_buy_pct"))
    day = _num(current.get("day_after_buy"))
    retention = _num(current.get("ratio_retention"))
    q = current.get("rebound_quality_score")
    qv = _num(q, -1.0) if q is not None else -1.0
    depth_band = max(2.0, depth * 0.25)
    relaxed_depth_band = max(3.0, depth * 0.35)
    day_band = 3
    retention_band = 0.25
    q_band = 25.0

    def price_match(r: dict[str, Any], relaxed: bool = False) -> bool:
        rv = _num(r.get("price_vs_first_buy_pct"))
        return abs(abs(rv) - depth) <= (relaxed_depth_band if relaxed else depth_band) and rv <= 1.0

    def day_match(r: dict[str, Any], relaxed: bool = False) -> bool:
        return abs(_num(r.get("day_after_buy")) - day) <= (5 if relaxed else day_band)

    def retention_match(r: dict[str, Any]) -> bool:
        return abs(_num(r.get("ratio_retention")) - retention) <= retention_band

    def q_match(r: dict[str, Any], relaxed: bool = False) -> bool:
        if qv < 0 or r.get("rebound_quality_score") is None:
            return True
        return abs(_num(r.get("rebound_quality_score")) - qv) <= (35.0 if relaxed else q_band)

    if mode == "depth_only":
        out = [r for r in records if price_match(r)]
    elif mode == "depth_day":
        out = [r for r in records if price_match(r) and day_match(r)]
    elif mode == "depth_day_q":
        out = [r for r in records if price_match(r) and day_match(r) and q_match(r)]
    elif mode == "state_strict":
        out = [r for r in records if price_match(r) and day_match(r) and q_match(r) and retention_match(r)]
    elif mode == "state_relaxed":
        out = [r for r in records if price_match(r, True) and day_match(r, True) and q_match(r, True)]
    else:
        out = []
    meta = {
        "mode": mode,
        "depth_band_pct": round(depth_band, 3),
        "relaxed_depth_band_pct": round(relaxed_depth_band, 3),
        "day_band": day_band,
        "q_band": q_band,
        "retention_band": retention_band,
        "current_signed_price_vs_first_buy_pct": signed_depth,
    }
    return out, meta


def _current_from_position(position: dict[str, Any]) -> dict[str, Any]:
    hist = position.get("signal_history") or {}
    forecast = position.get("pullback_forecast") or {}
    features = forecast.get("features") or {}
    rq = forecast.get("rebound_quality") or features.get("rebound_quality") or {}
    cons = int(_num(hist.get("consecutive_buy_days")))
    return {
        "ticker": position.get("ticker"),
        "candidate_id": position.get("candidate_id"),
        "stage": position.get("stage"),
        "bucket": position.get("bucket"),
        "gate": position.get("gate"),
        "first_buy_date": hist.get("first_buy_date"),
        "day_after_buy": max(0, cons - 1),
        "consecutive_buy_days": cons,
        "first_buy_price": hist.get("first_buy_price"),
        "current_price": position.get("last_price"),
        "price_vs_first_buy_pct": hist.get("proposed_vs_first_buy_pct"),
        "ratio_retention": hist.get("ratio_retention"),
        "rebound_confirmed": hist.get("rebound_confirmed"),
        "rebound_quality_score": rq.get("score"),
        "rebound_quality_label": rq.get("label"),
        "target_upside_pct": features.get("target_upside_pct"),
        "stop_downside_pct": features.get("stop_downside_pct"),
        "risk_reward_to_target": features.get("risk_reward_to_target"),
        "forecast_label": forecast.get("label"),
        "forecast_display": forecast.get("display"),
    }


def _diagnose(current: dict[str, Any], summaries: dict[str, Any]) -> list[str]:
    out: list[str] = []
    strict = summaries.get("state_strict", {})
    relaxed = summaries.get("state_relaxed", {})
    depth_q = summaries.get("depth_day_q", {})
    best = strict if _num(strict.get("n")) >= 5 else relaxed if _num(relaxed.get("n")) >= 5 else depth_q
    q = _num(current.get("rebound_quality_score"), -1.0)
    target = _num(best.get("rebound_to_target_rate"))
    drop = _num(best.get("drop_more_rate"))
    n = int(_num(best.get("n")))
    if n < 5:
        out.append("유사 replay 표본이 5개 미만이라 통계 신뢰도가 낮음")
    if q >= 75 and drop >= target + 10:
        out.append("현재 반등 품질은 강하지만 과거 유사상황은 하락/손절 우위")
    if q < 45 and target >= drop + 10:
        out.append("과거 유사상황은 반등 우위지만 현재 반등 품질이 약함")
    if target >= 50 and q >= 60:
        out.append("과거 replay와 현재 반등 품질이 모두 반등 쪽")
    if drop >= 45 and q < 60:
        out.append("과거 replay와 현재 반등 품질이 모두 위험 쪽")
    if not out:
        out.append("과거 replay와 현재 지표가 뚜렷하게 한쪽으로 정렬되지 않음")
    return out


def build_current_pullback_replay_report(*, force_refresh: bool = False, horizon_days: int = 10, max_offset_days: int = 20, persist: bool = True) -> dict[str, Any]:
    cache_key = f"current:{horizon_days}:{max_offset_days}:v2"
    now = time.time()
    if not force_refresh and cache_key in _cache:
        payload, ts = _cache[cache_key]
        if now - ts < CACHE_TTL_SEC:
            out = dict(payload)
            out["cache"] = {"hit": True, "age_seconds": round(now - ts, 3), "ttl_sec": CACHE_TTL_SEC}
            return out
    from engine.live.elite_pullback_forecast import attach_pullback_forecasts_to_strategy_payload

    payload = attach_pullback_forecasts_to_strategy_payload(strategy_sim_payload(recent_trade_limit=20))
    positions = [p for p in (payload.get("strategies", {}).get("pullback_only", {}).get("open_positions") or []) if isinstance(p, dict) and str(p.get("gate") or "") in {"BUY_PULLBACK_REENTRY", "WAIT_PULLBACK_CONFIRM"}]
    analyses: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    for pos in positions:
        current = _current_from_position(pos)
        candidate = _find_candidate(pos)
        if not candidate:
            analyses.append({"ok": False, "ticker": pos.get("ticker"), "reason": "candidate_not_found", "current": current})
            continue
        ds = build_candidate_replay_dataset(candidate, max_offset_days=max_offset_days, horizon_days=horizon_days)
        if not ds.get("ok"):
            analyses.append({"ok": False, "ticker": pos.get("ticker"), "reason": ds.get("reason"), "current": current, "dataset": ds})
            continue
        records = ds.get("records") or []
        all_records.extend(records)
        filters: dict[str, Any] = {}
        for mode in ("depth_only", "depth_day", "depth_day_q", "state_strict", "state_relaxed"):
            rows, meta = _filter_records(records, current, mode)
            filters[mode] = {"meta": meta, "summary": _summarize_records(rows), "sample": rows[-8:]}
        summaries = {k: v.get("summary", {}) for k, v in filters.items()}
        analyses.append(
            {
                "ok": True,
                "ticker": pos.get("ticker"),
                "current": current,
                "dataset_summary": {k: ds.get(k) for k in ["ticker", "record_count", "row_count", "context_mode", "horizon_days", "max_offset_days", "summary"]},
                "similarity": filters,
                "diagnosis": _diagnose(current, summaries),
            }
        )

    summary_rows = []
    for a in analyses:
        if not a.get("ok"):
            summary_rows.append({"ticker": a.get("ticker"), "ok": False, "reason": a.get("reason")})
            continue
        cur = a.get("current") or {}
        sims = a.get("similarity") or {}
        strict = (sims.get("state_strict") or {}).get("summary") or {}
        relaxed = (sims.get("state_relaxed") or {}).get("summary") or {}
        depth_q = (sims.get("depth_day_q") or {}).get("summary") or {}
        use_mode = "state_strict" if _num(strict.get("n")) >= 5 else "state_relaxed" if _num(relaxed.get("n")) >= 5 else "depth_day_q"
        use = strict if use_mode == "state_strict" else relaxed if use_mode == "state_relaxed" else depth_q
        summary_rows.append(
            {
                "ticker": a.get("ticker"),
                "ok": True,
                "forecast": cur.get("forecast_display"),
                "q": cur.get("rebound_quality_score"),
                "current_pullback_pct": _round(cur.get("price_vs_first_buy_pct"), 2),
                "current_day_after_buy": cur.get("day_after_buy"),
                "replay_match_mode": use_mode,
                "replay_n": use.get("n", 0),
                "replay_rebound_rate": use.get("rebound_to_target_rate"),
                "replay_drop_rate": use.get("drop_more_rate"),
                "replay_base_rate": use.get("base_hold_rate"),
                "replay_avg_return_pct": use.get("avg_future_return_pct"),
                "primary_diagnosis": (a.get("diagnosis") or [None])[0],
            }
        )
    result = {
        "ok": True,
        "_comment": "Historical first-BUY pullback replay. Uses 5y OHLCV and current market-context signal-shape replay. No broker/live state is modified.",
        "generated_at": _utc_now(),
        "horizon_days": horizon_days,
        "max_offset_days": max_offset_days,
        "position_count": len(positions),
        "summary_rows": summary_rows,
        "analyses": analyses,
    }
    if persist:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        snapshot_path = OUTPUT_DIR / f"current_pullback_replay_{stamp}.json"
        latest_path = OUTPUT_DIR / "current_pullback_replay_latest.json"
        records_path = OUTPUT_DIR / f"current_pullback_replay_records_{stamp}.jsonl"
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        tmp = snapshot_path.with_name(f".{snapshot_path.name}.tmp.{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, snapshot_path)
        tmp2 = latest_path.with_name(f".{latest_path.name}.tmp.{os.getpid()}")
        tmp2.write_text(text, encoding="utf-8")
        os.replace(tmp2, latest_path)
        with records_path.open("w", encoding="utf-8") as handle:
            for row in all_records:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        result["artifacts"] = {"snapshot": str(snapshot_path), "latest": str(latest_path), "records": str(records_path), "record_count": len(all_records)}
    _cache[cache_key] = (result, now)
    result["cache"] = {"hit": False, "age_seconds": 0.0, "ttl_sec": CACHE_TTL_SEC}
    return result
