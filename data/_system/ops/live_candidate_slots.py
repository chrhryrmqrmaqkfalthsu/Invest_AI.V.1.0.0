#!/usr/bin/env python3
"""Live 8-slot candidate display tool.

Virtual/operational helper only.
- Does not submit broker orders.
- Does not modify engine/live source modules, positions.json, parameters.json, or .env.
- Reuses the elite strategy simulation / elite shadow candidate evaluation stack:
  build_elite_shadow_report(...), evaluate_candidate(...), get_market_context(), regular_hours_snapshot().
"""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.elite_shadow_report import build_elite_shadow_report
from engine.live.elite_shadow_trader import evaluate_candidate
from engine.live.regular_hours_gate import regular_hours_snapshot
from engine.market.context import get_market_context

STATE_PATH = Path("data/_system/live_slots_state.json")
EVENTS_PATH = Path("data/_system/live_slots_events.jsonl")
LOCK_PATH = Path("data/_system/live_slots_tick.lock")
GATE_SOURCE_PATH = Path("data/_system/analysis/entry_quality_stops_regime_20260707/entry_filter_candidates.csv")
VOL_SOURCE_PATH = Path("data/_system/analysis/vol_perstock_mae_mfe_20260707/per_candidate_summary.csv")
LIVE_CANDIDATE_LIST_PATH = Path("data/_system/live_candidate_list_20260707.json")
SPY_REGIME_PATHS = [
    Path("data/_system/analysis/entry_quality_stops_regime_20260707/benchmark_SPY_regime.csv"),
    Path("data/_system/analysis/ohlc_snapshot_20260707/benchmark_SPY_regime.csv"),
]
SLOT_COUNT = 8
DEFAULT_INTERVAL_SEC = 60
MAX_CANDIDATES = 93
KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
EQ_POLICY = "EQ_FILTER_UNVERIFIED_REFERENCE_ONLY_NOT_A_GATE"
EQ_VERDICT = "EQ_FILTER_UNVERIFIED"
EQ_REFERENCE_LABEL = "EQ_UNVERIFIED_REFERENCE_ONLY"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = dict(event)
    row.setdefault("time", utc_now())
    with EVENTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def acquire_lock(ttl_sec: float = 900.0) -> bool:
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


def release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except Exception:
        pass


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if math.isnan(out):
            return default
        return out
    except Exception:
        return default


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("held_exclusions", {})
                data.setdefault("manual_buy_events", [])
                data.setdefault("slots", [])
                data.setdefault("waitlist", [])
                data.setdefault("candidate_pool", [])
                data.setdefault("first_seen_signals", {})
                data.setdefault("events", [])
                return data
        except Exception:
            pass
    return {
        "_comment": "Live 8-slot candidate display state. Operational helper only; no broker orders are placed.",
        "version": 1,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "state_path": str(STATE_PATH),
        "events_path": str(EVENTS_PATH),
        "slot_count": SLOT_COUNT,
        "held_exclusions": {},
        "manual_buy_events": [],
        "slots": [],
        "waitlist": [],
        "candidate_pool": [],
        "first_seen_signals": {},
        "events": [],
    }


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    ev = state.get("events") or []
    if isinstance(ev, list) and len(ev) > 300:
        state["events"] = ev[-300:]
    mbe = state.get("manual_buy_events") or []
    if isinstance(mbe, list) and len(mbe) > 1000:
        state["manual_buy_events"] = mbe[-1000:]
    atomic_write_json(STATE_PATH, state)


def add_state_event(state: dict[str, Any], event: str, message: str, payload: dict[str, Any] | None = None) -> None:
    row = {
        "time": utc_now(),
        "event": event,
        "message": message,
        "candidate_id": (payload or {}).get("candidate_id"),
        "ticker": (payload or {}).get("ticker"),
    }
    state.setdefault("events", []).append(row)
    append_event({**row, "payload": payload or {}})


def import_pandas():
    import pandas as pd
    return pd


