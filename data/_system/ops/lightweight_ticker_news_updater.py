#!/usr/bin/env python3
"""Candidate/holding-scoped ticker sentiment updater wrapper.

Targets:
- current live candidate_pool tickers
- authoritative broker holdings tickers

The wrapper delegates all Alpha Vantage retrieval, raw-cache merge, aggregation,
usage accounting, and CSV writes to update_ticker_sentiment_recent.py.
It never changes candidate eligibility: missing/stale news remains fail-open in
the existing evaluator (News=0, NewsTopics={}).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.live.real_focus_news_refresh import collect_real_holding_targets

LIVE_SLOTS_STATE_PATH = ROOT / "data/_system/live_slots_state.json"
STATE_PATH = ROOT / "data/_system/lightweight_ticker_news_updater_state.json"
RUN_LOG_PATH = ROOT / "data/_system/analysis/lightweight_ticker_news_updater/runs.jsonl"
MARKET_USAGE_PATH = ROOT / "data/_system/news_cache/_usage.json"
TICKER_USAGE_PATH = ROOT / "data/_system/ticker_sentiment_update_usage.json"
UPDATER_RUN_LOG_PATH = ROOT / "data/_system/ticker_sentiment_update.log.jsonl"
TICKER_SENTIMENT_DIR = ROOT / "data/_system/ticker_sentiment"
UPDATER_SCRIPT = ROOT / "scripts/news_downloader/update_ticker_sentiment_recent.py"
DEFAULT_DAILY_LIMIT = 25
DEFAULT_MARKET_RESERVE = 2
DEFAULT_REQUEST_INTERVAL = 0.86


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def load_wrapper_state() -> dict[str, Any]:
    data = load_json(STATE_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("known_candidates", [])
    data.setdefault("known_holdings", [])
    data.setdefault("deferred", [])
    data.setdefault("last_daily_date", "")
    data.setdefault("last_run", None)
    return data


def collect_candidate_targets() -> list[dict[str, Any]]:
    state = load_json(LIVE_SLOTS_STATE_PATH, {})
    rows = state.get("candidate_pool") if isinstance(state, dict) else []
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        out[ticker] = {
            "ticker": ticker,
            "candidate_id": str(row.get("candidate_id") or ""),
            "source": "candidate_pool",
            "first_signal_at": row.get("first_signal_at"),
            "last_seen_at": row.get("last_seen_at"),
        }
    return [out[key] for key in sorted(out)]


def collect_holding_targets() -> list[dict[str, Any]]:
    rows = collect_real_holding_targets()
    out: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        out[ticker] = {
            "ticker": ticker,
            "source": "broker_holdings",
            "qty": row.get("qty"),
            "avg_price": row.get("avg_price"),
        }
    return [out[key] for key in sorted(out)]


def last_csv_date(ticker: str) -> str:
    path = TICKER_SENTIMENT_DIR / f"{ticker}_daily.csv"
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            return ""
        return str(rows[-1].get("Date") or rows[-1].get("date") or "")[:10]
    except Exception:
        return ""


def current_usage_count(path: Path, today: str | None = None) -> int:
    today = today or date.today().isoformat()
    data = load_json(path, {})
    if not isinstance(data, dict) or str(data.get("date") or "") != today:
        return 0
    try:
        return max(0, int(data.get("count") or 0))
    except Exception:
        return 0


def usage_snapshot(daily_limit: int, market_reserve: int) -> dict[str, int]:
    market_used = current_usage_count(MARKET_USAGE_PATH)
    ticker_used = current_usage_count(TICKER_USAGE_PATH)
    reserve_remaining = max(0, int(market_reserve) - market_used)
    available = max(0, int(daily_limit) - market_used - ticker_used - reserve_remaining)
    return {
        "market_used": market_used,
        "ticker_used": ticker_used,
        "reserve_remaining": reserve_remaining,
        "available": available,
    }


def remaining_ticker_budget(daily_limit: int, market_reserve: int) -> int:
    return usage_snapshot(daily_limit, market_reserve)["available"]


def snapshot_csvs(tickers: list[str]) -> dict[str, tuple[float, int, str]]:
    out: dict[str, tuple[float, int, str]] = {}
    for ticker in tickers:
        path = TICKER_SENTIMENT_DIR / f"{ticker}_daily.csv"
        if path.exists():
            stat = path.stat()
            out[ticker] = (stat.st_mtime, stat.st_size, last_csv_date(ticker))
    return out


def updater_log_offset() -> int:
    try:
        return UPDATER_RUN_LOG_PATH.stat().st_size if UPDATER_RUN_LOG_PATH.exists() else 0
    except Exception:
        return 0


def read_updater_rows_since(offset: int) -> list[dict[str, Any]]:
    if not UPDATER_RUN_LOG_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with UPDATER_RUN_LOG_PATH.open("r", encoding="utf-8") as handle:
            handle.seek(max(0, int(offset)))
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except Exception:
        return []
    return rows


def _ordered_unique(parts: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for rows in parts:
        for ticker in rows:
            ticker = str(ticker or "").upper().strip()
            if ticker and ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def plan_targets(mode: str, wrapper_state: dict[str, Any]) -> dict[str, Any]:
    candidates = collect_candidate_targets()
    holdings = collect_holding_targets()
    candidate_tickers = [row["ticker"] for row in candidates]
    holding_tickers = [row["ticker"] for row in holdings]
    known_candidates = {str(x).upper() for x in wrapper_state.get("known_candidates") or []}
    known_holdings = {str(x).upper() for x in wrapper_state.get("known_holdings") or []}
    new_candidates = [ticker for ticker in candidate_tickers if ticker not in known_candidates]
    new_holdings = [ticker for ticker in holding_tickers if ticker not in known_holdings]
    deferred = [str(x).upper() for x in wrapper_state.get("deferred") or []]
    stale_or_missing = [
        ticker for ticker in candidate_tickers + holding_tickers
        if not last_csv_date(ticker) or last_csv_date(ticker) < date.today().isoformat()
    ]

    if mode == "on-demand":
        ordered = _ordered_unique([new_holdings, holding_tickers, new_candidates, deferred])
    else:
        ordered = _ordered_unique([
            new_holdings,
            holding_tickers,
            new_candidates,
            deferred,
            stale_or_missing,
            candidate_tickers,
        ])
    return {
        "candidates": candidates,
        "holdings": holdings,
        "candidate_tickers": candidate_tickers,
        "holding_tickers": holding_tickers,
        "new_candidates": new_candidates,
        "new_holdings": new_holdings,
        "stale_or_missing": stale_or_missing,
        "ordered_targets": ordered,
    }


def run_once(
    *,
    mode: str,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
    market_reserve: int = DEFAULT_MARKET_RESERVE,
    request_interval: float = DEFAULT_REQUEST_INTERVAL,
    dry_run: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    wrapper_state = load_wrapper_state()
    plan = plan_targets(mode, wrapper_state)
    budget_before = remaining_ticker_budget(daily_limit, market_reserve)
    ordered = plan["ordered_targets"]
    selected = ordered[:budget_before]
    deferred = ordered[budget_before:]
    before = snapshot_csvs(selected)
    updater_log_before = updater_log_offset()
    usage_before_snapshot = usage_snapshot(daily_limit, market_reserve)
    usage_before = usage_before_snapshot["ticker_used"]

    result: dict[str, Any] = {
        "started_at": started_at,
        "mode": mode,
        "dry_run": bool(dry_run),
        "candidate_count": len(plan["candidate_tickers"]),
        "holding_count": len(plan["holding_tickers"]),
        "target_count": len(ordered),
        "selected_count": len(selected),
        "deferred_count": len(deferred),
        "selected": selected,
        "deferred": deferred,
        "new_candidates": plan["new_candidates"],
        "new_holdings": plan["new_holdings"],
        "stale_or_missing": plan["stale_or_missing"],
        "usage_before": usage_before,
        "usage_before_snapshot": usage_before_snapshot,
        "budget_before": budget_before,
        "daily_limit": daily_limit,
        "market_reserve": market_reserve,
        "updater_script": str(UPDATER_SCRIPT),
        "fail_open_unchanged": True,
    }

    return_code = 0
    stdout = ""
    stderr = ""
    if selected and not dry_run:
        command = [
            sys.executable,
            str(UPDATER_SCRIPT),
            *selected,
            "--daily-limit",
            str(daily_limit),
            "--market-reserve",
            str(market_reserve),
            "--request-interval",
            str(request_interval),
        ]
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        return_code = int(completed.returncode)
        stdout = completed.stdout[-20000:]
        stderr = completed.stderr[-20000:]

    after = snapshot_csvs(selected)
    updated = []
    created = []
    unchanged = []
    for ticker in selected:
        if ticker not in before and ticker in after:
            created.append(ticker)
        elif ticker in before and ticker in after and before[ticker] != after[ticker]:
            updated.append(ticker)
        else:
            unchanged.append(ticker)

    usage_after_snapshot = usage_snapshot(daily_limit, market_reserve)
    usage_after = usage_after_snapshot["ticker_used"]
    updater_rows = read_updater_rows_since(updater_log_before)
    failed_selected: list[str] = []
    for row in updater_rows:
        ticker = str(row.get("ticker") or "").upper()
        status = str(row.get("status") or "").upper()
        if ticker in selected and status in {"FAILED", "ERROR", "API_LIMIT", "EMPTY_FEED"}:
            failed_selected.append(ticker)
    if return_code != 0 and not failed_selected:
        failed_selected = list(selected)

    next_deferred = _ordered_unique([failed_selected, deferred])
    wrapper_state["known_candidates"] = plan["candidate_tickers"]
    wrapper_state["known_holdings"] = plan["holding_tickers"]
    wrapper_state["deferred"] = next_deferred
    if mode == "daily" and return_code == 0:
        wrapper_state["last_daily_date"] = date.today().isoformat()
    result.update({
        "finished_at": utc_now(),
        "return_code": return_code,
        "usage_after": usage_after,
        "usage_after_snapshot": usage_after_snapshot,
        "usage_delta": usage_after - usage_before,
        "budget_after": remaining_ticker_budget(daily_limit, market_reserve),
        "created_csvs": created,
        "updated_csvs": updated,
        "unchanged_csvs": unchanged,
        "failed_selected": failed_selected,
        "next_deferred": next_deferred,
        "updater_rows": updater_rows,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
    })
    wrapper_state["last_run"] = result
    atomic_write_json(STATE_PATH, wrapper_state)
    append_jsonl(RUN_LOG_PATH, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Candidate/holding-scoped ticker sentiment updater")
    parser.add_argument("--mode", choices=["daily", "on-demand"], default="on-demand")
    parser.add_argument("--daily-limit", type=int, default=DEFAULT_DAILY_LIMIT)
    parser.add_argument("--market-reserve", type=int, default=DEFAULT_MARKET_RESERVE)
    parser.add_argument("--request-interval", type=float, default=DEFAULT_REQUEST_INTERVAL)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_once(
        mode=args.mode,
        daily_limit=args.daily_limit,
        market_reserve=args.market_reserve,
        request_interval=args.request_interval,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if int(result.get("return_code") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
