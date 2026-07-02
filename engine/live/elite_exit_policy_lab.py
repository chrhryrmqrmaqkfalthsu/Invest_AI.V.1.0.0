"""Elite Shadow Exit Policy Lab.

목적:
- 실제 broker 주문 없이, Elite Shadow의 동일한 진입을 여러 청산 정책에 복제해 병렬 검증한다.
- active Elite Shadow 청산 결과와 무관하게 각 정책은 독립 장부로 열린다/닫힌다.
- 핵심 공정성 조건은 "진입은 모두 같아야 한다"이다.

주의:
- 이 모듈은 검증용 가상 ledger만 갱신한다.
- 실제 주문, live positions.json, broker 계정에는 관여하지 않는다.
- Lab 생성 이전에 이미 열려 있던 Shadow 포지션은 clean comparison에서 제외한다.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from engine.live.elite_shadow_exit_omen import _load_intraday, evaluate_shadow_exit_omen
from engine.live.elite_shadow_peak_exit_v4 import evaluate_peak_exhaustion
from engine.live.elite_shadow_trader import (
    _holding_days,
    _latest_price,
    _load_ohlcv,
    _safe_float,
    load_state as load_shadow_state,
    utc_now,
)

STATE_PATH = Path("data/_system/elite_exit_policy_lab_state.json")
TRADES_PATH = Path("data/_system/elite_exit_policy_lab_trades.jsonl")
LOCK_PATH = Path("data/_system/elite_exit_policy_lab_tick.lock")
MARKET_PROXY = "QQQ"
MIN_CLOSED_FOR_RANKING = 30
MAX_PATH_SAMPLES_PER_POSITION = 600
MAX_CLOSED_POSITIONS_IN_STATE = 1200

POLICIES: list[dict[str, Any]] = [
    {
        "policy_id": "mfe_trail_40",
        "name": "MFE 40% 반납",
        "family": "mfe_trailing",
        "description": "MFE +2% 이상 도달 후 현재 수익이 MFE의 60% 이하로 내려오면 청산",
    },
    {
        "policy_id": "mfe_trail_50",
        "name": "MFE 50% 반납",
        "family": "mfe_trailing",
        "description": "MFE +2% 이상 도달 후 현재 수익이 MFE의 50% 이하로 내려오면 청산",
    },
    {
        "policy_id": "mfe_trail_60",
        "name": "MFE 60% 반납",
        "family": "mfe_trailing",
        "description": "MFE +2% 이상 도달 후 현재 수익이 MFE의 40% 이하로 내려오면 청산",
    },
    {
        "policy_id": "profit_lock_3p_1_5p",
        "name": "+3%→+1.5% 보존",
        "family": "profit_lock",
        "description": "MFE +3% 이상 도달 후 현재 수익이 +1.5% 이하로 내려오면 청산",
    },
    {
        "policy_id": "profit_lock_5p_3p",
        "name": "+5%→+3% 보존",
        "family": "profit_lock",
        "description": "MFE +5% 이상 도달 후 현재 수익이 +3% 이하로 내려오면 청산",
    },
    {
        "policy_id": "peak_prediction_v0",
        "name": "Peak Prediction v0",
        "family": "peak_prediction",
        "description": "수익권 + VWAP 과이격 + 단기급등 + 거래량/윗꼬리/고점실패 최소 신호 조합",
    },
    {
        "policy_id": "v2_only",
        "name": "v2 Exit Omen only",
        "family": "shadow_omen",
        "description": "Shadow Exit Omen v2만 단독 적용",
    },
    {
        "policy_id": "v2_plus_v3_1",
        "name": "v2 + v3.1",
        "family": "shadow_omen_stack",
        "description": "Shadow Exit Omen v2 또는 Peak Exhaustion v3.1 중 먼저 발동하면 청산",
    },
]
POLICY_BY_ID = {p["policy_id"]: p for p in POLICIES}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        return default if math.isnan(out) else out
    except Exception:
        return default


def _pct(a: float, b: float) -> float | None:
    if a > 0.0 and b > 0.0:
        return (a / b - 1.0) * 100.0
    return None


def _parse_dt(value: Any) -> datetime | None:
    try:
        if not value:
            return None
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_lab_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("open_positions", {})
                data.setdefault("closed_positions", [])
                data.setdefault("source_entries", {})
                data.setdefault("events", [])
                data.setdefault("ignored_existing_positions", {})
                return data
        except Exception:
            pass
    now = utc_now()
    return {
        "_comment": "Exit Policy Lab. Virtual-only parallel exit policy ledger. No broker orders are placed.",
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "policies": POLICIES,
        "min_closed_for_ranking": MIN_CLOSED_FOR_RANKING,
        "clean_entry_only": True,
        "fairness_rule": "All policy copies for an entry_group_id must share the exact same source_position_id, opened_at, entry_price, shares and notional.",
        "open_positions": {},
        "closed_positions": [],
        "source_entries": {},
        "ignored_existing_positions": {},
        "events": [],
        "last_tick": None,
        "summary": {},
    }


def save_lab_state(state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    events = state.get("events") or []
    if isinstance(events, list) and len(events) > 500:
        state["events"] = events[-500:]
    closed = state.get("closed_positions") or []
    if isinstance(closed, list) and len(closed) > MAX_CLOSED_POSITIONS_IN_STATE:
        state["closed_positions"] = closed[-MAX_CLOSED_POSITIONS_IN_STATE:]
    _atomic_write_json(STATE_PATH, state)


def append_lab_trade(row: dict[str, Any]) -> None:
    TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRADES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def load_lab_trades(limit: int = 1000) -> list[dict[str, Any]]:
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
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if LOCK_PATH.exists():
        try:
            if now - LOCK_PATH.stat().st_mtime > ttl_sec:
                LOCK_PATH.unlink()
            else:
                return False
        except Exception:
            return False
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"pid={os.getpid()} ts={utc_now()}\n".encode("utf-8"))
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _event(state: dict[str, Any], event_type: str, ticker: str, message: str, payload: dict[str, Any] | None = None) -> None:
    state.setdefault("events", []).append(
        {
            "time": utc_now(),
            "event": event_type,
            "ticker": ticker,
            "message": message,
            "entry_group_id": (payload or {}).get("entry_group_id"),
            "source_position_id": (payload or {}).get("source_position_id"),
            "policy_id": (payload or {}).get("policy_id"),
            "lab_position_id": (payload or {}).get("lab_position_id"),
        }
    )


def _entry_fingerprint(source_pos: dict[str, Any]) -> str:
    ticker = str(source_pos.get("ticker") or "").upper().strip()
    source_position_id = str(source_pos.get("position_id") or "")
    opened_at = str(source_pos.get("opened_at") or "")
    entry = _num(source_pos.get("entry_price"), 0.0)
    shares = _num(source_pos.get("shares"), 0.0)
    notional = _num(source_pos.get("notional"), 0.0)
    return f"{source_position_id}|{ticker}|{opened_at}|{entry:.6f}|{shares:.8f}|{notional:.2f}"


def _clean_entry_allowed(source_pos: dict[str, Any], state: dict[str, Any]) -> tuple[bool, str]:
    created = _parse_dt(state.get("created_at")) or datetime.now(timezone.utc)
    opened = _parse_dt(source_pos.get("opened_at"))
    if opened is None:
        return False, "opened_at_missing"
    # Lab 도입 전 이미 열려 있던 보유는 같은 진입 시점부터 경로를 기록하지 못하므로 clean 비교에서 제외한다.
    if opened < created - timedelta(seconds=2):
        return False, "opened_before_lab_created"
    return True, "clean_entry"


def _clone_policy_position(source_pos: dict[str, Any], policy: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    ticker = str(source_pos.get("ticker") or "").upper().strip()
    source_position_id = str(source_pos.get("position_id") or "")
    entry_group_id = source_position_id
    policy_id = str(policy["policy_id"])
    lab_position_id = f"{entry_group_id}:{policy_id}"
    entry = _num(source_pos.get("entry_price"), 0.0)
    shares = _num(source_pos.get("shares"), 0.0)
    notional = _num(source_pos.get("notional"), 0.0)
    opened_at = str(source_pos.get("opened_at") or "")
    fingerprint = _entry_fingerprint(source_pos)
    pos = {
        "_comment": "Exit Policy Lab virtual position. No broker order was placed.",
        "lab_position_id": lab_position_id,
        "entry_group_id": entry_group_id,
        "source_position_id": source_position_id,
        "source_candidate_id": source_pos.get("candidate_id"),
        "source_rulebook_hash_short": source_pos.get("rulebook_hash_short"),
        "entry_fingerprint": fingerprint,
        "entry_same_as_source": True,
        "clean_entry": True,
        "policy_id": policy_id,
        "policy_name": policy.get("name"),
        "policy_family": policy.get("family"),
        "ticker": ticker,
        "stage": source_pos.get("stage"),
        "bucket": source_pos.get("bucket"),
        "opened_at": opened_at,
        "lab_opened_at": utc_now(),
        "entry_price": entry,
        "shares": shares,
        "notional": notional,
        "entry_score": source_pos.get("entry_score"),
        "entry_threshold": source_pos.get("entry_threshold"),
        "entry_ratio": source_pos.get("entry_ratio"),
        "entry_reasons": list(source_pos.get("entry_reasons") or [])[:8],
        "entry_quality": source_pos.get("entry_quality") or {},
        "entry_quality_score": source_pos.get("entry_quality_score"),
        "entry_quality_label": source_pos.get("entry_quality_label"),
        "entry_quality_size_factor": source_pos.get("entry_quality_size_factor"),
        "atr_at_entry": source_pos.get("atr_at_entry"),
        "exit_strategy": source_pos.get("exit_strategy"),
        "max_holding_days": source_pos.get("max_holding_days"),
        "stop_price": source_pos.get("stop_price"),
        "target_price": source_pos.get("target_price"),
        "trailing_distance": source_pos.get("trailing_distance"),
        "trailing_stop": source_pos.get("trailing_stop"),
        "rulebook_snapshot": source_pos.get("rulebook_snapshot") or {},
        "sell_omen_enabled": source_pos.get("sell_omen_enabled"),
        "sell_omen_threshold": source_pos.get("sell_omen_threshold"),
        "market_proxy": source_pos.get("market_proxy") or MARKET_PROXY,
        "market_entry_price": source_pos.get("market_entry_price"),
        "last_price": entry,
        "last_seen_at": utc_now(),
        "highest_price": entry,
        "lowest_price": entry,
        "max_profit_pct": 0.0,
        "max_loss_pct": 0.0,
        "unrealized_pnl_pct": 0.0,
        "unrealized_pnl_usd": 0.0,
        "capture_ratio": None,
        "giveback_pct": 0.0,
        "giveback_ratio": 0.0,
        "status": "open",
        "path_samples": [],
    }
    state.setdefault("open_positions", {})[lab_position_id] = pos
    return pos


def _sync_new_entries_from_shadow(state: dict[str, Any], shadow_state: dict[str, Any]) -> dict[str, Any]:
    open_shadow = shadow_state.get("open_positions") or {}
    source_entries = state.setdefault("source_entries", {})
    ignored = state.setdefault("ignored_existing_positions", {})
    created_entries = 0
    created_policy_positions = 0
    ignored_existing = 0
    for _source_key, source_pos in list(open_shadow.items()):
        if not isinstance(source_pos, dict):
            continue
        source_position_id = str(source_pos.get("position_id") or "")
        ticker = str(source_pos.get("ticker") or "").upper().strip()
        if not source_position_id or not ticker:
            continue
        if source_position_id in source_entries:
            continue
        if source_position_id in ignored:
            continue
        allowed, reason = _clean_entry_allowed(source_pos, state)
        if not allowed:
            ignored[source_position_id] = {
                "ticker": ticker,
                "opened_at": source_pos.get("opened_at"),
                "entry_price": source_pos.get("entry_price"),
                "ignored_at": utc_now(),
                "reason": reason,
                "note": "Lab 도입 전 또는 경로 기록 시작 전 포지션이므로 정책별 공정 비교에서 제외",
            }
            ignored_existing += 1
            continue
        fingerprint = _entry_fingerprint(source_pos)
        source_entries[source_position_id] = {
            "entry_group_id": source_position_id,
            "source_position_id": source_position_id,
            "ticker": ticker,
            "stage": source_pos.get("stage"),
            "bucket": source_pos.get("bucket"),
            "candidate_id": source_pos.get("candidate_id"),
            "opened_at": source_pos.get("opened_at"),
            "entry_price": source_pos.get("entry_price"),
            "shares": source_pos.get("shares"),
            "notional": source_pos.get("notional"),
            "entry_quality_score": source_pos.get("entry_quality_score"),
            "entry_quality_label": source_pos.get("entry_quality_label"),
            "entry_fingerprint": fingerprint,
            "policies": [p["policy_id"] for p in POLICIES],
            "created_at": utc_now(),
            "clean_entry": True,
            "fairness_check": {
                "same_entry_price": True,
                "same_opened_at": True,
                "same_shares": True,
                "same_notional": True,
                "same_source_position_id": True,
            },
        }
        created_entries += 1
        for policy in POLICIES:
            pos = _clone_policy_position(source_pos, policy, state)
            created_policy_positions += 1
            _event(state, "OPEN", ticker, f"{policy['policy_id']} cloned from identical Shadow entry", pos)
    return {
        "created_entries": created_entries,
        "created_policy_positions": created_policy_positions,
        "ignored_existing": ignored_existing,
        "source_entry_count": len(source_entries),
    }


def _append_path_sample(pos: dict[str, Any], price: float, pnl_pct: float, max_profit_pct: float, max_loss_pct: float) -> None:
    giveback_pct = max(0.0, max_profit_pct - pnl_pct)
    giveback_ratio = giveback_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0
    capture_ratio = pnl_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else None
    sample = {
        "time": utc_now(),
        "price": price,
        "pnl_pct": pnl_pct,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "giveback_pct": giveback_pct,
        "giveback_ratio": giveback_ratio,
        "capture_ratio": capture_ratio,
    }
    samples = pos.setdefault("path_samples", [])
    samples.append(sample)
    if len(samples) > MAX_PATH_SAMPLES_PER_POSITION:
        pos["path_samples"] = samples[-MAX_PATH_SAMPLES_PER_POSITION:]
    pos["giveback_pct"] = giveback_pct
    pos["giveback_ratio"] = giveback_ratio
    pos["capture_ratio"] = capture_ratio


def _update_position_mark(pos: dict[str, Any], price: float) -> dict[str, Any]:
    entry = _num(pos.get("entry_price"), 0.0)
    shares = _num(pos.get("shares"), 0.0)
    highest = max(_num(pos.get("highest_price"), entry), price)
    lowest = min(_num(pos.get("lowest_price"), entry), price)
    pnl_pct = (price / entry - 1.0) * 100.0 if entry > 0.0 and price > 0.0 else 0.0
    pnl_usd = shares * (price - entry)
    max_profit_pct = max(_num(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0 if entry > 0.0 else 0.0)
    max_loss_pct = min(_num(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0 if entry > 0.0 else 0.0)
    pos.update(
        {
            "last_price": price,
            "last_seen_at": utc_now(),
            "highest_price": highest,
            "lowest_price": lowest,
            "max_profit_pct": max_profit_pct,
            "max_loss_pct": max_loss_pct,
            "unrealized_pnl_pct": pnl_pct,
            "unrealized_pnl_usd": pnl_usd,
            "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        }
    )
    _append_path_sample(pos, price, pnl_pct, max_profit_pct, max_loss_pct)
    return {
        "entry": entry,
        "shares": shares,
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "giveback_pct": max(0.0, max_profit_pct - pnl_pct),
        "giveback_ratio": pos.get("giveback_ratio"),
        "capture_ratio": pos.get("capture_ratio"),
    }


def _peak_prediction_snapshot(intraday_df: pd.DataFrame | None, price: float) -> dict[str, Any]:
    if intraday_df is None or intraday_df.empty or len(intraday_df) < 25:
        return {"ok": False, "reason": "intraday_missing"}
    try:
        work = intraday_df.copy().dropna(subset=["Open", "High", "Low", "Close"])
        if work.empty or len(work) < 25:
            return {"ok": False, "reason": "intraday_window_insufficient"}
        close = work["Close"].astype(float)
        high = work["High"].astype(float)
        low = work["Low"].astype(float)
        open_ = work["Open"].astype(float)
        volume = work["Volume"].fillna(0).astype(float) if "Volume" in work.columns else pd.Series([0.0] * len(work), index=work.index)
        current = price if price > 0.0 else float(close.iloc[-1])
        typical = (high + low + close) / 3.0
        vol_sum = float(volume.sum())
        vwap = float((typical * volume).sum() / vol_sum) if vol_sum > 0.0 else float(close.expanding().mean().iloc[-1])
        day_high = float(max(high.max(), current))
        day_low = float(min(low.min(), current))
        high_idx = high.idxmax()
        try:
            high_pos = int(work.index.get_loc(high_idx))
            bars_since_high = max(0, len(work) - high_pos - 1)
        except Exception:
            bars_since_high = None
        ret_15m = _pct(current, float(close.iloc[-16])) if len(close) > 16 else None
        last_range = max(float(high.iloc[-1] - low.iloc[-1]), 0.0001)
        last_close_position = max(0.0, min(1.0, float((current - low.iloc[-1]) / last_range)))
        upper_wick_ratio = max(0.0, min(1.0, float((high.iloc[-1] - max(current, open_.iloc[-1])) / last_range)))
        vol_base = [float(v) for v in volume.iloc[-21:-1].tolist() if float(v) > 0.0]
        volume_ratio_20m = float(volume.iloc[-1]) / (sum(vol_base) / len(vol_base)) if vol_base and float(volume.iloc[-1]) > 0.0 else None
        range_position = (current - day_low) / max(day_high - day_low, 0.0001)
        high_giveback_pct = _pct(current, day_high) if day_high > 0.0 else None
        return {
            "ok": True,
            "price": current,
            "vwap": vwap,
            "dist_vwap_pct": _pct(current, vwap),
            "ret_15m_pct": ret_15m,
            "volume_ratio_20m": volume_ratio_20m,
            "upper_wick_ratio": upper_wick_ratio,
            "last_close_position": last_close_position,
            "intraday_high": day_high,
            "intraday_low": day_low,
            "intraday_range_position": max(0.0, min(1.0, range_position)),
            "intraday_high_giveback_pct": high_giveback_pct,
            "bars_since_intraday_high": bars_since_high,
        }
    except Exception as exc:
        return {"ok": False, "reason": f"intraday_error:{type(exc).__name__}"}


def _evaluate_peak_prediction_v0(pos: dict[str, Any], price: float, intraday_df: pd.DataFrame | None) -> dict[str, Any]:
    pnl_pct = _num(pos.get("unrealized_pnl_pct"), 0.0)
    if pnl_pct < 2.5:
        return {"close": False, "reason": "hold", "score": 0.0, "signals": [], "metrics": {"active": False, "pnl_pct": pnl_pct}}
    snap = _peak_prediction_snapshot(intraday_df, price)
    if not snap.get("ok"):
        return {"close": False, "reason": "hold", "score": 0.0, "signals": [], "metrics": {"active": True, "intraday": snap}}
    signals: list[dict[str, Any]] = []
    dist_vwap = _num(snap.get("dist_vwap_pct"), 0.0)
    ret15 = _num(snap.get("ret_15m_pct"), 0.0)
    vol_ratio = _num(snap.get("volume_ratio_20m"), 0.0)
    upper_wick = _num(snap.get("upper_wick_ratio"), 0.0)
    bars_since_high = _num(snap.get("bars_since_intraday_high"), 0.0)
    close_pos = _num(snap.get("last_close_position"), 0.5)
    range_pos = _num(snap.get("intraday_range_position"), 0.0)
    if dist_vwap >= 2.0:
        signals.append({"signal": "vwap_extension", "value": round(dist_vwap, 3)})
    if ret15 >= 2.0:
        signals.append({"signal": "short_term_surge_15m", "value": round(ret15, 3)})
    if vol_ratio >= 2.0:
        signals.append({"signal": "volume_climax", "value": round(vol_ratio, 3)})
    if upper_wick >= 0.35 and close_pos <= 0.55:
        signals.append({"signal": "upper_wick_rejection", "upper_wick_ratio": round(upper_wick, 3), "close_position": round(close_pos, 3)})
    elif bars_since_high >= 8:
        signals.append({"signal": "no_new_high_after_climax", "bars_since_high": int(bars_since_high)})
    if range_pos >= 0.85 and dist_vwap >= 1.2:
        signals.append({"signal": "intraday_range_top_extension", "range_position": round(range_pos, 3)})
    score = min(100.0, len(signals) * 25.0)
    close = len(signals) >= 3
    return {
        "close": close,
        "reason": "peak_prediction_v0" if close else "hold",
        "score": score,
        "signals": signals,
        "metrics": {"active": True, "intraday": snap, "signal_count": len(signals), "pnl_pct": pnl_pct},
    }


def _evaluate_policy(
    pos: dict[str, Any],
    *,
    df: pd.DataFrame | None,
    price: float,
    intraday_df: pd.DataFrame | None,
    market_price: float | None,
    market_intraday_df: pd.DataFrame | None,
) -> dict[str, Any]:
    policy_id = str(pos.get("policy_id") or "")
    pnl_pct = _num(pos.get("unrealized_pnl_pct"), 0.0)
    max_profit_pct = _num(pos.get("max_profit_pct"), 0.0)
    if policy_id == "mfe_trail_40":
        close = max_profit_pct >= 2.0 and pnl_pct <= max_profit_pct * 0.60
        return {"close": close, "reason": "mfe_trail_40" if close else "hold", "detail": {"max_profit_pct": max_profit_pct, "pnl_pct": pnl_pct, "threshold_pct": max_profit_pct * 0.60}}
    if policy_id == "mfe_trail_50":
        close = max_profit_pct >= 2.0 and pnl_pct <= max_profit_pct * 0.50
        return {"close": close, "reason": "mfe_trail_50" if close else "hold", "detail": {"max_profit_pct": max_profit_pct, "pnl_pct": pnl_pct, "threshold_pct": max_profit_pct * 0.50}}
    if policy_id == "mfe_trail_60":
        close = max_profit_pct >= 2.0 and pnl_pct <= max_profit_pct * 0.40
        return {"close": close, "reason": "mfe_trail_60" if close else "hold", "detail": {"max_profit_pct": max_profit_pct, "pnl_pct": pnl_pct, "threshold_pct": max_profit_pct * 0.40}}
    if policy_id == "profit_lock_3p_1_5p":
        close = max_profit_pct >= 3.0 and pnl_pct <= 1.5
        return {"close": close, "reason": "profit_lock_3p_1_5p" if close else "hold", "detail": {"max_profit_pct": max_profit_pct, "pnl_pct": pnl_pct, "lock_pct": 1.5}}
    if policy_id == "profit_lock_5p_3p":
        close = max_profit_pct >= 5.0 and pnl_pct <= 3.0
        return {"close": close, "reason": "profit_lock_5p_3p" if close else "hold", "detail": {"max_profit_pct": max_profit_pct, "pnl_pct": pnl_pct, "lock_pct": 3.0}}
    if policy_id == "peak_prediction_v0":
        pp = _evaluate_peak_prediction_v0(pos, price, intraday_df)
        return {"close": bool(pp.get("close")), "reason": pp.get("reason"), "detail": pp}
    if policy_id in {"v2_only", "v2_plus_v3_1"}:
        omen = evaluate_shadow_exit_omen(
            pos=pos,
            df=df,
            price=price,
            market_price=market_price,
            intraday_df=intraday_df,
            market_intraday_df=market_intraday_df,
        )
        if bool(omen.get("close")):
            return {"close": True, "reason": f"{policy_id}:{omen.get('reason')}", "detail": {"v2": omen}}
        if policy_id == "v2_plus_v3_1":
            peak = evaluate_peak_exhaustion(
                pos=pos,
                price=price,
                intraday_df=intraday_df,
                market_intraday_df=market_intraday_df,
            )
            if bool(peak.get("close")):
                return {"close": True, "reason": f"{policy_id}:{peak.get('reason')}", "detail": {"v2": omen, "v3_1": peak}}
            return {"close": False, "reason": "hold", "detail": {"v2": omen, "v3_1": peak}}
        return {"close": False, "reason": "hold", "detail": {"v2": omen}}
    return {"close": False, "reason": "hold", "detail": {"unknown_policy": policy_id}}


def _close_lab_position(state: dict[str, Any], lab_id: str, pos: dict[str, Any], price: float, mark: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    entry = _num(pos.get("entry_price"), 0.0)
    shares = _num(pos.get("shares"), 0.0)
    pnl_pct = _num(mark.get("pnl_pct"), 0.0)
    pnl_usd = shares * (price - entry)
    max_profit_pct = _num(mark.get("max_profit_pct"), 0.0)
    max_loss_pct = _num(mark.get("max_loss_pct"), 0.0)
    giveback_pct = max(0.0, max_profit_pct - pnl_pct)
    capture_ratio = pnl_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else None
    closed_at = utc_now()
    trade = {
        "_comment": "Exit Policy Lab virtual closed trade. No broker order was placed.",
        "lab_position_id": lab_id,
        "entry_group_id": pos.get("entry_group_id"),
        "source_position_id": pos.get("source_position_id"),
        "source_candidate_id": pos.get("source_candidate_id"),
        "entry_fingerprint": pos.get("entry_fingerprint"),
        "entry_same_as_source": pos.get("entry_same_as_source"),
        "clean_entry": pos.get("clean_entry"),
        "policy_id": pos.get("policy_id"),
        "policy_name": pos.get("policy_name"),
        "policy_family": pos.get("policy_family"),
        "ticker": pos.get("ticker"),
        "stage": pos.get("stage"),
        "bucket": pos.get("bucket"),
        "opened_at": pos.get("opened_at"),
        "lab_opened_at": pos.get("lab_opened_at"),
        "closed_at": closed_at,
        "entry_price": entry,
        "exit_price": price,
        "shares": shares,
        "notional": _num(pos.get("notional"), 0.0),
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd,
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "giveback_pct": giveback_pct,
        "giveback_ratio": giveback_pct / max(max_profit_pct, 0.0001) if max_profit_pct > 0.0 else 0.0,
        "capture_ratio": capture_ratio,
        "mfe_bucket": "mfe_ge_2" if max_profit_pct >= 2.0 else "mfe_lt_2",
        "exit_reason": decision.get("reason"),
        "policy_detail": decision.get("detail"),
        "path_sample_count": len(pos.get("path_samples") or []),
        "path_samples": list(pos.get("path_samples") or [])[-MAX_PATH_SAMPLES_PER_POSITION:],
        "entry_quality_score": pos.get("entry_quality_score"),
        "entry_quality_label": pos.get("entry_quality_label"),
        "post_exit": {
            "tracking_until": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "post_exit_high_price": price,
            "post_exit_high_pct": 0.0,
        },
    }
    append_lab_trade(trade)
    state.get("open_positions", {}).pop(lab_id, None)
    state.setdefault("closed_positions", []).append(trade)
    _event(state, "CLOSE", str(pos.get("ticker") or ""), f"{pos.get('policy_id')} {trade['exit_reason']} pnl={pnl_pct:+.2f}% cap={capture_ratio if capture_ratio is not None else 'NA'}", trade)
    return trade


def _update_post_exit_tracking(state: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    updated = 0
    errors: list[dict[str, Any]] = []
    for trade in state.get("closed_positions") or []:
        if not isinstance(trade, dict):
            continue
        post = trade.setdefault("post_exit", {})
        closed_at = _parse_dt(trade.get("closed_at"))
        if closed_at is None:
            continue
        tracking_until = _parse_dt(post.get("tracking_until")) or (closed_at + timedelta(days=2))
        if now > tracking_until and post.get("next_day_return_pct") is not None:
            continue
        ticker = str(trade.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        df = _load_ohlcv(ticker)
        price = _latest_price(ticker, df)
        if not price:
            errors.append({"ticker": ticker, "reason": "post_price_missing"})
            continue
        exit_price = _num(trade.get("exit_price"), 0.0)
        if exit_price <= 0.0:
            continue
        post_high = max(_num(post.get("post_exit_high_price"), exit_price), price)
        post["post_exit_high_price"] = post_high
        post["post_exit_high_pct"] = (post_high / exit_price - 1.0) * 100.0
        if now >= closed_at + timedelta(minutes=30) and post.get("return_30m_pct") is None:
            post["price_30m"] = price
            post["return_30m_pct"] = (price / exit_price - 1.0) * 100.0
            updated += 1
        if now >= closed_at + timedelta(minutes=60) and post.get("return_60m_pct") is None:
            post["price_60m"] = price
            post["return_60m_pct"] = (price / exit_price - 1.0) * 100.0
            updated += 1
        if now >= closed_at + timedelta(days=1) and post.get("next_day_return_pct") is None:
            post["price_next_day"] = price
            post["next_day_return_pct"] = (price / exit_price - 1.0) * 100.0
            updated += 1
        post["premature_30m_2p"] = bool(_num(post.get("return_30m_pct"), 0.0) >= 2.0) if post.get("return_30m_pct") is not None else None
        post["premature_60m_3p"] = bool(_num(post.get("return_60m_pct"), 0.0) >= 3.0) if post.get("return_60m_pct") is not None else None
    return {"updated": updated, "errors": errors[-10:]}


def _summarize_lab(state: dict[str, Any]) -> dict[str, Any]:
    open_positions = list((state.get("open_positions") or {}).values())
    closed_positions = list(state.get("closed_positions") or [])
    by_policy: dict[str, dict[str, Any]] = {}
    for policy in POLICIES:
        pid = policy["policy_id"]
        opens = [p for p in open_positions if p.get("policy_id") == pid]
        closed = [t for t in closed_positions if t.get("policy_id") == pid]
        pnl = [_num(t.get("pnl_pct"), 0.0) for t in closed]
        pnl_usd = [_num(t.get("pnl_usd"), 0.0) for t in closed]
        wins = [x for x in pnl if x > 0]
        mfe_ge_2 = [t for t in closed if _num(t.get("max_profit_pct"), 0.0) >= 2.0]
        mfe_lt_2 = [t for t in closed if _num(t.get("max_profit_pct"), 0.0) < 2.0]
        captures = [_num(t.get("capture_ratio"), 0.0) * 100.0 for t in mfe_ge_2 if t.get("capture_ratio") is not None]
        givebacks = [_num(t.get("giveback_pct"), 0.0) for t in mfe_ge_2]
        loss_failed = [_num(t.get("pnl_pct"), 0.0) for t in mfe_lt_2]
        post_30 = [((t.get("post_exit") or {}).get("return_30m_pct")) for t in closed]
        post_60 = [((t.get("post_exit") or {}).get("return_60m_pct")) for t in closed]
        post_30_vals = [_num(x, 0.0) for x in post_30 if x is not None]
        post_60_vals = [_num(x, 0.0) for x in post_60 if x is not None]
        premature_30 = [t for t in closed if bool((t.get("post_exit") or {}).get("premature_30m_2p"))]
        premature_60 = [t for t in closed if bool((t.get("post_exit") or {}).get("premature_60m_3p"))]
        closed_n = len(closed)
        by_policy[pid] = {
            "policy_id": pid,
            "name": policy.get("name"),
            "family": policy.get("family"),
            "description": policy.get("description"),
            "open_count": len(opens),
            "closed_count": closed_n,
            "ranking_locked": closed_n < MIN_CLOSED_FOR_RANKING,
            "min_closed_for_ranking": MIN_CLOSED_FOR_RANKING,
            "win_rate": len(wins) / len(pnl) * 100.0 if pnl else 0.0,
            "avg_pnl_pct": sum(pnl) / len(pnl) if pnl else 0.0,
            "total_pnl_usd": sum(pnl_usd),
            "avg_capture_pct_mfe_ge_2": sum(captures) / len(captures) if captures else None,
            "avg_giveback_pct_mfe_ge_2": sum(givebacks) / len(givebacks) if givebacks else None,
            "mfe_ge_2_count": len(mfe_ge_2),
            "mfe_lt_2_count": len(mfe_lt_2),
            "avg_loss_containment_mfe_lt_2": sum(loss_failed) / len(loss_failed) if loss_failed else None,
            "avg_post_30m_return_pct": sum(post_30_vals) / len(post_30_vals) if post_30_vals else None,
            "avg_post_60m_return_pct": sum(post_60_vals) / len(post_60_vals) if post_60_vals else None,
            "premature_30m_2p_rate": len(premature_30) / len(post_30_vals) * 100.0 if post_30_vals else None,
            "premature_60m_3p_rate": len(premature_60) / len(post_60_vals) * 100.0 if post_60_vals else None,
        }
    entry_groups: dict[str, dict[str, Any]] = {}
    for p in open_positions:
        gid = str(p.get("entry_group_id") or "")
        if not gid:
            continue
        row = entry_groups.setdefault(
            gid,
            {
                "entry_group_id": gid,
                "ticker": p.get("ticker"),
                "opened_at": p.get("opened_at"),
                "entry_price": p.get("entry_price"),
                "stage": p.get("stage"),
                "bucket": p.get("bucket"),
                "open_policy_count": 0,
                "closed_policy_count": 0,
                "policy_states": [],
            },
        )
        row["open_policy_count"] += 1
        row["policy_states"].append({"policy_id": p.get("policy_id"), "status": "open", "pnl_pct": p.get("unrealized_pnl_pct"), "max_profit_pct": p.get("max_profit_pct")})
    for t in closed_positions:
        gid = str(t.get("entry_group_id") or "")
        if not gid:
            continue
        row = entry_groups.setdefault(
            gid,
            {
                "entry_group_id": gid,
                "ticker": t.get("ticker"),
                "opened_at": t.get("opened_at"),
                "entry_price": t.get("entry_price"),
                "stage": t.get("stage"),
                "bucket": t.get("bucket"),
                "open_policy_count": 0,
                "closed_policy_count": 0,
                "policy_states": [],
            },
        )
        row["closed_policy_count"] += 1
        row["policy_states"].append({"policy_id": t.get("policy_id"), "status": "closed", "pnl_pct": t.get("pnl_pct"), "max_profit_pct": t.get("max_profit_pct"), "capture_ratio": t.get("capture_ratio")})
    return {
        "policy_count": len(POLICIES),
        "source_entry_count": len(state.get("source_entries") or {}),
        "ignored_existing_count": len(state.get("ignored_existing_positions") or {}),
        "open_policy_positions": len(open_positions),
        "closed_policy_positions": len(closed_positions),
        "clean_entry_only": bool(state.get("clean_entry_only", True)),
        "ranking_locked": all(row.get("ranking_locked") for row in by_policy.values()) if by_policy else True,
        "min_closed_for_ranking": MIN_CLOSED_FOR_RANKING,
        "by_policy": by_policy,
        "entry_groups": sorted(entry_groups.values(), key=lambda x: str(x.get("opened_at") or ""), reverse=True)[:120],
    }


def run_exit_policy_lab_tick(*, force: bool = False) -> dict[str, Any]:
    if not force and not _acquire_lock():
        return {"ok": False, "reason": "tick_already_running", "state": load_lab_state()}
    started = time.time()
    state = load_lab_state()
    closed = 0
    evaluated = 0
    errors: list[dict[str, Any]] = []
    close_counts: Counter[str] = Counter()
    try:
        shadow_state = load_shadow_state()
        sync = _sync_new_entries_from_shadow(state, shadow_state)
        post = _update_post_exit_tracking(state)

        market_df = _load_ohlcv(MARKET_PROXY)
        market_price = _latest_price(MARKET_PROXY, market_df) if market_df is not None else None
        market_intraday_df = _load_intraday(MARKET_PROXY)
        grouped: dict[str, dict[str, Any]] = {}
        for lab_id, pos in list((state.get("open_positions") or {}).items()):
            ticker = str(pos.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            grouped.setdefault(ticker, {})[lab_id] = pos

        for ticker, positions in grouped.items():
            df = _load_ohlcv(ticker)
            price = _latest_price(ticker, df)
            intraday_df = _load_intraday(ticker)
            if not price:
                for lab_id in positions:
                    errors.append({"ticker": ticker, "lab_position_id": lab_id, "reason": "price_missing"})
                continue
            for lab_id, pos in list(positions.items()):
                if lab_id not in (state.get("open_positions") or {}):
                    continue
                mark = _update_position_mark(pos, price)
                decision = _evaluate_policy(pos, df=df, price=price, intraday_df=intraday_df, market_price=market_price, market_intraday_df=market_intraday_df)
                pos["last_policy_decision"] = decision
                evaluated += 1
                if bool(decision.get("close")):
                    trade = _close_lab_position(state, lab_id, pos, price, mark, decision)
                    closed += 1
                    close_counts[str(trade.get("policy_id") or "unknown")] += 1
        state["summary"] = _summarize_lab(state)
        state["last_tick"] = {
            "time": utc_now(),
            "elapsed_sec": round(time.time() - started, 3),
            "evaluated": evaluated,
            "closed": closed,
            "close_counts": dict(close_counts),
            "errors": errors[-20:],
            "sync": sync,
            "post_exit_tracking": post,
            "open_policy_positions": len(state.get("open_positions") or {}),
            "closed_policy_positions": len(state.get("closed_positions") or []),
        }
        save_lab_state(state)
        return {"ok": True, **state["last_tick"], "state": state}
    finally:
        if not force:
            _release_lock()


def exit_policy_lab_payload(*, recent_trade_limit: int = 300) -> dict[str, Any]:
    state = load_lab_state()
    state["summary"] = _summarize_lab(state)
    recent_trades = list(reversed(load_lab_trades(limit=recent_trade_limit)))
    open_positions = list((state.get("open_positions") or {}).values())
    return {
        "_comment": "Exit Policy Lab payload. Virtual-only parallel exit policy comparison; no broker orders are placed.",
        "state_path": str(STATE_PATH),
        "trades_path": str(TRADES_PATH),
        "policies": POLICIES,
        "fairness_rule": state.get("fairness_rule"),
        "clean_entry_only": state.get("clean_entry_only", True),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "last_tick": state.get("last_tick"),
        "summary": state.get("summary") or {},
        "open_positions": open_positions,
        "recent_trades": recent_trades,
        "closed_positions": list(reversed((state.get("closed_positions") or [])[-recent_trade_limit:])),
        "ignored_existing_positions": list((state.get("ignored_existing_positions") or {}).values())[-100:],
        "events": list(reversed((state.get("events") or [])[-120:])),
    }