def derive_gate_list() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    pd = import_pandas()
    if not GATE_SOURCE_PATH.exists():
        raise RuntimeError(f"gate source missing: {GATE_SOURCE_PATH}")
    gate = pd.read_csv(GATE_SOURCE_PATH)
    vol_map: dict[str, str] = {}
    if VOL_SOURCE_PATH.exists():
        vol = pd.read_csv(VOL_SOURCE_PATH)
        vol = vol[vol["split"].astype(str).str.upper().eq("OOS")]
        vol_map = dict(zip(vol["candidate_id"].astype(str), vol["vol_group"].astype(str)))
    out: dict[str, dict[str, Any]] = {}
    for _, r in gate.iterrows():
        cid = str(r.get("candidate_id") or "")
        if not cid:
            continue
        drop = bool(r.get("drop_bad_mae_capture"))
        out[cid] = {
            "candidate_id": cid,
            "ticker": str(r.get("ticker") or "").upper(),
            "gate_status": "DROP_BAD_MAE_CAPTURE" if drop else "KEEP",
            "gate_keep": not drop,
            "drop_bad_mae_capture": drop,
            "vol_group": vol_map.get(cid, "UNKNOWN"),
            "is_n": int(safe_float(r.get("is_n"), 0)),
            "is_worst_mae_pct": safe_float(r.get("is_worst_mae_pct"), None),
            "is_avg_mfe_capture": safe_float(r.get("is_avg_mfe_capture"), None),
        }
    summary = {
        "source": str(GATE_SOURCE_PATH),
        "vol_source": str(VOL_SOURCE_PATH),
        "total": len(out),
        "keep": sum(1 for x in out.values() if x["gate_keep"]),
        "drop": sum(1 for x in out.values() if not x["gate_keep"]),
    }
    payload = {
        "_comment": "Derived live candidate gate list for 20260707. KEEP means not in the IS bad-MAE/mfe-capture drop group.",
        "created_at": utc_now(),
        "gate_rule": "DROP if IS worst_mae is bottom 20% AND IS avg_mfe_capture <= median; otherwise KEEP",
        "summary": summary,
        "candidates": list(out.values()),
    }
    atomic_write_json(LIVE_CANDIDATE_LIST_PATH, payload)
    return out, summary


def load_gate_list() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    if LIVE_CANDIDATE_LIST_PATH.exists():
        try:
            data = json.loads(LIVE_CANDIDATE_LIST_PATH.read_text(encoding="utf-8"))
            rows = data.get("candidates") or []
            if isinstance(rows, list) and rows:
                out = {str(r.get("candidate_id")): r for r in rows if r.get("candidate_id")}
                return out, dict(data.get("summary") or {})
        except Exception:
            pass
    return derive_gate_list()


def load_spy_regime() -> dict[str, Any]:
    pd = import_pandas()
    path = next((p for p in SPY_REGIME_PATHS if p.exists()), None)
    if path is None:
        return {"source": "missing", "regime": "UNKNOWN", "regime_2022split": "UNKNOWN", "is_down": False}
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    now_et = datetime.now(timezone.utc).astimezone(ET)
    today = pd.Timestamp(now_et.date())
    hist = df[df["Date"] <= today]
    if hist.empty:
        row = df.iloc[-1]
    else:
        row = hist.iloc[-1]
    regime = str(row.get("regime") or "UNKNOWN")
    return {
        "source": str(path),
        "asof_date": row["Date"].strftime("%Y-%m-%d"),
        "regime": regime,
        "regime_2022split": str(row.get("regime_2022split") or regime),
        "is_down": regime == "DOWN",
        "now_et": now_et.isoformat(),
    }


def position_key(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or f"{candidate.get('stage')}:{candidate.get('ticker')}:{candidate.get('rulebook_hash_short')}")


def active_held_ids(state: dict[str, Any]) -> set[str]:
    held = state.get("held_exclusions") or {}
    out = set()
    for cid, row in held.items():
        if str((row or {}).get("status") or "open").lower() in {"open", "held", "active"}:
            out.add(str(cid))
    return out


