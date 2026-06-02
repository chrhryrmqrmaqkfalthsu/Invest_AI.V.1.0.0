#!/usr/bin/env python3
"""bulk swing diagnostic orchestrator.

검증된 _bulk_swing_worker.py를 subprocess로 실행해 대량 1차 진단을 관리한다.
운영 경로(data/symbols)와 GA 운영 dump(data/_system/ga_population_dump_*.json)는 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_ROOT = ROOT / "data" / "_system" / "bulk_diagnostic" / "swing"
RESULT_DIR = OUTPUT_ROOT / "results"
LOG_DIR = OUTPUT_ROOT / "logs"
PROGRESS_PATH = OUTPUT_ROOT / "_progress.json"
SUMMARY_PATH = OUTPUT_ROOT / "_summary.csv"
POS_PATH = OUTPUT_ROOT / "_candidates_pos.csv"
UNCERTAIN_PATH = OUTPUT_ROOT / "_candidates_uncertain.csv"
ERRORS_PATH = OUTPUT_ROOT / "_errors.csv"
WORKER_PATH = ROOT / "scripts" / "screening" / "_bulk_swing_worker.py"
SENTIMENT_DIR = ROOT / "data" / "_system" / "ticker_sentiment"

FORCE_INCLUDE = ["NVDA", "MSFT", "JPM", "AAPL", "KO", "XOM"]
DEFAULT_LIMIT = 50
DEFAULT_PARALLEL = 8
DEFAULT_TIMEOUT_SEC = 1200
DEFAULT_FITNESS_MODE = "swing"

SUMMARY_FIELDS = [
    "ticker",
    "status",
    "priority_score",
    "train_trades",
    "train_exp",
    "test_trades",
    "test_exp",
    "elapsed_sec",
    "error",
]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_ticker_file(path: Path) -> list[str]:
    tickers: list[str] = []
    if not path.exists():
        raise FileNotFoundError(f"tickers file not found: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # csv/space 둘 다 허용
        for part in line.replace(",", " ").split():
            t = part.strip().upper()
            if t:
                tickers.append(t)
    return _dedupe_keep_order(tickers)


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _parse_date(value: str | None):
    if not value:
        return None
    value = str(value).strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[: len(fmt)], fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None


def _to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        if isinstance(value, str) and not value.strip():
            return 0.0
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return 0.0
        return v
    except Exception:
        return 0.0


def _sentiment_value(row: dict[str, Any]) -> float:
    """CSV별 감성 컬럼명이 조금 달라도 abs 합산이 되도록 후보 컬럼을 순서대로 찾는다."""
    for key in (
        "ticker_sentiment",
        "ticker_sentiment_score",
        "sentiment",
        "sentiment_score",
        "news_sentiment",
        "score",
    ):
        if key in row:
            return _to_float(row.get(key))
    total = 0.0
    found = False
    for key, value in row.items():
        lk = key.lower()
        if "sentiment" in lk or lk.endswith("score"):
            total += _to_float(value)
            found = True
    return total if found else 0.0


def _scan_one_sentiment_csv(path: Path) -> dict[str, Any] | None:
    ticker = path.name
    if ticker.endswith("_daily.csv"):
        ticker = ticker[: -len("_daily.csv")]
    else:
        ticker = path.stem.upper()
    ticker = ticker.upper()

    rows = 0
    last_date = None
    abs_sum = 0.0
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return None
        date_col = "date" if "date" in reader.fieldnames else reader.fieldnames[0]
        for row in reader:
            rows += 1
            d = _parse_date(row.get(date_col))
            if d and (last_date is None or d > last_date):
                last_date = d
            abs_sum += abs(_sentiment_value(row))

    if rows <= 0:
        return None
    today = datetime.now().date()
    has_recent = bool(last_date and last_date >= today - timedelta(days=7))
    return {
        "ticker": ticker,
        "sentiment_days": rows,
        "last_date": last_date.isoformat() if last_date else "",
        "last_date_obj": last_date,
        "abs_sentiment_sum": abs_sum,
        "has_recent": has_recent,
        "path": str(path),
    }


def build_universe(limit: int, tickers_file: Path | None = None) -> list[dict[str, Any]]:
    meta_by_ticker: dict[str, dict[str, Any]] = {}

    if SENTIMENT_DIR.exists():
        for path in SENTIMENT_DIR.glob("*_daily.csv"):
            try:
                item = _scan_one_sentiment_csv(path)
                if item:
                    meta_by_ticker[item["ticker"]] = item
            except Exception as e:
                # universe 생성 중 일부 CSV가 깨져도 전체를 막지 않는다.
                ticker = path.name.replace("_daily.csv", "").upper()
                meta_by_ticker[ticker] = {
                    "ticker": ticker,
                    "sentiment_days": 0,
                    "last_date": "",
                    "last_date_obj": None,
                    "abs_sentiment_sum": 0.0,
                    "has_recent": False,
                    "path": str(path),
                    "scan_error": f"{type(e).__name__}: {e}",
                }

    if tickers_file:
        ordered = _read_ticker_file(tickers_file)
        for ticker in ordered:
            meta_by_ticker.setdefault(
                ticker,
                {
                    "ticker": ticker,
                    "sentiment_days": 0,
                    "last_date": "",
                    "last_date_obj": None,
                    "abs_sentiment_sum": 0.0,
                    "has_recent": False,
                    "path": "",
                },
            )

    sorted_items = sorted(
        meta_by_ticker.values(),
        key=lambda x: (
            1 if x.get("has_recent") else 0,
            int(x.get("sentiment_days") or 0),
            float(x.get("abs_sentiment_sum") or 0.0),
            x.get("ticker", ""),
        ),
        reverse=True,
    )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ticker in FORCE_INCLUDE:
        item = meta_by_ticker.get(ticker) or {
            "ticker": ticker,
            "sentiment_days": 0,
            "last_date": "",
            "last_date_obj": None,
            "abs_sentiment_sum": 0.0,
            "has_recent": False,
            "path": "",
        }
        out.append(item)
        seen.add(ticker)

    for item in sorted_items:
        ticker = item.get("ticker", "").upper()
        if ticker in seen:
            continue
        out.append(item)
        seen.add(ticker)
        if len(out) >= limit:
            break

    return out[:limit]


def _load_progress() -> dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return {}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_progress(progress: dict[str, Any]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    progress["updated_at"] = _now_iso()
    tmp = PROGRESS_PATH.with_suffix(PROGRESS_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def _result_path(ticker: str) -> Path:
    return RESULT_DIR / f"{ticker}.json"


def _load_worker_result(ticker: str) -> dict[str, Any]:
    p = _result_path(ticker)
    if not p.exists():
        raise FileNotFoundError(f"worker result missing: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _summary_row(result: dict[str, Any]) -> dict[str, Any]:
    train = result.get("train") or {}
    test = result.get("test") or {}
    return {
        "ticker": result.get("ticker", ""),
        "status": result.get("status", "ERROR"),
        "priority_score": result.get("priority_score", 0.0),
        "train_trades": train.get("trades", ""),
        "train_exp": train.get("expectancy_pct", ""),
        "test_trades": test.get("trades", ""),
        "test_exp": test.get("expectancy_pct", ""),
        "elapsed_sec": result.get("elapsed_sec", ""),
        "error": result.get("error", ""),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in SUMMARY_FIELDS})


def _write_outputs(results: list[dict[str, Any]]) -> None:
    rows = [_summary_row(r) for r in results]
    rows_sorted = sorted(rows, key=lambda r: (str(r.get("ticker") or "")))
    _write_csv(SUMMARY_PATH, rows_sorted)

    pos = [r for r in rows if r.get("status") == "POS"]
    uncertain = [r for r in rows if r.get("status") == "UNCERTAIN"]
    errors = [r for r in rows if r.get("status") == "ERROR"]

    pos.sort(key=lambda r: float(r.get("priority_score") or 0.0), reverse=True)
    uncertain.sort(key=lambda r: float(r.get("priority_score") or 0.0), reverse=True)
    errors.sort(key=lambda r: str(r.get("ticker") or ""))

    _write_csv(POS_PATH, pos)
    _write_csv(UNCERTAIN_PATH, uncertain)
    _write_csv(ERRORS_PATH, errors)


def _make_error_result(ticker: str, reason: str, elapsed_sec: float | None = None) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "type": "bulk_swing_diagnostic",
        "validated": False,
        "fitness_mode": DEFAULT_FITNESS_MODE,
        "status": "ERROR",
        "priority_score": 0.0,
        "elapsed_sec": elapsed_sec,
        "error": reason,
        "note": "Diagnostic only. Not validated. Must pass true-WF before trading.",
    }


def _start_worker(ticker: str, fitness_mode: str) -> dict[str, Any]:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{ticker}.log"
    log_f = log_path.open("w", encoding="utf-8")
    cmd = [
        str(ROOT / "venv" / "bin" / "python"),
        str(WORKER_PATH),
        ticker,
        "--fitness-mode",
        fitness_mode,
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    return {
        "ticker": ticker,
        "proc": proc,
        "log_f": log_f,
        "log_path": str(log_path),
        "started_at": time.time(),
        "cmd": cmd,
    }


def _kill_proc(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def run_batch(args: argparse.Namespace, universe: list[dict[str, Any]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    progress = _load_progress() if args.resume else {}
    if not progress:
        progress = {
            "started_at": _now_iso(),
            "fitness_mode": args.fitness_mode,
            "limit": args.limit,
            "parallel": args.parallel,
            "timeout_sec": args.timeout_sec,
            "completed": {},
            "failed": {},
            "running": [],
        }
    progress.setdefault("completed", {})
    progress.setdefault("failed", {})
    progress["running"] = []
    _save_progress(progress)

    selected = [str(x["ticker"]).upper() for x in universe]
    pending: list[str] = []
    for ticker in selected:
        if args.resume and ticker in progress.get("completed", {}):
            continue
        if args.resume and ticker in progress.get("failed", {}) and not args.retry_failed:
            continue
        pending.append(ticker)

    running: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    # 기존 결과도 요약 출력에 포함한다.
    for ticker, status in (progress.get("completed") or {}).items():
        try:
            results.append(_load_worker_result(ticker))
        except Exception:
            results.append(_make_error_result(ticker, f"completed result missing while resume: {status}"))
    for ticker, reason in (progress.get("failed") or {}).items():
        if not args.retry_failed:
            results.append(_make_error_result(ticker, str(reason)))

    print(f"selected={len(selected)} pending={len(pending)} parallel={args.parallel} timeout={args.timeout_sec}s")

    while pending or running:
        while pending and len(running) < args.parallel:
            ticker = pending.pop(0)
            item = _start_worker(ticker, args.fitness_mode)
            running[ticker] = item
            progress["running"] = sorted(running.keys())
            _save_progress(progress)
            print(f"START {ticker} pid={item['proc'].pid}")

        time.sleep(2)
        now = time.time()
        for ticker in list(running.keys()):
            item = running[ticker]
            proc: subprocess.Popen = item["proc"]
            elapsed = now - float(item["started_at"])
            rc = proc.poll()
            if rc is None and elapsed <= args.timeout_sec:
                continue

            try:
                item["log_f"].close()
            except Exception:
                pass

            if rc is None and elapsed > args.timeout_sec:
                _kill_proc(proc)
                reason = f"timeout after {args.timeout_sec}s"
                result = _make_error_result(ticker, reason, elapsed)
                _result_path(ticker).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                progress["failed"][ticker] = reason
                print(f"ERROR {ticker} {reason}")
            elif rc != 0:
                reason = f"worker exit code {rc}; log={item['log_path']}"
                try:
                    result = _load_worker_result(ticker)
                    result["status"] = "ERROR"
                    result["error"] = result.get("error") or reason
                except Exception:
                    result = _make_error_result(ticker, reason, elapsed)
                    _result_path(ticker).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                progress["failed"][ticker] = reason
                print(f"ERROR {ticker} {reason}")
            else:
                try:
                    result = _load_worker_result(ticker)
                    status = result.get("status", "ERROR")
                    if status == "ERROR":
                        progress["failed"][ticker] = result.get("error", "worker returned ERROR")
                    else:
                        progress["completed"][ticker] = status
                        progress.get("failed", {}).pop(ticker, None)
                    print(
                        f"DONE {ticker} {status} "
                        f"test={((result.get('test') or {}).get('trades'))}/"
                        f"{((result.get('test') or {}).get('expectancy_pct')):+.2f}% "
                        f"elapsed={float(result.get('elapsed_sec') or 0):.0f}s"
                    )
                except Exception as e:
                    reason = f"result parse error: {type(e).__name__}: {e}"
                    result = _make_error_result(ticker, reason, elapsed)
                    _result_path(ticker).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                    progress["failed"][ticker] = reason
                    print(f"ERROR {ticker} {reason}")

            results = [r for r in results if r.get("ticker") != ticker]
            results.append(result)
            running.pop(ticker, None)
            progress["running"] = sorted(running.keys())
            _save_progress(progress)
            _write_outputs(results)

    progress["running"] = []
    _save_progress(progress)
    _write_outputs(results)

    counts: dict[str, int] = {}
    for result in results:
        counts[result.get("status", "ERROR")] = counts.get(result.get("status", "ERROR"), 0) + 1
    print("SUMMARY", counts)


def print_dry_run(universe: list[dict[str, Any]]) -> None:
    print("ticker,sentiment_days,last_date,has_recent,abs_sentiment_sum")
    for item in universe:
        print(
            f"{item.get('ticker')},"
            f"{item.get('sentiment_days', 0)},"
            f"{item.get('last_date', '')},"
            f"{item.get('has_recent', False)},"
            f"{float(item.get('abs_sentiment_sum') or 0.0):.6f}"
        )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="bulk swing diagnostic orchestrator")
    p.add_argument("--tickers-file", default=None)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    p.add_argument("--timeout-sec", type=int, default=DEFAULT_TIMEOUT_SEC)
    p.add_argument("--fitness-mode", default=DEFAULT_FITNESS_MODE)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    args.fitness_mode = (args.fitness_mode or DEFAULT_FITNESS_MODE).strip().lower()
    if args.fitness_mode != "swing":
        raise SystemExit("bulk diagnostic currently supports --fitness-mode swing only")
    if args.limit <= 0:
        raise SystemExit("--limit must be positive")
    if args.parallel <= 0:
        raise SystemExit("--parallel must be positive")
    if args.timeout_sec <= 0:
        raise SystemExit("--timeout-sec must be positive")

    tickers_file = Path(args.tickers_file) if args.tickers_file else None
    universe = build_universe(limit=args.limit, tickers_file=tickers_file)
    if args.dry_run:
        print_dry_run(universe)
        return 0

    run_batch(args, universe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
