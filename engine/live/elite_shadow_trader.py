"""Elite shadow trader.

무엇을 하는 파일인가:
- elite-shadow 정예 후보를 대상으로 실제 broker 주문 없이 가상 매매를 기록한다.
- 후보 룰북의 BUY 신호가 뜨면 샀다고 가정하고 shadow position을 연다.
- 룰북의 손절/목표/트레일링/본전보호/최대보유일/sell_omen 조건이 맞으면 팔았다고 가정하고 거래 기록을 남긴다.
- 상태 파일: data/_system/elite_shadow_state.json
- 거래 파일: data/_system/elite_shadow_trades.jsonl

주의:
- broker.submit_order, live positions.json, parameters.json은 절대 수정하지 않는다.
- 이 모듈은 시스템 내부 모의거래 ledger 전용이다.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from engine.core.feature_lag import DEFAULT_LAG_DAYS, DEFAULT_MAX_AGE_DAYS, lookup_lagged_daily_dict
from engine.core.indicators import calc_indicators
from engine.live.elite_shadow_entry_quality import assess_shadow_entry_quality
from engine.live.elite_shadow_report import ROOT as ELITE_ROOT
from engine.live.elite_shadow_report import build_elite_shadow_report
from engine.live.news_alerts import lookup_live_sell_omen_score
from engine.market.context import get_market_context
from engine.market.ticker_sentiment import load_csv as load_ticker_sentiment
from engine.strategies.evaluator import evaluate_signal, get_dynamic_exit_params
from engine.strategies.news_features import precompute_topic_features
from engine.strategies.rulebook import Rulebook

log = logging.getLogger("elite_shadow_trader")

STATE_PATH = Path("data/_system/elite_shadow_state.json")
TRADES_PATH = Path("data/_system/elite_shadow_trades.jsonl")
LOCK_PATH = Path("data/_system/elite_shadow_tick.lock")
TRADE_LOCK_PATH = Path("data/_system/elite_shadow_trades.lock")
DEFAULT_NOTIONAL_USD = 5000.0
OHLCV_CACHE_TTL_SEC = 600.0
PRICE_CACHE_TTL_SEC = 30.0

_ohlcv_cache: dict[str, tuple[pd.DataFrame, float]] = {}
_price_cache: dict[str, tuple[float, float]] = {}
_sentiment_cache: dict[str, tuple[dict, float]] = {}
_topic_cache: dict[tuple[str, int, int, str], tuple[dict, float]] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ShadowStateCorruptionError(RuntimeError):
    """Shadow state 파일 손상 시 빈 state로 fail-open 하지 않기 위한 예외."""


def _blank_state() -> dict[str, Any]:
    return {
        "_comment": "Elite shadow trader state. This is virtual-only; no broker orders are placed.",
        "version": 1,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "open_positions": {},
        "closed_count": 0,
        "events": [],
        "last_tick": None,
    }


def _mark_corrupt_state(exc: Exception) -> Path:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    corrupt_path = STATE_PATH.with_name(f"{STATE_PATH.name}.corrupt.{suffix}")
    try:
        shutil.copy2(STATE_PATH, corrupt_path)
    except Exception:
        pass
    log.critical("Shadow state JSON parse failed; copied corrupt state to %s and aborting tick: %s", corrupt_path, exc)
    return corrupt_path


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("open_positions", {})
                data.setdefault("closed_count", 0)
                data.setdefault("events", [])
                return data
            raise ValueError("state root is not an object")
        except Exception as exc:
            corrupt_path = _mark_corrupt_state(exc)
            raise ShadowStateCorruptionError(f"Shadow state is corrupt; backup={corrupt_path}") from exc
    return _blank_state()


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    events = state.get("events") or []
    if isinstance(events, list) and len(events) > 300:
        state["events"] = events[-300:]
    _atomic_write_json(STATE_PATH, state)


def _acquire_file_lock(path: Path, ttl_sec: float = 900.0) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if path.exists():
        try:
            age = now - path.stat().st_mtime
            if age > ttl_sec:
                path.unlink()
            else:
                return False
        except Exception:
            return False
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"pid={os.getpid()} ts={utc_now()}\n".encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_file_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def append_trade(row: dict[str, Any]) -> None:
    if not _acquire_file_lock(TRADE_LOCK_PATH, ttl_sec=300.0):
        raise RuntimeError(f"trade append lock busy: {TRADE_LOCK_PATH}")
    try:
        TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRADES_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    finally:
        _release_file_lock(TRADE_LOCK_PATH)


def load_recent_trades(limit: int = 200) -> list[dict[str, Any]]:
    if not TRADES_PATH.exists():
        return []
    lines = [line for line in TRADES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit):]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _acquire_lock(ttl_sec: float = 900.0) -> bool:
    return _acquire_file_lock(LOCK_PATH, ttl_sec=ttl_sec)


def _release_lock() -> None:
    _release_file_lock(LOCK_PATH)


def _load_rulebook_for_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    stage = str(candidate.get("stage") or "")
    if stage == "stage2":
        source_file = candidate.get("source_file")
        source_row_index = _safe_int(candidate.get("source_row_index"))
        path = ELITE_ROOT / str(source_file) if source_file else None
        if not path or not path.exists() or source_row_index <= 0:
            return None
        with path.open("r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle, 1):
                if idx != source_row_index:
                    continue
                try:
                    row = json.loads(line)
                    rb = row.get("rulebook") or {}
                    return rb if isinstance(rb, dict) else None
                except Exception:
                    return None
    if stage == "stage3":
        path = Path(str(candidate.get("source_file") or ""))
        target_hash = str(candidate.get("rulebook_hash") or "")
        if not path.exists() or not target_hash:
            return None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if target_hash not in line[:500]:
                    # hash key is near the top of final_rulebooks rows; still parse fallback below if needed
                    pass
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("rulebook_hash") or "") == target_hash:
                    rb = row.get("rulebook") or {}
                    return rb if isinstance(rb, dict) else None
    return None


def _load_ohlcv(ticker: str) -> pd.DataFrame | None:
    ticker = ticker.upper().strip()
    now = time.time()
    cached = _ohlcv_cache.get(ticker)
    if cached is not None:
        df, ts = cached
        if now - ts < OHLCV_CACHE_TTL_SEC:
            return df
    try:
        from engine.adapters.factory import get_adapter

        adapter = get_adapter(ticker)
        df = adapter.load_history(years=1)
    except Exception as exc:
        log.warning("%s adapter OHLCV failed, yfinance fallback: %s", ticker, exc)
        try:
            df = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=False)
        except Exception as exc2:
            log.warning("%s yfinance OHLCV failed: %s", ticker, exc2)
            return None
    if df is None or df.empty or len(df) < 60:
        return None
    try:
        df = calc_indicators(df.copy())
    except Exception as exc:
        log.warning("%s indicator calc failed: %s", ticker, exc)
        return None
    _ohlcv_cache[ticker] = (df, now)
    return df


def _latest_price(ticker: str, df: pd.DataFrame | None = None) -> float | None:
    ticker = ticker.upper().strip()
    now = time.time()
    cached = _price_cache.get(ticker)
    if cached is not None:
        price, ts = cached
        if now - ts < PRICE_CACHE_TTL_SEC:
            return price
    price = None
    try:
        hist = yf.Ticker(ticker).history(period="1d", interval="1m", prepost=True)
        if hist is not None and not hist.empty:
            close = hist["Close"].dropna()
            if not close.empty:
                price = _safe_float(close.iloc[-1], 0.0)
    except Exception:
        price = None
    if (price is None or price <= 0.0) and df is not None and not df.empty:
        try:
            price = _safe_float(df["Close"].iloc[-1], 0.0)
        except Exception:
            price = None
    if price is None or price <= 0.0:
        return None
    _price_cache[ticker] = (price, now)
    return price


def _signal_date(df: pd.DataFrame) -> Any:
    try:
        if "Date" in df.columns:
            return df["Date"].iloc[-1]
        if "date" in df.columns:
            return df["date"].iloc[-1]
        return df.index[-1]
    except Exception:
        return None


def _load_sentiment(ticker: str) -> dict:
    now = time.time()
    cached = _sentiment_cache.get(ticker)
    if cached is not None:
        data, ts = cached
        if now - ts < OHLCV_CACHE_TTL_SEC:
            return data
    try:
        data = load_ticker_sentiment(ticker) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _sentiment_cache[ticker] = (data, now)
    return data


def _topic_features(ticker: str, sentiment: dict, rb: Rulebook) -> dict:
    if not sentiment:
        return {}
    try:
        window = int(getattr(rb, "news_zscore_window", 60) or 60)
    except Exception:
        window = 60
    window = max(1, min(window, 252))
    try:
        latest_key = max(str(k)[:10] for k in sentiment.keys())
    except Exception:
        latest_key = ""
    key = (ticker, window, len(sentiment), latest_key)
    now = time.time()
    cached = _topic_cache.get(key)
    if cached is not None:
        features, ts = cached
        if now - ts < OHLCV_CACHE_TTL_SEC:
            return features
    try:
        features = precompute_topic_features(sentiment, window)
        if not isinstance(features, dict):
            features = {}
    except Exception:
        features = {}
    _topic_cache[key] = (features, now)
    return features


def _news_context(ticker: str, rb: Rulebook, signal_date: Any) -> tuple[float, dict]:
    sentiment = _load_sentiment(ticker)
    if not sentiment:
        return 0.0, {}
    row = lookup_lagged_daily_dict(sentiment, signal_date, lag_days=DEFAULT_LAG_DAYS, max_age_days=DEFAULT_MAX_AGE_DAYS)
    try:
        news_sentiment = float(row.get("sentiment_avg", 0.0)) if row else 0.0
    except Exception:
        news_sentiment = 0.0
    topic_map = _topic_features(ticker, sentiment, rb)
    topic = lookup_lagged_daily_dict(topic_map, signal_date, lag_days=DEFAULT_LAG_DAYS, max_age_days=DEFAULT_MAX_AGE_DAYS) if topic_map else {}
    if not isinstance(topic, dict):
        topic = {}
    return news_sentiment, topic


def _event_flags(ctx: Any) -> dict[str, int] | None:
    try:
        active = getattr(ctx, "active_events", {}) or {}
        return {
            "has_war": int("전쟁" in active),
            "has_rate_hike": int("금리정책_인상" in active),
            "has_rate_cut": int("금리정책_인하" in active),
            "has_geopolitical": int("지정학_긴장" in active),
            "has_tariff": int("관세" in active),
            "has_export_ban": int("수출규제" in active),
            "has_earnings_shock": int("실적쇼크" in active),
            "has_oil_surge": int("유가급등" in active),
            "has_banking_crisis": int("은행위기" in active),
            "has_inflation": int("인플레이션" in active),
            "has_fed_statement": int("연준발언" in active),
        }
    except Exception:
        return None


def evaluate_candidate(candidate: dict[str, Any], ctx: Any = None) -> dict[str, Any]:
    ticker = str(candidate.get("ticker") or "").upper().strip()
    rb_dict = _load_rulebook_for_candidate(candidate)
    if not ticker or not rb_dict:
        return {"ok": False, "ticker": ticker, "reason": "rulebook_missing"}
    rb_dict = dict(rb_dict)
    rb_dict["ticker"] = ticker
    rb = Rulebook.from_dict(rb_dict)
    df = _load_ohlcv(ticker)
    if df is None or len(df) < 60:
        return {"ok": False, "ticker": ticker, "reason": "ohlcv_missing"}
    price = _latest_price(ticker, df)
    if not price:
        return {"ok": False, "ticker": ticker, "reason": "price_missing"}
    if ctx is None:
        try:
            ctx = get_market_context()
        except Exception:
            ctx = None
    if ctx is not None:
        market_score = float(getattr(ctx, "score", 50.0))
        sector_strength = getattr(ctx, "sector_strength", {}) or {}
        sector_score = float(sector_strength.get(rb.sector_name, 50.0))
        vix_level = float(getattr(ctx, "vix_level", 18.0))
    else:
        market_score, sector_score, vix_level = 50.0, 50.0, 18.0
    signal_date = _signal_date(df)
    news_sentiment, topic = _news_context(ticker, rb, signal_date)
    res = evaluate_signal(
        rb=rb,
        df=df,
        market_score=market_score,
        sector_score=sector_score,
        vix_level=vix_level,
        news_sentiment=news_sentiment,
        event_flags=_event_flags(ctx),
        topic_features=topic,
    )
    atr = _safe_float(df["ATR"].iloc[-1], 0.0) if "ATR" in df.columns else 0.0
    sl_atr, tp_atr, tr_atr = get_dynamic_exit_params(rb, market_score=market_score, vix_level=vix_level)
    score = float(res.score)
    threshold = float(res.threshold)
    ratio = score / max(threshold, 0.0001)
    reasons = list(res.reasons)
    components = dict(res.components)
    entry_quality = assess_shadow_entry_quality(
        candidate=candidate,
        df=df,
        price=price,
        score=score,
        threshold=threshold,
        ratio=ratio,
        reasons=reasons,
        components=components,
    )
    return {
        "ok": True,
        "ticker": ticker,
        "price": price,
        "atr": atr,
        "should_buy": bool(res.should_buy),
        "score": score,
        "raw_score": float(res.raw_score),
        "threshold": threshold,
        "ratio": ratio,
        "reasons": reasons,
        "components": components,
        "entry_quality": entry_quality,
        "market_score": market_score,
        "sector_score": sector_score,
        "vix_level": vix_level,
        "news_sentiment": news_sentiment,
        "topic_count": len(topic),
        "rulebook": rb,
        "rulebook_dict": rb_dict,
        "exit_atr": {"stop": sl_atr, "target": tp_atr, "trailing": tr_atr},
    }


def _position_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or f"{candidate.get('stage')}:{candidate.get('ticker')}:{candidate.get('rulebook_hash_short')}")


def _open_position(candidate: dict[str, Any], ev: dict[str, Any], state: dict[str, Any], *, notional: float) -> dict[str, Any]:
    rb: Rulebook = ev["rulebook"]
    price = float(ev["price"])
    atr = max(float(ev.get("atr") or 0.0), price * 0.01)
    sl_atr = float(ev["exit_atr"]["stop"])
    tp_atr = float(ev["exit_atr"]["target"])
    tr_atr = float(ev["exit_atr"]["trailing"])
    stop_price = max(0.01, price - sl_atr * atr)
    target_price = price + tp_atr * atr
    trailing_distance = tr_atr * atr
    quality = ev.get("entry_quality") or {}
    pos = {
        "position_id": f"shadow-{int(time.time())}-{_position_key(candidate).replace(':','-')}",
        "candidate_id": _position_key(candidate),
        "ticker": ev["ticker"],
        "stage": candidate.get("stage"),
        "bucket": candidate.get("bucket"),
        "rulebook_hash": candidate.get("rulebook_hash"),
        "rulebook_hash_short": candidate.get("rulebook_hash_short"),
        "opened_at": utc_now(),
        "entry_price": price,
        "shares": notional / price,
        "notional": notional,
        "entry_score": ev["score"],
        "entry_threshold": ev["threshold"],
        "entry_ratio": ev["ratio"],
        "entry_reasons": ev["reasons"][:8],
        "entry_quality": quality,
        "entry_quality_score": quality.get("score"),
        "entry_quality_label": quality.get("label"),
        "entry_quality_size_factor": quality.get("size_factor"),
        "atr_at_entry": atr,
        "target_price": target_price,
        "stop_price": stop_price,
        "trailing_distance": trailing_distance,
        "trailing_stop": price - trailing_distance,
        "highest_price": price,
        "lowest_price": price,
        "max_profit_pct": 0.0,
        "max_loss_pct": 0.0,
        "last_price": price,
        "last_seen_at": utc_now(),
        "exit_strategy": rb.exit_strategy,
        "max_holding_days": int(rb.max_holding_days),
        "breakeven_enabled": bool(rb.breakeven_enabled),
        "breakeven_trigger_profit_pct": float(rb.breakeven_trigger_profit_pct),
        "breakeven_floor_profit_pct": float(rb.breakeven_floor_profit_pct),
        "sell_omen_enabled": bool(rb.sell_omen_enabled),
        "sell_omen_threshold": float(rb.sell_omen_threshold),
        "rulebook_snapshot": {
            "exit_strategy": rb.exit_strategy,
            "max_holding_days": int(rb.max_holding_days),
            "take_profit_atr": float(rb.take_profit_atr),
            "stop_loss_atr": float(rb.stop_loss_atr),
            "trailing_atr": float(rb.trailing_atr),
            "trailing_activation_profit_pct": float(rb.trailing_activation_profit_pct),
            "sell_omen_enabled": bool(rb.sell_omen_enabled),
            "market_adjustment_strength": float(rb.market_adjustment_strength),
        },
    }
    state["open_positions"][_position_key(candidate)] = pos
    qtxt = ""
    if quality:
        qtxt = f" q={quality.get('score')} {quality.get('primary_reason')} size={quality.get('size_factor')}"
    _event(state, "OPEN", pos["ticker"], f"BUY score={ev['score']:.2f}/{ev['threshold']:.2f} price={price:.2f}{qtxt}", pos)
    return pos


def _holding_days(opened_at: str) -> int:
    try:
        start = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc).date() - start.date()).days)
    except Exception:
        return 0


def _sell_omen_hit(pos: dict[str, Any]) -> tuple[bool, float | None, str]:
    if not bool(pos.get("sell_omen_enabled")):
        return False, None, "disabled"
    try:
        row = lookup_live_sell_omen_score(str(pos.get("ticker") or ""), asof=datetime.now(timezone.utc))
    except Exception:
        row = None
    if not isinstance(row, dict):
        return False, None, "missing"
    score = row.get("score") if "score" in row else row.get("sell_omen_score")
    score_f = _safe_float(score, -1.0)
    if score_f < 0.0:
        return False, None, "missing"
    threshold = _safe_float(pos.get("sell_omen_threshold"), 1.0)
    return score_f >= threshold, score_f, "live_sell_omen"


def _maybe_close_position(pos_key: str, pos: dict[str, Any], price: float, state: dict[str, Any]) -> dict[str, Any] | None:
    entry = _safe_float(pos.get("entry_price"), 0.0)
    if entry <= 0.0 or price <= 0.0:
        return None
    highest = max(_safe_float(pos.get("highest_price"), entry), price)
    lowest = min(_safe_float(pos.get("lowest_price"), entry), price)
    pnl_pct = (price / entry - 1.0) * 100.0
    max_profit_pct = max(_safe_float(pos.get("max_profit_pct", 0.0)), (highest / entry - 1.0) * 100.0)
    max_loss_pct = min(_safe_float(pos.get("max_loss_pct", 0.0)), (lowest / entry - 1.0) * 100.0)
    pos.update({
        "highest_price": highest,
        "lowest_price": lowest,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "last_price": price,
        "last_seen_at": utc_now(),
        "unrealized_pnl_pct": pnl_pct,
        "unrealized_pnl_usd": (_safe_float(pos.get("shares")) * (price - entry)),
        "holding_days": _holding_days(str(pos.get("opened_at") or "")),
    })
    reason = None
    exit_strategy = str(pos.get("exit_strategy") or "")
    if price <= _safe_float(pos.get("stop_price"), 0.0):
        reason = "stop_loss"
    if reason is None and exit_strategy in {"fixed", "hybrid"} and price >= _safe_float(pos.get("target_price"), 10**12):
        reason = "take_profit"
    if reason is None and bool(pos.get("breakeven_enabled")):
        trigger = _safe_float(pos.get("breakeven_trigger_profit_pct"), 0.0)
        floor = _safe_float(pos.get("breakeven_floor_profit_pct"), 0.0)
        breakeven_stop = entry * (1.0 + floor / 100.0)
        pos["breakeven_stop"] = breakeven_stop
        if max_profit_pct >= trigger and price <= breakeven_stop:
            reason = "breakeven_stop"
    if reason is None and exit_strategy in {"trailing", "hybrid"}:
        activation = _safe_float((pos.get("rulebook_snapshot") or {}).get("trailing_activation_profit_pct"), 0.0)
        if max_profit_pct >= activation:
            trailing_stop = max(_safe_float(pos.get("trailing_stop"), 0.0), highest - _safe_float(pos.get("trailing_distance"), 0.0))
            pos["trailing_stop"] = trailing_stop
            if price <= trailing_stop:
                reason = "trailing_stop"
    if reason is None:
        hit, score, source = _sell_omen_hit(pos)
        if score is not None:
            pos["sell_omen_score"] = score
            pos["sell_omen_source"] = source
        if hit:
            reason = "sell_omen"
    if reason is None and _holding_days(str(pos.get("opened_at") or "")) >= _safe_int(pos.get("max_holding_days"), 9999):
        reason = "time_out"
    if reason is None:
        return None

    shares = _safe_float(pos.get("shares"), 0.0)
    pnl_usd = shares * (price - entry)
    trade = {
        "_comment": "Elite shadow virtual closed trade. No broker order was placed.",
        "position_id": pos.get("position_id"),
        "candidate_id": pos_key,
        "ticker": pos.get("ticker"),
        "stage": pos.get("stage"),
        "bucket": pos.get("bucket"),
        "rulebook_hash_short": pos.get("rulebook_hash_short"),
        "opened_at": pos.get("opened_at"),
        "closed_at": utc_now(),
        "entry_price": entry,
        "exit_price": price,
        "shares": shares,
        "notional": _safe_float(pos.get("notional"), 0.0),
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "exit_reason": reason,
        "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "entry_score": pos.get("entry_score"),
        "entry_threshold": pos.get("entry_threshold"),
        "entry_ratio": pos.get("entry_ratio"),
        "entry_quality_score": pos.get("entry_quality_score"),
        "entry_quality_label": pos.get("entry_quality_label"),
        "entry_quality_primary_reason": (pos.get("entry_quality") or {}).get("primary_reason"),
        "last_sell_omen_score": pos.get("sell_omen_score"),
    }
    append_trade(trade)
    state["open_positions"].pop(pos_key, None)
    state["closed_count"] = _safe_int(state.get("closed_count"), 0) + 1
    _event(state, "CLOSE", str(pos.get("ticker") or ""), f"{reason} pnl={pnl_pct:+.2f}% price={price:.2f}", trade)
    return trade


def _event(state: dict[str, Any], event_type: str, ticker: str, message: str, payload: dict[str, Any] | None = None) -> None:
    events = state.setdefault("events", [])
    events.append({
        "time": utc_now(),
        "event": event_type,
        "ticker": ticker,
        "message": message,
        "candidate_id": (payload or {}).get("candidate_id"),
        "position_id": (payload or {}).get("position_id"),
    })


def _summarize_state(state: dict[str, Any]) -> dict[str, Any]:
    open_positions = list((state.get("open_positions") or {}).values())
    trades = load_recent_trades(limit=10000)
    pnls = [_safe_float(t.get("pnl_pct"), 0.0) for t in trades]
    usd = [_safe_float(t.get("pnl_usd"), 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    return {
        "open_count": len(open_positions),
        "closed_count": len(trades),
        "win_rate": (len(wins) / len(pnls) * 100.0) if pnls else 0.0,
        "avg_pnl_pct": (sum(pnls) / len(pnls)) if pnls else 0.0,
        "total_pnl_usd": sum(usd),
        "open_unrealized_usd": sum(_safe_float(p.get("unrealized_pnl_usd"), 0.0) for p in open_positions),
        "open_unrealized_pct_avg": (sum(_safe_float(p.get("unrealized_pnl_pct"), 0.0) for p in open_positions) / len(open_positions)) if open_positions else 0.0,
    }


def run_shadow_tick(*, max_candidates: int = 93, notional: float = DEFAULT_NOTIONAL_USD, force: bool = False) -> dict[str, Any]:
    # force=True도 공통 writer lock은 우회하지 않는다. API 수동 tick과 daemon tick의 lost update를 막기 위함이다.
    if not _acquire_lock():
        return {"ok": False, "reason": "shadow_state_lock_busy"}
    started = time.time()
    try:
        state = load_state()
    except ShadowStateCorruptionError as exc:
        _release_lock()
        return {"ok": False, "reason": "state_corrupt", "error": str(exc)}
    opened = 0
    closed = 0
    evaluated = 0
    quality_filtered = 0
    quality_reduced = 0
    quality_skip_counts: Counter[str] = Counter()
    quality_skip_samples: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        try:
            ctx = get_market_context()
        except Exception:
            ctx = None
        report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
        candidates = (report.get("candidates") or [])[:max_candidates]

        # 먼저 열린 포지션 청산 조건을 평가한다.
        for pos_key, pos in list((state.get("open_positions") or {}).items()):
            ticker = str(pos.get("ticker") or "").upper()
            df = _load_ohlcv(ticker)
            price = _latest_price(ticker, df)
            if not price:
                errors.append({"ticker": ticker, "candidate_id": pos_key, "reason": "open_price_missing"})
                continue
            trade = _maybe_close_position(pos_key, pos, price, state)
            if trade is not None:
                closed += 1

        # 후보별 BUY 신호를 평가해 새 shadow position을 연다.
        open_keys = set((state.get("open_positions") or {}).keys())
        open_tickers = {str(p.get("ticker") or "").upper() for p in (state.get("open_positions") or {}).values()}
        for candidate in candidates:
            key = _position_key(candidate)
            ticker = str(candidate.get("ticker") or "").upper()
            if not key or not ticker or key in open_keys or ticker in open_tickers:
                continue
            ev = evaluate_candidate(candidate, ctx=ctx)
            evaluated += 1
            if not ev.get("ok"):
                errors.append({"ticker": ticker, "candidate_id": key, "reason": ev.get("reason")})
                continue
            if bool(ev.get("should_buy")):
                quality = ev.get("entry_quality") or {}
                if not bool(quality.get("allow", True)):
                    reason = str(quality.get("primary_reason") or "quality_filtered")
                    quality_filtered += 1
                    quality_skip_counts[reason] += 1
                    if len(quality_skip_samples) < 20:
                        quality_skip_samples.append({
                            "ticker": ticker,
                            "candidate_id": key,
                            "reason": reason,
                            "quality_score": quality.get("score"),
                            "quality_label": quality.get("label"),
                            "entry_ratio": ev.get("ratio"),
                            "entry_reasons": ev.get("reasons", [])[:4],
                            "metrics": {k: (quality.get("metrics") or {}).get(k) for k in ["ret_1d_pct", "ret_5d_pct", "dist_ma5_pct", "dist_ma20_pct", "bounce_low5_pct", "dist_high5_pct", "volume_ratio20", "event_score", "event_heavy", "overheat", "high_vol", "low_price"]},
                        })
                    continue
                size_factor = max(0.1, min(1.0, _safe_float(quality.get("size_factor"), 1.0)))
                actual_notional = max(100.0, notional * size_factor)
                if size_factor < 0.999:
                    quality_reduced += 1
                _open_position(candidate, ev, state, notional=actual_notional)
                opened += 1
                open_keys.add(key)
                open_tickers.add(ticker)

        state["last_tick"] = {
            "time": utc_now(),
            "elapsed_sec": round(time.time() - started, 3),
            "evaluated": evaluated,
            "opened": opened,
            "closed": closed,
            "entry_quality_filtered": quality_filtered,
            "entry_quality_reduced": quality_reduced,
            "entry_quality_skip_counts": dict(quality_skip_counts),
            "entry_quality_skip_samples": quality_skip_samples,
            "errors": errors[-20:],
            "candidate_count": len(candidates),
        }
        state["summary"] = _summarize_state(state)
        save_state(state)
        return {"ok": True, "opened": opened, "closed": closed, "evaluated": evaluated, "elapsed_sec": round(time.time() - started, 3), "state": state}
    finally:
        _release_lock()


def shadow_dashboard_payload(*, recent_trade_limit: int = 200) -> dict[str, Any]:
    state = load_state()
    state["summary"] = _summarize_state(state)
    trades = load_recent_trades(limit=recent_trade_limit)
    return {
        "_comment": "Elite shadow live ledger. Virtual-only; no broker orders are placed.",
        "state_path": str(STATE_PATH),
        "trades_path": str(TRADES_PATH),
        "summary": state.get("summary") or {},
        "last_tick": state.get("last_tick"),
        "open_positions": list((state.get("open_positions") or {}).values()),
        "recent_trades": list(reversed(trades)),
        "events": list(reversed((state.get("events") or [])[-80:])),
    }