def public_candidate_row(candidate: dict[str, Any], ev: dict[str, Any], gate: dict[str, Any], spy: dict[str, Any]) -> dict[str, Any]:
    vol_group = str(gate.get("vol_group") or "UNKNOWN")
    down_deprioritize = bool(spy.get("is_down") and vol_group == "HIGH_VOL")
    metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    rb = candidate.get("rulebook") if isinstance(candidate.get("rulebook"), dict) else {}
    return {
        "candidate_id": position_key(candidate),
        "ticker": str(candidate.get("ticker") or ev.get("ticker") or "").upper(),
        "stage": candidate.get("stage"),
        "bucket": candidate.get("bucket"),
        "rulebook_hash_short": candidate.get("rulebook_hash_short"),
        "source_rank": candidate.get("rank"),
        "final_score": safe_float(ev.get("score"), 0.0),
        "raw_score": safe_float(ev.get("raw_score"), 0.0),
        "threshold": safe_float(ev.get("threshold"), 0.0),
        "ratio": safe_float(ev.get("ratio"), 0.0),
        "price": safe_float(ev.get("price"), 0.0),
        "atr": safe_float(ev.get("atr"), 0.0),
        "vol_group": vol_group,
        "gate_status": gate.get("gate_status") or "UNKNOWN",
        "gate_keep": bool(gate.get("gate_keep")),
        "down_deprioritize": down_deprioritize,
        "priority_group": 1 if down_deprioritize else 0,
        "entry_quality_policy": EQ_POLICY,
        "entry_quality_verdict": EQ_VERDICT,
        "entry_quality_allow": None,
        "entry_quality_score": None,
        "entry_quality_label": EQ_REFERENCE_LABEL,
        "entry_quality_primary_reason": "excluded_from_candidate_decision_after_eq_validity_20260708",
        "market_score": safe_float(ev.get("market_score"), 0.0),
        "sector_score": safe_float(ev.get("sector_score"), 0.0),
        "vix_level": safe_float(ev.get("vix_level"), 0.0),
        "reasons": list(ev.get("reasons") or [])[:10],
        "win_rate": safe_float(metrics.get("oos_win_rate", metrics.get("min_win_rate")), None),
        "expectancy_pct": safe_float(metrics.get("oos_expectancy_pct", metrics.get("avg_expectancy_pct", metrics.get("min_expectancy_pct"))), None),
        "mdd_pct": safe_float(metrics.get("worst_drawdown_pct", metrics.get("oos_drawdown_pct")), None),
        "fitness": safe_float(metrics.get("oos_fitness", metrics.get("min_fitness")), None),
        "trade_count": int(safe_float(metrics.get("oos_trade_count", metrics.get("min_trade_count")), 0)),
        "max_holding_days": int(safe_float(rb.get("max_holding_days"), 0)) if rb.get("max_holding_days") is not None else None,
        "exit_strategy": rb.get("exit_strategy"),
        "stop_loss_atr": safe_float(rb.get("stop_loss_atr"), None),
        "take_profit_atr": safe_float(rb.get("take_profit_atr"), None),
        "trailing_atr": safe_float(rb.get("trailing_atr"), None),
    }


def sort_candidate_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (int(r.get("priority_group") or 0), -safe_float(r.get("final_score"), 0.0), str(r.get("ticker") or ""), str(r.get("candidate_id") or "")))


def rebuild_slots_from_pool(state: dict[str, Any], reason: str = "cached_rebuild") -> dict[str, Any]:
    held = active_held_ids(state)
    pool = [r for r in (state.get("candidate_pool") or []) if str(r.get("candidate_id")) not in held]
    pool = sort_candidate_pool(pool)
    slots = []
    for idx in range(SLOT_COUNT):
        if idx < len(pool):
            row = dict(pool[idx])
            row["slot_no"] = idx + 1
            row["status"] = "FILLED"
            slots.append(row)
        else:
            slots.append({"slot_no": idx + 1, "status": "WAITING_FOR_SIGNAL"})
    state["slots"] = slots
    state["current_slots"] = slots
    state["slots_filled"] = sum(1 for r in slots if r.get("candidate_id"))
    state["waitlist_count"] = max(0, len(pool) - SLOT_COUNT)
    state["held_count"] = len(held)
    state["waitlist"] = [dict(r, wait_rank=i + 1) for i, r in enumerate(pool[SLOT_COUNT:])]
    state["last_rebuild_reason"] = reason
    state["updated_at"] = utc_now()
    return state


