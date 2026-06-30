"""Elite gate strategy simulator.

무엇을 하는 파일인가:
- 기존 elite shadow 후보 중 BUY 신호가 뜬 종목을 바로 사지 않고,
  신호 히스토리 기반 매수 게이트를 적용한 별도 모의 ledger를 운용한다.
- strategy=final_gate: 최종 판단 로직으로 BUY/WAIT/NO_BUY를 결정한다.
- strategy=pullback_only: 눌림 재진입으로 확인된 후보만 매수한다.
- 실제 broker 주문, live runner, positions.json, parameters.json은 절대 수정하지 않는다.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from engine.live.elite_shadow_report import build_elite_shadow_report
from engine.live.elite_shadow_trader import (
    DEFAULT_NOTIONAL_USD,
    _holding_days,
    _latest_price,
    _load_ohlcv,
    _open_position,
    _position_key,
    _safe_float,
    _safe_int,
    _sell_omen_hit,
    evaluate_candidate,
    utc_now,
)
from engine.live.elite_signal_history import build_signal_history
from engine.market.context import get_market_context

STATE_PATH = Path("data/_system/elite_strategy_sim_state.json")
TRADES_PATH = Path("data/_system/elite_strategy_sim_trades.jsonl")
LOCK_PATH = Path("data/_system/elite_strategy_sim_tick.lock")
STRATEGIES = ("final_gate", "pullback_only")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _new_strategy_state(name: str) -> dict[str, Any]:
    return {"strategy": name, "open_positions": {}, "closed_count": 0, "events": [], "last_tick": None, "summary": {}}


def load_strategy_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("strategies", {})
                for name in STRATEGIES:
                    data["strategies"].setdefault(name, _new_strategy_state(name))
                return data
        except Exception:
            pass
    return {
        "_comment": "Elite strategy simulator state. Virtual-only; no broker orders are placed.",
        "version": 1,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "strategies": {name: _new_strategy_state(name) for name in STRATEGIES},
    }


def save_strategy_state(state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    for sim in (state.get("strategies") or {}).values():
        events = sim.get("events") or []
        if isinstance(events, list) and len(events) > 300:
            sim["events"] = events[-300:]
    _atomic_write_json(STATE_PATH, state)


def _acquire_lock(ttl_sec: float = 1200.0) -> bool:
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
    except Exception:
        pass


def _append_trade(strategy: str, row: dict[str, Any]) -> None:
    TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(row)
    payload["strategy"] = strategy
    payload["_comment"] = "Elite strategy virtual closed trade. No broker order was placed."
    with TRADES_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _load_strategy_trades(strategy: str, limit: int = 10000) -> list[dict[str, Any]]:
    if not TRADES_PATH.exists():
        return []
    out: list[dict[str, Any]] = []
    lines = [line for line in TRADES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in lines[-max(limit * 3, limit):]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if row.get("strategy") == strategy:
            out.append(row)
    return out[-limit:]


def _event(sim: dict[str, Any], event_type: str, ticker: str, message: str, payload: dict[str, Any] | None = None) -> None:
    sim.setdefault("events", []).append(
        {
            "time": utc_now(),
            "event": event_type,
            "ticker": ticker,
            "message": message,
            "candidate_id": (payload or {}).get("candidate_id"),
            "position_id": (payload or {}).get("position_id"),
        }
    )


def _ratio_retention(rows: list[dict[str, Any]]) -> float | None:
    buy_rows = [r for r in rows if r.get("ok") and r.get("should_buy")]
    if not buy_rows:
        return None
    first = _safe_float(buy_rows[0].get("ratio"), 0.0)
    last = _safe_float(buy_rows[-1].get("ratio"), 0.0)
    if first <= 0:
        return None
    return last / first


def _price_rebound_confirmed(rows: list[dict[str, Any]], current_price: float) -> bool:
    ok_rows = [r for r in rows if r.get("ok")]
    if len(ok_rows) < 3:
        return False
    c1 = _safe_float(ok_rows[-1].get("close"), 0.0)
    c2 = _safe_float(ok_rows[-2].get("close"), 0.0)
    c3 = _safe_float(ok_rows[-3].get("close"), 0.0)
    recent_low = min(c1, c2, c3)
    return (current_price > c1 and current_price > recent_low * 1.01) or (c1 > c2 and c2 >= c3)


def judge_buy_gate(candidate: dict[str, Any], ev: dict[str, Any] | None = None, *, days: int = 12) -> dict[str, Any]:
    if ev is not None and not ev.get("should_buy"):
        return {"action": "NO_BUY", "gate": "NO_BUY_NO_CURRENT_SIGNAL", "reason": "current evaluator says HOLD"}
    hist = build_signal_history(candidate_id=_position_key(candidate), days=days)
    if not hist.get("ok"):
        return {"action": "NO_BUY", "gate": "NO_BUY_HISTORY_ERROR", "reason": hist.get("reason"), "history": hist}
    summary = hist.get("summary") or {}
    rows = hist.get("rows") or []
    if not summary.get("last_should_buy"):
        return {"action": "NO_BUY", "gate": "NO_BUY_NO_CURRENT_SIGNAL", "reason": "last replay day is not BUY", "history": hist}

    cons = _safe_int(summary.get("consecutive_buy_days"), 0)
    current_price = _safe_float(summary.get("current_price"), 0.0)
    first_price = _safe_float(summary.get("first_buy_price"), 0.0)
    proposed_vs_first = summary.get("chase_from_first_buy_pct")
    if proposed_vs_first is None and current_price > 0 and first_price > 0:
        proposed_vs_first = (current_price / first_price - 1.0) * 100.0
    proposed_vs_first_f = _safe_float(proposed_vs_first, 0.0)
    retention = _ratio_retention(rows)
    rebound = _price_rebound_confirmed(rows, current_price)
    last_ratio = _safe_float(summary.get("last_ratio"), 0.0)
    score_stable = retention is not None and retention >= 0.70 and last_ratio >= 1.0

    if cons <= 2:
        if proposed_vs_first_f < 3.0:
            gate, action, reason = "BUY_FRESH", "BUY", "fresh signal and not chased"
        else:
            gate, action, reason = "WAIT_PRICE_CHASE", "WAIT", "fresh-ish signal but price already chased"
    elif cons >= 5:
        if proposed_vs_first_f >= 3.0:
            gate, action, reason = "NO_BUY_LATE_CHASE", "NO_BUY", "old signal with price chase"
        elif proposed_vs_first_f <= -3.0:
            if score_stable and rebound:
                gate, action, reason = "BUY_PULLBACK_REENTRY", "BUY", "old signal but pullback + rebound + score stable"
            elif score_stable:
                gate, action, reason = "WAIT_PULLBACK_CONFIRM", "WAIT", "pullback candidate but rebound not confirmed"
            else:
                gate, action, reason = "NO_BUY_SIGNAL_FAILING", "NO_BUY", "pullback candidate but score/ratio decayed"
        else:
            gate, action, reason = "WAIT_OLD_SIGNAL_NEEDS_RESET", "WAIT", "old signal in neutral price zone"
    else:
        if proposed_vs_first_f >= 3.0:
            gate, action, reason = "WAIT_PRICE_CHASE", "WAIT", "price chased before enough reset"
        else:
            gate, action, reason = "BUY_NORMAL", "BUY", "normal non-chased signal"

    return {
        "action": action,
        "gate": gate,
        "reason": reason,
        "candidate_id": _position_key(candidate),
        "ticker": candidate.get("ticker"),
        "consecutive_buy_days": cons,
        "first_buy_date": summary.get("first_buy_date"),
        "first_buy_price": first_price,
        "current_price": current_price,
        "proposed_vs_first_buy_pct": proposed_vs_first_f,
        "ratio_retention": retention,
        "last_ratio": last_ratio,
        "rebound_confirmed": rebound,
        "history_summary": summary,
    }


def _strategy_allows(strategy: str, judgment: dict[str, Any]) -> bool:
    gate = judgment.get("gate")
    action = judgment.get("action")
    if strategy == "pullback_only":
        return gate == "BUY_PULLBACK_REENTRY"
    if strategy == "final_gate":
        return action == "BUY" and gate in {"BUY_FRESH", "BUY_NORMAL", "BUY_PULLBACK_REENTRY"}
    return False


def _open_strategy_position(strategy: str, candidate: dict[str, Any], ev: dict[str, Any], judgment: dict[str, Any], sim: dict[str, Any], *, notional: float) -> dict[str, Any]:
    sim["open_positions"] = dict(sim.get("open_positions") or {})
    pos = _open_position(candidate, ev, sim, notional=notional)
    pos["strategy"] = strategy
    pos["gate"] = judgment.get("gate")
    pos["gate_reason"] = judgment.get("reason")
    pos["signal_history"] = {
        "first_buy_date": judgment.get("first_buy_date"),
        "first_buy_price": judgment.get("first_buy_price"),
        "consecutive_buy_days": judgment.get("consecutive_buy_days"),
        "proposed_vs_first_buy_pct": judgment.get("proposed_vs_first_buy_pct"),
        "ratio_retention": judgment.get("ratio_retention"),
        "rebound_confirmed": judgment.get("rebound_confirmed"),
    }
    _event(sim, "OPEN", str(pos.get("ticker") or ""), f"{strategy} {judgment.get('gate')} price={pos.get('entry_price'):.2f}", pos)
    return pos


def _close_strategy_position(strategy: str, pos_key: str, pos: dict[str, Any], price: float, sim: dict[str, Any]) -> dict[str, Any] | None:
    entry = _safe_float(pos.get("entry_price"), 0.0)
    if entry <= 0.0 or price <= 0.0:
        return None
    highest = max(_safe_float(pos.get("highest_price"), entry), price)
    lowest = min(_safe_float(pos.get("lowest_price"), entry), price)
    pnl_pct = (price / entry - 1.0) * 100.0
    max_profit_pct = max(_safe_float(pos.get("max_profit_pct"), 0.0), (highest / entry - 1.0) * 100.0)
    max_loss_pct = min(_safe_float(pos.get("max_loss_pct"), 0.0), (lowest / entry - 1.0) * 100.0)
    pos.update(
        {
            "highest_price": highest,
            "lowest_price": lowest,
            "max_profit_pct": max_profit_pct,
            "max_loss_pct": max_loss_pct,
            "last_price": price,
            "last_seen_at": utc_now(),
            "unrealized_pnl_pct": pnl_pct,
            "unrealized_pnl_usd": _safe_float(pos.get("shares")) * (price - entry),
            "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        }
    )
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
    trade = {
        "position_id": pos.get("position_id"),
        "candidate_id": pos_key,
        "ticker": pos.get("ticker"),
        "stage": pos.get("stage"),
        "bucket": pos.get("bucket"),
        "rulebook_hash_short": pos.get("rulebook_hash_short"),
        "gate": pos.get("gate"),
        "opened_at": pos.get("opened_at"),
        "closed_at": utc_now(),
        "entry_price": entry,
        "exit_price": price,
        "shares": shares,
        "notional": _safe_float(pos.get("notional"), 0.0),
        "pnl_pct": pnl_pct,
        "pnl_usd": shares * (price - entry),
        "exit_reason": reason,
        "holding_days": _holding_days(str(pos.get("opened_at") or "")),
        "max_profit_pct": max_profit_pct,
        "max_loss_pct": max_loss_pct,
        "entry_score": pos.get("entry_score"),
        "entry_threshold": pos.get("entry_threshold"),
        "entry_ratio": pos.get("entry_ratio"),
        "last_sell_omen_score": pos.get("sell_omen_score"),
    }
    _append_trade(strategy, trade)
    sim["open_positions"].pop(pos_key, None)
    sim["closed_count"] = _safe_int(sim.get("closed_count"), 0) + 1
    _event(sim, "CLOSE", str(pos.get("ticker") or ""), f"{strategy} {reason} pnl={pnl_pct:+.2f}%", trade)
    return trade


def _summary_for_sim(strategy: str, sim: dict[str, Any]) -> dict[str, Any]:
    open_positions = list((sim.get("open_positions") or {}).values())
    trades = _load_strategy_trades(strategy, limit=10000)
    realized_pnl = sum(_safe_float(t.get("pnl_usd"), 0.0) for t in trades)
    realized_notional = sum(_safe_float(t.get("notional"), 0.0) for t in trades)
    open_pnl = sum(_safe_float(p.get("unrealized_pnl_usd"), 0.0) for p in open_positions)
    open_notional = sum(_safe_float(p.get("notional"), 0.0) for p in open_positions)
    pnls = [_safe_float(t.get("pnl_pct"), 0.0) for t in trades]
    wins = [p for p in pnls if p > 0]
    total_notional = realized_notional + open_notional
    total_pnl = realized_pnl + open_pnl
    return {
        "open_count": len(open_positions),
        "closed_count": len(trades),
        "win_rate": len(wins) / len(pnls) * 100.0 if pnls else 0.0,
        "realized_pnl_usd": realized_pnl,
        "open_unrealized_usd": open_pnl,
        "total_pnl_usd": total_pnl,
        "open_notional": open_notional,
        "closed_notional": realized_notional,
        "total_notional": total_notional,
        "total_roi_pct": total_pnl / total_notional * 100.0 if total_notional else 0.0,
        "open_roi_pct": open_pnl / open_notional * 100.0 if open_notional else 0.0,
    }


def run_strategy_sim_tick(*, max_candidates: int = 93, notional: float = DEFAULT_NOTIONAL_USD, force: bool = False) -> dict[str, Any]:
    if not force and not _acquire_lock():
        return {"ok": False, "reason": "strategy_sim_tick_already_running", "state": load_strategy_state()}
    started = time.time()
    state = load_strategy_state()
    results: dict[str, Any] = {name: {"opened": 0, "closed": 0, "skipped": Counter(), "evaluated": 0} for name in STRATEGIES}
    try:
        try:
            ctx = get_market_context()
        except Exception:
            ctx = None
        report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
        candidates = (report.get("candidates") or [])[:max_candidates]

        for strategy in STRATEGIES:
            sim = state["strategies"][strategy]
            for pos_key, pos in list((sim.get("open_positions") or {}).items()):
                ticker = str(pos.get("ticker") or "").upper()
                df = _load_ohlcv(ticker)
                price = _latest_price(ticker, df)
                if not price:
                    continue
                if _close_strategy_position(strategy, pos_key, pos, price, sim) is not None:
                    results[strategy]["closed"] += 1

        for candidate in candidates:
            key = _position_key(candidate)
            ticker = str(candidate.get("ticker") or "").upper()
            if not key or not ticker:
                continue
            ev = evaluate_candidate(candidate, ctx=ctx)
            if not ev.get("ok"):
                for strategy in STRATEGIES:
                    results[strategy]["skipped"][str(ev.get("reason") or "eval_error")] += 1
                continue
            if not ev.get("should_buy"):
                for strategy in STRATEGIES:
                    results[strategy]["skipped"]["not_buy_signal"] += 1
                continue
            judgment = judge_buy_gate(candidate, ev=ev, days=12)
            for strategy in STRATEGIES:
                sim = state["strategies"][strategy]
                results[strategy]["evaluated"] += 1
                open_positions = sim.get("open_positions") or {}
                open_tickers = {str(p.get("ticker") or "").upper() for p in open_positions.values()}
                if key in open_positions or ticker in open_tickers:
                    results[strategy]["skipped"]["already_open"] += 1
                    continue
                if not _strategy_allows(strategy, judgment):
                    results[strategy]["skipped"][str(judgment.get("gate") or "gate_blocked")] += 1
                    continue
                _open_strategy_position(strategy, candidate, ev, judgment, sim, notional=notional)
                results[strategy]["opened"] += 1

        for strategy in STRATEGIES:
            sim = state["strategies"][strategy]
            sim["summary"] = _summary_for_sim(strategy, sim)
            sim["last_tick"] = {
                "time": utc_now(),
                "elapsed_sec": round(time.time() - started, 3),
                "candidate_count": len(candidates),
                "opened": results[strategy]["opened"],
                "closed": results[strategy]["closed"],
                "evaluated": results[strategy]["evaluated"],
                "skipped": dict(results[strategy]["skipped"].most_common(20)),
            }
        save_strategy_state(state)
        return {"ok": True, "elapsed_sec": round(time.time() - started, 3), "results": {k: {**v, "skipped": dict(v["skipped"])} for k, v in results.items()}, "state": state}
    finally:
        if not force:
            _release_lock()


def strategy_sim_payload(*, recent_trade_limit: int = 300) -> dict[str, Any]:
    state = load_strategy_state()
    strategies: dict[str, Any] = {}
    for name in STRATEGIES:
        sim = state["strategies"].setdefault(name, _new_strategy_state(name))
        sim["summary"] = _summary_for_sim(name, sim)
        trades = _load_strategy_trades(name, limit=recent_trade_limit)
        strategies[name] = {
            "name": name,
            "summary": sim.get("summary") or {},
            "last_tick": sim.get("last_tick"),
            "open_positions": list((sim.get("open_positions") or {}).values()),
            "recent_trades": list(reversed(trades)),
            "events": list(reversed((sim.get("events") or [])[-80:])),
            "description": "최종 판단 로직" if name == "final_gate" else "눌림 재진입 전용",
        }
    return {
        "_comment": "Elite strategy simulations. Virtual-only; no broker orders are placed.",
        "state_path": str(STATE_PATH),
        "trades_path": str(TRADES_PATH),
        "strategies": strategies,
    }