def refresh_slots(*, force_evaluate: bool = False, max_candidates: int = MAX_CANDIDATES) -> dict[str, Any]:
    if not acquire_lock():
        state = load_state()
        state["last_refresh_error"] = "live_slots_lock_busy"
        return {"ok": False, "reason": "live_slots_lock_busy", "state": state}
    started = time.time()
    state = load_state()
    try:
        gate_map, gate_summary = load_gate_list()
        spy = load_spy_regime()
        decision_gate = regular_hours_snapshot()
        state["decision_gate"] = decision_gate
        state["spy_regime"] = spy
        state["gate_summary"] = gate_summary
        state["source_system"] = {
            "primary_sim_reference": "engine.live.elite_strategy_sim / scripts/run_elite_strategy_sim.py",
            "sim_default_interval_sec": DEFAULT_INTERVAL_SEC,
            "candidate_source": "engine.live.elite_shadow_report.build_elite_shadow_report(stage2_limit=60, stage3_limit=80)",
            "signal_evaluator": "engine.live.elite_shadow_trader.evaluate_candidate(candidate, ctx=get_market_context())",
            "decision_gate": "engine.live.regular_hours_gate.regular_hours_snapshot()",
            "elite_shadow_relation": "elite_shadow_trader shares candidate/evaluator stack but has a separate virtual ledger and default daemon interval 300s.",
            "validated_candidate_policy": "KEEP gate + evaluate_signal should_buy=True(final_score >= threshold) + final_score descending priority + SPY DOWN/HIGH_VOL deprioritization.",
            "entry_quality_policy": EQ_POLICY,
            "entry_quality_note": "EQ allow/block is not used for slot eligibility or ordering after eq_validity_20260708; exposed only as an unverified reference marker for API/UI compatibility.",
        }
        if not bool(decision_gate.get("allow_decision")) and not force_evaluate:
            rebuild_slots_from_pool(state, reason="outside_regular_hours_cached_pool")
            add_state_event(state, "REFRESH_SKIPPED", "outside regular hours; reused cached candidate pool", {"decision_gate": decision_gate})
            save_state(state)
            return {"ok": True, "skipped": True, "reason": "outside_regular_hours_cached_pool", "state": state}

        try:
            ctx = get_market_context()
        except Exception:
            ctx = None
        report = build_elite_shadow_report(stage2_limit=60, stage3_limit=80, include_trades=False)
        candidates = (report.get("candidates") or [])[:max_candidates]
        held = active_held_ids(state)
        pool: list[dict[str, Any]] = []
        blocked: dict[str, int] = {}
        errors: list[dict[str, Any]] = []
        evaluated = 0
        buy_signal_count = 0
        for candidate in candidates:
            cid = position_key(candidate)
            ticker = str(candidate.get("ticker") or "").upper()
            gate = gate_map.get(cid)
            if not gate:
                blocked["gate_missing"] = blocked.get("gate_missing", 0) + 1
                continue
            if not bool(gate.get("gate_keep")):
                blocked["DROP_BAD_MAE_CAPTURE"] = blocked.get("DROP_BAD_MAE_CAPTURE", 0) + 1
                continue
            if cid in held:
                blocked["held_excluded"] = blocked.get("held_excluded", 0) + 1
                continue
            try:
                ev = evaluate_candidate(candidate, ctx=ctx)
            except Exception as exc:
                errors.append({"candidate_id": cid, "ticker": ticker, "reason": f"evaluate_exception:{exc}"})
                blocked["evaluate_exception"] = blocked.get("evaluate_exception", 0) + 1
                continue
            evaluated += 1
            if not ev.get("ok"):
                reason = str(ev.get("reason") or "evaluate_not_ok")
                blocked[reason] = blocked.get(reason, 0) + 1
                errors.append({"candidate_id": cid, "ticker": ticker, "reason": reason})
                continue
            if not bool(ev.get("should_buy")):
                blocked["not_buy_signal"] = blocked.get("not_buy_signal", 0) + 1
                continue
            buy_signal_count += 1
            pool.append(public_candidate_row(candidate, ev, gate, spy))
        first_seen = state.setdefault("first_seen_signals", {})
        now_iso = utc_now()
        active_ids = {str(r.get("candidate_id")) for r in pool if r.get("candidate_id")}
        for row in pool:
            cid = str(row.get("candidate_id") or "")
            if not cid:
                continue
            prev = first_seen.get(cid) if isinstance(first_seen.get(cid), dict) else None
            if not prev or prev.get("closed_at"):
                first_seen[cid] = {
                    "candidate_id": cid,
                    "ticker": row.get("ticker"),
                    "first_signal_at": now_iso,
                    "first_signal_price": row.get("price"),
                    "first_final_score": row.get("final_score"),
                    "status": "active",
                }
            else:
                prev["last_seen_at"] = now_iso
                prev["last_price"] = row.get("price")
                prev["last_final_score"] = row.get("final_score")
        for cid, rec in list(first_seen.items()):
            if isinstance(rec, dict) and rec.get("status") == "active" and cid not in active_ids and cid not in held:
                rec["status"] = "inactive"
                rec["last_inactive_at"] = now_iso
        for row in pool:
            rec = first_seen.get(str(row.get("candidate_id") or ""))
            if isinstance(rec, dict):
                row["first_signal_at"] = rec.get("first_signal_at")
                row["first_signal_price"] = rec.get("first_signal_price")
                row["first_final_score"] = rec.get("first_final_score")
                row["last_seen_at"] = rec.get("last_seen_at") or now_iso
        pool = sort_candidate_pool(pool)
        state["candidate_pool"] = pool
        state["last_refresh"] = {
            "time": utc_now(),
            "force_evaluate": bool(force_evaluate),
            "elapsed_sec": round(time.time() - started, 3),
            "candidate_count": len(candidates),
            "evaluated": evaluated,
            "buy_signal_count": buy_signal_count,
            "eligible_pool_count": len(pool),
            "blocked_summary": blocked,
            "errors": errors[-30:],
        }
        rebuild_slots_from_pool(state, reason="fresh_evaluation")
        add_state_event(state, "REFRESH", f"slots refreshed: pool={len(pool)} slots={sum(1 for s in state.get('slots', []) if s.get('candidate_id'))}", state["last_refresh"])
        save_state(state)
        return {"ok": True, "state": state}
    finally:
        release_lock()


def select_slot_or_candidate(state: dict[str, Any], *, slot_no: int | None = None, candidate_id: str | None = None) -> dict[str, Any]:
    if slot_no is not None:
        for row in state.get("slots") or []:
            if int(row.get("slot_no") or 0) == int(slot_no) and row.get("candidate_id"):
                return row
        raise SystemExit(f"slot {slot_no} is empty or missing")
    if candidate_id:
        for row in (state.get("slots") or []) + (state.get("waitlist") or []) + (state.get("candidate_pool") or []):
            if str(row.get("candidate_id") or "") == str(candidate_id):
                return row
        return {"candidate_id": candidate_id}
    raise SystemExit("select --slot or --candidate-id")


def mark_manual_buy(*, slot_no: int | None = None, candidate_id: str | None = None, note: str = "", force_evaluate: bool = False) -> dict[str, Any]:
    if not acquire_lock():
        return {"ok": False, "reason": "live_slots_lock_busy", "state": load_state()}
    state = load_state()
    try:
        selected = select_slot_or_candidate(state, slot_no=slot_no, candidate_id=candidate_id)
        cid = str(selected.get("candidate_id") or candidate_id or "")
        if not cid:
            raise SystemExit("candidate_id missing")
        event = {
            "time": utc_now(),
            "candidate_id": cid,
            "ticker": selected.get("ticker"),
            "slot_no": selected.get("slot_no"),
            "note": note,
            "snapshot": selected,
            "status": "open",
            "source": "manual_slot_buy",
        }
        state.setdefault("held_exclusions", {})[cid] = event
        state.setdefault("manual_buy_events", []).append(event)
        add_state_event(state, "MANUAL_BUY", f"manual buy selected; exclude {cid}", event)
        rebuild_slots_from_pool(state, reason="manual_buy_cached_rebuild")
        save_state(state)
    finally:
        release_lock()
    return refresh_slots(force_evaluate=force_evaluate)


def release_candidate(candidate_id: str, note: str = "") -> dict[str, Any]:
    if not acquire_lock():
        return {"ok": False, "reason": "live_slots_lock_busy", "state": load_state()}
    state = load_state()
    try:
        held = state.setdefault("held_exclusions", {})
        if candidate_id in held:
            held[candidate_id]["status"] = "released"
            held[candidate_id]["released_at"] = utc_now()
            held[candidate_id]["release_note"] = note
            add_state_event(state, "RELEASE", f"released held exclusion {candidate_id}", {"candidate_id": candidate_id, "note": note})
        rebuild_slots_from_pool(state, reason="release_cached_rebuild")
        save_state(state)
    finally:
        release_lock()
    return refresh_slots(force_evaluate=False)


def print_summary(result: dict[str, Any]) -> None:
    state = result.get("state") or {}
    print(json.dumps({
        "ok": result.get("ok"),
        "reason": result.get("reason"),
        "skipped": result.get("skipped"),
        "state_path": str(STATE_PATH),
        "slots_filled": sum(1 for s in state.get("slots", []) if s.get("candidate_id")),
        "waitlist_count": len(state.get("waitlist") or []),
        "held_count": len(active_held_ids(state)),
        "last_refresh": state.get("last_refresh"),
        "decision_gate": state.get("decision_gate"),
        "spy_regime": state.get("spy_regime"),
        "slots": state.get("slots", []),
    }, ensure_ascii=False, indent=2, default=str))


def daemon_loop(interval: int, force_evaluate: bool) -> int:
    stop = {"value": False}

    def on_signal(signum, frame):
        stop["value"] = True
        print(f"signal {signum} received; stopping", file=sys.stderr)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    while not stop["value"]:
        started = time.time()
        result = refresh_slots(force_evaluate=force_evaluate)
        state = result.get("state") or {}
        filled = sum(1 for s in state.get("slots", []) if s.get("candidate_id"))
        print(f"{utc_now()} tick ok={result.get('ok')} skipped={result.get('skipped')} slots={filled} waitlist={len(state.get('waitlist') or [])}", flush=True)
        elapsed = time.time() - started
        sleep_for = max(1, int(interval - elapsed))
        if elapsed >= interval:
            sleep_for = 1
        for _ in range(sleep_for):
            if stop["value"]:
                break
            time.sleep(1)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live 8-slot candidate display tool")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("refresh", help="refresh slots once")
    p.add_argument("--force-evaluate", action="store_true", help="evaluate even outside regular-hours gate; for manual bootstrap/testing only")
    p.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    p = sub.add_parser("daemon", help="run refresh loop using elite strategy sim cadence")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC)
    p.add_argument("--force-evaluate", action="store_true")
    p = sub.add_parser("buy", help="mark a displayed candidate as manually bought and refill all 8 slots")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--slot", type=int)
    group.add_argument("--candidate-id")
    p.add_argument("--note", default="")
    p.add_argument("--force-evaluate", action="store_true")
    p = sub.add_parser("release", help="release a held candidate after it is sold/closed")
    p.add_argument("--candidate-id", required=True)
    p.add_argument("--note", default="")
    sub.add_parser("state", help="print current state summary")
    sub.add_parser("init-gates", help="rebuild live_candidate_list_20260707.json from analysis artifacts")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cmd == "refresh":
        print_summary(refresh_slots(force_evaluate=args.force_evaluate, max_candidates=args.max_candidates))
    elif args.cmd == "daemon":
        return daemon_loop(max(1, int(args.interval)), bool(args.force_evaluate))
    elif args.cmd == "buy":
        print_summary(mark_manual_buy(slot_no=args.slot, candidate_id=args.candidate_id, note=args.note, force_evaluate=args.force_evaluate))
    elif args.cmd == "release":
        print_summary(release_candidate(args.candidate_id, note=args.note))
    elif args.cmd == "state":
        print_summary({"ok": True, "state": load_state()})
    elif args.cmd == "init-gates":
        gates, summary = derive_gate_list()
        print(json.dumps({"ok": True, "path": str(LIVE_CANDIDATE_LIST_PATH), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
